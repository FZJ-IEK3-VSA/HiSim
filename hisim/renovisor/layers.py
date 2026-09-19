"""The insulation layers a package added, with the element area each of them covers.

One figure of the result payload is an integral over this set of objects: the embodied carbon of
a renovation (decision Q22). Under rule 5 of the contract the request carries the material's
properties rather than its id, so the footprint travels on the layer itself and no database is
read here::

    layers = EnvelopeLayers.of(applied.layers, areas)
    layers.total_embodied_co2_in_kg()      # 3 614.5

Where an area comes from: the realized energy-system record's ``Building`` config, which is the
building the simulation actually ran (``facade_area_in_m2`` and its four siblings). Those config
values are the ones the translator wrote from the request, so the fallback when the record
carries ``null`` -- which happens when the request did not state that element's area and the
``Building`` component derived it from its TABULA row instead -- is the request itself, and when
neither has a number the layer is *unresolved* rather than assumed. An unresolved layer makes
the field that needs it absent, with the reason named, because half a building's insulation is
not a smaller answer to "how much carbon did this cost", it is a wrong one.
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.renovisor.apply import AddedLayer
from hisim.renovisor.request import House, Material
from hisim.renovisor.vocabulary import ThermalElement


class ElementAreas:
    """The area of each envelope element, resolved from the run and from the request.

    The five elements of the request's ``house.building`` are the five envelope fields of
    :class:`hisim.components.building.config.BuildingConfig`, named identically apart from the
    element's own case: ``facade`` is ``facade_area_in_m2``. That correspondence is what
    :meth:`config_field` states, and it is the whole mapping -- there is no table of exceptions.

    Args:
        areas: Area in square metres per element, for every element a number was found for.
        sources: Where each of those numbers came from, as a phrase for the payload's ``source``.
    """

    #: The suffix a ``BuildingConfig`` envelope area field carries.
    AREA_SUFFIX: ClassVar[str] = "_area_in_m2"

    #: The component of the energy-system file whose config carries the areas.
    BUILDING_COMPONENT: ClassVar[str] = "Building"

    #: The request block holding the same five areas.
    REQUEST_BLOCK: ClassVar[str] = "house.building"

    def __init__(self, areas: Mapping[ThermalElement, float], sources: Mapping[ThermalElement, str]) -> None:
        """Store the resolved areas and the phrase naming where each came from."""
        self._areas = dict(areas)
        self._sources = dict(sources)

    @classmethod
    def config_field(cls, element: ThermalElement) -> str:
        """Return the ``BuildingConfig`` field holding one element's area."""
        return f"{element.value}{cls.AREA_SUFFIX}"

    @classmethod
    def request_path(cls, element: ThermalElement) -> str:
        """Return the request path holding one element's area."""
        return f"{cls.REQUEST_BLOCK}.{element.value}.area_in_m2"

    @classmethod
    def resolve(cls, building_config: Mapping[str, Any], house: House) -> "ElementAreas":
        """Resolve every element's area from the realized record, falling back to the request.

        Args:
            building_config: The ``config`` block of the realized record's ``Building``
                component; an empty mapping when the record has no such component.
            house: The renovated house, read for the areas the request stated.

        Returns:
            The resolved areas. An element neither source carries a number for is absent from
            the result, which :meth:`area_of` reports as ``None``.
        """
        areas: Dict[ThermalElement, float] = {}
        sources: Dict[ThermalElement, str] = {}
        for element in ThermalElement:
            field_name = cls.config_field(element)
            from_record = building_config.get(field_name)
            if isinstance(from_record, (int, float)) and not isinstance(from_record, bool):
                areas[element] = float(from_record)
                sources[element] = f"realized.energy_system.yaml: Building.config.{field_name}"
                continue
            from_request = house.element(element.value).area_in_m2
            if from_request is not None:
                areas[element] = float(from_request)
                sources[element] = f"the request: {cls.request_path(element)}"
        return cls(areas=areas, sources=sources)

    def area_of(self, element: ThermalElement) -> Optional[float]:
        """Return one element's area in square metres, or ``None`` when neither source had it."""
        return self._areas.get(element)

    def source_of(self, element: ThermalElement) -> str:
        """Return the phrase naming where one element's area came from, or a stated absence."""
        return self._sources.get(element, f"no area for {element.value}")


@dataclass(frozen=True)
class EnvelopeLayer:
    """One insulation layer of a package, with everything a per-area figure needs.

    Args:
        element: The thermal element the layer sits on.
        material: The material's properties as the request carried them.
        thickness_in_mm: How thick the layer is, as the measure resolved it.
        area_in_m2: The element's area, or ``None`` when neither the realized record nor the
            request carries one.
        area_source: Where that area came from, as a phrase for the payload's ``source``.
        measure_id: The catalogue measure that added the layer.
    """

    element: ThermalElement
    material: Material
    thickness_in_mm: int
    area_in_m2: Optional[float]
    area_source: str
    measure_id: str

    def volume_in_m3(self) -> Optional[float]:
        """Return the installed volume in cubic metres, or ``None`` when the area is unknown."""
        if self.area_in_m2 is None:
            return None
        return self.thickness_in_mm / 1000.0 * self.area_in_m2

    def embodied_co2_in_kg(self) -> Optional[float]:
        """Return the layer's embodied carbon, or ``None`` when a factor is missing.

        The request's material carries ``co2_footprint_a1_a3_c3_c4_kg_m2``, an EN 15804+A2
        figure **per square metre** at the thickness for U = 0.3, so the product is the
        footprint times the element's area and not times the installed volume.
        """
        if self.area_in_m2 is None or self.material.co2_footprint_a1_a3_c3_c4_kg_m2 is None:
            return None
        return self.material.co2_footprint_a1_a3_c3_c4_kg_m2 * self.area_in_m2

    def describe(self) -> str:
        """Return one phrase naming the layer, for a ``source`` string.

        Returns:
            E.g. ``"facade polystyrene_eps_rigid_board 120 mm x 173 m2 (external_insulation)"``.
        """
        area = "unknown area" if self.area_in_m2 is None else f"{self.area_in_m2:g} m2"
        return (
            f"{self.element.value} {self.material.asp_id} {self.thickness_in_mm} mm x {area} "
            f"({self.measure_id})"
        )


class EnvelopeLayers:
    """Every insulation layer of one package, resolved onto areas.

    The collection exists so that "this package has no envelope measure" is one question with
    one answer: an empty collection, which decision R8 turns into an absent field rather than a
    zero.

    Args:
        layers: The layers, in the order the measures added them.
    """

    def __init__(self, layers: Sequence[EnvelopeLayer]) -> None:
        """Store the layers in the order they were added."""
        self._layers = tuple(layers)

    @classmethod
    def of(cls, additions: Sequence[AddedLayer], areas: ElementAreas) -> "EnvelopeLayers":
        """Build the collection from the package's recorded layers.

        Args:
            additions: The layers ``apply`` produced, in order.
            areas: The resolved element areas.

        Returns:
            One :class:`EnvelopeLayer` per addition, each carrying the area of its element.
        """
        return cls(
            [
                EnvelopeLayer(
                    element=addition.element,
                    material=addition.material,
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

    def without_footprint(self) -> Tuple[EnvelopeLayer, ...]:
        """Return the layers whose material carries no CO2 footprint."""
        return tuple(
            layer for layer in self._layers if layer.material.co2_footprint_a1_a3_c3_c4_kg_m2 is None
        )

    def total_volume_in_m3(self) -> Optional[float]:
        """Return the installed volume over every layer, or ``None`` when one is unresolved."""
        total = 0.0
        for layer in self._layers:
            volume = layer.volume_in_m3()
            if volume is None:
                return None
            total += volume
        return total

    def total_embodied_co2_in_kg(self) -> Optional[float]:
        """Return the embodied carbon over every layer, or ``None`` when one cannot be computed.

        A partial sum over some of a building's insulation would read as the whole building's
        carbon, so one unresolved layer makes the whole figure absent.
        """
        total = 0.0
        for layer in self._layers:
            contribution = layer.embodied_co2_in_kg()
            if contribution is None:
                return None
            total += contribution
        return total

    def describe_terms(self) -> str:
        """Return the arithmetic of the embodied-carbon sum, term by term."""
        return "; ".join(
            f"{layer.describe()} = {layer.area_in_m2:g} m2 x "
            f"{layer.material.co2_footprint_a1_a3_c3_c4_kg_m2:g} kg/m2"
            for layer in self._layers
            if layer.area_in_m2 is not None and layer.material.co2_footprint_a1_a3_c3_c4_kg_m2 is not None
        )
