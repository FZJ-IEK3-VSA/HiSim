"""Configuration dataclass for the Building component.

Holds ``BuildingConfig`` with its named default preset, its TABULA constructor and the sizing
facts it contributes to every other component of a scenario. The physics behind those facts is
``BuildingInformation`` in ``information.py``, which the contribution calls.
"""

# pylint: disable=cyclic-import
# (the only backward edges are runtime-local imports of Building and BuildingInformation;
# module import order is acyclic)

from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple

from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.config import (
    ComponentID,
    ConfigBase,
    FactContribution,
    OwnFields,
    Sizable,
    Size,
    SizingContext,
    SizingLaw,
    constructor,
    law,
    preset,
    sized_field,
)


def _technical_room_location_of_the_tabula_row(ctx: SizingContext, own: OwnFields) -> lt.InstallationLocation:
    """Derive where the building's technical room is from the cellar code of its TABULA row.

    The law behind the ``technical_room_location`` field of :class:`BuildingConfig`: it reads the row the sibling
    field ``building_code`` names, takes its cellar code (``Code_CellarCond``) and maps it by
    :attr:`BuildingInformation.TECHNICAL_ROOM_LOCATION_BY_CELLAR_CONDITION`. For example, the row
    ``DE.N.SFH.09.Gen.ReEx.001.002`` (cellar code ``N``, an unheated cellar) gives an unheated space, and
    ``DE.N.SFH.11.Gen.ReEx.001.002`` (``-``, no cellar) the heated zone. The house's own row is the one source HiSim
    has about where its technical room is; a construction year would only guess it.

    Args:
        ctx: The sizing facts of the surrounding system; unused, the row is the building's own.
        own: The sibling fields of the configuration being resolved, for ``building_code``.

    Returns:
        The heated zone or an unheated space.

    Raises:
        ValueError: If no TABULA row has the building code, or its cellar code is unknown.
    """
    del ctx
    # Imported here because information.py imports this module; the call is long after both are loaded.
    from hisim.components.building.information import (  # pylint: disable=import-outside-toplevel
        BuildingInformation,
    )

    cellar_code = BuildingInformation.cellar_code_of(own.value_of("building_code"))
    return BuildingInformation.technical_room_location_of_cellar_code(cellar_code)


@dataclass_json
@dataclass
class ConfiguredWindow:
    """One window of a building that states its windows itself instead of taking the TABULA row's.

    A window is its area, its orientation and four optics factors, which is exactly what the
    solar-gains producer takes per window (``solar_gains.WindowGeometry``). The TABULA row states
    one area per direction (south, east, north, west, horizontal) at a tilt of 90° or 0° and one
    set of optics factors for all of them; a configured window may have any tilt and azimuth and
    its own factors. An unset factor takes the row's value, as every unset field of
    :class:`BuildingConfig` does.
    """

    #: The window's area in m², the whole of it: the frame fraction below takes its share.
    area_in_m2: float
    #: Azimuth in degrees, 180 being south, 90 east, 270 west; ``None`` for a horizontal window,
    #: which has no azimuth, and for no other.
    azimuth_angle_in_degrees: Optional[float]
    #: Tilt against the horizontal in degrees: 90 for a facade window, 0 for a roof light.
    tilt_angle_in_degrees: float
    #: TABULA ``g_gl_n``: the total solar energy transmittance for perpendicular radiation.
    glass_solar_transmittance: Optional[float] = None
    #: TABULA ``F_f``: the fraction of the window area taken up by the frame.
    frame_area_fraction_reduction_factor: Optional[float] = None
    #: TABULA ``F_w``: the correction for radiation that does not arrive perpendicularly.
    nonperpendicular_reduction_factor: Optional[float] = None
    #: TABULA ``F_sh_vert``: the correction for external shading.
    external_shading_vertical_reduction_factor: Optional[float] = None

    def __post_init__(self) -> None:
        """Refuse a negative area and an azimuth-less window that is not horizontal.

        Raises:
            ValueError: for a negative area, or ``azimuth_angle_in_degrees`` ``None`` at a tilt
                other than 0.
        """
        if self.area_in_m2 < 0:
            raise ValueError(f"A window cannot have a negative area; this one states {self.area_in_m2} m2.")
        if self.azimuth_angle_in_degrees is None and self.tilt_angle_in_degrees != 0:
            raise ValueError(
                f"A window without an azimuth is horizontal, but this one is tilted by {self.tilt_angle_in_degrees} " "degrees; state its azimuth (180 is south)."
            )


@dataclass_json
@dataclass
class BuildingConfig(ConfigBase):
    """Configuration of the Building class.

    The named default variant is :meth:`preset_german_single_family_home`, and any other
    building comes from :meth:`for_tabula_code`. The building is the *source* of the sizing
    facts every other component sizes against (see :attr:`SIZING_CONTRIBUTIONS`) and therefore
    has few sizable fields of its own: the two the weather provides — the identity of the
    weather it is computed with and the outside design temperature it is computed against — and
    the technical room's location, which its own TABULA row decides. Its presets are plain concrete
    archetypes, and a setup that deviates from one — a different TABULA code, an explicit envelope
    U-value, a measured maximum thermal demand — takes the preset and assigns the field.
    """

    MAIN_CLASS = "hisim.components.building.building.Building"

    #: Sizing law of the technical room's location: :func:`_technical_room_location_of_the_tabula_row`, the cellar
    #: code of the row the sibling ``building_code`` names, mapped to the heated zone or an unheated space. Named as
    #: a ClassVar so that the field declaration reads as one line.
    TECHNICAL_ROOM_LOCATION_LAW: ClassVar[SizingLaw] = law(
        _technical_room_location_of_the_tabula_row,
        reads=(),
        fields=("building_code",),
        description=(
            'the cellar code (Code_CellarCond) of the TABULA row Self("building_code") names: N (an unheated cellar)'
            " gives unheated_space, C, P, -, NI and 0 give heated_zone"
        ),
    )

    component_id: ComponentID
    #: TABULA/EPISCOPE code selecting the archetype, e.g. "DE.N.SFH.05.Gen.ReEx.001.002".
    building_code: str
    #: TABULA thermal-mass class, one of "very light" … "very heavy".
    building_heat_capacity_class: str = "medium"
    initial_internal_temperature_in_celsius: float = 22.0
    #: Heated floor area. Unset lets the Building derive it from the archetype, and the same
    #: holds for every measurement below it: unset means "take the TABULA row's value".
    absolute_conditioned_floor_area_in_m2: Optional[float] = None
    total_base_area_in_m2: Optional[float] = None
    number_of_apartments: Optional[float] = None
    max_thermal_building_demand_in_watt: Optional[float] = None
    floor_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    floor_area_in_m2: Optional[float] = None
    facade_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    facade_area_in_m2: Optional[float] = None
    roof_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    roof_area_in_m2: Optional[float] = None
    window_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    window_area_in_m2: Optional[float] = None
    door_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None
    door_area_in_m2: Optional[float] = None
    #: The windows, each with its own angles and optics, instead of the TABULA row's areas per
    #: direction with its single set of optics factors; unset keeps the row's windows. The window
    #: area is then the list's sum, so a list beside ``window_area_in_m2`` is refused.
    windows: Optional[List[ConfiguredWindow]] = None
    #: Air change rates in 1/h: the use-related one (TABULA ``n_air_use``) and the infiltration
    #: (``n_air_infiltration``); unset means the row's value, each on its own.
    use_air_change_rate_per_hour: Optional[float] = None
    infiltration_air_change_rate_per_hour: Optional[float] = None
    #: The air volume in m³ the ventilation conductance multiplies; unset means the row's room
    #: height times the conditioned floor area.
    conditioned_air_volume_in_m3: Optional[float] = None
    #: Transmission adjustment factors b of the opaque elements, used as given; unset means the
    #: row's largest ``b_Transmission_*`` column, or the element's fixed factor when its U-value
    #: is configured (0.5 for the floor, 1 for facade and roof).
    floor_transmission_adjustment_factor: Optional[float] = None
    facade_transmission_adjustment_factor: Optional[float] = None
    roof_transmission_adjustment_factor: Optional[float] = None
    #: The ISO 13790 mass and surface quantities of the 5R1C model: the thermal capacity C_m in
    #: J/K, the effective mass area A_m and the total internal surface area A_tot in m²; unset
    #: means the heat-capacity class times the conditioned floor area for the first two and 4.5
    #: times the conditioned floor area for the last.
    thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin: Optional[float] = None
    effective_mass_area_in_m2: Optional[float] = None
    total_internal_surface_area_in_m2: Optional[float] = None
    #: Indoor temperatures the residents set, which bound the heating and the cooling season.
    set_heating_temperature_in_celsius: float = 20.0
    set_cooling_temperature_in_celsius: float = 25.0
    enable_opening_windows: bool = False
    #: CO2 footprint of investment in kg. Unset throughout the repository, which is what makes
    #: postprocessing look the building up in the cost database instead.
    device_co2_footprint_in_kg: Optional[float] = None
    #: cost for investment in Euro
    investment_costs_in_euro: Optional[float] = None
    #: lifetime in years
    lifetime_in_years: Optional[float] = None
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: Optional[float] = None
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float] = None

    #: Outside design temperature the heating load is computed against, sized from the weather
    #: by the sizing engine. The design condition belongs to the place the building stands in
    #: rather than to the building, so ``WeatherConfig`` states it and the building, the
    #: heat-distribution controller and the hplib heat pump all read the one number (D-21).
    heating_reference_temperature_in_celsius: Sizable[float] = sized_field(
        rule=Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS
    )

    #: The weather this building is computed with, as ``WeatherConfig.identity()`` spells it,
    #: sized from the weather by the sizing engine. It is not cache-key material -- the
    #: solar-gains series are keyed by their producer, under the weather's own artifact key --
    #: but it is sizing wire format, spelled out in every recorded twin and in the schema.
    weather_identity: Sizable[str] = sized_field(rule=Size.WEATHER_IDENTITY, value_type=str)

    #: Where the building's technical room is, the heated zone or an unheated space: the place where every device
    #: stands unless its own configuration names another one. Left ``AUTO`` by every preset, it is derived from the
    #: cellar code of the TABULA row (:data:`TECHNICAL_ROOM_LOCATION_LAW`); a file pins it for a house whose row says
    #: otherwise. It cannot be outdoors: a technical room is under the house's roof.
    technical_room_location: Sizable[lt.InstallationLocation] = sized_field(
        rule=TECHNICAL_ROOM_LOCATION_LAW,
        value_type=lt.InstallationLocation,
        note="the TABULA row's cellar code: an unheated cellar gives an unheated space, every other code the heated zone",
    )

    def __post_init__(self) -> None:
        """Refuse a technical room outdoors.

        A technical room is under the house's roof, in the heated zone or in an unheated space; a device that stands
        outdoors says so in its own configuration.

        Raises:
            ValueError: If ``technical_room_location`` is ``OUTDOORS``.
        """
        if self.technical_room_location is lt.InstallationLocation.OUTDOORS:
            raise ValueError(
                f"The building {self.component_id.name!r} has technical_room_location 'outdoors', but a technical room "
                "is under the house's roof: pin 'heated_zone' or 'unheated_space', or name 'outdoors' on the device "
                "that stands outside."
            )

    @staticmethod
    def sizing_facts(config: "BuildingConfig", ctx: SizingContext) -> dict:
        """Contributes the building-scope facts every other component sizes against.

        Runs the TABULA/EPISCOPE lookup once, through :class:`BuildingInformation`, and
        snapshots the quantities derived from it — the heating load, the apartment count, the
        conditioned floor area and the roof area — beside the two indoor temperatures the
        configuration states itself. Doing it here rather than per consumer is what keeps the
        lookup to one run per resolution, and it needs no constructed component.

        The outside design temperature is not among them: it belongs to the place rather than to
        the building, so ``WeatherConfig`` contributes it and this class reads it (D-21).

        Args:
            config: this building configuration.
            ctx: the sizing context; unused, the building is the root of the fact graph.

        Returns:
            dict: the seven facts named in :attr:`SIZING_CONTRIBUTIONS`.
        """
        del ctx
        # Imported here because information.py imports this module; the call is long after both
        # are loaded.
        from hisim.components.building.information import (  # pylint: disable=import-outside-toplevel
            BuildingInformation,
        )

        information = BuildingInformation(config=config)
        return {
            "heating_load_in_watt": information.max_thermal_building_demand_in_watt,
            "number_of_apartments": information.number_of_apartments,
            "conditioned_floor_area_in_m2": information.scaled_conditioned_floor_area_in_m2,
            "roof_area_in_m2": information.roof_area_in_m2,
            "set_heating_temperature_in_celsius": config.set_heating_temperature_in_celsius,
            "set_cooling_temperature_in_celsius": config.set_cooling_temperature_in_celsius,
            "technical_room_location": config.technical_room_location,
        }

    #: Sizing facts this config contributes to the scenario-wide fact pool: the building is the
    #: root of the fact graph, and these seven are what everything else sizes against.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=(
                "heating_load_in_watt",
                "number_of_apartments",
                "conditioned_floor_area_in_m2",
                "roof_area_in_m2",
                "set_heating_temperature_in_celsius",
                "set_cooling_temperature_in_celsius",
                "technical_room_location",
            ),
            compute=sizing_facts,
        ),
    )

    @preset(note="TABULA/EPISCOPE German single-family reference house")
    @classmethod
    def preset_german_single_family_home(cls, name: str) -> "BuildingConfig":
        """The German single-family reference house, the repo's default building."""
        return cls.for_tabula_code(
            name,
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            absolute_conditioned_floor_area_in_m2=121.2,
        )

    @constructor
    @classmethod
    def for_tabula_code(
        cls,
        name: str,
        building_code: str,
        number_of_apartments: Optional[float] = None,
        absolute_conditioned_floor_area_in_m2: Optional[float] = None,
        total_base_area_in_m2: Optional[float] = None,
        building_heat_capacity_class: str = "medium",
    ) -> "BuildingConfig":
        """Builds a building from a TABULA/EPISCOPE building code and its few free numbers.

        The building is parameterised by a *lookup* — the TABULA building-code space has
        hundreds of members — rather than by a handful of variants, which is why it is a
        named constructor and not a preset per code: a preset name is wire format forever,
        and minting hundreds of them would freeze an arbitrary subset of the catalogue into
        the file format. Everything the code decides — every envelope U-value and area — keeps
        its unset default, so the Building derives it from the archetype.

        Args:
            name: Instance name of the building component; its ``ComponentID`` is built
                from it.
            building_code: TABULA/EPISCOPE code selecting the archetype, e.g.
                ``"DE.N.SFH.05.Gen.ReEx.001.002"``.
            number_of_apartments: Dwelling units in the building; ``None`` lets the
                Building derive it from the archetype.
            absolute_conditioned_floor_area_in_m2: Heated floor area; ``None`` lets the
                Building derive it from the archetype.
            total_base_area_in_m2: Footprint area, an alternative to the floor area for
                scaling the archetype; ``None`` unless the caller measured it.
            building_heat_capacity_class: TABULA thermal-mass class, one of ``"very light"``
                … ``"very heavy"``.

        Returns:
            A fresh, fully populated configuration; nothing about it is shared with any
            other instance.
        """
        return cls(
            component_id=ComponentID(name=name),
            building_code=building_code,
            building_heat_capacity_class=building_heat_capacity_class,
            absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
            total_base_area_in_m2=total_base_area_in_m2,
            number_of_apartments=number_of_apartments,
        )
