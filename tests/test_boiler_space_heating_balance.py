"""A boiler books the space heating its buffer receives: booked heat against delivered heat, loss and content change.

The boiler books the heat its space-heating water carries, ``m c (T_sup - T_ret)``. The buffer it charges passes that
heat on to the heat distribution system, loses some through its walls and keeps the rest as a change of its
content. Over a winter day at 60 s, the reference step, the boiler's booked heat closes against the sum of those
three. The pellet and wood-chip twins did not: their on/off controller asked for full power on a lift that shrank to
a few mK while the buffer reached its set temperature, the boiler sized its flow to that lift (hundreds of kg/s) and
booked its full heat, and the buffer, which mixes a step's inflow into its water, took in only a small part of it.
Over the year 2021 they booked 51.7 MWh for 11.0 MWh delivered (hisim-component-physics-4g9.63). The boiler's pump now has a largest
flow, and a lift below the rise the heat gives at that flow is raised to it.

The buffer is a start-of-step node: every circuit is booked on the temperature it starts the step at, and the buffer
ends the step at that temperature plus the net heat over its heat capacity, so the balance closes on every step to
the simulator's convergence tolerance. Before, the buffer mixed a step's inflowing water into its own and its content
grew by only ``M / (M + m dt)`` of what its inflows brought: on this window the booked heat lay +2.21 % (gas),
+7.51 % (pellets) and +6.40 % (wood chips) above the sinks at 60 s (over the year 2021 +0.87 %, +5.07 % and +5.41 %),
and before the pump limit the pellet and wood-chip twins booked 144.7 and 142.4 kWh on this day for 50.1 and
50.0 kWh, +189 % and +185 %.

Every test here simulates a whole energy system, so it runs in the ``extendedbase2`` shard.
"""

from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim.calculation_scope import CalculationScope
from hisim.components.generic_boiler import GenericBoiler
from hisim.components.heat_distribution_system import HeatDistribution
from hisim.components.simple_water_storage import SimpleHotWaterStorage
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system


class BoilerTwinDay:
    """One winter day of a boiler twin at 60 s, and the space-heating balance read from its results."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The window: a January day on which the boiler heats the buffer on most steps.
    WINDOW: Tuple[str, str] = ("2021-01-10T00:00:00", "2021-01-11T00:00:00")

    #: The twins, each with the name of its boiler.
    TWINS: ClassVar[Dict[str, str]] = {
        "household_gas_building_sizer": "CondensingGasBoiler",
        "household_pellets_building_sizer": "ConventionalPelletBoiler",
        "household_wood_chips_building_sizer": "ConventionalWoodChipBoiler",
    }

    #: How far the booked heat may lie from the delivered heat plus the buffer's loss and content change, relative:
    #: the start-of-step buffer closes to the simulator's convergence tolerance; the earlier mixing law left up to
    #: 7.5 % at 60 s, and a booking that ignores the pump's largest flow lies near +190 %.
    RELATIVE_TOLERANCE: ClassVar[float] = 1e-6

    #: Seconds per hour, to turn the step's watts into kilowatt hours.
    SECONDS_PER_HOUR: ClassVar[float] = 3600.0

    #: Watt hours per kilowatt hour.
    WATT_HOURS_PER_KILOWATT_HOUR: ClassVar[float] = 1000.0

    @classmethod
    def run(cls, twin: str, work: Path) -> Tuple[pd.DataFrame, float]:
        """Run ``twin`` over :attr:`WINDOW` at 60 s in ``work`` with the shipped predefined occupancy profile.

        Args:
            twin: A twin of :attr:`TWINS`.
            work: An empty directory for the energy system, its parameters and its results.

        Returns:
            The result frame and the largest flow of the boiler's pump, in kg/s.
        """
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        start, end = cls.WINDOW
        values = {
            "start_date": start,
            "end_date": end,
            "seconds_per_timestep": 60,
            "country": "DE",
            "logging_level": 3,
            "post_processing_options": [],
        }
        path = work / "window.simulation.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        results_directory = work / "results"
        with CalculationScope.open(label=str(energy_system), run_directory=str(results_directory)):
            simulation_parameters = SimulationParametersReader.read(path)
            simulation_parameters.result_directory = str(results_directory)
            simulator = build_energy_system(energy_system, simulation_parameters).simulator
            boilers = [
                wrapper.my_component
                for wrapper in simulator.wrapped_components
                if isinstance(wrapper.my_component, GenericBoiler)
            ]
            assert len(boilers) == 1, boilers
            simulator.run_all_timesteps()
            return simulator.results_data_frame, boilers[0].pump.maximal_mass_flow_in_kg_per_second

    @staticmethod
    def column(frame: pd.DataFrame, prefix: str) -> np.ndarray:
        """The one column whose name starts with ``prefix``."""
        names: List[str] = [name for name in frame.columns if name.startswith(prefix)]
        assert len(names) == 1, (prefix, names)
        values: np.ndarray = frame[names[0]].to_numpy(dtype=float)
        return values

    @classmethod
    def energy_in_kwh(cls, power_in_watt: np.ndarray, seconds_per_timestep: float) -> float:
        """The energy of a power series over the window, in kWh."""
        energy_in_wh = float(power_in_watt.sum()) * seconds_per_timestep / cls.SECONDS_PER_HOUR
        return energy_in_wh / cls.WATT_HOURS_PER_KILOWATT_HOUR


@pytest.mark.extendedbase2
@pytest.mark.parametrize("twin", sorted(BoilerTwinDay.TWINS))
def test_the_boiler_books_the_space_heating_its_buffer_receives(twin: str, tmp_path: Path) -> None:
    """Booked space heating closes against delivered heat, buffer loss and content change, within the pump's flow.

    The balance runs from the end of the first step to the end of the last, so the content change is the difference
    of the buffer's published contents and the powers are summed over every step but the first. A boiler that sized
    its flow to a lift of a few mK booked almost three times the right heat on this day, at hundreds of kg/s. The
    pump's largest flow is not bounded here in kg/s: it follows the twin's boiler, which is sized to the building, and
    :func:`tests.test_generic_boiler.test_a_lift_close_to_zero_runs_the_pumps_largest_flow_and_books_the_burners_heat`
    pins how it is sized.
    """
    boiler = BoilerTwinDay.TWINS[twin]
    frame, maximal_mass_flow_in_kg_per_second = BoilerTwinDay.run(twin, tmp_path)
    booked_in_watt = BoilerTwinDay.column(frame, f"{boiler} - {GenericBoiler.ThermalPowerGenerationSh} [")
    mass_flow_in_kg_per_second = BoilerTwinDay.column(frame, f"{boiler} - {GenericBoiler.WaterOutputMassFlowSh} [")
    delivered_in_watt = BoilerTwinDay.column(
        frame, f"HeatDistributionSystem - {HeatDistribution.ThermalPowerDelivered} ["
    )
    standby_loss_in_watt = BoilerTwinDay.column(
        frame, f"SimpleHotWaterStorage - {SimpleHotWaterStorage.StandbyHeatLoss} ["
    )
    content_in_wh = BoilerTwinDay.column(
        frame, f"SimpleHotWaterStorage - {SimpleHotWaterStorage.ThermalEnergyInStorage} ["
    )
    booked_in_kwh = BoilerTwinDay.energy_in_kwh(booked_in_watt[1:], 60)
    sinks_in_kwh = (
        BoilerTwinDay.energy_in_kwh(delivered_in_watt[1:], 60)
        + BoilerTwinDay.energy_in_kwh(standby_loss_in_watt[1:], 60)
        + (content_in_wh[-1] - content_in_wh[0]) / BoilerTwinDay.WATT_HOURS_PER_KILOWATT_HOUR
    )
    assert booked_in_kwh > 0.0
    assert booked_in_kwh == pytest.approx(sinks_in_kwh, rel=BoilerTwinDay.RELATIVE_TOLERANCE)
    assert mass_flow_in_kg_per_second.max() <= maximal_mass_flow_in_kg_per_second * (1.0 + 1e-12)
