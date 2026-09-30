"""The simulator's progress hook: what it reports, when, and that it can never break a run.

``Simulator.add_progress_callback`` is generic -- nothing in it knows who listens -- and it
reports three moments: the time loop's start, every progress message ``show_progress`` logs, and
the loop's end. One day at hourly resolution with the example component is a real loop that
finishes in well under a second; the periodic report is driven through ``show_progress`` itself,
because a loop that short never logs one.
"""

import datetime
from pathlib import Path
from typing import List

import pytest

from hisim.components import example_component
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import ProgressEvent, SimulationProgress, Simulator
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


def one_day_simulator(tmp_path: Path) -> Simulator:
    """Return a simulator over one hourly day of the example component, ready to run."""
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.result_directory = str(tmp_path / "results")
    simulator: Simulator = Simulator(
        module_directory=str(tmp_path),
        module_filename="progress_hook",
        my_simulation_parameters=parameters,
    )
    simulator.set_simulation_parameters(parameters)
    simulator.add_component(
        example_component.ExampleComponent(
            config=fft.sized_example_component_config(), my_simulation_parameters=parameters
        )
    )
    return simulator


class TestTheLoopReports:
    """A registered callback hears the loop's start and end, with plain values."""

    def test_start_and_end_are_reported_with_the_loops_figures(self, tmp_path: Path) -> None:
        """Timestep 0 before the first step, every timestep after the last, one day in all."""
        reports: List[SimulationProgress] = []
        simulator = one_day_simulator(tmp_path)
        simulator.add_progress_callback(reports.append)

        simulator.run_all_timesteps()

        assert [report.event for report in reports] == [ProgressEvent.LOOP_START, ProgressEvent.LOOP_END]
        start, end = reports[0], reports[-1]
        assert (start.timesteps_done, start.fraction, start.eta_seconds, start.simulated_days) == (0, 0.0, None, 0.0)
        assert (end.timesteps_done, end.timesteps, end.seconds_per_timestep) == (24, 24, 3600)
        assert end.fraction == 1.0
        assert end.simulated_days == end.total_days == 1.0

    def test_the_progress_message_reports_its_own_estimate(self, tmp_path: Path) -> None:
        """``show_progress`` hands its callbacks the steps done and the time left it logs."""
        reports: List[SimulationProgress] = []
        simulator = one_day_simulator(tmp_path)
        simulator.add_progress_callback(reports.append)
        ten_seconds_ago = datetime.datetime.now() - datetime.timedelta(seconds=10)

        simulator.show_progress(ten_seconds_ago, 11, 11, 0, False)

        assert len(reports) == 1
        report = reports[0]
        assert report.event is ProgressEvent.PERIODIC
        assert report.timesteps_done == 12
        # 11 steps in 10 s leave 13 steps, about 12 s.
        assert report.eta_seconds == pytest.approx(13 / 1.1, rel=0.05)


class TestTheLoopCannotBeBroken:
    """Progress is informational: a failing listener is dropped and the run goes on."""

    def test_a_callback_that_raises_is_dropped_and_the_run_finishes(self, tmp_path: Path) -> None:
        """Called once, raising, never called again; the loop and its postprocessing finish."""
        calls: List[ProgressEvent] = []
        heard: List[ProgressEvent] = []

        def failing(progress: SimulationProgress) -> None:
            calls.append(progress.event)
            raise BrokenPipeError("standard output is gone")

        simulator = one_day_simulator(tmp_path)
        simulator.add_progress_callback(failing)
        simulator.add_progress_callback(lambda progress: heard.append(progress.event))

        simulator.run_all_timesteps()

        assert calls == [ProgressEvent.LOOP_START]
        assert heard == [ProgressEvent.LOOP_START, ProgressEvent.LOOP_END]
        assert len(simulator.results_data_frame) == 24

    def test_a_run_without_a_callback_prints_no_progress_line(self, tmp_path: Path, capfd) -> None:
        """Examples and every other HiSim user see the output they always saw."""
        one_day_simulator(tmp_path).run_all_timesteps()

        captured = capfd.readouterr()
        assert "RENOVISOR_PROGRESS" not in captured.out + captured.err
