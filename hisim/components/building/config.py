"""Configuration dataclass for the Building component.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout). Holds ``BuildingConfig``, moved verbatim from the former
single-module ``building.py``.
"""

# clean

# pylint: disable=cyclic-import
# (the only backward edge is a runtime-local import of Building inside
# get_main_classname; module import order is acyclic)

from dataclasses import dataclass
from typing import Optional

from dataclasses_json import dataclass_json

from hisim.config import ConfigBase, ComponentID


@dataclass_json
@dataclass
class BuildingConfig(ConfigBase):
    """Configuration of the Building class."""

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

    @classmethod
    def get_default_german_single_family_home(
        cls,
        set_heating_temperature_in_celsius: float = 20.0,
        set_cooling_temperature_in_celsius: float = 25.0,
        heating_reference_temperature_in_celsius: float = -7.0,
        max_thermal_building_demand_in_watt: Optional[float] = None,
        floor_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        floor_area_in_m2: Optional[float] = None,
        facade_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        facade_area_in_m2: Optional[float] = None,
        roof_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        roof_area_in_m2: Optional[float] = None,
        window_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        window_area_in_m2: Optional[float] = None,
        door_u_value_in_watt_per_m2_per_kelvin: Optional[float] = None,
        door_area_in_m2: Optional[float] = None,
        component_id: Optional[ComponentID] = None,
    ) -> "BuildingConfig":
        """Create a BuildingConfig for a default German single-family home.

        Uses TABULA building code "DE.N.SFH.05.Gen.ReEx.001.002" with a medium
        heat-capacity class and 121.2 m² conditioned floor area. Envelope
        U-values, areas, and temperature setpoints can be overridden via the
        optional parameters; when left as None the TABULA defaults are used.

        Args:
            set_heating_temperature_in_celsius: Heating setpoint in °C.
            set_cooling_temperature_in_celsius: Cooling setpoint in °C.
            heating_reference_temperature_in_celsius: Reference outdoor
                temperature for heating-demand sizing in °C.
            max_thermal_building_demand_in_watt: Optional override for the
                maximum thermal demand in W; if None, computed from the
                building's heat conductances and temperature difference.
            floor_u_value_in_watt_per_m2_per_kelvin: Optional floor U-value
                override in W/(m²·K).
            floor_area_in_m2: Optional floor area override in m².
            facade_u_value_in_watt_per_m2_per_kelvin: Optional facade U-value
                override in W/(m²·K).
            facade_area_in_m2: Optional facade area override in m².
            roof_u_value_in_watt_per_m2_per_kelvin: Optional roof U-value
                override in W/(m²·K).
            roof_area_in_m2: Optional roof area override in m².
            window_u_value_in_watt_per_m2_per_kelvin: Optional window U-value
                override in W/(m²·K).
            window_area_in_m2: Optional window area override in m².
            door_u_value_in_watt_per_m2_per_kelvin: Optional door U-value
                override in W/(m²·K).
            door_area_in_m2: Optional door area override in m².
            component_id: Structured identity (name, building, unit) of the building component.

        Returns:
            A BuildingConfig configured for a German single-family home with
            the specified or default TABULA parameters.
        """
        if component_id is None:
            component_id = ComponentID(name="Building")
        config = BuildingConfig(
            component_id=component_id,
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=22.0,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            absolute_conditioned_floor_area_in_m2=121.2,
            max_thermal_building_demand_in_watt=max_thermal_building_demand_in_watt,
            floor_u_value_in_watt_per_m2_per_kelvin=floor_u_value_in_watt_per_m2_per_kelvin,
            floor_area_in_m2=floor_area_in_m2,
            facade_u_value_in_watt_per_m2_per_kelvin=facade_u_value_in_watt_per_m2_per_kelvin,
            facade_area_in_m2=facade_area_in_m2,
            roof_u_value_in_watt_per_m2_per_kelvin=roof_u_value_in_watt_per_m2_per_kelvin,
            roof_area_in_m2=roof_area_in_m2,
            window_u_value_in_watt_per_m2_per_kelvin=window_u_value_in_watt_per_m2_per_kelvin,
            window_area_in_m2=window_area_in_m2,
            door_u_value_in_watt_per_m2_per_kelvin=door_u_value_in_watt_per_m2_per_kelvin,
            door_area_in_m2=door_area_in_m2,
            total_base_area_in_m2=None,
            number_of_apartments=None,
            predictive=False,
            set_heating_temperature_in_celsius=set_heating_temperature_in_celsius,
            set_cooling_temperature_in_celsius=set_cooling_temperature_in_celsius,
            enable_opening_windows=False,
            device_co2_footprint_in_kg=None,  # todo: check value
            investment_costs_in_euro=None,   # todo: check value
            maintenance_costs_in_euro_per_year=None,  # noqa: E501 # todo: check value
            subsidy_as_percentage_of_investment_costs=None,
            lifetime_in_years=None,  # todo: check value
        )
        return config
