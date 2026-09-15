"""The translation report: what happened to every input field and every measure (requirement R7).

A RenoVisor calculation must be able to say, for each field of the home inventory and each measure
of the package, whether it was used as given, approximated, defaulted, ignored, or accepted while
changing nothing. The report is that statement, built while the translation runs and written
beside the results::

    report = MappingReport()
    report.used("building_config.general.construction_year", "TABULA band 04 (1950-1966)")
    report.measure("EXTERNAL_INSULATION", ReportStatus.DEFAULTED,
                   "thickness 80 mm", rule="Q11 target-driven thickness")
    report.finalize(inventory.to_dict())     # every untouched leaf becomes an 'ignored' line

Why a class and not a list of strings: :meth:`MappingReport.finalize` has to know which leaves are
already covered, including leaves under a path that was recorded as a whole (recording
``energy_system_config.photovoltaics`` covers ``…photovoltaics.tilt_in_degree``), and that
prefix rule belongs in one place.

This module is the v1 ``MappingReport`` moved out of the deleted ``mapping.py``, with its statuses
turned into an enum, a per-measure line added, and an optional ``rule`` naming the law, table or
formula that produced a number (decision Q27).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, Iterator, List, Optional


class ReportStatus(str, Enum):
    """What the translation did with one field or one measure.

    ``USED`` — taken as given. ``APPROXIMATED`` — represented by something close but not equal,
    with ``rule`` naming what produced it. ``DEFAULTED`` — absent from the request and replaced by
    a default whose source the note carries. ``IGNORED`` — accepted and not used. ``NON_SIMULATION``
    — a measure that HiSim has no model for, simulated unchanged and reported with a reason code
    (requirement M8).
    """

    USED = "USED"
    APPROXIMATED = "APPROXIMATED"
    DEFAULTED = "DEFAULTED"
    IGNORED = "IGNORED"
    NON_SIMULATION = "NON_SIMULATION"


@dataclass(frozen=True)
class ReportEntry:
    """One report line: what happened at one JSON path.

    Args:
        path: The dotted, index-bearing path of the field or package entry, e.g.
            ``building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin`` or
            ``package.measures[2]``.
        status: One of :class:`ReportStatus`.
        note: A sentence a person can read: what happened and, for a default, where the number
            came from.
        rule: The name of the law, table or formula that produced the value, when one did.
            ``None`` for a plain pass-through.
        measure_id: The catalogue measure this line is about; ``None`` for inventory-field lines.
    """

    path: str
    status: ReportStatus
    note: str = ""
    rule: Optional[str] = None
    measure_id: Optional[str] = None


class MappingReport:
    """Collects one report line per inventory field and per package measure.

    Lines are keyed by path, so recording the same path twice replaces the first line: the last
    word on a field wins, which is what happens when a measure overwrites an inventory value.
    :meth:`finalize` then fills in every inventory leaf that no line covers, so the report is
    complete by construction rather than by discipline.
    """

    #: The path prefix under which per-measure lines are recorded, indexed by the order in which
    #: measures were first reported.
    MEASURE_PATH_PREFIX: ClassVar[str] = "package.measures"

    #: The note :meth:`finalize` gives a leaf that no measure and no binding touched.
    UNTOUCHED_NOTE: ClassVar[str] = "no measure changed this field"

    def __init__(self) -> None:
        """Create an empty report."""
        self._entries: Dict[str, ReportEntry] = {}
        self._measure_order: List[str] = []

    def add(self, path: str, status: ReportStatus, note: str = "", rule: Optional[str] = None) -> None:
        """Record (or replace) the line for *path*.

        Args:
            path: The dotted path the line is about.
            status: One of :class:`ReportStatus`.
            note: The human-readable sentence.
            rule: The law, table or formula that produced the value, if any.
        """
        self._entries[path] = ReportEntry(path=path, status=status, note=note, rule=rule)

    def used(self, path: str, note: str = "", rule: Optional[str] = None) -> None:
        """Record *path* as taken from the request unchanged."""
        self.add(path, ReportStatus.USED, note, rule)

    def approximated(self, path: str, note: str = "", rule: Optional[str] = None) -> None:
        """Record *path* as represented only approximately, with *rule* naming what produced it."""
        self.add(path, ReportStatus.APPROXIMATED, note, rule)

    def defaulted(self, path: str, note: str = "", rule: Optional[str] = None) -> None:
        """Record *path* as absent from the request and replaced by a default."""
        self.add(path, ReportStatus.DEFAULTED, note, rule)

    def ignored(self, path: str, note: str = "", rule: Optional[str] = None) -> None:
        """Record *path* as accepted but not used by the translation."""
        self.add(path, ReportStatus.IGNORED, note, rule)

    def non_simulation(self, path: str, note: str = "", rule: Optional[str] = None) -> None:
        """Record *path* as accepted while changing nothing, because HiSim has no model for it."""
        self.add(path, ReportStatus.NON_SIMULATION, note, rule)

    def measure(
        self,
        measure_id: str,
        status: ReportStatus,
        note: str = "",
        rule: Optional[str] = None,
        index: Optional[int] = None,
    ) -> None:
        """Record the line for one package measure.

        Args:
            measure_id: The catalogue measure id, e.g. ``EXTERNAL_INSULATION``.
            status: One of :class:`ReportStatus`.
            note: What the measure did, or why it did nothing.
            rule: The law, table or formula behind the number the measure produced.
            index: The measure's position in the package. Omit it to use the position at which
                this measure was first reported, which is the package order when the application
                walks the package once.
        """
        position = self.measure_index(measure_id) if index is None else index
        path = f"{self.MEASURE_PATH_PREFIX}[{position}]"
        self._entries[path] = ReportEntry(
            path=path, status=status, note=note, rule=rule, measure_id=measure_id
        )

    def measure_index(self, measure_id: str) -> int:
        """Return the position this measure holds in the report, registering it if it is new.

        Args:
            measure_id: The catalogue measure id.

        Returns:
            The zero-based position, assigned in order of first appearance.
        """
        if measure_id not in self._measure_order:
            self._measure_order.append(measure_id)
        return self._measure_order.index(measure_id)

    def has_measure(self, measure_id: str) -> bool:
        """Return whether a line for *measure_id* has already been recorded, wherever it sits.

        Lets a caller that does not know a measure's position ask the question anyway; a caller
        that does know it asks :meth:`has_measure_line`, which cannot be confused by two measures
        whose lines were recorded out of order.
        """
        return any(entry.measure_id == measure_id for entry in self._entries.values())

    def has_measure_line(self, index: int) -> bool:
        """Return whether a line has already been recorded for the package entry at *index*.

        Args:
            index: The measure's position in the package.

        Returns:
            ``True`` when something -- a registry function, usually -- already said what this
            entry did, so that a derived line does not overwrite a more precise one.
        """
        return f"{self.MEASURE_PATH_PREFIX}[{index}]" in self._entries

    def finalize(self, inventory_dict: Dict[str, Any]) -> None:
        """Add an ``IGNORED`` line for every inventory leaf no line covers yet.

        Args:
            inventory_dict: The post-measure inventory as plain dictionaries and lists.
        """
        for leaf_path in self._iter_leaf_paths(inventory_dict):
            if not self._is_covered(leaf_path):
                self.add(leaf_path, ReportStatus.IGNORED, self.UNTOUCHED_NOTE)

    def to_list(self) -> List[Dict[str, Any]]:
        """Return the lines as JSON-ready dictionaries, sorted by path.

        Returns:
            One dictionary per line with ``path``, ``status`` and ``note``, plus ``rule`` and
            ``measure_id`` where the line carries them.
        """
        rows: List[Dict[str, Any]] = []
        for entry in sorted(self._entries.values(), key=lambda item: item.path):
            row: Dict[str, Any] = {"path": entry.path, "status": entry.status.value, "note": entry.note}
            if entry.rule is not None:
                row["rule"] = entry.rule
            if entry.measure_id is not None:
                row["measure_id"] = entry.measure_id
            rows.append(row)
        return rows

    def _is_covered(self, leaf_path: str) -> bool:
        """Return whether *leaf_path* equals, or lies under, an already recorded path."""
        if leaf_path in self._entries:
            return True
        return any(
            leaf_path.startswith(recorded) and leaf_path[len(recorded)] in ".["
            for recorded in self._entries
        )

    @classmethod
    def _iter_leaf_paths(cls, value: Any, prefix: str = "") -> Iterator[str]:
        """Yield the dotted, index-bearing path of every leaf in a nested JSON-like structure."""
        if isinstance(value, dict):
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                yield from cls._iter_leaf_paths(child, child_prefix)
        elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
            for index, item in enumerate(value):
                yield from cls._iter_leaf_paths(item, f"{prefix}[{index}]")
        elif prefix:
            yield prefix
