"""Integration tests for individual PostProcessingOptions.

This module deliberately uses one named test function per PostProcessingOptions
enum member (e.g. ``test_postprocessing_option_plot_line``) instead of a single
parametrized test over the enum.  This structure makes it immediately obvious
in CI/CD output which exact option failed, rather than requiring developers to
parse a parametrized test ID.  The guard
``test_each_postprocessing_option_has_a_named_test`` at the bottom ensures the
list stays in sync with the enum — it will fail as soon as an enum member is
added or removed without a corresponding test function.
"""
from __future__ import annotations

import datetime
import inspect
import shutil
import warnings
from pathlib import Path
from uuid import uuid4

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.result_path_provider import ResultPathProviderSingleton, RunMode
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator
from tests.postprocessing_option_test_framework import PostProcessingOptionTestFramework


pytestmark: pytest.MarkDecorator = pytest.mark.postprocessingoptions


class _ConvergingFeedbackComponent(cp.StatelessComponent):
    """Minimal component that reads one input and writes a converging output.

    ``output = 0.5 * input + offset`` — two cross-connected instances (A reads
    B's output, B reads A's output) form a circular dependency that converges
    geometrically (factor 0.5 per iteration).  Starting from zero, the pair
    needs more than two iterations to converge within the simulator's tolerance,
    which is the only way to exercise the
    :attr:`PostProcessingOptions.PROVIDE_DETAILED_ITERATION_LOGGING` code path:
    ``Simulator.process_one_timestep`` appends per-iteration value differences
    to ``Detailed_Iteration_Log.txt`` only when ``iterative_tries > 2``.
    """

    FeedbackOutput: str = "FeedbackOutput"
    FeedbackInput: str = "FeedbackInput"

    def __init__(
        self,
        name: str,
        my_simulation_parameters: SimulationParameters,
        offset: float = 10.0,
    ) -> None:
        """Initialize the component with one input and one output.

        Args:
            name: Component name, also used as the config name and I/O object name.
            my_simulation_parameters: Simulation parameters for this component.
            offset: Constant added to ``0.5 * input``; determines the fixed point.
        """
        super().__init__(
            name=name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=cp.ConfigBase(name=name),
            my_display_config=cp.DisplayConfig(),
        )
        self._offset: float = offset
        self.feedback_input: cp.ComponentInput = self.add_input(
            object_name=name,
            field_name=self.FeedbackInput,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            mandatory=True,
        )
        self.feedback_output: cp.ComponentOutput = self.add_output(
            object_name=name,
            field_name=self.FeedbackOutput,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            output_description="Feedback output for iteration-logging test",
        )

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Read the feedback input and write a value that converges toward the fixed point.

        ``force_convergence`` is part of the abstract :meth:`Component.i_simulate` signature
        but irrelevant to this stateless test component, so it is intentionally unused.
        """
        del force_convergence  # signature-matched but unused by this test component
        input_value = stsv.get_input_value(self.feedback_input)
        stsv.set_output_value(self.feedback_output, 0.5 * input_value + self._offset)

    def write_to_report(self) -> list[str]:
        """Return a minimal report entry for this component."""
        return [f"Converging feedback component: {self.component_name}"]


@pytest.fixture(scope="module", name="postprocessing_option_framework")
def fixture_postprocessing_option_framework() -> PostProcessingOptionTestFramework:
    """Shared framework that keeps the individual option tests small."""
    return PostProcessingOptionTestFramework()


def test_postprocessing_option_plot_line(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.PLOT_LINE produces per-component line plot PNG files."""
    postprocessing_option_framework.run(PostProcessingOptions.PLOT_LINE, expected_files=["*/*/line.png"])


def test_postprocessing_option_plot_carpet(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.PLOT_CARPET produces per-component carpet plot PNG files."""
    postprocessing_option_framework.run(PostProcessingOptions.PLOT_CARPET, expected_files=["*/*/carpet.png"])


def test_postprocessing_option_plot_sankey(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.PLOT_SANKEY runs and produces a simulation log file."""
    postprocessing_option_framework.run(PostProcessingOptions.PLOT_SANKEY, expected_files=["hisim_simulation.log"])


def test_postprocessing_option_plot_single_days(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.PLOT_SINGLE_DAYS produces per-component single-day plot PNG files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.PLOT_SINGLE_DAYS,
        expected_files=["*/*/days_m0_d0*.PNG"],
    )


def test_postprocessing_option_plot_monthly_bar_charts(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS produces per-component monthly bar chart PNG files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.PLOT_MONTHLY_BAR_CHARTS,
        expected_files=["*/*/bar.png"],
    )


def test_postprocessing_option_open_directory_in_explorer(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER runs without producing extra result files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.OPEN_DIRECTORY_IN_EXPLORER,
        expected_files=["hisim_simulation.log"],
    )


def test_postprocessing_option_export_to_csv(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.EXPORT_TO_CSV produces CSV result files."""
    postprocessing_option_framework.run(PostProcessingOptions.EXPORT_TO_CSV, expected_files=["*.csv"])


def test_postprocessing_option_make_network_charts(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.MAKE_NETWORK_CHARTS produces network diagram PNG files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.MAKE_NETWORK_CHARTS,
        expected_files=["System_*.PNG"],
    )


def test_postprocessing_option_generate_pdf_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.GENERATE_PDF_REPORT produces a PDF report file."""
    postprocessing_option_framework.run(PostProcessingOptions.GENERATE_PDF_REPORT, expected_files=["report.pdf"])


def test_postprocessing_option_write_components_to_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT writes component descriptions into the PDF report."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_COMPONENTS_TO_REPORT,
        expected_files=["report.pdf"],
    )


def test_postprocessing_option_write_all_outputs_to_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT writes all component outputs into the PDF report."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_ALL_OUTPUTS_TO_REPORT,
        expected_files=["report.pdf"],
    )


def test_postprocessing_option_write_network_charts_to_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT embeds network charts in the PDF report."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_NETWORK_CHARTS_TO_REPORT,
        expected_files=["System_*.PNG", "report.pdf"],
    )


def test_postprocessing_option_plot_special_testing_single_day(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.PLOT_SPECIAL_TESTING_SINGLE_DAY produces single-day plot PNG files using the minutely baseline."""
    postprocessing_option_framework.run(
        PostProcessingOptions.PLOT_SPECIAL_TESTING_SINGLE_DAY,
        expected_files=["*/*/days_m0_d0*.PNG"],
    )


def test_postprocessing_option_generate_csv_for_housing_data_base(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE produces annual and seasonal housing-data-base CSV files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.GENERATE_CSV_FOR_HOUSING_DATA_BASE,
        expected_files=[
            "csv_for_housing_data_base_annual_*.csv",
            "csv_for_housing_data_base_seasonal_*.csv",
        ],
    )


def test_postprocessing_option_include_configs_in_pdf_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT embeds component configs in the PDF report."""
    postprocessing_option_framework.run(
        PostProcessingOptions.INCLUDE_CONFIGS_IN_PDF_REPORT,
        expected_files=["report.pdf"],
    )


def test_postprocessing_option_include_images_in_pdf_report(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.INCLUDE_IMAGES_IN_PDF_REPORT embeds line plots in the PDF report."""
    postprocessing_option_framework.run(
        PostProcessingOptions.INCLUDE_IMAGES_IN_PDF_REPORT,
        expected_files=["*/*/line.png", "report.pdf"],
    )


def _run_converging_feedback_simulation(
    post_processing_options: list[PostProcessingOptions],
    label: str,
) -> tuple[Path, str]:
    """Build and run the minimal two-component feedback simulation once.

    Creates a fresh result directory, wires two :class:`_ConvergingFeedbackComponent`
    instances into a circular dependency (``output = 0.5 * input + offset``), and
    runs the simulator for three one-second timesteps.  The circular dependency
    needs more than two iterations to converge, so when
    ``PROVIDE_DETAILED_ITERATION_LOGGING`` is in ``post_processing_options`` the
    simulator writes per-iteration value differences to ``Detailed_Iteration_Log.txt``;
    with the option disabled the file is never created.

    Args:
        post_processing_options: Post-processing options to enable for this run.
        label: Suffix for the unique result-directory name, isolating the
            positive and negative runs from each other.

    Returns:
        A ``(log_path, result_directory)`` tuple: ``log_path`` points at the
        ``Detailed_Iteration_Log.txt`` the simulator would create, and
        ``result_directory`` is the directory the caller is responsible for
        cleaning up.
    """
    ResultPathProviderSingleton.reset()
    ResultPathProviderSingleton().configure(
        run_mode=RunMode.TEST,
        test_name=f"detailed_iteration_logging_{label}_{uuid4().hex}",
    )
    result_directory_path = ResultPathProviderSingleton().get_result_directory_name()
    if result_directory_path is None:
        raise ValueError("Result directory could not be determined for iteration logging test.")
    result_directory = str(result_directory_path)

    simulation_parameters = SimulationParameters(
        start_date=datetime.datetime(2021, 1, 1),
        end_date=datetime.datetime(2021, 1, 1, 0, 0, 3),
        seconds_per_timestep=1,
        result_directory=result_directory,
        post_processing_options=post_processing_options,
    )

    simulator = Simulator(
        module_directory="",
        module_filename="feedback_iteration_test",
        my_simulation_parameters=simulation_parameters,
    )

    component_a = _ConvergingFeedbackComponent(
        name="FeedbackA",
        my_simulation_parameters=simulation_parameters,
    )
    component_b = _ConvergingFeedbackComponent(
        name="FeedbackB",
        my_simulation_parameters=simulation_parameters,
    )
    simulator.add_component(component_a)
    simulator.add_component(component_b)
    # Cross-connect: A reads B's output, B reads A's output — a circular
    # dependency that needs >2 iterations to converge.
    component_a.connect_input(
        input_fieldname=_ConvergingFeedbackComponent.FeedbackInput,
        src_object_name=component_b.component_name,
        src_field_name=_ConvergingFeedbackComponent.FeedbackOutput,
    )
    component_b.connect_input(
        input_fieldname=_ConvergingFeedbackComponent.FeedbackInput,
        src_object_name=component_a.component_name,
        src_field_name=_ConvergingFeedbackComponent.FeedbackOutput,
    )

    # Manually replicate the relevant parts of Simulator.run() (prepare → log →
    # connect → iterate). The shared PostProcessingOptionTestFramework only re-runs
    # post-processing and cannot exercise this option, which affects the simulation
    simulator.prepare_simulation_directory()
    log.logger.reset()
    log.logger.setup(result_directory)
    simulator.prepare_calculation()
    simulator.connect_all_components()

    # With offset=10.0 and factor 0.5, the fixed point is 20. Starting from zero,
    # convergence is geometric (factor 0.5 per iteration). The simulator's tolerance
    # (see SingleTimeStepValues.is_close_enough_to_previous) is 0.0001, so the loop
    # needs roughly 10 iterations — well above the iterative_tries > 2 threshold that
    # gates Detailed_Iteration_Log.txt. If that tolerance were ever loosened to >= 1.0,
    # convergence could occur in <= 2 iterations and the log file would not be created.
    stsv = cp.SingleTimeStepValues(number_of_values=len(simulator.all_outputs))
    for step in range(simulation_parameters.timesteps):
        stsv, _, _ = simulator.process_one_timestep(step, stsv)

    # prepare_simulation_directory() always sets iteration_logging_path to the full
    # path of Detailed_Iteration_Log.txt (even when the option is disabled — the file
    # is simply never written). Guard against an empty path so a future regression
    # fails loudly instead of silently producing Path(".") (a directory).
    if not simulator.iteration_logging_path:
        raise RuntimeError(
            "Simulator.iteration_logging_path is empty; "
            "prepare_simulation_directory() should have set it."
        )
    return Path(simulator.iteration_logging_path), result_directory


def test_postprocessing_option_provide_detailed_iteration_logging() -> None:
    """Test that PROVIDE_DETAILED_ITERATION_LOGGING writes per-iteration convergence data.

    Unlike most PostProcessingOptions, this option affects the simulation loop
    (``Simulator.process_one_timestep``), not post-processing: when enabled and a
    timestep takes more than two iterations to converge, the simulator appends
    the per-iteration value differences (via ``get_differences_for_error_msg``)
    to ``Detailed_Iteration_Log.txt``.  The file is never created when the option
    is disabled, so its existence and content are a direct verification of the
    option's effect.

    The shared ``PostProcessingOptionTestFramework.run`` only re-runs
    post-processing and cannot exercise this option, so this test builds a
    minimal two-component circular dependency (see ``_ConvergingFeedbackComponent``)
    that converges slowly, runs three timesteps, and asserts both that enabling
    the option writes the iteration-specific ``previously:``/``currently:`` markers
    to ``Detailed_Iteration_Log.txt`` and that disabling the option leaves no such
    file behind.
    """
    directories_to_clean: list[str] = []
    try:
        # Positive case: option enabled → Detailed_Iteration_Log.txt exists and
        # contains the per-iteration markers written by get_differences_for_error_msg.
        log_path, result_directory = _run_converging_feedback_simulation(
            post_processing_options=[PostProcessingOptions.PROVIDE_DETAILED_ITERATION_LOGGING],
            label="enabled",
        )
        directories_to_clean.append(result_directory)
        assert log_path.is_file(), (
            f"Detailed_Iteration_Log.txt was not created at {log_path}. "
            "PROVIDE_DETAILED_ITERATION_LOGGING should write per-iteration convergence "
            "data when a timestep takes more than two iterations to converge."
        )
        log_text = log_path.read_text(encoding="utf-8")
        # get_differences_for_error_msg writes "previously:" and "currently:" per
        # divergent output — these markers are absent when the option is disabled
        # (the file is never created) and present only when iterative_tries > 2.
        assert "previously:" in log_text, (
            f"Detailed_Iteration_Log.txt does not contain the iteration-specific "
            f"'previously:' marker written by get_differences_for_error_msg.\n"
            f"Content:\n{log_text}"
        )
        assert "currently:" in log_text, (
            f"Detailed_Iteration_Log.txt does not contain the iteration-specific "
            f"'currently:' marker written by get_differences_for_error_msg.\n"
            f"Content:\n{log_text}"
        )

        # Negative case: option disabled → no Detailed_Iteration_Log.txt is created,
        # proving the markers are specific to this option rather than appearing in
        # general log output.
        log_path_disabled, result_directory_disabled = _run_converging_feedback_simulation(
            post_processing_options=[],
            label="disabled",
        )
        directories_to_clean.append(result_directory_disabled)
        assert not log_path_disabled.is_file(), (
            f"Detailed_Iteration_Log.txt was created at {log_path_disabled} even though "
            "PROVIDE_DETAILED_ITERATION_LOGGING was not enabled. The file must only be "
            "written when the option is active."
        )
    finally:
        for directory in directories_to_clean:
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                # Cleanup failures should not mask test results, but neither should
                # they crash the suite — log visibly per the fail-loudly principle.
                warnings.warn(f"Could not remove test result directory {directory}: {exc}", stacklevel=2)
        ResultPathProviderSingleton.reset()
        log.logger.reset()


def test_postprocessing_option_compute_opex(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.COMPUTE_OPEX produces the operational costs and CO2 footprint CSV file."""
    postprocessing_option_framework.run(
        PostProcessingOptions.COMPUTE_OPEX,
        expected_files=["operational_costs_co2_footprint.csv"],
    )


def test_postprocessing_option_compute_capex(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.COMPUTE_CAPEX produces the investment cost and CO2 footprint CSV file."""
    postprocessing_option_framework.run(
        PostProcessingOptions.COMPUTE_CAPEX,
        expected_files=["investment_cost_co2_footprint.csv"],
    )


def test_postprocessing_option_compute_kpis(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.COMPUTE_KPIS computes key performance indicators and logs them."""
    postprocessing_option_framework.run(PostProcessingOptions.COMPUTE_KPIS, expected_files=["hisim_simulation.log"])


def test_postprocessing_option_prepare_outputs_for_scenario_evaluation(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION produces hourly/daily/monthly/yearly result CSVs and config JSONs."""
    postprocessing_option_framework.run(
        PostProcessingOptions.PREPARE_OUTPUTS_FOR_SCENARIO_EVALUATION,
        expected_files=[
            "result_data_for_scenario_evaluation/hourly_*_days.csv",
            "result_data_for_scenario_evaluation/daily_*_days.csv",
            "result_data_for_scenario_evaluation/monthly_*_days.csv",
            "result_data_for_scenario_evaluation/yearly_*_days.csv",
            "result_data_for_scenario_evaluation/scenario.json",
            "result_data_for_scenario_evaluation/simulation.json",
        ],
    )


def test_postprocessing_option_write_component_configs_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON produces scenario and simulation JSON files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON,
        expected_files=["scenario.json", "simulation.json"],
    )


def test_postprocessing_option_write_kpis_to_json_for_building_sizer(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER produces the KPI config JSON for the BuildingSizer."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
        expected_files=["*_kpi_config_for_building_sizer.json"],
    )


def test_postprocessing_option_write_kpis_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_KPIS_TO_JSON produces the all_kpis.json file."""
    postprocessing_option_framework.run(PostProcessingOptions.WRITE_KPIS_TO_JSON, expected_files=["all_kpis.json"])


def test_postprocessing_option_export_to_pkl(postprocessing_option_framework: PostProcessingOptionTestFramework) -> None:
    """Test that PostProcessingOptions.EXPORT_TO_PKL produces pickled result files."""
    postprocessing_option_framework.run(PostProcessingOptions.EXPORT_TO_PKL, expected_files=["*.pkl"])


def test_postprocessing_option_write_configs_for_scenario_evaluation_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON produces scenario and simulation JSON files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON,
        expected_files=["scenario.json", "simulation.json"],
    )


def test_postprocessing_option_export_monthly_results(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.EXPORT_MONTHLY_RESULTS produces monthly result CSV files."""
    postprocessing_option_framework.run(PostProcessingOptions.EXPORT_MONTHLY_RESULTS, expected_files=["*_monthly.csv"])


def test_postprocessing_option_export_results_in_one_file(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE produces a single consolidated all_results.csv file."""
    postprocessing_option_framework.run(
        PostProcessingOptions.EXPORT_RESULTS_IN_ONE_FILE,
        expected_files=["all_results.csv"],
    )


def test_each_postprocessing_option_has_a_named_test() -> None:
    """Guard against adding enum values without adding a dedicated runtime-statistics test."""

    actual_test_names = {
        name
        for name, obj in globals().items()
        if inspect.isfunction(obj) and name.startswith("test_postprocessing_option_")
    }
    expected_test_names = {
        f"test_postprocessing_option_{postprocessing_option.name.lower()}"
        for postprocessing_option in PostProcessingOptions
    }

    assert actual_test_names == expected_test_names
