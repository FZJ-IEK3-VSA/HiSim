"""Part load in the recorded twins: a partial charge ends the hot-water tank in its controller's target band.

Each case runs one week of 2021 of a twin with the shipped predefined occupancy profile:

* at 900 s with the default 600 s threshold, a controller commands part load on some hot-water steps, every ratio
  lies in [0, 1], and a step whose ratio lies strictly between 0 and 1 and that converged before the simulator forced
  convergence ends the tank in the controller's target band, from its published target to the target plus
  ``PartLoadRule.TARGET_BAND_IN_KELVIN``. Two cases: the gas boiler's hot-water charge in the gas twin's first week,
  and the solar pump in the gas + solar twin's first week of June, where the default 4 m² collector fills the tank to
  its 80 °C maximum storage temperature (hisim-fxix.11);
* at 900 s with the threshold at 900 s the twin is refused when it is built, since its space-heating buffer is a
  start-of-step node that cannot take whole steps above 600 s
  (``tests/test_space_heating_buffer_whole_step_refusal.py``); that a threshold at the step length keeps every
  hot-water charge whole is checked on the controller (``tests/test_part_load_dhw.py``).

Every test here simulates a whole energy system, so it runs in the ``extendedbase2`` shard.
"""

from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim.calculation_scope import CalculationScope
from hisim.components.generic_boiler import GenericBoilerController
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.solar_thermal_system import SolarThermalSystemController
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system
from hisim.part_load import PartLoadRule


class PartLoadTwins:
    """One week of a twin at 900 s, and the columns the tests read."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The passes the simulator runs before it forces convergence; a step that needed more had a forced pass.
    UNFORCED_PASSES: int = 12

    #: The cases by name: the twin, the controller's part-load ratio output and target output, and the window. The
    #: simulator evaluates each controller before its device and the device before the tank (the kind order of #922).
    #: Gas boiler: in the first week of 2021 every partial step of the gas twin converges (15 of 15, measured
    #: 2026-10-12), ending 0.02 to 0.05 K above the target. Solar pump: the default 4 m² collector first runs on
    #: 20 January, so the case takes the first week of June, which mid-year windows read with the window's own weather,
    #: sun and occupancy (#988, #1031, #1032); there the pump runs part load on its way to the 80 °C maximum, and
    #: every partial step converges (4 of 4, measured 2026-10-19), ending 0.016 to 0.023 K above the target.
    CASES: ClassVar[Dict[str, Tuple[str, str, str, Tuple[str, str]]]] = {
        "gas_boiler_first_week": (
            "household_gas_building_sizer",
            f"ModulatingBoilerController - {GenericBoilerController.PartLoadRatioDhw} [",
            f"ModulatingBoilerController - {GenericBoilerController.TargetTemperatureDhwInCelsius} [",
            ("2021-01-01T00:00:00", "2021-01-08T00:00:00"),
        ),
        "solar_pump_june_week": (
            "household_gas_solar_thermal_building_sizer",
            f"SolarThermalSystemController - {SolarThermalSystemController.PartLoadRatio} [",
            f"SolarThermalSystemController - {SolarThermalSystemController.TargetTemperatureInCelsius} [",
            ("2021-06-01T00:00:00", "2021-06-08T00:00:00"),
        ),
    }

    @classmethod
    def run(cls, twin: str, window: Tuple[str, str], work: Path) -> Tuple[pd.DataFrame, np.ndarray]:
        """Run ``twin`` over ``window`` at 900 s in ``work``, with the predefined occupancy profile.

        Args:
            twin: The stem of the recorded energy-system file under ``energy_systems/``.
            window: The start and the end of the run, ISO dates.
            work: The directory the run writes to.

        Returns:
            The result frame and the passes every step took.
        """
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        start, end = window
        values = {
            "start_date": start,
            "end_date": end,
            "seconds_per_timestep": 900,
            "country": "DE",
            "logging_level": 3,
            "post_processing_options": [],
        }
        path = work / "window.simulation.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        results_directory = work / "results"
        passes: List[int] = []
        with CalculationScope.open(label=str(energy_system), run_directory=str(results_directory)):
            simulation_parameters = SimulationParametersReader.read(path)
            simulation_parameters.result_directory = str(results_directory)
            simulator = build_energy_system(energy_system, simulation_parameters).simulator
            process_one_timestep = simulator.process_one_timestep

            def counting(step, stsv):  # the passes the simulator returns per step
                result = process_one_timestep(step, stsv)
                passes.append(result[1])
                return result

            setattr(simulator, "process_one_timestep", counting)
            simulator.run_all_timesteps()
            return simulator.results_data_frame, np.array(passes)

    @staticmethod
    def column(frame: pd.DataFrame, prefix: str) -> np.ndarray:
        """The one column whose name starts with ``prefix``."""
        names: List[str] = [name for name in frame.columns if name.startswith(prefix)]
        assert len(names) == 1, (prefix, names)
        values: np.ndarray = frame[names[0]].to_numpy(dtype=float)
        return values


@pytest.mark.extendedbase2
@pytest.mark.parametrize("case", sorted(PartLoadTwins.CASES))
def test_a_partial_charge_ends_the_tank_at_its_target(case: str, tmp_path: Path) -> None:
    """Above the threshold some hot-water steps run part load, and each that converged ends the tank in the band.

    A controller whose rule aimed elsewhere would end a converged partial step outside the band. A step the simulator
    had to force ends wherever its last pass left it; the year benchmark counts those.
    """
    twin, ratio_prefix, target_prefix, window = PartLoadTwins.CASES[case]
    frame, passes = PartLoadTwins.run(twin, window, tmp_path)
    ratio = PartLoadTwins.column(frame, ratio_prefix)
    target_c = PartLoadTwins.column(frame, target_prefix)
    end_c = PartLoadTwins.column(frame, f"DHWStorage - {SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius} [")
    assert np.all((ratio >= 0.0) & (ratio <= 1.0))
    partial = (ratio > 0.0) & (ratio < 1.0) & (passes <= PartLoadTwins.UNFORCED_PASSES)
    assert partial.sum() > 0
    deviation_k = end_c[partial] - target_c[partial]
    assert np.all((deviation_k >= 0.0) & (deviation_k <= PartLoadRule.TARGET_BAND_IN_KELVIN))
