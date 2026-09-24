"""Which TABULA archetype row a dwelling is simulated as, for every country the table covers.

The ``Building`` component is parameterised by a TABULA building code, and a calculation request
does not carry one: it carries a country, a kind of dwelling and a construction year. This module
is the whole derivation, generalised from v1's Irish-only lookup to every country whose codes
follow the generic-example grammar.

The reference dataset is the *IEE TABULA + EPISCOPE* building-typology project (www.episcope.eu),
whose workbook HiSim ships in processed form at
``hisim/inputs/housing/data_processed/episcope-tabula.csv``. It is read exactly as
``hisim/components/building/information.py`` reads it (``sep=";"``, ``decimal=","``,
``encoding="cp1252"``), so the two cannot disagree about a decimal comma or an umlaut.

A selectable code is ``<CC>.N.<TYPE>.<NN>.Gen.ReEx.001.<VVV>``. Only the generic examples are
indexed; special sub-typologies such as ``IE.N.SFH.01.325SB…`` are deliberately excluded. The
variant is **always** ``001``, the existing state: under rule 5 of the contract every element's
U-value travels in the request and overrides the row's, so the refurbishment variants carry no
information any more.

Two things can go wrong and each has one answer. A country with no ``.N.`` typology -- ``ES``
today -- is a data gap and is refused with ``location.country.unsupported``. A construction year
outside every band of its typology is clamped to the nearest band, and the note says which band
was wanted. The former third answer -- rows whose door or window area is zero crash the
``Building`` component and are skipped for a neighbouring band -- is gone: the ``Building``
guards every zero envelope area now (hisim-4g9.1), so every generic-example row is selectable.
"""

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import ClassVar, Dict, FrozenSet, List, Optional, Tuple

from hisim import utils
from hisim.renovisor.vocabulary import BuildingType


class TabulaUnresolvable(Exception):
    """No TABULA row can be chosen for a dwelling, so the request cannot be simulated.

    Raised for a country and typology the table does not carry, and for a requested code that is
    not in the table or is not usable. The caller turns it into a ``tabula.unresolvable`` or
    ``location.country.unsupported`` problem; it never reaches an exit code of its own.
    """


@dataclass(frozen=True)
class AgeBand:
    """One construction-year class of one (country, typology) pair, in its ``001`` variant.

    Args:
        band: The two-digit band number as it appears in the code, e.g. ``"05"``.
        year_start: The first construction year the band covers.
        year_end: The last one; ``9999`` for the open-ended newest band.
    """

    band: str
    year_start: int
    year_end: int

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
        code: The full ``<CC>.N.<TYPE>.<NN>.Gen.ReEx.001.001`` code.
        typology: The typology the code carries, for the note.
        notes: One sentence per approximation that was made; empty when the code is the exact
            band of the exact typology.
    """

    code: str
    typology: str
    notes: Tuple[str, ...]

    def is_approximated(self) -> bool:
        """Return whether anything about the selection was approximated."""
        return bool(self.notes)


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
    probe of the capability document asks it the same question. Only the ``001`` variant is
    indexed: the translator never selects another one.
    """

    #: Only the generic examples; the special sub-typologies are excluded on purpose.
    CODE_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(
        r"^(?P<country>[A-Z]{2})\.N\.(?P<typology>[A-Z]+)\.(?P<band>\d{2})\.Gen\.ReEx\.001\.(?P<variant>\d{3})$"
    )

    #: The refurbishment variant the translator always selects (F-spec §5.3 step 3).
    VARIANT: ClassVar[str] = "001"

    #: How the processed table is encoded, as ``building/information.py`` reads it.
    ENCODING: ClassVar[str] = "cp1252"

    #: Its column separator and decimal mark.
    DELIMITER: ClassVar[str] = ";"
    DECIMAL: ClassVar[str] = ","

    #: The columns the selection reads.
    CODE_COLUMN: ClassVar[str] = "Code_BuildingVariant"
    YEAR_START_COLUMN: ClassVar[str] = "Year1_Building"
    YEAR_END_COLUMN: ClassVar[str] = "Year2_Building"

    @classmethod
    @lru_cache(maxsize=1)
    def bands(cls) -> Dict[Tuple[str, str], Tuple[AgeBand, ...]]:
        """Return the age bands of every (country, typology) pair, sorted by band number."""
        collected: Dict[Tuple[str, str], Dict[str, AgeBand]] = {}
        for match, row in cls._rows():
            if match.group("variant") != cls.VARIANT:
                continue
            try:
                year_start = int(row[cls.YEAR_START_COLUMN])
                year_end = int(row[cls.YEAR_END_COLUMN])
            except (KeyError, TypeError, ValueError):
                continue
            key = (match.group("country"), match.group("typology"))
            collected.setdefault(key, {})[match.group("band")] = AgeBand(
                band=match.group("band"),
                year_start=year_start,
                year_end=year_end,
            )
        return {
            key: tuple(sorted(bands.values(), key=lambda band: band.band))
            for key, bands in collected.items()
        }

    @classmethod
    def countries(cls) -> FrozenSet[str]:
        """Return every country code that has generic-example rows in the table."""
        return frozenset(country for country, _ in cls.bands())

    @classmethod
    def typologies(cls, country: str) -> FrozenSet[str]:
        """Return the typologies one country carries."""
        return frozenset(typology for indexed, typology in cls.bands() if indexed == country)

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

    Three inputs decide it -- the country, the kind of dwelling and the construction year. Every
    generic-example row of the table is selectable: the ``Building`` component guards every zero
    envelope area (hisim-4g9.1), so no row can crash it any more and no row is skipped.
    """

    @classmethod
    def select(
        cls,
        country: str,
        building_type: BuildingType,
        construction_year: int,
        requested_code: Optional[str] = None,
    ) -> BuildingCode:
        """Return the code for one dwelling.

        Args:
            country: The ISO country code of ``location.country``.
            building_type: The kind of dwelling the request states.
            construction_year: The year the age band is chosen by.
            requested_code: The expert override ``building.tabula_building_code``; when given,
                the derivation is skipped and only the row's existence is checked.

        Returns:
            The selected code with its notes and the row's own reference temperature.

        Raises:
            TabulaUnresolvable: When the country and typology have no rows at all, or when a
                requested code is not in the table.
        """
        if requested_code is not None:
            return cls._requested(requested_code)
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
        code = f"{country}.N.{typology}.{band.band}.Gen.ReEx.{TabulaIndex.VARIANT}.001"
        return BuildingCode(
            code=code,
            typology=typology,
            notes=tuple(notes),
        )

    @classmethod
    def _requested(cls, code: str) -> BuildingCode:
        """Return the expert-supplied code, checking only that it exists in the table."""
        match = TabulaIndex.CODE_PATTERN.match(code)
        if match is None:
            raise TabulaUnresolvable(
                f"'{code}' is not a generic-example row of the processed TABULA table"
            )
        country, typology, wanted = match.group("country"), match.group("typology"), match.group("band")
        band = next(
            (entry for entry in TabulaIndex.bands().get((country, typology), ()) if entry.band == wanted),
            None,
        )
        if band is None:
            raise TabulaUnresolvable(
                f"'{code}' is not a generic-example row of the processed TABULA table"
            )
        return BuildingCode(
            code=code,
            typology=typology,
            notes=(),
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
