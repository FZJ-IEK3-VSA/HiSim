"""Configuration dataclass for the Building component.

Holds ``BuildingConfig`` with its named default preset, its TABULA constructor and the sizing
facts it contributes to every other component of a scenario. The physics behind those facts is
``BuildingInformation`` in ``information.py``, which the contribution calls.
"""

# pylint: disable=cyclic-import
# (the only backward edges are runtime-local imports of Building and BuildingInformation;
# module import order is acyclic)

from dataclasses import dataclass
from typing import ClassVar, Optional, Tuple

from dataclasses_json import dataclass_json

from hisim.config import (
    ComponentID,
    ConfigBase,
    FactContribution,
    Sizable,
    Size,
    SizingContext,
    constructor,
    preset,
    sized_field,
)


@dataclass_json
@dataclass
class BuildingConfig(ConfigBase):
    """Configuration of the Building class.

    The named default variant is :meth:`preset_german_single_family_home`, and any other
    building comes from :meth:`for_tabula_code`. The building is the *source* of the sizing
    facts every other component sizes against (see :attr:`SIZING_CONTRIBUTIONS`) and therefore
    has no sizable field of its own apart from the weather it is computed with: its presets are
    plain concrete archetypes, and a setup that deviates from one — a different TABULA code, an
    explicit envelope U-value, a measured maximum thermal demand — takes the preset and assigns
    the field.
    """

    MAIN_CLASS = "hisim.components.building.building.Building"

    component_id: ComponentID
    #: Outside design temperature the heating load is computed for.
    heating_reference_temperature_in_celsius: float
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

    #: The weather this building is computed with, as ``WeatherConfig.identity()`` spells it,
    #: sized from the weather by the sizing engine. It is not cache-key material -- the
    #: solar-gains series are keyed by their producer, under the weather's own artifact key --
    #: but it is sizing wire format, spelled out in every recorded twin and in the schema.
    weather_identity: Sizable[str] = sized_field(rule=Size.WEATHER_IDENTITY, value_type=str)

    @staticmethod
    def sizing_facts(config: "BuildingConfig", ctx: SizingContext) -> dict:
        """Contributes the building-scope facts every other component sizes against.

        Runs the TABULA/EPISCOPE lookup once, through :class:`BuildingInformation`, and
        snapshots the quantities derived from it — the heating load, the apartment count, the
        conditioned floor area and the roof area — beside the three temperatures the
        configuration states itself. Doing it here rather than per consumer is what keeps the
        lookup to one run per resolution, and it needs no constructed component.

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
            "heating_reference_temperature_in_celsius": config.heating_reference_temperature_in_celsius,
            "set_heating_temperature_in_celsius": config.set_heating_temperature_in_celsius,
            "set_cooling_temperature_in_celsius": config.set_cooling_temperature_in_celsius,
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
                "heating_reference_temperature_in_celsius",
                "set_heating_temperature_in_celsius",
                "set_cooling_temperature_in_celsius",
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
        heating_reference_temperature_in_celsius: float = -7.0,
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
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
            total_base_area_in_m2=total_base_area_in_m2,
            number_of_apartments=number_of_apartments,
        )
