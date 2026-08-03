"""Tests for the sizing figures the heat distribution system derives when its config omits them.

A JSON scenario has no sizing step. The Python system setups compute the circuit's water mass
flow rate and the design heating load from the building and pass them into the configs; a
scenario written by hand or by the editor carries whatever was in the file, which for these two
fields is a zero nobody can be expected to fill in. They are therefore derived at
``i_prepare_simulation`` from the building's design heating load, which the Building publishes.

The conditioned floor area is deliberately *not* derived: it describes the building the system
heats, so it is something the author knows, and leaving it at zero used to surface as a bare
``ZeroDivisionError`` several call levels into the free-convection branch.
"""
# clean
import pytest

from hisim.components import building, heat_distribution_system
from hisim.simulationparameters import SimulationParameters
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository

SECONDS_PER_TIMESTEP = 60
BUILDING_CODE = "DE.N.SFH.05.Gen.ReEx.001.002"


def _simulation_parameters() -> SimulationParameters:
    return SimulationParameters.one_day_only(year=2021, seconds_per_timestep=SECONDS_PER_TIMESTEP)


def _build_building(floor_area_in_m2: float = 121.2) -> building.Building:
    """A building whose construction publishes its design heating load."""
    config = building.BuildingConfig.get_default_german_single_family_home()
    config.building_code = BUILDING_CODE
    config.absolute_conditioned_floor_area_in_m2 = floor_area_in_m2
    return building.Building(config=config, my_simulation_parameters=_simulation_parameters())


def _hds(
    water_mass_flow_rate_in_kg_per_second: float,
    absolute_conditioned_floor_area_in_m2: float,
    heating_system=heat_distribution_system.HeatDistributionSystemType.FLOORHEATING,
) -> heat_distribution_system.HeatDistribution:
    config = heat_distribution_system.HeatDistributionConfig.get_default_heatdistributionsystem_config(
        water_mass_flow_rate_in_kg_per_second=water_mass_flow_rate_in_kg_per_second,
        absolute_conditioned_floor_area_in_m2=absolute_conditioned_floor_area_in_m2,
        heating_system=heating_system,
    )
    return heat_distribution_system.HeatDistribution(
        config=config, my_simulation_parameters=_simulation_parameters()
    )


@pytest.fixture(autouse=True)
def _clear_singleton_repository():
    """The repository is a process-wide singleton — keep entries from leaking between tests."""
    SingletonSimRepository().my_dict.pop(SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND, None)
    yield
    SingletonSimRepository().my_dict.pop(SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND, None)


@pytest.mark.base
def test_building_publishes_its_design_heating_load() -> None:
    """Everything downstream is sized on this figure, so the Building has to publish it."""
    my_building = _build_building()
    assert SingletonSimRepository().entry_exists(key=SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND)
    assert SingletonSimRepository().get_entry(key=SingletonDictKeyEnum.MAXTHERMALBUILDINGDEMAND) == (
        my_building.my_building_information.max_thermal_building_demand_in_watt
    )


@pytest.mark.base
def test_water_mass_flow_rate_is_sized_from_the_building() -> None:
    """A mass flow rate of zero is replaced by the figure the system setups would have passed."""
    my_building = _build_building()
    my_hds = _hds(water_mass_flow_rate_in_kg_per_second=0, absolute_conditioned_floor_area_in_m2=121.2)
    assert my_hds.heating_distribution_system_water_mass_flow_rate_in_kg_per_second == 0

    my_hds.i_prepare_simulation()

    # Same formula as HeatDistributionControllerInformation: load / (cp * design spread), and
    # FLOORHEATING's spread is 35 - 28 K.
    expected = round(
        my_building.my_building_information.max_thermal_building_demand_in_watt
        / (my_hds.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius * (35.0 - 28.0)),
        2,
    )
    assert my_hds.heating_distribution_system_water_mass_flow_rate_in_kg_per_second == expected
    assert expected > 0
    # The config is kept in step so report and KPIs show what was actually simulated.
    assert my_hds.heat_distribution_system_config.water_mass_flow_rate_in_kg_per_second == expected


@pytest.mark.base
def test_a_configured_mass_flow_rate_is_never_overwritten() -> None:
    """Deriving is a fallback, not a policy — an explicit value wins."""
    _build_building()
    my_hds = _hds(water_mass_flow_rate_in_kg_per_second=0.42, absolute_conditioned_floor_area_in_m2=121.2)
    my_hds.i_prepare_simulation()
    assert my_hds.heating_distribution_system_water_mass_flow_rate_in_kg_per_second == 0.42


@pytest.mark.base
def test_radiator_system_is_sized_on_its_own_temperature_spread() -> None:
    """The design spread depends on the system type: 70/55 K for radiators, not 35/28."""
    my_building = _build_building()
    my_hds = _hds(
        water_mass_flow_rate_in_kg_per_second=0,
        absolute_conditioned_floor_area_in_m2=121.2,
        heating_system=heat_distribution_system.HeatDistributionSystemType.RADIATOR,
    )
    my_hds.i_prepare_simulation()

    expected = round(
        my_building.my_building_information.max_thermal_building_demand_in_watt
        / (my_hds.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius * (70.0 - 55.0)),
        2,
    )
    assert my_hds.heating_distribution_system_water_mass_flow_rate_in_kg_per_second == expected


@pytest.mark.base
def test_mass_flow_rate_stays_zero_without_a_building() -> None:
    """Nothing to derive from — the run continues, but the reason is on the record."""
    my_hds = _hds(water_mass_flow_rate_in_kg_per_second=0, absolute_conditioned_floor_area_in_m2=121.2)
    my_hds.i_prepare_simulation()
    assert my_hds.heating_distribution_system_water_mass_flow_rate_in_kg_per_second == 0


@pytest.mark.base
def test_zero_floor_area_is_rejected_before_the_first_timestep() -> None:
    """The message has to name the field; the old failure was a bare ZeroDivisionError."""
    _build_building()
    my_hds = _hds(water_mass_flow_rate_in_kg_per_second=0, absolute_conditioned_floor_area_in_m2=0)

    with pytest.raises(ValueError, match="absolute_conditioned_floor_area_in_m2"):
        my_hds.i_prepare_simulation()


@pytest.mark.base
def test_zero_floor_area_used_to_divide_by_zero() -> None:
    """Documents the failure the check above prevents, straight from the free-convection branch."""
    my_hds = _hds(water_mass_flow_rate_in_kg_per_second=0, absolute_conditioned_floor_area_in_m2=0)
    with pytest.raises(ZeroDivisionError):
        my_hds.determine_water_temperature_input_output_effective_thermal_power_without_massflow(
            residence_temperature_in_celsius=21.0
        )


@pytest.mark.base
def test_controller_takes_the_heating_load_from_the_building() -> None:
    """The controller's design heating load follows the building for the same reason."""
    my_building = _build_building()
    config = heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        heating_load_of_building_in_watt=0,
        set_heating_temperature_for_building_in_celsius=20,
        set_cooling_temperature_for_building_in_celsius=25,
    )
    controller = heat_distribution_system.HeatDistributionController(
        config=config, my_simulation_parameters=_simulation_parameters()
    )
    assert controller.hsd_controller_config.heating_load_of_building_in_watt == 0

    controller.i_prepare_simulation()

    assert controller.hsd_controller_config.heating_load_of_building_in_watt == round(
        my_building.my_building_information.max_thermal_building_demand_in_watt, 2
    )


@pytest.mark.base
def test_sizing_does_not_depend_on_component_order() -> None:
    """Derivation runs in i_prepare_simulation, so a system added before its building still works."""
    my_hds = _hds(water_mass_flow_rate_in_kg_per_second=0, absolute_conditioned_floor_area_in_m2=121.2)
    my_building = _build_building()  # constructed *after* the system that depends on it

    my_hds.i_prepare_simulation()

    assert my_hds.heating_distribution_system_water_mass_flow_rate_in_kg_per_second > 0
    assert my_building.my_building_information.max_thermal_building_demand_in_watt > 0


@pytest.mark.base
def test_design_temperature_table_matches_the_controller() -> None:
    """One table feeds both the heating curve and the sizing — they cannot drift apart."""
    for system_type in heat_distribution_system.HeatDistributionSystemType:
        flow, return_, exponent = (
            heat_distribution_system.get_design_temperatures_of_heat_distribution_system(system_type)
        )
        info = heat_distribution_system.HeatDistributionControllerInformation(
            config=heat_distribution_system.HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
                heating_load_of_building_in_watt=8000,
                set_heating_temperature_for_building_in_celsius=20,
                set_cooling_temperature_for_building_in_celsius=25,
                heating_system=system_type,
            )
        )
        assert info.max_flow_temperature_in_celsius == flow
        assert info.max_return_temperature_in_celsius == return_
        assert info.exponent_factor_of_heating_distribution_system == exponent


@pytest.mark.base
def test_unknown_system_type_is_rejected() -> None:
    """An int outside the enum used to fall through to a ValueError; it still must."""
    with pytest.raises(ValueError, match="Heating System Type"):
        heat_distribution_system.get_design_temperatures_of_heat_distribution_system(99)
