"""Unit tests for the HPC-harness runners and submit-script discovery helpers."""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.hpcharness

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


# ------------------------------------------------- system-setup runner & submit script


def test_setup_runner_builds_one_week_parameters():
    """The setup runner builds one-week SimulationParameters and rejects unknown durations."""
    from hpc_harness.runners.hisim_setup_runner import _build_parameters

    params = _build_parameters({"duration": "one_week", "year": 2021, "seconds_per_timestep": 60})
    assert (params.end_date - params.start_date).days == 7
    assert params.seconds_per_timestep == 60
    with pytest.raises(ValueError, match="Unknown duration"):
        _build_parameters({"duration": "two_fortnights"})


def test_setup_runner_applies_post_processing_options():
    """Payload post_processing_options are appended (deduped); unknown names are rejected."""
    from hisim.postprocessingoptions import PostProcessingOptions
    from hpc_harness.runners.hisim_setup_runner import _build_parameters

    params = _build_parameters({"duration": "one_day", "post_processing_options": ["PLOT_LINE", "COMPUTE_KPIS"]})
    assert PostProcessingOptions.PLOT_LINE in params.post_processing_options
    assert PostProcessingOptions.COMPUTE_KPIS in params.post_processing_options
    assert params.post_processing_options.count(PostProcessingOptions.PLOT_LINE) == 1  # no dupes
    with pytest.raises(ValueError, match="Unknown PostProcessingOptions"):
        _build_parameters({"duration": "one_day", "post_processing_options": ["NOT_A_REAL_OPTION"]})


def test_setup_runner_is_registered():
    """The hisim_setup runner is discoverable through the runner registry."""
    from hpc_harness.runners import get_runner

    assert get_runner("hisim_setup").name == "hisim_setup"


def test_find_setups_skips_init_and_excludes(tmp_path):
    """find_setups returns *_setup.py files, skipping __init__.py, non-py, and excludes."""
    sys.path.insert(0, str(SCRIPTS / "hpc_harness"))
    from submit_system_setups import find_setups  # pylint: disable=import-error

    for name in ("__init__.py", "a_setup.py", "b_setup.py", "notes.txt"):
        (tmp_path / name).write_text("", encoding="utf-8")
    found = find_setups(tmp_path, exclude=["b_setup"])
    assert [p.name for p in found] == ["a_setup.py"]


def test_find_json_setups_filters_by_name(tmp_path):
    """find_json_setups keeps only *.scenario.json whose name contains the (case-insensitive) filter."""
    sys.path.insert(0, str(SCRIPTS / "hpc_harness"))
    from submit_json_setups import find_json_setups  # pylint: disable=import-error

    for name in ("household_gas_building_sizer.scenario.json", "Household_HP_Building_Sizer.scenario.json",
                 "basic_household.scenario.json", "household_gas_building_sizer.py",
                 "notes.txt"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    found = {p.name for p in find_json_setups(tmp_path, "building_sizer")}
    assert found == {"Household_HP_Building_Sizer.scenario.json",
                     "household_gas_building_sizer.scenario.json"}  # case-insensitive; .py/non-matching excluded


def test_submit_json_setups_dry_run_builds_one_job_per_matching_scenario(tmp_path, capsys):
    """main() --dry-run resolves each matching scenario once and lists one job per setup.

    Guards the jobs loop (resolve() cached per scenario, str(sim_params) hoisted out of
    the loop) against regressions: it must select only matching *.scenario.json files,
    strip the suffix for the label, report the shared sim-params name, and short-circuit
    before contacting the harness server.
    """
    sys.path.insert(0, str(SCRIPTS / "hpc_harness"))
    from submit_json_setups import main  # pylint: disable=import-error

    for name in ("alpha_building_sizer.scenario.json",
                 "beta_building_sizer.scenario.json",
                 "basic_household.scenario.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    sim_params_name = "test_dry_run.simulation.json"
    (tmp_path / sim_params_name).write_text("{}", encoding="utf-8")

    rc = main([
        "--setup-dir", str(tmp_path),
        "--name-filter", "building_sizer",
        "--sim-params", sim_params_name,
        "--dry-run",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    # only the two matching scenarios, labels strip the .scenario.json suffix
    assert "  - alpha_building_sizer" in out
    assert "  - beta_building_sizer" in out
    assert "basic_household" not in out
    assert "2 JSON scenario" in out
    assert sim_params_name in out
    assert "nothing submitted" in out
