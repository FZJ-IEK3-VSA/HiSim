"""The part-load threshold and the unit of a part-load ratio.

Above ``SimulationParameters.part_load_above_seconds`` a device its controller has switched on may run only a
fraction of the step; at and below it every device runs whole steps. These tests check the threshold's default, its
validation, that keys and files stay byte-identical at the default, and how a ratio is aggregated.
"""

import pathlib
from typing import Any

import pytest

from hisim import loadtypes as lt
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.executor import SimulationParametersReader
from hisim.energy_system.parameters_format import ParameterFileName, ParameterFileWriter, ParameterNormalisation
from hisim.simulationparameters import PartLoadThresholdError
from tests.part_load_rigs import Parameters


@pytest.mark.base
@pytest.mark.parametrize(
    "seconds_per_timestep, runs", [(60, False), (600, False), (601, True), (900, True), (3600, True)]
)
def test_the_default_threshold_is_600_s_and_applies_strictly_above_it(seconds_per_timestep: int, runs: bool) -> None:
    """Part load runs only when a step is longer than 600 s."""
    parameters = Parameters.one_day(seconds_per_timestep)
    assert parameters.part_load_above_seconds == 600
    assert parameters.runs_part_load() is runs


@pytest.mark.base
def test_a_threshold_at_the_step_length_keeps_whole_steps() -> None:
    """A 900 s run with ``part_load_above_seconds = 900`` runs whole steps."""
    assert not Parameters.one_day(900, part_load_above_seconds=900).runs_part_load()


@pytest.mark.base
@pytest.mark.parametrize("value", [-1, True, "600", float("inf"), float("nan")])
def test_a_threshold_that_is_not_a_step_length_is_refused(value: Any) -> None:
    """A negative, boolean, textual or non-finite threshold raises ``PartLoadThresholdError``."""
    with pytest.raises(PartLoadThresholdError, match="part_load_above_seconds"):
        Parameters.one_day(part_load_above_seconds=value)


@pytest.mark.base
def test_keys_and_files_are_unchanged_at_the_default_and_carry_another_threshold() -> None:
    """At the default the unique key, the normalised record and its name are what they were; otherwise they say so."""
    default = Parameters.one_day()
    explicit_default = Parameters.one_day(part_load_above_seconds=600)
    other = Parameters.one_day(part_load_above_seconds=900)
    assert default.get_unique_key() == explicit_default.get_unique_key()
    assert "part_load" not in default.get_unique_key()
    assert other.get_unique_key() == default.get_unique_key() + "###part_load_above=900"
    normalised = ParameterNormalisation.normalise(default)
    assert ParameterNormalisation.PART_LOAD_KEY not in normalised
    assert ParameterFileName.stem(normalised) == "one_day_hourly_plain"
    other_normalised = ParameterNormalisation.normalise(other)
    assert other_normalised[ParameterNormalisation.PART_LOAD_KEY] == 900
    assert ParameterFileName.stem(other_normalised) == "one_day_hourly_plain_partload900"
    assert "\npart_load_above_seconds: 900\n" in ParameterFileWriter.text(other_normalised)


@pytest.mark.base
@pytest.mark.parametrize("threshold_seconds", [None, 900])
def test_a_simulation_yaml_round_trips_the_threshold(tmp_path: pathlib.Path, threshold_seconds: Any) -> None:
    """What the writer writes, the YAML reader reads back as the same threshold."""
    extra = {} if threshold_seconds is None else {"part_load_above_seconds": threshold_seconds}
    written = ParameterNormalisation.normalise(Parameters.one_day(**extra))
    path = tmp_path / "round_trip.simulation.yaml"
    path.write_text(ParameterFileWriter.text(written), encoding="utf-8")
    read = SimulationParametersReader.read(path)
    assert read.part_load_above_seconds == (600 if threshold_seconds is None else threshold_seconds)
    assert ParameterNormalisation.normalise(read) == written


@pytest.mark.base
def test_the_json_spelling_carries_the_threshold_and_a_bad_one_names_the_file(tmp_path: pathlib.Path) -> None:
    """A ``*.simulation.json`` passes the key through; a negative value is a format error naming the file."""
    path = tmp_path / "threshold.simulation.json"
    path.write_text(
        '{"start_date": "2021-01-01T00:00:00", "end_date": "2021-01-02T00:00:00", '
        '"seconds_per_timestep": 3600, "part_load_above_seconds": 1800}',
        encoding="utf-8",
    )
    assert SimulationParametersReader.read(path).part_load_above_seconds == 1800
    values = {"start_date": "2021-01-01T00:00:00", "end_date": "2021-01-02T00:00:00", "seconds_per_timestep": 3600}
    with pytest.raises(EnergySystemFormatError, match="somewhere.simulation.yaml"):
        SimulationParametersReader.build({**values, "part_load_above_seconds": -5}, "somewhere.simulation.yaml")


@pytest.mark.base
def test_a_part_load_ratio_is_a_fraction_aggregated_as_a_mean() -> None:
    """The fraction unit is averaged over time by the post-processing, never summed like an energy.

    A sum of part-load ratios over a year means nothing; their mean is the share of the year a device ran at full load.
    """
    assert lt.Units.FRACTION in lt.UNITS_USING_MEAN_AGGREGATION
