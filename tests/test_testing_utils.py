"""Unit tests for the helpers in tests/testing_utils.py."""

# clean

from pathlib import Path
from typing import List, Optional

import pytest

from hisim.result_path_provider import ResultPathProviderSingleton, RunMode
from tests.testing_utils import TestingUtils


class _RecordingProvider:
    """A stub stand-in for ResultPathProviderSingleton used to assert call orchestration.

    TestingUtils.get_result_directory drives its provider through
    reset -> () -> configure -> get_result_directory_name. Each call starts with
    reset() (clearing last_instance) and then constructs a fresh instance, which
    records the configure() arguments and counts get_result_directory_name() calls.
    """

    last_instance: "_RecordingProvider | None" = None

    def __init__(self) -> None:
        self.configure_calls: List[dict] = []
        self.get_calls: int = 0
        self._directory: str = str(Path("/fake/results/test/example"))
        type(self).last_instance = self

    @classmethod
    def reset(cls) -> None:
        cls.last_instance = None

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(dict(kwargs))

    def get_result_directory_name(self) -> Optional[str]:
        self.get_calls += 1
        return self._directory


class _NoneDirectoryProvider(_RecordingProvider):
    """A stub provider whose get_result_directory_name() returns None."""

    def get_result_directory_name(self) -> Optional[str]:
        self.get_calls += 1
        return None


@pytest.mark.base
def test_get_result_directory_drives_injected_provider() -> None:
    """get_result_directory drives the provider through reset -> configure -> get."""
    _RecordingProvider.reset()
    directory = TestingUtils.get_result_directory(
        test_name="my_isolated_test", provider=_RecordingProvider
    )

    instance = _RecordingProvider.last_instance
    assert instance is not None
    assert instance.configure_calls == [
        {"run_mode": RunMode.TEST, "test_name": "my_isolated_test"},
    ]
    assert instance.get_calls == 1
    assert directory == str(Path("/fake/results/test/example"))


@pytest.mark.base
def test_get_result_directory_raises_when_directory_is_none() -> None:
    """A provider returning None for the directory name triggers ValueError."""
    _NoneDirectoryProvider.reset()
    with pytest.raises(ValueError, match="Result directory could not be determined"):
        TestingUtils.get_result_directory(test_name="whatever", provider=_NoneDirectoryProvider)

    instance = _NoneDirectoryProvider.last_instance
    assert instance is not None
    # configure is still driven before the get that returns None
    assert instance.configure_calls == [
        {"run_mode": RunMode.TEST, "test_name": "whatever"},
    ]
    assert instance.get_calls == 1


@pytest.mark.base
def test_get_result_directory_detects_test_name_when_omitted() -> None:
    """When test_name is None the helper falls back to detect_test_name()."""
    _RecordingProvider.reset()
    TestingUtils.get_result_directory(provider=_RecordingProvider)

    instance = _RecordingProvider.last_instance
    assert instance is not None
    (kwargs,) = instance.configure_calls
    assert kwargs["run_mode"] == RunMode.TEST
    # detect_test_name() returns "unknown_test" without PYTEST_CURRENT_TEST and a
    # node-id-derived name under pytest; either way it must be a non-empty string.
    assert isinstance(kwargs["test_name"], str)
    assert kwargs["test_name"]


@pytest.mark.base
def test_get_result_directory_default_provider_builds_test_path() -> None:
    """With the default provider the helper returns results/test/<test_name>."""
    test_name = "test_testing_utils_default_provider"
    try:
        directory = TestingUtils.get_result_directory(test_name=test_name)
    finally:
        # Avoid leaking global singleton state into other tests.
        ResultPathProviderSingleton.reset()

    assert directory.endswith(str(Path("results") / "test" / test_name))
