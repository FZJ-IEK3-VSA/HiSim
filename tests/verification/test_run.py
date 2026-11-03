"""Case 600 runs end to end: two passes, the outputs on disk, the air held between the set points."""

import json
import pathlib
from typing import Dict

import pandas as pd
import pytest

from hisim.verification import runner, system_builder
from hisim.verification.schema import Case


@pytest.mark.base
def test_the_summary_sums_heating_and_cooling_from_the_delivered_power() -> None:
    """Positive power is heating, negative cooling; energies in MWh, peaks with their time."""
    index = pd.date_range("2021-01-01", periods=4, freq="h")
    frame = pd.DataFrame(
        {
            "indoor_air_temperature_in_celsius": [20.0, 21.0, 27.0, 26.0],
            "internal_surface_temperature_in_celsius": [20.0, 20.0, 26.0, 26.0],
            "thermal_mass_temperature_in_celsius": [20.0, 20.5, 25.0, 25.5],
            "solar_gain_through_windows_in_watt": [0.0, 1000.0, 2000.0, 0.0],
            runner.DELIVERED_POWER_COLUMN: [1000.0, 500.0, -2000.0, 0.0],
        },
        index=index,
    )
    frame[runner.OPERATIVE_TEMPERATURE_COLUMN] = 0.3 * frame["indoor_air_temperature_in_celsius"] + 0.7 * frame["internal_surface_temperature_in_celsius"]

    summary = runner.summarise(frame, 3600)

    assert summary["annual_heating_in_megawatt_hour"] == pytest.approx(1.5e-3)
    assert summary["annual_sensible_cooling_in_megawatt_hour"] == pytest.approx(2.0e-3)
    assert summary["peak_heating_in_kilowatt"] == 1.0
    assert summary["peak_heating_at"] == str(index[0])
    assert summary["peak_sensible_cooling_at"] == str(index[2])
    assert summary["solar_gain_through_windows_in_kilowatt_hour"] == pytest.approx(3.0)
    assert summary["operative_temperature_in_celsius"]["max"] == pytest.approx(0.3 * 27.0 + 0.7 * 26.0)


@pytest.mark.verification
def test_case_600_runs_at_one_hour_in_two_passes(cases: Dict[str, Case], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second pass starts where the first one's mass ended; the outputs are written; the air stays in band."""
    monkeypatch.setenv("HISIM_WRITE_GUARD", "collect")
    built = system_builder.build(cases["600"])

    run = runner.run_case(built, runner.RunSettings(year=system_builder.DEFAULT_YEAR, seconds_per_timestep=3600, output_directory=tmp_path))

    assert [entry.index for entry in run.passes] == [1, 2]
    assert run.passes[0].initial_temperature_in_celsius == 20.0
    assert run.passes[1].initial_temperature_in_celsius == run.passes[0].final_thermal_mass_temperature_in_celsius
    frame = run.passes[1].frame
    assert len(frame) == 8760
    assert frame["indoor_air_temperature_in_celsius"].min() >= 20.0 - 0.01
    assert frame["indoor_air_temperature_in_celsius"].max() <= 27.0 + 0.01
    assert run.summary["annual_heating_in_megawatt_hour"] > 0.0
    assert run.summary["annual_sensible_cooling_in_megawatt_hour"] > 0.0
    for name in ("parameters", "timeseries", "summary"):
        assert run.files[name].is_file(), name
    with open(run.files["summary"], encoding="utf-8") as summary_file:
        assert json.load(summary_file)["reported_pass"] == 2
    written = pd.read_csv(run.files["timeseries"], index_col="time")
    assert runner.OPERATIVE_TEMPERATURE_COLUMN in written.columns and len(written) == 8760
