"""Fixtures of the verification tests: the private case files and ranges, found through ``HISIM_VERIFICATION_DATA``.

Neither is in the repository (spec §7.2, decision D19 of 2026-11-03); every test that needs them
skips with a message naming the variable when it is unset or the file is missing.
"""

import os
import pathlib
from typing import Dict

import pytest

from hisim.verification.case_loader import load_cases
from hisim.verification.schema import Case
from hisim.verification.suite import Suite

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


@pytest.fixture(scope="session", name="reference_file")
def fixture_reference_file() -> pathlib.Path:
    """The private file of the acceptance ranges, or a skip."""
    root = os.environ.get(DATA_ENVIRONMENT_VARIABLE)
    if not root:
        pytest.skip(f"the private reference file needs {DATA_ENVIRONMENT_VARIABLE} to name the data folder")
    path = pathlib.Path(root) / Suite.load("ashrae140").reference_file
    if not path.is_file():
        pytest.skip(f"{path} does not exist")
    return path
