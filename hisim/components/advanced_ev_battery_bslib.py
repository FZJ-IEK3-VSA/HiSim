""" Car Battery implementation built upon the bslib library. It contains a CarBattery Class together with its Configuration and State. """

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
import importlib
from dataclasses import dataclass
from typing import Any, List

import pandas as pd
from bslib import bslib as bsl
from dataclasses_json import dataclass_json

# Import modules from HiSim
from hisim.component import (
    Component,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
    SingleTimeStepValues,
    OpexCostDataClass,
    DisplayConfig,
    CapexCostDataClass
)
from hisim.loadtypes import ComponentType, InandOutputType, LoadTypes, Units
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry

__authors__ = "Tjarko Tjaden, Hauke Hoops, Kai Rösken"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = "..."
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Tjarko Tjaden"
__email__ = "tjarko.tjaden@hs-emden-leer.de"
__status__ = "development"


@dataclass_json
@dataclass
class CarBatteryConfig(ConfigBase):
    """Configuration of a Car Battery."""

    #: building_name in which component is
    building_name: str
    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: name of battery to search in database (bslib)
    system_id: str
    #: charging and discharging power in Watt
    p_inv_custom: float
    #: battery capacity in in kWh
    e_bat_custom: float
    #: amount of energy used to charge the car battery
    charge: float
    #: amount of energy discharged from the battery
    discharge: float

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return CarBattery.get_full_classname()

    @classmethod
    def get_default_config(cls, building_name: str = "BUI1", name: str = "CarBattery") -> "CarBatteryConfig":
        """Returns default configuration of a Car Battery."""
        config = CarBatteryConfig(
            building_name=building_name,
            name=name,
            system_id="SG1",
            p_inv_custom=1e4,
            e_bat_custom=30,
            source_weight=1,
            charge=0,
            discharge=0,
        )
        return config


class CarBattery(Component):
    """Car Battery class.

    Simulate state of charge and realized power of a ac coupled battery
    storage system with the bslib library. Relevant simulation parameters
    are loaded within the init for a specific or generic battery type.

    Components to connect to:
    (1) CarBattery controller (controller_l1_generic_ev_charge)
    """

    # Inputs
    LoadingPowerInput = "LoadingPowerInput"  # W

    # Outputs
    AcBatteryPower = "AcBatteryPower"  # W
    DcBatteryPower = "DcBatteryPower"  # W
    StateOfCharge = "StateOfCharge"  # [0..1]

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CarBatteryConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ):
        """Loads the parameters of the specified battery storage."""
        self.battery_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.source_weight = self.battery_config.source_weight

        self.system_id = self.battery_config.system_id

        self.p_inv_custom = self.battery_config.p_inv_custom

        self.e_bat_custom = self.battery_config.e_bat_custom

        # Component has states
        self.state = EVBatteryState(soc=0.5)
        self.previous_state = self.state.clone()

        # Load battery object with parameters from bslib database
        self.bat = bsl.ACBatMod(
            system_id=self.system_id,
            p_inv_custom=self.p_inv_custom,
            e_bat_custom=self.e_bat_custom,
        )

        # Define component inputs
        self.p_set: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.LoadingPowerInput,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            mandatory=True,
        )

        # Define component outputs
        self.p_bs: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.AcBatteryPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=[
                InandOutputType.CHARGE_DISCHARGE,
                ComponentType.CAR_BATTERY,
            ],
            output_description="Charging power of the battery in Watt (Alternating current)",
        )

        self.p_bat: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DcBatteryPower,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Charging power of the battery in Watt (Direct current).",
        )

        self.soc: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StateOfCharge,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            postprocessing_flag=[InandOutputType.STORAGE_CONTENT],
            output_description="State of charge of the battery.",
        )

        self.add_default_connections(self.get_default_connections_from_charge_controller())

    def get_default_connections_from_charge_controller(self) -> Any:
        """Get default connections from charge controller."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.controller_l1_generic_ev_charge"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "L1Controller")
        connections: List[ComponentConnection] = []
        ev_charge_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                CarBattery.LoadingPowerInput,
                ev_charge_controller_classname,
                component_class.ToOrFromBattery,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # Parameters
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep

        # Load input values
        p_set = stsv.get_input_value(self.p_set)
        soc = self.state.soc

        # Simulate battery charging
        if p_set >= 0:
            results = self.bat.simulate(p_load=p_set, soc=soc, dt=seconds_per_timestep)
            p_bs = results[0]
            p_bat = results[1]
            soc = results[2]

        # Simulate battery discharge without losses (this is included in the car consumption of the car component)
        else:
            soc = soc + (p_set * self.my_simulation_parameters.seconds_per_timestep / 3600) / (self.e_bat_custom * 1e3)
            if soc < 0:
                raise ValueError(
                    "Car cannot drive, because battery is empty."
                    + "This points towards a major problem in the battery configuration - or the consumption pattern of the car."
                )
            p_bs = 0
            p_bat = 0

        # write values for output time series
        stsv.set_output_value(self.p_bs, p_bs)
        stsv.set_output_value(self.p_bat, p_bat)
        stsv.set_output_value(self.soc, soc)

        # write values to state
        self.state.soc = soc

    def write_to_report(self) -> List[str]:
        """Writes Car Battery values to report."""
        return self.battery_config.get_string_dict()

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV

        electricity_consumption is considered vor generic_car already
        """
        battery_losses_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if (
                output.postprocessing_flag is not None
                and output.component_name == self.component_name
            ):
                if InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag:
                    self.battery_config.charge = round(
                        postprocessing_results.iloc[:, index].clip(lower=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6,
                        1,
                    )
                    self.battery_config.discharge = round(
                        postprocessing_results.iloc[:, index].clip(upper=0).sum()
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6,
                        1,
                    ) * (-1)
                    battery_losses_in_kwh = self.battery_config.charge - self.battery_config.discharge
        # Todo: Battery Aging like in component advanced_battery_bslib? Or is this considered in maintenance cost of car?

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=0,
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=battery_losses_in_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.CAR_BATTERY
        )

        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(config: CarBatteryConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


@dataclass
class EVBatteryState:
    """Electric vehicle battery state class."""

    # state of charge of the battery
    soc: float = 0

    def clone(self):
        """Creates a copy of the Car Battery State."""
        return EVBatteryState(soc=self.soc)
