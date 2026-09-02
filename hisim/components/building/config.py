"""Configuration dataclass for the Building component.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout). Holds ``BuildingConfig`` together with its named default preset and
its TABULA constructor. The
sizing facts the building contributes to the rest of a scenario are declared next to the
physics that computes them, in ``information.py``, which assigns
``BuildingConfig.SIZING_CONTRIBUTIONS`` on import.
"""

# clean

# pylint: disable=cyclic-import
# (the only backward edge is a runtime-local import of Building inside
# get_main_classname; module import order is acyclic)

from dataclasses import dataclass
from typing import ClassVar, Optional

from dataclasses_json import dataclass_json

from hisim.config import ComponentID, ConfigBase, Sizable, Size, constructor, preset, sized_field


@dataclass_json
@dataclass
class BuildingConfig(ConfigBase):
    """Configuration of the Building class.

    The named default variant is :meth:`preset_standard`, which replaced the former
    ``get_default_german_single_family_home`` factory, and any other building comes from
    :meth:`for_tabula_code`. The building is the *source* of the
    sizing facts every other component sizes against (see :attr:`SIZING_CONTRIBUTIONS`) and
    therefore has no sizable field of its own: its presets are plain concrete archetypes, and
    a setup that deviates from one — a different TABULA code, an explicit envelope U-value, a
    measured maximum thermal demand — takes the preset and assigns the field.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the base class."""
        from hisim.components.building.building import Building  # pylint: disable=import-outside-toplevel  # avoids config->building import cycle

        return Building.get_full_classname()  # type: ignore[no-any-return]

    component_id: ComponentID
    heating_reference_temperature_in_celsius: float
    building_code: str
    building_heat_capacity_class: str
    initial_internal_temperature_in_celsius: float
    absolute_conditioned_floor_area_in_m2: Optional[float]
    total_base_area_in_m2: Optional[float]
    number_of_apartments: Optional[float]
    max_thermal_building_demand_in_watt: Optional[float]
    floor_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    floor_area_in_m2: Optional[float]
    facade_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    facade_area_in_m2: Optional[float]
    roof_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    roof_area_in_m2: Optional[float]
    window_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    window_area_in_m2: Optional[float]
    door_u_value_in_watt_per_m2_per_kelvin: Optional[float]
    door_area_in_m2: Optional[float]
    predictive: bool
    set_heating_temperature_in_celsius: float
    set_cooling_temperature_in_celsius: float
    enable_opening_windows: bool
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg:  Optional[float]
    #: cost for investment in Euro
    investment_costs_in_euro:  Optional[float]
    #: lifetime in years
    lifetime_in_years:  Optional[float]
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year:  Optional[float]
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: Optional[float]

    #: Which weather the cached solar gains through the windows were computed against, as the
    #: weather's own readable identity (see ``WeatherConfig.identity``). Sized from the weather so
    #: the cache key, which hashes this whole config, is complete. ``roadmap/pylpg_flakiness.md`` F7.
    weather_identity: Sizable[str] = sized_field(rule=Size.WEATHER_IDENTITY, value_type=str)

    #: Sizing facts this config contributes to the scenario-wide fact pool.
    #: Computed from the config alone via BuildingInformation, so the TABULA lookup runs
    #: once per resolution, never per consumer, and never needs a constructed component.
    #: Assigned in ``information.py``, next to the physics it calls.
    SIZING_CONTRIBUTIONS: ClassVar[tuple] = ()

    @preset(note="TABULA/EPISCOPE German single-family reference house")
    @classmethod
    def preset_standard(cls, name: str) -> "BuildingConfig":
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
        heating_reference_temperature_in_celsius: float = -7.0,
    ) -> "BuildingConfig":
        """Builds a building from a TABULA/EPISCOPE building code and its few free numbers.

        The building is parameterised by a *lookup* — the TABULA building-code space has
        hundreds of members — rather than by a handful of variants, which is why it is a
        named constructor and not a preset per code: a preset name is wire format forever,
        and minting hundreds of them would freeze an arbitrary subset of the catalogue into
        the file format. Every envelope U-value and area is left ``None`` so the Building
        component derives it from the code, the capex fields stay ``None`` so postprocessing
        looks them up from the device database, and the comfort settings take the values the
        repository has always used.

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
            heating_reference_temperature_in_celsius: Outside design temperature the
                heating load is computed for.

        Returns:
            A fresh, fully populated configuration; nothing about it is shared with any
            other instance.
        """
        return cls(
            component_id=ComponentID(name=name),
            building_code=building_code,
            building_heat_capacity_class=building_heat_capacity_class,
            initial_internal_temperature_in_celsius=22.0,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
            max_thermal_building_demand_in_watt=None,
            floor_u_value_in_watt_per_m2_per_kelvin=None,
            floor_area_in_m2=None,
            facade_u_value_in_watt_per_m2_per_kelvin=None,
            facade_area_in_m2=None,
            roof_u_value_in_watt_per_m2_per_kelvin=None,
            roof_area_in_m2=None,
            window_u_value_in_watt_per_m2_per_kelvin=None,
            window_area_in_m2=None,
            door_u_value_in_watt_per_m2_per_kelvin=None,
            door_area_in_m2=None,
            total_base_area_in_m2=total_base_area_in_m2,
            number_of_apartments=number_of_apartments,
            predictive=False,
            set_heating_temperature_in_celsius=20.0,
            set_cooling_temperature_in_celsius=25.0,
            enable_opening_windows=False,
            device_co2_footprint_in_kg=None,  # todo: check value
            investment_costs_in_euro=None,   # todo: check value
            maintenance_costs_in_euro_per_year=None,  # todo: check value
            subsidy_as_percentage_of_investment_costs=None,
            lifetime_in_years=None,  # todo: check value
        )
