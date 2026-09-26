"""Which TABULA archetype row a dwelling is simulated as, for every country the table covers.

The ``Building`` component is parameterised by a TABULA building code, and a calculation request
does not carry one: it carries a country, a kind of dwelling, a construction year and how far the
envelope has been refurbished. This module is the whole derivation, generalised from v1's
Irish-only lookup to every country whose codes follow the generic-example grammar.

The reference dataset is the *IEE TABULA + EPISCOPE* building-typology project (www.episcope.eu),
whose workbook HiSim ships in processed form at
``hisim/inputs/housing/data_processed/episcope-tabula.csv``. It is read exactly as
``hisim/components/building/information.py`` reads it (``sep=";"``, ``decimal=","``,
``encoding="cp1252"``), so the two cannot disagree about a decimal comma or an umlaut.

A selectable code is ``<CC>.N.<TYPE>.<NN>.Gen.ReEx.001.<VVV>``. Only the generic examples are
indexed; special sub-typologies such as ``IE.N.SFH.01.325SB…`` are deliberately excluded. ``VVV``
is the refurbishment variant, and ``building.retrofit_status`` chooses it (§5.3 step 3 of the
calculation-request specification): ``unrenovated`` -> ``001``, ``usual_refurb`` -> ``002``,
``advanced_refurb`` -> ``003``, absent -> ``001``. The variant matters although a request may
state every U-value itself: an element it leaves out takes the variant row's U-value, and the
row's air infiltration and thermal-bridge surcharge -- which no request field sets -- apply in
every case, so a refurbished variant is a tighter house even when every U-value is stated. Until
2026-09-26 the variant was always ``001`` and refurbishment was expressed by U-values alone;
that made a household choose between describing every element and being simulated as
unrenovated.

Every variant of the table is indexed, per band, but a band is selectable only through its
``001`` row, the existing state every other variant falls back to. A band without the wanted
variant -- TABULA has no ``002`` for the newest Irish, Dutch and Belgian bands -- takes ``001`` and the
note names the missing variant. A band with no ``001`` row at all (``IE.N.TH.10``) is not a band
of the index, and an expert code naming one of its rows is refused like a code the table
lacks.

Two things can go wrong and each has one answer. A country with no ``.N.`` typology -- ``ES``
today -- is a data gap and is refused with ``location.country.unsupported``. A construction year
outside every band of its typology is clamped to the nearest band, and the note says which band
was wanted. The former third answer -- rows whose door or window area is zero crash the
``Building`` component and are skipped for a neighbouring band -- is gone: the ``Building``
guards a zero door or window area now (hisim-4g9.1) and gives a row without a door area
TABULA's estimated door, so every generic-example row is selectable. Only the door and the
window are guarded: a zero floor, wall or roof reference area still divides by zero
(hisim-4g9.16), and no generic-example row has one. A missing door area is the only zero area
those rows have.

What the selected row makes of the envelope is not re-derived here: :class:`ArchetypeEnvelope`
asks the ``Building``'s own ``BuildingInformation`` for it, so the U-value the report names for an
element the request leaves out is, bit for bit, the one the simulation uses.
"""

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Tuple

from hisim import utils
from hisim.renovisor.vocabulary import BuildingType, RetrofitStatus, ThermalElement

if TYPE_CHECKING:  # pragma: no cover - the request module imports this one lazily
    from hisim.renovisor.request import Request


class TabulaUnresolvable(Exception):
    """No TABULA row can be chosen for a dwelling, so the request cannot be simulated.

    Raised for a country and typology the table does not carry, and for a requested code that is
    not in the table. The caller turns it into a ``tabula.unresolvable`` or
    ``location.country.unsupported`` problem; it never reaches an exit code of its own.
    """


class TabulaVariantConflict(Exception):
    """A requested code's variant and a stated ``retrofit_status`` name different variants.

    The request check turns it into a ``tabula.variant_conflict`` problem on
    ``house.building.retrofit_status`` (§5.3); the translator never sees such a request.
    """


@dataclass(frozen=True)
class AgeBand:
    """One construction-year class of one (country, typology) pair, with the variants it has.

    Args:
        band: The two-digit band number as it appears in the code, e.g. ``"05"``.
        year_start: The first construction year the band covers.
        year_end: The last one; ``9999`` for the open-ended newest band.
        variants: Every refurbishment variant the table carries for the band, ``001`` always
            among them (a band is indexed through its ``001`` row).
    """

    band: str
    year_start: int
    year_end: int
    variants: FrozenSet[str] = frozenset({"001"})

    def covers(self, year: int) -> bool:
        """Return whether a construction year falls inside this band's range."""
        return self.year_start <= year <= self.year_end

    def distance_to(self, year: int) -> int:
        """Return how many years separate *year* from this band's range; zero when inside."""
        if year < self.year_start:
            return self.year_start - year
        if year > self.year_end:
            return year - self.year_end
        return 0

    def describe(self) -> str:
        """Return the band and its year range, for a report note."""
        return f"{self.band} ({self.year_start}-{self.year_end})"


@dataclass(frozen=True)
class BuildingCode:
    """One selected TABULA code, with everything the mapping report has to say about it.

    Args:
        code: The full ``<CC>.N.<TYPE>.<NN>.Gen.ReEx.001.<VVV>`` code.
        typology: The typology the code carries, for the note.
        notes: One sentence per approximation of the typology or the band; empty when the code
            is the exact band of the exact typology.
        variant: The code's refurbishment variant, its last three digits.
        variant_note: The sentence naming a variant the band lacks, which the selection
            replaced by ``001``; ``None`` when the wanted variant was used.
    """

    code: str
    typology: str
    notes: Tuple[str, ...]
    variant: str = "001"
    variant_note: Optional[str] = None

    def is_approximated(self) -> bool:
        """Return whether the typology or the band was approximated; the variant has its own note."""
        return bool(self.notes)

    @property
    def retrofit_status(self) -> Optional[RetrofitStatus]:
        """Return the status whose variant the code carries, or ``None`` for a variant none selects."""
        return RetrofitStatus.of_variant(self.variant)


class TabulaTypology:
    """Which TABULA typology each kind of dwelling is simulated as.

    Three of the six answers a homeowner can give have no typology of their own and are mapped
    onto the nearest one, which is what makes them ``approximated`` in the report: a bungalow and
    an "other" building are single-family houses, and a semi-detached house is simulated as a
    terraced one because TABULA has no semi-detached typology.
    """

    #: The kind of dwelling -> (typology, whether the mapping is an approximation).
    BY_BUILDING_TYPE: ClassVar[Dict[BuildingType, Tuple[str, bool]]] = {
        BuildingType.DETACHED_SFH: ("SFH", False),
        BuildingType.SEMI_DETACHED_SFH: ("TH", True),
        BuildingType.TERRACED_SFH: ("TH", False),
        BuildingType.BUNGALOW: ("SFH", True),
        BuildingType.APARTMENT: ("AB", False),
        BuildingType.OTHER: ("SFH", True),
    }

    @classmethod
    def of(cls, building_type: BuildingType) -> Tuple[str, bool]:
        """Return the typology of one kind of dwelling and whether that is an approximation."""
        return cls.BY_BUILDING_TYPE[building_type]


class TabulaIndex:
    """The ``(country, typology) -> age bands`` index over the processed TABULA table.

    It is built once per process and cached, because the CSV has some thousands of rows and every
    probe of the capability document asks it the same question. Every variant of every generic
    example is indexed; a band's year range is its ``001`` row's, and a band without a ``001`` row
    is left out, because ``001`` is what every other variant falls back to.
    """

    #: Only the generic examples; the special sub-typologies are excluded on purpose.
    CODE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(
        r"^(?P<country>[A-Z]{2})\.N\.(?P<typology>[A-Z]+)\.(?P<band>\d{2})\.Gen\.ReEx\.001\.(?P<variant>\d{3})$"
    )

    #: The existing state: the variant a band is indexed by and every missing variant falls back to.
    EXISTING_STATE: ClassVar[str] = "001"

    #: How the processed table is encoded, as ``building/information.py`` reads it.
    ENCODING: ClassVar[str] = "cp1252"

    #: Its column separator.
    DELIMITER: ClassVar[str] = ";"

    #: The columns the selection reads.
    CODE_COLUMN: ClassVar[str] = "Code_BuildingVariant"
    YEAR_START_COLUMN: ClassVar[str] = "Year1_Building"
    YEAR_END_COLUMN: ClassVar[str] = "Year2_Building"

    @classmethod
    @lru_cache(maxsize=1)
    def bands(cls) -> Dict[Tuple[str, str], Tuple[AgeBand, ...]]:
        """Return the age bands of every (country, typology) pair, sorted by band number."""
        years: Dict[Tuple[str, str], Dict[str, Tuple[int, int]]] = {}
        variants: Dict[Tuple[str, str, str], set] = {}
        for match, row in cls._rows():
            key = (match.group("country"), match.group("typology"))
            band = match.group("band")
            variants.setdefault((*key, band), set()).add(match.group("variant"))
            if match.group("variant") != cls.EXISTING_STATE or band in years.get(key, {}):
                continue
            try:
                year_start = int(row[cls.YEAR_START_COLUMN])
                year_end = int(row[cls.YEAR_END_COLUMN])
            except (KeyError, TypeError, ValueError):
                continue
            years.setdefault(key, {})[band] = (year_start, year_end)
        return {
            key: tuple(
                AgeBand(
                    band=band,
                    year_start=year_start,
                    year_end=year_end,
                    variants=frozenset(variants[(*key, band)]),
                )
                for band, (year_start, year_end) in sorted(by_band.items())
            )
            for key, by_band in years.items()
        }

    @classmethod
    @lru_cache(maxsize=1)
    def codes(cls) -> FrozenSet[str]:
        """Return every generic-example code of the table, in every refurbishment variant.

        An expert override is checked against this set, whole code by whole code, so a band that
        exists does not vouch for a variant it lacks.
        """
        return frozenset(match.group(0) for match, _ in cls._rows())

    @classmethod
    def countries(cls) -> FrozenSet[str]:
        """Return every country code that has generic-example rows in the table."""
        return frozenset(country for country, _ in cls.bands())

    @classmethod
    def typologies(cls, country: str) -> FrozenSet[str]:
        """Return the typologies one country carries."""
        return frozenset(typology for indexed, typology in cls.bands() if indexed == country)

    @classmethod
    def variant_of(cls, code: str) -> str:
        """Return a generic-example code's refurbishment variant, its last three digits.

        Raises:
            TabulaUnresolvable: When the code is not a generic-example code.
        """
        match = cls.CODE_PATTERN.match(code)
        if match is None:
            raise TabulaUnresolvable(f"'{code}' is not a generic-example row of the processed TABULA table")
        return match.group("variant")

    @classmethod
    def _rows(cls) -> List[Tuple["re.Match[str]", Dict[str, str]]]:
        """Return every generic-example row of the processed table with its parsed code."""
        rows: List[Tuple["re.Match[str]", Dict[str, str]]] = []
        with open(utils.HISIMPATH["housing"], encoding=cls.ENCODING) as handle:
            for row in csv.DictReader(handle, delimiter=cls.DELIMITER):
                match = cls.CODE_PATTERN.match(row.get(cls.CODE_COLUMN, "") or "")
                if match is not None:
                    rows.append((match, row))
        return rows


class BuildingCodeSelector:
    """Picks the TABULA code one dwelling is simulated as, and says what it approximated.

    Four inputs decide it -- the country, the kind of dwelling, the construction year and the
    retrofit status. Every generic-example row of the table is selectable: the one zero envelope
    area those rows have, a missing door area, no longer crashes the ``Building`` component
    (hisim-4g9.1), so no row is skipped.
    """

    @classmethod
    def select(
        cls,
        country: str,
        building_type: BuildingType,
        construction_year: int,
        requested_code: Optional[str] = None,
        retrofit_status: Optional[RetrofitStatus] = None,
    ) -> BuildingCode:
        """Return the code for one dwelling.

        Args:
            country: The ISO country code of ``location.country``.
            building_type: The kind of dwelling the request states.
            construction_year: The year the age band is chosen by.
            requested_code: The expert override ``building.tabula_building_code``; when given,
                the derivation is skipped and only the row's existence is checked.
            retrofit_status: ``building.retrofit_status``, which selects the variant; ``None``
                (absent) is ``unrenovated`` for a derived code and the code's own variant for a
                requested one.

        Returns:
            The selected code with its notes.

        Raises:
            TabulaUnresolvable: When the country and typology have no rows at all, or when a
                requested code is not in the table.
            TabulaVariantConflict: When a requested code's variant is not the one a stated
                retrofit status selects.
        """
        if requested_code is not None:
            return cls._requested(requested_code, retrofit_status)
        typology, approximated_typology = TabulaTypology.of(building_type)
        bands = TabulaIndex.bands().get((country, typology))
        if not bands:
            raise TabulaUnresolvable(
                f"the TABULA table carries no generic-example rows for '{country}.N.{typology}'"
            )
        notes: List[str] = []
        if approximated_typology:
            notes.append(
                f"a {building_type.value} has no TABULA typology of its own and is simulated as "
                f"{typology}"
            )
        band, band_note = cls._band(bands, construction_year, country, typology)
        if band_note is not None:
            notes.append(band_note)
        status = retrofit_status if retrofit_status is not None else RetrofitStatus.UNRENOVATED
        variant, variant_note = cls._variant(band, status, f"{country}.N.{typology}.{band.band}")
        code = f"{country}.N.{typology}.{band.band}.Gen.ReEx.001.{variant}"
        return BuildingCode(
            code=code,
            typology=typology,
            notes=tuple(notes),
            variant=variant,
            variant_note=variant_note,
        )

    @classmethod
    def for_request(cls, request: "Request") -> BuildingCode:
        """Return the code a validated request's house is simulated as."""
        building = request.house.building
        return cls.select(
            country=request.country.value,
            building_type=building.building_type,
            construction_year=building.construction_year,
            requested_code=building.tabula_building_code,
            retrofit_status=building.retrofit_status,
        )

    @classmethod
    def _requested(cls, code: str, retrofit_status: Optional[RetrofitStatus]) -> BuildingCode:
        """Return the expert-supplied code, checking that it exists and agrees with a stated status."""
        match = TabulaIndex.CODE_PATTERN.match(code)
        if match is None or code not in TabulaIndex.codes():
            raise TabulaUnresolvable(
                f"'{code}' is not a generic-example row of the processed TABULA table"
            )
        country, typology, wanted = match.group("country"), match.group("typology"), match.group("band")
        if not any(band.band == wanted for band in TabulaIndex.bands().get((country, typology), ())):
            raise TabulaUnresolvable(
                f"'{code}' belongs to no age band of the index: {country}.N.{typology}.{wanted} has no "
                f"existing-state row {TabulaIndex.EXISTING_STATE}"
            )
        variant = match.group("variant")
        if retrofit_status is not None and retrofit_status.variant != variant:
            raise TabulaVariantConflict(
                f"tabula_building_code '{code}' is variant {variant}, but retrofit_status "
                f"'{retrofit_status.value}' selects variant {retrofit_status.variant}"
            )
        return BuildingCode(
            code=code,
            typology=typology,
            notes=(),
            variant=variant,
        )

    @classmethod
    def _variant(cls, band: AgeBand, status: RetrofitStatus, stem: str) -> Tuple[str, Optional[str]]:
        """Return the variant a status selects in one band, or ``001`` with the note naming the missing one."""
        if status.variant in band.variants:
            return status.variant, None
        return TabulaIndex.EXISTING_STATE, (
            f"the TABULA table has no variant {status.variant} ({status.value}) for {stem}; its "
            f"existing state {TabulaIndex.EXISTING_STATE} is used"
        )

    @classmethod
    def _band(
        cls,
        bands: Tuple[AgeBand, ...],
        construction_year: int,
        country: str,
        typology: str,
    ) -> Tuple[AgeBand, Optional[str]]:
        """Return the band a dwelling belongs to and the note explaining any substitution."""
        wanted = next((band for band in bands if band.covers(construction_year)), None)
        if wanted is not None:
            return wanted, None
        nearest = min(bands, key=lambda band: (band.distance_to(construction_year), band.band))
        return nearest, (
            f"no {country}.N.{typology} age band covers construction year {construction_year}; "
            f"the nearest band {nearest.describe()} is used"
        )


@dataclass(frozen=True)
class ElementDefault:
    """What the ``Building`` makes of one element whose U-value the request leaves out.

    Args:
        u_value_in_watt_per_m2_per_kelvin: The U-value the simulation uses.
        adjustment_factor: The transmission adjustment factor it is paired with, the row's own.
        origin: Where the U-value comes from, in a phrase the report can quote.
    """

    u_value_in_watt_per_m2_per_kelvin: float
    adjustment_factor: float
    origin: str


@dataclass(frozen=True)
class ArchetypeEnvelope:
    """The envelope one TABULA row gives the ``Building``, as the ``Building`` itself computes it.

    Built by one ``BuildingInformation`` over a configuration that states the archetype, the floor
    area and the request's door area and leaves every U-value unset, so each value here is the
    one the simulation will use for an element the request does not describe: the area-weighted
    average of the row's sub-element U-values with the row's adjustment factor, and for a row
    whose door U-value is ``0`` the ``Building``'s estimated door (§3.4).

    Args:
        code: The TABULA code.
        elements: One :class:`ElementDefault` per envelope element.
        air_infiltration_rate_per_hour: The row's ``n_air_infiltration``.
        thermal_bridging_surcharge_in_watt_per_m2_per_kelvin: The surcharge the ``Building`` uses,
            the row's ``delta_U_ThermalBridging`` (0.1 where the row says 0).
    """

    code: str
    elements: Mapping[ThermalElement, ElementDefault]
    air_infiltration_rate_per_hour: float
    thermal_bridging_surcharge_in_watt_per_m2_per_kelvin: float

    #: The outside design temperature the throw-away ``BuildingInformation`` is built with. It only
    #: sizes the maximum heating load, which nothing here reads; the envelope does not depend on it.
    DESIGN_TEMPERATURE_IN_CELSIUS: ClassVar[float] = 0.0

    #: The ``BuildingInformation`` element descriptor of each request element, by attribute name.
    DESCRIPTORS: ClassVar[Dict[ThermalElement, str]] = {
        ThermalElement.ROOF: "ROOF_ELEMENT",
        ThermalElement.FACADE: "WALL_ELEMENT",
        ThermalElement.FLOOR: "FLOOR_ELEMENT",
        ThermalElement.WINDOW: "WINDOW_ELEMENT",
        ThermalElement.DOOR: "DOOR_ELEMENT",
    }

    def u_value(self, element: ThermalElement) -> float:
        """Return the U-value the ``Building`` uses for an element the request leaves out."""
        return self.elements[element].u_value_in_watt_per_m2_per_kelvin

    @classmethod
    def for_request(cls, request: "Request") -> "ArchetypeEnvelope":
        """Return the envelope of the row a validated request's house is simulated as."""
        building = request.house.building
        return cls.of(
            BuildingCodeSelector.for_request(request).code,
            building.absolute_conditioned_floor_area_in_m2,
            building.door.area_in_m2,
        )

    @classmethod
    def of(
        cls,
        code: str,
        absolute_conditioned_floor_area_in_m2: float,
        door_area_in_m2: Optional[float] = None,
    ) -> "ArchetypeEnvelope":
        """Return the envelope of one row, for one floor area and the request's door area.

        Args:
            code: The selected TABULA code.
            absolute_conditioned_floor_area_in_m2: The request's conditioned floor area.
            door_area_in_m2: The request's door area, or ``None``. It is the one area that decides a
                U-value: a row without a door area of its own gets an estimated door U-value only
                when its door has an area. The other areas weigh nothing here -- an element's
                U-value is averaged over the row's own sub-areas -- and are left out, so a window
                area the row cannot place is refused by the run that uses it, not here.

        Returns:
            The envelope, cached per argument tuple: the capability probes ask for the same one
            hundreds of times.
        """
        area = None if door_area_in_m2 is None else float(door_area_in_m2)
        return cls._cached(code, float(absolute_conditioned_floor_area_in_m2), area)

    @classmethod
    @lru_cache(maxsize=256)
    def _cached(
        cls,
        code: str,
        absolute_conditioned_floor_area_in_m2: float,
        door_area_in_m2: Optional[float],
    ) -> "ArchetypeEnvelope":
        """Build the envelope once per argument tuple; see :meth:`of`."""
        # Function-local: the building package pulls pandas and the TABULA frame in with it, and
        # the request checks import this module without needing either.
        from hisim.components.building import (  # pylint: disable=import-outside-toplevel
            BuildingConfig,
            BuildingInformation,
        )

        config = BuildingConfig.for_tabula_code(
            name="Building",
            building_code=code,
            number_of_apartments=1,
            absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        )
        config.door_area_in_m2 = door_area_in_m2
        config.heating_reference_temperature_in_celsius = cls.DESIGN_TEMPERATURE_IN_CELSIUS
        information = BuildingInformation(config)
        elements: Dict[ThermalElement, ElementDefault] = {}
        for element, descriptor_name in cls.DESCRIPTORS.items():
            descriptor = getattr(BuildingInformation, descriptor_name)
            if element is ThermalElement.DOOR:
                origin = information.door_u_value_origin
            else:
                origin = "the row's area-weighted " + " / ".join(descriptor.u_value_columns)
            elements[element] = ElementDefault(
                u_value_in_watt_per_m2_per_kelvin=float(
                    getattr(information, f"{element.value}_u_value_in_watt_per_m2_per_kelvin")
                ),
                adjustment_factor=float(getattr(information, f"{element.value}_adjustment_factor_from_tabula")),
                origin=origin,
            )
        return cls(
            code=code,
            elements=elements,
            air_infiltration_rate_per_hour=information.air_infiltration_rate_per_hour,
            thermal_bridging_surcharge_in_watt_per_m2_per_kelvin=(
                information.thermal_bridging_surcharge_in_watt_per_m2_per_kelvin
            ),
        )
