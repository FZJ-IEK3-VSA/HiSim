# clean

"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

import os
from typing import List, Tuple, Union, Any
from pathlib import Path

import pandas as pd

from hisim.component import ComponentOutput
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes
from hisim.modular_household.interface_configs.kpi_config import KPIConfig
from hisim.simulationparameters import SimulationParameters
from hisim.utils import HISIMPATH
from hisim.component_wrapper import ComponentWrapper

from hisim import log
from hisim.postprocessing.investment_cost_co2 import compute_investment_cost

from hisim.components import generic_hot_water_storage_modular


def compute_energy_from_power(
    power_timeseries: pd.Series, timeresolution: int
) -> float:
    """Computes the energy from a power value."""
    if power_timeseries.empty:
        return 0.0
    return float(power_timeseries.sum() * timeresolution / 3.6e6)

def compute_hot_water_storage_losses_and_cycles(
    components: List[ComponentWrapper],
    all_outputs: List,
    results: pd.DataFrame,
    timeresolution: int,
) -> Tuple[float, float, float, float, float, float]:
    """Computes hot water storage losses and cycles."""

    # initialize columns consumption, production, battery_charge, battery_discharge, storage
    charge_sum_dhw = 0.0
    charge_sum_buffer = 0.0
    discharge_sum_dhw = 0.0
    discharge_sum_buffer = 0.0
    cycle_buffer = None
    cycle_dhw = None

    # get cycle of water storages
    for elem in components:
        if isinstance(
            elem.my_component, generic_hot_water_storage_modular.HotWaterStorage
        ):
            use = elem.my_component.use
            if use == ComponentType.BUFFER:
                cycle_buffer = elem.my_component.config.energy_full_cycle
            elif use == ComponentType.BOILER:
                cycle_dhw = elem.my_component.config.energy_full_cycle

    for index, output in enumerate(all_outputs):
        if output.postprocessing_flag is not None:
            if InandOutputType.CHARGE in output.postprocessing_flag:
                if InandOutputType.WATER_HEATING in output.postprocessing_flag:
                    charge_sum_dhw = charge_sum_dhw + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        timeresolution=timeresolution,
                    )
                elif InandOutputType.HEATING in output.postprocessing_flag:
                    charge_sum_buffer = charge_sum_buffer + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        timeresolution=timeresolution,
                    )
            elif InandOutputType.DISCHARGE in output.postprocessing_flag:
                if ComponentType.BOILER in output.postprocessing_flag:
                    discharge_sum_dhw = discharge_sum_dhw + compute_energy_from_power(
                        power_timeseries=results.iloc[:, index],
                        timeresolution=timeresolution,
                    )
                elif ComponentType.BUFFER in output.postprocessing_flag:
                    discharge_sum_buffer = (
                        discharge_sum_buffer
                        + compute_energy_from_power(
                            power_timeseries=results.iloc[:, index],
                            timeresolution=timeresolution,
                        )
                    )
        else:
            continue
        if cycle_dhw is not None:
            cycles_dhw = charge_sum_dhw / cycle_dhw
        else:
            cycles_dhw = 0
            log.error(
                "Energy of full cycle must be defined in config of modular hot water storage to compute the number of cycles. "
            )
        storage_loss_dhw = charge_sum_dhw - discharge_sum_dhw
        if cycle_buffer is not None:
            cycles_buffer = charge_sum_buffer / cycle_buffer
        else:
            cycles_buffer = 0
            log.error(
                "Energy of full cycle must be defined in config of modular hot water storage to compute the number of cycles. "
            )
        storage_loss_buffer = charge_sum_buffer - discharge_sum_buffer
    if cycle_buffer == 0:
        building_heating = charge_sum_buffer
    else:
        building_heating = discharge_sum_buffer

    return (
        cycles_dhw,
        storage_loss_dhw,
        discharge_sum_dhw,
        cycles_buffer,
        storage_loss_buffer,
        building_heating,
    )
