"""Part load in the recorded twins: a partial charge ends the hot-water tank in its controller's target band.

Each twin runs the first week of 2021 with the shipped predefined occupancy profile:

* at 900 s with the default 600 s threshold, a controller commands part load on some hot-water steps, every ratio
  lies in [0, 1], and a step whose ratio lies strictly between 0 and 1 ends the tank in the controller's target band,
  from its published target to the target plus ``PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN``;
* at 900 s with the threshold at 900 s, every hot-water step runs whole.

Every test here simulates a whole energy system, so it runs in the ``extendedbase2`` shard.
"""

from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest
import yaml

from hisim.components.generic_boiler import GenericBoilerController
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.part_load import PartLoadRatioRegulator
from hisim.energy_system.executor import run_energy_system


class PartLoadTwins:
    """One week of a twin at 900 s, and the columns the tests read."""

    #: The repository's root.
    ROOT = Path(__file__).resolve().parents[1]

    #: The window: the first week of 2021.
    WINDOW: Tuple[str, str] = ("2021-01-01T00:00:00", "2021-01-08T00:00:00")

    #: The twins, each with its controller's part-load ratio output and target output.
    TWINS: ClassVar[Dict[str, Tuple[str, str]]] = {
        "household_gas_building_sizer": (
            f"ModulatingBoilerController - {GenericBoilerController.PartLoadRatioDhw} [",
            f"ModulatingBoilerController - {GenericBoilerController.TargetTemperatureDhwInCelsius} [",
        ),
    }

    @classmethod
    def run(cls, twin: str, work: Path, **parameters: int) -> pd.DataFrame:
        """Run ``twin`` over :attr:`WINDOW` at 900 s in ``work``, with extra simulation parameters."""
        text = (cls.ROOT / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        energy_system = work / f"{twin}.energy_system.yaml"
        energy_system.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
        start, end = cls.WINDOW
        values = {
            "start_date": start,
            "end_date": end,
            "seconds_per_timestep": 900,
            "country": "DE",
            "logging_level": 3,
            "post_processing_options": [],
            **parameters,
        }
        path = work / "window.simulation.yaml"
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        built = run_energy_system(energy_system, path, result_directory=str(work / "results"))
        return built.simulator.results_data_frame

    @staticmethod
    def column(frame: pd.DataFrame, prefix: str) -> np.ndarray:
        """The one column whose name starts with ``prefix``."""
        names: List[str] = [name for name in frame.columns if name.startswith(prefix)]
        assert len(names) == 1, (prefix, names)
        values: np.ndarray = frame[names[0]].to_numpy(dtype=float)
        return values


@pytest.mark.extendedbase2
@pytest.mark.parametrize("twin", sorted(PartLoadTwins.TWINS))
def test_a_partial_charge_ends_the_tank_at_its_target(twin: str, tmp_path: Path) -> None:
    """Above the threshold some hot-water steps run part load, and each of them ends the tank in the target band.

    A controller whose search paired a trial with the wrong answer, or stopped early, would end a partial step
    outside the band.
    """
    ratio_prefix, target_prefix = PartLoadTwins.TWINS[twin]
    frame = PartLoadTwins.run(twin, tmp_path)
    ratio = PartLoadTwins.column(frame, ratio_prefix)
    target_c = PartLoadTwins.column(frame, target_prefix)
    end_c = PartLoadTwins.column(frame, f"DHWStorage - {SimpleDHWStorage.WaterTemperatureAtEndOfStepInCelsius} [")
    assert np.all((ratio >= 0.0) & (ratio <= 1.0))
    partial = (ratio > 0.0) & (ratio < 1.0)
    assert partial.sum() > 0
    deviation_k = end_c[partial] - target_c[partial]
    assert np.all((deviation_k >= 0.0) & (deviation_k <= PartLoadRatioRegulator.TARGET_BAND_IN_KELVIN))


@pytest.mark.extendedbase2
@pytest.mark.parametrize("twin", sorted(PartLoadTwins.TWINS))
def test_a_threshold_at_the_step_length_keeps_every_charge_whole(twin: str, tmp_path: Path) -> None:
    """With ``part_load_above_seconds`` at the step length, every hot-water step runs the whole step."""
    ratio_prefix, _ = PartLoadTwins.TWINS[twin]
    frame = PartLoadTwins.run(twin, tmp_path, part_load_above_seconds=900)
    ratio = PartLoadTwins.column(frame, ratio_prefix)
    assert set(np.unique(ratio)) <= {0.0, 1.0}
    assert (ratio == 1.0).sum() > 0
