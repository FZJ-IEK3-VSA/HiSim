"""What the grouping pass tells the person who ran it, including what it did not prove.

A pass that only printed "done" would be worse than useless here, because the two things a reader
has to know are exactly the two the file itself cannot say. The grouped file carries the baseline's
value for every knob a consumer sets, so those values have to be named somewhere; and a grouped
file's guarantees reach exactly as far as its probe list, so the combinations nobody probed have to
be named too, rather than being left for the file's structure to imply.

The second of those is the one that is easy to get wrong. A group asserts that its switch is
independent of the others, and a probe list that toggles each fork on its own has not tested a
single pair of them together. The report therefore spans the full space of the module-configuration
fields the probe list varies and names every point in it the probes did not visit. It is a list of
things nobody has checked, not a list of things that are broken, and it reads that way.

The verdicts come first because they are the pass or fail: one line per probe column saying whether
realizing the grouped file at that column's switch positions reproduced that column's flat recording
byte for byte, and how many knobs it needed to do so. A column with no knobs proves the file
outright; a column with several proves everything except those values, and the number is printed so
that nobody has to infer it.
"""

# clean

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Sequence, Tuple

from hisim.energy_system.recording.probes import ProbeList
from hisim.energy_system.recording.regrouping import Knob


class CombinationSpace:
    """The space of module configurations a probe list touches, and the corners it never visits.

    The axes are the fields some probe changes; the values of an axis are the class default plus
    every value some probe gives it. The probed points are the probes themselves, each read as one
    value per axis with the default standing in wherever a probe says nothing. Everything else is a
    combination the probe list did not exercise, and therefore something the grouped file's
    structure suggests but nothing has checked.

    The default is written as a name rather than as the value it stands for, because reading the
    real value would mean importing and calling the setup's configuration class, and a report that
    can only be produced inside a recording process is a report nobody runs.
    """

    #: How the report spells the value an axis has when a probe does not mention it.
    DEFAULT: ClassVar[str] = "(class default)"

    @classmethod
    def untested(cls, probe_list: ProbeList) -> Tuple[Tuple[Tuple[str, Any], ...], ...]:
        """The combinations of the varied fields that no probe exercised.

        Args:
            probe_list: The probes, whose overlays are the axes.

        Returns:
            One tuple of ``(field, value)`` pairs per unvisited point, in the order the cross
            product enumerates them; empty when the probes cover the space or vary nothing.
        """
        axes: Dict[str, Tuple[Any, ...]] = {
            field_name: (cls.DEFAULT, *values) for field_name, values in probe_list.axes().items()
        }
        if len(axes) < 2:
            return ()
        probed = {
            tuple(dict(probe.module_config).get(field_name, cls.DEFAULT) for field_name in axes)
            for probe in probe_list.probes
        }
        return tuple(
            tuple(zip(axes, point))
            for point in itertools.product(*axes.values())
            if point not in probed
        )


@dataclass(frozen=True)
class ColumnVerdict:
    """What one probe column's assertion came to.

    A verdict is deliberately not a boolean. When a column does not reproduce, the reader needs the
    diff between what the grouped file realized to and what the setup actually recorded, and when it
    does reproduce the reader still needs the knob count, because that is how much of the column the
    file itself accounted for.
    """

    column: str
    reproduced: bool
    knobs: int
    diff: str = ""

    def describe(self) -> str:
        """Renders the verdict as one line of the report.

        Returns:
            The column, whether it reproduced, and what it needed to.
        """
        knobs = "no knobs" if not self.knobs else f"{self.knobs} knob(s)"
        state = "reproduced byte for byte" if self.reproduced else "DID NOT reproduce"
        return f"{self.column}: {state}, with {knobs}."


@dataclass(frozen=True)
class GroupingReport:
    """Everything one grouping pass has to say, as data a caller renders where it wants to.

    Keeping it as data rather than as printed text is what lets the same report be asserted on in a
    test, printed by the command line and, later, pasted into a pull request. Nothing here decides
    anything; the pass has already decided, and this is the account of it.
    """

    setup: str
    grouped: str
    verdicts: Tuple[ColumnVerdict, ...] = ()
    knobs: Tuple[Knob, ...] = ()
    untested: Tuple[Tuple[Tuple[str, Any], ...], ...] = ()
    groups: Tuple[str, ...] = ()
    variants: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)

    @property
    def reproduced(self) -> bool:
        """Whether every probe column's assertion held.

        Returns:
            ``True`` when no verdict failed.
        """
        return all(verdict.reproduced for verdict in self.verdicts)

    def describe(self) -> Tuple[str, ...]:
        """Renders the whole report, one line at a time.

        Returns:
            The shape of the grouped file, one line per column verdict, one per consumer knob and
            one per untested combination, each section introduced by a heading line.
        """
        lines: List[str] = [
            f"Grouped {self.setup} into {self.grouped}.",
            f"  groups: {', '.join(self.groups) or 'none'}",
            "  variants: " + (self._variants() or "none"),
            "Every probe column is an assertion:",
        ]
        lines.extend(f"  {verdict.describe()}" for verdict in self.verdicts)
        lines.append(f"Consumer knobs the file does not determine ({len(self.knobs)}):")
        lines.extend(f"  {knob.describe()}" for knob in self.knobs)
        if not self.knobs:
            lines.append("  none — the file determines every value of every probed configuration.")
        lines.append(f"Fork combinations the probe list never exercised ({len(self.untested)}):")
        lines.extend(f"  {self._combination(point)}" for point in self.untested)
        if not self.untested:
            lines.append("  none — the probes cover every combination of the fields they vary.")
        return tuple(lines)

    def _variants(self) -> str:
        """Renders the variants and their options for the heading block.

        Returns:
            One ``name(option, option)`` group per variant, comma separated.
        """
        return ", ".join(f"{name}({', '.join(options)})" for name, options in dict(self.variants).items())

    @classmethod
    def _combination(cls, point: Sequence[Tuple[str, Any]]) -> str:
        """Renders one unvisited point of the combination space.

        Args:
            point: The ``(field, value)`` pairs describing it.

        Returns:
            The rendered point.
        """
        return ", ".join(f"{name}={value}" for name, value in point)
