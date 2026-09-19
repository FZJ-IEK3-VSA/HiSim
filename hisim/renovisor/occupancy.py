"""Choosing the LoadProfileGenerator household that comes closest to the dwelling's residents.

**Nothing in the MVP calls this module.** Decision D-C settled that every household is simulated
as the one precomputed ``CHR01 Couple both at Work`` profile the image ships, because the
LoadProfileGenerator is not in the image and its calculation times were what made the decision.
The module is kept, and kept working, because that decision is a deferral and not a reversal:
the moment the binary ships, ``OccupancyMode.LOCAL_LPG`` becomes selectable and the translator
needs exactly this -- a nearest neighbour over the catalogue and an honest note saying what was
relaxed::

    match = HouseholdMatcher().match(number_of_residents=3)
    match.household.Name   # 'CHR03 Family, 1 child, both at work'
    match.note             # '3 resident(s) matched exactly.'

The catalogue is read at import from :mod:`utspclient.helpers.lpgdata`, never transcribed here:
a LoadProfileGenerator release that adds a household must widen the choice rather than need a
code change. What the catalogue does *not* carry is the composition of each household as data --
a ``JsonReference`` holds a name and a GUID and nothing else -- so the composition is read out
of the name by the small, closed set of phrase rules in :class:`HouseholdNameReading`. A name no
rule can read contributes no adult count, and such an entry is left out of the choice rather
than guessed at; :meth:`HouseholdCatalogue.unreadable` lists them so the rules can be widened
deliberately.

Decisions: A3, C3, Q20, deferred by D-C.
"""

import re
from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple

from utspclient.helpers.lpgdata import Households
from utspclient.helpers.lpgpythonbindings import JsonReference


@dataclass(frozen=True)
class HouseholdComposition:
    """How many adults, how many children, and how many of the adults go out to work.

    This is the only shape both sides of the match are reduced to: the inventory's resident lists
    on one side and a catalogue household's name on the other. Keeping it to three numbers is what
    makes "nearest" definable at all.

    Args:
        adults: People of adult age living in the dwelling.
        children: People counted as children.
        working_adults: How many of the adults are in paid employment. ``None`` when a catalogue
            name says nothing about work, which the matcher treats as the worst possible match on
            that facet rather than as agreement.
    """

    adults: int
    children: int
    working_adults: Optional[int]

    def describe(self) -> str:
        """Return the composition in one readable phrase, for the note and for the map."""
        working = "an unstated number" if self.working_adults is None else str(self.working_adults)
        return f"{self.adults} adult(s), {self.children} child(ren), {working} in work"


class HouseholdNameReading:
    """Reads a composition out of a LoadProfileGenerator household name.

    The catalogue names are prose — ``"CHR44 Family with 2 children, 1 at work, 1 at home"`` — and
    they are the only description of a household the client library ships. The rules below are a
    closed, ordered set of phrase patterns over exactly that prose; they are deliberately small,
    and a name that falls through them yields no adult count so that the entry drops out of the
    choice instead of being guessed at.

    Example::

        HouseholdNameReading.of("CHR44 Family with 2 children, 1 at work, 1 at home")
        # HouseholdComposition(adults=2, children=2, working_adults=1)
    """

    #: The prefixes of the catalogue entries that are dwellings. The LoadProfileGenerator also
    #: ships office and shop profiles (``OR01 Single Person Office``), and a dwelling's occupancy
    #: must never be replaced by one of those, however well its person count matches.
    DWELLING_PREFIXES: ClassVar[Tuple[str, ...]] = ("CHR", "CHS")

    #: Patterns stating the adult count outright. The first that matches wins and no phrase rule
    #: is applied afterwards, because such a name has already said the number.
    EXPLICIT_ADULT_PATTERNS: ClassVar[Tuple[str, ...]] = (
        r"(\d+)\s+adults\b",
        r"(\d+)\s+parents\b",
    )

    #: Phrase -> how many adults it contributes. Every match of every pattern is counted, so
    #: ``"working couple, 2 children, 2 seniors"`` comes to four adults. ``None`` as the count
    #: means "take the number the pattern captured".
    ADULT_PHRASES: ClassVar[Tuple[Tuple[str, Optional[int]], ...]] = (
        (r"\bcouple\b", 2),
        (r"\bfamily\b", 2),
        (r"\bsingle\b", 1),
        (r"\bstudent\b", 1),
        (r"(\d+)\s+seniors\b", None),
        (r"\bsenior\b(?!s)", 1),
    )

    #: The pattern giving a child count. A catalogue name always writes the number out, so there
    #: is no rule turning a bare "child" into one.
    CHILD_PATTERN: ClassVar[str] = r"(\d+)\s+(?:child|children|kid|kids|toddler|toddlers)\b"

    #: Ordered rules for how many adults go out to work. ``"all"`` means every adult of the
    #: household, an integer means that many, and the first rule that matches wins — which is why
    #: the phrases denying work come before the ones asserting it, as ``"without work"`` contains
    #: ``"work"``.
    EMPLOYMENT_RULES: ClassVar[Tuple[Tuple[str, object], ...]] = (
        (r"\bboth\s+(?:at work|with work|working|employed)\b", "all"),
        (r"\ball\s+(?:at work|with work|working|employed)\b", "all"),
        (r"\bwithout work\b", 0),
        (r"\bno work\b", 0),
        (r"\bjobless\b", 0),
        (r"\bunemployed\b", 0),
        (r"\bretired\b", 0),
        (r"\bover 65\b", 0),
        (r"\b1 at work\b", 1),
        (r"\bone at work\b", 1),
        (r"\b1 working\b", 1),
        (r"\bone work home\b", 1),
        (r"\bhusband at work\b", 1),
        (r"\bman at work\b", 1),
        (r"\bdad employed\b", 1),
        (r"\bwith work\b", "all"),
        (r"\bwith homehelp\b", "all"),
        (r"\bshift\s?worker\b", "all"),
        (r"\bemployed\b", "all"),
    )

    @classmethod
    def is_dwelling(cls, name: str) -> bool:
        """Return whether a catalogue name belongs to a dwelling rather than to a workplace.

        Args:
            name: The reference's ``Name``, e.g. ``"CHR01 Couple both at Work"``.

        Returns:
            ``True`` when the name starts with one of :attr:`DWELLING_PREFIXES`.
        """
        return name.startswith(cls.DWELLING_PREFIXES)

    @classmethod
    def of(cls, name: str) -> Optional[HouseholdComposition]:
        """Read the composition a catalogue name describes.

        Args:
            name: The reference's ``Name``.

        Returns:
            The composition, or ``None`` when no rule yields an adult count — which is the signal
            that this entry must not take part in the choice.
        """
        text = name.lower()
        adults = cls._adults(text)
        if adults is None:
            return None
        return HouseholdComposition(
            adults=adults,
            children=cls._children(text),
            working_adults=cls._working_adults(text, adults),
        )

    @classmethod
    def _adults(cls, text: str) -> Optional[int]:
        """Return the adult count a lower-cased name states, or ``None`` when it states none."""
        for pattern in cls.EXPLICIT_ADULT_PATTERNS:
            match = re.search(pattern, text)
            if match is not None:
                return int(match.group(1))
        total = 0
        for pattern, count in cls.ADULT_PHRASES:
            for match in re.finditer(pattern, text):
                total += int(match.group(1)) if count is None else count
        return total or None

    @classmethod
    def _children(cls, text: str) -> int:
        """Return the child count a lower-cased name states; zero when it names none."""
        match = re.search(cls.CHILD_PATTERN, text)
        return int(match.group(1)) if match is not None else 0

    @classmethod
    def _working_adults(cls, text: str, adults: int) -> Optional[int]:
        """Return how many adults a lower-cased name puts in work, or ``None`` when it is silent."""
        for pattern, outcome in cls.EMPLOYMENT_RULES:
            if re.search(pattern, text) is not None:
                return adults if outcome == "all" else int(str(outcome))
        return None


@dataclass(frozen=True)
class CatalogueHousehold:
    """One LoadProfileGenerator household that the matcher may choose.

    Args:
        attribute: The attribute name it carries on ``lpgdata.Households``, e.g.
            ``"CHR01_Couple_both_at_Work"``. It is the stable identifier a note can quote.
        reference: The reference itself, which is what the occupancy constructor takes.
        composition: What :class:`HouseholdNameReading` read out of its name.
    """

    attribute: str
    reference: JsonReference
    composition: HouseholdComposition

    @property
    def name(self) -> str:
        """Return the catalogue name, e.g. ``"CHR01 Couple both at Work"``."""
        return str(self.reference.Name)


class HouseholdCatalogue:
    """The readable dwelling households of the LoadProfileGenerator, in a stable order.

    Built once from :class:`utspclient.helpers.lpgdata.Households` by walking its attributes, so a
    client-library release that adds or removes a household changes what this returns without a
    change here. The order is the attribute name's, which makes every tie in the match resolve the
    same way on every machine (requirement R10).
    """

    @classmethod
    def entries(cls) -> Tuple[CatalogueHousehold, ...]:
        """Return every dwelling household whose name yields a composition, sorted by attribute.

        Returns:
            The choosable households. Workplace profiles and names no rule reads are not among
            them; :meth:`unreadable` lists the latter.
        """
        found: List[CatalogueHousehold] = []
        for attribute, reference in cls._references():
            composition = HouseholdNameReading.of(str(reference.Name))
            if composition is not None:
                found.append(
                    CatalogueHousehold(attribute=attribute, reference=reference, composition=composition)
                )
        return tuple(sorted(found, key=lambda entry: entry.attribute))

    @classmethod
    def unreadable(cls) -> Tuple[str, ...]:
        """Return the names of the dwelling households no phrase rule could read, sorted.

        Kept as a query rather than as a warning because it is a fact about the rules, not about
        one request: a test prints it so that widening :class:`HouseholdNameReading` is a decision
        somebody takes rather than something that happens by accident.
        """
        names = [
            str(reference.Name)
            for _, reference in cls._references()
            if HouseholdNameReading.of(str(reference.Name)) is None
        ]
        return tuple(sorted(names))

    @classmethod
    def _references(cls) -> Tuple[Tuple[str, JsonReference], ...]:
        """Return every dwelling reference of the catalogue with its attribute name, sorted."""
        found = [
            (attribute, value)
            for attribute, value in vars(Households).items()
            if isinstance(value, JsonReference)
            and value.Name is not None
            and HouseholdNameReading.is_dwelling(str(value.Name))
        ]
        return tuple(sorted(found, key=lambda item: item[0]))


@dataclass(frozen=True)
class HouseholdMatch:
    """The occupancy a calculation would simulate, and how faithful it is.

    Args:
        household: The LoadProfileGenerator household reference the occupancy constructor takes.
        note: One sentence saying what matched exactly and what had to be relaxed. It would be
            the note of the ``approximated`` report line, so a reader learns that three
            residents became a catalogue couple.
        requested: How many residents the request stated.
        matched: The composition of the household that was chosen.
    """

    household: JsonReference
    note: str
    requested: int
    matched: HouseholdComposition

    def is_exact(self) -> bool:
        """Return whether the chosen household has exactly as many people as the request stated."""
        return self.requested == self.matched.adults + self.matched.children


class HouseholdMatcher:
    """Picks the catalogue household nearest to the dwelling's residents (criterion A3).

    The calculation request carries one fact about the people who live in the dwelling: how many
    of them there are. "Nearest" is therefore the smallest difference in the number of people,
    with ties falling to the alphabetically first attribute name so that two runs of the same
    request always choose the same household.

    Nothing calls it in the MVP (decision D-C); it is the seam the LoadProfileGenerator path
    plugs back into, and ``tests/test_renovisor_occupancy.py`` keeps it working.

    Example::

        HouseholdMatcher().match(4).household.Name
    """

    def match(self, number_of_residents: int) -> HouseholdMatch:
        """Return the catalogue household nearest to a number of residents.

        Args:
            number_of_residents: How many people live in the dwelling.

        Returns:
            The match, with the note a report line would carry.

        Raises:
            ValueError: When no catalogue name could be read at all, which would mean the
                LoadProfileGenerator client library has changed its naming beyond every rule.
        """
        entries = HouseholdCatalogue.entries()
        if not entries:
            raise ValueError(
                "no LoadProfileGenerator household name could be read; the phrase rules of "
                "HouseholdNameReading need widening"
            )
        chosen = min(
            entries,
            key=lambda entry: (
                abs(entry.composition.adults + entry.composition.children - number_of_residents),
                entry.attribute,
            ),
        )
        people = chosen.composition.adults + chosen.composition.children
        if people == number_of_residents:
            note = f"{number_of_residents} resident(s) matched exactly by {chosen.name}."
        else:
            note = (
                f"{number_of_residents} resident(s) became {chosen.name}, which the catalogue "
                f"describes as {chosen.composition.describe()}."
            )
        return HouseholdMatch(
            household=chosen.reference,
            note=note,
            requested=number_of_residents,
            matched=chosen.composition,
        )
