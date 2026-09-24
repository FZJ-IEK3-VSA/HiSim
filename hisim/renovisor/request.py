"""The calculation request: what a valid one is, and the typed view the rest of the layer reads.

One request is one house inventory plus one list of catalogue measures, and this module is the
only place that decides whether a document is one. It answers in two steps, and reports every
problem it finds rather than the first::

    request = Request.parse(document)        # raises RequestError with every problem at once
    request.house.heating.type_of_system     # HeatGenerator.CONVENTIONAL_GAS_HEATING

**Structural validation** runs the vendored ``calculation-request.schema.json`` itself, through
``jsonschema``'s Draft 2020-12 validator, rather than a hand-written mirror of it. The frontend
side's spec expected a pydantic mirror plus a test (T-SCHEMA) proving the mirror and the schema
agree; using the schema directly leaves nothing to prove, so the test became "the vendored mockup
validates, and a corpus of one-key mutations of it is rejected with the expected code and path"
(step 8 §3.1).

**Semantic validation** is everything the schema cannot express: that a measure id is one of the
catalogue's 32, that an option belongs to its measure, that an ``everyone`` option is present,
that the country has a TABULA typology, that two blocks do not state two different hot-water
storage volumes, and that a TABULA row can be found. The catalogue facts those checks need are a
frozen table in this module (:class:`CatalogueTable`), not a YAML read at request time, so a
catalogue edit fails the build by name (T-CAT) and never a user's request.

Rule 5 of the contract is why no material database is read here: the request carries the physical
*properties* of a material, and its ``asp_id`` travels as provenance only. The only thing checked
about a material is that its conductivity is a positive number, which the schema already says.
"""

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.vocabulary import (
    AirTightness,
    BuildingType,
    CollectorType,
    Country,
    FrameMaterial,
    HeatDistributionType,
    HeatGenerator,
    HotWaterSupply,
    RoofShape,
    SolarThermalSupplies,
    TemperatureControl,
    VentilationType,
    WhiteAppliances,
)


class ProblemCode(str, Enum):
    """The codes ``problems.json`` carries, verbatim from §7 of the contract.

    They are deliberately dotted strings rather than ``UPPER_SNAKE`` reason codes: the frontend
    matches on them and the contract spells them this way. The first seven come out of the schema
    (:class:`SchemaProblems`), the rest out of the semantic checks (:class:`SemanticChecks`).
    """

    KEY_UNKNOWN = "key.unknown"
    TYPE_INVALID = "type.invalid"
    ENUM_UNKNOWN = "enum.unknown"
    RANGE_EXCEEDED = "range.exceeded"
    REQUIRED_MISSING = "required.missing"
    ONEOF_VIOLATED = "oneof.violated"
    SCHEMA_VERSION_UNSUPPORTED = "schema_version.unsupported"
    MEASURE_UNKNOWN = "measure.unknown"
    MEASURE_DUPLICATE = "measure.duplicate"
    MEASURE_OPTION_UNKNOWN = "measure.option.unknown"
    MEASURE_OPTION_MISSING = "measure.option.missing"
    MEASURE_OPTION_VALUE_UNKNOWN = "measure.option.value.unknown"
    ADDED_INSULATION_NOT_ALLOWED = "added_insulation.not_allowed"
    LOCATION_COUNTRY_UNSUPPORTED = "location.country.unsupported"
    HOT_WATER_CONFLICTING_VOLUMES = "hot_water.conflicting_volumes"
    TABULA_UNRESOLVABLE = "tabula.unresolvable"
    # Not in §7 of the contract: the `measures[i].cost` block it belongs to is the E-spec §7
    # proposal the vendored schema has not adopted yet (findings F7/F10), so its code is minted
    # here in the contract's own spelling and joins §7 when the block does.
    MEASURE_COST_BAND_INVALID = "measure.cost.band_invalid"


@dataclass(frozen=True)
class Problem:
    """One reason a request was refused, at one path.

    Args:
        path: The dotted path of the offending value, with ``[i]`` for list indices, e.g.
            ``house.heating.type_of_system`` or ``measures[2].options.type_of_system``.
        code: Which kind of problem it is.
        message: One sentence a person can read, naming the value and what is wrong with it.
        accepted: For an unknown enum value, the values that would have been accepted; ``None``
            for every other code.
    """

    path: str
    code: ProblemCode
    message: str
    accepted: Optional[Tuple[Any, ...]] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the problem as the dictionary ``problems.json`` carries."""
        row: Dict[str, Any] = {"path": self.path, "code": self.code.value, "message": self.message}
        if self.accepted is not None:
            row["accepted"] = list(self.accepted)
        return row


class RequestError(Exception):
    """A request that cannot be simulated because it is not a valid request.

    Every problem found is carried, not just the first, because a frontend fixing one field at a
    time against a backend that reports one problem at a time is the slow way to find out that a
    form is wrong. Exit code 2 and ``problems.json`` (F-spec §3.3).

    Args:
        problems: The problems found, in the order they were found.
    """

    def __init__(self, problems: Sequence[Problem]) -> None:
        """Store the problems and build the one-line message the command line prints."""
        self.problems: Tuple[Problem, ...] = tuple(problems)
        count = len(self.problems)
        super().__init__(f"{count} problem{'' if count == 1 else 's'} in the calculation request")

    def to_json(self) -> Dict[str, Any]:
        """Return the ``problems.json`` document: ``{"problems": [...]}``."""
        return {"problems": [problem.to_json() for problem in self.problems]}


class AccessLevel(str, Enum):
    """Whether an option must be present in a request or may be left to the translator's default.

    ``EVERYONE`` options are the ones the fine-tuning page always asks for, and their absence is
    ``measure.option.missing``. ``EXPERTS`` options may be absent, in which case the translator
    applies the default of the F-spec's §4.2 table and reports a ``defaulted`` line.
    """

    EVERYONE = "everyone"
    EXPERTS = "experts"


class ValueType(str, Enum):
    """What kind of value an option carries, as ``measures.yaml`` declares it.

    ``ENUM`` values are checked against the option's own ``values`` list, except for the
    ``material`` option, whose request value is the material *object* of the schema and whose
    catalogue values are class names the frontend resolved before sending (rule 5).
    """

    ENUM = "enum"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class OptionSpec:
    """One option of one catalogue measure, as the frozen table states it.

    Args:
        name: The option's name in the request, e.g. ``thickness_in_mm``.
        access_level: Whether the request must carry it.
        value_type: What kind of value it carries.
        values: The accepted values for an enum or a closed integer option; ``None`` for a free
            number or integer, and for a boolean.
    """

    name: str
    access_level: AccessLevel
    value_type: ValueType
    values: Optional[Tuple[Any, ...]] = None


class CatalogueTable:
    """The 32 measures of the 2026-09-17 catalogue revision, frozen as code.

    The semantic checks need the ids, the option names, their access levels and their value
    lists, and reading ``measures.yaml`` at request time would turn a catalogue edit into a
    refused user request. So the facts live here and the ``capabilities`` command's first job is
    to assert that this table equals the vendored file (T-CAT): a catalogue edit then fails the
    build, by name, before an image is ever published.

    The measures whose entry is an empty tuple carry no options at all -- the catalogue writes
    ``options: []`` for them, and a request may send no ``options`` key or an empty one.
    """

    #: The option ``experts`` option that five measures share and no HiSim parameter receives.
    INSTALLATION_YEAR: ClassVar[str] = "installation_year"

    #: The option whose request value is the material object of the schema rather than one of the
    #: catalogue's class names; rule 5 of the contract moved the resolution to the frontend.
    MATERIAL: ClassVar[str] = "material"

    #: measure id -> its options, in the order the catalogue declares them.
    BY_ID: ClassVar[Dict[str, Tuple[OptionSpec, ...]]] = {
        "external_insulation": (
            OptionSpec("material", AccessLevel.EVERYONE, ValueType.ENUM, ("EPS", "XPS")),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "internal_dry_lining_insulation": (
            OptionSpec(
                "material",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("PIR", "XPS", "EPS", "Mineral wool"),
            ),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "cavity_wall_insulation": (
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "window_replacement": (
            OptionSpec("glazing_panes", AccessLevel.EVERYONE, ValueType.INTEGER, (2, 3)),
            OptionSpec(
                "frame_material",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("wood", "plastic", "metal", "composite"),
            ),
            OptionSpec("low_emissivity_coating", AccessLevel.EVERYONE, ValueType.BOOLEAN, None),
            OptionSpec(
                "u_value_in_watt_per_m2_per_kelvin", AccessLevel.EXPERTS, ValueType.NUMBER, None
            ),
        ),
        "outside_shading": (),
        "door_replacement": (
            OptionSpec("glazing_panes", AccessLevel.EVERYONE, ValueType.INTEGER, (0, 2, 3)),
            OptionSpec(
                "frame_material",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("wood", "plastic", "metal", "composite"),
            ),
            OptionSpec(
                "u_value_in_watt_per_m2_per_kelvin", AccessLevel.EXPERTS, ValueType.NUMBER, None
            ),
        ),
        "basement_ceiling_insulation": (
            OptionSpec("material", AccessLevel.EVERYONE, ValueType.ENUM, ("EPS Foam", "Mineral Wool")),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "basement_internal_insulation": (),
        "basement_external_insulation": (
            OptionSpec("material", AccessLevel.EVERYONE, ValueType.ENUM, ("EPS Foam", "Mineral Wool")),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "solid_ground_floor_insulation": (
            OptionSpec(
                "material", AccessLevel.EVERYONE, ValueType.ENUM, ("Liquid Insulation", "EPS Foam")
            ),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "suspended_ground_floor_insulation": (
            OptionSpec("material", AccessLevel.EVERYONE, ValueType.ENUM, ("EPS Foam", "Mineral Wool")),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
            OptionSpec("air_barrier", AccessLevel.EXPERTS, ValueType.BOOLEAN, None),
        ),
        "warm_roof_insulation": (
            OptionSpec(
                "material",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("PIR", "mineral wool", "glass wool", "wood fiber"),
            ),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "rafter_insulation": (
            OptionSpec(
                "material",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("open cell spray foam", "PIR", "glass wool", "wood fiber"),
            ),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "rolled_out_attic_insulation": (
            OptionSpec(
                "material",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("mineral wool", "glass wool", "wood fiber"),
            ),
            OptionSpec("thickness_in_mm", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "top_floor_ceiling_insulation": (),
        "ventilation_system": (
            OptionSpec(
                "type_of_system",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                (
                    "mechanical_extract",
                    "demand_controlled_extract",
                    "mechanical_ventilation_with_heat_recovery",
                    "window_trickle_vent",
                ),
            ),
            OptionSpec("installation_year", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "shallow_air_tightness_measures": (),
        "hot_water_tank_and_pipe_insulation": (),
        "heating_system": (
            OptionSpec(
                "type_of_system",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                (
                    "air_source_heat_pump",
                    "ground_source_heat_pump",
                    "hybrid_heat_pump",
                    "electric_heating",
                    "biomass_heating",
                    "pellet_heating",
                    "woodchip_heating",
                    "district_heating",
                    "hvo_heating",
                    "hydrogen_heating",
                    "conventional_gas_heating",
                    "conventional_oil_heating",
                    "conventional_lpg_heating",
                    "condensing_gas_heating",
                    "condensing_oil_heating",
                    "condensing_lpg_heating",
                ),
            ),
            OptionSpec("installation_year", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "heating_installation": (
            OptionSpec(
                "type_of_system",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("surface_heating", "low_temperature_radiator", "conventional_radiator"),
            ),
        ),
        "air_conditioners": (
            OptionSpec("power_in_watt", AccessLevel.EVERYONE, ValueType.INTEGER, None),
        ),
        "hot_water_system": (
            OptionSpec(
                "supply",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("together_with_heating_system", "separate_heat_pump", "separate_direct_electric"),
            ),
        ),
        "temperature_control_system": (
            OptionSpec(
                "type_of_system",
                AccessLevel.EVERYONE,
                ValueType.ENUM,
                ("traditional_thermostats", "smart_heating_control_system"),
            ),
        ),
        "replace_white_appliances": (),
        "photovoltaic_system": (
            OptionSpec("size_in_percent_of_roof_area", AccessLevel.EVERYONE, ValueType.INTEGER, None),
            OptionSpec("installation_year", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "battery_system": (
            OptionSpec("days_to_cover", AccessLevel.EVERYONE, ValueType.INTEGER, None),
            OptionSpec("installation_year", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "solar_thermal_system": (
            OptionSpec(
                "supplies", AccessLevel.EVERYONE, ValueType.ENUM, ("dhw_only", "dhw_and_space_heating")
            ),
            OptionSpec("installation_year", AccessLevel.EXPERTS, ValueType.INTEGER, None),
        ),
        "electric_vehicle": (
            OptionSpec("number", AccessLevel.EVERYONE, ValueType.INTEGER, None),
        ),
        "change_room_temperature": (
            OptionSpec("new_room_temperature", AccessLevel.EVERYONE, ValueType.INTEGER, None),
        ),
        "diy_sealing_of_air_leaks": (),
        "thermocover_for_the_windows": (),
        "optimize_behaviour_for_self_consumption_of_pv": (),
    }

    @classmethod
    def ids(cls) -> Tuple[str, ...]:
        """Return every catalogue id, in the catalogue's own order."""
        return tuple(cls.BY_ID)

    @classmethod
    def options_of(cls, measure_id: str) -> Tuple[OptionSpec, ...]:
        """Return one measure's options, in the catalogue's order.

        Args:
            measure_id: A catalogue id.

        Returns:
            The option specifications; an empty tuple for a measure with no options.

        Raises:
            KeyError: When the id is not in the catalogue, which the semantic checks refuse
                before anything asks for its options.
        """
        return cls.BY_ID[measure_id]

    @classmethod
    def option(cls, measure_id: str, name: str) -> Optional[OptionSpec]:
        """Return one named option of one measure, or ``None`` when the measure has no such option."""
        for option in cls.BY_ID.get(measure_id, ()):
            if option.name == name:
                return option
        return None


class SchemaProblems:
    """Turns the JSON Schema validator's errors into the contract's problem codes.

    ``jsonschema`` reports a keyword and a path; the contract reports a code and a path. This
    class is the whole translation between the two, in one place, so that adding a keyword to the
    schema is a line here rather than a surprise in a message. Errors are collected with
    ``iter_errors``, so a document with five faults produces five problems.
    """

    #: The message ``required`` produces, whose quoted property name is the missing key.
    REQUIRED_PATTERN: ClassVar["re.Pattern[str]"] = re.compile(r"^'(?P<name>[^']+)' is a required property")

    #: Validator keyword -> problem code, for the keywords whose mapping needs no context.
    BY_KEYWORD: ClassVar[Dict[str, ProblemCode]] = {
        "type": ProblemCode.TYPE_INVALID,
        "pattern": ProblemCode.TYPE_INVALID,
        "propertyNames": ProblemCode.TYPE_INVALID,
        "enum": ProblemCode.ENUM_UNKNOWN,
        "minimum": ProblemCode.RANGE_EXCEEDED,
        "maximum": ProblemCode.RANGE_EXCEEDED,
        "exclusiveMinimum": ProblemCode.RANGE_EXCEEDED,
        "exclusiveMaximum": ProblemCode.RANGE_EXCEEDED,
        "maxLength": ProblemCode.RANGE_EXCEEDED,
        "minLength": ProblemCode.RANGE_EXCEEDED,
        "oneOf": ProblemCode.ONEOF_VIOLATED,
        "anyOf": ProblemCode.TYPE_INVALID,
    }

    @classmethod
    def validator(cls) -> Draft202012Validator:
        """Return a validator over the vendored request schema.

        Returns:
            A Draft 2020-12 validator; building one is cheap and a fresh one cannot carry state
            from a previous document.
        """
        return Draft202012Validator(ContractFiles.request_schema())

    @classmethod
    def of(cls, document: Any) -> Tuple[Problem, ...]:
        """Return every structural problem of one document, in path order.

        Args:
            document: The parsed request, as JSON or YAML produced it.

        Returns:
            One :class:`Problem` per fault; an empty tuple for a document the schema accepts.
        """
        problems: List[Problem] = []
        for error in sorted(cls.validator().iter_errors(document), key=lambda item: list(item.absolute_path)):
            problems.extend(cls._of_error(error))
        return tuple(problems)

    @classmethod
    def _of_error(cls, error: SchemaValidationError) -> List[Problem]:
        """Return the problems of one validator error; more than one for a set of unknown keys."""
        path = cls.path_of(error.absolute_path)
        keyword = str(error.validator)
        if keyword == "additionalProperties":
            return [
                Problem(
                    path=cls.join(path, name),
                    code=ProblemCode.KEY_UNKNOWN,
                    message=f"'{name}' is not a field of this object",
                )
                for name in cls._unexpected(error)
            ]
        if keyword == "required":
            name = cls._missing(error)
            return [
                Problem(
                    path=cls.join(path, name),
                    code=ProblemCode.REQUIRED_MISSING,
                    message=f"'{name}' is required and the request does not carry it",
                )
            ]
        if keyword == "const":
            return [
                Problem(
                    path=path,
                    code=ProblemCode.SCHEMA_VERSION_UNSUPPORTED
                    if path == "schema_version"
                    else ProblemCode.ENUM_UNKNOWN,
                    message=error.message,
                    accepted=(error.validator_value,),
                )
            ]
        code = cls.BY_KEYWORD.get(keyword, ProblemCode.TYPE_INVALID)
        accepted = (
            tuple(error.validator_value)
            if code is ProblemCode.ENUM_UNKNOWN and isinstance(error.validator_value, list)
            else None
        )
        return [Problem(path=path, code=code, message=error.message, accepted=accepted)]

    @classmethod
    def _unexpected(cls, error: SchemaValidationError) -> Tuple[str, ...]:
        """Return the keys an object carries that its schema does not declare."""
        instance = error.instance
        declared = set(error.schema.get("properties", {})) if isinstance(error.schema, Mapping) else set()
        if not isinstance(instance, Mapping):
            return ()
        return tuple(sorted(str(key) for key in instance if key not in declared))

    @classmethod
    def _missing(cls, error: SchemaValidationError) -> str:
        """Return the name of the required property one ``required`` error is about."""
        match = cls.REQUIRED_PATTERN.match(error.message)
        if match is not None:
            return match.group("name")
        return "?"

    @classmethod
    def path_of(cls, parts: Any) -> str:
        """Return the dotted path of a validator's ``absolute_path``.

        Args:
            parts: The deque of keys and list indices the validator carries.

        Returns:
            ``house.building.roof.shape`` for object keys, ``measures[2].id`` for list indices;
            the empty string for the document itself.
        """
        path = ""
        for part in parts:
            if isinstance(part, int):
                path = f"{path}[{part}]"
            else:
                path = f"{path}.{part}" if path else str(part)
        return path

    @classmethod
    def join(cls, path: str, name: str) -> str:
        """Return *path* extended by one object key, handling the document root."""
        return f"{path}.{name}" if path else name


@dataclass(frozen=True)
class Material:
    """The physical properties of one insulation material, as the request carries them.

    Field names are ``materials.yaml``'s verbatim, because the frontend copies the row and the
    backend reads no catalogue (rule 5). ``asp_id`` is provenance: it is reported and never
    looked up. Only ``thermal_conductivity_w_mk`` reaches the physics; the other three are
    recorded in the mapping report, and ``co2_footprint_a1_a3_c3_c4_kg_m2`` feeds the embodied
    carbon of ``result.json``.

    Args:
        asp_id: The materials database's own id of the row the frontend copied.
        thermal_conductivity_w_mk: Lambda in W/(m·K); the only property the U-value arithmetic uses.
        heat_capacity_j_kgk: Specific heat capacity in J/(kg·K), recorded only.
        density_kg_m3: Density in kg/m³, the midpoint of the file's range, recorded only.
        co2_footprint_a1_a3_c3_c4_kg_m2: EN 15804+A2 footprint per square metre.
        lifespan_years: Expected service life, recorded only.
    """

    asp_id: str
    thermal_conductivity_w_mk: float
    heat_capacity_j_kgk: Optional[float] = None
    density_kg_m3: Optional[float] = None
    co2_footprint_a1_a3_c3_c4_kg_m2: Optional[float] = None
    lifespan_years: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Material":
        """Build a material from one request object, ignoring nothing and inventing nothing."""
        return cls(
            asp_id=str(raw["asp_id"]),
            thermal_conductivity_w_mk=float(raw["thermal_conductivity_w_mk"]),
            heat_capacity_j_kgk=cls._number(raw.get("heat_capacity_j_kgk")),
            density_kg_m3=cls._number(raw.get("density_kg_m3")),
            co2_footprint_a1_a3_c3_c4_kg_m2=cls._number(raw.get("co2_footprint_a1_a3_c3_c4_kg_m2")),
            lifespan_years=cls._number(raw.get("lifespan_years")),
        )

    @classmethod
    def _number(cls, value: Any) -> Optional[float]:
        """Return a number as a float, or ``None`` when the request did not carry the field."""
        return None if value is None else float(value)


@dataclass(frozen=True)
class Layer:
    """One insulation layer a measure added to one envelope element.

    The schema's ``added_insulation`` is a single object because one layer is the common case;
    the internal model stores a list, because two measures on one element are two real layers and
    the second is applied to the result of the first (F-spec §4.2).

    Args:
        placement: The ``building_components`` vocabulary value naming where the layer sits.
            Provenance in this release: it does not enter the arithmetic.
        thickness_in_mm: The layer's thickness.
        material: Its material's properties.
        air_barrier: Whether an air barrier was installed with it; no HiSim parameter.
    """

    placement: str
    thickness_in_mm: int
    material: Material
    air_barrier: Optional[bool] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Layer":
        """Build a layer from one ``added_insulation`` object of the renovated house."""
        barrier = raw.get("air_barrier")
        return cls(
            placement=str(raw["placement"]),
            thickness_in_mm=int(raw["thickness_in_mm"]),
            material=Material.from_dict(raw["material"]),
            air_barrier=None if barrier is None else bool(barrier),
        )


@dataclass(frozen=True)
class Element:
    """One of the five envelope elements, as the renovated house carries it.

    Every element has a U-value, because the frontend derives it from its country pack and the
    schema requires it (rule 5); every other field is optional and several of them belong to one
    element only. An absent ``area_in_m2`` keeps the TABULA archetype's area, scaled to the
    conditioned floor area.

    Args:
        u_value_in_watt_per_m2_per_kelvin: W/(m²·K) of the element as it stands after the measures.
        area_in_m2: The element's area, or ``None`` to leave it to the TABULA row.
        added_insulation: The layers the measures added, in the order they were added.
        shape: ``roof`` only: pitched or flat, which sets the photovoltaic tilt.
        glazing_panes: ``window`` (1, 2, 3) and ``door`` (0, 2, 3) only.
        frame_material: ``window`` and ``door`` only; no HiSim parameter.
        low_emissivity_coating: ``window`` only; it enters the replacement's U-value table.
        outside_shading: ``window`` only; no HiSim parameter.
        thermocover: ``window`` only; no HiSim parameter.
    """

    u_value_in_watt_per_m2_per_kelvin: float
    area_in_m2: Optional[float] = None
    added_insulation: Tuple[Layer, ...] = ()
    shape: Optional[RoofShape] = None
    glazing_panes: Optional[int] = None
    frame_material: Optional[FrameMaterial] = None
    low_emissivity_coating: Optional[bool] = None
    outside_shading: Optional[bool] = None
    thermocover: Optional[bool] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Element":
        """Build one element from its block of the renovated house."""
        layers = raw.get("added_insulation") or []
        if isinstance(layers, Mapping):
            layers = [layers]
        return cls(
            u_value_in_watt_per_m2_per_kelvin=float(raw["u_value_in_watt_per_m2_per_kelvin"]),
            area_in_m2=None if raw.get("area_in_m2") is None else float(raw["area_in_m2"]),
            added_insulation=tuple(Layer.from_dict(layer) for layer in layers),
            shape=_member(RoofShape, raw.get("shape")),
            glazing_panes=None if raw.get("glazing_panes") is None else int(raw["glazing_panes"]),
            frame_material=_member(FrameMaterial, raw.get("frame_material")),
            low_emissivity_coating=_boolean(raw.get("low_emissivity_coating")),
            outside_shading=_boolean(raw.get("outside_shading")),
            thermocover=_boolean(raw.get("thermocover")),
        )


@dataclass(frozen=True)
class Building:
    """The building block of the house: the archetype, the geometry and the five elements.

    Args:
        building_type: The homeowner's answer, which selects the TABULA typology.
        construction_year: The year the TABULA age band is chosen by.
        absolute_conditioned_floor_area_in_m2: Heated living area; scales every TABULA area.
        set_heating_temperature_in_celsius: The room set point, which propagates to the heat
            distribution controller.
        roof, facade, floor, window, door: The five envelope elements.
        number_of_storeys: Recorded; the archetype's own storey count is used.
        tabula_building_code: The expert override that skips the derivation entirely.
    """

    building_type: BuildingType
    construction_year: int
    absolute_conditioned_floor_area_in_m2: float
    set_heating_temperature_in_celsius: float
    roof: Element
    facade: Element
    floor: Element
    window: Element
    door: Element
    number_of_storeys: Optional[int] = None
    tabula_building_code: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Building":
        """Build the building block from the renovated house."""
        storeys = raw.get("number_of_storeys")
        code = raw.get("tabula_building_code")
        return cls(
            building_type=BuildingType(raw["building_type"]),
            construction_year=int(raw["construction_year"]),
            absolute_conditioned_floor_area_in_m2=float(raw["absolute_conditioned_floor_area_in_m2"]),
            set_heating_temperature_in_celsius=float(raw["set_heating_temperature_in_celsius"]),
            roof=Element.from_dict(raw["roof"]),
            facade=Element.from_dict(raw["facade"]),
            floor=Element.from_dict(raw["floor"]),
            window=Element.from_dict(raw["window"]),
            door=Element.from_dict(raw["door"]),
            number_of_storeys=None if storeys is None else int(storeys),
            tabula_building_code=None if code is None else str(code),
        )


@dataclass(frozen=True)
class Occupancy:
    """Who lives in the dwelling, and how they use it.

    In the MVP none of it reaches the simulation: every household is the predefined CHR01 couple
    (decision D-C). ``pv_self_consumption_optimised`` is the one field a measure writes.

    Args:
        number_of_residents: How many people live there.
        home_office_days_per_week: How many working days are spent at home.
        pv_self_consumption_optimised: Whether the household shifts loads into the sunshine.
    """

    number_of_residents: int
    home_office_days_per_week: Optional[float] = None
    pv_self_consumption_optimised: Optional[bool] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Occupancy":
        """Build the occupancy block from the renovated house."""
        days = raw.get("home_office_days_per_week")
        return cls(
            number_of_residents=int(raw["number_of_residents"]),
            home_office_days_per_week=None if days is None else float(days),
            pv_self_consumption_optimised=_boolean(raw.get("pv_self_consumption_optimised")),
        )


@dataclass(frozen=True)
class Heating:
    """The heat generator and what is known about it.

    ``flow_temperature_in_celsius`` and ``seasonal_efficiency_in_percent`` describe the generator
    the dwelling has; a ``heating_system`` measure removes both, because they described the
    machine it replaced.

    Args:
        type_of_system: The generator, which selects the base file.
        cooking_range: Whether the generator is a range cooker; no HiSim component.
        secondary: A second heat source; no HiSim component.
        flow_temperature_in_celsius: The heating circuit's design flow temperature.
        seasonal_efficiency_in_percent: The generator's seasonal efficiency.
    """

    type_of_system: HeatGenerator
    cooking_range: Optional[bool] = None
    secondary: Optional[str] = None
    flow_temperature_in_celsius: Optional[float] = None
    seasonal_efficiency_in_percent: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Heating":
        """Build the heating block from the renovated house."""
        flow = raw.get("flow_temperature_in_celsius")
        efficiency = raw.get("seasonal_efficiency_in_percent")
        secondary = raw.get("secondary")
        return cls(
            type_of_system=HeatGenerator(raw["type_of_system"]),
            cooking_range=_boolean(raw.get("cooking_range")),
            secondary=None if secondary is None else str(secondary),
            flow_temperature_in_celsius=None if flow is None else float(flow),
            seasonal_efficiency_in_percent=None if efficiency is None else float(efficiency),
        )


@dataclass(frozen=True)
class HotWater:
    """How domestic hot water is made and stored.

    The schema requires ``supply`` of a request that carries the block at all, but the
    ``hot_water_tank_and_pipe_insulation`` measure creates the block on a house that had none,
    so the renovated house can carry a block without a supply. ``None`` there means the
    translator's own default, and the report says so.

    Args:
        supply: Which machine makes it, or ``None`` when only a measure created the block.
        volume_heating_water_storage_in_liter: The storage volume; ``None`` leaves HiSim's own law.
        tank_and_pipe_insulated: Whether the tank and its pipes were insulated by a measure.
    """

    supply: Optional[HotWaterSupply] = None
    volume_heating_water_storage_in_liter: Optional[float] = None
    tank_and_pipe_insulated: Optional[bool] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "HotWater":
        """Build the hot-water block from the renovated house."""
        volume = raw.get("volume_heating_water_storage_in_liter")
        return cls(
            supply=_member(HotWaterSupply, raw.get("supply")),
            volume_heating_water_storage_in_liter=None if volume is None else float(volume),
            tank_and_pipe_insulated=_boolean(raw.get("tank_and_pipe_insulated")),
        )


@dataclass(frozen=True)
class PvSystem:
    """A photovoltaic array, sized either by its power or by the share of roof it covers.

    Args:
        power_in_watt: An existing array's installed peak power.
        size_in_percent_of_roof_area: The share of the usable roof a new array covers.
        azimuth: Degrees, 180 being south.
        tilt: Degrees from horizontal.
    """

    power_in_watt: Optional[float] = None
    size_in_percent_of_roof_area: Optional[int] = None
    azimuth: Optional[float] = None
    tilt: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PvSystem":
        """Build the photovoltaic block from the renovated house."""
        share = raw.get("size_in_percent_of_roof_area")
        return cls(
            power_in_watt=None if raw.get("power_in_watt") is None else float(raw["power_in_watt"]),
            size_in_percent_of_roof_area=None if share is None else int(share),
            azimuth=None if raw.get("azimuth") is None else float(raw["azimuth"]),
            tilt=None if raw.get("tilt") is None else float(raw["tilt"]),
        )


@dataclass(frozen=True)
class Battery:
    """A household battery, sized either by its capacity or by the days it should cover.

    Args:
        custom_battery_capacity_generic_in_kilowatt_hour: An existing battery's usable capacity.
        days_to_cover: How many days of household electricity a new battery should hold.
    """

    custom_battery_capacity_generic_in_kilowatt_hour: Optional[float] = None
    days_to_cover: Optional[int] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Battery":
        """Build the battery block from the renovated house."""
        capacity = raw.get("custom_battery_capacity_generic_in_kilowatt_hour")
        days = raw.get("days_to_cover")
        return cls(
            custom_battery_capacity_generic_in_kilowatt_hour=None if capacity is None else float(capacity),
            days_to_cover=None if days is None else int(days),
        )


@dataclass(frozen=True)
class SolarThermal:
    """A solar thermal collector and the storage it feeds.

    Args:
        supplies: What the collector feeds.
        collector_type: Flat plate or evacuated tube.
        area_m2: The absorber area; HiSim's own field name, without the ``_in_``.
        storage_volume_in_liter: The hot-water storage volume, the same storage as ``hot_water``.
    """

    supplies: SolarThermalSupplies
    collector_type: Optional[CollectorType] = None
    area_m2: Optional[float] = None
    storage_volume_in_liter: Optional[float] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SolarThermal":
        """Build the solar-thermal block from the renovated house."""
        volume = raw.get("storage_volume_in_liter")
        return cls(
            supplies=SolarThermalSupplies(raw["supplies"]),
            collector_type=_member(CollectorType, raw.get("collector_type")),
            area_m2=None if raw.get("area_m2") is None else float(raw["area_m2"]),
            storage_volume_in_liter=None if volume is None else float(volume),
        )


@dataclass(frozen=True)
class ElectricVehicles:
    """The home-charged electric cars, which are the only vehicles that touch the simulation.

    Args:
        number: How many cars are charged at the dwelling; one or two.
        commuting_distance_in_km: The one-way commute the driving profile is built from.
        charging_power_in_watt: The wallbox's power.
    """

    number: int
    commuting_distance_in_km: Optional[int] = None
    charging_power_in_watt: Optional[int] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ElectricVehicles":
        """Build the vehicle block from the renovated house."""
        distance = raw.get("commuting_distance_in_km")
        power = raw.get("charging_power_in_watt")
        return cls(
            number=int(raw["number"]),
            commuting_distance_in_km=None if distance is None else int(distance),
            charging_power_in_watt=None if power is None else int(power),
        )


@dataclass(frozen=True)
class House:
    """The whole dwelling, typed, as it stands after the measures were applied.

    An absent optional block is ``None`` rather than an empty object, because "no battery" and
    "a battery nobody said anything about" are different houses and the switch table of §5.4 of
    the contract reads the difference.

    Args:
        building: The archetype, the geometry and the five envelope elements.
        occupancy: Who lives there.
        heating: The heat generator.
        heat_distribution: How the heat reaches the rooms.
        hot_water: How hot water is made; ``None`` means the translator's own default applies.
        ventilation, temperature_control, air_conditioning, appliances: The four optional blocks
            that carry one or two fields each.
        pv_system, battery, solar_thermal_system, electric_vehicles: The four optional devices.
    """

    building: Building
    occupancy: Occupancy
    heating: Heating
    heat_distribution: HeatDistributionType
    hot_water: Optional[HotWater] = None
    ventilation_type: Optional[VentilationType] = None
    air_tightness: Optional[AirTightness] = None
    temperature_control: Optional[TemperatureControl] = None
    air_conditioning_power_in_watt: Optional[int] = None
    white_appliances: Optional[WhiteAppliances] = None
    pv_system: Optional[PvSystem] = None
    battery: Optional[Battery] = None
    solar_thermal_system: Optional[SolarThermal] = None
    electric_vehicles: Optional[ElectricVehicles] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "House":
        """Build the typed house from the renovated house dictionary.

        Args:
            raw: The ``house`` block of a validated request, after ``apply`` has written the
                measures into a copy of it.

        Returns:
            The typed view every later step reads. Nothing is defaulted here: an absent block
            stays absent, and :mod:`hisim.renovisor.translate` is what names the defaults and
            reports them.
        """
        ventilation = raw.get("ventilation") or {}
        control = raw.get("temperature_control") or {}
        conditioning = raw.get("air_conditioning") or {}
        appliances = raw.get("appliances") or {}
        return cls(
            building=Building.from_dict(raw["building"]),
            occupancy=Occupancy.from_dict(raw["occupancy"]),
            heating=Heating.from_dict(raw["heating"]),
            heat_distribution=HeatDistributionType(raw["heat_distribution"]["type_of_system"]),
            hot_water=cls._block(HotWater, raw.get("hot_water")),
            ventilation_type=_member(VentilationType, ventilation.get("type_of_system")),
            air_tightness=_member(AirTightness, ventilation.get("air_tightness")),
            temperature_control=_member(TemperatureControl, control.get("type_of_system")),
            air_conditioning_power_in_watt=(
                None if conditioning.get("power_in_watt") is None else int(conditioning["power_in_watt"])
            ),
            white_appliances=_member(WhiteAppliances, appliances.get("white_appliances")),
            pv_system=cls._block(PvSystem, raw.get("pv_system")),
            battery=cls._block(Battery, raw.get("battery")),
            solar_thermal_system=cls._block(SolarThermal, raw.get("solar_thermal_system")),
            electric_vehicles=cls._block(ElectricVehicles, raw.get("electric_vehicles")),
        )

    @classmethod
    def _block(cls, kind: Any, raw: Any) -> Any:
        """Build one optional block, or return ``None`` when the request does not carry it."""
        return None if raw is None else kind.from_dict(raw)

    def element(self, name: str) -> Element:
        """Return one envelope element by its request name.

        Args:
            name: ``roof``, ``facade``, ``floor``, ``window`` or ``door``.

        Returns:
            The element block.

        Raises:
            AttributeError: When the name is not one of the five.
        """
        element: Element = getattr(self.building, name)
        return element


@dataclass(frozen=True)
class Measure:
    """One catalogue measure of a package, as the request carries it.

    Args:
        id: The catalogue id.
        options: The option values by name, with a ``material`` option already built into a
            :class:`Material`.
    """

    id: str
    options: Mapping[str, Any]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Measure":
        """Build one measure from its request object, expanding a material option."""
        options: Dict[str, Any] = {}
        for name, value in (raw.get("options") or {}).items():
            options[str(name)] = (
                Material.from_dict(value) if name == CatalogueTable.MATERIAL and isinstance(value, Mapping)
                else value
            )
        return cls(id=str(raw["id"]), options=options)


@dataclass(frozen=True)
class Request:
    """One validated calculation request: where the house is, what it is, and what to do to it.

    Args:
        schema_version: The request schema version; ``1`` today.
        country: The country the dwelling stands in.
        postcode: Recorded in the mapping report; there is one weather station per country.
        house: The house as the request describes it, before the measures.
        measures: The package, in the order it is applied.
        document: The validated document itself, which ``apply`` deep-copies and the content
            hash is computed over.
    """

    schema_version: int
    country: Country
    postcode: Optional[str]
    house: House
    measures: Tuple[Measure, ...]
    document: Mapping[str, Any]

    @classmethod
    def parse(cls, document: Any) -> "Request":
        """Validate one document and return the typed request.

        Args:
            document: The parsed request, as JSON or YAML produced it.

        Returns:
            The typed request.

        Raises:
            RequestError: With every structural and semantic problem the document has. Semantic
                checks run only when the structure is sound, because a check reading a field the
                schema rejected would report the same fault twice in different words.
        """
        structural = SchemaProblems.of(document)
        if structural:
            raise RequestError(structural)
        semantic = SemanticChecks.of(document)
        if semantic:
            raise RequestError(semantic)
        location = document["location"]
        postcode = location.get("postcode")
        return cls(
            schema_version=int(document["schema_version"]),
            country=Country(location["country"]),
            postcode=None if postcode is None else str(postcode),
            house=House.from_dict(document["house"]),
            measures=tuple(Measure.from_dict(entry) for entry in document["measures"]),
            document=document,
        )

    def content_hash(self) -> str:
        """Return the first 16 hexadecimal characters of the SHA-256 of the canonical request.

        Canonical means object keys sorted recursively, no insignificant whitespace and UTF-8 --
        the same recipe the backend uses for the job id, so the energy-system file's name is
        recognisable in a job directory and two requests differing only in key order produce the
        same file.

        Returns:
            Sixteen lowercase hexadecimal characters.
        """
        import hashlib

        canonical = json.dumps(self.document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _member(vocabulary: Any, raw: Any) -> Any:
    """Return the member of *vocabulary* whose value is *raw*, or ``None`` when *raw* is absent."""
    return None if raw is None else vocabulary(raw)


def _boolean(raw: Any) -> Optional[bool]:
    """Return a boolean as itself, or ``None`` when the request did not carry the field."""
    return None if raw is None else bool(raw)


class SemanticChecks:
    """Everything a valid request must satisfy that the JSON Schema cannot say.

    The schema knows the shape of a measure entry but not the catalogue; it knows the range of
    ``electric_vehicles.number`` but not that a measure may raise it past two; it knows the three
    countries but not which of them has a TABULA typology. Those are the checks here, and each of
    them maps to exactly one code of §7 of the contract so that a frontend can act on the answer
    without reading the message.

    Every check runs, and every problem is collected: a request with a wrong country and two
    duplicate measures reports three problems, not one.
    """

    #: The most electric vehicles the request may end up with after the measures.
    MAXIMUM_VEHICLES: ClassVar[int] = 2

    #: The room set point the ``change_room_temperature`` measure must stay inside, which is the
    #: range the schema enforces on ``building.set_heating_temperature_in_celsius``.
    ROOM_TEMPERATURE_RANGE: ClassVar[Tuple[int, int]] = (12, 28)

    #: The measure whose option writes the room set point, and the option's name.
    ROOM_TEMPERATURE_MEASURE: ClassVar[Tuple[str, str]] = ("change_room_temperature", "new_room_temperature")

    #: The measure whose option writes the number of vehicles, and the option's name.
    VEHICLE_MEASURE: ClassVar[Tuple[str, str]] = ("electric_vehicle", "number")

    #: The per-measure price block of E-spec §7 and its two price fields. They are spelled here
    #: as well as on the translator's :class:`~hisim.renovisor.economics.EconomicContextBuilder`
    #: because the check has to run before anything is translated, and
    #: ``tests/renovisor/test_request.py`` pins the two spellings to each other.
    COST_KEY: ClassVar[str] = "cost"
    COST_MINIMUM_KEY: ClassVar[str] = "min_in_euro_per_m2"
    COST_MAXIMUM_KEY: ClassVar[str] = "max_in_euro_per_m2"

    @classmethod
    def of(cls, document: Mapping[str, Any]) -> Tuple[Problem, ...]:
        """Return every semantic problem of one structurally valid document.

        Args:
            document: A request the schema has already accepted.

        Returns:
            One :class:`Problem` per fault, in the order the checks run.
        """
        problems: List[Problem] = []
        house = document["house"]
        problems.extend(cls._country(document["location"]))
        problems.extend(cls._added_insulation(house))
        problems.extend(cls._hot_water_volumes(house))
        problems.extend(cls._measures(document["measures"], house))
        problems.extend(cls._cost_bands(document["measures"]))
        problems.extend(cls._tabula(document))
        return tuple(problems)

    @classmethod
    def _cost_bands(cls, measures: Sequence[Any]) -> List[Problem]:
        """Refuse a ``measures[i].cost`` block whose price band is not a price band.

        The frontend copies two euro-per-square-metre figures out of the contract's material table
        into the measure (E-spec §7), and the translator multiplies them by the element's area to
        get the subject's investment band. A minimum above the maximum, a negative price or a
        value that is not finite is a request problem and not something to price around: an
        inverted band is refused by :class:`~hisim.economics.uncertainty.UncertainValue` in the
        middle of a build, and a negative one would publish a renovation that pays the owner.

        Every measure is checked, so a request with two bad blocks reports two problems.

        Args:
            measures: The request's ``measures`` array, already known to be well shaped.

        Returns:
            One problem per measure whose block is present and unusable; an absent or incomplete
            block is not a problem, because the measure is then simply unpriced.
        """
        problems: List[Problem] = []
        for index, entry in enumerate(measures):
            block = entry.get(cls.COST_KEY) if isinstance(entry, Mapping) else None
            if not isinstance(block, Mapping):
                continue
            low = block.get(cls.COST_MINIMUM_KEY)
            high = block.get(cls.COST_MAXIMUM_KEY)
            if any(
                not isinstance(value, (int, float)) or isinstance(value, bool) for value in (low, high)
            ):
                continue
            minimum, maximum = float(low), float(high)  # type: ignore[arg-type]
            fault = cls._band_fault(minimum, maximum)
            if fault is None:
                continue
            problems.append(
                Problem(
                    path=f"measures[{index}].{cls.COST_KEY}",
                    code=ProblemCode.MEASURE_COST_BAND_INVALID,
                    message=(
                        f"'{entry.get('id')}' states a price band of {minimum} to {maximum} "
                        f"euro per square metre, and {fault}"
                    ),
                )
            )
        return problems

    @classmethod
    def _band_fault(cls, minimum: float, maximum: float) -> Optional[str]:
        """What is wrong with one price band, or ``None`` when nothing is.

        Args:
            minimum: The cheap end, in euro per square metre.
            maximum: The expensive end.

        Returns:
            The half-sentence the problem message ends with, or ``None``.
        """
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            return "a price has to be a finite number"
        if minimum < 0 or maximum < 0:
            return "a price cannot be negative"
        if minimum > maximum:
            return "the cheap end of a band cannot be above the expensive end"
        return None

    @classmethod
    def _country(cls, location: Mapping[str, Any]) -> List[Problem]:
        """Refuse a country the TABULA table has no ``.N.`` typology for."""
        from hisim.renovisor.tabula import TabulaIndex

        country = str(location["country"])
        if country in TabulaIndex.countries():
            return []
        return [
            Problem(
                path="location.country",
                code=ProblemCode.LOCATION_COUNTRY_UNSUPPORTED,
                message=(
                    f"'{country}' has no TABULA '.N.' building typology in the processed table, "
                    "so no archetype can be chosen for a dwelling there"
                ),
                accepted=tuple(sorted(TabulaIndex.countries() & {member.value for member in Country})),
            )
        ]

    @classmethod
    def _added_insulation(cls, house: Mapping[str, Any]) -> List[Problem]:
        """Refuse an ``added_insulation`` block the frontend sent; only ``apply`` may write one."""
        problems: List[Problem] = []
        building = house.get("building") or {}
        for name in ("roof", "facade", "floor"):
            element = building.get(name)
            if isinstance(element, Mapping) and element.get("added_insulation") is not None:
                problems.append(
                    Problem(
                        path=f"house.building.{name}.added_insulation",
                        code=ProblemCode.ADDED_INSULATION_NOT_ALLOWED,
                        message=(
                            "added_insulation is written by the translator when a measure adds a "
                            "layer; a request states the element's U-value instead"
                        ),
                    )
                )
        return problems

    @classmethod
    def _hot_water_volumes(cls, house: Mapping[str, Any]) -> List[Problem]:
        """Refuse two different volumes for the one hot-water storage the house has."""
        hot_water = house.get("hot_water") or {}
        solar = house.get("solar_thermal_system") or {}
        first = hot_water.get("volume_heating_water_storage_in_liter")
        second = solar.get("storage_volume_in_liter")
        if first is None or second is None or float(first) == float(second):
            return []
        return [
            Problem(
                path="house.solar_thermal_system.storage_volume_in_liter",
                code=ProblemCode.HOT_WATER_CONFLICTING_VOLUMES,
                message=(
                    f"the house has one hot-water storage and the request gives it two volumes: "
                    f"{first} l under house.hot_water and {second} l under "
                    "house.solar_thermal_system"
                ),
            )
        ]

    @classmethod
    def _measures(cls, measures: Sequence[Any], house: Mapping[str, Any]) -> List[Problem]:
        """Check every measure against the frozen catalogue table, and the two ranges a measure moves."""
        problems: List[Problem] = []
        seen: Dict[str, int] = {}
        for index, entry in enumerate(measures):
            measure_id = str(entry["id"])
            path = f"measures[{index}]"
            if measure_id not in CatalogueTable.BY_ID:
                problems.append(
                    Problem(
                        path=f"{path}.id",
                        code=ProblemCode.MEASURE_UNKNOWN,
                        message=f"'{measure_id}' is not a measure of the catalogue",
                        accepted=CatalogueTable.ids(),
                    )
                )
                continue
            if measure_id in seen:
                problems.append(
                    Problem(
                        path=f"{path}.id",
                        code=ProblemCode.MEASURE_DUPLICATE,
                        message=(
                            f"'{measure_id}' appears twice, at measures[{seen[measure_id]}] and "
                            f"at {path}; one package applies each measure at most once"
                        ),
                    )
                )
                continue
            seen[measure_id] = index
            problems.extend(cls._options(measure_id, entry.get("options") or {}, path))
        problems.extend(cls._ranges(measures, house))
        return problems

    @classmethod
    def _options(cls, measure_id: str, options: Mapping[str, Any], path: str) -> List[Problem]:
        """Check one measure's options: unknown names, missing ``everyone`` options, bad values."""
        problems: List[Problem] = []
        specs = {option.name: option for option in CatalogueTable.options_of(measure_id)}
        for name, value in options.items():
            spec = specs.get(str(name))
            if spec is None:
                problems.append(
                    Problem(
                        path=f"{path}.options.{name}",
                        code=ProblemCode.MEASURE_OPTION_UNKNOWN,
                        message=f"'{measure_id}' has no option '{name}'",
                        accepted=tuple(specs),
                    )
                )
                continue
            problems.extend(cls._value(measure_id, spec, value, f"{path}.options.{name}"))
        for spec in specs.values():
            if spec.access_level is AccessLevel.EVERYONE and spec.name not in options:
                problems.append(
                    Problem(
                        path=f"{path}.options.{spec.name}",
                        code=ProblemCode.MEASURE_OPTION_MISSING,
                        message=(
                            f"'{spec.name}' is an 'everyone' option of '{measure_id}' and every "
                            "request that carries the measure has to state it"
                        ),
                    )
                )
        return problems

    @classmethod
    def _value(cls, measure_id: str, spec: OptionSpec, value: Any, path: str) -> List[Problem]:
        """Check one option value against its declared type and, where it has one, its value list."""
        if spec.name == CatalogueTable.MATERIAL:
            return cls._material(value, path)
        if spec.value_type is ValueType.BOOLEAN:
            return cls._of_type(measure_id, spec, value, path, bool, "a boolean")
        if spec.values is not None and value not in spec.values:
            return [
                Problem(
                    path=path,
                    code=ProblemCode.MEASURE_OPTION_VALUE_UNKNOWN,
                    message=f"'{value}' is not a value of '{spec.name}' of '{measure_id}'",
                    accepted=spec.values,
                )
            ]
        if spec.value_type is ValueType.INTEGER:
            return cls._of_type(measure_id, spec, value, path, int, "an integer")
        if spec.value_type is ValueType.NUMBER:
            return cls._of_type(measure_id, spec, value, path, (int, float), "a number")
        return []

    @classmethod
    def _of_type(
        cls,
        measure_id: str,
        spec: OptionSpec,
        value: Any,
        path: str,
        accepted: Union[type, Tuple[type, ...]],
        description: str,
    ) -> List[Problem]:
        """Report one ``TYPE_INVALID`` problem when a value is not of the type its option declares.

        Args:
            measure_id: The catalogue id of the measure the option belongs to, for the message.
            spec: The option's specification; only its name reaches the message.
            value: The value the request sent.
            path: The request path the problem is reported against.
            accepted: The Python type, or tuple of types, that satisfies the declared type.
            description: The declared type as the message spells it, e.g. ``"an integer"``.

        Returns:
            An empty list when the value is of the accepted type, otherwise the one problem.
        """
        if isinstance(value, accepted):
            return []
        return [
            Problem(
                path=path,
                code=ProblemCode.TYPE_INVALID,
                message=f"'{spec.name}' of '{measure_id}' is {description} and the request sends {value!r}",
            )
        ]

    @classmethod
    def _material(cls, value: Any, path: str) -> List[Problem]:
        """Check the one property of a material the physics needs: a positive conductivity.

        The ``asp_id`` is provenance and is not looked up anywhere (rule 5 of the contract), so
        a material the database has never heard of is accepted with its numbers.
        """
        if not isinstance(value, Mapping):
            return [
                Problem(
                    path=path,
                    code=ProblemCode.TYPE_INVALID,
                    message="a material option carries the material's properties as an object",
                )
            ]
        conductivity = value.get("thermal_conductivity_w_mk")
        if isinstance(conductivity, (int, float)) and not isinstance(conductivity, bool) and conductivity > 0:
            return []
        return [
            Problem(
                path=f"{path}.thermal_conductivity_w_mk",
                code=ProblemCode.TYPE_INVALID,
                message=(
                    "the material's thermal conductivity has to be a positive number of W/(m·K); "
                    f"the request sends {conductivity!r}"
                ),
            )
        ]

    @classmethod
    def _ranges(cls, measures: Sequence[Any], house: Mapping[str, Any]) -> List[Problem]:
        """Refuse the two values a measure can push outside the range the request schema enforces."""
        problems: List[Problem] = []
        vehicle_id, vehicle_option = cls.VEHICLE_MEASURE
        room_id, room_option = cls.ROOM_TEMPERATURE_MEASURE
        low, high = cls.ROOM_TEMPERATURE_RANGE
        for index, entry in enumerate(measures):
            options = entry.get("options") or {}
            path = f"measures[{index}].options"
            if str(entry["id"]) == vehicle_id:
                number = options.get(vehicle_option)
                if isinstance(number, int) and not isinstance(number, bool) and number > cls.MAXIMUM_VEHICLES:
                    problems.append(
                        Problem(
                            path=f"{path}.{vehicle_option}",
                            code=ProblemCode.RANGE_EXCEEDED,
                            message=(
                                f"a dwelling charges at most {cls.MAXIMUM_VEHICLES} electric cars; "
                                f"the measure asks for {number}"
                            ),
                        )
                    )
            if str(entry["id"]) == room_id:
                temperature = options.get(room_option)
                if isinstance(temperature, (int, float)) and not low <= temperature <= high:
                    problems.append(
                        Problem(
                            path=f"{path}.{room_option}",
                            code=ProblemCode.RANGE_EXCEEDED,
                            message=(
                                f"the room set point has to be between {low} and {high} degrees "
                                f"Celsius; the measure asks for {temperature}"
                            ),
                        )
                    )
        del house
        return problems

    @classmethod
    def _tabula(cls, document: Mapping[str, Any]) -> List[Problem]:
        """Refuse a dwelling the TABULA index cannot place, which no later step could recover from."""
        from hisim.renovisor.tabula import BuildingCodeSelector, TabulaUnresolvable

        building = document["house"]["building"]
        country = str(document["location"]["country"])
        from hisim.renovisor.tabula import TabulaIndex

        if country not in TabulaIndex.countries():
            return []
        try:
            BuildingCodeSelector.select(
                country=country,
                building_type=BuildingType(building["building_type"]),
                construction_year=int(building["construction_year"]),
                requested_code=building.get("tabula_building_code"),
            )
        except TabulaUnresolvable as error:
            return [
                Problem(
                    path="house.building.tabula_building_code"
                    if building.get("tabula_building_code") is not None
                    else "house.building.construction_year",
                    code=ProblemCode.TABULA_UNRESOLVABLE,
                    message=str(error),
                )
            ]
        return []
