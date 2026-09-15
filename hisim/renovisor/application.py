"""Applying one renovation package to one home inventory.

This is where the pieces meet: the catalogue says what a measure is, the registry says what it
does, the effect resolver composes what several of them come to, and this module walks a package
through all of it and hands the parametriser of step 5 a post-measure inventory plus everything
that is not an inventory field — which base file to run, which variants and groups to switch, and
which values still need a sizing law::

    application = PackageApplication(Catalogue.load(), MeasureRegistry(), InsulationMaterials.load())
    result = application.apply(inventory, [{"measure_id": "EXTERNAL_INSULATION", "options": {...}}])
    result.inventory.get("building_config.envelope_details.facade_u_value_in_watt_per_m2_per_kelvin")

The order of work is fixed and each step has a reason. The package's shape is validated before
anything is applied, so a malformed request never half-applies. The inventory is deep-copied, so
the caller's document is untouched and a second package starts from the same state (requirement
R5). Every measure runs before anything is composed, so two layers on one wall add up (requirement
M2). Every refusal is collected before any is raised, so a caller with three problems learns all
three at once.
"""

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.renovisor.base_files import BaseFileKey, BaseFiles
from hisim.renovisor.catalogue import Catalogue
from hisim.renovisor.effects import Effects, LawRequest, NoEffect, ResolvedEffects
from hisim.renovisor.envelope import (
    CurrentUValues,
    EnvelopePaths,
    ExclusivityTable,
    FitToBuilding,
    InventoryThenTabula,
    RegulatoryTargets,
    TabulaArchetype,
)
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.options import Options
from hisim.renovisor.reasons import ReasonCode, RefusalDetail, RefusalError, ValidationError
from hisim.renovisor.registry import MeasureRegistry
from hisim.renovisor.report import MappingReport, ReportStatus
from hisim.renovisor.tabula_ie import TabulaLookupError
from hisim.renovisor.vocabulary import DhwSupply, HeatGenerator, VocabularyLookup


@dataclass(frozen=True)
class PackageEntry:
    """One element of a package: which measure, with which option values.

    Args:
        measure_id: The catalogue measure id, e.g. ``"CHANGE_ROOM_TEMPERATURE"``.
        options: The option values the request supplies, by option id.
    """

    measure_id: str
    options: Mapping[str, Any]


@dataclass(frozen=True)
class ApplicationResult:
    """Everything applying a package produced, for the parametriser of step 5.

    Args:
        inventory: The post-measure inventory, in the inventory's own units. Fields whose value is
            a pending sizing law still hold whatever the inventory had.
        base_file_key: Which recorded energy-system file the calculation runs, after the
            inventory's own system and every measure that spoke to it.
        base_file_name: The recorded file that key selects.
        variant_selections: Variant name -> selected option, to write into the base file.
        enabled_groups: The component groups to switch on.
        pending_laws: Inventory path -> the sizing law that will produce its value.
        report: The translation report, finalised over the post-measure inventory.
        u_values: The composed U-value per envelope element any measure touched.
    """

    inventory: Inventory
    base_file_key: BaseFileKey
    base_file_name: str
    variant_selections: Mapping[str, str]
    enabled_groups: Tuple[str, ...]
    pending_laws: Mapping[str, LawRequest]
    report: MappingReport
    u_values: Mapping[str, float]


class PackagePaths:
    """The JSON paths of a request's package, so errors address the caller's own document."""

    #: The path of the package's measure list.
    MEASURES: ClassVar[str] = "package.measures"

    #: The two keys a package entry may carry, per decision Q1.
    MEASURE_ID_KEY: ClassVar[str] = "measure_id"
    OPTIONS_KEY: ClassVar[str] = "options"

    #: The key a stage-0 measure would carry. Naming it lets the shape check give the caller the
    #: specific reason code decision Q4 asks for instead of a generic unknown-key error.
    STAGE_KEY: ClassVar[str] = "stage"

    #: The stage value that makes an entry a description of the existing building rather than a
    #: renovation; HiSim reads the base state from the inventory alone (requirement M9).
    BASE_STATE_STAGE: ClassVar[int] = 0

    @classmethod
    def entry(cls, index: int) -> str:
        """Return the path of one package entry, e.g. ``package.measures[2]``."""
        return f"{cls.MEASURES}[{index}]"


class InventorySystem:
    """Reads the dwelling's existing energy system out of the inventory.

    The base-file key starts from what the building already has — a gas boiler, no collector, no
    car — and every measure that speaks to one of those three facts overrides it. Reading them is
    its own small job because each has a different shape in the contract: the generator is an enum
    value, the collector a whole block that may be absent, and the cars a list.
    """

    #: Where the generator's name sits.
    HEATING_SYSTEM: ClassVar[str] = "energy_system_config.heating_system.system"

    #: The collector block, and the field that says whether it is a real collector.
    SOLAR_THERMAL_BLOCK: ClassVar[str] = "energy_system_config.solar_thermal_system"
    COLLECTOR_AREA: ClassVar[str] = "energy_system_config.solar_thermal_system.collector_area_in_m2"

    #: The list of electric vehicles charged at the dwelling.
    ELECTRIC_VEHICLES: ClassVar[str] = "energy_system_config.vehicles.electric_vehicles"

    @classmethod
    def base_file_key(cls, inventory: Inventory) -> BaseFileKey:
        """Return the base-file key the inventory alone implies.

        Args:
            inventory: The pre-measure inventory.

        Returns:
            The key of the file the building would run without any measure.

        Raises:
            ValidationError: ``SCHEMA_VIOLATION`` when the inventory names a generator that is not
                in the heat-generator vocabulary.
        """
        raw = inventory.get(cls.HEATING_SYSTEM)
        try:
            generator = VocabularyLookup.member(HeatGenerator, raw)
        except KeyError as error:
            raise ValidationError(
                ReasonCode.SCHEMA_VIOLATION,
                cls.HEATING_SYSTEM,
                f"'{raw}' is not one of {[member.value for member in HeatGenerator]}",
            ) from error
        return BaseFileKey(
            generator=generator,
            solar_thermal=cls.has_solar_thermal(inventory),
            cars=cls.car_count(inventory),
        )

    @classmethod
    def has_solar_thermal(cls, inventory: Inventory) -> bool:
        """Return whether the dwelling already has a solar thermal collector.

        A collector block with a zero or absent area is not a collector; the contract has no
        "present" flag, so the area is what says so.
        """
        area = inventory.get(cls.COLLECTOR_AREA)
        return isinstance(area, (int, float)) and not isinstance(area, bool) and area > 0

    @classmethod
    def car_count(cls, inventory: Inventory) -> int:
        """Return how many electric vehicles the dwelling charges."""
        vehicles = inventory.get(cls.ELECTRIC_VEHICLES)
        return len(vehicles) if isinstance(vehicles, list) else 0


class PackageApplication:
    """Applies one package to a copy of one inventory and returns what step 5 needs.

    Args:
        catalogue: The measure catalogue, for the spec of each named measure.
        registry: The measure registry, for the function of each named measure.
        materials: The insulation-material table, for the conductivities.
        current_u_values_factory: How to build the U-value source for an inventory. Defaults to
            :class:`~hisim.renovisor.envelope.InventoryThenTabula`, which is decision Q10's rule; a
            test hands in a fixed set of numbers instead.
        targets: The regulatory target table a defaulted thickness aims at. Defaults to the
            committed Irish table.
    """

    def __init__(
        self,
        catalogue: Catalogue,
        registry: MeasureRegistry,
        materials: InsulationMaterials,
        current_u_values_factory: Optional[Callable[[Inventory], CurrentUValues]] = None,
        targets: Optional[RegulatoryTargets] = None,
    ) -> None:
        """Store the four collaborators, loading the default target table when none is given."""
        self._catalogue = catalogue
        self._registry = registry
        self._materials = materials
        self._current_u_values_factory = current_u_values_factory or InventoryThenTabula
        self._targets = targets or RegulatoryTargets.load()

    def apply(self, inventory: Inventory, package: Sequence[Mapping[str, Any]]) -> ApplicationResult:
        """Apply *package* to a copy of *inventory*.

        Args:
            inventory: The pre-measure inventory. It is not modified.
            package: The request's measure list, as parsed JSON.

        Returns:
            The :class:`ApplicationResult`.

        Raises:
            ValidationError: When the package is malformed — an unknown measure, a duplicate, an
                unknown key, a stage-0 entry, or an option that does not check out.
            RefusalError: When the package is well formed but cannot be simulated; it carries
                every refusal, not only the first.
        """
        entries = self._read_package(package)
        report = MappingReport()
        post = inventory.copy()
        base_key = InventorySystem.base_file_key(inventory)

        current_u_values, archetype_refusal = self._current_u_values(inventory, report)
        if current_u_values is None:
            raise RefusalError([archetype_refusal] if archetype_refusal is not None else [])

        effects = Effects(self._materials, current_u_values, self._targets)
        for index, entry in enumerate(entries):
            spec = self._catalogue.by_id(entry.measure_id)
            options = Options(spec, entry.options, report, PackagePaths.entry(index))
            self._registry.function_for(entry.measure_id)(options, inventory, effects)

        resolved = effects.resolve(inventory, current_u_values)
        refusals: List[RefusalDetail] = [item.as_detail() for item in resolved.refusals]
        measure_ids = [entry.measure_id for entry in entries]
        refusals.extend(ExclusivityTable.check(measure_ids))
        refusals.extend(FitToBuilding.check(measure_ids, inventory))
        base_key = self._merge_base_file_key(base_key, resolved, refusals)
        base_file_name = self._select_base_file(base_key, refusals)
        if refusals:
            raise RefusalError(refusals)

        u_values = self._write_results(post, resolved, report)
        self._report_measures(entries, effects, report)
        report.finalize(post.to_dict())
        post.validate()
        return ApplicationResult(
            inventory=post,
            base_file_key=base_key,
            base_file_name=base_file_name,
            variant_selections=dict(resolved.variant_selections),
            enabled_groups=resolved.enabled_groups,
            pending_laws=dict(resolved.pending_laws),
            report=report,
            u_values=u_values,
        )

    def _read_package(self, package: Sequence[Mapping[str, Any]]) -> Tuple[PackageEntry, ...]:
        """Validate the package's shape and return its entries.

        Raises:
            ValidationError: ``UNKNOWN_KEY_IN_MEASURE`` for a key other than ``measure_id`` and
                ``options``, ``STAGE_ZERO_MEASURE`` for the specific case of ``stage: 0``,
                ``UNKNOWN_MEASURE`` for a measure the catalogue does not have, and
                ``DUPLICATE_MEASURE`` when one measure appears twice.
        """
        entries: List[PackageEntry] = []
        seen: Dict[str, int] = {}
        for index, raw in enumerate(package):
            path = PackagePaths.entry(index)
            if not isinstance(raw, Mapping):
                raise ValidationError(
                    ReasonCode.UNKNOWN_KEY_IN_MEASURE,
                    path,
                    f"a package entry is an object with {PackagePaths.MEASURE_ID_KEY} and "
                    f"{PackagePaths.OPTIONS_KEY}, not a {type(raw).__name__}",
                )
            self._check_entry_keys(raw, path)
            measure_id = str(raw.get(PackagePaths.MEASURE_ID_KEY, ""))
            try:
                self._catalogue.by_id(measure_id)
            except KeyError as error:
                raise ValidationError(
                    ReasonCode.UNKNOWN_MEASURE,
                    f"{path}.{PackagePaths.MEASURE_ID_KEY}",
                    f"'{measure_id}' is not a catalogue measure",
                ) from error
            if measure_id in seen:
                raise ValidationError(
                    ReasonCode.DUPLICATE_MEASURE,
                    f"{path}.{PackagePaths.MEASURE_ID_KEY}",
                    f"'{measure_id}' already appears at {PackagePaths.entry(seen[measure_id])}",
                )
            seen[measure_id] = index
            options = raw.get(PackagePaths.OPTIONS_KEY) or {}
            if not isinstance(options, Mapping):
                raise ValidationError(
                    ReasonCode.UNKNOWN_KEY_IN_MEASURE,
                    f"{path}.{PackagePaths.OPTIONS_KEY}",
                    f"options is an object, not a {type(options).__name__}",
                )
            entries.append(PackageEntry(measure_id=measure_id, options=dict(options)))
        return tuple(entries)

    @classmethod
    def _check_entry_keys(cls, raw: Mapping[str, Any], path: str) -> None:
        """Reject a package entry carrying anything but ``measure_id`` and ``options``."""
        if raw.get(PackagePaths.STAGE_KEY) == PackagePaths.BASE_STATE_STAGE:
            raise ValidationError(
                ReasonCode.STAGE_ZERO_MEASURE,
                f"{path}.{PackagePaths.STAGE_KEY}",
                "a package carries measures to be applied; the base state comes from the inventory alone",
            )
        allowed = {PackagePaths.MEASURE_ID_KEY, PackagePaths.OPTIONS_KEY}
        for key in raw:
            if key not in allowed:
                raise ValidationError(
                    ReasonCode.UNKNOWN_KEY_IN_MEASURE,
                    f"{path}.{key}",
                    f"a package entry carries only {sorted(allowed)}",
                )

    def _current_u_values(
        self, inventory: Inventory, report: MappingReport
    ) -> Tuple[Optional[CurrentUValues], Optional[RefusalDetail]]:
        """Build the U-value source, turning a missing TABULA archetype into a refusal."""
        try:
            source = self._current_u_values_factory(inventory)
        except TabulaLookupError as error:
            return None, TabulaArchetype.refusal_for(error)
        for note in getattr(source, "selection_notes", ()):
            report.approximated(
                f"{EnvelopePaths.GENERAL}.construction_year", note, rule="TABULA archetype selection"
            )
        return source, None

    @classmethod
    def _merge_base_file_key(
        cls, base_key: BaseFileKey, resolved: ResolvedEffects, refusals: List[RefusalDetail]
    ) -> BaseFileKey:
        """Override the inventory's base-file key with what the measures asked for.

        A domestic hot-water heat pump is checked here rather than in the measure, because it is
        the *combination* that has no recorded file: the measure alone cannot know which generator
        the building ends up with.
        """
        selection = resolved.base_file
        generator = selection.generator or base_key.generator
        solar_thermal = base_key.solar_thermal if selection.solar_thermal is None else True
        cars = base_key.cars if selection.cars is None else selection.cars
        if selection.dhw_supply is DhwSupply.HEAT_PUMP and generator is not BaseFiles.DHW_HEAT_PUMP_GENERATOR:
            refusals.append(
                RefusalDetail(
                    reason=ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                    path="package.measures",
                    detail=(
                        f"a domestic hot water heat pump needs a {BaseFiles.DHW_HEAT_PUMP_GENERATOR.value} "
                        f"house; this one runs {generator.value}"
                    ),
                    measure_id="HOT_WATER_SYSTEM",
                )
            )
        return BaseFileKey(generator=generator, solar_thermal=solar_thermal, cars=cars)

    @classmethod
    def _select_base_file(cls, base_key: BaseFileKey, refusals: List[RefusalDetail]) -> str:
        """Return the recorded file for the key, recording a refusal when none exists."""
        try:
            return BaseFiles.select(base_key)
        except KeyError:
            refusals.append(
                RefusalDetail(
                    reason=ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                    path="package.measures",
                    detail=(
                        f"no recorded energy-system file runs {base_key.generator.value} with "
                        f"solar_thermal={base_key.solar_thermal} and {base_key.cars} vehicle(s)"
                    ),
                )
            )
            return ""

    def _write_results(
        self, post: Inventory, resolved: ResolvedEffects, report: MappingReport
    ) -> Dict[str, float]:
        """Write the composed U-values and the inventory fields, and report every one."""
        written: Dict[str, float] = {}
        for element, u_value in resolved.u_values.items():
            path = EnvelopePaths.u_value_path(element)
            post.set(path, u_value)
            written[path] = u_value
            report.used(path, f"composed to {u_value:.4g} W/m2K", rule=resolved.u_value_notes[element])
        for path, value in resolved.writes.items():
            post.set(path, value)
            report.used(path, f"set to {value!r} by a measure")
        for path, law in resolved.pending_laws.items():
            report.approximated(
                path,
                f"to be sized by the {law.law.value} law from {law.argument:g}",
                rule=f"{law.law.value}, resolved by the parametriser before the simulation",
            )
        return written

    @classmethod
    def _report_measures(
        cls, entries: Sequence[PackageEntry], effects: Effects, report: MappingReport
    ) -> None:
        """Add a derived report line for every measure whose function wrote none itself."""
        for index, entry in enumerate(entries):
            report.measure_index(entry.measure_id)
            if report.has_measure(entry.measure_id):
                continue
            produced = effects.by_measure(entry.measure_id)
            no_effects = [item for item in produced if isinstance(item, NoEffect)]
            if no_effects:
                reason = no_effects[0].reason
                report.measure(
                    entry.measure_id,
                    ReportStatus.NON_SIMULATION,
                    reason.describe(),
                    rule=reason.value,
                    index=index,
                )
                continue
            report.measure(
                entry.measure_id,
                ReportStatus.USED,
                f"applied with {len(produced)} effect(s)",
                index=index,
            )

    @classmethod
    def written_paths(cls, resolved: ResolvedEffects) -> Tuple[str, ...]:
        """Return every inventory path a resolution writes, sorted.

        Used by check 4 of ``measures_v2_requirements.md`` §7.5, which asserts that each of them is
        declared by the contract or listed as pending.
        """
        paths = set(resolved.writes) | set(resolved.pending_laws)
        paths |= {EnvelopePaths.u_value_path(element) for element in resolved.u_values}
        return tuple(sorted(paths))
