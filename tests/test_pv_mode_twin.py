"""The heat-pump twin with a battery runs PV mode in a spring week at 900 s and at 60 s, on PV electricity.

The energy manager raises the set temperatures as PV mode: it decides on the surplus after the battery with the heat
pump's own need in place of its draw, and sends the heat pump's space-heating controller a power target between the
heat pump's minimum and full load. The test runs the recorded twin ``household_heatpump_building_sizer`` from 12 to 19
April 2021 and checks, at both step lengths, that

* PV mode runs, and the raise of the set temperatures is on exactly while the target is above 0;
* every target lies between the minimum-load and the full-load electricity the heat pump published in that step;
* the grid supplies at most 1 % of the heat pump's electricity while PV mode is on: what it does supply comes from a
  raised hot-water charge, which runs at full power whatever the target, and from the band below the minimum load in
  which PV mode stays on.

The energy balance check runs in every simulation and fails the run when a balance does not close, so a finished run
is also the proof that the balance closes.
"""

from pathlib import Path
from typing import ClassVar, Dict, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.more_advanced_heat_pump.heat_pump import MoreAdvancedHeatPumpHPLib
from hisim.energy_system.executor import run_energy_system

pytestmark = pytest.mark.extendedbase


class SpringWeek:
    """One spring week of the heat-pump twin at one step length, and the columns the test reads."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The twin: the heat pump with buffer and hot water, PV, a battery and the energy manager.
    TWIN = "household_heatpump_building_sizer"

    #: The window: a spring week with PV surplus and some heating.
    WINDOW: Tuple[str, str] = ("2021-04-12T00:00:00", "2021-04-19T00:00:00")

    #: The largest share of the heat pump's electricity in PV mode the grid may supply, dimensionless.
    LARGEST_GRID_SHARE_IN_PV_MODE: ClassVar[float] = 0.01

    #: The least PV mode the week must show, in h: measured 54 to 57 h on 2026-11-07.
    LEAST_PV_MODE_IN_HOURS: ClassVar[float] = 20.0

    @classmethod
    def run(cls, seconds_per_timestep: int, work: Path) -> pd.DataFrame:
        """Run the twin over :attr:`WINDOW` at one step length in ``work`` and return its results, one column per output."""
        text = (cls.ROOT / "energy_systems" / f"{cls.TWIN}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{cls.TWIN}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        parameters = work / "spring.simulation.yaml"
        start, end = cls.WINDOW
        parameters.write_text(
            yaml.safe_dump(
                {
                    "start_date": start,
                    "end_date": end,
                    "seconds_per_timestep": seconds_per_timestep,
                    "country": "DE",
                    "logging_level": 3,
                    "post_processing_options": [],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        built = run_energy_system(energy_system, parameters, result_directory=str(work / "results"))
        return built.simulator.results_data_frame

    @staticmethod
    def column(results: pd.DataFrame, component: str, field: str) -> np.ndarray:
        """Return the one result column of ``component``'s output ``field``."""
        matches = [name for name in results.columns if name.startswith(f"{component} - {field} [")]
        assert len(matches) == 1, (component, field, matches)
        values: np.ndarray = results[matches[0]].to_numpy(dtype=float)
        return values


@pytest.mark.parametrize("seconds_per_timestep", [900, 60])
def test_pv_mode_runs_on_pv_electricity_within_the_heat_pump_s_range(seconds_per_timestep: int, tmp_path: Path) -> None:
    """Catches PV mode that never comes on, a raise without a target, a target outside the range, or PV mode on the grid."""
    results = SpringWeek.run(seconds_per_timestep, tmp_path)
    manager = "L2EMSElectricityController"
    heat_pump = "MoreAdvancedHeatPumpHPLib"
    target_in_watt = SpringWeek.column(results, manager, L2GenericEnergyManagementSystem.HeatPumpElectricalPowerTargetInWatt)
    raise_in_kelvin = SpringWeek.column(results, manager, L2GenericEnergyManagementSystem.BuildingTemperatureModifierInKelvin)
    minimum_in_watt = SpringWeek.column(results, heat_pump, MoreAdvancedHeatPumpHPLib.ElectricalPowerAtMinimumLoadSpaceHeatingInWatt)
    full_in_watt = SpringWeek.column(results, heat_pump, MoreAdvancedHeatPumpHPLib.ElectricalPowerAtFullLoadSpaceHeatingInWatt)
    heat_pump_in_watt = SpringWeek.column(results, heat_pump, MoreAdvancedHeatPumpHPLib.ElectricalInputPowerTotal)
    grid_in_watt = SpringWeek.column(results, "ElectricityMeter", ElectricityMeter.ElectricityFromGridInWatt)

    pv_mode = target_in_watt > 0.0
    hours_per_step = seconds_per_timestep / 3600.0
    assert pv_mode.sum() * hours_per_step >= SpringWeek.LEAST_PV_MODE_IN_HOURS
    assert np.array_equal(pv_mode, raise_in_kelvin > 0.0)
    assert np.all(target_in_watt[pv_mode] >= minimum_in_watt[pv_mode] - 1e-6)
    assert np.all(target_in_watt[pv_mode] <= full_in_watt[pv_mode] + 1e-6)
    grid_share: Dict[str, float] = {
        "grid": float(grid_in_watt[pv_mode].sum()),
        "heat pump": float(heat_pump_in_watt[pv_mode].sum()),
    }
    assert grid_share["grid"] <= SpringWeek.LARGEST_GRID_SHARE_IN_PV_MODE * grid_share["heat pump"], grid_share
