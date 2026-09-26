"""The three artefacts of tier 1 as flat ``path -> value`` tables, and the difference of two of them.

A diff a person can read is a list of ``path: before -> after`` lines, so every artefact the
harness compares is first flattened into a table of leaves, and two tables are compared leaf by
leaf. The flattening is where the granularity is decided:

* **the request** (:class:`RequestLeaves`): one leaf per scalar of ``location``, ``house``,
  ``applicant`` and ``schema_version``, spelled as the mapping report spells them
  (``house.building.facade.u_value_in_watt_per_m2_per_kelvin``); the package is keyed by measure
  id rather than by position, in the spelling of the path-verification spec,
  ``measures[id=external_insulation].options.thickness_in_mm``, so adding a measure in front of
  another does not show as a change of every later one;
* **the energy system** (:class:`SystemLeaves`): one leaf per configuration field and per
  constructor argument, ``Building.config.facade_u_value_in_watt_per_m2_per_kelvin``; the wiring
  (``inputs``, ``sizing_sources``), the class and the preset are one leaf each, a group's flag and
  a variant's selection one leaf each, and a component that exists on one side only is *one* line
  -- its class appearing or disappearing -- rather than every field it carries. A component name
  that occurs in more than one place of a file (the meter of both electricity-management
  options) is qualified with where it lives, ``ElectricityMeter@variants.electricity_management.
  metered_directly``. The document's ``name`` and ``description`` are left out: they are the
  request's hash and the translator's version, and would differ in every comparison;
* **the economic context** the translator builds beside the file (``economic_context.*``), which
  is where the leaves that write no simulation component land -- an installation year, the
  living area, the applicant. Without it every economics-only leaf would read as "no effect".
"""

import dataclasses
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class Absent:
    """The value of a leaf one side of a comparison does not carry.

    One instance, :data:`ABSENT`, compared by identity. It is not ``None`` because ``None`` is a
    value a configuration field can carry.
    """

    def __repr__(self) -> str:
        """Return how a person reads it on a page."""
        return "(absent)"


#: The one :class:`Absent` instance.
ABSENT = Absent()


@dataclass(frozen=True)
class Change:
    """One leaf that differs between two artefacts.

    Args:
        path: The leaf's flat path.
        before: Its value in the base artefact, or :data:`ABSENT`.
        after: Its value in the probe's artefact, or :data:`ABSENT`.
        source: What asked for the new value, when the artefact says so: the request path an
            energy-system edit names as its source.
    """

    path: str
    before: Any
    after: Any
    source: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the change as ``report.json`` carries it: an absent side is a missing key."""
        row: Dict[str, Any] = {"path": self.path}
        if self.before is not ABSENT:
            row["before"] = self.before
        if self.after is not ABSENT:
            row["after"] = self.after
        if self.source is not None:
            row["source"] = self.source
        return row


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> Tuple[Change, ...]:
    """Return every leaf whose value differs between two flat tables, sorted by path.

    Args:
        before: The base artefact's leaves.
        after: The probe's artefact's leaves.

    Returns:
        One :class:`Change` per differing leaf; a leaf present on one side only is a change from
        or to :data:`ABSENT`.
    """
    changes: List[Change] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path, ABSENT)
        new = after.get(path, ABSENT)
        if old is ABSENT or new is ABSENT or not _same(old, new):
            changes.append(Change(path=path, before=old, after=new))
    return tuple(changes)


def _same(first: Any, second: Any) -> bool:
    """Return whether two leaf values are the same value, ``1`` and ``1.0`` included, ``True`` and ``1`` not."""
    if isinstance(first, bool) or isinstance(second, bool):
        return isinstance(first, bool) and isinstance(second, bool) and first == second
    return bool(first == second)


def request_hash(document: Mapping[str, Any]) -> str:
    """Return the cache key of one request body: :meth:`Request.content_hash`'s recipe.

    The same recipe as the translator's own and the backend's job id -- keys sorted recursively,
    no insignificant whitespace, UTF-8, the first sixteen hexadecimal characters of the SHA-256 --
    computed on the raw document, so a request the validation refuses has a key as well.

    Args:
        document: The request body.

    Returns:
        Sixteen lowercase hexadecimal characters.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def flatten(value: Any, prefix: str, into: Dict[str, Any]) -> None:
    """Write every leaf of a nested JSON-like value into a flat table.

    A mapping recurses by key and an empty mapping is one leaf of value ``{}``, so a block that is
    present but empty still differs from a block that is absent. A list of mappings that all carry
    one distinguishing key (:data:`LIST_KEYS`) is keyed by it, ``[asset_class=GasHeater]``, so
    that an item added in front does not renumber every later one; any other list is one leaf.

    Args:
        value: The value to flatten.
        prefix: The path of *value* itself.
        into: The table the leaves are written into.
    """
    if isinstance(value, Mapping):
        if not value:
            into[prefix] = {}
        for key, child in value.items():
            flatten(child, f"{prefix}.{key}" if prefix else str(key), into)
        return
    if isinstance(value, list) and value and all(isinstance(item, Mapping) for item in value):
        key = _list_key(value)
        if key is not None:
            for item in value:
                flatten({k: v for k, v in item.items() if k != key}, f"{prefix}[{key}={item[key]}]", into)
            return
    into[prefix] = value


#: The item keys a list of mappings is keyed by, in order of preference.
LIST_KEYS: Tuple[str, ...] = ("id", "asset_class", "subject", "name")


def _list_key(items: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """Return the first of :data:`LIST_KEYS` every item carries with a distinct value, or ``None``."""
    for key in LIST_KEYS:
        values = [item.get(key) for item in items]
        if all(value is not None for value in values) and len(set(map(str, values))) == len(values):
            return key
    return None


class RequestLeaves:
    """A calculation request as a flat table, in the spelling the report and the spec use."""

    #: The request blocks whose leaves are spelled as the mapping report spells them.
    BLOCKS: ClassVar[Tuple[str, ...]] = ("schema_version", "location", "house", "applicant")

    #: The key of the package.
    MEASURES: ClassVar[str] = "measures"

    @classmethod
    def of(cls, document: Mapping[str, Any]) -> Dict[str, Any]:
        """Return every leaf of one request.

        Args:
            document: The request body.

        Returns:
            ``path -> value``; a measure is ``measures[id=<id>]`` followed by its options and its
            cost block, and a measure with nothing but its id is one leaf of value ``{}``.
        """
        leaves: Dict[str, Any] = {}
        for key in cls.BLOCKS:
            if key in document:
                flatten(document[key], key, leaves)
        for entry in document.get(cls.MEASURES) or []:
            prefix = cls.measure_path(str(entry.get("id")))
            flatten({key: value for key, value in entry.items() if key != "id"}, prefix, leaves)
        return leaves

    @classmethod
    def measure_path(cls, measure_id: str) -> str:
        """Return the path one measure of the package is spelled with."""
        return f"{cls.MEASURES}[id={measure_id}]"

    @staticmethod
    def is_under(path: str, prefix: str) -> bool:
        """Return whether *path* is *prefix* itself or lies inside it."""
        return path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


class SystemLeaves:
    """A translated energy system -- the rendered file and its economic context -- as flat tables.

    Kept apart as components, other leaves and economic-context leaves, because a component that
    exists on one side only is reported as one line rather than field by field.

    Args:
        components: ``component key -> {sub-path -> value}``, e.g. ``"Building" ->
            {"config.facade_u_value_in_watt_per_m2_per_kelvin": 1.78, ...}``.
        other: The group flags, the variant selections and any other top-level key.
        economic_context: The economic context's leaves, each prefixed ``economic_context.``.
        base_file: The recorded twin the file was written into.
    """

    #: The top-level keys left out of the comparison: the request's hash and the version string.
    IGNORED_TOP_LEVEL: ClassVar[Tuple[str, ...]] = ("name", "description")

    #: The entry keys whose value is a mapping flattened field by field.
    FLATTENED_ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("config", "constructor")

    #: The prefix of every economic-context leaf.
    ECONOMIC_CONTEXT: ClassVar[str] = "economic_context"

    def __init__(
        self,
        components: Dict[str, Dict[str, Any]],
        other: Dict[str, Any],
        economic_context: Dict[str, Any],
        base_file: Optional[str],
    ) -> None:
        """Store the three tables."""
        self.components = components
        self.other = other
        self.economic_context = economic_context
        self.base_file = base_file

    @classmethod
    def of(cls, document: Mapping[str, Any], economic_context: Any, base_file: Optional[str]) -> "SystemLeaves":
        """Flatten one rendered energy-system document and its economic context.

        Args:
            document: The file as :meth:`~hisim.energy_system.emitter.EnergySystemEmitter.to_document`
                renders it.
            economic_context: The translator's :class:`~hisim.economics.bridge.EconomicContext`, or
                ``None``.
            base_file: The twin's file name.

        Returns:
            The three tables.
        """
        entries: List[Tuple[str, str, Mapping[str, Any]]] = []
        other: Dict[str, Any] = {}
        for name, entry in (document.get("components") or {}).items():
            entries.append(("components", name, entry))
        for group, body in (document.get("groups") or {}).items():
            other[f"groups.{group}.enabled"] = body.get("enabled")
            for name, entry in (body.get("components") or {}).items():
                entries.append((f"groups.{group}", name, entry))
        for variant, body in (document.get("variants") or {}).items():
            other[f"variants.{variant}.selected"] = body.get("selected")
            for option, members in (body.get("options") or {}).items():
                for name, entry in (members.get("components") or {}).items():
                    entries.append((f"variants.{variant}.{option}", name, entry))
        for key, value in document.items():
            if key not in ("components", "groups", "variants", *cls.IGNORED_TOP_LEVEL):
                other[key] = value
        occurrences = Counter(name for _, name, _ in entries)
        components: Dict[str, Dict[str, Any]] = {}
        for where, name, entry in entries:
            key = name if occurrences[name] == 1 else f"{name}@{where}"
            fields: Dict[str, Any] = {}
            for entry_key, value in entry.items():
                if entry_key in cls.FLATTENED_ENTRY_KEYS and isinstance(value, Mapping):
                    flatten(value, entry_key, fields)
                else:
                    fields[entry_key] = value
            components[key] = fields
        context: Dict[str, Any] = {}
        if economic_context is not None:
            flatten(_plain(economic_context), cls.ECONOMIC_CONTEXT, context)
        return cls(components=components, other=other, economic_context=context, base_file=base_file)

    def diff(self, tested: "SystemLeaves", sources: Mapping[str, str]) -> Tuple[Change, ...]:
        """Return what differs from this system to *tested*, at field level.

        Args:
            tested: The probe's system.
            sources: Edit location -> the request path that asked for it, from the probe's
                translation (``components.`` stripped), to annotate each change with.

        Returns:
            The changes, sorted by path: a component present on one side only as one change of
            its ``class``; a component on both sides field by field; then the flags, the
            selections and the economic context.
        """
        changes: List[Change] = []
        for key in sorted(set(self.components) | set(tested.components)):
            mine, theirs = self.components.get(key), tested.components.get(key)
            if mine is None or theirs is None:
                changes.append(
                    Change(
                        path=f"{key}.class",
                        before=ABSENT if mine is None else mine.get("class"),
                        after=ABSENT if theirs is None else theirs.get("class"),
                    )
                )
                continue
            for change in diff(mine, theirs):
                changes.append(dataclasses.replace(change, path=f"{key}.{change.path}"))
        changes.extend(diff(self.other, tested.other))
        changes.extend(diff(self.economic_context, tested.economic_context))
        return tuple(
            dataclasses.replace(change, source=_source_of(change.path, sources)) for change in changes
        )


def _source_of(path: str, sources: Mapping[str, str]) -> Optional[str]:
    """Return the source of the edit whose location is *path* or contains it, or ``None``."""
    if path in sources:
        return sources[path]
    candidates = [location for location in sources if RequestLeaves.is_under(path, location)]
    return sources[max(candidates, key=len)] if candidates else None


def edit_sources(edits: Iterable[Any]) -> Dict[str, str]:
    """Return ``location -> source`` of a translation's edits, locations spelled as :class:`SystemLeaves` spells them.

    Args:
        edits: The translation's :class:`~hisim.renovisor.translate.Edit` records.

    Returns:
        The table, with the ``components.`` prefix the edit locations carry removed.
    """
    table: Dict[str, str] = {}
    for edit in edits:
        location = str(edit.location)
        location = location[len("components."):] if location.startswith("components.") else location
        table[location] = str(edit.source)
    return table


def _plain(value: Any) -> Any:
    """Return a dataclass tree as JSON-like values: enums by their value, dataclasses as mappings."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value if isinstance(value.value, (str, int, float, bool)) else value.name
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
