"""Fixtures of the verification tests: the private case files, found through ``HISIM_VERIFICATION_DATA``.

The case files are not in the repository (decision D19 of 2026-11-03); every test that needs them
skips with a message naming the variable when it is unset or the directory is missing.
"""

import os
import pathlib
from typing import Dict

import pytest

from hisim.verification.case_loader import load_cases
from hisim.verification.schema import Case

#: The environment variable naming the private data folder (spec section 7.2).
DATA_ENVIRONMENT_VARIABLE = "HISIM_VERIFICATION_DATA"

#: Where the case files of the ashrae140 suite sit inside that folder.
CASE_SUBDIRECTORY = pathlib.Path("cases") / "ashrae140"


@pytest.fixture(scope="session", name="case_directory")
def fixture_case_directory() -> pathlib.Path:
    """The directory of the ashrae140 case files, or a skip."""
    root = os.environ.get(DATA_ENVIRONMENT_VARIABLE)
    if not root:
        pytest.skip(f"the private case files need {DATA_ENVIRONMENT_VARIABLE} to name the data folder")
    directory = pathlib.Path(root) / CASE_SUBDIRECTORY
    if not directory.is_dir():
        pytest.skip(f"{directory} does not exist")
    return directory


@pytest.fixture(scope="session", name="cases")
def fixture_cases(case_directory: pathlib.Path) -> Dict[str, Case]:
    """Every resolved case of the suite, by id."""
    return load_cases(case_directory)
