"""The progress line (progress-spec §1, §6): its format, its cadence, and that it cannot fail a run.

The writer is driven directly -- with the simulator's own report type and a fake clock -- so that
the five-second cadence is tested in microseconds rather than waited for. The order of the phases
of a whole ``run`` is in ``test_cli.py``, beside the other tests of the command.
"""

import io
import json
from typing import Any, Dict, List

import pytest

from hisim.renovisor.progress import Phase, ProgressLine, ProgressWriter
from hisim.simulator import ProgressEvent, SimulationProgress

pytestmark = pytest.mark.base


class FakeClock:
    """A clock that says whatever time the test sets."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the time the test last set."""
        return self.now


def report(event: ProgressEvent, done: int, eta: Any = None, timesteps: int = 96) -> SimulationProgress:
    """Return one report of a one-day, quarter-hourly loop."""
    return SimulationProgress(
        event=event, timesteps_done=done, timesteps=timesteps, seconds_per_timestep=900, eta_seconds=eta
    )


def lines(stream: io.StringIO) -> List[Dict[str, Any]]:
    """Return every line written, parsed; a line that is not a progress line fails the test."""
    parsed = []
    for line in stream.getvalue().splitlines():
        fields = ProgressLine.parse(line)
        assert fields is not None, line
        parsed.append(fields)
    return parsed


class TestTheFormat:
    """One prefix, one space, one compact JSON object with only the spec's fields."""

    def test_a_line_is_the_prefix_and_compact_json(self) -> None:
        """The example of spec §1, byte for byte."""
        line = ProgressLine.format(
            {"phase": "simulating", "fraction": 0.42, "eta_seconds": 35, "simulated_days": 153.3, "total_days": 365}
        )
        assert line == (
            'RENOVISOR_PROGRESS {"phase":"simulating","fraction":0.42,"eta_seconds":35,'
            '"simulated_days":153.3,"total_days":365}'
        )

    def test_every_written_line_parses_and_carries_only_the_allowed_keys(self) -> None:
        """``phase`` on every line, nothing outside the field table, no line break inside."""
        stream = io.StringIO()
        writer = ProgressWriter(stream=stream, clock=FakeClock())
        writer.enter(Phase.READING)
        writer.enter(Phase.PREPARING)
        writer.on_simulation_progress(report(ProgressEvent.LOOP_START, 0))
        writer.on_simulation_progress(report(ProgressEvent.LOOP_END, 96, 0.0))
        writer.enter(Phase.WRITING)

        for raw in stream.getvalue().splitlines():
            assert raw.startswith(ProgressLine.PREFIX + " {")
            fields = json.loads(raw[len(ProgressLine.PREFIX) + 1:])
            assert "phase" in fields
            assert set(fields) <= set(ProgressLine.FIELDS)

    def test_what_is_not_a_progress_line_is_not_parsed_as_one(self) -> None:
        """The worker's reading: a log line, a bad object and a list are all refused."""
        assert ProgressLine.parse("INFO Simulating... 42.0%") is None
        assert ProgressLine.parse("RENOVISOR_PROGRESS {not json") is None
        assert ProgressLine.parse("RENOVISOR_PROGRESS [1]") is None


class TestTheCadence:
    """One line per phase entered, periodic lines at most every five seconds, days never back."""

    def test_the_simulating_lines_follow_the_loop_and_its_clock(self) -> None:
        """Start, the periodic reports five seconds apart, the end, then ``evaluating``."""
        stream, clock = io.StringIO(), FakeClock()
        writer = ProgressWriter(stream=stream, clock=clock)
        writer.enter(Phase.PREPARING)
        writer.on_simulation_progress(report(ProgressEvent.LOOP_START, 0))
        for now, done in ((1.0, 4), (5.5, 20), (7.0, 30), (10.4, 40), (12.0, 60)):
            clock.now = now
            writer.on_simulation_progress(report(ProgressEvent.PERIODIC, done, eta=30.0))
        clock.now = 13.0
        writer.on_simulation_progress(report(ProgressEvent.LOOP_END, 96, eta=0.0))

        written = lines(stream)
        assert [line["phase"] for line in written] == ["preparing"] + ["simulating"] * 4 + ["evaluating"]
        simulating = written[1:5]
        # 1.0 s is too soon after the start, 7.0 s and 10.4 s too soon after 5.5 s.
        assert [line["fraction"] for line in simulating] == [0.0, round(20 / 96, 4), round(60 / 96, 4), 1.0]
        assert simulating[0] == {"phase": "simulating", "fraction": 0.0, "simulated_days": 0.0, "total_days": 1.0}
        assert simulating[1]["eta_seconds"] == 30
        assert simulating[-1]["simulated_days"] == simulating[-1]["total_days"] == 1.0
        assert written[-1] == {"phase": "evaluating", "simulated_days": 1.0, "total_days": 1.0}

    def test_the_simulated_days_never_decrease(self) -> None:
        """A report behind the last one leaves the figure where it was."""
        stream, clock = io.StringIO(), FakeClock()
        writer = ProgressWriter(stream=stream, clock=clock)
        writer.on_simulation_progress(report(ProgressEvent.LOOP_START, 0))
        clock.now = 6.0
        writer.on_simulation_progress(report(ProgressEvent.PERIODIC, 48))
        clock.now = 12.0
        writer.on_simulation_progress(report(ProgressEvent.PERIODIC, 24))

        days = [line["simulated_days"] for line in lines(stream)]
        assert days == sorted(days) == [0.0, 0.5, 0.5]

    def test_entering_a_phase_twice_writes_one_line(self) -> None:
        """A phase line is written on entering, not on every call."""
        stream = io.StringIO()
        writer = ProgressWriter(stream=stream)
        writer.enter(Phase.READING)
        writer.enter(Phase.READING)
        assert [line["phase"] for line in lines(stream)] == ["reading"]

    def test_a_silent_writer_writes_nothing(self, capsys) -> None:
        """What ``translate`` and ``validate`` use: their standard output stays theirs."""
        writer = ProgressWriter.silent()
        writer.enter(Phase.READING)
        writer.on_simulation_progress(report(ProgressEvent.LOOP_END, 96, 0.0))
        assert capsys.readouterr().out == ""
        assert writer.simulated_days is None


class BrokenPipe(io.StringIO):
    """A standard output whose reader has gone away."""

    def write(self, text: str) -> int:
        """Raise the way a closed pipe does."""
        raise BrokenPipeError(32, "Broken pipe")


def _closed() -> io.StringIO:
    """Return a stream that has been closed."""
    stream = io.StringIO()
    stream.close()
    return stream


class TestTheWriterCannotFail:
    """Every way a line can fail to be written is swallowed."""

    @pytest.mark.parametrize("make_stream", [BrokenPipe, _closed], ids=["broken pipe", "closed"])
    def test_a_stream_that_refuses_the_line_raises_nothing(self, make_stream: Any) -> None:
        """The calculation goes on, and the figures are still kept for ``calculation.json``."""
        writer = ProgressWriter(stream=make_stream())
        writer.enter(Phase.READING)
        writer.on_simulation_progress(report(ProgressEvent.LOOP_END, 96, 0.0))
        assert writer.simulated_days == 1.0

    def test_no_standard_output_at_all_raises_nothing(self, monkeypatch) -> None:
        """A process started without one has ``sys.stdout`` set to ``None``."""
        monkeypatch.setattr("sys.stdout", None)
        ProgressWriter().enter(Phase.READING)
