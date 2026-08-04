"""Tests for detecting a local-LPG run that produced no usable results.

``pylpg.LPGExecutor.execute_lpg_binaries`` calls ``subprocess.run`` without ``check=True``
and discards the ``CompletedProcess``, so an LPG that dies part-way through writing its
output looks exactly like a successful one. The connector then walked into a bare
``FileNotFoundError`` on ``SumProfiles.HH1.Electricity.csv`` several stack frames later,
after the working directory -- and with it the LPG's own log -- had been deleted.

These tests cover the verification, the diagnostics and the retry, all without invoking the
LPG binary.
"""

import os
import subprocess

import pytest

from hisim.components.loadprofilegenerator_utsp_connector import (
    DEFAULT_LOCAL_LPG_ATTEMPTS,
    DEFAULT_LOCAL_LPG_TIMEOUT_SECONDS,
    LOCAL_LPG_ATTEMPTS_ENV_VAR,
    LOCAL_LPG_FINISHED_FLAG,
    LOCAL_LPG_LOG_FILE,
    LOCAL_LPG_TIMEOUT_ENV_VAR,
    LocalLpgExecutionError,
    UtspLpgConnector,
    describe_local_lpg_failure,
    local_lpg_attempts,
    local_lpg_timeout_seconds,
    missing_local_lpg_result_files,
)

pytestmark = pytest.mark.base


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test against a known-clean environment."""
    monkeypatch.delenv(LOCAL_LPG_ATTEMPTS_ENV_VAR, raising=False)
    monkeypatch.delenv(LOCAL_LPG_TIMEOUT_ENV_VAR, raising=False)


def _completed(returncode: int = 1, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a stand-in for the LPG's ``CompletedProcess``."""
    return subprocess.CompletedProcess(args=["simengine2"], returncode=returncode, stdout=stdout, stderr=stderr)


# --------------------------------------------------------------------------- #
# result verification
# --------------------------------------------------------------------------- #
def test_missing_files_are_reported_relative(tmp_path) -> None:
    """The check names exactly the required files the LPG failed to write."""
    results = tmp_path / "Results"
    results.mkdir()
    (results / "BodilyActivityLevel.High.HH1.json").write_text("{}", encoding="utf-8")

    missing = missing_local_lpg_result_files(
        str(tmp_path),
        ["Results/BodilyActivityLevel.High.HH1.json", "Results/SumProfiles.HH1.Electricity.csv"],
    )

    assert missing == ["Results/SumProfiles.HH1.Electricity.csv"]


def test_partial_run_is_detected(tmp_path) -> None:
    """Regression: bodily-activity JSONs present but no sum profiles is a *failed* run.

    That is the exact shape of the golden-check failure this guards against -- the run got far
    enough to write the files read first, so the old code only noticed at the CSV.
    """
    results = tmp_path / "Results"
    results.mkdir()
    for name in ("BodilyActivityLevel.High.HH1.json", "BodilyActivityLevel.Low.HH1.json"):
        (results / name).write_text("{}", encoding="utf-8")

    missing = missing_local_lpg_result_files(
        str(tmp_path),
        [
            "Results/SumProfiles.HH1.Electricity.csv",
            "Results/SumProfiles.HH1.Warm Water.csv",
            "Results/BodilyActivityLevel.High.HH1.json",
            "Results/BodilyActivityLevel.Low.HH1.json",
        ],
    )

    assert missing == ["Results/SumProfiles.HH1.Electricity.csv", "Results/SumProfiles.HH1.Warm Water.csv"]


def test_complete_run_reports_nothing_missing(tmp_path) -> None:
    """A run that wrote everything must not be flagged."""
    results = tmp_path / "Results"
    results.mkdir()
    (results / "SumProfiles.HH1.Electricity.csv").write_text("Time;Sum [kWh]\n", encoding="utf-8")

    assert missing_local_lpg_result_files(str(tmp_path), ["Results/SumProfiles.HH1.Electricity.csv"]) == []


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #
def test_failure_message_carries_the_post_mortem(tmp_path) -> None:
    """Everything needed to diagnose the failure must be in the message.

    The working directory is deleted during cleanup and CI keeps only the traceback, so
    anything not quoted here is lost.
    """
    result_folder = tmp_path / "results"
    (result_folder / "Results").mkdir(parents=True)
    (result_folder / LOCAL_LPG_LOG_FILE).write_text(
        "10:31:15 [INFOR] Starting CalcManager.Run - Core Simulation\n"
        "10:32:25 [ERROR] disk full while writing Results.HH1.sqlite\n",
        encoding="utf-8",
    )

    message = describe_local_lpg_failure(
        calculation_directory=str(tmp_path),
        result_folder=str(result_folder),
        missing_files=["Results/SumProfiles.HH1.Electricity.csv"],
        completed=_completed(returncode=134, stderr="Unhandled exception."),
        console_output="Downloading LPG binaries from https://www.loadprofilegenerator.de/...",
    )

    assert "134" in message
    assert "Results/SumProfiles.HH1.Electricity.csv" in message
    assert "disk full while writing Results.HH1.sqlite" in message
    assert "Unhandled exception." in message
    assert "Downloading LPG binaries" in message
    assert f"{LOCAL_LPG_FINISHED_FLAG} written: False" in message
    assert "Free disk space" in message


def test_failure_message_survives_a_missing_result_folder(tmp_path) -> None:
    """An LPG that wrote nothing at all must still yield a message, not a second exception."""
    message = describe_local_lpg_failure(
        calculation_directory=str(tmp_path),
        result_folder=str(tmp_path / "results"),
        missing_files=["Results/SumProfiles.HH1.Electricity.csv"],
        completed=_completed(returncode=1),
    )

    assert "did not produce usable results" in message
    assert "cannot list" in message or "empty" in message


# --------------------------------------------------------------------------- #
# retry
# --------------------------------------------------------------------------- #
def test_attempt_is_retried_on_a_rebuilt_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient LPG failure gets a second attempt instead of failing the whole run.

    The old loop only ever downgraded the acquisition mode on failure, so ``USE_LOCAL_LPG``
    was never retried -- and under strict mode it was not retried at all.
    """
    calls = []

    def fake_attempt(self, calculation_index, household, random_seed=None):  # noqa: ANN001
        calls.append(calculation_index)
        if len(calls) == 1:
            raise LocalLpgExecutionError("first attempt died")
        return "/tmp/C1/results"

    monkeypatch.setattr(UtspLpgConnector, "execute_one_local_lpg_attempt", fake_attempt)

    result = UtspLpgConnector.execute_local_lpg_single_household(
        UtspLpgConnector.__new__(UtspLpgConnector), calculation_index=500, household=None
    )

    assert result == "/tmp/C1/results"
    assert calls == [500, 500], "the retry must reuse the index, whose directory is rebuilt from scratch"


def test_failure_is_raised_after_the_last_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the attempts are used up the error propagates -- no silent substitution."""

    def always_fail(self, calculation_index, household, random_seed=None):  # noqa: ANN001
        raise LocalLpgExecutionError("LPG died")

    monkeypatch.setattr(UtspLpgConnector, "execute_one_local_lpg_attempt", always_fail)
    monkeypatch.setenv(LOCAL_LPG_ATTEMPTS_ENV_VAR, "3")

    with pytest.raises(LocalLpgExecutionError, match="LPG died"):
        UtspLpgConnector.execute_local_lpg_single_household(
            UtspLpgConnector.__new__(UtspLpgConnector), calculation_index=500, household=None
        )


def test_a_hung_lpg_is_retried_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout is a transient failure like any other and must not skip the retry."""
    calls = []

    def fake_attempt(self, calculation_index, household, random_seed=None):  # noqa: ANN001
        calls.append(calculation_index)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd="simengine2", timeout=1.0)
        return "/tmp/C1/results"

    monkeypatch.setattr(UtspLpgConnector, "execute_one_local_lpg_attempt", fake_attempt)

    assert (
        UtspLpgConnector.execute_local_lpg_single_household(
            UtspLpgConnector.__new__(UtspLpgConnector), calculation_index=500, household=None
        )
        == "/tmp/C1/results"
    )
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# knobs
# --------------------------------------------------------------------------- #
def test_attempts_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The attempt count is configurable and never drops below one."""
    assert local_lpg_attempts() == DEFAULT_LOCAL_LPG_ATTEMPTS
    monkeypatch.setenv(LOCAL_LPG_ATTEMPTS_ENV_VAR, "5")
    assert local_lpg_attempts() == 5
    monkeypatch.setenv(LOCAL_LPG_ATTEMPTS_ENV_VAR, "0")
    assert local_lpg_attempts() == 1


def test_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged LPG hits a limit by default; a non-positive value opts out."""
    assert local_lpg_timeout_seconds() == float(DEFAULT_LOCAL_LPG_TIMEOUT_SECONDS)
    monkeypatch.setenv(LOCAL_LPG_TIMEOUT_ENV_VAR, "90")
    assert local_lpg_timeout_seconds() == 90.0
    monkeypatch.setenv(LOCAL_LPG_TIMEOUT_ENV_VAR, "0")
    assert local_lpg_timeout_seconds() is None


def test_complete_output_is_not_failed_over_a_grumpy_exit_code() -> None:
    """A run that wrote everything is usable; only a *missing* file may fail it.

    Asserted against the source: the check must not become stricter than what the component
    actually reads, or it would start failing runs that work today.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "hisim" / "components" / "loadprofilegenerator_utsp_connector.py"
    ).read_text(encoding="utf-8")
    body = source.partition("def execute_one_local_lpg_attempt")[2]

    assert "if missing_files:\n            raise LocalLpgExecutionError(" in body
    assert "Proceeding with its results." in body


def test_binary_is_run_with_output_captured_and_no_check() -> None:
    """The exit code must be inspected here, not swallowed by pylpg.

    Asserted against the source: the point is that ``run_local_lpg_binary`` keeps the evidence
    ``pylpg.LPGExecutor.execute_lpg_binaries`` throws away.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "hisim" / "components" / "loadprofilegenerator_utsp_connector.py"
    ).read_text(encoding="utf-8")
    _, _, after = source.partition("def run_local_lpg_binary")
    body = after.partition("def missing_local_lpg_result_files")[0]

    assert "capture_output=True" in body
    assert "timeout=local_lpg_timeout_seconds()" in body
    assert "lpe.execute_lpg_binaries" not in body


def test_lpg_binaries_are_no_longer_executed_unverified() -> None:
    """Regression: nothing may call pylpg's unchecked runner again."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "hisim" / "components" / "loadprofilegenerator_utsp_connector.py"
    ).read_text(encoding="utf-8")

    assert "lpe.execute_lpg_binaries()" not in source
    assert os.path.basename(LOCAL_LPG_LOG_FILE) in source
