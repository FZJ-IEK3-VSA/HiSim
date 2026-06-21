"""Shared framework for PostProcessingOptions integration tests."""
from __future__ import annotations

import datetime
import fnmatch
import importlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from uuid import uuid4

from hisim import component as cp
from hisim import log
from hisim.postprocessing import postprocessing_main
from hisim.postprocessing.postprocessing_datatransfer import PostProcessingDataTransfer
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton, RunMode
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator


REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_SETUPS = REPO_ROOT / "system_setups"
SETUP_MODULE_NAME = "simple_system_setup_one"
HOUSEHOLD_SETUP_MODULE_NAME = "basic_household"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DEPENDENCIES_BY_OPTION: dict[PostProcessingOptions, tuple[PostProcessingOptions, ...]] = {
    PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT: (PostProcessingOptions.GENERATE_PDF_REPORT,),
    PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT: (PostProcessingOptions.GENERATE_PDF_REPORT,),
    PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT: (
        PostProcessingOptions.MAKE_NETWORK_CHARTS,
        PostProcessingOptions.GENERATE_PDF_REPORT,
    ),
    PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT: (
        PostProcessingOptions.GENERATE_PDF_REPORT,
        PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT,
    ),
    PostProcessingOptions.INCLUDE_IMAGES_IN_PDF_REPORT: (
        PostProcessingOptions.PLOT_LINE,
        PostProcessingOptions.GENERATE_PDF_REPORT,
        PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT,
    ),
    PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER: (
        PostProcessingOptions.COMPUTE_OPEX,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_KPIS,
    ),
    PostProcessingOptions.WRITE_KPIS_TO_JSON: (PostProcessingOptions.COMPUTE_KPIS,),
    PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL: (
        PostProcessingOptions.COMPUTE_OPEX,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_KPIS,
    ),
    PostProcessingOptions.EXPORT_MONTHLY_RESULTS: (PostProcessingOptions.EXPORT_TO_CSV,),
    PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE: (PostProcessingOptions.EXPORT_TO_CSV,),
}

OPTIONS_USING_SINGLE_DAY_MINUTELY_BASELINE = {
    PostProcessingOptions.PLOT_SPECIAL_TESTING_SINGLE_DAY,
}

OPTIONS_USING_HOUSEHOLD_BASELINE = {
    PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE,
    PostProcessingOptions.COMPUTE_OPEX,
    PostProcessingOptions.COMPUTE_CAPEX,
    PostProcessingOptions.COMPUTE_KPIS,
    PostProcessingOptions.MAKE_RESULT_JSON_FOR_WEBTOOL,
    PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
    PostProcessingOptions.WRITE_KPIS_TO_JSON,
    PostProcessingOptions.MAKE_OPERATION_RESULTS_FOR_WEBTOOL,
}


@dataclass
class PreparedPostProcessingCase:
    """Reusable simulation output for isolated postprocessing option runs."""

    simulator: Simulator
    ppdt: PostProcessingDataTransfer


@dataclass
class PostProcessingOptionTestFramework:
    """Runs one PostProcessingOptions value against a common prepared result set."""

    _yearly_hourly_case: PreparedPostProcessingCase | None = None
    _single_day_minutely_case: PreparedPostProcessingCase | None = None
    _household_single_day_hourly_case: PreparedPostProcessingCase | None = None
    _runs_by_option: dict[PostProcessingOptions, Path] = field(default_factory=dict)

    def run(self, option: PostProcessingOptions, expected_files: Sequence[str] | None = None) -> None:
        """Run postprocessing for one option plus the minimal required support options."""

        case = self._case_for_option(option)
        options = self._options_for(option)
        run_directory = _result_directory_for(option)
        os.makedirs(run_directory, exist_ok=True)
        self._runs_by_option[option] = Path(run_directory)

        simulation_parameters = _clone_simulation_parameters(
            source=case.ppdt.simulation_parameters,
            result_directory=run_directory,
            post_processing_options=options,
        )
        ppdt = _clone_ppdt(case=case, simulation_parameters=simulation_parameters)

        log.logger.reset()
        log.logger.setup(run_directory)
        files_before_postprocessing = _file_signatures_below(run_directory)
        post_processor = postprocessing_main.PostProcessor()
        if option == PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER:
            post_processor.open_dir_in_file_explorer = lambda ppdt: None  # type: ignore[method-assign]
        post_processor.run(ppdt=ppdt, my_sim=case.simulator)
        files_after_postprocessing = _file_signatures_below(run_directory)
        files_changed_by_postprocessing = _changed_files(
            before=files_before_postprocessing,
            after=files_after_postprocessing,
        )

        assert files_changed_by_postprocessing, (
            f"{option.name} did not create or update any file in {run_directory}. "
            "Each PostProcessingOptions test is expected to produce at least one file-system artifact."
        )
        if expected_files is not None:
            _assert_expected_files_exist(
                option=option,
                run_directory=run_directory,
                expected_files=expected_files,
                files_after_postprocessing=set(files_after_postprocessing),
            )

    def _case_for_option(self, option: PostProcessingOptions) -> PreparedPostProcessingCase:
        if option in OPTIONS_USING_SINGLE_DAY_MINUTELY_BASELINE:
            if self._single_day_minutely_case is None:
                self._single_day_minutely_case = _prepare_case(
                    setup_module_name=SETUP_MODULE_NAME,
                    start_date=datetime.datetime(2021, 1, 1),
                    end_date=datetime.datetime(2021, 1, 2),
                    seconds_per_timestep=60,
                    test_name_prefix="postprocessing_options_base_single_day_minutely",
                )
            return self._single_day_minutely_case

        if option in OPTIONS_USING_HOUSEHOLD_BASELINE:
            if self._household_single_day_hourly_case is None:
                self._household_single_day_hourly_case = _prepare_case(
                    setup_module_name=HOUSEHOLD_SETUP_MODULE_NAME,
                    start_date=datetime.datetime(2021, 1, 1),
                    end_date=datetime.datetime(2021, 1, 2),
                    seconds_per_timestep=3600,
                    test_name_prefix="postprocessing_options_base_household_single_day_hourly",
                )
            return self._household_single_day_hourly_case

        if self._yearly_hourly_case is None:
            self._yearly_hourly_case = _prepare_case(
                setup_module_name=SETUP_MODULE_NAME,
                start_date=datetime.datetime(2021, 1, 1),
                end_date=datetime.datetime(2022, 1, 1),
                seconds_per_timestep=3600,
                test_name_prefix="postprocessing_options_base_yearly_hourly",
            )
        return self._yearly_hourly_case

    @staticmethod
    def _options_for(option: PostProcessingOptions) -> list[PostProcessingOptions]:
        options = list(DEPENDENCIES_BY_OPTION.get(option, ()))
        options.append(option)
        return _deduplicate(options)


def _prepare_case(
    setup_module_name: str,
    start_date: datetime.datetime,
    end_date: datetime.datetime,
    seconds_per_timestep: int,
    test_name_prefix: str,
) -> PreparedPostProcessingCase:
    simulation_parameters = SimulationParameters(
        start_date=start_date,
        end_date=end_date,
        seconds_per_timestep=seconds_per_timestep,
    )
    simulation_parameters.result_directory = _result_directory_for_prefix(test_name_prefix)

    simulator = Simulator(
        module_directory=str(SYSTEM_SETUPS),
        module_filename=setup_module_name,
        my_simulation_parameters=simulation_parameters,
    )
    setup_module = importlib.import_module(f"system_setups.{setup_module_name}")
    setup_module.setup_function(simulator, simulation_parameters)

    simulator.prepare_simulation_directory()
    log.logger.reset()
    log.logger.setup(simulation_parameters.result_directory)
    simulator.prepare_calculation()
    simulator.connect_all_components()

    all_result_lines: list[list[float]] = []
    stsv = cp.SingleTimeStepValues(number_of_values=len(simulator.all_outputs))
    start_counter = time.perf_counter()
    for step in range(simulation_parameters.timesteps):
        stsv, _, _ = simulator.process_one_timestep(step, stsv)
        all_result_lines.append(stsv.values.copy())

    ppdt = simulator.prepare_post_processing(all_result_lines, start_counter)
    (
        ppdt.results_cumulative,
        ppdt.results_monthly,
        ppdt.results_daily,
        ppdt.results_hourly,
    ) = simulator.get_std_results(ppdt.results)

    return PreparedPostProcessingCase(simulator=simulator, ppdt=ppdt)


def _clone_simulation_parameters(
    source: SimulationParameters,
    result_directory: str,
    post_processing_options: list[PostProcessingOptions],
) -> SimulationParameters:
    simulation_parameters = SimulationParameters(
        start_date=source.start_date,
        end_date=source.end_date,
        seconds_per_timestep=source.seconds_per_timestep,
        result_directory=result_directory,
        post_processing_options=post_processing_options,
        logging_level=source.logging_level,
        skip_finished_results=source.skip_finished_results,
        surplus_control=source.surplus_control,
        cache_dir_path=source.cache_dir_path,
        multiple_buildings=source.multiple_buildings,
        log_connections=source.log_connections,
    )
    simulation_parameters.figure_format = source.figure_format
    return simulation_parameters


def _clone_ppdt(
    case: PreparedPostProcessingCase,
    simulation_parameters: SimulationParameters,
) -> PostProcessingDataTransfer:
    return PostProcessingDataTransfer(
        results=case.ppdt.results,
        all_outputs=case.ppdt.all_outputs,
        simulation_parameters=simulation_parameters,
        wrapped_components=case.ppdt.wrapped_components,
        mode=case.ppdt.mode,
        setup_function=case.ppdt.setup_function,
        module_filename=case.ppdt.module_filename,
        my_module_config=case.ppdt.my_module_config,
        execution_time=case.ppdt.execution_time,
        results_monthly=case.ppdt.results_monthly,
        results_hourly=case.ppdt.results_hourly,
        results_cumulative=case.ppdt.results_cumulative,
        results_daily=case.ppdt.results_daily,
        kpi_collection_dict={},
    )


def _result_directory_for(option: PostProcessingOptions) -> str:
    return _result_directory_for_prefix(f"postprocessing_option_{option.name.lower()}")


def _result_directory_for_prefix(prefix: str) -> str:
    ResultPathProviderSingleton.reset()
    ResultPathProviderSingleton().configure(
        run_mode=RunMode.TEST,
        test_name=f"{prefix}_{uuid4().hex}",
    )
    result_directory = ResultPathProviderSingleton().get_result_directory_name()
    if result_directory is None:
        raise ValueError("Result directory could not be determined for postprocessing option test.")
    return str(result_directory)


def _deduplicate(options: Iterable[PostProcessingOptions]) -> list[PostProcessingOptions]:
    unique_options: list[PostProcessingOptions] = []
    for option in options:
        if option not in unique_options:
            unique_options.append(option)
    return unique_options


def _file_signatures_below(directory: str) -> dict[Path, tuple[int, int]]:
    root = Path(directory)
    if not root.exists():
        return {}
    return {
        path.relative_to(root): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _changed_files(
    before: dict[Path, tuple[int, int]],
    after: dict[Path, tuple[int, int]],
) -> set[Path]:
    return {path for path, signature in after.items() if path not in before or before[path] != signature}


def _assert_expected_files_exist(
    option: PostProcessingOptions,
    run_directory: str,
    expected_files: Sequence[str],
    files_after_postprocessing: set[Path],
) -> None:
    missing_patterns = [
        pattern
        for pattern in expected_files
        if not any(_matches_pattern(path=path, pattern=pattern) for path in files_after_postprocessing)
    ]
    assert not missing_patterns, (
        f"{option.name} did not create the expected file pattern(s) in {run_directory}: "
        + ", ".join(missing_patterns)
    )


def _matches_pattern(path: Path, pattern: str) -> bool:
    normalized_path = path.as_posix()
    normalized_pattern = pattern.replace("\\", "/")
    return path.match(normalized_pattern) or fnmatch.fnmatch(normalized_path, normalized_pattern)
