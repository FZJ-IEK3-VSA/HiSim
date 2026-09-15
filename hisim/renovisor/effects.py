"""The closed set of effects a measure can have, and the accumulator that resolves them.

A registry function does not change an inventory; it records *effects*, and every effect is one of
eight frozen dataclasses. That closure is the point (``measures_v2_requirements.md`` §7.3): mypy
can then check that a measure produces nothing else, and an ``assert_never`` in the resolver makes
a newly added effect type a type error until it is handled.

Why an accumulator and not direct writes: two measures on one wall are two insulation layers, and
a structure that stores a final U-value per element while measures are still running loses the
second layer silently (requirement M2). :class:`Effects` therefore holds contributions until every
measure has run, and :meth:`Effects.resolve` composes each element once::

    effects = Effects(materials, current_u_values, targets)
    MeasureRegistry.external_insulation(options, inventory, effects)
    MeasureRegistry.rolled_out_attic_insulation(other_options, inventory, effects)
    resolved = effects.resolve(inventory, current_u_values)
    resolved.u_values[ThermalElement.FACADE]        # one composed value

Sizing laws are recorded, not run. ``days to cover`` needs an occupancy profile and a heating
demand that only the parametriser of step 5 has, so :class:`LawRequest` travels as a value and the
report line says ``approximated`` and names the law (decision Q12).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union, assert_never

from hisim.renovisor.envelope import (
    EnvelopePaths,
    CurrentUValues,
    RegulatoryTargets,
    ThicknessDefault,
    UValueComposer,
)
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.options import Default
from hisim.renovisor.reasons import ReasonCode, RefusalDetail
from hisim.renovisor.vocabulary import DhwSupply, HeatGenerator, SolarThermalSupplies, ThermalElement


class SizingLaw(str, Enum):
    """A rule that turns a relative size into an absolute one, run later than this step.

    ``PV_SHARE_OF_ROOF`` turns a share of the usable roof into an installed peak power, using the
    roof geometry (decision Q16). ``BATTERY_FROM_DAYS_TO_COVER`` turns a number of days into a
    storage capacity from the household load plus the heat pump plus the vehicle (decision Q12).
    Both are reported as approximations naming the law, because both rest on assumptions the
    request did not make.
    """

    PV_SHARE_OF_ROOF = "PV_SHARE_OF_ROOF"
    BATTERY_FROM_DAYS_TO_COVER = "BATTERY_FROM_DAYS_TO_COVER"


@dataclass(frozen=True)
class LawRequest:
    """A sizing law and its argument, recorded now and resolved by the parametriser.

    Args:
        law: Which law to run.
        argument: Its single input — a share between 0 and 1 for the PV law, a number of days for
            the battery law.
    """

    law: SizingLaw
    argument: float


@dataclass(frozen=True)
class AddThermalResistance:
    """An insulation layer added to one envelope element.

    Args:
        element: Which element the layer sits on.
        material_asp_id: The insulation material's database id, which supplies λ.
        thickness_in_mm: The layer's thickness.
        measure_id: The catalogue measure that asked for it.
    """

    element: ThermalElement
    material_asp_id: str
    thickness_in_mm: int
    measure_id: str


@dataclass(frozen=True)
class SetUValue:
    """A replacement of one envelope element, which sets its U-value instead of improving it.

    Args:
        element: Which element was replaced.
        u_value_in_watt_per_m2_per_kelvin: The new element's U-value.
        measure_id: The catalogue measure that asked for it.
    """

    element: ThermalElement
    u_value_in_watt_per_m2_per_kelvin: float
    measure_id: str


@dataclass(frozen=True)
class SetInventoryField:
    """One write into the post-measure inventory, by path.

    Exactly one of *value* and *law* is set. A law is a value the parametriser computes later; the
    field is left as the inventory had it until then, and the application reports it as pending.

    Args:
        path: The dotted inventory path to write.
        value: The value to write, when the measure knows it.
        law: The sizing law to run, when it does not.
        measure_id: The catalogue measure that asked for it.
    """

    path: str
    measure_id: str
    value: Any = None
    law: Optional[LawRequest] = None


@dataclass(frozen=True)
class SelectVariant:
    """A choice of one of a base file's exclusive variant options.

    Args:
        variant: The variant's name in the energy-system file, e.g. ``electricity_management``.
        option: The option to select, e.g. ``ems_with_battery``.
        measure_id: The catalogue measure that asked for it.
    """

    variant: str
    option: str
    measure_id: str


@dataclass(frozen=True)
class EnableGroup:
    """A switch turning on one of a base file's optional component groups.

    Args:
        group: The group's name in the energy-system file.
        measure_id: The catalogue measure that asked for it.
    """

    group: str
    measure_id: str


@dataclass(frozen=True)
class SelectBaseFile:
    """A change to which recorded energy-system file the calculation runs.

    Each field is ``None`` when the measure does not speak to it: the heating-system measure sets
    only the generator, the solar-thermal measure only the collector, the vehicle measure only the
    car count. The application merges them and refuses when two measures disagree.

    Args:
        measure_id: The catalogue measure that asked for it.
        generator: The heat generator, or ``None``.
        solar_thermal: What a collector feeds, or ``None``.
        cars: The number of electric vehicles, or ``None``.
        dhw_supply: How domestic hot water is produced, or ``None``.
    """

    measure_id: str
    generator: Optional[HeatGenerator] = None
    solar_thermal: Optional[SolarThermalSupplies] = None
    cars: Optional[int] = None
    dhw_supply: Optional[DhwSupply] = None


@dataclass(frozen=True)
class NoEffect:
    """A measure that was accepted and changed nothing, because HiSim has no model for it.

    Requirement M8: the package still simulates and the report says what did nothing, so a
    homeowner comparing two packages is told why they came out the same.

    Args:
        reason: A reason code from the no-effect group.
        measure_id: The catalogue measure.
    """

    reason: ReasonCode
    measure_id: str


@dataclass(frozen=True)
class Refusal:
    """A measure that cannot be simulated, which stops the whole package.

    Args:
        reason: A reason code from the refusal group.
        path: The JSON path of the input that cannot be honoured.
        detail: One sentence naming the value and what is missing.
        measure_id: The catalogue measure.
    """

    reason: ReasonCode
    path: str
    detail: str
    measure_id: str

    def as_detail(self) -> RefusalDetail:
        """Return this refusal in the form :class:`~hisim.renovisor.reasons.RefusalError` carries."""
        return RefusalDetail(
            reason=self.reason, path=self.path, detail=self.detail, measure_id=self.measure_id
        )


#: Every effect a measure may produce. A type alias, not data: it is what lets mypy check that the
#: resolver handles all of them and that a measure produces nothing else.
Effect = Union[
    AddThermalResistance,
    SetUValue,
    SetInventoryField,
    SelectVariant,
    EnableGroup,
    SelectBaseFile,
    NoEffect,
    Refusal,
]


@dataclass(frozen=True)
class BaseFileSelection:
    """The merged base-file wishes of every measure in a package.

    Each field is ``None`` when no measure spoke to it, in which case the application keeps what
    the inventory says.

    Args:
        generator: The heat generator a measure asked for.
        solar_thermal: What a collector should feed.
        cars: The number of electric vehicles.
        dhw_supply: How domestic hot water should be produced.
    """

    generator: Optional[HeatGenerator] = None
    solar_thermal: Optional[SolarThermalSupplies] = None
    cars: Optional[int] = None
    dhw_supply: Optional[DhwSupply] = None


@dataclass(frozen=True)
class ResolvedEffects:
    """What a package's effects come to, once every measure has run.

    Args:
        u_values: The composed U-value per element, for the elements any measure touched.
        u_value_notes: Per element, one sentence saying how the value was composed, for the report.
        writes: Inventory path -> the value to write.
        pending_laws: Inventory path -> the sizing law that will produce its value in step 5.
        variant_selections: Variant name -> selected option.
        enabled_groups: The groups to switch on, sorted.
        base_file: The merged base-file wishes.
        no_effects: Every measure that changed nothing, with its reason.
        refusals: Everything that makes the package unsimulable; empty for a good package.
    """

    u_values: Mapping[ThermalElement, float]
    u_value_notes: Mapping[ThermalElement, str]
    writes: Mapping[str, Any]
    pending_laws: Mapping[str, LawRequest]
    variant_selections: Mapping[str, str]
    enabled_groups: Tuple[str, ...]
    base_file: BaseFileSelection
    no_effects: Tuple[NoEffect, ...]
    refusals: Tuple[Refusal, ...]


class Effects:
    """The accumulator a registry function writes its effects into.

    One instance per package. It also carries the three things a measure needs to size a layer —
    the material table, the element's current U-values and the regulatory targets — so that a
    registry function can ask for a target-driven default thickness without being handed the
    physics itself (:meth:`target_driven_thickness`).

    Args:
        materials: The insulation-material table, for λ.
        current_u_values: Where each element's pre-measure U-value comes from.
        targets: The regulatory target U-values a defaulted thickness aims at.
    """

    def __init__(
        self,
        materials: InsulationMaterials,
        current_u_values: CurrentUValues,
        targets: RegulatoryTargets,
    ) -> None:
        """Create an empty accumulator over the given physics inputs."""
        self._materials = materials
        self._current_u_values = current_u_values
        self._targets = targets
        self._effects: List[Effect] = []

    @property
    def materials(self) -> InsulationMaterials:
        """Return the material table this accumulator sizes layers with."""
        return self._materials

    @property
    def targets(self) -> RegulatoryTargets:
        """Return the regulatory target table a defaulted thickness aims at."""
        return self._targets

    def all(self) -> Tuple[Effect, ...]:
        """Return every effect recorded so far, in the order the measures produced them."""
        return tuple(self._effects)

    def by_measure(self, measure_id: str) -> Tuple[Effect, ...]:
        """Return the effects one measure produced, in order."""
        return tuple(effect for effect in self._effects if effect.measure_id == measure_id)

    def add_thermal_resistance(
        self, element: ThermalElement, material_asp_id: str, thickness_in_mm: int, measure_id: str
    ) -> None:
        """Record an insulation layer on one element."""
        self._effects.append(
            AddThermalResistance(
                element=element,
                material_asp_id=material_asp_id,
                thickness_in_mm=thickness_in_mm,
                measure_id=measure_id,
            )
        )

    def set_u_value(
        self, element: ThermalElement, u_value_in_watt_per_m2_per_kelvin: float, measure_id: str
    ) -> None:
        """Record a replacement of one element, which sets its baseline U-value."""
        self._effects.append(
            SetUValue(
                element=element,
                u_value_in_watt_per_m2_per_kelvin=u_value_in_watt_per_m2_per_kelvin,
                measure_id=measure_id,
            )
        )

    def set_inventory_field(
        self, path: str, measure_id: str, value: Any = None, law: Optional[LawRequest] = None
    ) -> None:
        """Record one inventory write, by value or by sizing law.

        Raises:
            ValueError: When both a value and a law are given, or neither.
        """
        if (value is None) == (law is None):
            raise ValueError(f"writing '{path}' needs exactly one of a value and a law")
        self._effects.append(SetInventoryField(path=path, measure_id=measure_id, value=value, law=law))

    def select_variant(self, variant: str, option: str, measure_id: str) -> None:
        """Record a choice of one of the base file's exclusive variant options."""
        self._effects.append(SelectVariant(variant=variant, option=option, measure_id=measure_id))

    def enable_group(self, group: str, measure_id: str) -> None:
        """Record that one of the base file's optional component groups is switched on."""
        self._effects.append(EnableGroup(group=group, measure_id=measure_id))

    def select_base_file(
        self,
        measure_id: str,
        generator: Optional[HeatGenerator] = None,
        solar_thermal: Optional[SolarThermalSupplies] = None,
        cars: Optional[int] = None,
        dhw_supply: Optional[DhwSupply] = None,
    ) -> None:
        """Record a wish about which recorded base file the calculation runs."""
        self._effects.append(
            SelectBaseFile(
                measure_id=measure_id,
                generator=generator,
                solar_thermal=solar_thermal,
                cars=cars,
                dhw_supply=dhw_supply,
            )
        )

    def no_effect(self, reason: ReasonCode, measure_id: str) -> None:
        """Record that a measure was accepted and changed nothing."""
        self._effects.append(NoEffect(reason=reason, measure_id=measure_id))

    def refuse(self, reason: ReasonCode, path: str, detail: str, measure_id: str) -> None:
        """Record that a measure cannot be simulated, which will stop the package."""
        self._effects.append(Refusal(reason=reason, path=path, detail=detail, measure_id=measure_id))

    def target_driven_thickness(
        self, element: ThermalElement, material_asp_id: str, target_id: str
    ) -> Default:
        """Return the default thickness for a layer, with the sentence the report prints.

        Decision Q11: an insulation layer whose thickness the request does not state is made just
        thick enough to reach the Irish regulatory target for the element, from the element's
        current U-value and the material's λ, rounded up to the next 10 mm and with no
        thermal-bridge surcharge.

        Args:
            element: The element the layer sits on.
            material_asp_id: The chosen material's database id.
            target_id: The row of the regulatory target table to aim at, e.g. ``"wall"``.

        Returns:
            A :class:`~hisim.renovisor.options.Default` whose value is the thickness in
            millimetres and whose source names the target, the inputs and the simplification.

        Raises:
            KeyError: When the material or the target row does not exist; the registry function
                turns that into a refusal rather than inventing a number.
        """
        conductivity = self._materials.by_asp_id(
            material_asp_id
        ).thermal_conductivity_in_watt_per_meter_per_kelvin
        current = self._current_u_values.u_value(element)
        target = self._targets.u_value(target_id)
        thickness = ThicknessDefault.for_target(current, conductivity, target)
        return Default(
            value=thickness,
            source=(
                f"Q11 target-driven thickness: the smallest {ThicknessDefault.STEP_IN_MM} mm step that brings "
                f"the {element.value.lower()} from {current:g} W/m2K ({self._current_u_values.source_of(element)}) "
                f"to the target {self._targets.describe(target_id)} with lambda = {conductivity:g} W/mK; "
                "no thermal-bridge surcharge"
            ),
        )

    def resolve(self, inventory: Any, current_u_values: CurrentUValues) -> ResolvedEffects:
        """Compose every recorded effect into the changes the application applies.

        Per element: the baseline U-value is the last replacement if a measure replaced the
        element, otherwise the building's current value; every insulation layer's resistance is
        added to it, and one U-value is written. Everything else is collected, with two writes of
        different values to one target becoming a ``CONFLICTING_WRITES`` refusal.

        Args:
            inventory: The pre-measure inventory. Unused today — the element's current U-value
                comes from *current_u_values*, which reads the inventory itself — and kept in the
                signature because a later effect (a relative write, an area-dependent rule) will
                need it.
            current_u_values: Where each element's pre-measure U-value comes from.

        Returns:
            The :class:`ResolvedEffects`.

        Raises:
            ValueError: When an insulation layer names a material the table does not have, which
                a registry function must have refused before recording the layer.
        """
        del inventory
        resistances: Dict[ThermalElement, List[Tuple[AddThermalResistance, float]]] = {}
        baselines: Dict[ThermalElement, SetUValue] = {}
        writes: Dict[str, Any] = {}
        write_sources: Dict[str, str] = {}
        pending_laws: Dict[str, LawRequest] = {}
        variants: Dict[str, str] = {}
        variant_sources: Dict[str, str] = {}
        groups: List[str] = []
        selection = BaseFileSelection()
        no_effects: List[NoEffect] = []
        refusals: List[Refusal] = []

        for effect in self._effects:
            if isinstance(effect, AddThermalResistance):
                material = self._materials.by_asp_id(effect.material_asp_id)
                resistance = UValueComposer.resistance(
                    effect.thickness_in_mm, material.thermal_conductivity_in_watt_per_meter_per_kelvin
                )
                resistances.setdefault(effect.element, []).append((effect, resistance))
            elif isinstance(effect, SetUValue):
                baselines[effect.element] = effect
            elif isinstance(effect, SetInventoryField):
                refusal = self._collect_write(effect, writes, write_sources, pending_laws)
                if refusal is not None:
                    refusals.append(refusal)
            elif isinstance(effect, SelectVariant):
                refusal = self._collect_variant(effect, variants, variant_sources)
                if refusal is not None:
                    refusals.append(refusal)
            elif isinstance(effect, EnableGroup):
                if effect.group not in groups:
                    groups.append(effect.group)
            elif isinstance(effect, SelectBaseFile):
                selection, refusal = self._merge_base_file(selection, effect)
                if refusal is not None:
                    refusals.append(refusal)
            elif isinstance(effect, NoEffect):
                no_effects.append(effect)
            elif isinstance(effect, Refusal):
                refusals.append(effect)
            else:
                assert_never(effect)

        u_values, u_value_notes = self._compose_elements(resistances, baselines, current_u_values)
        return ResolvedEffects(
            u_values=u_values,
            u_value_notes=u_value_notes,
            writes=writes,
            pending_laws=pending_laws,
            variant_selections=variants,
            enabled_groups=tuple(sorted(groups)),
            base_file=selection,
            no_effects=tuple(no_effects),
            refusals=tuple(refusals),
        )

    def _compose_elements(
        self,
        resistances: Mapping[ThermalElement, List[Tuple[AddThermalResistance, float]]],
        baselines: Mapping[ThermalElement, SetUValue],
        current_u_values: CurrentUValues,
    ) -> Tuple[Dict[ThermalElement, float], Dict[ThermalElement, str]]:
        """Compose one U-value per touched element and describe how it was reached."""
        composed: Dict[ThermalElement, float] = {}
        notes: Dict[ThermalElement, str] = {}
        for element in set(resistances) | set(baselines):
            replacement = baselines.get(element)
            if replacement is not None:
                baseline = replacement.u_value_in_watt_per_m2_per_kelvin
                origin = f"{replacement.measure_id} replaced the element at {baseline:g} W/m2K"
            else:
                baseline = current_u_values.u_value(element)
                origin = f"{baseline:g} W/m2K from {current_u_values.source_of(element)}"
            layers = resistances.get(element, [])
            composed[element] = UValueComposer.compose(baseline, [resistance for _, resistance in layers])
            described = ", ".join(
                f"{layer.measure_id} {layer.thickness_in_mm} mm of {layer.material_asp_id} "
                f"(+{resistance:.3g} m2K/W)"
                for layer, resistance in layers
            )
            notes[element] = f"{origin}; {described}" if described else origin
        return composed, notes

    def _collect_write(
        self,
        effect: SetInventoryField,
        writes: Dict[str, Any],
        sources: Dict[str, str],
        pending_laws: Dict[str, LawRequest],
    ) -> Optional[Refusal]:
        """Record one inventory write, refusing when another measure already wrote that path."""
        if effect.law is not None:
            existing_law = pending_laws.get(effect.path)
            if existing_law is not None and existing_law != effect.law:
                return self._conflict(effect.path, sources.get(effect.path, "?"), effect.measure_id)
            pending_laws[effect.path] = effect.law
            sources[effect.path] = effect.measure_id
            return None
        if effect.path in writes and writes[effect.path] != effect.value:
            return self._conflict(effect.path, sources.get(effect.path, "?"), effect.measure_id)
        if effect.path in pending_laws:
            return self._conflict(effect.path, sources.get(effect.path, "?"), effect.measure_id)
        writes[effect.path] = effect.value
        sources[effect.path] = effect.measure_id
        return None

    def _collect_variant(
        self, effect: SelectVariant, variants: Dict[str, str], sources: Dict[str, str]
    ) -> Optional[Refusal]:
        """Record one variant selection, refusing when another measure chose a different option."""
        existing = variants.get(effect.variant)
        if existing is not None and existing != effect.option:
            return Refusal(
                reason=ReasonCode.CONFLICTING_WRITES,
                path=f"variants.{effect.variant}",
                detail=(
                    f"{sources.get(effect.variant, '?')} selected '{existing}' and "
                    f"{effect.measure_id} selects '{effect.option}'"
                ),
                measure_id=effect.measure_id,
            )
        variants[effect.variant] = effect.option
        sources[effect.variant] = effect.measure_id
        return None

    def _merge_base_file(
        self, selection: BaseFileSelection, effect: SelectBaseFile
    ) -> Tuple[BaseFileSelection, Optional[Refusal]]:
        """Merge one base-file wish into the running selection, refusing on a disagreement."""
        merged: Dict[str, Any] = {
            "generator": selection.generator,
            "solar_thermal": selection.solar_thermal,
            "cars": selection.cars,
            "dhw_supply": selection.dhw_supply,
        }
        for field_name in merged:
            wished = getattr(effect, field_name)
            if wished is None:
                continue
            if merged[field_name] is not None and merged[field_name] != wished:
                return selection, Refusal(
                    reason=ReasonCode.CONFLICTING_WRITES,
                    path=f"base_file.{field_name}",
                    detail=(
                        f"two measures disagree about the base file's {field_name}: "
                        f"'{merged[field_name]}' and '{wished}'"
                    ),
                    measure_id=effect.measure_id,
                )
            merged[field_name] = wished
        return BaseFileSelection(**merged), None

    @classmethod
    def _conflict(cls, path: str, first_measure: str, second_measure: str) -> Refusal:
        """Return the refusal for two measures writing different values to one inventory path."""
        return Refusal(
            reason=ReasonCode.CONFLICTING_WRITES,
            path=path,
            detail=f"{first_measure} and {second_measure} write different values to '{path}'",
            measure_id=second_measure,
        )

    @classmethod
    def u_value_path(cls, element: ThermalElement) -> str:
        """Return the inventory path a composed U-value is written to."""
        return EnvelopePaths.u_value_path(element)
