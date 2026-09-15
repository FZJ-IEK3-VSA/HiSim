"""The only reader of the measure catalogue, and the derivation of the ids the catalogue lacks.

The *catalogue* is ``measures.yaml`` in the vendored contract: the closed list of 33 renovation
measures a RenoVisor request may name, each with its options and the values those options may take.
It is written for people — measures are named ``external insulation``, options ``type of system`` —
while a request has to name them with stable ids that survive translation and re-wording
(challenge C4, decision Q1). This module turns the one into the other::

    catalogue = Catalogue.load()
    spec = catalogue.by_id("EXTERNAL_INSULATION")
    spec.option("material").values         # ('polystyrene_eps_rigid_board', 'extruded_polystyrene_xps')
    spec.option("material").display_values # ('EPS', 'XPS')

Nothing else in HiSim reads ``measures.yaml``: normalisation happens once, here, so a catalogue
revision changes one module.

Two transformations happen on the way. :class:`IdDerivation` slugs the human names into ids
(``external insulation`` → ``EXTERNAL_INSULATION``, ``type of system`` → ``type_of_system``), and
:class:`CatalogueSpellings` maps the catalogue's own enum spellings onto HiSim's, which decision C3
says the catalogue will eventually carry itself. Both are temporary in different ways: the ids
become ``id:`` fields in the catalogue (and then win over derivation), and the spelling table
shrinks to nothing as the catalogue is revised.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
import re

from hisim.renovisor.contract import ContractFiles


class OptionValueType(str, Enum):
    """The kind of value one catalogue option takes.

    ``ENUM`` is a closed list of strings, ``INTEGER`` a whole number (sometimes restricted to a
    listed set, as the glazing-pane counts are), ``BOOLEAN`` a JSON ``true``/``false``.
    """

    ENUM = "ENUM"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"


class AccessLevel(str, Enum):
    """Who is expected to supply an option.

    ``EVERYONE`` options are asked of every homeowner and are present in essentially every
    request; ``EXPERTS`` options are the ones a surveyor fills in, absent from most requests, and
    therefore the ones that need a default with a source (requirement M7).
    """

    EVERYONE = "EVERYONE"
    EXPERTS = "EXPERTS"


class MaterialResolution(str, Enum):
    """Marks a catalogue value that has no counterpart in the insulation-material database.

    Three materials the catalogue offers have no row (``PIR``, thermal laminate drylining board,
    liquid insulation), one is ambiguous between two rows (mineral wool), and windows and doors
    have no rows at all. Decisions Q6, Q7 and Q13 all say the same thing: HiSim does not guess, it
    refuses. :attr:`UNRESOLVED` is the value that carries that through the option reader to the
    registry function, which turns it into ``Refusal(MATERIAL_NOT_IN_DATABASE)``.
    """

    UNRESOLVED = "UNRESOLVED"

    @classmethod
    def is_unresolved(cls, value: str) -> bool:
        """Return whether *value* is the unresolved marker rather than a real material id."""
        return value == cls.UNRESOLVED.value


class IdDerivation:
    """Derives the ids of measures, options and enum values from their catalogue spellings.

    The rule is one substitution and a case: every run of characters that is not a letter or a
    digit becomes a single ``_``, leading and trailing ``_`` are stripped, and the result is
    upper-cased for measure ids and enum values, lower-cased for option ids::

        IdDerivation.upper_id("optimize behaviour for self-consumption of PV")
        # 'OPTIMIZE_BEHAVIOUR_FOR_SELF_CONSUMPTION_OF_PV'
        IdDerivation.lower_id("size in percent of roof area")
        # 'size_in_percent_of_roof_area'

    Why mechanical and not hand-written: the derivation is proposed as the committed truth
    (challenge C4), so the ``id:`` fields the contract PR adds are exactly what this produces. An
    explicit ``id:`` in the catalogue always wins over derivation, which is how a measure whose
    name is re-worded keeps its id.
    """

    #: Every run of characters that is not an ASCII letter or digit becomes one underscore.
    SEPARATOR_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"[^A-Za-z0-9]+")

    @classmethod
    def upper_id(cls, display_name: str) -> str:
        """Return the UPPER_SNAKE id of a measure name or enum value.

        Args:
            display_name: The catalogue's spelling, e.g. ``"basement ceiling insulation"``.

        Returns:
            The slug, e.g. ``"BASEMENT_CEILING_INSULATION"``.
        """
        return cls._slug(display_name).upper()

    @classmethod
    def lower_id(cls, display_name: str) -> str:
        """Return the snake_case id of an option name.

        Args:
            display_name: The catalogue's spelling, e.g. ``"type of system"``.

        Returns:
            The slug, e.g. ``"type_of_system"``.
        """
        return cls._slug(display_name).lower()

    @classmethod
    def _slug(cls, display_name: str) -> str:
        """Return *display_name* with every non-alphanumeric run replaced by one underscore."""
        return cls.SEPARATOR_PATTERN.sub("_", str(display_name)).strip("_")


class CatalogueSpellings:
    """Catalogue value → HiSim wire value, per ``(measure_id, option_id)``.

    Decision C3 says the contract will carry HiSim's spelling of every enum value; today the
    catalogue carries RenoVisor's (``surface_heating``, ``MechanicalExtract``, ``EPS Foam``). This
    table is the one place that knows the difference, so that no second mapping grows elsewhere,
    and it is meant to shrink: as the catalogue adopts a HiSim spelling, its entry disappears. A
    test asserts that no entry maps a value onto itself, which is what keeps the shrinking honest.

    The table does two jobs. For a value HiSim can simulate it *rewrites* the spelling: a material
    name becomes the database's ``asp_id``, a distribution system becomes the HiSim member name.
    For a value HiSim cannot simulate it *marks* the value unresolved and leaves the spelling
    alone — the three materials with no database row, the ambiguous "mineral wool", and every
    glazing-pane count (decisions Q6, Q7, Q13). Leaving the spelling alone matters: an option whose
    unresolvable values were all rewritten to one marker would offer a caller two identical values
    and no way to say which material it meant. :meth:`is_unresolved` is what the registry asks.
    """

    BY_OPTION: ClassVar[Dict[Tuple[str, str], Dict[str, str]]] = {
        ("HEATING_INSTALLATION", "type_of_system"): {
            "SURFACE_HEATING": "FLOORHEATING",
            "CONVENTIONAL_RADIATOR": "RADIATOR",
        },
        ("VENTILATION_SYSTEM", "type_of_system"): {
            "MECHANICALEXTRACT": "MECHANICAL_EXTRACT",
            "DEMANDCONTROLLEDEXTRACT": "DEMAND_CONTROLLED_EXTRACT",
            "MECHANICALVENTILATIONWITHHEATRECOVERY": "MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY",
        },
        ("EXTERNAL_INSULATION", "material"): {
            "EPS": "polystyrene_eps_rigid_board",
            "XPS": "extruded_polystyrene_xps",
        },
        ("INTERNAL_DRY_LINING_INSULATION", "material"): {
            "THERMAL_LAMINATE_DRYLINING_BOARD": MaterialResolution.UNRESOLVED.value,
            "MINERAL_WOOL": MaterialResolution.UNRESOLVED.value,
        },
        ("BASEMENT_CEILING_INSULATION", "material"): {
            "EPS_FOAM": "polystyrene_eps_rigid_board",
            "MINERAL_WOOL": MaterialResolution.UNRESOLVED.value,
        },
        ("BASEMENT_EXTERNAL_INSULATION", "material"): {
            "EPS_FOAM": "polystyrene_eps_rigid_board",
            "MINERAL_WOOL": MaterialResolution.UNRESOLVED.value,
        },
        ("SOLID_GROUND_FLOOR_INSULATION", "material"): {
            "LIQUID_INSULATION": MaterialResolution.UNRESOLVED.value,
            "EPS_FOAM": "polystyrene_eps_rigid_board",
        },
        ("SUSPENDED_GROUND_FLOOR_INSULATION", "material"): {
            "EPS_FOAM": "polystyrene_eps_rigid_board",
            "MINERAL_WOOL": MaterialResolution.UNRESOLVED.value,
        },
        ("WARM_ROOF_INSULATION", "material"): {
            "PIR": MaterialResolution.UNRESOLVED.value,
            "MINERAL_WOOL": MaterialResolution.UNRESOLVED.value,
            "GLASS_WOOL": "metac_glasswool",
            "WOOD_FIBER": "wood_fiber_rigid_board",
        },
        ("RAFTER_INSULATION", "material"): {
            "OPEN_CELL_SPRAY_FOAM": "open_cell_spray_foam",
            "PIR": MaterialResolution.UNRESOLVED.value,
            "GLASS_WOOL": "metac_glasswool",
            "WOOD_FIBER": "wood_fiber_rigid_board",
        },
        ("ROLLED_OUT_ATTIC_INSULATION", "material"): {
            "MINERAL_WOOL": MaterialResolution.UNRESOLVED.value,
            "GLASS_WOOL": "metac_glasswool",
            "WOOD_FIBER": "wood_fiber_rigid_board",
        },
        ("WINDOW_REPLACEMENT", "glazing_panes"): {
            "2": MaterialResolution.UNRESOLVED.value,
            "3": MaterialResolution.UNRESOLVED.value,
        },
        ("DOOR_REPLACEMENT", "glazing_panes"): {
            "0": MaterialResolution.UNRESOLVED.value,
            "2": MaterialResolution.UNRESOLVED.value,
            "3": MaterialResolution.UNRESOLVED.value,
        },
    }

    @classmethod
    def hisim_value(cls, measure_id: str, option_id: str, catalogue_value: str) -> str:
        """Return the HiSim wire value for one catalogue value, or the value itself.

        Args:
            measure_id: The measure the option belongs to, e.g. ``"WARM_ROOF_INSULATION"``.
            option_id: The option, e.g. ``"material"``.
            catalogue_value: The derived catalogue value, e.g. ``"GLASS_WOOL"``.

        Returns:
            The mapped value (an ``asp_id`` or a HiSim enum member name), or *catalogue_value*
            unchanged — both when the table has no entry, which is the case for every value the
            catalogue already spells HiSim's way, and when the entry is the unresolved marker,
            because an unresolvable value still needs a distinct, sendable spelling of its own.
            :meth:`is_unresolved` is what carries the marker.
        """
        mapped = cls.BY_OPTION.get((measure_id, option_id), {}).get(catalogue_value)
        if mapped is None or mapped == MaterialResolution.UNRESOLVED.value:
            return catalogue_value
        return mapped

    @classmethod
    def is_unresolved(cls, measure_id: str, option_id: str, catalogue_value: str) -> bool:
        """Return whether this catalogue value has no counterpart HiSim can simulate.

        Args:
            measure_id: The measure the option belongs to.
            option_id: The option.
            catalogue_value: The derived catalogue value.

        Returns:
            ``True`` when the table marks the value unresolved, whether the option is an enum
            whose value was rewritten to the marker or an integer whose value stays a number.
        """
        mapped = cls.BY_OPTION.get((measure_id, option_id), {}).get(catalogue_value)
        return mapped == MaterialResolution.UNRESOLVED.value


@dataclass(frozen=True)
class OptionSpec:
    """One option of one catalogue measure, with its values in HiSim spelling.

    Args:
        option_id: The snake_case id a request uses, e.g. ``"thickness_in_mm"``.
        display_name: The catalogue's spelling, e.g. ``"thickness_in_mm"`` or ``"type of system"``.
        value_type: Whether the option takes an enum value, an integer or a boolean.
        access_level: Whether every request carries it or only an expert's.
        values: The allowed values in HiSim spelling, as strings; empty when the catalogue lists
            none (a free integer such as a thickness, or a boolean).
        display_values: The catalogue's own spellings in the same order, for the translation map,
            which shows both (decision V3).
    """

    option_id: str
    display_name: str
    value_type: OptionValueType
    access_level: AccessLevel
    values: Tuple[str, ...]
    display_values: Tuple[str, ...]

    def display_value_of(self, value: str) -> str:
        """Return the catalogue spelling that produced *value*.

        Args:
            value: One entry of :attr:`values`.

        Returns:
            The entry of :attr:`display_values` at the same position, or *value* itself when the
            option lists no values.
        """
        if value in self.values:
            return self.display_values[self.values.index(value)]
        return value


@dataclass(frozen=True)
class MeasureSpec:
    """One catalogue measure: its id, its label, where it sits in the catalogue, and its options.

    Args:
        measure_id: The UPPER_SNAKE id a request uses, e.g. ``"ROLLED_OUT_ATTIC_INSULATION"``.
        display_name: The catalogue's label, e.g. ``"rolled out attic insulation"``.
        category: The catalogue's top-level grouping, e.g. ``"building envelope measures"``.
        subcategory: The catalogue's second-level grouping, e.g. ``"roof insulation"``.
        options: The measure's options in catalogue order; empty for the measures that take none.
    """

    measure_id: str
    display_name: str
    category: str
    subcategory: str
    options: Tuple[OptionSpec, ...]

    def option(self, option_id: str) -> OptionSpec:
        """Return one option by its id.

        Args:
            option_id: The snake_case option id.

        Returns:
            The :class:`OptionSpec`.

        Raises:
            KeyError: When this measure has no such option; callers turn that into a
                ``ValidationError(UNKNOWN_OPTION)``.
        """
        for option in self.options:
            if option.option_id == option_id:
                return option
        raise KeyError(f"measure '{self.measure_id}' has no option '{option_id}'")

    def option_ids(self) -> Tuple[str, ...]:
        """Return the ids of every option of this measure, in catalogue order."""
        return tuple(option.option_id for option in self.options)


class Catalogue:
    """The 33 catalogue measures as typed specs, loaded from the vendored ``measures.yaml``.

    Example::

        catalogue = Catalogue.load()
        catalogue.measure_ids()[:2]        # ('EXTERNAL_INSULATION', 'INTERNAL_DRY_LINING_INSULATION')
        catalogue.by_id("OUTSIDE_SHADING").options   # ()

    Loading parses the vendored file every time it is called, as
    :class:`hisim.renovisor.contract.ContractFiles` does: the file is small, and a cache would
    hide a contract refresh that happened during a long test session.
    """

    def __init__(self, measures: Tuple[MeasureSpec, ...]) -> None:
        """Store the measures and index them by id.

        Args:
            measures: The specs, in catalogue order.

        Raises:
            ValueError: When two measures derive the same id, which would make a request
                ambiguous.
        """
        self._measures = measures
        self._by_id: Dict[str, MeasureSpec] = {}
        for spec in measures:
            if spec.measure_id in self._by_id:
                raise ValueError(f"duplicate measure id '{spec.measure_id}' in the catalogue")
            self._by_id[spec.measure_id] = spec

    @classmethod
    def load(cls) -> "Catalogue":
        """Read the vendored ``measures.yaml`` and return the typed catalogue.

        Returns:
            A :class:`Catalogue` holding one :class:`MeasureSpec` per catalogue entry.

        Raises:
            ValueError: When the file has no ``measures`` list, or two measures share an id.
        """
        document = ContractFiles.measures()
        entries = document.get("measures") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            raise ValueError("measures.yaml has no 'measures' list")
        return cls(tuple(cls._read_measure(entry) for entry in entries))

    def by_id(self, measure_id: str) -> MeasureSpec:
        """Return one measure spec by its id.

        Args:
            measure_id: The UPPER_SNAKE measure id from the request.

        Returns:
            The :class:`MeasureSpec`.

        Raises:
            KeyError: When the catalogue has no such measure; the application turns that into a
                ``ValidationError(UNKNOWN_MEASURE)``.
        """
        return self._by_id[measure_id]

    def measure_ids(self) -> Tuple[str, ...]:
        """Return every measure id, in catalogue order."""
        return tuple(spec.measure_id for spec in self._measures)

    def measures(self) -> Tuple[MeasureSpec, ...]:
        """Return every measure spec, in catalogue order."""
        return self._measures

    @classmethod
    def _read_measure(cls, entry: Mapping[str, Any]) -> MeasureSpec:
        """Turn one raw catalogue entry into a :class:`MeasureSpec`."""
        display_name = str(entry["display_name"])
        measure_id = str(entry.get("id") or IdDerivation.upper_id(display_name))
        raw_options = entry.get("options") or []
        options = tuple(cls._read_option(measure_id, raw_option) for raw_option in raw_options)
        return MeasureSpec(
            measure_id=measure_id,
            display_name=display_name,
            category=str(entry.get("category", "")),
            subcategory=str(entry.get("subcategory", "")),
            options=options,
        )

    @classmethod
    def _read_option(cls, measure_id: str, entry: Mapping[str, Any]) -> OptionSpec:
        """Turn one raw catalogue option into an :class:`OptionSpec`, applying the spelling table."""
        display_name = str(entry["name"])
        option_id = str(entry.get("id") or IdDerivation.lower_id(display_name))
        value_type = OptionValueType[str(entry["value_type"]).strip().upper()]
        access_level = AccessLevel[str(entry.get("access_level", "Everyone")).strip().upper()]
        display_values, values = cls._read_values(measure_id, option_id, value_type, entry.get("values"))
        return OptionSpec(
            option_id=option_id,
            display_name=display_name,
            value_type=value_type,
            access_level=access_level,
            values=values,
            display_values=display_values,
        )

    @classmethod
    def _read_values(
        cls,
        measure_id: str,
        option_id: str,
        value_type: OptionValueType,
        raw_values: Optional[List[Any]],
    ) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        """Return ``(display_values, hisim_values)`` for one option's value list.

        Integer values keep their digits in both tuples; only enum values go through
        :class:`CatalogueSpellings`, because a glazing-pane count is a number in every spelling.
        """
        if not raw_values:
            return (), ()
        display_values = tuple(str(value) for value in raw_values)
        if value_type is not OptionValueType.ENUM:
            return display_values, display_values
        derived = tuple(IdDerivation.upper_id(value) for value in display_values)
        hisim_values = tuple(
            CatalogueSpellings.hisim_value(measure_id, option_id, value) for value in derived
        )
        return display_values, hisim_values
