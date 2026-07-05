"""Unit tests for the RenoVisor uploader: multipart submission, retries, file matching (spec section 7)."""

from pathlib import Path
from typing import Any, List

import pytest
import requests

from hisim.renovisor.uploader import (
    RETRY_DELAYS_IN_SECONDS,
    UploadError,
    match_result_files,
    post_failure,
    post_started,
    post_success,
)

pytestmark = pytest.mark.base


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code: int) -> None:
        """Store the status code to report."""
        self.status_code = status_code


class FakePoster:
    """Callable that returns scripted responses/exceptions and records every call."""

    def __init__(self, outcomes: List[Any]) -> None:
        """Script the outcomes; an Exception instance is raised, an int becomes a response."""
        self.outcomes = list(outcomes)
        self.calls: List[dict] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record the call and produce the next scripted outcome."""
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class SleepRecorder:
    """Records requested sleep durations instead of sleeping."""

    def __init__(self) -> None:
        """Start with no recorded sleeps."""
        self.delays: List[float] = []

    def __call__(self, seconds: float) -> None:
        """Record the requested delay."""
        self.delays.append(seconds)


def _result_dir(tmp_path: Path) -> Path:
    """Create a fake result directory with typical HiSim output files."""
    (tmp_path / "all_kpis.json").write_text("{}", encoding="utf-8")
    (tmp_path / "BUI1_kpi_config_for_building_sizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "report.pdf").write_bytes(b"%PDF")
    subdir = tmp_path / "csv"
    subdir.mkdir()
    (subdir / "electricity.csv").write_text("a;b", encoding="utf-8")
    return tmp_path


def test_match_result_files_globs_relative_paths_and_names(tmp_path: Path) -> None:
    """Patterns match both the relative path and the bare filename, recursively."""
    result_dir = _result_dir(tmp_path)
    matched = match_result_files(result_dir, ["all_kpis.json", "*_kpi_config_for_building_sizer.json", "*.csv"])
    assert [rel for rel, _ in matched] == [
        "BUI1_kpi_config_for_building_sizer.json",
        "all_kpis.json",
        "csv/electricity.csv",
    ]


def test_post_success_sends_multipart_with_auth_and_fields(tmp_path: Path) -> None:
    """A successful upload sends one multipart POST with bearer auth, form fields and files."""
    result_dir = _result_dir(tmp_path)
    files = match_result_files(result_dir, ["all_kpis.json"])
    poster = FakePoster([200])
    post_success(
        "https://server/results",
        "secret",
        {"jobId": "j1", "variant": "base", "status": "succeeded", "translatorVersion": "1.0.0"},
        files,
        post_fn=poster,
        sleep_fn=SleepRecorder(),
    )
    call = poster.calls[0]
    assert call["headers"] == {"Authorization": "Bearer secret"}
    assert call["data"]["jobId"] == "j1"
    part_name, (filename, content, mimetype) = call["files"][0]
    assert (part_name, filename, content) == ("files", "all_kpis.json", b"{}")


def test_post_success_retries_on_5xx_and_network_errors(tmp_path: Path) -> None:
    """5xx responses and network errors retry with the 5/25/125s backoff; files are re-read."""
    result_dir = _result_dir(tmp_path)
    files = match_result_files(result_dir, ["all_kpis.json"])
    poster = FakePoster([500, requests.ConnectionError("boom"), 201])
    sleeper = SleepRecorder()
    post_success("https://server/results", None, {"jobId": "j1"}, files, post_fn=poster, sleep_fn=sleeper)
    assert sleeper.delays == list(RETRY_DELAYS_IN_SECONDS[:2])
    assert len(poster.calls) == 3
    assert all("files" in call for call in poster.calls)


def test_post_success_gives_up_after_all_retries(tmp_path: Path) -> None:
    """Persistent 5xx exhausts the retries and raises UploadError."""
    files = match_result_files(_result_dir(tmp_path), ["all_kpis.json"])
    poster = FakePoster([500, 503, 502, 500])
    sleeper = SleepRecorder()
    with pytest.raises(UploadError, match="failed after 4 attempts"):
        post_success("https://server/results", None, {}, files, post_fn=poster, sleep_fn=sleeper)
    assert sleeper.delays == list(RETRY_DELAYS_IN_SECONDS)


def test_post_success_does_not_retry_4xx(tmp_path: Path) -> None:
    """A 4xx response is a contract error: fail immediately, no retries."""
    files = match_result_files(_result_dir(tmp_path), ["all_kpis.json"])
    poster = FakePoster([403])
    sleeper = SleepRecorder()
    with pytest.raises(UploadError, match="403"):
        post_success("https://server/results", None, {}, files, post_fn=poster, sleep_fn=sleeper)
    assert sleeper.delays == []
    assert len(poster.calls) == 1


def test_post_started_is_non_fatal_with_one_reattempt() -> None:
    """The started event tries twice and reports success/failure without raising."""
    ok_poster = FakePoster([requests.ConnectionError("boom"), 200])
    assert post_started("https://server/results", None, {"status": "started"}, post_fn=ok_poster) is True
    assert len(ok_poster.calls) == 2

    failing_poster = FakePoster([500, requests.ConnectionError("boom")])
    assert post_started("https://server/results", None, {"status": "started"}, post_fn=failing_poster) is False
    assert len(failing_poster.calls) == 2


def test_post_failure_sends_json_payload_with_retries() -> None:
    """The failure report is a JSON POST using the standard retry policy."""
    poster = FakePoster([502, 200])
    sleeper = SleepRecorder()
    payload = {"jobId": "j1", "status": "failed", "stage": "simulation"}
    post_failure("https://server/results", "tok", payload, post_fn=poster, sleep_fn=sleeper)
    assert sleeper.delays == [RETRY_DELAYS_IN_SECONDS[0]]
    assert poster.calls[-1]["json"] == payload
    assert poster.calls[-1]["headers"] == {"Authorization": "Bearer tok"}
