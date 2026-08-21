"""Configuration dataclass for the Building component.

Part of the ``hisim.components.building`` package split (see the package ``__init__``
for the layout). Holds ``BuildingConfig`` together with its named default presets. The
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

from hisim.config import Catalog, ComponentID, ConfigBase


@dataclass_json
@dataclass
class BuildingConfig(ConfigBase):
    """Configuration of the Building class.

    The named default variants live in :attr:`presets` (design B of
    ``system_docs/config_defaults_spec.md``), which replaced the former
    ``get_default_german_single_family_home`` factory. The building is the *source* of the
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

    #: Sizing facts this config contributes to the scenario-wide fact pool (spec §8.4).
    #: Computed from the config alone via BuildingInformation, so the TABULA lookup runs
    #: once per resolution, never per consumer, and never needs a constructed component.
    #: Assigned in ``information.py``, next to the physics it calls.
    SIZING_CONTRIBUTIONS: ClassVar[tuple] = ()

    #: Named building archetypes (preset names are wire format, spec §8.1). The canonical
    #: ``german_single_family_home`` is the TABULA/EPISCOPE reference house
    #: "DE.N.SFH.05.Gen.ReEx.001.002" with a medium heat-capacity class and 121.2 m²
    #: conditioned floor area — exactly what the deleted factory produced with all its
    #: optional arguments left out. Every envelope U-value and area stays ``None`` so the
    #: Building component derives it from the TABULA code; the capex fields stay ``None`` so
    #: postprocessing looks them up from the device database.
    presets: ClassVar[Catalog] = Catalog(
        german_single_family_home=lambda: BuildingConfig(
            component_id=ComponentID(name="Building"),
            building_code="DE.N.SFH.05.Gen.ReEx.001.002",
            building_heat_capacity_class="medium",
            initial_internal_temperature_in_celsius=22.0,
            heating_reference_temperature_in_celsius=-7.0,
            absolute_conditioned_floor_area_in_m2=121.2,
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
            total_base_area_in_m2=None,
            number_of_apartments=None,
            predictive=False,
            set_heating_temperature_in_celsius=20.0,
            set_cooling_temperature_in_celsius=25.0,
            enable_opening_windows=False,
            device_co2_footprint_in_kg=None,  # todo: check value
            investment_costs_in_euro=None,   # todo: check value
            maintenance_costs_in_euro_per_year=None,  # todo: check value
            subsidy_as_percentage_of_investment_costs=None,
            lifetime_in_years=None,  # todo: check value
        ),
    )
