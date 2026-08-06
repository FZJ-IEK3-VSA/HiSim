"""Unit tests for the RenoVisor CLI helpers (`hisim.renovisor.__main__`).

Regression focus: base and measures runs sharing one ``--result-dir`` must never share files.
With a common directory, the fixed module-config filename let a parallel base (gas) run
overwrite the measures run's config before the heat-pump setup read it, failing with
"Heating system needs to be heat pump for this system setup".
"""

import argparse
from pathlib import Path

from hisim.renovisor.__main__ import _prepare_result_directory, _sanitize_for_filesystem


def _arguments(result_dir: Path, variant: str) -> argparse.Namespace:
    return argparse.Namespace(result_dir=str(result_dir), variant=variant)


def test_variants_sharing_a_result_dir_get_separate_run_directories(tmp_path: Path) -> None:
    """Base and measures runs of one job must not write into the same directory."""
    base_dir, base_owned = _prepare_result_directory(_arguments(tmp_path, "base"), "job-42")
    measures_dir, measures_owned = _prepare_result_directory(_arguments(tmp_path, "measures"), "job-42")

    assert base_dir != measures_dir
    assert base_dir.parent == tmp_path and measures_dir.parent == tmp_path
    assert base_dir.is_dir() and measures_dir.is_dir()
    # caller-supplied directories are never owned (deleted) by the translator
    assert not base_owned and not measures_owned


def test_different_jobs_sharing_a_result_dir_get_separate_run_directories(tmp_path: Path) -> None:
    """Two concurrent jobs sharing one --result-dir must not clobber each other's configs."""
    gas_dir, _ = _prepare_result_directory(_arguments(tmp_path, "base"), "gas-job")
    heatpump_dir, _ = _prepare_result_directory(_arguments(tmp_path, "base"), "heatpump-job")

    assert gas_dir != heatpump_dir


def test_temporary_result_directory_is_owned_and_unique() -> None:
    """Without --result-dir a fresh owned temp directory is created per call."""
    arguments = argparse.Namespace(result_dir=None, variant="base")
    first_dir, first_owned = _prepare_result_directory(arguments, "job-42")
    second_dir, second_owned = _prepare_result_directory(arguments, "job-42")
    try:
        assert first_owned and second_owned
        assert first_dir != second_dir
        assert first_dir.is_dir() and second_dir.is_dir()
    finally:
        first_dir.rmdir()
        second_dir.rmdir()


def test_sanitize_for_filesystem_replaces_hostile_characters() -> None:
    """Opaque job ids (slashes, colons, ...) must yield a safe directory name."""
    assert _sanitize_for_filesystem("job/42:extra\\bad?") == "job_42_extra_bad_"
    assert _sanitize_for_filesystem("Safe.Job-1_x") == "Safe.Job-1_x"
    assert _sanitize_for_filesystem("") == "job"
