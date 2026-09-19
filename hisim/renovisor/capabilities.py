"""The capability document: what this build of the translator does with the whole catalogue.

The mapping report says what one run did with one request. This document says what *any* run
would do with *any* request, and it is produced the same way: by running the translator, over a
probe set instead of over one request, and aggregating the report lines per measure, per option
and per inventory field. Because it is produced by running the translator, it cannot announce
something the translator does not do::

    python -m hisim.renovisor capabilities --out capabilities.json

The probe set is data, not a loop (:class:`ProbeSet`), so the verification harness can reuse it.
It is the anchor -- the vendored mockup with an empty package -- plus the bare baseline with
every optional block absent, plus one probe per optional block present alone, one per measure,
one per enum value of every option, one per boundary of every free numeric option, one per
inventory enum value and one per inventory range boundary, plus the handful of two-change
probes the conditional entries of ``not_implemented_yet.yaml`` need (a solar thermal collector
on an oil boiler cannot be reached by changing one thing).

Every probe runs ``validate`` + ``apply`` + ``translate`` and no simulation, so the whole set
takes a second. A probe that raises a translator error fails the build: the backend marks an
image ``broken`` on that, and the point of the document is that it cannot.

The document's shape is ``measure-capabilities.openapi.yaml``'s
``components.schemas.ImplementedMeasures``, and :meth:`CapabilityDocument.validate` checks it
against that vendored schema before writing.
"""

import copy
import datetime
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml
from jsonschema import Draft202012Validator

from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.apply import apply
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.report import HiSimCommit
from hisim.renovisor.request import (
    AccessLevel,
    CatalogueTable,
    OptionSpec,
    Request,
    RequestError,
    ValueType,
)
from hisim.renovisor.translate import Translator
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
    and a request with nothing optional in it at all. ``BLOCK`` turns one optional block on;
    ``MEASURE`` applies one measure; ``OPTION`` varies one option of one measure; ``FIELD``
    varies one inventory field; ``PAIR`` changes two things at once, which the conditional
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
        subject: The measure id or inventory path the probe is about, for the aggregation.
        value: The value it set, for a per-value status.
    """

    name: str
    kind: ProbeKind
    house: Mapping[str, Any] = field(default_factory=dict)
    measures: Optional[Sequence[Mapping[str, Any]]] = None
    location: Mapping[str, Any] = field(default_factory=dict)
    subject: Optional[str] = None
    value: Any = None

    def document(self, anchor: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the request this probe sends.

        Args:
            anchor: The anchor request, which is never mutated.

        Returns:
            A deep copy of the anchor with this probe's changes applied.
        """
        request = copy.deepcopy(dict(anchor))
        for path, value in self.house.items():
            _write(request["house"], path, value)
        for key, value in self.location.items():
            _write(request["location"], key, value)
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


class ProbeSet:
    """The probes the capability document is aggregated from, as data.

    Everything here is generated from two tables and the frozen catalogue, so a catalogue value
    added tomorrow is probed tomorrow without anybody writing a probe. The two tables are the
    inventory fields worth varying (:attr:`FIELD_VALUES`) and the bounds of the free numeric
    options (:attr:`OPTION_BOUNDS`); both are the *request schema's* own numbers, so nothing is
    invented here either.
    """

    #: The material object every probe of a ``material`` option sends. It is the mockup's own
    #: row, so no material id and no conductivity is invented: rule 5 makes the catalogue's
    #: class names ("EPS", "Mineral wool") the frontend's business, and the translator only ever
    #: sees properties.
    MATERIAL: ClassVar[Dict[str, Any]] = {
        "asp_id": "polystyrene_eps_rigid_board",
        "thermal_conductivity_w_mk": 0.0355,
        "heat_capacity_j_kgk": 1400,
        "density_kg_m3": 20.5,
        "co2_footprint_a1_a3_c3_c4_kg_m2": 21.207108,
        "lifespan_years": 57.5,
    }

    #: The optional blocks a request may carry, each with the smallest body the schema accepts.
    BLOCKS: ClassVar[Dict[str, Dict[str, Any]]] = {
        "hot_water": {"supply": "together_with_heating_system"},
        "ventilation": {"type_of_system": "natural", "air_tightness": "as_built"},
        "temperature_control": {"type_of_system": "traditional_thermostats"},
        "air_conditioning": {"power_in_watt": 3000},
        "appliances": {"white_appliances": "existing"},
        "pv_system": {"size_in_percent_of_roof_area": 50},
        "battery": {"days_to_cover": 2},
        "solar_thermal_system": {"supplies": "dhw_only"},
        "electric_vehicles": {"number": 1},
    }

    #: Inventory paths worth varying, each with the values to probe. Enum lists are the request
    #: schema's; numeric pairs are its ``minimum``/``maximum``. ``ES`` is left out of the
    #: country list because it is refused for want of a TABULA typology, which is a data gap the
    #: document reports as an unsupported country rather than as a capability.
    FIELD_VALUES: ClassVar[Dict[str, Tuple[Any, ...]]] = {
        "building.building_type": (
            "detached_sfh", "semi_detached_sfh", "terraced_sfh", "bungalow", "apartment", "other",
        ),
        "building.construction_year": (1700, 2100),
        "building.absolute_conditioned_floor_area_in_m2": (30, 400),
        "building.number_of_storeys": (1, 6),
        "building.set_heating_temperature_in_celsius": (12, 28),
        "building.roof.shape": ("pitched", "flat"),
        "building.roof.u_value_in_watt_per_m2_per_kelvin": (0.1, 10),
        "building.roof.area_in_m2": (0, 500),
        "building.facade.area_in_m2": (0, 500),
        "building.floor.area_in_m2": (0, 500),
        "building.window.area_in_m2": (0, 500),
        "building.door.area_in_m2": (0, 500),
        "building.window.glazing_panes": (1, 2, 3),
        "building.window.frame_material": ("wood", "plastic", "metal", "composite"),
        "building.window.low_emissivity_coating": (True, False),
        "building.window.outside_shading": (True, False),
        "building.window.thermocover": (True, False),
        "building.door.glazing_panes": (0, 2, 3),
        "building.door.frame_material": ("wood", "plastic", "metal", "composite"),
        "occupancy.number_of_residents": (1, 12),
        "occupancy.home_office_days_per_week": (0, 7),
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
        "heating.flow_temperature_in_celsius": (20, 90),
        "heating.seasonal_efficiency_in_percent": (1, 400),
        "heat_distribution.type_of_system": (
            "surface_heating", "low_temperature_radiator", "conventional_radiator",
        ),
        "hot_water.supply": (
            "together_with_heating_system", "separate_heat_pump", "separate_direct_electric",
        ),
        "hot_water.volume_heating_water_storage_in_liter": (0, 2000),
        "hot_water.tank_and_pipe_insulated": (True, False),
        "ventilation.type_of_system": (
            "natural", "window_trickle_vent", "mechanical_extract", "demand_controlled_extract",
            "mechanical_ventilation_with_heat_recovery",
        ),
        "ventilation.air_tightness": ("as_built", "diy_sealed", "professionally_sealed"),
        "temperature_control.type_of_system": ("traditional_thermostats", "smart_heating_control_system"),
        "air_conditioning.power_in_watt": (0, 50000),
        "appliances.white_appliances": ("existing", "new_efficient"),
        "pv_system.size_in_percent_of_roof_area": (1, 100),
        "pv_system.azimuth": (0, 360),
        "pv_system.tilt": (0, 90),
        "battery.days_to_cover": (1, 14),
        "solar_thermal_system.supplies": ("dhw_only", "dhw_and_space_heating"),
        "solar_thermal_system.collector_type": ("flat_plate", "evacuated_tube"),
        "solar_thermal_system.area_m2": (1, 100),
        "electric_vehicles.number": (1, 2),
        "electric_vehicles.commuting_distance_in_km": (5, 30),
        "electric_vehicles.charging_power_in_watt": (3700, 22000),
    }

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
    #: bounds on the field the option writes into: ``thickness_in_mm`` from ``added_insulation``,
    #: ``new_room_temperature`` from ``set_heating_temperature_in_celsius``, ``installation_year``
    #: from ``construction_year``, and the four device options from their own blocks.
    OPTION_BOUNDS: ClassVar[Dict[str, Tuple[Any, Any]]] = {
        "thickness_in_mm": (10, 500),
        "installation_year": (1700, 2100),
        "u_value_in_watt_per_m2_per_kelvin": (0.1, 10),
        "power_in_watt": (0, 50000),
        "size_in_percent_of_roof_area": (1, 100),
        "days_to_cover": (1, 14),
        "number": (1, 2),
        "new_room_temperature": (12, 28),
    }

    #: The two-change probes the conditional entries of the list need, as
    #: ``name -> (house changes, package)``.
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
                house={"building.roof.shape": None, **{block: None for block in cls.BLOCKS}},
            ),
        ]
        for block, body in cls.BLOCKS.items():
            probes.append(
                Probe(name=f"block:{block}", kind=ProbeKind.BLOCK, house={block: dict(body)},
                      subject=f"house.{block}")
            )
        probes.extend(cls._measure_probes())
        probes.extend(cls._field_probes())
        for name, (house, measures) in cls.PAIRS.items():
            probes.append(
                Probe(
                    name=name,
                    kind=ProbeKind.PAIR,
                    house=dict(house),
                    measures=measures,
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
                for value in cls._option_values(option):
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
    def _field_probes(cls) -> List[Probe]:
        """Return one probe per inventory value worth varying, with its block switched on first."""
        probes: List[Probe] = []
        for path, values in cls.FIELD_VALUES.items():
            block = path.split(".")[0]
            prelude: Dict[str, Any] = (
                {block: dict(cls.BLOCKS[block])} if block in cls.FIELD_BLOCK else {}
            )
            for value in values:
                probes.append(
                    Probe(
                        name=f"field:{path}={value}",
                        kind=ProbeKind.FIELD,
                        house={**prelude, path: value},
                        subject=f"house.{path}",
                        value=value,
                    )
                )
        return probes

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
            options[option.name] = cls._first_value(option)
        return {"id": measure_id, "options": options}

    @classmethod
    def _first_value(cls, option: OptionSpec) -> Any:
        """Return the value a probe sends for one required option when it varies nothing."""
        if option.name == CatalogueTable.MATERIAL:
            return dict(cls.MATERIAL)
        if option.values:
            return option.values[0]
        if option.value_type is ValueType.BOOLEAN:
            return True
        return cls.OPTION_BOUNDS[option.name][0]

    @classmethod
    def _option_values(cls, option: OptionSpec) -> Tuple[Any, ...]:
        """Return the values one option is probed with: its list, its bounds, or both booleans."""
        if option.name == CatalogueTable.MATERIAL:
            return (dict(cls.MATERIAL),)
        if option.values:
            return tuple(option.values)
        if option.value_type is ValueType.BOOLEAN:
            return (True, False)
        return cls.OPTION_BOUNDS[option.name]


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
        translator = Translator(self._directory, self._whitelist)
        results: List[ProbeResult] = []
        for probe in probes if probes is not None else ProbeSet.build():
            results.append(self._one(probe, anchor, translator))
        return tuple(results)

    def _one(self, probe: Probe, anchor: Mapping[str, Any], translator: Translator) -> ProbeResult:
        """Run one probe, turning a refusal into a result rather than into an exception."""
        self._whitelist.forget_hits()
        document = probe.document(anchor)
        try:
            request = Request.parse(document)
        except RequestError as error:
            return ProbeResult(
                probe=probe, refused=tuple(problem.code.value for problem in error.problems)
            )
        applied = apply(request.document["house"], request.measures, self._whitelist)
        translated = translator.translate(request, applied)
        report = translated.report.to_json()
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
        return ProbeResult(
            probe=probe,
            fields=fields,
            measures=measures,
            hits=self._whitelist.hits(),
            targets=targets,
        )


class Aggregation:
    """Reduces the probe results to one status per measure, option, value and inventory field.

    The rule is the frontend side's specification §8 step 4: a status is the worst one any probe
    observed, an option value a probe refused as unknown is left out of ``accepted_values``, and
    the note is the whitelist entry's sentence. "Worst" is
    :meth:`hisim.renovisor.vocabulary.ReportStatus.worst_of`, which is what makes the document a
    promise rather than an average: a run can never report an item worse than it announces.
    """

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

        Two probe kinds are deliberately left out of the *status* and counted only for their
        note. A ``PAIR`` probe constructs an unsupported combination on purpose -- a solar
        thermal collector on an oil boiler -- and a measure's status is about the measure, not
        about a combination, so letting a pair probe set it would announce that the collector
        is never modelled. And an ``OPTION`` probe of a valued option carries one value's own
        status, which belongs in ``values`` and, by the openapi schema's own wording, never
        changes the option's.
        """
        entries: List[Dict[str, Any]] = []
        for measure_id in CatalogueTable.ids():
            observed: List[ReportStatus] = []
            note: Optional[str] = None
            targets: List[str] = []
            option_statuses: Dict[str, List[ReportStatus]] = {}
            option_notes: Dict[str, Optional[str]] = {}
            value_statuses: Dict[str, Dict[Any, Tuple[ReportStatus, Optional[str]]]] = {}
            valued = {spec.name for spec in CatalogueTable.options_of(measure_id) if spec.values}
            for result in results:
                row = result.measures.get(measure_id)
                if row is None:
                    continue
                status, measure_note, options = row
                if result.probe.kind is not ProbeKind.PAIR:
                    observed.append(status)
                note = note or measure_note
                targets.extend(result.targets.get(measure_id, ()))
                own = (
                    result.probe.subject
                    if result.probe.kind is ProbeKind.OPTION and result.probe.subject
                    else ""
                )
                for name, (option_status, option_note) in options.items():
                    if own == f"{measure_id}.{name}" and name in valued:
                        value_statuses.setdefault(name, {})[_key(result.probe.value)] = (
                            option_status,
                            option_note,
                        )
                    elif result.probe.kind is not ProbeKind.PAIR:
                        option_statuses.setdefault(name, []).append(option_status)
                    if option_note is not None and option_notes.get(name) is None:
                        option_notes[name] = option_note
            status = ReportStatus.worst_of(*observed) if observed else ReportStatus.NOT_IMPLEMENTED_YET
            entry: Dict[str, Any] = {
                "measure_id": measure_id,
                "status": MeasureStatus.of(status).value,
                "options": cls._options(measure_id, option_statuses, option_notes, value_statuses),
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
        statuses: Mapping[str, List[ReportStatus]],
        notes: Mapping[str, Optional[str]],
        values: Mapping[str, Mapping[Any, Tuple[ReportStatus, Optional[str]]]],
    ) -> List[Dict[str, Any]]:
        """Return one entry per option the catalogue declares for one measure."""
        entries: List[Dict[str, Any]] = []
        for spec in CatalogueTable.options_of(measure_id):
            observed = statuses.get(spec.name) or [
                status for status, _ in values.get(spec.name, {}).values()
            ]
            status = ReportStatus.worst_of(*observed) if observed else ReportStatus.NOT_IMPLEMENTED_YET
            entry: Dict[str, Any] = {"name": spec.name, "status": status.value}
            if spec.values is not None:
                entry["accepted_values"] = list(spec.values)
                entry["values"] = [
                    cls._value(value, values.get(spec.name, {}).get(_key(value)))
                    for value in spec.values
                ]
            bounds = ProbeSet.OPTION_BOUNDS.get(spec.name)
            if spec.values is None and bounds is not None and spec.value_type is not ValueType.BOOLEAN:
                entry["minimum"], entry["maximum"] = bounds
            note = notes.get(spec.name)
            if note is not None:
                entry["note"] = note
            entries.append(entry)
        return entries

    @classmethod
    def _value(cls, value: Any, observed: Optional[Tuple[ReportStatus, Optional[str]]]) -> Dict[str, Any]:
        """Return one ``values`` entry: the catalogue value with the status its probe observed."""
        status, note = observed if observed is not None else (ReportStatus.USED, None)
        entry: Dict[str, Any] = {
            "value": value,
            "status": (
                ReportStatus.APPROXIMATED.value
                if status is ReportStatus.DEFAULTED
                else status.value
            ),
        }
        if note is not None:
            entry["note"] = note
        return entry

    @classmethod
    def fields(cls, results: Sequence[ProbeResult]) -> List[Dict[str, Any]]:
        """Return the ``fields`` array: the same aggregation over the inventory.

        As for a measure's options, a probe that varies one field's value contributes that
        value's own status to ``values`` and not to the field's, and a ``PAIR`` probe
        contributes its note and not its status. What is left for the field's own status is
        every probe that carried the field without being about it -- the anchor, the block
        probes and the measure probes -- and, when nothing did, the worst of its values.
        """
        statuses: Dict[str, List[ReportStatus]] = {}
        notes: Dict[str, Optional[str]] = {}
        per_value: Dict[str, Dict[Any, Tuple[ReportStatus, Optional[str]]]] = {}
        for result in results:
            probe = result.probe
            own = probe.subject if probe.kind is ProbeKind.FIELD and probe.subject else ""
            for path, (status, note) in result.fields.items():
                if path == own:
                    per_value.setdefault(path, {})[_key(probe.value)] = (status, note)
                elif probe.kind is not ProbeKind.PAIR:
                    statuses.setdefault(path, []).append(status)
                if note is not None and notes.get(path) is None:
                    notes[path] = note
        entries: List[Dict[str, Any]] = []
        for path in sorted(set(statuses) | set(per_value)):
            observed = statuses.get(path) or [
                status for status, _ in per_value.get(path, {}).values()
            ]
            status = ReportStatus.worst_of(*observed)
            entry: Dict[str, Any] = {"path": path, "status": status.value}
            if per_value.get(path):
                entry["values"] = [
                    {
                        "value": value,
                        "status": (
                            ReportStatus.APPROXIMATED.value
                            if value_status is ReportStatus.DEFAULTED
                            else value_status.value
                        ),
                        **({"note": value_note} if value_note else {}),
                    }
                    for value, (value_status, value_note) in per_value[path].items()
                ]
            if notes.get(path) is not None:
                entry["note"] = notes[path]
            entry["substitution"] = cls.is_substitution(notes.get(path))
            entries.append(entry)
        return entries

    @classmethod
    def tally(cls, measures: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
        """Return how many measures carry each status, which acceptance compares against §4.2."""
        counts: Dict[str, int] = {status.value: 0 for status in MeasureStatus}
        for entry in measures:
            counts[str(entry["status"])] += 1
        return counts


def _key(value: Any) -> Any:
    """Return a hashable key for one probed value, so a material object can index a dictionary."""
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True)
    return value


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
        generated_at: Optional[str] = None,
        base_files_directory: Optional[Path] = None,
    ) -> "CapabilityDocument":
        """Read the catalogue, check the frozen table against it, run the probes and aggregate.

        Args:
            measures_path: A ``measures.yaml`` to check the frozen table against; the vendored
                copy when omitted.
            generated_at: Override the document's one non-deterministic field, so that a test
                can compare two runs byte for byte.
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
            "engine_version": f"{cls.ENGINE}-{HiSimCommit.of() or 'unknown'}",
            "translator": {
                "version": TRANSLATOR_VERSION,
                "commit": HiSimCommit.of(),
                "hisim_commit": HiSimCommit.of(),
                "request_schema_version": 1,
                "catalogue_revision": cls.catalogue_revision(),
                "generated_at": generated_at or datetime.datetime.now(datetime.timezone.utc)
                .replace(microsecond=0)
                .isoformat(),
                "probes": len(results),
            },
            "measures": measures,
            "fields": Aggregation.fields(results),
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

    def validate(self) -> None:
        """Check the document against the vendored ``measure-capabilities.openapi.yaml``.

        Raises:
            jsonschema.ValidationError: On the first way the document is not what the contract
                says ``GET /measures`` returns.
        """
        openapi = ContractFiles.capabilities_schema()
        schema = dict(openapi)
        schema["$ref"] = "#/components/schemas/ImplementedMeasures"
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


def assert_catalogue_matches(measures_path: Optional[Path] = None) -> None:
    """Raise when the frozen catalogue table does not equal ``measures.yaml`` (T-CAT).

    Args:
        measures_path: The file to compare against; the vendored copy when omitted.

    Raises:
        AssertionError: Naming the first difference. A catalogue edit has to be a deliberate
            change to :class:`hisim.renovisor.request.CatalogueTable`, not a surprise in a
            user's refused request.
    """
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
                str(option["value_type"]),
                tuple(option["values"]) if option.get("values") is not None else None,
            )
            for option in (measure.get("options") or [])
        ]
    frozen = {
        measure_id: [
            (option.name, option.access_level.value, option.value_type.value, option.values)
            for option in options
        ]
        for measure_id, options in CatalogueTable.BY_ID.items()
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
