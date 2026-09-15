"""The generated translation map: every measure, every effect, every HiSim field, on one page.

Decision V1 asked for one thing that can be handed to the frontend team beside the contract PR and
to a reviewer beside the code: a single HTML page that shows, for all 33 catalogue measures and
every value each of their options can take, what HiSim does with it — which inventory path the
measure writes, which component and field that path reaches through
:class:`~hisim.renovisor.bindings.Bindings`, which recorded base file or variant it switches, and
whether the result is a simulation, a refusal or nothing at all::

    python -m hisim.renovisor.map --out roadmap/renovisor/translation_map.html

The page is written to a repository file and committed. It is not published anywhere: it is
documentation that travels with the code, and ``tests/test_renovisor_map.py`` asserts that the
committed file is byte-for-byte what this module produces today, so a registry, catalogue or
bindings change that is not reflected in the map fails the build — the same discipline the
energy-system JSON schema is kept under.

Three properties the page must keep, all of them checked by that test. It is **self-contained**:
no external script, stylesheet, font or image, because a page that fetches anything is a page that
renders differently in six months. It is **deterministic**: no timestamp, no random id, no
dictionary iteration that depends on insertion luck, so that regenerating it without changing the
code produces no diff. And it carries the **decision ids** from the registry docstrings (decision
V2) and the **display names** from the catalogue beside the HiSim spellings (decision V3), so that
a reader who knows only one of the two vocabularies can still follow a row.

What the page deliberately does not do is resolve anything. Effects are collected exactly as the
measures produce them — no TABULA lookup, no U-value composition, no sizing law — because the map
is about the translation rules, not about one particular house. Where a number would otherwise
depend on the building, :class:`ReferenceBuilding` supplies one fixed U-value and the legend says
so.
"""

import argparse
import difflib
import html
import itertools
import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple, Type

from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.application import ApplicationResult, PackageApplication
from hisim.energy_system.loader import dump_energy_system, load_energy_system
from hisim.renovisor.base_files import BaseFileKey, BaseFiles
from hisim.renovisor.bindings import BindingError, Bindings
from hisim.renovisor.catalogue import AccessLevel, Catalogue, MeasureSpec, OptionSpec, OptionValueType
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.effects import (
    AddThermalResistance,
    Effect,
    Effects,
    EnableGroup,
    LawRequest,
    NoEffect,
    Refusal,
    SelectBaseFile,
    SelectVariant,
    SetInventoryField,
    SetUValue,
)
from hisim.renovisor.envelope import EnvelopePaths, ExclusivityTable, RegulatoryTargets
from hisim.renovisor.laws import LawResolver, StaticDemandEstimator
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.options import Options
from hisim.renovisor.parametriser import Edit, Parametriser
from hisim.renovisor.reasons import ReasonCode
from hisim.renovisor.registry import MeasureRegistry
from hisim.renovisor.report import MappingReport, ReportStatus
from hisim.renovisor.vocabulary import HeatGenerator, ThermalElement


class MapStatus(str, Enum):
    """What becomes of one measure at one combination of option values.

    ``SIMULATED`` — every effect is something the parametriser of step 5 can apply: a write, a
    variant switch, a base-file selection. ``LAW_PENDING`` — the same, except that one value is a
    sizing law that needs simulation inputs and is therefore resolved later. ``BLOCKED_ON_DATA`` —
    a refusal that goes away when data arrives: a material with no database row, or a measure with
    no defined build-up. ``REFUSED`` — a refusal that needs a decision or a new base file, not
    data. ``NO_EFFECT`` — accepted and changed nothing, because HiSim has no model for it.

    The order of the members is the order of preference, best first, which
    :meth:`MeasureMap.headline_status` uses to give a measure with several outcomes one status.
    """

    SIMULATED = "SIMULATED"
    LAW_PENDING = "LAW_PENDING"
    NO_EFFECT = "NO_EFFECT"
    BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
    REFUSED = "REFUSED"

    def label(self) -> str:
        """Return the status as it is written on the page, e.g. ``"blocked on data"``."""
        return self.value.replace("_", " ").lower()

    @classmethod
    def in_preference_order(cls) -> Tuple["MapStatus", ...]:
        """Return every status, best outcome first, which is declaration order."""
        return tuple(cls)


class StatusRules:
    """Which reason codes put a refusal into which status.

    The distinction the page is built around is "waiting for data" against "waiting for a
    decision": a material with no database row (decision Q6) becomes simulable the moment the
    materials team adds the row, while a hybrid heat pump needs a HiSim component that nobody has
    written. A reader planning work wants to see the two apart.
    """

    #: Refusals that disappear when a database row or a build-up definition arrives.
    BLOCKED_ON_DATA_REASONS: ClassVar[Tuple[ReasonCode, ...]] = (
        ReasonCode.MATERIAL_NOT_IN_DATABASE,
        ReasonCode.UNDEFINED_MEASURE_BUILDUP,
    )

    @classmethod
    def of(cls, effects: Sequence[Effect]) -> MapStatus:
        """Return the status one measure's effects come to.

        Args:
            effects: Everything the measure recorded, unresolved and in order.

        Returns:
            The :class:`MapStatus`. A refusal outranks everything, a pending law outranks a plain
            write, and a measure that recorded nothing but ``NoEffect`` is ``NO_EFFECT``.
        """
        refusals = [effect for effect in effects if isinstance(effect, Refusal)]
        if refusals:
            blocked = all(refusal.reason in cls.BLOCKED_ON_DATA_REASONS for refusal in refusals)
            return MapStatus.BLOCKED_ON_DATA if blocked else MapStatus.REFUSED
        if any(isinstance(effect, SetInventoryField) and effect.law is not None for effect in effects):
            return MapStatus.LAW_PENDING
        if effects and all(isinstance(effect, NoEffect) for effect in effects):
            return MapStatus.NO_EFFECT
        return MapStatus.SIMULATED


class ReferenceBuilding:
    """The fixed inputs the map runs every measure against.

    A measure that defaults its layer thickness asks for the element's current U-value, and a real
    one would come from the inventory or from TABULA — which would make the page depend on one
    house and on a CSV. The map instead states one reference U-value for every element and prints
    it in the legend, so a thickness on the page is read as "against this baseline" rather than as
    a promise about a particular dwelling.
    """

    #: The pre-measure U-value every element is given, in W/m2K. A plausible unrenovated Irish
    #: element; nothing depends on the exact number except the thicknesses the page shows.
    U_VALUE_IN_WATT_PER_M2_PER_KELVIN: ClassVar[float] = 2.0

    #: The example inventory the measures read their building facts from.
    INVENTORY_PATH: ClassVar[Path] = (
        Path(__file__).resolve().parents[2] / "tests" / "renovisor" / "example_inventory_ie_1988_detached.json"
    )

    def u_value(self, element: ThermalElement) -> float:
        """Return the reference pre-measure U-value, the same for every element."""
        del element
        return self.U_VALUE_IN_WATT_PER_M2_PER_KELVIN

    def source_of(self, element: ThermalElement) -> str:
        """Return the phrase naming this class as where the U-value came from."""
        del element
        return "the translation map's reference building"

    @classmethod
    def inventory(cls) -> Inventory:
        """Return the example Irish inventory the map reads building facts from.

        Returns:
            The :class:`~hisim.renovisor.inventory.Inventory`, loaded fresh so that nothing a
            measure does can leak from one row of the page into the next.

        Raises:
            FileNotFoundError: When the example inventory is missing from the checkout.
        """
        return Inventory.from_dict(json.loads(cls.INVENTORY_PATH.read_text(encoding="utf-8")))


class MapRepresentatives:
    """The values the map sends for integer options the catalogue leaves open.

    A catalogue option such as ``days_to_cover`` has no value list, so the product over "every
    option value" has nothing to iterate. These are the values the page uses instead. They are
    presentation inputs, not defaults: nothing in the translation layer reads them, and each is
    chosen to show a fate the page would otherwise not display — hence three vehicle counts, one
    of which is the refusal.
    """

    #: ``(measure id, option id)`` -> the values to show, in the order they are shown.
    BY_OPTION: ClassVar[Dict[Tuple[str, str], Tuple[int, ...]]] = {
        ("AIR_CONDITIONERS", "power_in_watt"): (3500,),
        ("PHOTOVOLTAIC_SYSTEM", "size_in_percent_of_roof_area"): (50,),
        ("BATTERY_SYSTEM", "days_to_cover"): (2,),
        ("ELECTRIC_VEHICLE", "number"): (0, 1, 2),
        ("CHANGE_ROOM_TEMPERATURE", "new_room_temperature"): (21,),
    }

    @classmethod
    def values_for(cls, measure_id: str, option: OptionSpec) -> Tuple[Any, ...]:
        """Return the values the page sends for one option, or none when it is left out.

        Args:
            measure_id: The measure the option belongs to.
            option: The option's catalogue spec.

        Returns:
            The values as the JSON types a request would carry. A boolean option is always shown
            both ways, because a boolean has no catalogue value list to default from and a measure
            that branches on it does something different in each case. Any other ``EXPERTS`` option
            with no value list returns an empty tuple, which means "omit it and let the measure's
            own default apply" (requirement M7) — which is what makes a defaulted thickness and its
            source visible on the page.

        Raises:
            KeyError: When an ``EVERYONE`` integer option has neither a catalogue value list nor
                an entry here, which a catalogue revision can cause and the map test catches.
        """
        if option.values:
            if option.value_type is OptionValueType.INTEGER:
                return tuple(int(value) for value in option.values)
            return tuple(option.values)
        if option.value_type is OptionValueType.BOOLEAN:
            return (True, False)
        if option.access_level is AccessLevel.EXPERTS:
            return ()
        return cls.BY_OPTION[(measure_id, option.option_id)]


@dataclass(frozen=True)
class OptionChoice:
    """One option set to one value, as the page prints it.

    Args:
        option_id: The snake_case option id a request carries.
        display_name: The catalogue's own spelling of the option.
        value: The value in HiSim spelling, as a string.
        display_value: The catalogue's own spelling of the value.
        defaulted: Whether the value came from a default rather than from the request.
        default_source: Where a defaulted value came from; empty when it was supplied.
    """

    option_id: str
    display_name: str
    value: str
    display_value: str
    defaulted: bool
    default_source: str


@dataclass(frozen=True)
class EffectRow:
    """One effect of one measure, joined onward to the inventory and to HiSim.

    Args:
        summary: The effect in one line, e.g. ``+R FACADE polystyrene_eps_rigid_board 140 mm``.
        inventory_path: The inventory path the effect writes, or empty when it writes none.
        hisim_target: Where that path lands — a component and field, a constructor, a base file
            name, or the reason code's description for an effect that lands nowhere.
    """

    summary: str
    inventory_path: str
    hisim_target: str


@dataclass(frozen=True)
class Outcome:
    """One distinct result of a measure, with every option combination that produces it.

    Collapsing is what keeps the page readable: warm roof insulation has four materials, two of
    which behave identically (both refuse), so they share one row and the row lists both.

    Args:
        choice_sets: Every option combination that produced this outcome, in page order.
        status: What the outcome is.
        effects: The effects, joined onward.
    """

    choice_sets: Tuple[Tuple[OptionChoice, ...], ...]
    status: MapStatus
    effects: Tuple[EffectRow, ...]


@dataclass(frozen=True)
class MeasureMap:
    """Everything the page shows about one catalogue measure.

    Args:
        measure_id: The UPPER_SNAKE id a request carries.
        display_name: The catalogue's label.
        category: The catalogue's top-level grouping, which becomes a section of the page.
        subcategory: The catalogue's second-level grouping.
        decisions: The decision ids the registry function's docstring names (decision V2).
        outcomes: The distinct results, in the order the option product produced them.
        elements: The thermal elements this measure touches, and how.
        switches: The base-file and variant switches this measure can ask for.
    """

    measure_id: str
    display_name: str
    category: str
    subcategory: str
    decisions: Tuple[str, ...]
    outcomes: Tuple[Outcome, ...]
    elements: Mapping[str, str]
    switches: Tuple[str, ...]

    def headline_status(self) -> MapStatus:
        """Return the best status any of this measure's outcomes reaches.

        A measure whose four materials include two with database rows does work today, and the
        page's per-measure count says so; the two blocked materials are still visible as their own
        rows.

        Returns:
            The first :class:`MapStatus` in preference order that one of the outcomes carries.
        """
        reached = {outcome.status for outcome in self.outcomes}
        for status in MapStatus.in_preference_order():
            if status in reached:
                return status
        return MapStatus.NO_EFFECT


class TraceExample:
    """The one worked example the trace tab follows from end to end.

    A map of rules is not the same thing as a worked example: the map says what
    ``EXTERNAL_INSULATION`` does to an inventory path, and only an example says what a particular
    Irish house actually ends up running. Both are committed files so the trace changes only when
    something real changes.

    Its three demand figures are **stated, not simulated**. Resolving the battery law needs a
    household's daily electricity and its heating's daily electricity, and computing those means
    running the LoadProfileGenerator -- which a documentation page must not depend on. They are
    round numbers of the right order for this dwelling, and the page says so beside them.
    """

    #: The dwelling: a detached Irish single-family house of 1988 with a gas boiler, radiators, no
    #: photovoltaics and no car.
    INVENTORY_PATH: ClassVar[Path] = (
        Path(__file__).resolve().parents[2] / "tests" / "renovisor" / "example_inventory_ie_1988_detached.json"
    )

    #: The package: a deep retrofit that insulates two elements, lays in floor heating, raises the
    #: room temperature, replaces the boiler by a heat pump and adds photovoltaics and a battery.
    PACKAGE_PATH: ClassVar[Path] = (
        Path(__file__).resolve().parents[2] / "tests" / "renovisor" / "example_package_gas_to_heat_pump.json"
    )

    #: Where the recorded base files live.
    BASE_FILES_PATH: ClassVar[Path] = Path(__file__).resolve().parents[2] / "energy_systems"

    #: STATED, not simulated: the residents' own electricity, in kWh per day.
    HOUSEHOLD_IN_KWH_PER_DAY: ClassVar[float] = 12.0

    #: STATED, not simulated: the heat pump's electricity, in kWh per day.
    HEAT_PUMP_IN_KWH_PER_DAY: ClassVar[float] = 25.0

    #: STATED, not simulated: the vehicles' charging energy, in kWh per day; this dwelling has no
    #: electric vehicle, and the contract carries no annual mileage yet in any case.
    VEHICLE_IN_KWH_PER_DAY: ClassVar[float] = 0.0

    @classmethod
    def inventory(cls) -> Inventory:
        """Return the example dwelling, loaded fresh so nothing leaks between uses."""
        return Inventory.from_dict(json.loads(cls.INVENTORY_PATH.read_text(encoding="utf-8")))

    @classmethod
    def package(cls) -> List[Dict[str, Any]]:
        """Return the example package's measure list.

        Returns:
            The list under the document's ``measures`` key, which is the shape decision Q1 fixed.
        """
        document = json.loads(cls.PACKAGE_PATH.read_text(encoding="utf-8"))
        measures: List[Dict[str, Any]] = list(document["measures"])
        return measures

    @classmethod
    def estimator(cls, generator: HeatGenerator) -> StaticDemandEstimator:
        """Return the stated-number estimator the trace's sizing laws read.

        Args:
            generator: The heat generator the dwelling ends up with, which decides whether the
                heating term contributes at all.
        """
        return StaticDemandEstimator(
            household_in_kwh_per_day=cls.HOUSEHOLD_IN_KWH_PER_DAY,
            heat_pump_in_kwh_per_day=cls.HEAT_PUMP_IN_KWH_PER_DAY,
            vehicle_in_kwh_per_day=cls.VEHICLE_IN_KWH_PER_DAY,
            generator=generator,
        )


@dataclass(frozen=True)
class InventoryChange:
    """One inventory leaf the package changed, before and after, with what changed it.

    Args:
        path: The dotted inventory path.
        before: The value the survey carried; ``None`` when the field was absent or null.
        after: The value the measures left behind.
        measure_ids: The measures that asked for it, in package order; empty when the change came
            out of composing several layers rather than out of one measure.
    """

    path: str
    before: Any
    after: Any
    measure_ids: Tuple[str, ...]


@dataclass(frozen=True)
class DiffHunk:
    """One hunk of the unified diff between the base file and the parametrised one.

    Args:
        header: The ``@@`` line, kept so a reader can locate the hunk in the file.
        lines: The hunk's lines, each with its leading ``+``, ``-`` or space.
        annotations: What asked for the changes in this hunk -- an inventory path, a measure id or
            a sizing law -- in the order the edits were made, without repetition.
    """

    header: str
    lines: Tuple[str, ...]
    annotations: Tuple[str, ...]


@dataclass(frozen=True)
class TraceData:
    """One inventory and one package followed through to a parametrised energy-system file.

    Args:
        base_file_name: The recorded file the package selected.
        inventory_changes: Every leaf the measures changed, in path order.
        hunks: The unified diff from the base file to the parametrised one, annotated.
        report_lines: The translation report, as :meth:`~hisim.renovisor.report.MappingReport.
            to_list` returns it.
        law_rules: The arithmetic of every sizing law that was resolved, by inventory path.
    """

    base_file_name: str
    inventory_changes: Tuple[InventoryChange, ...]
    hunks: Tuple[DiffHunk, ...]
    report_lines: Tuple[Dict[str, Any], ...]
    law_rules: Tuple[Tuple[str, str], ...]


class TraceDiff:
    """Turns two YAML documents into annotated hunks.

    The annotation is what makes the diff readable as a translation rather than as a patch: every
    changed line is looked up in the parametriser's own list of edits, by the component it sits
    under and the key it writes, so the hunk can say "this line is here because the inventory says
    ``building_config.envelope_details.roof_u_value_in_watt_per_m2_per_kelvin``".
    """

    #: How many unchanged lines of context each hunk carries.
    CONTEXT_LINES: ClassVar[int] = 2

    #: A line that names a component: a bare ``Key:`` at any indentation.
    COMPONENT_PATTERN: ClassVar[str] = r"^\s*([A-Za-z0-9_]+):\s*$"

    #: A line that writes a value: ``key: value`` at any indentation.
    FIELD_PATTERN: ClassVar[str] = r"^\s*([A-Za-z0-9_]+):"

    @classmethod
    def hunks(
        cls, base_text: str, parametrised_text: str, edits: Sequence[Edit], components: Sequence[str]
    ) -> Tuple[DiffHunk, ...]:
        """Return the annotated unified diff between two energy-system documents.

        Args:
            base_text: The recorded base file.
            parametrised_text: The file the calculation would run.
            edits: Every change the parametriser made, with its source.
            components: The component names of the document, so a bare ``Key:`` line can be told
                from a nested mapping that happens to look like one.

        Returns:
            The hunks, in file order.
        """
        by_field = cls._edits_by_field(edits)
        owners = cls._component_of_line(parametrised_text, components)
        hunks: List[DiffHunk] = []
        current: List[str] = []
        header = ""
        sources: List[str] = []
        new_line_number = 0
        for line in difflib.unified_diff(
            base_text.splitlines(),
            parametrised_text.splitlines(),
            fromfile="base",
            tofile="parametrised",
            lineterm="",
            n=cls.CONTEXT_LINES,
        ):
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("@@"):
                if current:
                    hunks.append(
                        DiffHunk(header=header, lines=tuple(current), annotations=tuple(sources))
                    )
                header, current, sources = line, [], []
                new_line_number = cls._new_start(line)
                continue
            current.append(line)
            if not line.startswith("-"):
                new_line_number += 1
            if line.startswith("+"):
                for source in cls._sources_of(line[1:], owners.get(new_line_number - 1, ""), by_field):
                    if source not in sources:
                        sources.append(source)
        if current:
            hunks.append(DiffHunk(header=header, lines=tuple(current), annotations=tuple(sources)))
        return tuple(hunks)

    @classmethod
    def _new_start(cls, header: str) -> int:
        """Return the first line number of the new file a ``@@`` header names, zero-based."""
        match = re.search(r"\+(\d+)", header)
        return int(match.group(1)) - 1 if match is not None else 0

    @classmethod
    def _edits_by_field(cls, edits: Sequence[Edit]) -> Dict[Tuple[str, str], List[str]]:
        """Return the sources of every edit, keyed by the component and field it wrote."""
        found: Dict[Tuple[str, str], List[str]] = {}
        for edit in edits:
            segments = edit.location.split(".")
            if len(segments) < 2:
                continue
            key = (segments[1], segments[-1])
            found.setdefault(key, [])
            if edit.source not in found[key]:
                found[key].append(edit.source)
        return found

    @classmethod
    def _component_of_line(cls, text: str, components: Sequence[str]) -> Dict[int, str]:
        """Return, per zero-based line of *text*, which component that line belongs to."""
        known = set(components)
        owners: Dict[int, str] = {}
        current = ""
        for number, line in enumerate(text.splitlines()):
            match = re.match(cls.COMPONENT_PATTERN, line)
            if match is not None and match.group(1) in known:
                current = match.group(1)
            owners[number] = current
        return owners

    @classmethod
    def _sources_of(
        cls, line: str, component: str, by_field: Mapping[Tuple[str, str], List[str]]
    ) -> Tuple[str, ...]:
        """Return what asked for one added line, by the component and key it writes."""
        match = re.match(cls.FIELD_PATTERN, line)
        if match is None:
            return ()
        return tuple(by_field.get((component, match.group(1)), ()))


@dataclass(frozen=True)
class MapData:
    """Everything the page is rendered from, collected once.

    Args:
        measures: One :class:`MeasureMap` per catalogue measure, in catalogue order.
        contract_commit: The contract commit ``PINNED.yaml`` records for ``openapi.yaml``.
        contract_sha256: Its content hash, from the same record.
        trace: One worked example followed from the inventory through to the parametrised file,
            for the trace tab; ``None`` when a caller collected the rules alone.
    """

    measures: Tuple[MeasureMap, ...]
    contract_commit: str
    contract_sha256: str
    trace: Optional[TraceData] = None

    @classmethod
    def collect(cls) -> "MapData":
        """Run every measure at every option combination and record what happens.

        Nothing is resolved: the effects are read straight off the accumulator, so a page row
        shows what the measure layer decided rather than what one house would come to.

        Returns:
            The :class:`MapData`.

        Raises:
            ValidationError: When a measure rejects a value the catalogue offers, which would be a
                registry bug and is what check 2 of ``measures_v2_requirements.md`` §7.5 already
                guards against.
        """
        catalogue = Catalogue.load()
        materials = InsulationMaterials.load()
        targets = RegulatoryTargets.load()
        inventory = ReferenceBuilding.inventory()
        pin = ContractFiles.pinned()["files"][ContractFiles.OPENAPI_FILENAME]
        measures = tuple(
            cls._collect_measure(spec, materials, targets, inventory) for spec in catalogue.measures()
        )
        return cls(
            measures=measures,
            contract_commit=str(pin["commit"]),
            contract_sha256=str(pin["sha256"]),
            trace=cls.collect_trace(TraceExample),
        )

    @classmethod
    def collect_trace(cls, example: Type["TraceExample"]) -> TraceData:
        """Follow one inventory and one package through to a parametrised energy-system file.

        Nothing is simulated and nothing is cached: the measures are applied, the sizing laws are
        resolved against the example's stated demands, and the file is written. The only two files
        it reads are the processed TABULA table and the recorded base file, which is what lets the
        page be regenerated on a machine with no weather data and no LoadProfileGenerator.

        Args:
            example: The example to trace, :class:`TraceExample` unless a test hands in another.

        Returns:
            The :class:`TraceData` the trace tab is rendered from.

        Raises:
            RefusalError: When the example package cannot be simulated, which would mean the
                committed example and the committed registry have drifted apart.
        """
        pre = example.inventory()
        application = PackageApplication(Catalogue.load(), MeasureRegistry(), InsulationMaterials.load())
        result = application.apply(pre, example.package())
        estimator = example.estimator(result.base_file_key.generator)
        parametriser = Parametriser(example.BASE_FILES_PATH)
        parametrised = parametriser.parametrise(result, estimator)
        base = load_energy_system(example.BASE_FILES_PATH / result.base_file_name)
        return TraceData(
            base_file_name=result.base_file_name,
            inventory_changes=cls._inventory_changes(pre, result),
            hunks=TraceDiff.hunks(
                dump_energy_system(base),
                parametrised.yaml_text,
                parametrised.edits,
                tuple(base.declared_components()),
            ),
            report_lines=tuple(result.report.to_list()),
            law_rules=tuple(
                (path, LawResolver.resolve(law, result.inventory, estimator).rule)
                for path, law in sorted(result.pending_laws.items())
            ),
        )

    @classmethod
    def _inventory_changes(
        cls, pre: Inventory, result: ApplicationResult
    ) -> Tuple[InventoryChange, ...]:
        """Return every inventory leaf the package changed, in path order.

        Args:
            pre: The dwelling as it was surveyed.
            result: What applying the package produced.

        Returns:
            One :class:`InventoryChange` per changed leaf, each carrying the measures that asked
            for it.
        """
        post = result.inventory
        by_path: Dict[str, List[str]] = {}
        for measure_id, paths in sorted(result.paths_by_measure.items()):
            for path in paths:
                by_path.setdefault(path, []).append(measure_id)
        changes = [
            InventoryChange(
                path=path,
                before=pre.get(path),
                after=post.get(path),
                measure_ids=tuple(by_path.get(path, ())),
            )
            for path in sorted(set(post.leaf_paths()) | set(by_path))
            if pre.get(path) != post.get(path)
        ]
        return tuple(changes)

    def statuses(self) -> Dict[MapStatus, Tuple[str, ...]]:
        """Return, per status, the measures whose headline status it is.

        Returns:
            A mapping with every status as a key, each holding the measure ids in catalogue order,
            so a reader can read both the counts and the membership off one structure.
        """
        grouped: Dict[MapStatus, List[str]] = {status: [] for status in MapStatus.in_preference_order()}
        for measure in self.measures:
            grouped[measure.headline_status()].append(measure.measure_id)
        return {status: tuple(ids) for status, ids in grouped.items()}

    def decision_ids(self) -> Tuple[str, ...]:
        """Return every decision id any registry docstring names, sorted for a stable filter."""
        found = {decision for measure in self.measures for decision in measure.decisions}
        return tuple(sorted(found))

    def categories(self) -> Tuple[str, ...]:
        """Return the catalogue's categories in catalogue order, without repeating one."""
        ordered: List[str] = []
        for measure in self.measures:
            if measure.category not in ordered:
                ordered.append(measure.category)
        return tuple(ordered)

    @classmethod
    def _collect_measure(
        cls,
        spec: MeasureSpec,
        materials: InsulationMaterials,
        targets: RegulatoryTargets,
        inventory: Inventory,
    ) -> MeasureMap:
        """Run one measure at every option combination and collapse identical results."""
        outcomes: List[Outcome] = []
        keyed: Dict[Tuple[Any, ...], int] = {}
        elements: Dict[str, str] = {}
        switches: List[str] = []
        for supplied_choices in cls._combinations(spec):
            effects, report = cls._run(spec, supplied_choices, materials, targets, inventory)
            choices = supplied_choices + cls._defaulted_choices(spec, report)
            recorded = effects.all()
            status = StatusRules.of(recorded)
            rows = tuple(EffectDescriber.describe(effect) for effect in recorded)
            cls._note_elements(recorded, elements)
            cls._note_switches(recorded, switches)
            key = (status.value, tuple((row.summary, row.inventory_path, row.hisim_target) for row in rows))
            if key in keyed:
                index = keyed[key]
                merged = outcomes[index]
                outcomes[index] = Outcome(
                    choice_sets=merged.choice_sets + (choices,), status=merged.status, effects=merged.effects
                )
                continue
            keyed[key] = len(outcomes)
            outcomes.append(Outcome(choice_sets=(choices,), status=status, effects=rows))
        return MeasureMap(
            measure_id=spec.measure_id,
            display_name=spec.display_name,
            category=spec.category,
            subcategory=spec.subcategory,
            decisions=MeasureRegistry.decisions_for(spec.measure_id),
            outcomes=tuple(outcomes),
            elements=dict(sorted(elements.items())),
            switches=tuple(switches),
        )

    @classmethod
    def _combinations(cls, spec: MeasureSpec) -> Tuple[Tuple[OptionChoice, ...], ...]:
        """Return every combination of option values the page shows for one measure."""
        per_option: List[List[Tuple[str, Any]]] = []
        for option in spec.options:
            values = MapRepresentatives.values_for(spec.measure_id, option)
            if not values:
                continue
            per_option.append([(option.option_id, value) for value in values])
        if not per_option:
            return ((),)
        combinations = []
        for product in itertools.product(*per_option):
            combinations.append(
                tuple(
                    OptionChoice(
                        option_id=option_id,
                        display_name=spec.option(option_id).display_name,
                        value=str(value),
                        display_value=spec.option(option_id).display_value_of(str(value)),
                        defaulted=False,
                        default_source="",
                    )
                    for option_id, value in product
                )
            )
        return tuple(combinations)

    @classmethod
    def _run(
        cls,
        spec: MeasureSpec,
        choices: Tuple[OptionChoice, ...],
        materials: InsulationMaterials,
        targets: RegulatoryTargets,
        inventory: Inventory,
    ) -> Tuple[Effects, MappingReport]:
        """Run one registry function over one combination and return its effects and report.

        The report comes back because the defaults a measure applies are only visible there: an
        Experts option the page omits leaves a ``DEFAULTED`` line naming the value and its source,
        which is what the page's ``defaulted`` marker shows (requirement M7).
        """
        supplied: Dict[str, Any] = {}
        for choice in choices:
            option = spec.option(choice.option_id)
            if option.value_type is OptionValueType.INTEGER:
                supplied[choice.option_id] = int(choice.value)
            elif option.value_type is OptionValueType.BOOLEAN:
                supplied[choice.option_id] = choice.value == str(True)
            else:
                supplied[choice.option_id] = choice.value
        effects = Effects(materials, ReferenceBuilding(), targets)
        report = MappingReport()
        options = Options(spec, supplied, report, "package.measures[0]")
        MeasureRegistry.function_for(spec.measure_id)(options, inventory, effects)
        return effects, report

    #: How a ``DEFAULTED`` report line spells "the value that was used" before the value itself.
    DEFAULT_NOTE_PREFIX: ClassVar[str] = "absent from the request; using "

    #: The path prefix every option line of the page's one-measure package sits under.
    OPTION_PATH_PREFIX: ClassVar[str] = "package.measures[0].options."

    @classmethod
    def _defaulted_choices(cls, spec: MeasureSpec, report: MappingReport) -> Tuple[OptionChoice, ...]:
        """Return one choice per option the measure defaulted, with the source for the hover text.

        Args:
            spec: The measure's catalogue spec, for the option's display name.
            report: The report the run filled in.

        Returns:
            The defaulted choices in option-id order, so the page is stable.
        """
        choices: List[OptionChoice] = []
        for row in report.to_list():
            if row["status"] != ReportStatus.DEFAULTED.value:
                continue
            path = str(row["path"])
            if not path.startswith(cls.OPTION_PATH_PREFIX):
                continue
            option_id = path[len(cls.OPTION_PATH_PREFIX):]
            value = str(row["note"]).replace(cls.DEFAULT_NOTE_PREFIX, "", 1)
            choices.append(
                OptionChoice(
                    option_id=option_id,
                    display_name=spec.option(option_id).display_name,
                    value=value,
                    display_value=value,
                    defaulted=True,
                    default_source=str(row.get("rule", "")),
                )
            )
        return tuple(sorted(choices, key=lambda choice: choice.option_id))

    @classmethod
    def _note_elements(cls, effects: Sequence[Effect], elements: Dict[str, str]) -> None:
        """Record which thermal elements this measure adds resistance to or replaces."""
        for effect in effects:
            if isinstance(effect, AddThermalResistance):
                elements[effect.element.value] = "R"
            elif isinstance(effect, SetUValue):
                elements[effect.element.value] = "U"

    @classmethod
    def _note_switches(cls, effects: Sequence[Effect], switches: List[str]) -> None:
        """Record which base-file or variant switches this measure can ask for."""
        for effect in effects:
            label = ""
            if isinstance(effect, SelectVariant):
                label = f"{effect.variant}={effect.option}"
            elif isinstance(effect, EnableGroup):
                label = f"group {effect.group}"
            elif isinstance(effect, SelectBaseFile):
                label = BaseFileDescriber.wish(effect)
            if label and label not in switches:
                switches.append(label)


class BaseFileDescriber:
    """Turns a base-file wish into the wish itself and into the recorded files it can reach.

    A measure that asks for a heat pump does not name a file; it narrows the selection table.
    Showing both — the wish and the files still possible under it — is what makes the switches
    column tell a reader whether a wish is a dead end.
    """

    @classmethod
    def wish(cls, effect: SelectBaseFile) -> str:
        """Return the wish one base-file effect expresses, e.g. ``generator=HEAT_PUMP``."""
        parts = []
        if effect.generator is not None:
            parts.append(f"generator={effect.generator.value}")
        if effect.solar_thermal is not None:
            parts.append(f"solar_thermal={effect.solar_thermal.value}")
        if effect.cars is not None:
            parts.append(f"cars={effect.cars}")
        if effect.dhw_supply is not None:
            parts.append(f"dhw_supply={effect.dhw_supply.value}")
        return ", ".join(parts) or "nothing"

    @classmethod
    def files(cls, effect: SelectBaseFile) -> Tuple[str, ...]:
        """Return the recorded base files still reachable under one wish, sorted.

        Args:
            effect: The base-file wish.

        Returns:
            Every file name whose key agrees with every field the wish states; empty when the wish
            leaves no file, which is what a ``NO_BASE_FILE_FOR_COMBINATION`` refusal is about.
        """
        names = set()
        for key, name in BaseFiles.BY_KEY.items():
            if effect.generator is not None and key.generator is not effect.generator:
                continue
            if effect.solar_thermal is not None and not key.solar_thermal:
                continue
            if effect.cars is not None and key.cars != effect.cars:
                continue
            if effect.dhw_supply is not None and key.generator is not BaseFiles.DHW_HEAT_PUMP_GENERATOR:
                continue
            names.add(name)
        return tuple(sorted(names))


class EffectDescriber:
    """Turns one effect into the three columns the map's effect row has.

    The join is the point of the page: an effect names an inventory path, the bindings table turns
    that path into a component and a field, and the row shows the whole chain on one line. Effects
    that reach no field — a variant switch, a refusal — say what they reach instead.
    """

    #: What an added insulation layer is marked with in the summary column.
    RESISTANCE_MARK: ClassVar[str] = "+R"

    #: What a replaced element is marked with.
    U_VALUE_MARK: ClassVar[str] = "U"

    @classmethod
    def describe(cls, effect: Effect) -> EffectRow:
        """Return one effect as a summary, an inventory path and a HiSim target.

        Args:
            effect: Any member of the closed effect set.

        Returns:
            The :class:`EffectRow`.
        """
        if isinstance(effect, AddThermalResistance):
            path = EnvelopePaths.u_value_path(effect.element)
            return EffectRow(
                summary=(
                    f"{cls.RESISTANCE_MARK} {effect.element.value} {effect.material_asp_id} "
                    f"{effect.thickness_in_mm} mm"
                ),
                inventory_path=path,
                hisim_target=cls.target_of(path),
            )
        if isinstance(effect, SetUValue):
            path = EnvelopePaths.u_value_path(effect.element)
            return EffectRow(
                summary=(
                    f"{cls.U_VALUE_MARK} {effect.element.value} "
                    f"{effect.u_value_in_watt_per_m2_per_kelvin:g} W/m2K"
                ),
                inventory_path=path,
                hisim_target=cls.target_of(path),
            )
        if isinstance(effect, SetInventoryField):
            value = cls._law_text(effect.law) if effect.law is not None else repr(effect.value)
            return EffectRow(
                summary=f"set {effect.path} = {value}",
                inventory_path=effect.path,
                hisim_target=cls.target_of(effect.path),
            )
        if isinstance(effect, SelectVariant):
            return EffectRow(
                summary=f"variant {effect.variant}={effect.option}",
                inventory_path="",
                hisim_target=f"the {effect.variant} variant of every base file",
            )
        if isinstance(effect, EnableGroup):
            return EffectRow(
                summary=f"group {effect.group}",
                inventory_path="",
                hisim_target=f"the {effect.group} group of the base file",
            )
        if isinstance(effect, SelectBaseFile):
            files = BaseFileDescriber.files(effect)
            return EffectRow(
                summary=f"base file {BaseFileDescriber.wish(effect)}",
                inventory_path="",
                hisim_target=", ".join(files) if files else "no recorded file matches",
            )
        if isinstance(effect, NoEffect):
            return EffectRow(
                summary=f"no effect: {effect.reason.value}",
                inventory_path="",
                hisim_target=effect.reason.describe(),
            )
        return EffectRow(
            summary=f"refuse: {effect.reason.value}",
            inventory_path="",
            hisim_target=effect.detail,
        )

    @classmethod
    def target_of(cls, inventory_path: str) -> str:
        """Return what the bindings table says one inventory path reaches, as one phrase.

        Args:
            inventory_path: The dotted path the effect writes.

        Returns:
            The targets joined by ``" + "``, e.g.
            ``Building.set_heating_temperature_in_celsius + HeatDistributionController.…``; the
            binding's note for a path that reaches no component; and a complaint when the table
            has no rule at all, which the bindings test forbids.
        """
        try:
            binding = Bindings.resolve(inventory_path)
        except BindingError:
            return "no binding covers this path"
        if binding.targets:
            return " + ".join(target.describe() for target in binding.targets)
        return f"{binding.kind.value}: {binding.note}"

    @classmethod
    def _law_text(cls, law: LawRequest) -> str:
        """Return a pending sizing law as it is printed, e.g. ``BATTERY_FROM_DAYS_TO_COVER(2)``."""
        return f"{law.law.value}({law.argument:g})"


class MapPalette:
    """The colours the page uses, once, for both colour schemes.

    They are declared as CSS custom properties in :meth:`MapRenderer.style` and named here so that
    a status has one colour on the legend, on the chip and on the row. The dark values are not
    darker versions of the light ones but the same hues at a lightness that reads on a dark
    ground, which is what ``prefers-color-scheme`` needs to be worth doing.
    """

    #: Status -> (light background, light text, dark background, dark text).
    BY_STATUS: ClassVar[Dict[MapStatus, Tuple[str, str, str, str]]] = {
        MapStatus.SIMULATED: ("#d8f0dd", "#14532d", "#14361f", "#9fe6b4"),
        MapStatus.LAW_PENDING: ("#d2eeee", "#0f4c4c", "#12383a", "#8ddcdc"),
        MapStatus.NO_EFFECT: ("#e4e4e4", "#3f3f3f", "#323232", "#bdbdbd"),
        MapStatus.BLOCKED_ON_DATA: ("#fbe6c4", "#6b4106", "#40300f", "#f0c073"),
        MapStatus.REFUSED: ("#f7d7d7", "#7a1a1a", "#41201f", "#f0a3a3"),
    }


class MapRenderer:
    """Renders collected map data into one self-contained HTML page.

    The page is assembled as a list of strings and joined once, which keeps the output byte-stable
    and makes the structure of the document readable in this class's methods: a header, four tab
    panels, and a script small enough to read in full.

    Args:
        data: What :meth:`MapData.collect` produced.
    """

    #: The page's title, which is also its ``<title>``.
    TITLE: ClassVar[str] = "RenoVisor translation map"

    #: The tabs, in order: id, label.
    TABS: ClassVar[Tuple[Tuple[str, str], ...]] = (
        ("map", "Map"),
        ("elements", "Elements"),
        ("switches", "Switches"),
        ("trace", "Trace"),
    )

    #: What marks a measure that is in an element's exclusivity table but records no effect today,
    #: because it refuses: it still contradicts the other group's measures, so it belongs on the
    #: matrix.
    EMPTY_GROUP_MARK: ClassVar[str] = "·"

    #: The columns of the map table.
    MAP_COLUMNS: ClassVar[Tuple[str, ...]] = (
        "Measure",
        "Options",
        "Effect",
        "Inventory path",
        "HiSim target",
        "Status",
    )

    def __init__(self, data: MapData) -> None:
        """Store the collected data this renderer writes out."""
        self._data = data

    def render(self) -> str:
        """Return the whole page as one string of HTML.

        Returns:
            A complete document, ending in a newline, with nothing loaded from outside it.
        """
        parts: List[str] = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html.escape(self.TITLE)}</title>",
            self.style(),
            "</head>",
            "<body>",
        ]
        parts.extend(self.header())
        parts.extend(self.tab_bar())
        parts.extend(self.map_panel())
        parts.extend(self.elements_panel())
        parts.extend(self.switches_panel())
        parts.extend(self.trace_panel())
        parts.append(self.script())
        parts.extend(["</body>", "</html>", ""])
        return "\n".join(parts)

    def style(self) -> str:
        """Return the page's inline stylesheet, including both colour schemes."""
        lines = [
            "<style>",
            ":root{--bg:#fdfdfc;--fg:#1c1c1a;--muted:#6b6b66;--line:#dcdcd6;--panel:#ffffff;",
            "--accent:#2a4d69;--chip:#eeeee8;}",
            "@media (prefers-color-scheme: dark){:root{--bg:#17181a;--fg:#e8e8e4;--muted:#9a9a94;",
            "--line:#33353a;--panel:#1e2023;--accent:#8fb6d6;--chip:#2a2d31;}}",
        ]
        for status, colours in MapPalette.BY_STATUS.items():
            key = status.value.lower().replace("_", "-")
            lines.append(f":root{{--s-{key}-bg:{colours[0]};--s-{key}-fg:{colours[1]};}}")
        lines.append("@media (prefers-color-scheme: dark){:root{")
        for status, colours in MapPalette.BY_STATUS.items():
            key = status.value.lower().replace("_", "-")
            lines.append(f"--s-{key}-bg:{colours[2]};--s-{key}-fg:{colours[3]};")
        lines.append("}}")
        lines.extend(
            [
                "*{box-sizing:border-box;}",
                "body{margin:0;background:var(--bg);color:var(--fg);",
                "font-family:ui-sans-serif,system-ui,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;",
                "font-size:14px;line-height:1.45;}",
                "main{max-width:1600px;margin:0 auto;padding:24px 20px 64px;}",
                "h1{font-size:22px;margin:0 0 4px;}",
                "h2{font-size:17px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px;}",
                "h3{font-size:14px;margin:20px 0 6px;color:var(--muted);font-weight:600;}",
                "p{margin:6px 0;}",
                ".sub{color:var(--muted);font-size:13px;}",
                ".pill{display:inline-block;padding:1px 8px;border-radius:9px;font-size:12px;",
                "background:var(--chip);margin:0 6px 4px 0;}",
                ".legend{margin:12px 0 4px;}",
                ".legend .pill{font-weight:600;}",
                "button{font:inherit;cursor:pointer;border:1px solid var(--line);background:var(--panel);",
                "color:var(--fg);border-radius:6px;padding:3px 10px;margin:0 6px 6px 0;}",
                "button[aria-pressed='true'],button[aria-selected='true']{background:var(--accent);",
                "color:var(--bg);border-color:var(--accent);}",
                ".tabs{margin:18px 0 12px;}",
                "[role='tabpanel'][hidden]{display:none;}",
                ".scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;",
                "background:var(--panel);}",
                "table{border-collapse:collapse;width:100%;font-size:13px;}",
                "th,td{text-align:left;vertical-align:top;padding:5px 9px;border-bottom:1px solid var(--line);}",
                "th{position:sticky;top:0;background:var(--panel);font-weight:600;z-index:1;}",
                "code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;}",
                ".name{color:var(--muted);font-weight:400;}",
                "tbody tr.dim{opacity:.22;}",
                "tbody tr.hot{background:var(--chip);}",
                "tbody tr.faded{opacity:.3;}",
                ".status{font-weight:600;white-space:nowrap;border-radius:5px;padding:1px 7px;}",
            ]
        )
        for status in MapStatus.in_preference_order():
            key = status.value.lower().replace("_", "-")
            lines.append(
                f".s-{key}{{background:var(--s-{key}-bg);color:var(--s-{key}-fg);}}"
            )
        lines.extend(
            [
                ".grid{border-collapse:collapse;font-size:12px;}",
                ".grid td,.grid th{border:1px solid var(--line);text-align:center;padding:4px 7px;}",
                ".grid th.row{text-align:left;font-weight:500;}",
                ".grid td.mark{font-weight:700;}",
                ".hunk{margin:10px 0 14px;}",
                "pre.diff{margin:0;padding:8px 10px;overflow-x:auto;border:1px solid var(--line);",
                "border-radius:8px;background:var(--panel);",
                "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;",
                "line-height:1.35;}",
                "pre.diff span{display:block;white-space:pre;}",
                "pre.diff .add{background:var(--s-simulated-bg);color:var(--s-simulated-fg);}",
                "pre.diff .del{background:var(--s-refused-bg);color:var(--s-refused-fg);}",
                "pre.diff .ctx{color:var(--muted);}",
                ".group-a{outline:2px solid var(--accent);outline-offset:-2px;}",
                ".group-b{outline:2px dashed var(--accent);outline-offset:-2px;}",
                "</style>",
            ]
        )
        return "\n".join(lines)

    def header(self) -> List[str]:
        """Return the page header: title, version line, legend, counts and decision filter."""
        statuses = self._data.statuses()
        lines = [
            "<main>",
            f"<h1>{html.escape(self.TITLE)}</h1>",
            f'<p class="sub">Translator {html.escape(TRANSLATOR_VERSION)} &middot; contract '
            f"{html.escape(self._data.contract_commit[:12])} "
            f"(sha256 {html.escape(self._data.contract_sha256[:12])}) &middot; "
            f"{len(self._data.measures)} catalogue measures</p>",
            '<p class="sub">Generated by <code>python -m hisim.renovisor.map</code>. Effects are shown '
            "unresolved: no TABULA lookup, no U-value composition, no sizing law. A defaulted layer "
            f"thickness is computed against a reference U-value of "
            f"{ReferenceBuilding.U_VALUE_IN_WATT_PER_M2_PER_KELVIN:g} W/m2K on every element.</p>",
            '<div class="legend">',
        ]
        for status, ids in statuses.items():
            key = status.value.lower().replace("_", "-")
            lines.append(
                f'<span class="pill status s-{key}">{html.escape(status.label())} '
                f"&middot; {len(ids)}</span>"
            )
        lines.append("</div>")
        lines.append('<h3>Filter by decision</h3>')
        lines.append('<div id="decisions">')
        lines.append('<button type="button" data-decision="" aria-pressed="true">all</button>')
        for decision in self._data.decision_ids():
            lines.append(
                f'<button type="button" data-decision="{html.escape(decision)}" '
                f'aria-pressed="false">{html.escape(decision)}</button>'
            )
        lines.append("</div>")
        return lines

    def tab_bar(self) -> List[str]:
        """Return the row of tab buttons."""
        lines = ['<div class="tabs" role="tablist">']
        for index, (tab_id, label) in enumerate(self.TABS):
            selected = "true" if index == 0 else "false"
            lines.append(
                f'<button type="button" role="tab" data-tab="{tab_id}" '
                f'aria-selected="{selected}">{html.escape(label)}</button>'
            )
        lines.append("</div>")
        return lines

    def map_panel(self) -> List[str]:
        """Return the map tab: one section per catalogue category, one table per section."""
        lines = ['<section role="tabpanel" id="panel-map">']
        for category in self._data.categories():
            lines.append(f"<h2>{html.escape(category)}</h2>")
            lines.append('<div class="scroll"><table><thead><tr>')
            lines.extend(f"<th>{html.escape(column)}</th>" for column in self.MAP_COLUMNS)
            lines.append("</tr></thead><tbody>")
            for measure in self._data.measures:
                if measure.category != category:
                    continue
                lines.extend(self._measure_rows(measure))
            lines.append("</tbody></table></div>")
        lines.append("</section>")
        return lines

    def _measure_rows(self, measure: MeasureMap) -> List[str]:
        """Return the table rows of one measure: one per outcome, one per effect inside it."""
        lines: List[str] = []
        decisions = " ".join(measure.decisions)
        first_of_measure = True
        for outcome in measure.outcomes:
            effects = outcome.effects or (EffectRow("(no effect recorded)", "", ""),)
            for index, effect in enumerate(effects):
                attributes = (
                    f'data-measure="{html.escape(measure.measure_id)}" '
                    f'data-decisions="{html.escape(decisions)}"'
                )
                lines.append(f"<tr {attributes}>")
                lines.append(self._measure_cell(measure, first_of_measure))
                lines.append(self._options_cell(outcome, index))
                lines.append(f'<td class="mono">{html.escape(effect.summary)}</td>')
                lines.append(f'<td class="mono">{html.escape(effect.inventory_path)}</td>')
                lines.append(f'<td class="mono">{html.escape(effect.hisim_target)}</td>')
                lines.append(self._status_cell(outcome, index))
                lines.append("</tr>")
                first_of_measure = False
        return lines

    def _measure_cell(self, measure: MeasureMap, first: bool) -> str:
        """Return the measure column, filled only on the measure's first row."""
        if not first:
            return "<td></td>"
        decisions = "".join(f'<span class="pill">{html.escape(item)}</span>' for item in measure.decisions)
        return (
            f'<td><strong class="mono">{html.escape(measure.measure_id)}</strong><br>'
            f'<span class="name">{html.escape(measure.display_name)}</span><br>'
            f'<span class="name">{html.escape(measure.subcategory)}</span><br>{decisions}</td>'
        )

    def _options_cell(self, outcome: Outcome, index: int) -> str:
        """Return the options column, filled only on the outcome's first row."""
        if index:
            return "<td></td>"
        if not any(outcome.choice_sets):
            return '<td class="name">(no options)</td>'
        rendered = [", ".join(self._choice(choice) for choice in choices) for choices in outcome.choice_sets]
        return "<td>" + "<br>".join(rendered) + "</td>"

    @classmethod
    def _choice(cls, choice: OptionChoice) -> str:
        """Return one option value as the page prints it, with its display name and any default."""
        marker = ""
        if choice.defaulted:
            title = html.escape(choice.default_source, quote=True)
            marker = f' <span class="pill" title="{title}">defaulted</span>'
        display = ""
        if choice.display_value != choice.value:
            display = f' <span class="name">({html.escape(choice.display_value)})</span>'
        return (
            f'<span class="mono">{html.escape(choice.option_id)}='
            f"{html.escape(choice.value)}</span>{display}{marker}"
        )

    def _status_cell(self, outcome: Outcome, index: int) -> str:
        """Return the status column, filled only on the outcome's first row."""
        if index:
            return "<td></td>"
        key = outcome.status.value.lower().replace("_", "-")
        return f'<td><span class="status s-{key}">{html.escape(outcome.status.label())}</span></td>'

    def elements_panel(self) -> List[str]:
        """Return the elements tab: measures against thermal elements, with exclusivity groups."""
        exclusivity = self._exclusivity_classes()
        in_a_group = {measure_id for _, measure_id in exclusivity}
        touching = [
            measure
            for measure in self._data.measures
            if measure.elements or measure.measure_id in in_a_group
        ]
        lines = [
            '<section role="tabpanel" id="panel-elements" hidden>',
            "<h2>Thermal elements</h2>",
            '<p class="sub"><strong>R</strong> adds a resistance layer, <strong>U</strong> replaces the '
            "element and sets its U-value, <strong>&middot;</strong> marks a measure that belongs to the "
            "element's exclusivity table but records no effect today. Cells outlined with the same border "
            "belong to one exclusivity group: two measures from different groups on one element describe "
            "two different buildings and are refused (decision Q3).</p>",
            '<div class="scroll"><table class="grid"><thead><tr><th class="row">Measure</th>',
        ]
        lines.extend(f"<th>{html.escape(element.value)}</th>" for element in ThermalElement)
        lines.append("</tr></thead><tbody>")
        for measure in touching:
            lines.append(
                f'<tr data-measure="{html.escape(measure.measure_id)}" '
                f'data-decisions="{html.escape(" ".join(measure.decisions))}">'
            )
            lines.append(f'<th class="row mono">{html.escape(measure.measure_id)}</th>')
            for element in ThermalElement:
                mark = measure.elements.get(element.value, "")
                group = exclusivity.get((element.value, measure.measure_id), "")
                if not mark and group:
                    mark = self.EMPTY_GROUP_MARK
                classes = " ".join(part for part in ("mark" if mark else "", group) if part)
                attribute = f' class="{classes}"' if classes else ""
                lines.append(f"<td{attribute}>{html.escape(mark)}</td>")
            lines.append("</tr>")
        lines.append("</tbody></table></div>")
        lines.append("</section>")
        return lines

    @classmethod
    def _exclusivity_classes(cls) -> Dict[Tuple[str, str], str]:
        """Return, per (element, measure), the CSS class outlining its exclusivity group."""
        names = ("group-a", "group-b", "group-c")
        classes: Dict[Tuple[str, str], str] = {}
        for element, groups in ExclusivityTable.BY_ELEMENT.items():
            for index, group in enumerate(groups):
                for measure_id in sorted(group):
                    classes[(element.value, measure_id)] = names[index % len(names)]
        return classes

    def switches_panel(self) -> List[str]:
        """Return the switches tab: which measure switches what, and the selection table."""
        switching = [measure for measure in self._data.measures if measure.switches]
        lines = [
            '<section role="tabpanel" id="panel-switches" hidden>',
            "<h2>Switches</h2>",
            '<p class="sub">A switch changes which recorded file runs, or which of its variant options '
            "is selected, rather than a value inside it. Decision Q18: a combination with no recorded "
            "file is a refusal, never a new HiSim-owned file.</p>",
            '<div class="scroll"><table><thead><tr><th>Measure</th><th>Switches it can ask for</th>'
            "</tr></thead><tbody>",
        ]
        for measure in switching:
            lines.append(
                f'<tr data-measure="{html.escape(measure.measure_id)}" '
                f'data-decisions="{html.escape(" ".join(measure.decisions))}">'
            )
            lines.append(f'<td class="mono">{html.escape(measure.measure_id)}</td>')
            lines.append(
                "<td>"
                + "<br>".join(f'<span class="mono">{html.escape(item)}</span>' for item in measure.switches)
                + "</td>"
            )
            lines.append("</tr>")
        lines.append("</tbody></table></div>")
        lines.extend(self._base_file_table())
        return lines + ["</section>"]

    def _base_file_table(self) -> List[str]:
        """Return the selection table and the combinations it has no file for."""
        lines = [
            "<h2>Recorded base files</h2>",
            '<div class="scroll"><table><thead><tr><th>Generator</th><th>Solar thermal</th>'
            "<th>Cars</th><th>File</th></tr></thead><tbody>",
        ]
        for key in sorted(
            BaseFiles.BY_KEY, key=lambda item: (item.generator.value, item.solar_thermal, item.cars)
        ):
            lines.append("<tr>")
            lines.append(f'<td class="mono">{html.escape(key.generator.value)}</td>')
            lines.append(f"<td>{'yes' if key.solar_thermal else 'no'}</td>")
            lines.append(f"<td>{key.cars}</td>")
            lines.append(f'<td class="mono">{html.escape(BaseFiles.BY_KEY[key])}</td>')
            lines.append("</tr>")
        lines.append("</tbody></table></div>")
        lines.append("<h3>Combinations with no recorded file</h3>")
        lines.append('<div class="scroll"><table><thead><tr><th>Generator</th><th>Solar thermal</th>'
                     "<th>Cars</th><th>Outcome</th></tr></thead><tbody>")
        for key in self._missing_combinations():
            lines.append("<tr>")
            lines.append(f'<td class="mono">{html.escape(key.generator.value)}</td>')
            lines.append(f"<td>{'yes' if key.solar_thermal else 'no'}</td>")
            lines.append(f"<td>{key.cars}</td>")
            lines.append(
                '<td><span class="status s-refused">'
                f"{html.escape(ReasonCode.NO_BASE_FILE_FOR_COMBINATION.value)}</span></td>"
            )
            lines.append("</tr>")
        lines.append("</tbody></table></div>")
        return lines

    @classmethod
    def _missing_combinations(cls) -> Tuple[BaseFileKey, ...]:
        """Return every generator-and-collector-and-car combination the table has no file for.

        Every heat generator of the vocabulary is walked, not only the ones a file exists for, so
        that the two the table has nothing at all for — the hybrid heat pump and HVO — are visible
        as gaps rather than absent from the page.
        """
        generators = sorted(HeatGenerator, key=lambda item: item.value)
        missing = [
            BaseFileKey(generator=generator, solar_thermal=solar_thermal, cars=cars)
            for generator in generators
            for solar_thermal in (False, True)
            for cars in (0, 1)
            if not BaseFiles.has(BaseFileKey(generator, solar_thermal, cars))
        ]
        return tuple(missing)

    def trace_panel(self) -> List[str]:
        """Return the trace tab: the inventory diff, the YAML diff and the report table."""
        lines = [
            '<section role="tabpanel" id="panel-trace" hidden>',
            "<h2>Worked example trace</h2>",
        ]
        trace = self._data.trace
        if trace is None:
            lines.extend(
                [
                    "<p>No trace was collected for this page.</p>",
                    "</section>",
                    "</main>",
                ]
            )
            return lines
        lines.extend(self._trace_intro(trace))
        lines.extend(self._trace_inventory(trace))
        lines.extend(self._trace_yaml(trace))
        lines.extend(self._trace_report(trace))
        lines.append("</section>")
        lines.append("</main>")
        return lines

    def _trace_intro(self, trace: TraceData) -> List[str]:
        """Return the paragraph naming the example, its base file and its stated numbers."""
        return [
            f'<p class="sub">{html.escape(TraceExample.INVENTORY_PATH.name)} plus '
            f"{html.escape(TraceExample.PACKAGE_PATH.name)}, parametrised into "
            f"<code>{html.escape(trace.base_file_name)}</code>. Nothing here is simulated: the page "
            "applies the measures, resolves the sizing laws and writes the file, and stops there.</p>",
            '<p class="sub">The sizing laws read three <strong>stated</strong> daily demands rather '
            "than a loaded occupancy profile, so that this page depends on no cache and no "
            f"LoadProfileGenerator run: {TraceExample.HOUSEHOLD_IN_KWH_PER_DAY:g} kWh/day household, "
            f"{TraceExample.HEAT_PUMP_IN_KWH_PER_DAY:g} kWh/day heat pump, "
            f"{TraceExample.VEHICLE_IN_KWH_PER_DAY:g} kWh/day vehicle. A real calculation computes all "
            "three.</p>",
        ]

    def _trace_inventory(self, trace: TraceData) -> List[str]:
        """Return the first pane: every inventory leaf the package changed."""
        lines = [
            "<h3>1. The inventory, before and after the measures</h3>",
            '<div class="scroll"><table><thead><tr>'
            "<th>Inventory path</th><th>Before</th><th>After</th><th>Measure</th>"
            "</tr></thead><tbody>",
        ]
        for change in trace.inventory_changes:
            measures = ", ".join(change.measure_ids) or self.EMPTY_GROUP_MARK
            lines.append(
                f'<tr><td class="mono">{html.escape(change.path)}</td>'
                f'<td class="mono">{html.escape(self._value(change.before))}</td>'
                f'<td class="mono">{html.escape(self._value(change.after))}</td>'
                f"<td>{html.escape(measures)}</td></tr>"
            )
        lines.append("</tbody></table></div>")
        if trace.law_rules:
            lines.append("<h3>The sizing laws that were resolved</h3>")
            lines.append('<div class="scroll"><table><thead><tr>'
                         "<th>Inventory path</th><th>Arithmetic</th></tr></thead><tbody>")
            for path, rule in trace.law_rules:
                lines.append(
                    f'<tr><td class="mono">{html.escape(path)}</td>'
                    f'<td class="mono">{html.escape(rule)}</td></tr>'
                )
            lines.append("</tbody></table></div>")
        return lines

    def _trace_yaml(self, trace: TraceData) -> List[str]:
        """Return the second pane: the base file to parametrised file diff, annotated."""
        lines = [
            "<h3>2. The energy-system file, base to parametrised</h3>",
            '<p class="sub">Every hunk is annotated with the inventory path, measure or sizing law '
            "that asked for it. Requirement R4 permits only these four kinds of change: config "
            "values, constructor swaps, variant selections and group flags.</p>",
        ]
        for hunk in trace.hunks:
            annotation = ", ".join(hunk.annotations) or "the document's own name and description"
            lines.append('<div class="hunk">')
            lines.append(
                f'<p class="sub"><code>{html.escape(hunk.header)}</code> &middot; '
                f"{html.escape(annotation)}</p>"
            )
            lines.append('<pre class="diff">')
            for line in hunk.lines:
                css = "add" if line.startswith("+") else ("del" if line.startswith("-") else "ctx")
                lines.append(f'<span class="{css}">{html.escape(line)}</span>')
            lines.append("</pre>")
            lines.append("</div>")
        return lines

    def _trace_report(self, trace: TraceData) -> List[str]:
        """Return the third pane: the translation report of the example."""
        lines = [
            "<h3>3. The translation report</h3>",
            '<p class="sub">One line per inventory field and per measure, which is what requirement '
            "R7 asks of every calculation.</p>",
            '<div class="scroll"><table><thead><tr>'
            "<th>Path</th><th>Status</th><th>Note</th><th>Rule</th></tr></thead><tbody>",
        ]
        for line in trace.report_lines:
            lines.append(
                f'<tr><td class="mono">{html.escape(str(line["path"]))}</td>'
                f'<td>{html.escape(str(line["status"]))}</td>'
                f'<td>{html.escape(str(line.get("note", "")))}</td>'
                f'<td class="sub">{html.escape(str(line.get("rule", "")))}</td></tr>'
            )
        lines.append("</tbody></table></div>")
        return lines

    @classmethod
    def _value(cls, value: Any) -> str:
        """Return one inventory value as the page shows it."""
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    def script(self) -> str:
        """Return the page's inline script: tab switching, decision filter, row highlighting."""
        return "\n".join(
            [
                "<script>",
                "(function(){",
                "var panels={map:'panel-map',elements:'panel-elements',"
                "switches:'panel-switches',trace:'panel-trace'};",
                "document.querySelectorAll('[role=tab]').forEach(function(tab){",
                "tab.addEventListener('click',function(){",
                "document.querySelectorAll('[role=tab]').forEach(function(other){",
                "other.setAttribute('aria-selected',String(other===tab));});",
                "Object.keys(panels).forEach(function(key){",
                "document.getElementById(panels[key]).hidden=(key!==tab.dataset.tab);});});});",
                "var filter='';",
                "function applyFilter(){",
                "document.querySelectorAll('tbody tr[data-decisions]').forEach(function(row){",
                "var carries=filter===''||(' '+row.dataset.decisions+' ').indexOf(' '+filter+' ')>=0;",
                "row.classList.toggle('faded',!carries);});}",
                "document.querySelectorAll('#decisions button').forEach(function(chip){",
                "chip.addEventListener('click',function(){",
                "filter=chip.dataset.decision;",
                "document.querySelectorAll('#decisions button').forEach(function(other){",
                "other.setAttribute('aria-pressed',String(other===chip));});",
                "applyFilter();});});",
                "var pinned='';",
                "function highlight(measure){",
                "document.querySelectorAll('tbody tr[data-measure]').forEach(function(row){",
                "row.classList.toggle('hot',measure!==''&&row.dataset.measure===measure);",
                "row.classList.toggle('dim',measure!==''&&row.dataset.measure!==measure);});}",
                "document.querySelectorAll('tbody tr[data-measure]').forEach(function(row){",
                "row.addEventListener('mouseenter',function(){",
                "if(pinned==='')highlight(row.dataset.measure);});",
                "row.addEventListener('mouseleave',function(){if(pinned==='')highlight('');});",
                "row.addEventListener('click',function(){",
                "pinned=pinned===row.dataset.measure?'':row.dataset.measure;",
                "highlight(pinned);});});",
                "})();",
                "</script>",
            ]
        )


class MapCommand:
    """The ``python -m hisim.renovisor.map`` entry point.

    Writing the page is one command with one option so that regenerating it after a registry or
    catalogue change is a single line in a review, and so that the freshness test can call exactly
    what a person calls.
    """

    #: Where the committed page lives, relative to the repository root.
    DEFAULT_OUTPUT: ClassVar[str] = "roadmap/renovisor/translation_map.html"

    #: The repository root, two directories above this module's package.
    REPOSITORY_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

    @classmethod
    def render(cls) -> str:
        """Return the page as it should be committed today."""
        return MapRenderer(MapData.collect()).render()

    @classmethod
    def main(cls, argv: Optional[Sequence[str]] = None) -> int:
        """Write the page and report where it went.

        Args:
            argv: The command-line arguments, without the program name. ``None`` reads
                ``sys.argv``.

        Returns:
            The process exit code: 0 on success.

        Raises:
            ValidationError: When a measure rejects a catalogue value, which is a registry bug.
        """
        parser = argparse.ArgumentParser(
            prog="python -m hisim.renovisor.map",
            description="Generate the RenoVisor translation map as one self-contained HTML file.",
        )
        parser.add_argument(
            "--out",
            default=str(cls.REPOSITORY_ROOT / cls.DEFAULT_OUTPUT),
            help=f"where to write the page (default: {cls.DEFAULT_OUTPUT})",
        )
        arguments = parser.parse_args(argv)
        document = cls.render()
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        print(f"wrote {target} ({len(document)} bytes)")
        return 0


if __name__ == "__main__":
    sys.exit(MapCommand.main())
