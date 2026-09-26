"""The one list of things the translator accepts without acting on them, and how it is read.

Rule 6 of the calculation-request specification, adopted as decision D-E: a feature the
translator does not implement is a **note** in the mapping report and the calculation runs -- but
only if it is written down in ``not_implemented_yet.yaml``. Anything the translator cannot map
that is *not* written down fails the translator's own build with exit 3, never the user's
request. That is what keeps the list honest in both directions, and what T-NIY tests::

    whitelist = Whitelist.load()
    entry = whitelist.match(Unmapped("house.heating.cooking_range", True), house)
    entry.note        # 'No HiSim component for a range cooker.'

An entry names either a request path or a catalogue measure item, and narrows it in two ways.
``except`` lists the values that *are* implemented, so a listed path whose value is excepted does
not match. ``when`` is a condition on the **renovated** house -- the house after the measures ran
-- because whether a separate hot-water heat pump can be modelled depends on the generator the
package installs, not on the one it replaced.

Where two entries match one item, the more specific wins: an exact path beats a prefix, a longer
path beats a shorter one, and among equals the one with more conditions wins. That is what lets
the two ``house.hot_water.supply`` entries divide the three supply values between them without
either of them having to know about the other.

Every entry also says what becomes of it, with exactly one of two keys (hisim-bc02): ``tracked``
names the bead -- or the beads -- whose work would let the entry be deleted, and ``deliberate``
gives the reason HiSim is not meant to act on the item at all. The loader refuses an entry with
neither or with both, so an omission can no longer be written down without an owner or a reason.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml


class TranslatorError(Exception):
    """Something the translator could not map and nobody wrote down.

    It is a bug in this package, not in the request: exit code 3, ``translator_error.json``, and
    a one-line reason on standard error that the backend shows as the job's error message. A
    released translator cannot raise it, because T-NIY runs the whole probe set and asserts that
    every unmapped item has an entry.

    Args:
        message: The one-line reason.
        detail: What was being translated when it happened, for the file.
    """

    def __init__(self, message: str, detail: str = "") -> None:
        """Store the reason and its detail."""
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_json(self) -> Dict[str, str]:
        """Return the ``translator_error.json`` document: ``{"message", "detail"}``."""
        return {"message": self.message, "detail": self.detail}


class ItemKind(str, Enum):
    """Whether an unmapped item is a request path or something out of the measure catalogue.

    The two are looked up in different halves of the list, because a measure id and a request
    path can spell the same word -- ``solar_thermal_system`` is both -- and they mean different
    things.
    """

    PATH = "path"
    MEASURE = "measure"


@dataclass(frozen=True)
class Unmapped:
    """One thing the translator found no target for, as it asks the list about it.

    Args:
        item: The request path (``house.heating.cooking_range``) or the measure item
            (``heating_system.installation_year``, ``hot_water_system.supply``).
        value: The value the request carried there, which ``except`` is tested against; ``None``
            when the item has no single value, as for a whole block.
        kind: Which half of the list to look in.
    """

    item: str
    value: Any = None
    kind: ItemKind = ItemKind.PATH

    @classmethod
    def measure(cls, item: str, value: Any = None) -> "Unmapped":
        """Return an unmapped measure item: an id, an ``id.option`` or an ``id.option`` value."""
        return cls(item=item, value=value, kind=ItemKind.MEASURE)


class Condition:
    """One ``when:`` expression, evaluated against the renovated house.

    The grammar is deliberately tiny -- a dotted field of the house, one of four operators, and a
    value or a list of values -- because the alternative is a condition language nobody reviews.
    Four operators cover every entry the list has ever needed::

        heating.type_of_system != air_source_heat_pump
        heating.type_of_system == electric_heating
        heating.type_of_system in [conventional_gas_heating, condensing_gas_heating]
        heating.type_of_system not in [conventional_gas_heating, air_source_heat_pump]

    Args:
        field: The dotted path inside ``house`` the condition reads.
        operator: One of ``==``, ``!=``, ``in``, ``not in``.
        values: The values the field is compared against; one for the first two operators.
    """

    #: The expression grammar: a dotted field, an operator, and a scalar or a bracketed list.
    PATTERN: ClassVar["re.Pattern[str]"] = re.compile(
        r"^\s*(?P<field>[A-Za-z0-9_.]+)\s+(?P<operator>==|!=|not in|in)\s+(?P<value>.+?)\s*$"
    )

    def __init__(self, field: str, operator: str, values: Tuple[str, ...]) -> None:
        """Store the parsed parts of one expression."""
        self.field = field
        self.operator = operator
        self.values = values

    @classmethod
    def parse(cls, expression: str) -> "Condition":
        """Parse one ``when:`` expression.

        Args:
            expression: The text of the entry's ``when`` key.

        Returns:
            The condition.

        Raises:
            TranslatorError: When the expression is not in the grammar, which is a fault in the
                list rather than in a request.
        """
        match = cls.PATTERN.match(expression)
        if match is None:
            raise TranslatorError(
                f"not_implemented_yet.yaml carries a 'when' this translator cannot read: {expression!r}",
                "the grammar is '<dotted field> == | != | in | not in <value or [list]>'",
            )
        raw = match.group("value").strip()
        if raw.startswith("[") and raw.endswith("]"):
            values = tuple(part.strip() for part in raw[1:-1].split(",") if part.strip())
        else:
            values = (raw,)
        return cls(field=match.group("field"), operator=match.group("operator"), values=values)

    def holds_for(self, house: Mapping[str, Any]) -> bool:
        """Return whether the condition is true of one renovated house.

        Args:
            house: The renovated ``house`` block, as plain dictionaries.

        Returns:
            ``True`` when the condition holds. A field the house does not carry compares as the
            empty string, so ``!=`` holds and ``==`` does not, which is the reading an entry
            about a value the house does not have needs.
        """
        actual = self._read(house)
        if self.operator == "==":
            return actual == self.values[0]
        if self.operator == "!=":
            return actual != self.values[0]
        if self.operator == "in":
            return actual in self.values
        return actual not in self.values

    def _read(self, house: Mapping[str, Any]) -> str:
        """Return the field's value as a string, or the empty string when the house lacks it."""
        current: Any = house
        for part in self.field.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return ""
            current = current[part]
        return "" if current is None else str(current)

    def describe(self) -> str:
        """Return the expression as it was written, for a message."""
        rendered = self.values[0] if len(self.values) == 1 else "[" + ", ".join(self.values) + "]"
        return f"{self.field} {self.operator} {rendered}"


@dataclass(frozen=True)
class WhitelistEntry:
    """One line of ``not_implemented_yet.yaml``.

    Args:
        kind: Whether the entry names a request path or a measure item.
        item: The path or the measure item, as written.
        note: The sentence the mapping report and the capability document carry, verbatim.
        excepted: The values that *are* implemented and therefore do not match this entry.
        when: The condition on the renovated house, or ``None``.
        tracked: The beads whose work would let the entry be deleted; empty when it is deliberate.
        deliberate: Why HiSim is not meant to act on the item, or ``None`` when it is tracked.
    """

    kind: ItemKind
    item: str
    note: str
    excepted: Tuple[str, ...] = ()
    when: Optional[Condition] = None
    tracked: Tuple[str, ...] = ()
    deliberate: Optional[str] = None

    def key(self) -> str:
        """Return the identity of this entry, which a test uses to say which probe hit it."""
        parts = [f"{self.kind.value}:{self.item}"]
        if self.excepted:
            parts.append("except=" + ",".join(self.excepted))
        if self.when is not None:
            parts.append("when=" + self.when.describe())
        return " ".join(parts)

    def matches(self, unmapped: Unmapped, house: Mapping[str, Any]) -> bool:
        """Return whether this entry covers one unmapped item of one renovated house."""
        if unmapped.kind is not self.kind or not self._covers(unmapped.item):
            return False
        if unmapped.value is not None and str(unmapped.value) in self.excepted:
            return False
        return self.when is None or self.when.holds_for(house)

    def specificity(self) -> Tuple[int, int, int]:
        """Return how specific this entry is, for choosing between two that both match.

        Returns:
            ``(length of the item, number of excepted values, whether it has a condition)`` --
            compared tuple-wise, so a longer path wins over a shorter one and, between two of the
            same length, the one with a condition wins over the one without.
        """
        return (len(self.item), len(self.excepted), 1 if self.when is not None else 0)

    def _covers(self, item: str) -> bool:
        """Return whether this entry's item equals *item* or is a prefix of its subtree."""
        if item == self.item:
            return True
        return item.startswith(self.item) and item[len(self.item)] in ".[="


class Whitelist:
    """The parsed ``not_implemented_yet.yaml``, and the question the translator asks it.

    Args:
        entries: The entries, in file order.
    """

    #: The list itself, beside this module.
    PATH: ClassVar[Path] = Path(__file__).resolve().parent / "not_implemented_yet.yaml"

    #: What a ``tracked`` value must look like: a bead id of this repository, e.g. ``hisim-4g9.6``.
    BEAD_ID: ClassVar["re.Pattern[str]"] = re.compile(r"^hisim-[0-9a-z]+(\.[0-9]+)*$")

    def __init__(self, entries: Sequence[WhitelistEntry]) -> None:
        """Store the entries in file order and start an empty record of which ones were used."""
        self._entries = tuple(entries)
        self._hits: List[str] = []

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Whitelist":
        """Read the list from disk.

        Args:
            path: Where the list is; the committed one beside this module when omitted.

        Returns:
            The parsed list.

        Raises:
            TranslatorError: When an entry is neither a ``path`` nor a ``measure`` entry, has no
                ``note``, carries a ``when`` the condition grammar cannot read, or carries neither
                or both of ``tracked`` and ``deliberate`` (or a ``tracked`` that is no bead id).
        """
        with (path or cls.PATH).open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or []
        entries: List[WhitelistEntry] = []
        for index, row in enumerate(raw):
            entries.append(cls._entry(row, index))
        return cls(entries)

    @classmethod
    def _entry(cls, row: Mapping[str, Any], index: int) -> WhitelistEntry:
        """Build one entry from one mapping of the file, refusing anything the grammar lacks."""
        if "path" in row:
            kind, item = ItemKind.PATH, str(row["path"])
        elif "measure" in row:
            kind, item = ItemKind.MEASURE, str(row["measure"])
        else:
            raise TranslatorError(
                f"not_implemented_yet.yaml entry {index} names neither a path nor a measure",
                repr(dict(row)),
            )
        note = row.get("note")
        if not note:
            raise TranslatorError(
                f"not_implemented_yet.yaml entry {index} ({item}) carries no note",
                "the note is the sentence users read; an entry without one cannot be published",
            )
        tracked, deliberate = cls._fate(row, index, item)
        return WhitelistEntry(
            kind=kind,
            item=item,
            note=str(note),
            excepted=tuple(str(value) for value in (row.get("except") or ())),
            when=Condition.parse(str(row["when"])) if row.get("when") else None,
            tracked=tracked,
            deliberate=deliberate,
        )

    @classmethod
    def _fate(cls, row: Mapping[str, Any], index: int, item: str) -> Tuple[Tuple[str, ...], Optional[str]]:
        """Return an entry's beads and its reason, refusing an entry with neither or with both.

        Args:
            row: The entry as the file spells it.
            index: Its position, for the message.
            item: Its path or measure item, for the message.

        Returns:
            ``(tracked, deliberate)``: the bead ids (one or a list in the file) and ``None``, or
            ``()`` and the reason.

        Raises:
            TranslatorError: When the entry carries neither key or both, or a ``tracked`` value
                that is not a bead id.
        """
        raw_tracked = row.get("tracked")
        raw_deliberate = row.get("deliberate")
        if bool(raw_tracked) == bool(raw_deliberate):
            raise TranslatorError(
                f"not_implemented_yet.yaml entry {index} ({item}) must carry exactly one of "
                "'tracked' and 'deliberate'",
                "'tracked' names the bead that would let the entry be deleted; 'deliberate' says "
                "why HiSim is not meant to act on the item (hisim-bc02)",
            )
        if raw_deliberate:
            return (), str(raw_deliberate)
        values = raw_tracked if isinstance(raw_tracked, list) else [raw_tracked]
        tracked = tuple(str(value) for value in values)
        wrong = [value for value in tracked if not cls.BEAD_ID.match(value)]
        if wrong:
            raise TranslatorError(
                f"not_implemented_yet.yaml entry {index} ({item}) tracks something that is not a "
                f"bead id: {', '.join(wrong)}",
                "a document that motivated the entry belongs in a comment; 'tracked' names beads",
            )
        return tracked, None

    def entries(self) -> Tuple[WhitelistEntry, ...]:
        """Return every entry, in file order."""
        return self._entries

    def hits(self) -> Tuple[str, ...]:
        """Return the key of every entry this instance has matched, in match order with repeats.

        T-NIY needs to know which entries a probe reached, and matching a note back to an entry
        afterwards is impossible because several entries deliberately share a sentence. So the
        list records its own use, and the probe runner gives each probe a fresh instance.
        """
        return tuple(self._hits)

    def forget_hits(self) -> None:
        """Clear the record of matched entries, so one instance can serve several probes."""
        self._hits.clear()

    def match(self, unmapped: Unmapped, house: Mapping[str, Any]) -> Optional[WhitelistEntry]:
        """Return the entry that covers one unmapped item, or ``None`` when none does.

        Args:
            unmapped: What the translator found no target for.
            house: The renovated house, which every ``when`` is evaluated on.

        Returns:
            The most specific matching entry, or ``None`` -- which the caller turns into a
            :class:`TranslatorError`, never into a refusal of the request.
        """
        candidates = [entry for entry in self._entries if entry.matches(unmapped, house)]
        if not candidates:
            return None
        chosen = max(candidates, key=lambda entry: entry.specificity())
        self._hits.append(chosen.key())
        return chosen

    def require(self, unmapped: Unmapped, house: Mapping[str, Any]) -> WhitelistEntry:
        """Return the covering entry, or raise the translator error that stops the run.

        Args:
            unmapped: What the translator found no target for.
            house: The renovated house.

        Returns:
            The entry.

        Raises:
            TranslatorError: When nothing covers the item. The message names the item so that
                whoever added the field knows exactly what sentence they now have to write.
        """
        entry = self.match(unmapped, house)
        if entry is not None:
            return entry
        value = "" if unmapped.value is None else f" = {unmapped.value}"
        raise TranslatorError(
            f"nothing maps {unmapped.kind.value} '{unmapped.item}'{value} and "
            "not_implemented_yet.yaml does not list it",
            "Either write the translation, or add an entry with the sentence a user should read; "
            "silently dropping a request field is not an option (rule 6).",
        )
