"""Generic helper functions shared across tests."""
# clean
from typing import Optional, Protocol

from hisim.result_path_provider import ResultPathProviderSingleton, RunMode, detect_test_name


class _ResultPathProviderInstance(Protocol):
    """Minimal instance surface that :meth:`TestingUtils.get_result_directory` drives."""

    def configure(self, *, run_mode: RunMode, test_name: str) -> None:
        ...

    def get_result_directory_name(self) -> Optional[str]:
        ...


class _ResultPathProviderFactory(Protocol):
    """Class-object surface of a result path provider (real singleton or test stub)."""

    def reset(self) -> None:
        ...

    def __call__(self) -> _ResultPathProviderInstance:
        ...


class TestingUtils:

    """Collection of generic helper functions for tests."""

    @staticmethod
    def get_result_directory(
        test_name: Optional[str] = None,
        provider: _ResultPathProviderFactory = ResultPathProviderSingleton,
    ) -> str:
        """Build a clean result directory under ``results/test/<test_name>`` for the running test.

        Resets the result path provider and configures it in test mode. If ``test_name`` is not
        given it is detected from pytest's ``PYTEST_CURRENT_TEST`` environment variable, so each
        test automatically gets its own subdirectory of ``results``.

        The ``provider`` argument is the source of the result path provider used by this helper.
        It defaults to the process-global :class:`ResultPathProviderSingleton`; production callers
        do not need to pass it. A unit test may inject a stub/fake provider (any class exposing the
        same small surface -- a ``reset()`` classmethod plus an instance ``configure(...)`` and
        ``get_result_directory_name()``) so the reset -> configure -> get -> validate orchestration
        can be asserted in isolation without touching shared global state.
        """
        provider.reset()
        instance = provider()
        instance.configure(
            run_mode=RunMode.TEST,
            test_name=test_name or detect_test_name(),
        )
        result_directory = instance.get_result_directory_name()
        if result_directory is None:
            raise ValueError("Result directory could not be determined for test run.")
        return str(result_directory)
