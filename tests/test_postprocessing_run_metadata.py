"""Tests that the run's name and description reach the files post-processing writes.

The scenario name and the description are metadata of one run, and since 2026-09-12 they
travel on the run's own :class:`~hisim.simulator.Simulator` instead of a process-global
dictionary. Everything up to the transfer object is pinned elsewhere; what is pinned here is
the last stretch, the one a user sees: the ``scenario`` column of the yearly pyam export, and
the ``name`` and ``description`` of ``scenario.json``.

The two consumers are selected by two independent post-processing options, and the second of
them used to read the description off an attribute only the first one assigned — so a run
asking for component configurations alone wrote an anonymous, undescribed file. Both options
therefore run here, and both artifacts are read.
"""

from __future__ import annotations

import datetime
import json
import shutil
import warnings
from pathlib import Path
from typing import Iterator, List

import pandas as pd
import pytest

from hisim import log
from hisim.postprocessing import postprocessing_main
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton
from tests.postprocessing_option_test_framework import (
    PreparedPostProcessingCase,
    SETUP_MODULE_NAME,
    _clone_ppdt,
    _clone_simulation_parameters,
    _prepare_case,
    _result_directory_for_prefix,
)


#: The name this run gives itself. It is deliberately unlike the module file name, so that the
#: fallback for an unnamed run cannot make the assertions pass.
SCENARIO_NAME = "Gas boiler household [heating=floor]"

#: The run's description, likewise unlike anything the post-processing could derive itself.
DESCRIPTION = "The one-line description this run carries into its scenario.json."


@pytest.fixture(name="named_case", scope="module")
def named_case_fixture() -> Iterator[PreparedPostProcessingCase]:
    """Run the smallest system setup once, under a name and a description of its own."""
    case = _prepare_case(
        setup_module_name=SETUP_MODULE_NAME,
        start_date=datetime.datetime(2021, 1, 1),
        end_date=datetime.datetime(2021, 1, 2),
        seconds_per_timestep=3600,
        test_name_prefix="postprocessing_run_metadata",
        scenario_name=SCENARIO_NAME,
        description=DESCRIPTION,
    )
    yield case
    _clean_up([Path(case.ppdt.simulation_parameters.result_directory)])


def _post_process(case: PreparedPostProcessingCase, options: List[PostProcessingOptions]) -> Path:
    """Post-process a finished run under the given options and return its result directory.

    Args:
        case: The prepared run whose results are post-processed.
        options: The options to run, and only those.

    Returns:
        The directory the post-processing wrote to.
    """
    run_directory = _result_directory_for_prefix("postprocessing_run_metadata_run")
    Path(run_directory).mkdir(parents=True, exist_ok=True)
    simulation_parameters = _clone_simulation_parameters(
        source=case.ppdt.simulation_parameters,
        result_directory=run_directory,
        post_processing_options=options,
    )
    ppdt = _clone_ppdt(case=case, simulation_parameters=simulation_parameters)
    log.logger.reset()
    log.logger.setup(run_directory)
    postprocessing_main.PostProcessor().run(ppdt=ppdt, simulator=case.simulator)
    return Path(run_directory)


@pytest.mark.base
def test_the_runs_name_and_description_reach_the_files_post_processing_writes(
    named_case: PreparedPostProcessingCase,
) -> None:
    """Catches the run's metadata being lost between the transfer object and the artifacts.

    Both consumers run: the scenario evaluation, which writes the pyam ``scenario`` column, and
    the component-configuration export, which writes ``scenario.json`` into the result
    directory itself. The second one read the description off an attribute the first one set,
    so with only the second option selected it wrote an empty description; that is the failure
    this pins, together with the two names agreeing.
    """
    directories_to_clean: List[Path] = []
    try:
        run_directory = _post_process(
            named_case,
            [
                PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION,
                PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON,
            ],
        )
        directories_to_clean.append(run_directory)

        yearly_files = sorted((run_directory / "result_data_for_scenario_evaluation").glob("yearly_*.csv"))
        assert yearly_files, f"the scenario evaluation wrote no yearly CSV below {run_directory}"
        yearly = pd.read_csv(yearly_files[0])
        assert set(yearly["scenario"].unique()) == {SCENARIO_NAME}

        scenario_json = json.loads((run_directory / "scenario.json").read_text(encoding="utf-8"))
        assert scenario_json["name"] == SCENARIO_NAME
        assert scenario_json["description"] == DESCRIPTION
    finally:
        _clean_up(directories_to_clean)


@pytest.mark.base
def test_the_description_reaches_scenario_json_without_the_scenario_evaluation(
    named_case: PreparedPostProcessingCase,
) -> None:
    """Catches ``scenario.json`` depending on a second, independently selected option.

    ``WRITE_COMPONENT_CONFIGS_TO_JSON`` is the option a run selects when it wants the
    configuration of its components and nothing else. It writes ``scenario.json``, and until
    the metadata was read off the transfer object where it is used, the name and the description
    in that file were whatever the scenario-evaluation option had left behind — nothing, when
    that option was not selected.
    """
    directories_to_clean: List[Path] = []
    try:
        run_directory = _post_process(named_case, [PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON])
        directories_to_clean.append(run_directory)

        scenario_json = json.loads((run_directory / "scenario.json").read_text(encoding="utf-8"))
        assert scenario_json["name"] == SCENARIO_NAME
        assert scenario_json["description"] == DESCRIPTION
    finally:
        _clean_up(directories_to_clean)


def _clean_up(directories: List[Path]) -> None:
    """Remove the result directories a test produced, without masking its result.

    Args:
        directories: The directories to remove; a directory that resists is warned about.
    """
    for directory in directories:
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            warnings.warn(f"Could not remove test result directory {directory}: {exc}", stacklevel=2)
    ResultPathProviderSingleton.reset()
    log.logger.reset()
