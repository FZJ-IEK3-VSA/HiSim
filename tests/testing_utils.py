"""Generic helper functions shared across tests."""
# clean
from typing import Optional

from hisim.result_path_provider import ResultPathProviderSingleton, RunMode, detect_test_name


class TestingUtils:

    """Collection of generic helper functions for tests."""

    @staticmethod
    def get_result_directory(test_name: Optional[str] = None) -> str:
        """Build a clean result directory under ``results/test/<test_name>`` for the running test.

        Resets the result path provider and configures it in test mode. If ``test_name`` is not
        given it is detected from pytest's ``PYTEST_CURRENT_TEST`` environment variable, so each
        test automatically gets its own subdirectory of ``results``.
        """
        ResultPathProviderSingleton.reset()
        ResultPathProviderSingleton().configure(
            run_mode=RunMode.TEST,
            test_name=test_name or detect_test_name(),
        )
        return ResultPathProviderSingleton().get_result_directory_name()
