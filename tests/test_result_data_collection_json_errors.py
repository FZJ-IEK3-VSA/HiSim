"""Tests for JSON error handling in ResultDataCollection.get_indoor_air_temperatures_of_building.

These tests cover the error-handling paths for the scenario-evaluation JSON
files. They ensure that:

* an empty JSON file raises a clear ``ValueError`` prompting the user to
  re-run the simulation, and
* a malformed (non-empty) JSON file re-raises the original
  ``json.JSONDecodeError`` instead of being silently swallowed or misreported
  as "empty".

See GitLab issue #166.
"""

# clean

import json
from pathlib import Path

import pytest

from hisim.postprocessing.scenario_evaluation.result_data_collection import (
    ResultDataCollection,
)


def _make_instance() -> ResultDataCollection:
    """Build a ResultDataCollection without running the heavy __init__.

    get_indoor_air_temperatures_of_building does not read any instance state,
    so bypassing __init__ keeps the test fast and isolated.
    """
    return ResultDataCollection.__new__(ResultDataCollection)


def _empty_lists() -> list:
    return [[] for _ in range(8)]


@pytest.mark.base
def test_empty_new_version_json_raises_value_error(tmp_path: Path) -> None:
    """An empty data_for_scenario_evaluation.json must raise ValueError."""
    folder = tmp_path / "results" / "building_run"
    folder.mkdir(parents=True)
    (folder / "data_for_scenario_evaluation.json").write_text("", encoding="utf-8")

    instance = _make_instance()
    with pytest.raises(ValueError, match="The json file is empty"):
        instance.get_indoor_air_temperatures_of_building(str(folder), *_empty_lists())


@pytest.mark.base
def test_malformed_new_version_json_raises_jsondecodeerror(tmp_path: Path) -> None:
    """Malformed-but-non-empty JSON must re-raise json.JSONDecodeError, not a misleading "empty" ValueError."""
    folder = tmp_path / "results" / "building_run"
    folder.mkdir(parents=True)
    (folder / "data_for_scenario_evaluation.json").write_text(
        "{ this is not valid json >>>",
        encoding="utf-8",
    )

    instance = _make_instance()
    with pytest.raises(json.JSONDecodeError):
        instance.get_indoor_air_temperatures_of_building(str(folder), *_empty_lists())


@pytest.mark.base
def test_empty_old_version_json_raises_value_error(tmp_path: Path) -> None:
    """An empty data_information_for_scenario_evaluation.json must raise ValueError."""
    folder = tmp_path / "results" / "building_run"
    folder.mkdir(parents=True)
    (folder / "data_information_for_scenario_evaluation.json").write_text(
        "", encoding="utf-8"
    )

    instance = _make_instance()
    with pytest.raises(ValueError, match="The json file is empty"):
        instance.get_indoor_air_temperatures_of_building(str(folder), *_empty_lists())


@pytest.mark.base
def test_malformed_old_version_json_raises_jsondecodeerror(tmp_path: Path) -> None:
    """Malformed old-version JSON must re-raise json.JSONDecodeError."""
    folder = tmp_path / "results" / "building_run"
    folder.mkdir(parents=True)
    (folder / "data_information_for_scenario_evaluation.json").write_text(
        "{ not : valid json ]",
        encoding="utf-8",
    )

    instance = _make_instance()
    with pytest.raises(json.JSONDecodeError):
        instance.get_indoor_air_temperatures_of_building(str(folder), *_empty_lists())
