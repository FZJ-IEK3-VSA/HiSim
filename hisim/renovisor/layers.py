"""The insulation layers a package added, with the area and the volume each of them covers.

Two figures of the result payload are integrals over the same set of objects: the embodied carbon
of a renovation (kg CO2 per cubic metre of material times the volume installed, decision Q22) and
the material half of its investment cost (euro per cubic metre times the same volume, decisions
Q8/Q9). Both need the layers the package added, the element area each sits on, and the product of
the two -- so both read this module rather than each deriving the geometry for itself, and a
disagreement between the carbon figure and the cost figure about how many cubic metres of wood
fibre went onto the roof is impossible by construction::

    layers = EnvelopeLayers.of(result.insulation_layers, areas)
    layers.total_volume_in_m3()      # 21.7

Where an area comes from: the realized energy-system record's ``Building`` config, which is the
building the simulation actually ran (``facade_area_in_m2`` and its four siblings). Those config
values are the ones the translation layer wrote from the home inventory, so the fallback when the
record carries ``null`` -- which happens when the inventory did not state that element's area and
the ``Building`` component derived it from its TABULA row instead -- is the inventory itself, and
when neither has a number the layer is *unresolved* rather than assumed. An unresolved layer
makes the field that needs it absent, with the reason named, because half a building's insulation
is not a smaller answer to "how much carbon did this cost", it is a wrong one.
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.renovisor.effects import AddThermalResistance
from hisim.renovisor.vocabulary import ThermalElement


class ElementAreas:
    """The area of each envelope element, resolved from the run and the inventory.

    The five thermal elements of the contract's ``envelope_details`` block are the five envelope
    fields of :class:`hisim.components.building.config.BuildingConfig`, named identically apart
    from the element's own case: ``FACADE`` is ``facade_area_in_m2``. That correspondence is what
    :meth:`config_field` states, and it is the whole mapping -- there is no table of exceptions.

    Args:
        areas: Area in square metres per element, for every element a number was found for.
        sources: Where each of those numbers came from, as a phrase for the payload's ``source``.
    """

    #: The suffix a ``BuildingConfig`` envelope area field carries.
    AREA_SUFFIX: ClassVar[str] = "_area_in_m2"

    #: The component of the energy-system file whose config carries the areas.
    BUILDING_COMPONENT: ClassVar[str] = "Building"

    #: The inventory block holding the same five areas.
    INVENTORY_BLOCK: ClassVar[str] = "building_config.envelope_details"

    def __init__(self, areas: Mapping[ThermalElement, float], sources: Mapping[ThermalElement, str]) -> None:
        """Store the resolved areas and the phrase naming where each came from."""
        self._areas = dict(areas)
        self._sources = dict(sources)

    @classmethod
    def config_field(cls, element: ThermalElement) -> str:
        """Return the ``BuildingConfig`` field holding one element's area.

        Args:
            element: One of the five thermal elements.

        Returns:
            E.g. ``"facade_area_in_m2"`` for :attr:`ThermalElement.FACADE`.
        """
        return f"{element.value.lower()}{cls.AREA_SUFFIX}"

    @classmethod
    def inventory_path(cls, element: ThermalElement) -> str:
        """Return the inventory path holding one element's area.

        Args:
            element: One of the five thermal elements.

        Returns:
            E.g. ``"building_config.envelope_details.facade_area_in_m2"``.
        """
        return f"{cls.INVENTORY_BLOCK}.{cls.config_field(element)}"

    @classmethod
    def resolve(cls, building_config: Mapping[str, Any], inventory: Any) -> "ElementAreas":
        """Resolve every element's area from the realized record, falling back to the inventory.

        Args:
            building_config: The ``config`` block of the realized record's ``Building`` component;
                an empty mapping when the record has no such component.
            inventory: The post-measure :class:`hisim.renovisor.inventory.Inventory`, read through
                its own path accessor.

        Returns:
            The resolved areas. An element neither source carries a number for is simply absent
            from the result, which :meth:`area_of` reports as ``None``.
        """
        areas: Dict[ThermalElement, float] = {}
        sources: Dict[ThermalElement, str] = {}
        for element in ThermalElement:
            field = cls.config_field(element)
            from_record = building_config.get(field)
            if isinstance(from_record, (int, float)) and not isinstance(from_record, bool):
                areas[element] = float(from_record)
                sources[element] = f"realized.energy_system.yaml: Building.config.{field}"
                continue
            from_inventory = inventory.get(cls.inventory_path(element))
            if isinstance(from_inventory, (int, float)) and not isinstance(from_inventory, bool):
                areas[element] = float(from_inventory)
                sources[element] = f"home_inventory.json: {cls.inventory_path(element)}"
        return cls(areas=areas, sources=sources)

    def area_of(self, element: ThermalElement) -> Optional[float]:
        """Return one element's area in square metres, or ``None`` when neither source had it."""
        return self._areas.get(element)

    def source_of(self, element: ThermalElement) -> str:
        """Return the phrase naming where one element's area came from, or a stated absence."""
        return self._sources.get(element, f"no area for {element.value}")


@dataclass(frozen=True)
class EnvelopeLayer:
    """One insulation layer of a package, with everything a per-volume figure needs.

    Args:
        element: The thermal element the layer sits on.
        material_asp_id: The material's database id, the key of the insulation-material table.
        thickness_in_mm: How thick the layer is, as the measure resolved it -- either the request's
            own value or the target-driven default of decision Q11.
        area_in_m2: The element's area, or ``None`` when neither the realized record nor the
            inventory carries one.
        area_source: Where that area came from, as a phrase for the payload's ``source``.
        measure_id: The catalogue measure that added the layer.
    """

    element: ThermalElement
    material_asp_id: str
    thickness_in_mm: int
    area_in_m2: Optional[float]
    area_source: str
    measure_id: str

    def volume_in_m3(self) -> Optional[float]:
        """Return the installed volume in cubic metres, or ``None`` when the area is unknown."""
        if self.area_in_m2 is None:
            return None
        return self.thickness_in_mm / 1000.0 * self.area_in_m2

    def describe(self) -> str:
        """Return one phrase naming the layer, for a ``source`` string.

        Returns:
            E.g. ``"FACADE polystyrene_eps_rigid_board 120 mm x 173 m2 (EXTERNAL_INSULATION)"``.
        """
        area = "unknown area" if self.area_in_m2 is None else f"{self.area_in_m2:g} m2"
        return (
            f"{self.element.value} {self.material_asp_id} {self.thickness_in_mm} mm x {area} "
            f"({self.measure_id})"
        )


class EnvelopeLayers:
    """Every insulation layer of one package, resolved onto areas.

    The collection exists so that the two per-volume figures of the payload agree by construction
    and so that "this package has no envelope measure" is one question with one answer: an empty
    collection, which decision R8 turns into an absent field rather than a zero.

    Args:
        layers: The layers, in the order the measures added them.
    """

    def __init__(self, layers: Sequence[EnvelopeLayer]) -> None:
        """Store the layers in the order they were added."""
        self._layers = tuple(layers)

    @classmethod
    def of(
        cls, additions: Sequence[AddThermalResistance], areas: ElementAreas
    ) -> "EnvelopeLayers":
        """Build the collection from the package's recorded insulation effects.

        Args:
            additions: The ``AddThermalResistance`` effects the package produced, in order.
            areas: The resolved element areas.

        Returns:
            One :class:`EnvelopeLayer` per effect, each carrying the area of its element.
        """
        return cls(
            [
                EnvelopeLayer(
                    element=addition.element,
                    material_asp_id=addition.material_asp_id,
                    thickness_in_mm=addition.thickness_in_mm,
                    area_in_m2=areas.area_of(addition.element),
                    area_source=areas.source_of(addition.element),
                    measure_id=addition.measure_id,
                )
                for addition in additions
            ]
        )

    def all(self) -> Tuple[EnvelopeLayer, ...]:
        """Return every layer, in the order the measures added them."""
        return self._layers

    def is_empty(self) -> bool:
        """Return whether the package added no insulation layer at all."""
        return not self._layers

    def unresolved(self) -> Tuple[EnvelopeLayer, ...]:
        """Return the layers whose element area neither source supplied."""
        return tuple(layer for layer in self._layers if layer.area_in_m2 is None)

    def total_volume_in_m3(self) -> Optional[float]:
        """Return the installed volume over every layer, or ``None`` when one is unresolved.

        Returns:
            The sum of the layers' volumes in cubic metres; ``None`` when any layer has no area,
            because a partial total would read as a complete one.
        """
        total = 0.0
        for layer in self._layers:
            volume = layer.volume_in_m3()
            if volume is None:
                return None
            total += volume
        return total
