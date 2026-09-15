"""Choosing the LoadProfileGenerator household that comes closest to the dwelling's residents.

The home inventory describes the people who live in the dwelling as three plain facts — how many
there are, which of them are adults and which are children, and what each of them does for a
living. HiSim's occupancy component does not take those facts: it takes one reference out of the
LoadProfileGenerator's catalogue of household types, each of which is a fixed composition with a
fixed daily rhythm. This module is the bridge, and it is the reason acceptance criterion A3 exists
at all: there is no exact translation, only a nearest neighbour and an honest note saying what was
relaxed::

    match = HouseholdMatcher().match(inventory)
    match.household.Name   # 'CHR01 Couple both at Work'
    match.note             # '2 adults, 0 children and 2 of them working matched exactly.'

The catalogue is read at import from :mod:`utspclient.helpers.lpgdata`, never transcribed here: a
LoadProfileGenerator release that adds a household must widen the choice rather than need a code
change. What the catalogue does *not* carry is the composition of each household as data — a
``JsonReference`` holds a name and a GUID and nothing else — so the composition is read out of the
name by the small, closed set of phrase rules in :class:`HouseholdNameReading`. A name no rule can
read contributes no adult count, and such an entry is left out of the choice rather than guessed
at; :meth:`HouseholdCatalogue.unreadable` lists them so the rules can be widened deliberately.

Decisions: A3, C3, Q20.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Sequence, Tuple, Type, TypeVar

from utspclient.helpers.lpgdata import Households, TravelRouteSets
from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim.renovisor.inventory import Inventory
from hisim.renovisor.reasons import ReasonCode, ValidationError

#: One member of an occupancy vocabulary -- :class:`ResidentType` or :class:`EmploymentStatus`
#: -- so that reading a list of them gives back the members and not a bag of enums.
OccupancyVocabularyMember = TypeVar("OccupancyVocabularyMember", "ResidentType", "EmploymentStatus")


class ResidentType(str, Enum):
    """What kind of person one entry of ``occupancy_config.residents_type`` is.

    The contract states the two values in prose ("Possible values: adult, kid") rather than as an
    enum, so they are spelled once here and read case-insensitively. A resident that is not a kid
    counts as an adult, because the LoadProfileGenerator's compositions are described in adults
    and children and nothing in between.
    """

    ADULT = "ADULT"
    KID = "KID"


class EmploymentStatus(str, Enum):
    """What one entry of ``occupancy_config.residents_employment_status`` says a resident does.

    Only the distinction "goes out to work" versus "does not" survives the translation, because
    that is the only distinction the LoadProfileGenerator's household names make. The five values
    are kept apart here anyway so that the note can say which of them were collapsed.
    """

    FULL_TIME = "FULL_TIME"
    HALF_TIME = "HALF_TIME"
    UNEMPLOYED = "UNEMPLOYED"
    EDUCATION = "EDUCATION"
    RETIRED = "RETIRED"

    def is_working(self) -> bool:
        """Return whether a resident of this status leaves the dwelling for paid work on a weekday.

        Returns:
            ``True`` for full-time and part-time employment, ``False`` for the three statuses that
            keep a person at home or in education.
        """
        return self in (EmploymentStatus.FULL_TIME, EmploymentStatus.HALF_TIME)


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


class TravelRouteSetCatalogue:
    """The commuting-distance profiles of the LoadProfileGenerator, looked up by name.

    The contract carries the travel route set as the profile's own display name ("Travel Route Set
    for 10km Commuting Distance"), and the occupancy constructor takes the reference, so the whole
    of the translation is a lookup. It is case- and space-insensitive because the contract's
    examples and the catalogue's names have disagreed about both.
    """

    @classmethod
    def by_name(cls, raw: str, path: str) -> JsonReference:
        """Return the travel route set the inventory names.

        Args:
            raw: The name as the inventory spells it.
            path: The inventory path, for the error message.

        Returns:
            The matching reference.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when no catalogue entry carries that name,
                listing the names that exist.
        """
        wanted = cls._normalise(raw)
        for attribute, reference in cls._references():
            if wanted in (cls._normalise(attribute), cls._normalise(str(reference.Name))):
                return reference
        available = ", ".join(str(reference.Name) for _, reference in cls._references())
        raise ValidationError(
            ReasonCode.SCHEMA_VIOLATION,
            path,
            f"'{raw}' is not a LoadProfileGenerator travel route set; available: {available}",
        )

    @classmethod
    def _references(cls) -> Tuple[Tuple[str, JsonReference], ...]:
        """Return every travel route set with its attribute name, sorted by attribute."""
        found = [
            (attribute, value)
            for attribute, value in vars(TravelRouteSets).items()
            if isinstance(value, JsonReference) and value.Name is not None
        ]
        return tuple(sorted(found, key=lambda item: item[0]))

    @classmethod
    def _normalise(cls, text: str) -> str:
        """Return a name reduced to lower-case letters and digits, so spacing cannot matter."""
        return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class HouseholdMatch:
    """The occupancy the calculation will simulate, and how faithful it is.

    Args:
        household: The LoadProfileGenerator household reference the occupancy constructor takes.
        travel_route_set: The commuting profile the inventory asked for, or ``None`` to leave the
            constructor's own default in place.
        note: One sentence saying what matched exactly and what had to be relaxed. It is the
            ``rule`` of the ``approximated`` report line, so a caller reading the report learns
            that its three residents became a catalogue couple.
        requested: The composition read out of the inventory.
        matched: The composition of the household that was chosen.
    """

    household: JsonReference
    travel_route_set: Optional[JsonReference]
    note: str
    requested: HouseholdComposition
    matched: HouseholdComposition

    def is_exact(self) -> bool:
        """Return whether the chosen household agrees with the request on all three facets."""
        return self.requested == self.matched


class HouseholdMatcher:
    """Picks the catalogue household nearest to the dwelling's residents (acceptance criterion A3).

    "Nearest" is a lexicographic order over three distances, in the order the facets matter for an
    energy simulation: the number of adults first, because it sets the base load and the hot-water
    draw; then the number of children; then how many adults are out of the house on a weekday,
    because that only reshapes the day. Ties fall to the alphabetically first attribute name, so
    two runs of the same request always choose the same household (requirement R10).

    Example::

        HouseholdMatcher().match(inventory).household.Name   # 'CHR27 Family both at work, 2 children'
    """

    #: Inventory paths this matcher reads.
    RESIDENTS_COUNT: ClassVar[str] = "occupancy_config.residents_count"
    RESIDENTS_TYPE: ClassVar[str] = "occupancy_config.residents_type"
    RESIDENTS_EMPLOYMENT: ClassVar[str] = "occupancy_config.residents_employment_status"
    TRAVEL_ROUTE_SET: ClassVar[str] = "occupancy_config.travel_route_set"

    #: What a catalogue household silent about employment is treated as being away from the
    #: request by. It is larger than any real difference in a 66-entry catalogue, so a household
    #: that states its employment always beats one that does not, without ever outranking a better
    #: adult or child count.
    UNSTATED_EMPLOYMENT_DISTANCE: ClassVar[int] = 99

    def match(self, inventory: Inventory) -> HouseholdMatch:
        """Return the household the calculation simulates for this dwelling.

        Args:
            inventory: The home inventory, pre- or post-measure; no measure changes the residents.

        Returns:
            The :class:`HouseholdMatch`, always populated: the catalogue covers every plausible
            composition well enough that there is no refusal here, only a relaxation the note
            names.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when a resident type or employment status is not
                one of the contract's values, or the named travel route set does not exist.
        """
        requested = self._requested(inventory)
        candidates = HouseholdCatalogue.entries()
        best = min(candidates, key=lambda entry: self._distance(requested, entry))
        return HouseholdMatch(
            household=best.reference,
            travel_route_set=self._travel_route_set(inventory),
            note=self._note(requested, best),
            requested=requested,
            matched=best.composition,
        )

    def _requested(self, inventory: Inventory) -> HouseholdComposition:
        """Read the dwelling's own composition out of the inventory.

        The resident lists are authoritative where they are present, because they say who the
        people are; ``residents_count`` fills in for a missing type list and is then read as that
        many adults, which is the only thing a bare count can mean.

        Args:
            inventory: The home inventory.

        Returns:
            The requested composition, with ``working_adults`` always stated.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` for a type or status the contract does not list.
        """
        types = self._members(inventory, self.RESIDENTS_TYPE, ResidentType)
        statuses = self._members(inventory, self.RESIDENTS_EMPLOYMENT, EmploymentStatus)
        count = inventory.get(self.RESIDENTS_COUNT)
        if types:
            adults = sum(1 for item in types if item is ResidentType.ADULT)
            children = sum(1 for item in types if item is ResidentType.KID)
        else:
            adults = int(count) if isinstance(count, int) else 0
            children = 0
        working = sum(1 for item in statuses if item.is_working())
        return HouseholdComposition(adults=adults, children=children, working_adults=min(working, adults))

    @classmethod
    def _members(
        cls,
        inventory: Inventory,
        path: str,
        vocabulary: Type[OccupancyVocabularyMember],
    ) -> Tuple[OccupancyVocabularyMember, ...]:
        """Read one list-valued occupancy field into its vocabulary members.

        Args:
            inventory: The home inventory.
            path: The dotted path of the list.
            vocabulary: :class:`ResidentType` or :class:`EmploymentStatus`.

        Returns:
            The members, in the inventory's order; empty when the field is absent or null.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` naming the offending element and the values the
                contract allows.
        """
        raw = inventory.get(path)
        if not isinstance(raw, list):
            return ()
        members: List[OccupancyVocabularyMember] = []
        for index, item in enumerate(raw):
            member = vocabulary.__members__.get(str(item).strip().upper())
            if member is None:
                allowed = ", ".join(str(candidate.value) for candidate in vocabulary)
                raise ValidationError(
                    ReasonCode.SCHEMA_VIOLATION,
                    f"{path}[{index}]",
                    f"'{item}' is not one of {allowed}",
                )
            members.append(member)
        return tuple(members)

    def _travel_route_set(self, inventory: Inventory) -> Optional[JsonReference]:
        """Return the commuting profile the inventory names, or ``None`` when it names none."""
        raw = inventory.get(self.TRAVEL_ROUTE_SET)
        if raw is None or str(raw).strip() == "":
            return None
        return TravelRouteSetCatalogue.by_name(str(raw), self.TRAVEL_ROUTE_SET)

    def _distance(
        self, requested: HouseholdComposition, candidate: CatalogueHousehold
    ) -> Tuple[int, int, int, str]:
        """Return the sort key ranking one candidate against the request.

        Args:
            requested: The dwelling's own composition.
            candidate: One catalogue household.

        Returns:
            ``(adult distance, child distance, employment distance, attribute name)`` — compared
            element by element, which is what makes the three facets a strict priority order and
            the attribute name a deterministic tie-break.
        """
        composition = candidate.composition
        if composition.working_adults is None:
            employment = self.UNSTATED_EMPLOYMENT_DISTANCE
        else:
            employment = abs((requested.working_adults or 0) - composition.working_adults)
        return (
            abs(requested.adults - composition.adults),
            abs(requested.children - composition.children),
            employment,
            candidate.attribute,
        )

    def _note(self, requested: HouseholdComposition, chosen: CatalogueHousehold) -> str:
        """Return the sentence the report carries: what matched and what was relaxed.

        Args:
            requested: The dwelling's own composition.
            chosen: The household that won.

        Returns:
            One sentence naming the catalogue household and every facet that had to give way.
        """
        relaxed = self._relaxations(requested, chosen.composition)
        head = (
            f"the dwelling's {requested.describe()} were simulated as the LoadProfileGenerator "
            f"household '{chosen.name}' ({chosen.composition.describe()})"
        )
        if not relaxed:
            return f"{head}; adults, children and employment all matched exactly"
        return f"{head}; relaxed: {', '.join(relaxed)}"

    @classmethod
    def _relaxations(
        cls, requested: HouseholdComposition, matched: HouseholdComposition
    ) -> Sequence[str]:
        """Return one phrase per facet on which the chosen household differs from the request."""
        differences: List[str] = []
        if requested.adults != matched.adults:
            differences.append(f"{requested.adults} adult(s) became {matched.adults}")
        if requested.children != matched.children:
            differences.append(f"{requested.children} child(ren) became {matched.children}")
        if matched.working_adults is None:
            differences.append("the catalogue household's name says nothing about employment")
        elif (requested.working_adults or 0) != matched.working_adults:
            differences.append(
                f"{requested.working_adults} adult(s) in work became {matched.working_adults}"
            )
        return differences


class HouseholdMatchReport:
    """How a match is written into the translation report (requirement R7).

    The four occupancy leaves all reach one constructor argument, so they are reported together
    rather than one by one: the fate of ``residents_count`` on its own is not a statement anybody
    can act on, while "these four became this catalogue household" is.
    """

    #: The four inventory paths one match accounts for, in contract order.
    PATHS: ClassVar[Tuple[str, ...]] = (
        HouseholdMatcher.RESIDENTS_COUNT,
        HouseholdMatcher.RESIDENTS_TYPE,
        HouseholdMatcher.RESIDENTS_EMPLOYMENT,
    )

    #: The ``rule`` every one of those lines carries, naming the decision behind the match.
    RULE: ClassVar[str] = "A3: nearest household of the LoadProfileGenerator catalogue"

    @classmethod
    def lines(cls, match: HouseholdMatch) -> Dict[str, str]:
        """Return the note to record for each of the occupancy paths.

        Args:
            match: The match to report.

        Returns:
            Path -> note. Every line is an ``approximated`` one, because no catalogue household is
            the dwelling's residents even when all three numbers agree.
        """
        return {path: match.note for path in cls.PATHS}
