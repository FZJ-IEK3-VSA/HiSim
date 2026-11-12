"""Case 600 runs end to end: two passes, the outputs on disk, the air held between the set points; a pass declares its kind."""

import json
import pathlib
from typing import Dict

import pandas as pd
import pytest

from hisim.setup_rules.kinds import SetupKind
from hisim.simulationparameters import SimulationParameters
from hisim.verification import metrics, runner, system_builder
from hisim.verification.schema import Case
from hisim.verification.suite import Suite


@pytest.mark.base
def test_a_pass_declares_the_demo_kind(cases: Dict[str, Case], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The simulator of a pass is a test rig to the setup rules (gap analysis §17)."""
    monkeypatch.setenv("HISIM_WRITE_GUARD", "collect")
    parameters = SimulationParameters.full_year(year=system_builder.DEFAULT_YEAR, seconds_per_timestep=3600)
    parameters.result_directory = str(tmp_path)

    assembled = runner.Assembled(system_builder.build(cases["600"]), parameters, initial_temperature_in_celsius=20.0)

    assert assembled.simulator.setup_kind is SetupKind.DEMO


@pytest.mark.verification
def test_case_600_runs_at_one_hour_in_two_passes(cases: Dict[str, Case], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second pass starts where the first one's mass ended; the outputs are written; the air stays in band."""
    monkeypatch.setenv("HISIM_WRITE_GUARD", "collect")
    built = system_builder.build(cases["600"])

    settings = runner.RunSettings(year=system_builder.DEFAULT_YEAR, seconds_per_timestep=3600, output_directory=tmp_path)
    run = runner.run_case(built, settings, Suite.load("ashrae140").outputs_for("600"))

    assert [entry.index for entry in run.passes] == [1, 2]
    assert run.passes[0].initial_temperature_in_celsius == 20.0
    assert run.passes[1].initial_temperature_in_celsius == run.passes[0].final_thermal_mass_temperature_in_celsius
    frame = run.passes[1].frame
    assert len(frame) == 8760
    assert frame[metrics.AIR_TEMPERATURE_COLUMN].min() >= 20.0 - 0.01
    assert frame[metrics.AIR_TEMPERATURE_COLUMN].max() <= 27.0 + 0.01
    assert run.summary["annual_heating_in_megawatt_hour"] > 0.0
    assert run.summary["annual_sensible_cooling_in_megawatt_hour"] > 0.0
    assert set(run.summary["daily_hourly"]) == {"02-01", "07-14"}
    for name in ("parameters", "timeseries", "summary"):
        assert run.files[name].is_file(), name
    with open(run.files["summary"], encoding="utf-8") as summary_file:
        written_summary = json.load(summary_file)
    assert written_summary["reported_pass"] == 2
    assert written_summary["annual_peak_heating_in_kilowatt"]["hour"] in range(1, 25)
    written = pd.read_csv(run.files["timeseries"], index_col="time")
    assert metrics.OPERATIVE_TEMPERATURE_COLUMN in written.columns and len(written) == 8760
