"""The progress line: what a running calculation tells the worker that runs it.

The translator writes its progress to standard output as single lines, one JSON object each,
behind a fixed prefix (``progress-spec`` §1)::

    RENOVISOR_PROGRESS {"phase":"simulating","fraction":0.42,"eta_seconds":35,"simulated_days":153.3,"total_days":365}

A ``run`` passes through five phases in order -- ``reading``, ``preparing``, ``simulating``,
``evaluating``, ``writing`` -- and writes one line on entering each. During ``simulating`` it
writes at most one more line every five seconds, from the time loop's own progress message
(:meth:`hisim.simulator.Simulator.add_progress_callback`), and one with ``fraction`` 1 when the loop
ends, which is also where ``evaluating`` begins: HiSim's postprocessing runs right after the loop.
The ``staged`` economics command writes only ``reading``, ``evaluating`` and ``writing``.

Progress is informational. :class:`ProgressWriter` swallows every way writing a line can fail
-- a closed or broken standard output, an encoding error -- so that a calculation runs and ends
exactly as it would without it.
"""

import enum
import json
import sys
import time
from typing import Any, Callable, ClassVar, Dict, Optional, TextIO, Tuple

from hisim.simulator import ProgressEvent, SimulationProgress


class Phase(str, enum.Enum):
    """The phases of a calculation, in the order it passes through them."""

    #: Parsing and validating the request.
    READING = "reading"
    #: Translating, building the system, weather data, occupancy and LPG profiles.
    PREPARING = "preparing"
    #: The time loop.
    SIMULATING = "simulating"
    #: HiSim's postprocessing, the KPIs, the economic inputs and the result document.
    EVALUATING = "evaluating"
    #: Writing the output files.
    WRITING = "writing"


class ProgressLine:
    """The format of one progress line, and the only place that knows it."""

    #: What every progress line starts with; one space and one JSON object follow.
    PREFIX: ClassVar[str] = "RENOVISOR_PROGRESS"

    #: The fields a line may carry; ``phase`` is on every line.
    FIELDS: ClassVar[Tuple[str, ...]] = ("phase", "fraction", "eta_seconds", "simulated_days", "total_days")

    @classmethod
    def format(cls, fields: Dict[str, Any]) -> str:
        """Return one line, without its line break, for the given fields.

        Args:
            fields: ``phase`` and any of the optional fields; a field set to ``None`` is left out.

        Returns:
            The prefix, one space and the fields as compact JSON.
        """
        body = {name: fields[name] for name in cls.FIELDS if fields.get(name) is not None}
        return f"{cls.PREFIX} {json.dumps(body, separators=(',', ':'), ensure_ascii=True)}"

    @classmethod
    def parse(cls, line: str) -> Optional[Dict[str, Any]]:
        """Return the fields of a progress line, or ``None`` when the line is not one.

        This is the worker's reading of the line, kept beside the writing so that the two are
        tested together.
        """
        head = cls.PREFIX + " "
        if not line.startswith(head):
            return None
        try:
            document = json.loads(line[len(head):])
        except ValueError:
            return None
        return document if isinstance(document, dict) else None


class ProgressWriter:
    """Writes a calculation's progress lines, and can never fail the calculation.

    It keeps the phase it is in, so that entering the same phase twice writes one line, and the
    simulated days, so that they never decrease and every line from ``simulating`` on carries them.

    Args:
        stream: Where the lines go; standard output, looked up at each write, when omitted.
        clock: Seconds from an arbitrary origin, for the five-second cadence; ``time.monotonic``
            when omitted.
        enabled: A writer that is not enabled writes nothing and keeps no state.
    """

    #: The shortest interval between two periodic lines during ``simulating``, in seconds.
    MINIMUM_INTERVAL_SECONDS: ClassVar[float] = 5.0

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        clock: Optional[Callable[[], float]] = None,
        enabled: bool = True,
    ) -> None:
        """Store the stream and the clock; nothing is written until the first phase."""
        self._stream = stream
        self._clock = clock or time.monotonic
        self._enabled = enabled
        self._phase: Optional[Phase] = None
        self._simulated_days: Optional[float] = None
        self._total_days: Optional[float] = None
        self._last_periodic: Optional[float] = None

    @classmethod
    def silent(cls) -> "ProgressWriter":
        """Return a writer that writes nothing, for the commands that report no progress."""
        return cls(enabled=False)

    @property
    def simulated_days(self) -> Optional[float]:
        """Return the simulated days reported so far, or ``None`` when no time loop has run."""
        return self._simulated_days

    def enter(self, phase: Phase) -> None:
        """Write the line of a phase the calculation enters, unless it is already in it."""
        if not self._enabled or phase is self._phase:
            return
        self._phase = phase
        self._write({"phase": phase.value})

    def on_simulation_progress(self, progress: SimulationProgress) -> None:
        """Turn one report of the time loop into a line; the callback the simulator is given.

        The loop's start enters ``simulating``, its periodic reports write a line at most every
        five seconds, and its end writes the last ``simulating`` line and enters ``evaluating``.
        """
        if not self._enabled:
            return
        self._total_days = round(progress.total_days, 3)
        days = round(progress.simulated_days, 3)
        self._simulated_days = days if self._simulated_days is None else max(self._simulated_days, days)
        now = self._clock()
        if progress.event is ProgressEvent.PERIODIC:
            if self._last_periodic is not None and now - self._last_periodic < self.MINIMUM_INTERVAL_SECONDS:
                return
        self._last_periodic = now
        self._phase = Phase.SIMULATING
        self._write(
            {
                "phase": Phase.SIMULATING.value,
                "fraction": round(progress.fraction, 4),
                "eta_seconds": None if progress.eta_seconds is None else round(progress.eta_seconds),
            }
        )
        if progress.event is ProgressEvent.LOOP_END:
            self.enter(Phase.EVALUATING)

    def _write(self, fields: Dict[str, Any]) -> None:
        """Write one line and flush it, swallowing every way that can fail."""
        fields = {**fields, "simulated_days": self._simulated_days, "total_days": self._total_days}
        try:
            stream = self._stream if self._stream is not None else sys.stdout
            stream.write(ProgressLine.format(fields) + "\n")
            stream.flush()
        except Exception:  # pylint: disable=broad-except  # progress never fails a calculation
            pass
