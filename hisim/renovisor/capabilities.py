"""The capability document: what this build of the translator does with the whole catalogue.

The mapping report says what one run did with one request. This document says what *any* run
would do with *any* request, and it is produced the same way: by running the translator, over a
probe set instead of over one request, and aggregating the report lines per measure, per option
and per inventory field. Because it is produced by running the translator, it cannot announce
something the translator does not do::

    python -m hisim.renovisor capabilities --out capabilities.json

The probe set is data, not a loop (:class:`ProbeSet`), so the verification harness can reuse it.
It is the anchor -- the vendored mockup with an empty package -- plus the bare baseline with
every optional block and every element U-value absent, plus one probe per optional block present alone, one per measure,
one per enum value of every option, one per boundary of every free numeric option, and one per
value of every settable leaf of the request schema -- the inventory, the location, the applicant
and a measure's cost band, each enum value, both booleans and both ends of a range -- plus the
handful of two-change probes the conditional entries of ``not_implemented_yet.yaml`` need (a
solar thermal collector on an oil boiler cannot be reached by changing one thing).

Every probe runs ``validate`` + ``apply`` + ``translate`` and no simulation, so the whole set
takes a second. A probe that raises a translator error fails the build: the backend marks an
image ``broken`` on that, and the point of the document is that it cannot.

The document's shape is ``measure-capabilities.openapi.yaml``'s
``components.schemas.ImplementedMeasures``, and :meth:`CapabilityDocument.validate` checks it
against that vendored schema before writing. The schema is strict since 0.3.0 (2026-09-23): every
key written here is declared there and no other key is admitted, so a key this module starts to
emit fails the build until the shared spec declares it. 0.4.0 (2026-09-24) added a numeric field's
``exclusiveMinimum``/``exclusiveMaximum``.

One section is not aggregated from probes at all. ``results`` (:class:`ResultsSection`) says what
the *answer* will look like -- every field ``result.json`` can carry, its source and its
provenance -- generated from the same two tables the payload is built from. Its schema,
``ResultFields``, was HiSim's own proposal until 2026-09-23 and is part of the shared schema now.
"""

import copy
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, cast

import yaml
from jsonschema import Draft202012Validator

from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.apply import apply
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.costs import CostSchema
from hisim.renovisor.kpis import KpiSchema
from hisim.renovisor.report import HiSimCommit
from hisim.renovisor.request import (
    AccessLevel,
    CatalogueTable,
    OptionSpec,
    Request,
    RequestError,
    SemanticChecks,
    ValueType,
)
from hisim.renovisor.tabula import ArchetypeEnvelope
from hisim.renovisor.translate import TranslatedSystem, Translator
from hisim.renovisor.vocabulary import ReportStatus
from hisim.renovisor.whitelist import Whitelist


class MeasureStatus(str, Enum):
    """The measure-level vocabulary of the capability document.

    It is the mapping report's, with ``used`` spelled ``supported``: that is the word
    ``measure-capabilities.openapi.yaml`` fixes for ``ImplementedMeasure.status``, and the
    document has to validate against it. Option and value statuses keep the report's own
    spelling, which the same schema also fixes.
    """

    SUPPORTED = "supported"
    APPROXIMATED = "approximated"
    NOT_IMPLEMENTED_YET = "not_implemented_yet"

    @classmethod
    def of(cls, status: ReportStatus) -> "MeasureStatus":
        """Return the document's word for one mapping-report status."""
        if status is ReportStatus.NOT_IMPLEMENTED_YET:
            return cls.NOT_IMPLEMENTED_YET
        if status is ReportStatus.USED:
            return cls.SUPPORTED
        return cls.APPROXIMATED


class ProbeKind(str, Enum):
    """What one probe is for, which decides what its result is aggregated into.

    ``ANCHOR`` and ``BARE`` prove the two ends of the sparseness rule: a request with a package
    and a request with nothing optional in it at all -- no optional block, no roof shape and no
    element U-value. ``BLOCK`` turns one optional block on;
    ``MEASURE`` applies one measure; ``OPTION`` varies one option of one measure; ``FIELD``
    varies one leaf of the request -- an inventory field, the country, an applicant answer or a
    bound of a measure's cost band; ``PAIR`` changes two things at once, which the conditional
    entries of the list need.
    """

    ANCHOR = "anchor"
    BARE = "bare"
    BLOCK = "block"
    MEASURE = "measure"
    OPTION = "option"
    FIELD = "field"
    PAIR = "pair"


@dataclass(frozen=True)
class Probe:
    """One request the document is aggregated from, as a patch on the anchor.

    Args:
        name: A stable identity, e.g. ``option:heating_system.type_of_system=hybrid_heat_pump``.
        kind: What the probe is for.
        house: What to write into the anchor's ``house``, by dotted path; ``None`` as a value
            removes the block.
        measures: The package this probe sends, or ``None`` to keep the anchor's empty one.
        location: What to write into the anchor's ``location``, by key.
        subject: The measure id or request path the probe is about, for the aggregation.
        value: The value it set, for a per-value status.
        applicant: What to write into the anchor's ``applicant``, by key.
    """

    name: str
    kind: ProbeKind
    house: Mapping[str, Any] = field(default_factory=dict)
    measures: Optional[Sequence[Mapping[str, Any]]] = None
    location: Mapping[str, Any] = field(default_factory=dict)
    subject: Optional[str] = None
    value: Any = None
    applicant: Mapping[str, Any] = field(default_factory=dict)

    def document(self, anchor: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the request this probe sends.

        Args:
            anchor: The anchor request, which is never mutated.

        Returns:
            A deep copy of the anchor with this probe's changes applied. Every value is copied
            in as well: a field probe's prelude block is one dictionary shared by every value
            probe of that field, and writing the field into the block itself would otherwise
            change the prelude of every sibling (found by the path-verification harness, whose
            bases are the preludes).
        """
        request = copy.deepcopy(dict(anchor))
        for path, value in self.house.items():
            _write(request["house"], path, copy.deepcopy(value))
        for key, value in self.location.items():
            _write(request["location"], key, copy.deepcopy(value))
        for key, value in self.applicant.items():
            _write(request["applicant"], key, copy.deepcopy(value))
        if self.measures is not None:
            request["measures"] = [copy.deepcopy(dict(entry)) for entry in self.measures]
        return request


def _write(house: Dict[str, Any], path: str, value: Any) -> None:
    """Write one dotted path into a house, creating blocks on the way and removing on ``None``."""
    parts = path.split(".")
    current = house
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if value is None:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = value


class RequestSchemaBounds:
    """The bounds a numeric inventory field publishes, read from the request schema itself.

    The shared ``measure-capabilities.openapi.yaml`` describes a field's ``minimum``/``maximum`` as
    the request schema's own bounds, so they are looked up there when the document is built rather
    than copied into a table beside it (decision of 2026-09-24, PR #807). Each bound is published
    under the schema's own keyword: an inclusive one as ``minimum``/``maximum``, an exclusive one as
    ``exclusiveMinimum``/``exclusiveMaximum`` (measure-capabilities 0.4.0, renovisorissues !18),
    and an end the schema leaves open publishes nothing -- never a probe point standing in for it.
    The schema is walked by :class:`SchemaLeaves`, the one walker over it.
    """

    #: The four bound keywords of JSON Schema, which are also the document's keys.
    KEYWORDS: ClassVar[Tuple[str, ...]] = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")

    @classmethod
    def of(cls, path: str, schema: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Return the bounds one request path publishes.

        Args:
            path: The dotted request path, ``house.`` included, e.g. ``house.building.construction_year``.
            schema: The request schema; the vendored ``calculation-request.schema.json`` when omitted.

        Returns:
            ``{"minimum": ..., "exclusiveMaximum": ...}`` and so on, each of the four keys present
            exactly when the schema declares that bound.

        Raises:
            KeyError: When the schema has no such path, which means the probe table names a
                field the request cannot carry.
            ValueError: When two alternatives of the leaf declare the same bound differently.
        """
        root = schema if schema is not None else ContractFiles.request_schema()
        node: Mapping[str, Any] = root
        for part in path.split("."):
            child = next(
                (
                    candidate["properties"][part]
                    for candidate in SchemaLeaves.candidates(root, node, items=True)
                    if part in candidate.get("properties", {})
                ),
                None,
            )
            if child is None:
                raise KeyError(f"the request schema has no field {path!r} (stuck at {part!r})")
            node = child
        declared: Dict[str, Any] = {}
        for candidate in SchemaLeaves.candidates(root, node, items=True):
            for keyword in cls.KEYWORDS:
                if keyword not in candidate:
                    continue
                if keyword in declared and declared[keyword] != candidate[keyword]:
                    raise ValueError(f"{path}: the schema declares {keyword} both {declared[keyword]} and "
                                     f"{candidate[keyword]}")
                declared[keyword] = candidate[keyword]
        return {keyword: declared[keyword] for keyword in cls.KEYWORDS if keyword in declared}


class SchemaLeaves:
    """The one walker over the vendored request JSON Schema: local ``$ref``, alternatives, properties.

    It enumerates every settable leaf of a request (:meth:`settable`), which two readers need: the
    probe set, which generates a probe for every leaf its tables do not name
    (:meth:`ProbeSet.varied_fields`), and the path-verification harness, which checks that some probe
    changes each of them (:class:`hisim.renovisor.verify.probes.Completeness`). :class:`RequestSchemaBounds`
    looks one path's bounds up with it.
    """

    #: The keywords whose alternatives are merged into one node.
    ALTERNATIVES: ClassVar[Tuple[str, ...]] = ("oneOf", "anyOf", "allOf")

    #: The request blocks whose leaves are walked.
    BLOCKS: ClassVar[Tuple[str, ...]] = ("location", "house", "applicant")

    #: How the cost block of any measure is spelled, since it is the same leaf on every measure.
    COST_PATH: ClassVar[str] = "measures[id=*].cost"

    @staticmethod
    def resolve(reference: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return the subschema a local ``$ref`` (``#/$defs/...``) points at.

        Raises:
            ValueError: For a reference outside the document, which the vendored schema has none of.
        """
        if not reference.startswith("#"):
            raise ValueError(f"only local references are resolved, not {reference!r}")
        target: Any = root
        for token in reference.lstrip("#").strip("/").split("/"):
            if token:
                target = target[token.replace("~1", "/").replace("~0", "~")]
        return cast(Mapping[str, Any], target)

    @classmethod
    def candidates(
        cls, root: Mapping[str, Any], node: Mapping[str, Any], items: bool = False
    ) -> List[Mapping[str, Any]]:
        """Return *node* and every subschema it stands for: ``$ref`` targets and alternatives.

        Args:
            root: The whole schema, which references are resolved in.
            node: The subschema.
            items: Whether an array's ``items`` stands for the array as well, which a path lookup
                that runs through a list wants and the leaf enumeration, which stops at a list, does not.

        Returns:
            The subschemas, breadth first, *node* itself first, each once.
        """
        found: List[Mapping[str, Any]] = []
        pending: List[Mapping[str, Any]] = [node]
        while pending:
            current = pending.pop(0)
            if any(current is seen for seen in found):
                continue
            found.append(current)
            if "$ref" in current:
                pending.append(cls.resolve(str(current["$ref"]), root))
            for keyword in cls.ALTERNATIVES:
                pending.extend(current.get(keyword, []))
            if items and isinstance(current.get("items"), Mapping):
                pending.append(current["items"])
        return found

    @classmethod
    def properties(cls, root: Mapping[str, Any], node: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
        """Return the union of the properties every alternative of *node* declares."""
        merged: Dict[str, Mapping[str, Any]] = {}
        for candidate in cls.candidates(root, node):
            merged.update(candidate.get("properties", {}))
        return merged

    @classmethod
    def merged(cls, root: Mapping[str, Any], node: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the keywords of every alternative of a leaf, merged into one mapping."""
        merged: Dict[str, Any] = {}
        for candidate in cls.candidates(root, node):
            merged.update(
                {key: value for key, value in candidate.items() if key not in cls.ALTERNATIVES and key != "$ref"}
            )
        return merged

    @classmethod
    def walk(cls, root: Mapping[str, Any], node: Mapping[str, Any], prefix: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Return ``(path, leaf keywords)`` for every leaf under *node*.

        An object recurses into its declared properties; anything else is a leaf.
        """
        properties = cls.properties(root, node)
        if not properties:
            return [(prefix, cls.merged(root, node))]
        leaves: List[Tuple[str, Dict[str, Any]]] = []
        for name, child in properties.items():
            leaves.extend(cls.walk(root, child, f"{prefix}.{name}"))
        return leaves

    @classmethod
    def settable(cls, schema: Optional[Mapping[str, Any]] = None) -> List[Tuple[str, Dict[str, Any]]]:
        """Return every leaf a request can set, in schema order.

        Args:
            schema: The request schema; the vendored one when omitted.

        Returns:
            ``(path, leaf keywords)`` for every leaf of ``location``, ``house`` and ``applicant``,
            then for every leaf of a measure's ``cost`` block, spelled :attr:`COST_PATH` ``.<name>``.
            A ``const`` leaf (``schema_version``) is not among them; the measures and their options
            are the catalogue's, not the schema's.
        """
        root = schema if schema is not None else ContractFiles.request_schema()
        leaves: List[Tuple[str, Dict[str, Any]]] = []
        for block in cls.BLOCKS:
            leaves.extend(cls.walk(root, root["properties"][block], block))
        measure = cls.properties(root, root["properties"]["measures"]["items"])
        for name, node in cls.properties(root, measure["cost"]).items():
            leaves.append((f"{cls.COST_PATH}.{name}", cls.merged(root, node)))
        return leaves

    @staticmethod
    def types(leaf: Mapping[str, Any]) -> Set[str]:
        """Return the JSON types a leaf declares, as a set whether the schema lists one or several."""
        declared = leaf.get("type")
        if isinstance(declared, list):
            return {str(item) for item in declared}
        return {str(declared)} if declared is not None else set()


class FieldShape(str, Enum):
    """What a varied request leaf is, which decides what the capability document publishes for it.

    ``ENUMERATED`` (an enum or a boolean) publishes one ``values`` entry per value; ``NUMERIC``
    publishes the request schema's own bounds (:class:`RequestSchemaBounds`) and never its probe
    points; ``FREE`` (a string such as the TABULA override) publishes neither, and its probes count
    towards the field's own status.
    """

    ENUMERATED = "enumerated"
    NUMERIC = "numeric"
    FREE = "free"


class MaterialRows:
    """The request's material object, derived from a real row of the vendored ``materials.yaml``.

    The rule is the frontend's, written in ``specs/calculation-request.md`` §4.3 (last paragraph, at
    renovisorissues@cc54813): the material object is filled from the ``materials.yaml`` row named by
    the option's ``asp_id``, scalars verbatim, a ``{min, max}`` range as its midpoint, and an
    open-ended range (``max: .inf``) has no midpoint, so the property is omitted. The properties are
    those of the request schema's ``$defs.material``, whose names are the file's verbatim; a property
    the row does not state is omitted as well. A row without a finite conductivity, the one property
    the schema requires beside ``asp_id``, has no material object.

    Applied to the ``eps_rigid_board`` row it reproduces the vendored mockup's material exactly
    (density 11-30 -> 20.5, lifespan 40-75 -> 57.5, the rest verbatim), which
    ``tests/renovisor/test_capabilities.py`` holds it to.

    For the probe set only (owner decision 2026-09-26, hisim-8mjc): the translator reads no catalogue
    (rule 5), and a request's material is whatever the frontend sent.
    """

    #: The request schema's definition of the material object.
    DEFINITION: ClassVar[str] = "material"

    #: The row's id, which the object carries as provenance.
    ID: ClassVar[str] = "asp_id"

    #: The property the schema requires beside the id.
    CONDUCTIVITY: ClassVar[str] = "thermal_conductivity_w_mk"

    @classmethod
    def properties(cls, schema: Optional[Mapping[str, Any]] = None) -> Tuple[str, ...]:
        """Return the physical properties the object carries, in the schema's order."""
        definition = (schema if schema is not None else ContractFiles.request_schema())["$defs"][cls.DEFINITION]
        return tuple(name for name in definition["properties"] if name != cls.ID)

    @classmethod
    def rows(cls, materials: Optional[Mapping[str, Any]] = None) -> List[Mapping[str, Any]]:
        """Return the rows of ``materials.yaml`` in file order; the vendored file when omitted."""
        return list((materials if materials is not None else ContractFiles.materials())["materials"])

    @classmethod
    def row(cls, asp_id: str, materials: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        """Return the row with one ``asp_id``.

        Raises:
            KeyError: When no row has it.
        """
        for row in cls.rows(materials):
            if row.get(cls.ID) == asp_id:
                return row
        raise KeyError(f"{ContractFiles.MATERIALS_FILENAME} has no row {asp_id!r}")

    @classmethod
    def material_object(
        cls, row: Mapping[str, Any], schema: Optional[Mapping[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Return the material object a request sends for one row, or ``None`` when it can send none.

        Args:
            row: One row of ``materials.yaml``.
            schema: The request schema; the vendored one when omitted.

        Returns:
            ``asp_id`` and every property the rule gives a number, in the schema's order; ``None``
            when the conductivity is not among them.

        Raises:
            ValueError: When a property is neither a number nor a ``{min, max}`` range of numbers.
        """
        material: Dict[str, Any] = {cls.ID: row[cls.ID]}
        for name in cls.properties(schema):
            if name in row:
                value = cls.value(row[name], f"{row[cls.ID]}.{name}")
                if value is not None:
                    material[name] = value
        return material if cls.CONDUCTIVITY in material else None

    @classmethod
    def value(cls, raw: Any, where: str) -> Optional[float]:
        """Return one property's number: a scalar verbatim, a range's midpoint, ``None`` for an open range.

        Raises:
            ValueError: When *raw* is neither a finite number nor a ``{min, max}`` mapping of numbers;
                *where* names the row and property.
        """
        if isinstance(raw, Mapping):
            ends = (raw.get("min"), raw.get("max"))
            if set(raw) != {"min", "max"} or not all(cls._is_number(end) for end in ends):
                raise ValueError(f"{where} is a range {dict(raw)!r}, not {{min, max}} of numbers")
            low, high = cast(Tuple[float, float], ends)
            return (low + high) / 2 if math.isfinite(low) and math.isfinite(high) else None
        if cls._is_number(raw) and math.isfinite(raw):
            return cast(float, raw)
        raise ValueError(f"{where} is {raw!r}, neither a finite number nor a {{min, max}} range")

    @staticmethod
    def _is_number(value: Any) -> bool:
        """Return whether *value* is an int or a float and not a boolean."""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def alternative(cls, measure_id: str, base: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a second real material for one measure, or ``None`` when the file has none.

        It is the first row in file order -- the order the catalogue's reverse lookup lists a
        measure's ``material`` values in -- whose ``measures`` name the measure, which yields a
        material object, and whose conductivity differs from *base*'s, so a probe that sends it in
        place of *base* changes the element's U-value. File order rather than, say, the most
        different conductivity, because it depends on nothing but the rows' order and picks an
        ordinary material rather than the file's outlier.

        Args:
            measure_id: A catalogue id whose ``material`` option is probed.
            base: The material object the probe's base sends.
        """
        for row in cls.rows():
            if measure_id not in (row.get("measures") or []):
                continue
            material = cls.material_object(row)
            if material is not None and material[cls.CONDUCTIVITY] != base.get(cls.CONDUCTIVITY):
                return material
        return None


class ProbeSet:
    """The probes the capability document is aggregated from, as data.

    Everything here is generated from a few tables, the frozen catalogue and the request schema,
    so a catalogue value added tomorrow is probed tomorrow without anybody writing a probe, and so
    is a request field. The measure side reads the bounds of the free numeric options
    (:attr:`OPTION_BOUNDS`) and the points of those the schema bounds only exclusively or not at
    all (:attr:`OPTION_PROBE_POINTS`, never published). The request side varies *every* settable
    leaf of the request schema (:meth:`varied_fields`): the inventory fields of
    :attr:`FIELD_VALUES` and :attr:`FIELD_PROBE_POINTS` at the values chosen there, and every other
    leaf at the values the schema itself decides -- each enum value, both booleans, an inclusive
    bound, a point just inside an exclusive one -- or, where it decides none, at the points of
    :attr:`OPEN_END_POINTS` and :attr:`FREE_VALUES`. A leaf that no table and no bound decides stops
    the build by name. The bounds a numeric field publishes are not in any of the tables: they are
    read from the request schema when the document is built (:class:`RequestSchemaBounds`).
    """

    #: The mockup measure whose ``material`` option is the row every probe carries as its material
    #: (:meth:`material`); a probe of a ``material`` option sends a second real row instead.
    MATERIAL_MEASURE: ClassVar[str] = "external_insulation"

    #: What a field probe has to change first so that the field is legal at all: a rated SCOP is
    #: only accepted on a heat pump (``heating.scop.not_a_heat_pump``), and the anchor heats with gas.
    #: A battery sized by its capacity replaces the block's ``days_to_cover`` (``BLOCKS``) rather
    #: than joining it, since the schema's ``house.battery`` is sized by exactly one of the two.
    FIELD_PRELUDE: ClassVar[Dict[str, Dict[str, Any]]] = {
        "heating.heatpump_scop_en14825_w35": {"heating.type_of_system": "air_source_heat_pump"},
        "heating.heatpump_scop_en14825_w55": {"heating.type_of_system": "air_source_heat_pump"},
        "battery.custom_battery_capacity_generic_in_kilowatt_hour": {
            "battery": {"custom_battery_capacity_generic_in_kilowatt_hour": 10},
        },
    }

    #: The ``added_insulation`` layer a probe of one of its leaves starts from, per element: the
    #: placement that belongs to the element, :attr:`LAYER_THICKNESS_IN_MM` and the mockup's
    #: material (:meth:`material`). The schema admits the block in a request although only
    #: ``apply`` may write one (owner decision of 2026-09-26: probe it as a request field), so every
    #: such probe is refused by ``added_insulation.not_allowed``, which is what it is there to show.
    LAYER_PLACEMENT: ClassVar[Dict[str, str]] = {
        "roof": "roof_between_rafter",
        "facade": "external_wall_external",
        "floor": "basement_ceiling",
    }

    #: The thickness of the layer :attr:`LAYER_PLACEMENT` describes.
    LAYER_THICKNESS_IN_MM: ClassVar[int] = 100

    #: The measure whose ``cost`` block the probes of a cost leaf vary. It is an envelope measure,
    #: the only kind whose band HiSim reads, on the mockup's facade, which states its area.
    COST_MEASURE: ClassVar[str] = "external_insulation"

    #: The band a probe of a cost leaf starts from, per leaf, so that every probe keeps the cheap end
    #: at or below the expensive one (``measure.cost.band_invalid``) while it moves one of them to
    #: either end of :attr:`OPEN_END_POINTS`; any other leaf of the block starts from ``""``'s.
    COST_BANDS: ClassVar[Dict[str, Dict[str, Any]]] = {
        "": {"min_in_euro_per_m2": 50, "max_in_euro_per_m2": 70, "source": "the capability probe set"},
        "min_in_euro_per_m2": {
            "min_in_euro_per_m2": 50, "max_in_euro_per_m2": 1000, "source": "the capability probe set",
        },
        "max_in_euro_per_m2": {
            "min_in_euro_per_m2": 0, "max_in_euro_per_m2": 70, "source": "the capability probe set",
        },
    }

    #: The prefix a probe's subject carries in front of an inventory path.
    HOUSE_PREFIX: ClassVar[str] = "house."

    #: The five envelope elements, each of whose U-value the bare probe leaves out: since the retrofit
    #: status (calculation-request §3.4) an element the request does not describe takes the TABULA
    #: row's, and that default is part of what "nothing optional" means.
    ELEMENTS: ClassVar[Tuple[str, ...]] = ("roof", "facade", "floor", "window", "door")

    #: The optional blocks a request may carry, each with the smallest body the schema accepts.
    BLOCKS: ClassVar[Dict[str, Dict[str, Any]]] = {
        "hot_water": {"supply": "together_with_heating_system"},
        "ventilation": {"type_of_system": "natural", "air_tightness": "as_built"},
        "temperature_control": {"type_of_system": "traditional_thermostats"},
        "air_conditioning": {"power_in_watt": 3000},
        "appliances": {"white_appliances": "existing"},
        "pv_system": {"size_in_percent_of_roof_area": 50},
        # The request schema's house.battery is sized by exactly one of a capacity or days_to_cover.
        "battery": {"days_to_cover": 2},
        "solar_thermal_system": {"supplies": "dhw_only"},
        "electric_vehicles": {"number": 1},
    }

    #: Enumerated inventory paths worth varying -- enums, booleans and the numeric enums such as
    #: the glazing-pane counts and the three ``electric_vehicles`` fields -- each with the
    #: request schema's own list. Every value is probed, and the document publishes one
    #: ``values`` entry per value. A numeric field with a range is in :attr:`FIELD_PROBE_POINTS`
    #: instead, and a path is in exactly one of the two (:meth:`varied_fields`).
    FIELD_VALUES: ClassVar[Dict[str, Tuple[Any, ...]]] = {
        "building.building_type": (
            "detached_sfh", "semi_detached_sfh", "terraced_sfh", "bungalow", "apartment", "other",
        ),
        "building.roof.shape": ("pitched", "flat"),
        "building.window.glazing_panes": (1, 2, 3),
        "building.window.frame_material": ("wood", "plastic", "metal", "composite"),
        "building.window.low_emissivity_coating": (True, False),
        "building.window.outside_shading": (True, False),
        "building.window.thermocover": (True, False),
        "building.door.glazing_panes": (0, 2, 3),
        "building.door.frame_material": ("wood", "plastic", "metal", "composite"),
        "occupancy.pv_self_consumption_optimised": (True, False),
        "heating.type_of_system": (
            "air_source_heat_pump", "ground_source_heat_pump", "hybrid_heat_pump",
            "electric_heating", "biomass_heating", "pellet_heating", "woodchip_heating",
            "district_heating", "hvo_heating", "hydrogen_heating", "conventional_gas_heating",
            "conventional_oil_heating", "conventional_lpg_heating", "condensing_gas_heating",
            "condensing_oil_heating", "condensing_lpg_heating", "solid_fuel_heating",
        ),
        "heating.cooking_range": (True, False),
        "heating.secondary": ("open_fireplace", "wood_stove", "electric_heater"),
        "heat_distribution.type_of_system": (
            "surface_heating", "low_temperature_radiator", "conventional_radiator",
        ),
        "hot_water.supply": (
            "together_with_heating_system", "separate_heat_pump", "separate_direct_electric",
        ),
        "hot_water.tank_and_pipe_insulated": (True, False),
        "ventilation.type_of_system": (
            "natural", "window_trickle_vent", "mechanical_extract", "demand_controlled_extract",
            "mechanical_ventilation_with_heat_recovery",
        ),
        "ventilation.air_tightness": ("as_built", "diy_sealed", "professionally_sealed"),
        "temperature_control.type_of_system": ("traditional_thermostats", "smart_heating_control_system"),
        "appliances.white_appliances": ("existing", "new_efficient"),
        "solar_thermal_system.supplies": ("dhw_only", "dhw_and_space_heating"),
        "solar_thermal_system.collector_type": ("flat_plate", "evacuated_tube"),
        "electric_vehicles.number": (1, 2),
        "electric_vehicles.commuting_distance_in_km": (5, 10, 15, 20, 25, 30),
        "electric_vehicles.charging_power_in_watt": (3700, 11000, 22000),
    }

    #: Numeric inventory paths worth varying, each with the low and the high value HiSim probes
    #: it at. Where the request schema's bound is inclusive the point is the bound itself; where
    #: it is exclusive (``exclusiveMinimum: 0``) the point lies inside it; where the schema has no
    #: maximum it is a representative large value. These are probe points and nothing else: the
    #: document never publishes them. A field publishes the schema's own bounds, inclusive and
    #: exclusive (:class:`RequestSchemaBounds`), and folds the probes' statuses into its own (decision of
    #: 2026-09-23: ``values[]`` is for enumerations only; of 2026-09-24: bounds come from the
    #: schema). ``tests/renovisor/test_capabilities.py`` keeps every point inside the schema.
    FIELD_PROBE_POINTS: ClassVar[Dict[str, Tuple[float, float]]] = {
        "building.construction_year": (1700, 2100),
        "building.absolute_conditioned_floor_area_in_m2": (1, 400),
        "building.number_of_storeys": (1, 6),
        "building.set_heating_temperature_in_celsius": (12, 28),
        "building.roof.u_value_in_watt_per_m2_per_kelvin": (0.01, 10),
        "building.roof.area_in_m2": (0, 500),
        "building.facade.area_in_m2": (0, 500),
        "building.floor.area_in_m2": (0, 500),
        "building.window.area_in_m2": (0, 500),
        "building.door.area_in_m2": (0, 500),
        "occupancy.number_of_residents": (1, 12),
        "occupancy.home_office_days_per_week": (0, 7),
        "heating.flow_temperature_in_celsius": (20, 90),
        "heating.seasonal_efficiency_in_percent": (1, 400),
        "hot_water.volume_heating_water_storage_in_liter": (0, 2000),
        "air_conditioning.power_in_watt": (0, 50000),
        "air_conditioning.installation_year": (1900, 2100),
        # The block carries its roof share beside the power (both are allowed since the shared
        # schema of 2026-09-25); the power's lower bound is exclusive.
        "pv_system.power_in_watt": (100, 100000),
        "pv_system.size_in_percent_of_roof_area": (1, 100),
        "pv_system.azimuth": (0, 360),
        "pv_system.tilt": (0, 90),
        "pv_system.shading_losses_in_percent": (0, 100),
        "pv_system.installation_year": (1900, 2100),
        "building.living_area_in_m2": (30, 400),
        "building.roof.installation_year": (1900, 2100),
        "building.facade.installation_year": (1900, 2100),
        "building.floor.installation_year": (1900, 2100),
        "building.window.installation_year": (1900, 2100),
        "building.door.installation_year": (1900, 2100),
        "heating.installation_year": (1900, 2100),
        # The schema's lower bound is exclusive (a SCOP above 1), so the low end is probed just inside it.
        "heating.heatpump_scop_en14825_w35": (1.1, 10),
        "heating.heatpump_scop_en14825_w55": (1.1, 10),
        "battery.days_to_cover": (1, 14),
        # Beside the block's days_to_cover; the lower bound is exclusive.
        "battery.power_in_watt": (250, 100000),
        "battery.installation_year": (1900, 2100),
        "solar_thermal_system.area_m2": (0.1, 100),
        "solar_thermal_system.installation_year": (1900, 2100),
    }

    #: The request leaves the schema walk skips, each with the reason. ``location.postcode`` is sent
    #: by the probe ``pair:postcode``, which keeps it out of the field aggregation.
    NOT_VARIED: ClassVar[Dict[str, str]] = {
        "location.postcode": "sent by pair:postcode",
    }

    #: The points of the generated numeric leaves whose schema leaves an end open, which the schema
    #: therefore cannot decide, by full request path (a cost leaf as ``measures[id=*].cost.<name>``).
    #: As :attr:`FIELD_PROBE_POINTS`, HiSim's own choice and never published; an exclusive bound gets
    #: a point inside it. ``tests/renovisor/test_capabilities.py`` keeps every point inside the schema.
    OPEN_END_POINTS: ClassVar[Dict[str, Tuple[Any, Any]]] = {
        **{
            f"house.building.{element}.added_insulation.material.{name}": points
            for element in ("roof", "facade", "floor")
            for name, points in (
                ("heat_capacity_j_kgk", (100, 5000)),
                ("density_kg_m3", (1, 3000)),
                ("co2_footprint_a1_a3_c3_c4_kg_m2", (0, 500)),
                ("lifespan_years", (1, 100)),
            )
        },
        "applicant.taxable_household_income_in_euro": (0, 250000),
        "applicant.household_size": (1, 12),
        "measures[id=*].cost.min_in_euro_per_m2": (0, 1000),
        "measures[id=*].cost.max_in_euro_per_m2": (0, 1000),
    }

    #: The values the free-text leaves are probed with, by full request path; each is a value no base
    #: already carries, so the probe changes the leaf.
    FREE_VALUES: ClassVar[Dict[str, Tuple[str, ...]]] = {
        # The neighbouring age band of the anchor's own derived IE.N.SFH.05.Gen.ReEx.001.001, a row of
        # the vendored TABULA table, so the override changes the archetype.
        "house.building.tabula_building_code": ("IE.N.SFH.06.Gen.ReEx.001.001",),
        **{
            f"house.building.{element}.added_insulation.material.asp_id": ("mineral_wool",)
            for element in ("roof", "facade", "floor")
        },
        "measures[id=*].cost.source": ("materials.yaml measure_costs, as the capability probe set copies it",),
    }

    #: How far inside an exclusive bound a derived point lies, as the leaf's range divided by this:
    #: ``u_value`` (0, 10] is probed at 0.01, as the roof's always was.
    INSIDE_DIVISOR: ClassVar[int] = 1000

    #: Which block each inventory path needs present before it can be set at all.
    FIELD_BLOCK: ClassVar[Dict[str, str]] = {
        "hot_water": "hot_water",
        "ventilation": "ventilation",
        "temperature_control": "temperature_control",
        "air_conditioning": "air_conditioning",
        "appliances": "appliances",
        "pv_system": "pv_system",
        "battery": "battery",
        "solar_thermal_system": "solar_thermal_system",
        "electric_vehicles": "electric_vehicles",
    }

    #: The low and high value probed for each free numeric option, from the request schema's own
    #: inclusive bounds on the field the option writes into: ``thickness_in_mm`` from
    #: ``added_insulation``, ``new_room_temperature_in_celsius`` from
    #: ``set_heating_temperature_in_celsius``, ``installation_year`` from ``construction_year``, and
    #: the device options from their own blocks. They are published as the option's
    #: ``minimum``/``maximum``, which the shared schema defines as the values probed from the
    #: request schema's bounds. A key is an option name, valid for every measure that has the
    #: option, or ``measure_id.option`` where one name means different fields in different measures
    #: (``power_in_watt``); the qualified key wins (:meth:`option_points`).
    OPTION_BOUNDS: ClassVar[Dict[str, Tuple[Any, Any]]] = {
        "thickness_in_mm": (10, 500),
        "installation_year": (1700, 2100),
        "u_value_in_watt_per_m2_per_kelvin": (0.1, 10),
        "air_conditioners.power_in_watt": (0, 50000),
        "size_in_percent_of_roof_area": (1, 100),
        # house.pv_system.azimuth and .tilt, the two fields these options write.
        "photovoltaic_system.azimuth_in_degree": (0, 360),
        "photovoltaic_system.tilt_in_degree": (0, 90),
        # house.pv_system.shading_losses_in_percent, which the option is copied into.
        "photovoltaic_system.shading_losses_in_percent": (0, 100),
        "number": (1, 2),
        SemanticChecks.ROOM_TEMPERATURE_MEASURE[1]: SemanticChecks.ROOM_TEMPERATURE_RANGE,
    }

    #: The values the numeric options with no inclusive schema bound are probed at, by
    #: ``measure_id.option``. As for :attr:`FIELD_PROBE_POINTS`, these are HiSim's own choice and
    #: are never published: the schema's bound on the field each of them writes is exclusive
    #: (``exclusiveMinimum: 0``), the low point lies just inside it, and the shared schema's
    #: ImplementedOption has no key for an exclusive bound (measure-capabilities 0.4.0 added them
    #: to ImplementedField only).
    OPTION_PROBE_POINTS: ClassVar[Dict[str, Tuple[Any, Any]]] = {
        # house.pv_system.power_in_watt: exclusiveMinimum 0, maximum 100000.
        "photovoltaic_system.power_in_watt": (100, 100000),
        # house.battery.custom_battery_capacity_generic_in_kilowatt_hour: exclusiveMinimum 0, maximum 200.
        "battery_system.capacity_in_kwh": (0.5, 200),
        # house.battery.power_in_watt: exclusiveMinimum 0, maximum 100000.
        "battery_system.power_in_watt": (250, 100000),
    }

    @classmethod
    def option_points(cls, measure_id: str, name: str) -> Tuple[Tuple[Any, Any], bool]:
        """Return the low and high value one numeric option is probed at, and whether they are published.

        Args:
            measure_id: The catalogue id.
            name: The option's name.

        Returns:
            ``((low, high), published)``: from :attr:`OPTION_BOUNDS` by qualified then by plain key,
            published; or from :attr:`OPTION_PROBE_POINTS`, not published.

        Raises:
            KeyError: When neither table names the option, which a catalogue edit that added a
                numeric option causes until somebody chooses its points.
        """
        qualified = f"{measure_id}.{name}"
        if qualified in cls.OPTION_BOUNDS:
            return cls.OPTION_BOUNDS[qualified], True
        if qualified in cls.OPTION_PROBE_POINTS:
            return cls.OPTION_PROBE_POINTS[qualified], False
        if name in cls.OPTION_BOUNDS:
            return cls.OPTION_BOUNDS[name], True
        raise KeyError(
            f"no probe points for the numeric option {qualified}; add them to ProbeSet.OPTION_BOUNDS "
            "(the request schema's inclusive bounds) or ProbeSet.OPTION_PROBE_POINTS"
        )

    #: The two-change probes the conditional entries of the list and the combinations the translator
    #: treats differently need, as ``name -> (house changes, package)``. A package entry without
    #: ``options`` stands for the measure's smallest package (:meth:`package`).
    PAIRS: ClassVar[Dict[str, Tuple[Dict[str, Any], Optional[List[Dict[str, Any]]]]]] = {
        "pair:solar_thermal_on_oil": (
            {"heating.type_of_system": "conventional_oil_heating",
             "solar_thermal_system": {"supplies": "dhw_only"}},
            None,
        ),
        "pair:solar_thermal_measure_on_oil": (
            {"heating.type_of_system": "conventional_oil_heating"},
            [{"id": "solar_thermal_system", "options": {"supplies": "dhw_only"}}],
        ),
        "pair:seasonal_efficiency_on_heat_pump": (
            {"heating.type_of_system": "air_source_heat_pump",
             "heating.seasonal_efficiency_in_percent": 300},
            None,
        ),
        "pair:flow_temperature_on_heat_pump": (
            {"heating.type_of_system": "air_source_heat_pump",
             "heating.flow_temperature_in_celsius": 40},
            None,
        ),
        "pair:dhw_heat_pump_on_heat_pump": (
            {"heating.type_of_system": "air_source_heat_pump",
             "hot_water": {"supply": "separate_heat_pump"}},
            None,
        ),
        "pair:dhw_heat_pump_measure_on_heat_pump": (
            {"heating.type_of_system": "air_source_heat_pump"},
            [{"id": "hot_water_system", "options": {"supply": "separate_heat_pump"}}],
        ),
        "pair:postcode": ({}, None),  # the location half is added in :meth:`build`
        # retrofit_status in a band without the variant it selects: Irish detached houses of 2011-
        # (IE.N.SFH.10) have no 002, so usual_refurb falls back to 001, approximated (§5.3 step 3).
        # The anchor's band has all three, so the field probes never meet the fallback; this pair
        # is what makes the document announce usual_refurb approximated (WORST_CASE_PAIRS).
        "pair:usual_refurb_in_a_band_without_its_variant": (
            {"building.construction_year": 2015, "building.retrofit_status": "usual_refurb"},
            None,
        ),
        # An insulation layer on an element the request gives no U-value: U_existing is the TABULA
        # row's (§4.3), which no single-change probe reaches, since the anchor states every U-value.
        "pair:external_insulation_on_a_facade_without_u_value": (
            {"building.facade.u_value_in_watt_per_m2_per_kelvin": None},
            [{"id": "external_insulation"}],
        ),
        # A cost block on a measure HiSim prices from its own cost database, which is read on an
        # envelope measure only; the band is the mockup's own.
        "pair:cost_on_heating_system": (
            {},
            [{"id": "heating_system", "options": {"type_of_system": "air_source_heat_pump"},
              "cost": {"min_in_euro_per_m2": 50, "max_in_euro_per_m2": 70, "source": "the capability probe set"}}],
        ),
    }

    #: The pairs that reach the worst case of one enumerated field's value, as ``name -> (path, value)``.
    #: Unlike every other pair, which constructs a combination whose status the document cannot
    #: state (hisim-5dfc), such a pair counts towards the value it names: the value is announced at
    #: the worse of its field probe's status and the pair's. ``usual_refurb`` is ``approximated``
    #: wherever it is sent, by owner decision (2026-09-26), because a request cannot know whether its
    #: construction year lands in a band without variant 002 and the document's conditions cannot
    #: state a year range; the pair's note names every such band (:meth:`TabulaIndex.bands_without`).
    WORST_CASE_PAIRS: ClassVar[Dict[str, Tuple[str, Any]]] = {
        "pair:usual_refurb_in_a_band_without_its_variant": ("house.building.retrofit_status", "usual_refurb"),
    }

    #: The postcode the one probe that carries one sends; a Dublin postal district.
    POSTCODE: ClassVar[str] = "D02 XY45"

    @classmethod
    def anchor(cls) -> Dict[str, Any]:
        """Return the anchor request: the vendored mockup with an empty package."""
        anchor = copy.deepcopy(ContractFiles.request_mockup())
        anchor["measures"] = []
        return anchor

    @classmethod
    def material(cls, mockup: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Return the material object every probe carries where it does not probe the material itself.

        It is the vendored mockup's own row -- the ``material`` option of its
        :attr:`MATERIAL_MEASURE` measure -- so no material id and no conductivity is invented
        here: rule 5 makes the catalogue's class names ("EPS", "Mineral wool") the frontend's
        business, and the translator only ever sees properties. It is what ``measure:<id>`` and an
        ``added_insulation`` layer send; the probe of a ``material`` option sends a second real row
        of ``materials.yaml`` in its place (:meth:`MaterialRows.alternative`). The mockup is read on
        every call, as :class:`ContractFiles` reads everything, so a re-vendored row is probed at once.

        Args:
            mockup: The request to take the row from; the vendored mockup when omitted.

        Returns:
            A fresh copy of the row, which a caller may change.

        Raises:
            ValueError: If the mockup carries no :attr:`MATERIAL_MEASURE` measure with a material
                object; the probe set has no other source for one.
        """
        request = ContractFiles.request_mockup() if mockup is None else mockup
        for measure in request.get("measures") or []:
            if not isinstance(measure, dict) or measure.get("id") != cls.MATERIAL_MEASURE:
                continue
            material = (measure.get("options") or {}).get(CatalogueTable.MATERIAL)
            if isinstance(material, dict):
                return copy.deepcopy(material)
        raise ValueError(
            f"{ContractFiles.REQUEST_MOCKUP_FILENAME} carries no {cls.MATERIAL_MEASURE!r} measure with a "
            f"{CatalogueTable.MATERIAL!r} object, and the capability probes read the material they send "
            "from exactly that measure"
        )

    @classmethod
    def build(cls) -> Tuple[Probe, ...]:
        """Return the whole probe set, in a stable order.

        Returns:
            The anchor, the bare baseline, the block probes, the measure probes, the option
            probes, the inventory probes and the pair probes, in that order.
        """
        probes: List[Probe] = [
            Probe(name="anchor", kind=ProbeKind.ANCHOR),
            Probe(
                name="bare",
                kind=ProbeKind.BARE,
                house={
                    "building.roof.shape": None,
                    **{f"building.{element}.u_value_in_watt_per_m2_per_kelvin": None for element in cls.ELEMENTS},
                    **{block: None for block in cls.BLOCKS},
                },
            ),
        ]
        for block, body in cls.BLOCKS.items():
            probes.append(
                Probe(name=f"block:{block}", kind=ProbeKind.BLOCK, house={block: dict(body)},
                      subject=f"{cls.HOUSE_PREFIX}{block}")
            )
        probes.extend(cls._measure_probes())
        probes.extend(cls._field_probes())
        for name, (house, measures) in cls.PAIRS.items():
            probes.append(
                Probe(
                    name=name,
                    kind=ProbeKind.PAIR,
                    house=dict(house),
                    # An entry without options is the measure's smallest package (:meth:`package`),
                    # which a table cannot spell where a material object is required.
                    measures=None if measures is None else [
                        entry if "options" in entry else cls.package(str(entry["id"])) for entry in measures
                    ],
                    location={"postcode": cls.POSTCODE} if name == "pair:postcode" else {},
                    subject="location.postcode" if name == "pair:postcode" else None,
                )
            )
        return tuple(probes)

    @classmethod
    def _measure_probes(cls) -> List[Probe]:
        """Return one probe per measure, plus one per option value and per numeric boundary."""
        probes: List[Probe] = []
        for measure_id in CatalogueTable.ids():
            base = cls.package(measure_id)
            probes.append(
                Probe(name=f"measure:{measure_id}", kind=ProbeKind.MEASURE,
                      measures=[base], subject=measure_id)
            )
            for option in CatalogueTable.options_of(measure_id):
                for value in cls._option_values(measure_id, option):
                    entry = copy.deepcopy(base)
                    entry["options"][option.name] = value
                    probes.append(
                        Probe(
                            name=f"option:{measure_id}.{option.name}={value}",
                            kind=ProbeKind.OPTION,
                            measures=[entry],
                            subject=f"{measure_id}.{option.name}",
                            value=value,
                        )
                    )
        return probes

    @classmethod
    def varied_fields(cls) -> Dict[str, Tuple[Any, ...]]:
        """Return every request leaf the probe set varies, by full request path, with its values.

        A cost leaf is spelled for :attr:`COST_MEASURE`, ``measures[id=external_insulation].cost.<name>``.

        Raises:
            ValueError: When a path is both enumerated and numeric (:meth:`field_plan`).
            KeyError: When a leaf of the schema has no values the probe set can choose
                (:meth:`schema_points`).
        """
        return {path: values for path, (values, _) in cls.field_plan().items()}

    @classmethod
    def field_shapes(cls) -> Dict[str, FieldShape]:
        """Return what each varied leaf is, by full request path, for what the document publishes."""
        return {path: shape for path, (_, shape) in cls.field_plan().items()}

    @classmethod
    def field_plan(cls, schema: Optional[Mapping[str, Any]] = None) -> Dict[str, Tuple[Tuple[Any, ...], FieldShape]]:
        """Return every varied request leaf with its values and its shape, in probe order.

        First the two inventory tables, in their own order, then every other settable leaf of the
        request schema (:meth:`SchemaLeaves.settable`) in schema order, except a ``const`` and the
        leaves of :attr:`NOT_VARIED`. A field added to the schema is therefore probed without a
        table entry wherever the schema decides its values.

        Args:
            schema: The request schema; the vendored one when omitted.

        Raises:
            ValueError: When a path is both enumerated and numeric. Merging the two tables would
                let one silently shadow the other, and the document would publish the path as
                whichever won -- a list of values or a pair of bounds -- without anybody deciding.
            KeyError: From :meth:`schema_points`, naming a leaf whose values nobody chose.
        """
        shared = sorted(set(cls.FIELD_VALUES) & set(cls.FIELD_PROBE_POINTS))
        if shared:
            raise ValueError(f"FIELD_VALUES and FIELD_PROBE_POINTS both list {shared}")
        plan: Dict[str, Tuple[Tuple[Any, ...], FieldShape]] = {}
        for path, values in cls.FIELD_VALUES.items():
            plan[f"{cls.HOUSE_PREFIX}{path}"] = (tuple(values), FieldShape.ENUMERATED)
        for path, points in cls.FIELD_PROBE_POINTS.items():
            plan[f"{cls.HOUSE_PREFIX}{path}"] = (tuple(points), FieldShape.NUMERIC)
        for generic, leaf in SchemaLeaves.settable(schema):
            path = cls.concrete(generic)
            if path in plan or generic in cls.NOT_VARIED or "const" in leaf:
                continue
            plan[path] = cls.schema_points(generic, leaf)
        return plan

    @classmethod
    def concrete(cls, path: str) -> str:
        """Return a settable path with a cost leaf spelled for :attr:`COST_MEASURE`."""
        generic = f"{SchemaLeaves.COST_PATH}."
        if path.startswith(generic):
            return f"measures[id={cls.COST_MEASURE}].cost.{path[len(generic):]}"
        return path

    @classmethod
    def schema_points(cls, path: str, leaf: Mapping[str, Any]) -> Tuple[Tuple[Any, ...], FieldShape]:
        """Return the values one schema leaf outside the inventory tables is probed with, and its shape.

        Args:
            path: The settable path, a cost leaf spelled ``measures[id=*].cost.<name>``.
            leaf: The leaf's schema keywords, alternatives merged.

        Returns:
            Every enum value; both booleans; for a number, the points of :attr:`OPEN_END_POINTS` or
            those :meth:`derived_points` reads off the schema's bounds; for a string, the values of
            :attr:`FREE_VALUES`.

        Raises:
            KeyError: When the leaf is a number with an open end or a string and no table names it,
                or of a type the probe set has no rule for.
        """
        if "enum" in leaf:
            return tuple(leaf["enum"]), FieldShape.ENUMERATED
        types = SchemaLeaves.types(leaf)
        if "boolean" in types:
            return (True, False), FieldShape.ENUMERATED
        if types & {"number", "integer"}:
            if path in cls.OPEN_END_POINTS:
                return tuple(cls.OPEN_END_POINTS[path]), FieldShape.NUMERIC
            return cls.derived_points(path, leaf), FieldShape.NUMERIC
        if "string" in types and path in cls.FREE_VALUES:
            return tuple(cls.FREE_VALUES[path]), FieldShape.FREE
        raise KeyError(
            f"the request schema's leaf {path} ({sorted(types) or 'untyped'}) has no probe values; add them to "
            "ProbeSet.FREE_VALUES (a string) or ProbeSet.OPEN_END_POINTS (a number), or the path to NOT_VARIED"
        )

    @classmethod
    def derived_points(cls, path: str, leaf: Mapping[str, Any]) -> Tuple[Any, Any]:
        """Return the low and high point of a numeric leaf the schema bounds at both ends.

        An inclusive bound is the point itself; an exclusive one gets a point the range divided by
        :attr:`INSIDE_DIVISOR` inside it, or one step inside for an integer.

        Raises:
            KeyError: When an end is open; its point is HiSim's choice (:attr:`OPEN_END_POINTS`).
        """
        lower = leaf.get("minimum", leaf.get("exclusiveMinimum"))
        upper = leaf.get("maximum", leaf.get("exclusiveMaximum"))
        if lower is None or upper is None:
            raise KeyError(
                f"the request schema leaves an end of {path} open; choose its points in ProbeSet.OPEN_END_POINTS"
            )
        integer = "integer" in SchemaLeaves.types(leaf) and "number" not in SchemaLeaves.types(leaf)
        step: Any = 1 if integer else (upper - lower) / cls.INSIDE_DIVISOR
        low = lower if "minimum" in leaf else lower + step
        high = upper if "maximum" in leaf else upper - step
        return low, high

    @classmethod
    def prelude(cls, path: str) -> Probe:
        """Return what a probe of one request leaf changes first so that the leaf can be set at all.

        It is read from the tables, never from a probe, so the path-verification harness measures
        every field probe from exactly this request (:meth:`hisim.renovisor.verify.probes.ProbeBases.natural_base`):
        the optional block the leaf lives in (:attr:`FIELD_BLOCK`), what :attr:`FIELD_PRELUDE` names
        for it, the layer an ``added_insulation`` leaf lives in (:attr:`LAYER_PLACEMENT`) and the
        package entry of :attr:`COST_MEASURE` with its band for a cost leaf (:attr:`COST_BANDS`).

        Args:
            path: A full request path, as :meth:`varied_fields` spells it.

        Returns:
            A patch on the anchor; empty for a leaf the anchor can carry as it is.
        """
        house: Dict[str, Any] = {}
        measures: Optional[List[Dict[str, Any]]] = None
        if path.startswith(cls.HOUSE_PREFIX):
            own = path[len(cls.HOUSE_PREFIX):]
            block = own.split(".")[0]
            if block in cls.FIELD_BLOCK:
                house[block] = dict(cls.BLOCKS[block])
            house.update(cls.FIELD_PRELUDE.get(own, {}))
            for element in cls.LAYER_PLACEMENT:
                layer = f"building.{element}.added_insulation"
                if own.startswith(f"{layer}."):
                    house[layer] = cls.layer(element)
        cost = f"measures[id={cls.COST_MEASURE}].cost."
        if path.startswith(cost):
            band = cls.COST_BANDS.get(path[len(cost):], cls.COST_BANDS[""])
            measures = [{**cls.package(cls.COST_MEASURE), "cost": dict(band)}]
        return Probe(name=f"prelude:{path}", kind=ProbeKind.FIELD, house=house, measures=measures)

    @classmethod
    def layer(cls, element: str) -> Dict[str, Any]:
        """Return the ``added_insulation`` layer a probe of one element's layer starts from."""
        return {
            "placement": cls.LAYER_PLACEMENT[element],
            "thickness_in_mm": cls.LAYER_THICKNESS_IN_MM,
            "material": cls.material(),
        }

    @classmethod
    def _field_probes(cls) -> List[Probe]:
        """Return one probe per request value worth varying, with its prelude set first."""
        probes: List[Probe] = []
        for path, values in cls.varied_fields().items():
            prelude = cls.prelude(path)
            for value in values:
                probes.append(cls._field_probe(prelude, path, value))
        return probes

    @classmethod
    def _field_probe(cls, prelude: Probe, path: str, value: Any) -> Probe:
        """Return the probe that sets one request leaf to one value on top of its prelude.

        Raises:
            ValueError: When the path lies in no block a field probe can write.
        """
        house = dict(prelude.house)
        location: Dict[str, Any] = {}
        applicant: Dict[str, Any] = {}
        measures = copy.deepcopy([dict(entry) for entry in prelude.measures]) if prelude.measures else None
        block, _, rest = path.partition(".")
        if block == "house":
            house[rest] = value
        elif block == "location":
            location[rest] = value
        elif block == "applicant":
            applicant[rest] = value
        elif block.startswith("measures[id=") and measures is not None:
            measure_id = block[len("measures[id="):-1]
            _write(next(entry for entry in measures if entry["id"] == measure_id), rest, value)
        else:
            raise ValueError(f"a field probe cannot write {path}")
        return Probe(
            name=f"field:{rest if block == 'house' else path}={value}",
            kind=ProbeKind.FIELD,
            house=house,
            measures=measures,
            location=location,
            subject=path,
            value=value,
            applicant=applicant,
        )

    @classmethod
    def package(cls, measure_id: str) -> Dict[str, Any]:
        """Return the smallest package entry that carries one measure.

        Args:
            measure_id: A catalogue id.

        Returns:
            ``{"id": ..., "options": {...}}`` with every ``everyone`` option filled with its
            first accepted value, which is the smallest request the semantic checks accept.
        """
        options: Dict[str, Any] = {}
        for option in CatalogueTable.options_of(measure_id):
            if option.access_level is not AccessLevel.EVERYONE:
                continue
            options[option.name] = cls._first_value(measure_id, option)
        return {"id": measure_id, "options": options}

    @classmethod
    def _first_value(cls, measure_id: str, option: OptionSpec) -> Any:
        """Return the value a probe sends for one required option when it varies nothing."""
        if option.value_type is ValueType.MATERIAL:
            return cls.material()
        if option.values:
            return option.values[0]
        if option.value_type is ValueType.BOOLEAN:
            return True
        return cls.option_points(measure_id, option.name)[0][0]

    @classmethod
    def _option_values(cls, measure_id: str, option: OptionSpec) -> Tuple[Any, ...]:
        """Return the values one option is probed with: its list, its two points, or both booleans.

        A ``material`` option is probed with one material: the second real row
        :meth:`MaterialRows.alternative` finds for the measure, so the probe changes the material its
        base (:meth:`material`) carries; the base's own row where the file offers no second one.
        """
        if option.value_type is ValueType.MATERIAL:
            base = cls.material()
            return (MaterialRows.alternative(measure_id, base) or base,)
        if option.values:
            return tuple(option.values)
        if option.value_type is ValueType.BOOLEAN:
            return (True, False)
        return cls.option_points(measure_id, option.name)[0]


@dataclass
class ProbeResult:
    """What one probe produced, reduced to what the document and T-NIY need.

    Args:
        probe: The probe itself.
        refused: The problem codes the request validation produced, if any. A refused probe is
            not a failure: ``ES`` has no TABULA typology and a zero-area window is a real
            refusal, and the document says so by leaving the value out of ``accepted_values``.
        fields: Per request path, what the mapping report said about it.
        measures: Per measure id, its status and the status of each of its options.
        hits: The keys of the whitelist entries this probe matched.
    """

    probe: Probe
    refused: Tuple[str, ...] = ()
    fields: Dict[str, Tuple[ReportStatus, Optional[str]]] = field(default_factory=dict)
    measures: Dict[str, Tuple[ReportStatus, Optional[str], Dict[str, Tuple[ReportStatus, Optional[str]]]]] = (
        field(default_factory=dict)
    )
    hits: Tuple[str, ...] = ()
    targets: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def of_report(cls, probe: Probe, report: Mapping[str, Any], hits: Tuple[str, ...]) -> "ProbeResult":
        """Reduce one translated probe's mapping report to what the document aggregates.

        The one place the reduction is written, so the capability run and the path-verification
        harness (:mod:`hisim.renovisor.verify`), which translates the same probes and keeps the
        whole report, aggregate the same statuses.

        Args:
            probe: The probe that was translated.
            report: Its ``mapping_report.json``, as :meth:`MappingReport.to_json` returns it.
            hits: The whitelist entries the translation matched.

        Returns:
            The probe's result, with its field, measure and target statuses.
        """
        fields = {
            row["path"]: (ReportStatus(row["status"]), row.get("note"))
            for row in report["fields"]
        }
        measures: Dict[str, Any] = {}
        targets: Dict[str, Tuple[str, ...]] = {}
        for row in report["measures"]:
            options = {
                option["name"]: (ReportStatus(option["status"]), option.get("note"))
                for option in row["options"]
            }
            measures[row["id"]] = (ReportStatus(row["status"]), row.get("note"), options)
            targets[row["id"]] = tuple(row["targets"])
        return cls(probe=probe, fields=fields, measures=measures, hits=hits, targets=targets)


class ProbeRunner:
    """Runs the probe set through ``validate`` + ``apply`` + ``translate`` and collects the answers.

    No simulation, no output directory, nothing written: the whole point of ``translate`` being
    pure is that this takes a second rather than a week.

    Args:
        base_files_directory: Where the recorded twins live.
        whitelist: The list every probe is run against; one instance, with its hit record
            cleared between probes.
    """

    #: Where the recorded base files live, relative to the repository root.
    DEFAULT_BASE_FILES: ClassVar[Path] = Path(__file__).resolve().parents[2] / "energy_systems"

    def __init__(
        self, base_files_directory: Optional[Path] = None, whitelist: Optional[Whitelist] = None
    ) -> None:
        """Store the directory and the list; nothing runs until :meth:`run`."""
        self._directory = base_files_directory or self.DEFAULT_BASE_FILES
        self._whitelist = whitelist if whitelist is not None else Whitelist.load()
        self._translator = Translator(self._directory, self._whitelist)

    @property
    def whitelist(self) -> Whitelist:
        """Return the list the probes were run against, for the T-NIY assertions."""
        return self._whitelist

    def run(self, probes: Optional[Sequence[Probe]] = None) -> Tuple[ProbeResult, ...]:
        """Run every probe and return what each of them produced.

        Args:
            probes: The probe set; the committed one when omitted.

        Returns:
            One :class:`ProbeResult` per probe, in probe order.

        Raises:
            TranslatorError: When a probe hits an item that is neither mapped nor listed. That
                is the whole point of the exercise: the build fails before an image ships.
        """
        anchor = ProbeSet.anchor()
        return tuple(self._one(probe, anchor) for probe in (probes if probes is not None else ProbeSet.build()))

    def translate(self, document: Mapping[str, Any]) -> TranslatedSystem:
        """Run ``validate`` + ``apply`` + ``translate`` on one request, with a fresh hit record.

        The one pipeline both the capability run and the path-verification harness
        (:class:`hisim.renovisor.verify.runner.ArtefactCache`) translate a probe with;
        :attr:`whitelist` ``.hits()`` afterwards is what this translation matched.

        Args:
            document: The request body.

        Returns:
            The translated system: file, mapping report, edits and economic context.

        Raises:
            RequestError: When the validation refuses the request.
            TranslatorError: When the translation hits an item that is neither mapped nor listed.
        """
        self._whitelist.forget_hits()
        request = Request.parse(document)
        applied = apply(
            request.document["house"],
            request.measures,
            self._whitelist,
            archetype=ArchetypeEnvelope.for_request(request),
        )
        return self._translator.translate(request, applied)

    def _one(self, probe: Probe, anchor: Mapping[str, Any]) -> ProbeResult:
        """Run one probe, turning a refusal into a result rather than into an exception."""
        try:
            translated = self.translate(probe.document(anchor))
        except RequestError as error:
            return ProbeResult(
                probe=probe, refused=tuple(problem.code.value for problem in error.problems)
            )
        return ProbeResult.of_report(probe, translated.report.to_json(), self._whitelist.hits())


@dataclass(frozen=True)
class Observation:
    """What one probe said about one item: the status it reported and the note beside it.

    The document aggregates many probes into one entry, and the two halves of an entry -- its
    status and its note -- have to come from the same probe, or the entry announces one thing
    and explains another. Keeping the pair together in one object, rather than in two parallel
    lists indexed by nothing, is what makes that impossible to get wrong.

    Args:
        status: The status the probe's mapping report carried for the item.
        note: The sentence it carried beside the status, or ``None`` when it carried none.
    """

    status: ReportStatus
    note: Optional[str] = None


class NoteAggregation:
    """How several probes' observations of one item become one status and one note.

    The status is the worst status any probe observed, which is what makes the document a
    promise rather than an average: a run can never report an item worse than the document
    announces. The note then has to be the note of the probe that produced *that* status. Taking
    the first note anybody wrote down instead is how ``house.solar_thermal_system.supplies`` came
    to be published as ``not_implemented_yet`` while explaining itself with the sentence of the
    value that works (step 8 addendum A)::

        status = NoteAggregation.status_of(observations)
        note = NoteAggregation.note_of(observations, status)

    Where several probes tie at the worst status with different sentences, all of them are
    carried, joined by :attr:`SEPARATOR` in probe order: each is true of a different request, and
    picking one of them would hide the other. Two probes that tie with the same sentence carry it
    once.
    """

    #: What joins two sentences that tie at the worst status.
    SEPARATOR: ClassVar[str] = " | "

    @classmethod
    def status_of(cls, observations: Sequence[Observation]) -> ReportStatus:
        """Return the worst status any of *observations* carried.

        Args:
            observations: What the probes said; at least one.

        Returns:
            The worst of the observed statuses, per
            :meth:`hisim.renovisor.vocabulary.ReportStatus.worst_of`.

        Raises:
            ValueError: When *observations* is empty, because there is no neutral status.
        """
        return ReportStatus.worst_of(*(observation.status for observation in observations))

    @classmethod
    def note_of(cls, observations: Sequence[Observation], status: ReportStatus) -> Optional[str]:
        """Return the sentence an entry of *status* carries, out of the same observations.

        Args:
            observations: What the probes said, in probe order.
            status: The status the entry is published with, normally :meth:`status_of` of the
                same observations.

        Returns:
            The note of the observations that carry *status*, de-duplicated and joined by
            :attr:`SEPARATOR` in probe order, or ``None`` when none of them carried one.
        """
        notes: List[str] = []
        for observation in observations:
            if observation.status is not status or not observation.note:
                continue
            if observation.note not in notes:
                notes.append(observation.note)
        return cls.SEPARATOR.join(notes) if notes else None


class Aggregation:
    """Reduces the probe results to one status per measure, option, value and inventory field.

    The rule is the frontend side's specification §8 step 4: a status is the worst one any probe
    observed, and an option value a probe refused as unknown is left out of ``accepted_values``.
    "Worst" is :meth:`hisim.renovisor.vocabulary.ReportStatus.worst_of`, which is what makes the
    document a promise rather than an average: a run can never report an item worse than it
    announces. The note beside a status comes from :class:`NoteAggregation`, so that an entry is
    always explained by the probe that produced the status it publishes.
    """

    #: The prefix of a mapping-report line about a package entry rather than a request field.
    PACKAGE_PREFIX: ClassVar[str] = "measures["

    #: The phrase a note has to contain for the entry to count as a substitution rather than as
    #: a missing model. The review's D-E asked for the distinction so that the frontend can hide
    #: one class and offer the other; this is the whole of it.
    SUBSTITUTION_MARKS: ClassVar[Tuple[str, ...]] = ("modelled as", "stands in for")

    @classmethod
    def is_substitution(cls, note: Optional[str]) -> bool:
        """Return whether a note describes a substitution rather than an absent model.

        Args:
            note: The whitelist entry's sentence, or ``None``.

        Returns:
            ``True`` when the note says the item is modelled as something else, which the
            frontend may offer with the note; ``False`` for a plain "no model", which it may
            hide.
        """
        lowered = (note or "").lower()
        return any(mark in lowered for mark in cls.SUBSTITUTION_MARKS)

    @classmethod
    def measures(cls, results: Sequence[ProbeResult]) -> List[Dict[str, Any]]:
        """Return the ``measures`` array of the document, one entry per catalogue measure.

        Two probe kinds are deliberately left out. A ``PAIR`` probe constructs an unsupported
        combination on purpose -- a solar thermal collector on an oil boiler -- and a measure's
        status is about the measure, not about a combination, so letting a pair probe set it
        would announce that the collector is never modelled. And an ``OPTION`` probe of a valued
        option carries one value's own status, which belongs in ``values`` and, by the openapi
        schema's own wording, never changes the option's. Neither kind contributes a note
        either: a note an entry's own status cannot account for is what addendum A removed.
        """
        entries: List[Dict[str, Any]] = []
        for measure_id in CatalogueTable.ids():
            observed: List[Observation] = []
            targets: List[str] = []
            option_observed: Dict[str, List[Observation]] = {}
            value_statuses: Dict[str, Dict[Any, Tuple[ReportStatus, Optional[str]]]] = {}
            valued = {spec.name for spec in CatalogueTable.options_of(measure_id) if spec.values}
            for result in results:
                row = result.measures.get(measure_id)
                if row is None:
                    continue
                measure_status, measure_note, options = row
                if result.probe.kind is not ProbeKind.PAIR:
                    observed.append(Observation(measure_status, measure_note))
                targets.extend(result.targets.get(measure_id, ()))
                own = (
                    result.probe.subject
                    if result.probe.kind is ProbeKind.OPTION and result.probe.subject
                    else ""
                )
                for name, (option_status, option_note) in options.items():
                    if own == f"{measure_id}.{name}" and name in valued:
                        value_statuses.setdefault(name, {})[value_key(result.probe.value)] = (
                            option_status,
                            option_note,
                        )
                    elif result.probe.kind is not ProbeKind.PAIR:
                        option_observed.setdefault(name, []).append(
                            Observation(option_status, option_note)
                        )
            status = (
                NoteAggregation.status_of(observed) if observed else ReportStatus.NOT_IMPLEMENTED_YET
            )
            note = NoteAggregation.note_of(observed, status)
            entry: Dict[str, Any] = {
                "measure_id": measure_id,
                "status": MeasureStatus.of(status).value,
                "options": cls._options(measure_id, option_observed, value_statuses),
                "hisim_targets": sorted(set(targets)),
                "substitution": cls.is_substitution(note),
            }
            if note is not None:
                entry["note"] = note
            entries.append(entry)
        return entries

    @classmethod
    def _options(
        cls,
        measure_id: str,
        observed: Mapping[str, List[Observation]],
        values: Mapping[str, Mapping[Any, Tuple[ReportStatus, Optional[str]]]],
    ) -> List[Dict[str, Any]]:
        """Return one entry per option the catalogue declares for one measure.

        Only the catalogue's options: a report line for an option the catalogue does not declare
        stays in the mapping report and never reaches the document, whose options are the
        contract's. It cannot change the measure's status either, because
        :class:`hisim.renovisor.apply.MeasureStatusRules` passes over it.

        An option's note is the note of its own worst *option-level* observation, never of one
        of its values: a value's sentence belongs to the ``values`` entry that carries the
        value's own status, and repeating it at option level announced ``used`` options that
        explained themselves with a sentence about something nobody asked for (addendum A).
        """
        entries: List[Dict[str, Any]] = []
        for spec in CatalogueTable.options_of(measure_id):
            observations = observed.get(spec.name) or [
                Observation(status, note) for status, note in values.get(spec.name, {}).values()
            ]
            status = (
                NoteAggregation.status_of(observations)
                if observations
                else ReportStatus.NOT_IMPLEMENTED_YET
            )
            entry: Dict[str, Any] = {"name": spec.name, "status": status.value}
            if spec.values is not None:
                entry["accepted_values"] = list(spec.values)
                entry["values"] = [
                    cls._value(value, values.get(spec.name, {}).get(value_key(value)))
                    for value in spec.values
                ]
            if spec.values is None and spec.value_type in (ValueType.INTEGER, ValueType.NUMBER):
                bounds, published = ProbeSet.option_points(measure_id, spec.name)
                if published:
                    entry["minimum"], entry["maximum"] = bounds
            note = NoteAggregation.note_of(observations, status)
            if note is not None:
                entry["note"] = note
            entries.append(entry)
        return entries

    @classmethod
    def _value(cls, value: Any, observed: Optional[Tuple[ReportStatus, Optional[str]]]) -> Dict[str, Any]:
        """Return one ``values`` entry: the catalogue value with the status its probe observed.

        The ``substitution`` flag is the value's own, from its own note, so the frontend can
        offer ``hybrid_heat_pump`` with its "modelled as ..." sentence while hiding a value
        whose absence no stand-in covers.
        """
        status, note = observed if observed is not None else (ReportStatus.USED, None)
        entry: Dict[str, Any] = {
            "value": value,
            "status": (
                ReportStatus.APPROXIMATED.value
                if status is ReportStatus.DEFAULTED
                else status.value
            ),
            "substitution": cls.is_substitution(note),
        }
        if note is not None:
            entry["note"] = note
        return entry

    @classmethod
    def fields(cls, results: Sequence[ProbeResult]) -> List[Dict[str, Any]]:
        """Return the ``fields`` array: the same aggregation over the inventory.

        As for a measure's options, a probe that varies an enumerated field's value contributes that
        value's own status to ``values`` and not to the field's, and a ``PAIR`` probe contributes
        to neither -- except a pair of :attr:`ProbeSet.WORST_CASE_PAIRS`, which counts towards the
        one value it names, so that the value announces the worst case the pair reaches. A numeric
        field (:attr:`FieldShape.NUMERIC`) carries no ``values``: it
        publishes the request schema's ``minimum``/``maximum`` and ``exclusiveMinimum``/
        ``exclusiveMaximum`` where the schema declares them (:class:`RequestSchemaBounds`) -- never
        its probe points -- and the probes at both ends count towards its own status, as the
        probes of a free-text field (:attr:`FieldShape.FREE`) count towards its. The report's line
        for a package entry's cost block, ``measures[<position>].cost``, is left out: it is spelled
        by position in one probe's package, which says nothing about the request's fields. What is
        left for the field's own status is every probe that carried the field
        without being about it -- the anchor, the block probes and the measure probes -- and,
        when nothing did, the worst of its values. The note follows the status out of the same
        observations, which is what makes ``house.hot_water.supply`` explain itself with the two
        supplies that are not implemented rather than with the one that is.
        """
        observed: Dict[str, List[Observation]] = {}
        per_value: Dict[str, Dict[Any, Tuple[ReportStatus, Optional[str]]]] = {}
        schema = ContractFiles.request_schema()
        shapes = ProbeSet.field_shapes()
        bounds = {
            path: RequestSchemaBounds.of(path, schema)
            for path, shape in shapes.items()
            if shape is FieldShape.NUMERIC and not path.startswith(cls.PACKAGE_PREFIX)
        }
        for result in results:
            probe = result.probe
            own = probe.subject if probe.kind is ProbeKind.FIELD and probe.subject else ""
            for path, (status, note) in result.fields.items():
                if path.startswith(cls.PACKAGE_PREFIX):
                    continue
                if path == own and shapes.get(path) is FieldShape.ENUMERATED:
                    per_value.setdefault(path, {})[value_key(probe.value)] = (status, note)
                elif probe.kind is not ProbeKind.PAIR:
                    observed.setdefault(path, []).append(Observation(status, note))
        for result in results:
            worst_case = ProbeSet.WORST_CASE_PAIRS.get(result.probe.name)
            if worst_case is None or worst_case[0] not in result.fields:
                continue
            path, value = worst_case
            cls._worsen(per_value.setdefault(path, {}), value_key(value), *result.fields[path])
        entries: List[Dict[str, Any]] = []
        for path in sorted(set(observed) | set(per_value)):
            observations = observed.get(path) or [
                Observation(status, note) for status, note in per_value.get(path, {}).values()
            ]
            status = NoteAggregation.status_of(observations)
            note = NoteAggregation.note_of(observations, status)
            entry: Dict[str, Any] = {"path": path, "status": status.value}
            entry.update(bounds.get(path, {}))
            if per_value.get(path):
                entry["values"] = [
                    {
                        "value": value,
                        "status": (
                            ReportStatus.APPROXIMATED.value
                            if value_status is ReportStatus.DEFAULTED
                            else value_status.value
                        ),
                        "substitution": cls.is_substitution(value_note),
                        **({"note": value_note} if value_note else {}),
                    }
                    for value, (value_status, value_note) in per_value[path].items()
                ]
            if note is not None:
                entry["note"] = note
            entry["substitution"] = cls.is_substitution(note)
            entries.append(entry)
        return entries

    @staticmethod
    def _worsen(
        values: Dict[Any, Tuple[ReportStatus, Optional[str]]],
        key: Any,
        status: ReportStatus,
        note: Optional[str],
    ) -> None:
        """Let one worst-case pair's observation replace a value's own when it is worse.

        A status the pair shares with the value's own probe keeps both notes, as
        :class:`NoteAggregation` joins a tie; a better one changes nothing. The value's place in
        ``values`` stays its field probe's, so the order remains the request schema's.
        """
        own = values.get(key)
        if own is None:
            values[key] = (status, note)
            return
        observations = [Observation(*own), Observation(status, note)]
        worst = NoteAggregation.status_of(observations)
        values[key] = (worst, NoteAggregation.note_of(observations, worst))

    @classmethod
    def tally(cls, measures: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
        """Return how many measures carry each status, which acceptance compares against §4.2."""
        counts: Dict[str, int] = {status.value: 0 for status in MeasureStatus}
        for entry in measures:
            counts[str(entry["status"])] += 1
        return counts


def value_key(value: Any) -> Any:
    """Return a hashable key for one probed value, so a material object can index a dictionary.

    The capability document and the path-verification harness's announcements
    (:class:`hisim.renovisor.verify.runner.Announcements`) key values by it, so both read a value
    the same way.
    """
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True)
    return value


class ResultsSection:
    """The ``results`` section of the document: what ``result.json`` will carry.

    The rest of the document says what the translator does with a *request*. This says what the
    caller gets *back*, which a frontend building a result page needs before the first
    calculation has run: every field the payload can carry, where its number is read from, and
    whether that number will be a simulation result, a constant standing in for a missing model,
    a partly-estimated figure, or nothing at all.

    It is generated from the two tables ``result.json`` is itself built out of --
    :class:`hisim.renovisor.kpis.KpiSchema`, written in terms of ``KpiSources``, and
    :class:`hisim.renovisor.costs.CostSchema`, whose rows read nothing from the simulation since
    step 10 because the money left the payload: each of them names instead the key of
    ``economics_result.json`` that answers the field (:class:`hisim.renovisor.costs.EconomicsDocument`),
    or says why no key does. Neither is a second list beside the builders, so the section cannot
    announce a field the payload does not carry or a source the builder does not read. Its shape is
    the shared schema's ``ResultFields``, which :meth:`CapabilityDocument.validate` checks with
    the rest of the document.
    """

    #: The key of the KPI half of the section, which is the payload block's own name.
    KPIS_KEY: ClassVar[str] = "kpis"

    #: The key of the cost half.
    COSTS_KEY: ClassVar[str] = "costs"

    @classmethod
    def build(cls) -> Dict[str, Any]:
        """Return the section, one entry per field ``result.json`` can emit.

        Returns:
            ``{"kpis": [...], "costs": [...]}``, each list in payload order.
        """
        return {
            cls.KPIS_KEY: [row.to_json() for row in KpiSchema.rows()],
            cls.COSTS_KEY: [row.to_json() for row in CostSchema.rows()],
        }


@dataclass(frozen=True)
class CapabilityDocument:
    """The document ``GET /measures`` serves for one engine version.

    Args:
        body: The document itself, ready to be written.
        results: What every probe produced, which T-NIY and T-CAP read.
        whitelist: The list the probes were run against, for the two-way check.
    """

    body: Dict[str, Any]
    results: Tuple[ProbeResult, ...]
    whitelist: Whitelist

    #: The engine name every document carries.
    ENGINE: ClassVar[str] = "hisim"

    @classmethod
    def build(
        cls,
        measures_path: Optional[Path] = None,
        base_files_directory: Optional[Path] = None,
    ) -> "CapabilityDocument":
        """Read the catalogue, check the frozen table against it, run the probes and aggregate.

        The document is a function of the code and the vendored catalogue alone: two builds of
        one commit are byte-identical. It therefore carries no timestamp. The backend serves the
        file under a strong ETag that is its hash and marks it immutable per version, so a clock
        reading inside the document would make equivalent content look changed on every rebuild
        (shared todo H18). The commit fields identify the build; a reader who wants to know when
        a file was written asks the file system.

        Args:
            measures_path: A ``measures.yaml`` to check the frozen table against; the vendored
                copy when omitted.
            base_files_directory: Where the recorded twins live.

        Returns:
            The document, with the probe results beside it.

        Raises:
            AssertionError: When the frozen catalogue table does not equal the file (T-CAT).
            TranslatorError: When a probe hits an item that is neither mapped nor listed.
        """
        assert_catalogue_matches(measures_path)
        runner = ProbeRunner(base_files_directory)
        results = runner.run()
        measures = Aggregation.measures(results)
        body = {
            "engine": cls.ENGINE,
            # `or_unknown` rather than `of`: the vendored contract schema types both commit
            # fields as strings, so an image with no commit marker at all would otherwise produce
            # a document that does not validate against the contract it is meant to satisfy.
            "engine_version": f"{cls.ENGINE}-{HiSimCommit.or_unknown()}",
            "translator": {
                "version": TRANSLATOR_VERSION,
                "commit": HiSimCommit.or_unknown(),
                "hisim_commit": HiSimCommit.or_unknown(),
                "request_schema_version": 1,
                "catalogue_revision": cls.catalogue_revision(),
                "probes": len(results),
            },
            "measures": measures,
            "fields": Aggregation.fields(results),
            "results": ResultsSection.build(),
        }
        return cls(body=body, results=results, whitelist=runner.whitelist)

    @classmethod
    def catalogue_revision(cls) -> str:
        """Return the contract revision the frozen table was taken from, for identification."""
        pin = ContractFiles.pinned()
        entry = pin.get("files", {}).get(ContractFiles.MEASURES_FILENAME, {})
        return f"renovisor-api-contract@{entry.get('commit', 'unknown')}"

    def tally(self) -> Dict[str, int]:
        """Return how many measures carry each status."""
        return Aggregation.tally(self.body["measures"])

    def unhit_entries(self) -> Tuple[str, ...]:
        """Return the whitelist entries no probe reached, which T-NIY refuses.

        An entry nothing reaches is either an omission that has since been implemented -- in
        which case it has to be deleted -- or a sentence about a request nobody can send, which
        is worse: it is a promise the document makes and no run can keep.
        """
        hit = {key for result in self.results for key in result.hits}
        return tuple(entry.key() for entry in self.whitelist.entries() if entry.key() not in hit)

    #: The document path of the whole document's schema inside the vendored capabilities file.
    SCHEMA_REF: ClassVar[str] = "#/components/schemas/ImplementedMeasures"

    def validate(self) -> None:
        """Check the document against the vendored ``measure-capabilities.openapi.yaml``.

        That is what ``GET /measures`` returns, and it is strict: every key the document always
        carries is required and no undeclared key is admitted, the ``results`` section included.

        Raises:
            jsonschema.ValidationError: On the first way the document is not what the shared
                schema says ``GET /measures`` returns.
        """
        schema = dict(ContractFiles.capabilities_schema())
        schema["$ref"] = self.SCHEMA_REF
        Draft202012Validator(schema).validate(self.body)

    def write(self, path: Path) -> int:
        """Validate the document and write it as JSON.

        Args:
            path: Where to write it.

        Returns:
            ``0``; the command line returns it as the exit code, and every way of failing is an
            exception rather than a code, because a broken capability document must not be
            published quietly.
        """
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return 0


def assert_catalogue_matches(
    measures_path: Optional[Path] = None,
    frozen_table: Optional[Mapping[str, Sequence[OptionSpec]]] = None,
) -> None:
    """Raise when the frozen catalogue table does not equal ``measures.yaml`` (T-CAT).

    Args:
        measures_path: The file to compare against; the vendored copy when omitted.
        frozen_table: The table to check, as :attr:`CatalogueTable.BY_ID` shapes it;
            :attr:`CatalogueTable.BY_ID` itself when omitted. A test passes a drifted copy.

    Raises:
        AssertionError: Naming the first difference. A catalogue edit has to be a deliberate
            change to :class:`hisim.renovisor.request.CatalogueTable`, not a surprise in a
            user's refused request. Also when an option the frozen table types
            :attr:`ValueType.MATERIAL` is not named :attr:`CatalogueTable.MATERIAL`: the code
            tells a material option by its type and the catalogue by its name, so the two have
            to agree.
    """
    table = CatalogueTable.BY_ID if frozen_table is None else frozen_table
    misnamed = sorted(
        f"{measure_id}.{option.name}"
        for measure_id, options in table.items()
        for option in options
        if option.value_type is ValueType.MATERIAL and option.name != CatalogueTable.MATERIAL
    )
    if misnamed:
        raise AssertionError(
            f"the frozen catalogue table types {misnamed} as a material, but only an option named "
            f"'{CatalogueTable.MATERIAL}' may be one"
        )
    if measures_path is None:
        catalogue = ContractFiles.measures()
    else:
        with measures_path.open(encoding="utf-8") as handle:
            catalogue = yaml.safe_load(handle)
    from_file: Dict[str, List[Tuple[str, str, str, Optional[Tuple[Any, ...]]]]] = {}
    for measure in catalogue["measures"]:
        from_file[str(measure["id"])] = [
            (
                str(option["name"]),
                str(option["access_level"]),
                _declared_value_type(option),
                tuple(option["values"]) if option.get("values") is not None else None,
            )
            for option in (measure.get("options") or [])
        ]
    frozen = {
        measure_id: [
            (option.name, option.access_level.value, option.value_type.value, option.values)
            for option in options
        ]
        for measure_id, options in table.items()
    }
    if frozen != from_file:
        missing = sorted(set(from_file) - set(frozen))
        extra = sorted(set(frozen) - set(from_file))
        changed = sorted(
            measure_id
            for measure_id in set(frozen) & set(from_file)
            if frozen[measure_id] != from_file[measure_id]
        )
        raise AssertionError(
            "the frozen catalogue table and measures.yaml disagree: "
            f"missing {missing}, extra {extra}, changed {changed}"
        )


def _declared_value_type(option: Mapping[str, Any]) -> str:
    """Return an option's value type as the catalogue declares it, for :func:`assert_catalogue_matches`.

    The catalogue declares a ``material`` option by name and access level only, since contract
    PR #10: its values are generated from ``materials.yaml``, and the request carries the material
    object. Such an option compares as :attr:`ValueType.MATERIAL`. Any other option without a
    ``value_type`` compares as ``"<missing>"``, so a catalogue that drops the key anywhere else
    fails T-CAT by name instead of being read as a material. This is the one place that reads the
    name as a type: the catalogue carries nothing else, and :func:`assert_catalogue_matches`
    asserts that the frozen table's material options carry this name.
    """
    declared = option.get("value_type")
    if declared is None and option.get("name") == CatalogueTable.MATERIAL:
        return ValueType.MATERIAL.value
    return str(declared) if declared is not None else "<missing>"


def unlisted_lines(results: Iterable[ProbeResult]) -> Tuple[str, ...]:
    """Return every ``not_implemented_yet`` line a probe produced without a whitelist note.

    By construction there can be none -- every such line is written by
    :meth:`hisim.renovisor.whitelist.Whitelist.require`, which raises when nothing matches --
    so this is the belt to that braces: it checks the invariant from the outside, over the
    document rather than over the code path, which is what T-NIY asks for.
    """
    unlisted: List[str] = []
    for result in results:
        for path, (status, note) in result.fields.items():
            if status is ReportStatus.NOT_IMPLEMENTED_YET and not note:
                unlisted.append(f"{result.probe.name}: {path}")
        for measure_id, (status, note, options) in result.measures.items():
            if status is ReportStatus.NOT_IMPLEMENTED_YET and not note:
                unlisted.append(f"{result.probe.name}: measure {measure_id}")
            for name, (option_status, option_note) in options.items():
                if option_status is ReportStatus.NOT_IMPLEMENTED_YET and not option_note:
                    unlisted.append(f"{result.probe.name}: {measure_id}.{name}")
    return tuple(sorted(set(unlisted)))
