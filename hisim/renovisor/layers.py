"""The insulation layers a package added, with the element area each of them covers.

One figure of the result payload is an integral over this set of objects: the embodied carbon of
a renovation (decision Q22). Under rule 5 of the contract the request carries the material's
properties rather than its id, so the footprint travels on the layer itself and no database is
read here::

    layers = EnvelopeLayers.of(applied.layers, areas)
    layers.total_embodied_co2_in_kg()      # 3 614.5

Where an area comes from: the area the ``Building`` of the realized energy-system record
simulates (:class:`SimulatedEnvelope`, the one rule the economic context sizes its envelope cost
subjects with too). That is the config's own ``facade_area_in_m2`` and its four siblings where
the translator wrote the request's area, and where it wrote none -- the request did not state
that element's area -- the TABULA row's area scaled to the conditioned floor area, read off the
Building's own computation (renovisorissues #61, hisim-2pw8). Only when the record cannot be
read do the areas come from the request alone. An element whose area is zero leaves its layer
*unresolved* rather than assumed. An unresolved layer makes the field that needs it absent, with
the reason named, because half a building's insulation is not a smaller answer to "how much
carbon did this cost", it is a wrong one.
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple

from hisim.renovisor.apply import AddedLayer
from hisim.renovisor.request import House, Material
from hisim.renovisor.vocabulary import ThermalElement


class SimulatedEnvelope:
    """The envelope areas the ``Building`` simulates, from a mapping of its configuration fields.

    The one rule for an element's area (renovisorissues #61): the ``<element>_area_in_m2`` the
    config carries, which is the request's stated area the translator wrote, and where the config
    carries none the TABULA row's area scaled to the conditioned floor area, as the Building's own
    :class:`~hisim.components.building.BuildingInformation` computes it -- never re-derived. The
    economic context sizes its envelope cost subjects with it, and the result payload its layers.

    Args:
        building_config: The ``Building`` configuration fields: the realized record's ``config``
            block, or the translator's config merged with its ``for_tabula_code`` arguments.
            Either carries the TABULA archetype under ``building_code``.
        heating_reference_temperature_in_celsius: The outside design temperature, which decides
            the design heat load only; ``None`` keeps whatever the mapping carries.
    """

    #: The suffix of a ``BuildingConfig`` envelope area field.
    AREA_SUFFIX: ClassVar[str] = "_area_in_m2"

    #: The field naming the TABULA archetype, a ``for_tabula_code`` constructor argument.
    BUILDING_CODE_KEY: ClassVar[str] = "building_code"

    #: Its dwelling-unit count, the second constructor argument.
    APARTMENTS_KEY: ClassVar[str] = "number_of_apartments"

    #: The conditioned floor area every TABULA area is scaled to, the third.
    FLOOR_AREA_KEY: ClassVar[str] = "absolute_conditioned_floor_area_in_m2"

    #: The three constructor arguments, which the mapping must not overwrite afterwards.
    CONSTRUCTOR_KEYS: ClassVar[Tuple[str, ...]] = (BUILDING_CODE_KEY, APARTMENTS_KEY, FLOOR_AREA_KEY)

    #: The name the rebuilt ``BuildingConfig`` carries. It never reaches a file.
    BUILDING_COMPONENT: ClassVar[str] = "Building"

    def __init__(
        self, building_config: Mapping[str, Any], heating_reference_temperature_in_celsius: Optional[float] = None
    ) -> None:
        """Store the configuration; the Building is computed on first use."""
        self._config = dict(building_config)
        self._temperature = heating_reference_temperature_in_celsius
        self._information: Any = None

    @classmethod
    def config_field(cls, element: ThermalElement) -> str:
        """Return the ``BuildingConfig`` field holding one element's area."""
        return f"{element.value}{cls.AREA_SUFFIX}"

    def building_code(self) -> Optional[str]:
        """Return the TABULA archetype the configuration names, or ``None``."""
        code = self._config.get(self.BUILDING_CODE_KEY)
        return code if isinstance(code, str) and code else None

    def information(self) -> Any:
        """Return the ``BuildingInformation`` the run's ``Building`` computes, built once.

        The configuration is rebuilt the way the run builds it: ``for_tabula_code`` with the
        archetype, the apartment count and the floor area, then every other field of the mapping
        on top, then the design temperature when one was given.

        Raises:
            ValueError: If the mapping names no archetype; a caller checks :meth:`building_code`.
        """
        if self._information is not None:
            return self._information
        code = self.building_code()
        if code is None:
            raise ValueError(f"the Building configuration names no {self.BUILDING_CODE_KEY}")
        # Function-local: the building package pulls the TABULA table and pandas in with it, and
        # the modules importing this one are translation tables a test drives without them.
        from hisim.components.building import (  # pylint: disable=import-outside-toplevel
            BuildingConfig,
            BuildingInformation,
        )

        config = BuildingConfig.for_tabula_code(
            name=self.BUILDING_COMPONENT,
            building_code=code,
            number_of_apartments=positive_number(self._config.get(self.APARTMENTS_KEY)),
            absolute_conditioned_floor_area_in_m2=positive_number(self._config.get(self.FLOOR_AREA_KEY)),
        )
        for name, value in self._config.items():
            if name not in self.CONSTRUCTOR_KEYS and hasattr(config, name):
                setattr(config, name, value)
        if self._temperature is not None:
            config.heating_reference_temperature_in_celsius = float(self._temperature)
        self._information = BuildingInformation(config)
        return self._information

    def stated_area(self, element: ThermalElement) -> Optional[float]:
        """Return the area the configuration itself carries for one element, or ``None``."""
        return positive_number(self._config.get(self.config_field(element)))

    def area_of(self, element: ThermalElement) -> Optional[float]:
        """Return one element's area as the Building simulates it.

        Returns:
            The stated area, else the Building's scaled TABULA area; ``None`` only when that is
            zero -- a TABULA row without windows, say.
        """
        stated = self.stated_area(element)
        if stated is not None:
            return stated
        return positive_number(getattr(self.information(), self.config_field(element)))


def positive_number(value: Any) -> Optional[float]:
    """Return a raw request or config value as a positive float, or ``None``.

    A JSON value is a number, is not a boolean (``True`` is an ``int`` in Python and would
    otherwise pass as a size of one), and is greater than zero, because none of the quantities
    read with it -- an area, a capacity, a peak power -- is meaningfully zero or negative.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


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
    AREA_SUFFIX: ClassVar[str] = SimulatedEnvelope.AREA_SUFFIX

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
        """Resolve every element's area as the realized record's Building simulates it.

        The rule is :class:`SimulatedEnvelope`'s: the config's own area, else the Building's
        scaled TABULA area. Only a record without a ``Building`` archetype -- one that could not
        be read -- falls back to the areas the request states.

        Args:
            building_config: The ``config`` block of the realized record's ``Building``
                component; an empty mapping when the record has no such component.
            house: The renovated house, read for the areas the request stated.

        Returns:
            The resolved areas. An element without one (a zero TABULA area, or no record and no
            stated area) is absent from the result, which :meth:`area_of` reports as ``None``.
        """
        areas: Dict[ThermalElement, float] = {}
        sources: Dict[ThermalElement, str] = {}
        envelope = SimulatedEnvelope(building_config)
        code = envelope.building_code()
        for element in ThermalElement:
            field_name = cls.config_field(element)
            stated = envelope.stated_area(element)
            if stated is not None or code is not None:
                area = envelope.area_of(element)
                if area is None:
                    continue
                areas[element] = area
                sources[element] = (
                    f"realized.energy_system.yaml: Building.config.{field_name}"
                    if stated is not None
                    else f"realized.energy_system.yaml: the Building's {field_name}, the TABULA row "
                    f"{code}'s area scaled to the conditioned floor area (the request states none)"
                )
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
        area_in_m2: The element's area as the Building simulates it (:class:`ElementAreas`), or
            ``None`` when it has none.
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
            E.g. ``"facade eps_rigid_board 120 mm x 173 m2 (external_insulation)"``.
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
