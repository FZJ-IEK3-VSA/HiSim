"""Applying one package of catalogue measures to one house, and saying what each of them did.

``apply(house, measures)`` writes every measure into a **deep copy** of the request's house, in
list order, and returns the renovated house together with one report line per measure and per
option. The original is never touched, which is what lets a test deep-diff the two and what lets
the capability document run hundreds of probes off one anchor request.

Three things are worth stating beyond the table of §4.2 of the calculation-request
specification:

*Applying a measure is a plain overwrite.* The inventory field and the measure option that
changes it share one vocabulary (rule 4), so ``heating_system.type_of_system =
air_source_heat_pump`` is written into ``house.heating.type_of_system`` verbatim, with no value
map anywhere.

*Insulation layers stack.* ``added_insulation`` is a list on the element, and each layer is
applied to the result of the one before it, so external insulation followed by cavity fill is
one wall with two layers. The element's U-value is recomputed as each layer lands, which is what
makes list order matter and what the note shows with its numbers.

*Nothing is refused here.* Every former refusal of this layer is now either a plain write or an
entry of ``not_implemented_yet.yaml``. A measure this translator has no model for is written
into the house like any other and produces its note in :mod:`hisim.renovisor.translate`; a
measure, option or value that is neither mapped nor listed fails the translator's build, and the
question is asked once, at the end, against the renovated house -- because whether a separate
hot-water heat pump can be modelled depends on the generator the *package* installs.
"""

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.renovisor.constants import (
    FixedMaterials,
    LayerDefaults,
    OpeningUValues,
    Placement,
)
from hisim.renovisor.envelope import LayerNote, UValueComposer
from hisim.renovisor.request import CatalogueTable, Material, Measure
from hisim.renovisor.vocabulary import ReportStatus, ThermalElement
from hisim.renovisor.whitelist import Unmapped, Whitelist, WhitelistEntry


class HousePaths:
    """The dotted paths inside ``house`` that measures and the translator both name.

    Held once so that a measure function, the report and the whitelist cannot disagree about
    where a value lives. A path here is relative to ``house``; the report prefixes ``house.``.
    """

    #: The building block and the five elements under it.
    BUILDING: ClassVar[str] = "building"
    SET_HEATING_TEMPERATURE: ClassVar[str] = "building.set_heating_temperature_in_celsius"

    #: The blocks a measure writes into, each of which a request may leave out entirely.
    OCCUPANCY: ClassVar[str] = "occupancy"
    HEATING: ClassVar[str] = "heating"
    HEAT_DISTRIBUTION: ClassVar[str] = "heat_distribution"
    HOT_WATER: ClassVar[str] = "hot_water"
    VENTILATION: ClassVar[str] = "ventilation"
    TEMPERATURE_CONTROL: ClassVar[str] = "temperature_control"
    AIR_CONDITIONING: ClassVar[str] = "air_conditioning"
    APPLIANCES: ClassVar[str] = "appliances"
    PV_SYSTEM: ClassVar[str] = "pv_system"
    BATTERY: ClassVar[str] = "battery"
    SOLAR_THERMAL: ClassVar[str] = "solar_thermal_system"
    ELECTRIC_VEHICLES: ClassVar[str] = "electric_vehicles"

    #: The key an insulation layer is appended to on an element.
    ADDED_INSULATION: ClassVar[str] = "added_insulation"

    #: The element key carrying the value the physics reads.
    U_VALUE: ClassVar[str] = "u_value_in_watt_per_m2_per_kelvin"

    @classmethod
    def element(cls, element: ThermalElement) -> str:
        """Return the path of one envelope element's block, e.g. ``building.facade``."""
        return f"{cls.BUILDING}.{element.value}"

    @classmethod
    def u_value(cls, element: ThermalElement) -> str:
        """Return the path of one envelope element's U-value."""
        return f"{cls.element(element)}.{cls.U_VALUE}"


@dataclass(frozen=True)
class AddedLayer:
    """One insulation layer a measure added, as the effect log records it.

    Args:
        element: The element the layer sits on.
        placement: The ``building_components`` value naming where in the build-up it sits.
        thickness_in_mm: How thick it is, from the request or from the default table.
        material: Its properties, from the request or from the fixed-material table.
        measure_id: The measure that added it.
        thickness_defaulted: Whether the thickness came from the table rather than the request.
        material_fixed: Whether the material came from the table rather than the request.
    """

    element: ThermalElement
    placement: str
    thickness_in_mm: int
    material: Material
    measure_id: str
    thickness_defaulted: bool = False
    material_fixed: bool = False

    def to_house(self) -> Dict[str, Any]:
        """Return the layer as the renovated house carries it under ``added_insulation``."""
        material: Dict[str, Any] = {
            "asp_id": self.material.asp_id,
            "thermal_conductivity_w_mk": self.material.thermal_conductivity_w_mk,
        }
        for name in ("heat_capacity_j_kgk", "density_kg_m3", "co2_footprint_a1_a3_c3_c4_kg_m2", "lifespan_years"):
            value = getattr(self.material, name)
            if value is not None:
                material[name] = value
        return {
            "placement": self.placement,
            "thickness_in_mm": self.thickness_in_mm,
            "material": material,
        }


@dataclass
class OptionLine:
    """What one measure option came to, as the mapping report's ``measures[].options`` carries it.

    It is mutable because a ``not_implemented_yet`` line is written before its note is known:
    the whitelist is asked once, at the end, against the renovated house, and the answer fills
    the note in. Nothing else ever changes a line after it is recorded.

    Args:
        name: The option's name in the catalogue.
        status: What the translator did with it.
        note: The sentence explaining it, or ``None`` when the status says everything.
    """

    name: str
    status: ReportStatus
    note: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the line as the mapping report writes it."""
        row: Dict[str, Any] = {"name": self.name, "status": self.status.value}
        if self.note is not None:
            row["note"] = self.note
        return row


@dataclass
class MeasureLine:
    """What one measure came to, as the mapping report's ``measures[]`` carries it.

    Args:
        id: The catalogue id.
        status: The measure's own status, derived from its ``everyone`` options and from whether
            the measure itself is on the whitelist.
        options: One line per option the request carried, plus one per option the translator
            defaulted.
        targets: The HiSim components and fields this measure caused to be written.
        note: The whitelist entry's note when the measure is listed, or the approximation's own
            sentence, or ``None``.
    """

    id: str
    status: ReportStatus = ReportStatus.USED
    options: List[OptionLine] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    note: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the entry as the mapping report writes it."""
        row: Dict[str, Any] = {
            "id": self.id,
            "status": self.status.value,
            "options": [option.to_json() for option in self.options],
            "targets": sorted(set(self.targets)),
        }
        if self.note is not None:
            row["note"] = self.note
        return row


class Effects:
    """The closed set of things a measure may do to a house, and the log of what it did.

    A registry function never touches the house dictionary: it calls one of the five methods
    below. That closure is what makes the measure layer reviewable -- a reader can see at a
    glance that no measure reaches into a HiSim component -- and what lets the capability
    document report a measure's targets without running a simulation.

    Writes land immediately, because list order is part of the contract: the second layer on a
    wall is applied to the result of the first, and the second measure writing one field wins.

    Args:
        house: The working copy of the request's house. It is mutated in place; the caller has
            already deep-copied it.
    """

    def __init__(self, house: Dict[str, Any]) -> None:
        """Store the working house and remember every element's U-value before any measure."""
        self._house = house
        self._layers: List[AddedLayer] = []
        self._original_u_values: Dict[ThermalElement, float] = {}
        self._written: List[str] = []
        self._removed: List[str] = []
        for element in ThermalElement:
            value = self.read(HousePaths.u_value(element))
            if isinstance(value, (int, float)):
                self._original_u_values[element] = float(value)

    @property
    def house(self) -> Dict[str, Any]:
        """Return the working house, as far as the measures have written it."""
        return self._house

    def layers(self) -> Tuple[AddedLayer, ...]:
        """Return every insulation layer the package added, in the order it added them."""
        return tuple(self._layers)

    def written_paths(self) -> Tuple[str, ...]:
        """Return every house path a measure wrote, in write order, with repeats."""
        return tuple(self._written)

    def removed_paths(self) -> Tuple[str, ...]:
        """Return every house path a measure removed, in removal order."""
        return tuple(self._removed)

    def original_u_value(self, element: ThermalElement) -> Optional[float]:
        """Return one element's U-value as the request stated it, before any layer."""
        return self._original_u_values.get(element)

    def read(self, path: str) -> Any:
        """Return the value at one dotted house path, or ``None`` when the house lacks it."""
        current: Any = self._house
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
        return current

    def set(self, path: str, value: Any) -> None:
        """Write one value into the house, creating the blocks on the way.

        Args:
            path: A dotted path relative to ``house``, e.g. ``heating.type_of_system``.
            value: The value to write, which is the catalogue's own string for every enum.
        """
        parts = path.split(".")
        current: Dict[str, Any] = self._house
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = value
        self._written.append(path)

    def replace_block(self, path: str, value: Dict[str, Any]) -> None:
        """Replace a whole block of the house, dropping whatever it carried before.

        Used by the three measures whose effect is "this device is now a different device":
        the photovoltaic array, the battery and the vehicle fleet describe the new total, not an
        addition to the old one (§4.2).
        """
        self._house[path] = value
        self._written.append(path)

    def remove(self, path: str) -> bool:
        """Remove one value from the house and say whether it was there.

        Args:
            path: A dotted path relative to ``house``.

        Returns:
            ``True`` when the house carried the value and it is now gone.
        """
        parts = path.split(".")
        current: Any = self._house
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        if not isinstance(current, dict) or parts[-1] not in current:
            return False
        del current[parts[-1]]
        self._removed.append(path)
        return True

    def add_layer(self, layer: AddedLayer) -> float:
        """Append one insulation layer to its element and recompute the element's U-value.

        Args:
            layer: The layer, with its thickness and its material already resolved.

        Returns:
            The element's U-value after the layer, in W/(m²·K).

        Raises:
            ValueError: When the element's U-value or the material's conductivity is not
                positive, which a validated request cannot produce.
        """
        element = self._element_block(layer.element)
        element.setdefault(HousePaths.ADDED_INSULATION, [])
        existing = element[HousePaths.ADDED_INSULATION]
        if isinstance(existing, dict):
            existing = [existing]
        existing.append(layer.to_house())
        element[HousePaths.ADDED_INSULATION] = existing
        composed = UValueComposer.compose(
            float(element[HousePaths.U_VALUE]),
            [UValueComposer.resistance(layer.thickness_in_mm, layer.material.thermal_conductivity_w_mk)],
        )
        element[HousePaths.U_VALUE] = composed
        self._layers.append(layer)
        self._written.append(HousePaths.u_value(layer.element))
        return composed

    def note_for(self, element: ThermalElement) -> str:
        """Return the arithmetic note for one element that layers were added to.

        Returns:
            The sentence showing the starting U-value, every layer and the result, as
            :class:`hisim.renovisor.envelope.LayerNote` builds it.
        """
        existing = self._original_u_values[element]
        layers = [
            (layer.measure_id, float(layer.thickness_in_mm), layer.material.asp_id,
             layer.material.thermal_conductivity_w_mk)
            for layer in self._layers
            if layer.element is element
        ]
        composed = float(self._element_block(element)[HousePaths.U_VALUE])
        return LayerNote.of(existing, layers, composed)

    def _element_block(self, element: ThermalElement) -> Dict[str, Any]:
        """Return the mutable block of one envelope element."""
        building: Dict[str, Any] = self._house.setdefault(HousePaths.BUILDING, {})
        block: Dict[str, Any] = building.setdefault(element.value, {})
        return block


@dataclass
class MeasureContext:
    """Everything one registry function is given, and everything it hands back.

    Args:
        measure: The measure as the request carried it.
        effects: The accumulator it writes through.
        line: Its report line, which it fills in as it goes.
        unmapped: The items it could not map, asked of the whitelist once the package is done.
    """

    measure: Measure
    effects: Effects
    line: MeasureLine
    unmapped: List[Tuple[Unmapped, OptionLine]] = field(default_factory=list)

    def option(self, name: str) -> Any:
        """Return one option value the request carried, or ``None`` when it did not."""
        return self.measure.options.get(name)

    def record(self, name: str, status: ReportStatus, note: Optional[str] = None) -> None:
        """Add one option line to the measure's report entry."""
        self.line.options.append(OptionLine(name=name, status=status, note=note))

    def target(self, *targets: str) -> None:
        """Record the HiSim components and fields this measure caused to be written."""
        self.line.targets.extend(targets)

    def defer(self, item: str, value: Any, name: str) -> None:
        """Record an option the translator has no target for, to be matched against the list.

        Args:
            item: The measure item the whitelist is asked about, e.g.
                ``heating_system.installation_year``.
            value: The value the request carried, which an entry's ``except`` is tested against.
            name: The option's name, for the report line the answer fills in.
        """
        line = OptionLine(name=name, status=ReportStatus.NOT_IMPLEMENTED_YET)
        self.line.options.append(line)
        self.unmapped.append((Unmapped.measure(item, value), line))


@dataclass(frozen=True)
class InsulationSpec:
    """Where one insulation measure puts its layer, per the §4.2 table.

    Args:
        element: The envelope element the layer sits on.
        placement: The ``materials.yaml`` ``building_components`` value naming the build-up
            position. Provenance in this release; it does not enter the arithmetic.
    """

    element: ThermalElement
    placement: str


class MeasureRegistry:
    """One function per catalogue measure, keyed by the catalogue id.

    A measure function reads its options off the context, writes through the effects, and adds
    one option line per option it saw. It never names a HiSim component or a config field: the
    ``targets`` it records are strings for the report and the capability document, and the
    actual writing into an energy-system file is :mod:`hisim.renovisor.translate`'s job. That
    separation is what makes a HiSim rename a change in one binding rather than in 32 functions.

    ``BY_ID`` is checked against the frozen catalogue table at import time by
    :meth:`assert_complete`, so a measure added to the catalogue and forgotten here is a failing
    test rather than a silently ignored request.
    """

    #: Where each insulation measure's layer goes (calculation-request.md §4.2).
    INSULATION: ClassVar[Dict[str, InsulationSpec]] = {
        "external_insulation": InsulationSpec(
            ThermalElement.FACADE, Placement.EXTERNAL_WALL_EXTERNAL.value
        ),
        "internal_dry_lining_insulation": InsulationSpec(
            ThermalElement.FACADE, Placement.EXTERNAL_WALL_INTERNAL.value
        ),
        "cavity_wall_insulation": InsulationSpec(
            ThermalElement.FACADE, Placement.EXTERNAL_WALL_CAVITY.value
        ),
        "basement_ceiling_insulation": InsulationSpec(
            ThermalElement.FLOOR, Placement.BASEMENT_CEILING.value
        ),
        "basement_internal_insulation": InsulationSpec(
            ThermalElement.FLOOR, Placement.BASEMENT_FLOOR_AND_WALLS_INSIDE.value
        ),
        "basement_external_insulation": InsulationSpec(
            ThermalElement.FLOOR, Placement.BASEMENT_FLOOR_AND_WALLS_OUTSIDE.value
        ),
        "solid_ground_floor_insulation": InsulationSpec(
            ThermalElement.FLOOR, Placement.FLOOR_AND_CEILING.value
        ),
        "suspended_ground_floor_insulation": InsulationSpec(
            ThermalElement.FLOOR, Placement.FLOOR_AND_CEILING.value
        ),
        "warm_roof_insulation": InsulationSpec(
            ThermalElement.ROOF, Placement.ROOF_EXTERNAL_RAFTER.value
        ),
        "rafter_insulation": InsulationSpec(
            ThermalElement.ROOF, Placement.ROOF_BETWEEN_RAFTER.value
        ),
        "rolled_out_attic_insulation": InsulationSpec(
            ThermalElement.ROOF, Placement.TOP_FLOOR_CEILING.value
        ),
        "top_floor_ceiling_insulation": InsulationSpec(
            ThermalElement.ROOF, Placement.TOP_FLOOR_CEILING.value
        ),
    }

    #: The note every ``material`` option carries: the conductivity is physics, the rest is data.
    MATERIAL_NOTE: ClassVar[str] = (
        "conductivity used; heat capacity, density, CO2 footprint and lifespan recorded only"
    )

    #: The five heating facts that described the generator a heating_system measure replaces.
    SUPERSEDED_BY_NEW_GENERATOR: ClassVar[Tuple[str, ...]] = (
        "heating.flow_temperature_in_celsius",
        "heating.seasonal_efficiency_in_percent",
        "heating.secondary",
        "heating.cooking_range",
        "heating.installation_year",
    )

    @classmethod
    def function_for(cls, measure_id: str) -> Callable[[MeasureContext], None]:
        """Return the function that applies one measure.

        Args:
            measure_id: A catalogue id.

        Returns:
            The function; for the twelve insulation measures a closure over the layer table.

        Raises:
            KeyError: When no function is registered, which :meth:`assert_complete` rules out.
        """
        if measure_id in cls.INSULATION:
            return cls._insulation
        return cls.BY_ID[measure_id]

    @classmethod
    def assert_complete(cls) -> None:
        """Raise when the registry and the frozen catalogue table do not name the same measures.

        Raises:
            AssertionError: Naming the measures that are in one and not the other. The message
                is the whole point: a catalogue edit has to be a deliberate change here.
        """
        registered = set(cls.INSULATION) | set(cls.BY_ID)
        catalogue = set(CatalogueTable.ids())
        missing = sorted(catalogue - registered)
        extra = sorted(registered - catalogue)
        if missing or extra:
            raise AssertionError(
                f"the measure registry and the catalogue disagree: no function for {missing}, "
                f"functions for measures the catalogue does not have: {extra}"
            )

    # ------------------------------------------------------------------ envelope: insulation

    @classmethod
    def _insulation(cls, context: MeasureContext) -> None:
        """Add one insulation layer to its element, defaulting the thickness and the material."""
        measure_id = context.measure.id
        spec = cls.INSULATION[measure_id]
        material, fixed = cls._material_of(context)
        thickness, defaulted = cls._thickness_of(context, measure_id)
        layer = AddedLayer(
            element=spec.element,
            placement=spec.placement,
            thickness_in_mm=thickness,
            material=material,
            measure_id=measure_id,
            thickness_defaulted=defaulted,
            material_fixed=fixed,
        )
        context.effects.add_layer(layer)
        context.target(f"Building.config.{spec.element.value}_u_value_in_watt_per_m2_per_kelvin")
        if "air_barrier" in context.measure.options:
            context.defer(f"{measure_id}.air_barrier", context.option("air_barrier"), "air_barrier")

    @classmethod
    def _material_of(cls, context: MeasureContext) -> Tuple[Material, bool]:
        """Return the layer's material and whether it came from the fixed table."""
        measure_id = context.measure.id
        given = context.option(CatalogueTable.MATERIAL)
        if isinstance(given, Material):
            context.record(CatalogueTable.MATERIAL, ReportStatus.USED, cls.MATERIAL_NOTE)
            return given, False
        asp_id, conductivity = FixedMaterials.of(measure_id)
        context.line.status = ReportStatus.worst_of(context.line.status, ReportStatus.APPROXIMATED)
        context.line.note = (
            f"the catalogue gives {measure_id} no material option, so the translator uses "
            f"{asp_id} with lambda {conductivity:g} W/mK"
        )
        return Material(asp_id=asp_id, thermal_conductivity_w_mk=conductivity), True

    @classmethod
    def _thickness_of(cls, context: MeasureContext, measure_id: str) -> Tuple[int, bool]:
        """Return the layer's thickness and whether the translator supplied it."""
        given = context.option("thickness_in_mm")
        capped = measure_id == "cavity_wall_insulation"
        if isinstance(given, int) and not isinstance(given, bool):
            thickness = min(given, LayerDefaults.CAVITY_MAXIMUM_IN_MM) if capped else given
            if thickness != given:
                context.record(
                    "thickness_in_mm",
                    ReportStatus.APPROXIMATED,
                    f"a cavity cannot be filled deeper than it is wide; {given} mm capped at "
                    f"{LayerDefaults.CAVITY_MAXIMUM_IN_MM} mm",
                )
            else:
                context.record("thickness_in_mm", ReportStatus.USED)
            return thickness, False
        default = LayerDefaults.thickness_of(measure_id)
        if CatalogueTable.option(measure_id, "thickness_in_mm") is not None:
            context.record(
                "thickness_in_mm",
                ReportStatus.DEFAULTED,
                f"absent from the request; the translator's default for {measure_id} is {default} mm",
            )
        return default, True

    # ------------------------------------------------------------------ envelope: openings

    @classmethod
    def window_replacement(cls, context: MeasureContext) -> None:
        """Replace the windows: panes, frame, coating, and the U-value the three imply."""
        panes = int(context.option("glazing_panes"))
        coating = bool(context.option("low_emissivity_coating"))
        context.effects.set("building.window.glazing_panes", panes)
        context.effects.set("building.window.frame_material", context.option("frame_material"))
        context.effects.set("building.window.low_emissivity_coating", coating)
        context.record("glazing_panes", ReportStatus.USED)
        context.defer("window_replacement.frame_material", context.option("frame_material"), "frame_material")
        expert = context.option("u_value_in_watt_per_m2_per_kelvin")
        if isinstance(expert, (int, float)) and not isinstance(expert, bool):
            context.effects.set(HousePaths.u_value(ThermalElement.WINDOW), float(expert))
            context.record("u_value_in_watt_per_m2_per_kelvin", ReportStatus.USED)
            context.record("low_emissivity_coating", ReportStatus.USED)
        else:
            value = OpeningUValues.window(panes, coating)
            context.effects.set(HousePaths.u_value(ThermalElement.WINDOW), value)
            context.record(
                "low_emissivity_coating",
                ReportStatus.APPROXIMATED,
                f"with {panes} panes it selects the whole-window U-value {value:g} W/(m2K) from the "
                "translator's table; no expert u_value_in_watt_per_m2_per_kelvin was given",
            )
            context.line.status = ReportStatus.worst_of(context.line.status, ReportStatus.APPROXIMATED)
        context.target("Building.config.window_u_value_in_watt_per_m2_per_kelvin")

    @classmethod
    def door_replacement(cls, context: MeasureContext) -> None:
        """Replace the door: panes, frame, and the U-value the panes imply."""
        panes = int(context.option("glazing_panes"))
        context.effects.set("building.door.glazing_panes", panes)
        context.effects.set("building.door.frame_material", context.option("frame_material"))
        context.record("glazing_panes", ReportStatus.USED)
        context.defer("door_replacement.frame_material", context.option("frame_material"), "frame_material")
        expert = context.option("u_value_in_watt_per_m2_per_kelvin")
        if isinstance(expert, (int, float)) and not isinstance(expert, bool):
            context.effects.set(HousePaths.u_value(ThermalElement.DOOR), float(expert))
            context.record("u_value_in_watt_per_m2_per_kelvin", ReportStatus.USED)
        else:
            value = OpeningUValues.door(panes)
            context.effects.set(HousePaths.u_value(ThermalElement.DOOR), value)
            context.line.status = ReportStatus.worst_of(context.line.status, ReportStatus.APPROXIMATED)
            context.line.note = (
                f"no expert U-value was given, so the whole-door U-value of a {panes}-pane door, "
                f"{value:g} W/(m2K), comes from the translator's table"
            )
        context.target("Building.config.door_u_value_in_watt_per_m2_per_kelvin")

    @classmethod
    def outside_shading(cls, context: MeasureContext) -> None:
        """Fit external shading to the windows; ``Building`` has no shading parameter."""
        context.effects.set("building.window.outside_shading", True)

    @classmethod
    def thermocover_for_the_windows(cls, context: MeasureContext) -> None:
        """Fit removable night covers to the windows; ``Building`` has no parameter for them."""
        context.effects.set("building.window.thermocover", True)

    # ------------------------------------------------------------------ ventilation and shallow

    @classmethod
    def ventilation_system(cls, context: MeasureContext) -> None:
        """Install a ventilation system; HiSim reads the air-change rate from the TABULA row."""
        context.effects.set("ventilation.type_of_system", context.option("type_of_system"))
        context.defer("ventilation_system.type_of_system", context.option("type_of_system"), "type_of_system")

    @classmethod
    def shallow_air_tightness_measures(cls, context: MeasureContext) -> None:
        """Seal the envelope professionally; ``Building`` has no infiltration parameter."""
        context.effects.set("ventilation.air_tightness", "professionally_sealed")

    @classmethod
    def diy_sealing_of_air_leaks(cls, context: MeasureContext) -> None:
        """Seal the obvious draughts; ``Building`` has no infiltration parameter."""
        context.effects.set("ventilation.air_tightness", "diy_sealed")

    @classmethod
    def hot_water_tank_and_pipe_insulation(cls, context: MeasureContext) -> None:
        """Insulate the hot-water tank and its pipes; the tank's loss coefficient is halved."""
        context.effects.set("hot_water.tank_and_pipe_insulated", True)
        context.line.status = ReportStatus.APPROXIMATED
        context.line.note = (
            "the storage's heat transfer coefficient is halved; the pipe losses have no HiSim "
            "parameter and are not modelled"
        )
        context.target(
            "DHWStorage.config.heat_transfer_coefficient_in_watt_per_m2_per_kelvin"
        )

    # ------------------------------------------------------------------ heating

    @classmethod
    def heating_system(cls, context: MeasureContext) -> None:
        """Replace the heat generator, and drop the four facts that described the old one."""
        generator = context.option("type_of_system")
        context.effects.set("heating.type_of_system", generator)
        context.record("type_of_system", ReportStatus.USED)
        removed = [path for path in cls.SUPERSEDED_BY_NEW_GENERATOR if context.effects.remove(path)]
        if removed:
            context.line.note = (
                "removed " + ", ".join(f"house.{path}" for path in removed)
                + ": they described the generator this measure replaces"
            )
        context.target("the base file selected by heating.type_of_system")

    @classmethod
    def heating_installation(cls, context: MeasureContext) -> None:
        """Replace the emitters, which is what the heat distribution controller is told."""
        context.effects.set("heat_distribution.type_of_system", context.option("type_of_system"))
        context.record("type_of_system", ReportStatus.USED)
        context.target("HeatDistributionController.config.heating_system")

    @classmethod
    def air_conditioners(cls, context: MeasureContext) -> None:
        """Install air conditioning of a stated power."""
        context.effects.set("air_conditioning.power_in_watt", context.option("power_in_watt"))
        context.defer("air_conditioners.power_in_watt", context.option("power_in_watt"), "power_in_watt")

    @classmethod
    def hot_water_system(cls, context: MeasureContext) -> None:
        """Change how domestic hot water is made."""
        supply = context.option("supply")
        context.effects.set("hot_water.supply", supply)
        context.record("supply", ReportStatus.USED)
        context.target("the generator's with_domestic_hot_water_preparation")

    @classmethod
    def temperature_control_system(cls, context: MeasureContext) -> None:
        """Install a heating control; the recorded base files have no night-setback group."""
        context.effects.set("temperature_control.type_of_system", context.option("type_of_system"))
        context.defer(
            "temperature_control_system.type_of_system", context.option("type_of_system"), "type_of_system"
        )

    # ------------------------------------------------------------------ appliances and renewables

    @classmethod
    def replace_white_appliances(cls, context: MeasureContext) -> None:
        """Replace the large appliances; the precomputed profile has a fixed intensity."""
        context.effects.set("appliances.white_appliances", "new_efficient")

    @classmethod
    def photovoltaic_system(cls, context: MeasureContext) -> None:
        """Install a photovoltaic array covering a share of the roof, replacing any existing one."""
        existing = context.effects.read(HousePaths.PV_SYSTEM) or {}
        replacement: Dict[str, Any] = {
            "size_in_percent_of_roof_area": context.option("size_in_percent_of_roof_area")
        }
        for kept in ("azimuth", "tilt"):
            if isinstance(existing, Mapping) and existing.get(kept) is not None:
                replacement[kept] = existing[kept]
        context.effects.replace_block(HousePaths.PV_SYSTEM, replacement)
        context.record("size_in_percent_of_roof_area", ReportStatus.USED)
        context.target("PVSystem.config.share_of_maximum_pv_potential")

    @classmethod
    def battery_system(cls, context: MeasureContext) -> None:
        """Install a battery sized by the days of household electricity it should cover."""
        context.effects.replace_block(HousePaths.BATTERY, {"days_to_cover": context.option("days_to_cover")})
        context.record("days_to_cover", ReportStatus.APPROXIMATED)
        context.line.status = ReportStatus.APPROXIMATED
        context.line.note = (
            "the capacity is the stated days times the daily electricity of the precomputed CHR01 "
            "profile the run itself uses; the formula and its numbers are in the field's note"
        )
        context.target("Battery.config.custom_battery_capacity_generic_in_kilowatt_hour")

    @classmethod
    def solar_thermal_system(cls, context: MeasureContext) -> None:
        """Install a solar thermal collector, keeping whatever the request said about it."""
        existing = context.effects.read(HousePaths.SOLAR_THERMAL)
        block: Dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
        block["supplies"] = context.option("supplies")
        context.effects.replace_block(HousePaths.SOLAR_THERMAL, block)
        context.record("supplies", ReportStatus.USED)
        context.target("groups.solar_thermal.enabled")

    @classmethod
    def electric_vehicle(cls, context: MeasureContext) -> None:
        """Buy electric cars; a car needs a driving profile the MVP image cannot compute."""
        existing = context.effects.read(HousePaths.ELECTRIC_VEHICLES)
        block: Dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
        block["number"] = context.option("number")
        context.effects.replace_block(HousePaths.ELECTRIC_VEHICLES, block)
        context.defer("electric_vehicle.number", context.option("number"), "number")

    # ------------------------------------------------------------------ behaviour

    @classmethod
    def change_room_temperature(cls, context: MeasureContext) -> None:
        """Change the room set point, which propagates to the heat distribution controller."""
        context.effects.set(HousePaths.SET_HEATING_TEMPERATURE, context.option("new_room_temperature"))
        context.record("new_room_temperature", ReportStatus.USED)
        context.target("Building.config.set_heating_temperature_in_celsius")

    @classmethod
    def optimize_behaviour_for_self_consumption_of_pv(cls, context: MeasureContext) -> None:
        """Shift the household's loads into the sunshine; the twins have no EMS without a battery."""
        context.effects.set("occupancy.pv_self_consumption_optimised", True)

    #: catalogue id -> the function that applies it, for every measure that is not insulation.
    BY_ID: ClassVar[Dict[str, Callable[[MeasureContext], None]]] = {}


MeasureRegistry.BY_ID = {
    "window_replacement": MeasureRegistry.window_replacement,
    "door_replacement": MeasureRegistry.door_replacement,
    "outside_shading": MeasureRegistry.outside_shading,
    "thermocover_for_the_windows": MeasureRegistry.thermocover_for_the_windows,
    "ventilation_system": MeasureRegistry.ventilation_system,
    "shallow_air_tightness_measures": MeasureRegistry.shallow_air_tightness_measures,
    "diy_sealing_of_air_leaks": MeasureRegistry.diy_sealing_of_air_leaks,
    "hot_water_tank_and_pipe_insulation": MeasureRegistry.hot_water_tank_and_pipe_insulation,
    "heating_system": MeasureRegistry.heating_system,
    "heating_installation": MeasureRegistry.heating_installation,
    "air_conditioners": MeasureRegistry.air_conditioners,
    "hot_water_system": MeasureRegistry.hot_water_system,
    "temperature_control_system": MeasureRegistry.temperature_control_system,
    "replace_white_appliances": MeasureRegistry.replace_white_appliances,
    "photovoltaic_system": MeasureRegistry.photovoltaic_system,
    "battery_system": MeasureRegistry.battery_system,
    "solar_thermal_system": MeasureRegistry.solar_thermal_system,
    "electric_vehicle": MeasureRegistry.electric_vehicle,
    "change_room_temperature": MeasureRegistry.change_room_temperature,
    "optimize_behaviour_for_self_consumption_of_pv":
        MeasureRegistry.optimize_behaviour_for_self_consumption_of_pv,
}


@dataclass(frozen=True)
class AppliedPackage:
    """One house after its package, with everything the later steps and the report need.

    Args:
        house: The renovated house as plain dictionaries -- the request's own shape, so the
            translator reads it exactly as it reads an unrenovated one.
        measures: One line per measure, in package order.
        layers: Every insulation layer the package added, in order, for the embodied-carbon
            figure of ``result.json``.
        written_paths: Every house path a measure wrote, so the report can say which values are
            the package's rather than the request's.
        removed_paths: Every house path a measure removed.
    """

    house: Dict[str, Any]
    measures: Tuple[MeasureLine, ...]
    layers: Tuple[AddedLayer, ...]
    written_paths: Tuple[str, ...]
    removed_paths: Tuple[str, ...]

    def element_note(self, element: ThermalElement) -> Optional[str]:
        """Return the U-value arithmetic note for one element, or ``None`` when it got no layer."""
        return self._notes.get(element)

    #: Filled in by :func:`apply`; a mapping rather than a method because the arithmetic is done
    #: while the layers are added and the composer is gone by the time the report is written.
    _notes: Mapping[ThermalElement, str] = field(default_factory=dict)


class MeasureStatusRules:
    """How one measure's own status follows from its options and from the whitelist.

    The rule is §4.2's: a measure is ``used`` when it is mapped and every ``everyone`` option is
    ``used``; ``approximated`` when it is mapped and an ``everyone`` option is worse than that;
    and ``not_implemented_yet`` only when the measure *itself* is on the list. ``experts``
    options and individual values carry their own status and never change the measure's, which
    is what keeps ``heating_system`` ``used`` although four of its sixteen values are not
    implemented.
    """

    @classmethod
    def of(cls, line: MeasureLine, base: ReportStatus) -> ReportStatus:
        """Return the measure's status from its option lines and the status the function set.

        Args:
            line: The measure's report entry, with every option line already recorded.
            base: The status the measure function set for itself, ``used`` unless it declared an
                approximation of its own.

        Returns:
            The measure's status, never ``defaulted`` and never ``not_implemented_yet`` (only a
            whitelist match at measure level produces that, and the caller applies it after).
        """
        status = base
        for option in line.options:
            spec = CatalogueTable.option(line.id, option.name)
            if spec is None or spec.access_level.value != "everyone":
                continue
            if option.status is not ReportStatus.USED:
                status = ReportStatus.worst_of(status, ReportStatus.APPROXIMATED)
        return status


def apply(house: Mapping[str, Any], measures: Sequence[Measure], whitelist: Whitelist) -> AppliedPackage:
    """Apply one package to one house and say what every measure and option came to.

    Args:
        house: The ``house`` block of a validated request. It is deep-copied and never mutated.
        measures: The package, in the order it is applied.
        whitelist: The parsed ``not_implemented_yet.yaml``, asked once at the end about every
            item no measure function could map.

    Returns:
        The renovated house and the measure half of the mapping report.

    Raises:
        TranslatorError: When a measure, an option or a value is neither mapped nor listed. That
            is a fault in this package, not in the request: exit 3, never exit 2.
    """
    MeasureRegistry.assert_complete()
    working: Dict[str, Any] = copy.deepcopy(dict(house))
    effects = Effects(working)
    contexts: List[MeasureContext] = []
    for measure in measures:
        line = MeasureLine(id=measure.id)
        context = MeasureContext(measure=measure, effects=effects, line=line)
        MeasureRegistry.function_for(measure.id)(context)
        _record_unseen_options(context)
        line.status = MeasureStatusRules.of(line, line.status)
        contexts.append(context)
    _resolve(contexts, effects.house, whitelist)
    notes = {
        element: effects.note_for(element)
        for element in {layer.element for layer in effects.layers()}
    }
    return AppliedPackage(
        house=effects.house,
        measures=tuple(context.line for context in contexts),
        layers=effects.layers(),
        written_paths=effects.written_paths(),
        removed_paths=effects.removed_paths(),
        _notes=notes,
    )


def _record_unseen_options(context: MeasureContext) -> None:
    """Add a line for every option the request carried that the measure function said nothing about.

    ``installation_year`` is the expected case -- five measures have it and none of them has a
    HiSim parameter for it -- but the rule is general: an option nobody recorded is deferred to
    the whitelist, so forgetting one in a measure function fails the build instead of dropping a
    value silently.
    """
    recorded = {option.name for option in context.line.options}
    for name in context.measure.options:
        if name in recorded:
            continue
        context.defer(f"{context.measure.id}.{name}", context.measure.options[name], name)


def _resolve(contexts: Sequence[MeasureContext], house: Mapping[str, Any], whitelist: Whitelist) -> None:
    """Ask the whitelist about everything the measures could not map, against the renovated house.

    Three questions per measure, in this order. Every deferred option gets its note or fails the
    build. Every measure that wrote no target, and every measure the list conditions on the
    renovated house, is asked at measure level. Every option value is asked about last, because
    an unimplemented value narrows one run's report line without changing what the measure is.
    """
    for context in contexts:
        for unmapped, line in context.unmapped:
            line.note = whitelist.require(unmapped, house).note
        entry = whitelist.match(Unmapped.measure(context.measure.id), house)
        if entry is not None:
            context.line.status = ReportStatus.NOT_IMPLEMENTED_YET
            context.line.note = entry.note
        elif not context.line.targets:
            raise _no_entry(context.measure.id)
        _resolve_values(context, house, whitelist)


def _resolve_values(context: MeasureContext, house: Mapping[str, Any], whitelist: Whitelist) -> None:
    """Narrow an option line to ``not_implemented_yet`` when the value it carried is listed."""
    for line in context.line.options:
        if line.status is ReportStatus.NOT_IMPLEMENTED_YET:
            continue
        value = context.measure.options.get(line.name)
        if value is None or isinstance(value, Material):
            continue
        entry: Optional[WhitelistEntry] = whitelist.match(
            Unmapped.measure(f"{context.measure.id}.{line.name}={value}"), house
        )
        if entry is not None:
            line.status = ReportStatus.NOT_IMPLEMENTED_YET
            line.note = entry.note


def _no_entry(measure_id: str) -> Exception:
    """Return the translator error for a measure that wrote nothing and is not on the list."""
    from hisim.renovisor.whitelist import TranslatorError

    return TranslatorError(
        f"the measure '{measure_id}' writes no HiSim target and not_implemented_yet.yaml does not list it",
        "Either give the measure a target, or add an entry with the sentence a user should read.",
    )
