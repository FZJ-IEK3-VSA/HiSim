"""Test for simple hot water storage."""

# clean
import pytest
import numpy as np
from hisim import component as cp
from hisim.components import simple_water_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.config import ComponentID, ConfigSizingError, SizingContext, auto_fields
from tests import functions_for_testing as fft


@pytest.mark.base
def test_simple_storage() -> None:
    """Test the simple hot water storage across several timestep sizes.

    Iterates over a range of seconds-per-timestep values (1 min to 2 h),
    computes the expected water-storage mixing factor for each (linear up to
    1 h, then clamped to 1.0), and delegates the actual simulation and
    assertions to `simulate_simple_water_storage`.
    """

    # calculate mixing factors and run simulation for different seconds per timestep
    seconds_per_timesteps_to_test = [60, 60 * 15, 60 * 30, 60 * 60, 60 * 120]
    factor_for_water_storage_portion = 0.0
    for sec_per_timestep in seconds_per_timesteps_to_test:
        if sec_per_timestep <= 3600:
            factor_for_water_storage_portion = sec_per_timestep / 3600
        elif sec_per_timestep > 3600:
            factor_for_water_storage_portion = 1.0

        simulate_simple_water_storage(
            sec_per_timestep,
            factor_for_water_storage_portion=factor_for_water_storage_portion,
        )


def simulate_simple_water_storage(sec_per_timesteps: int, factor_for_water_storage_portion: float) -> None:
    """Build a SimpleHotWaterStorage, run one simulation step, and assert outputs.

    Constructs a `SimpleHotWaterStorage` (100 L, parallel-to-heat-source
    setup) with fake input connections for mass flow rates and temperatures
    from the heat distribution system and heat generator plus a state
    controller, drives a single `i_simulate` call at timestep 300, and
    checks the mean storage temperature and both output temperatures against
    independently recomputed values.

    Args:
        sec_per_timesteps: Seconds represented by each simulation timestep;
            used to size the `SimulationParameters` and to convert mass
            flow rates (kg/s) into masses (kg) for the expected-temperature
            calculation.
        factor_for_water_storage_portion: Mixing fraction in [0.0, 1.0] of
            the storage's mean temperature blended into each output
            temperature; scales how strongly the storage temperature
            influences the outputs versus the respective input temperatures.
    """

    my_simulation_parameters = SimulationParameters.one_day_only(2017, sec_per_timesteps)

    # Set Simple Heat Water Storage
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 100

    # ===================================================================================================================
    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_water_storage.SimpleHotWaterStorageConfig(
        component_id=ComponentID(name=hws_name),
        volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
        heat_transfer_coefficient_in_watt_per_m2_per_kelvin=2.0,
        heat_exchanger_is_present=False,
        position_hot_water_storage_in_system=simple_water_storage.PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE,
        sizing_option=simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM,
        device_co2_footprint_in_kg=100,
        investment_costs_in_euro=volume_heating_water_storage_in_liter * 14.51,
        lifetime_in_years=100,
        maintenance_costs_in_euro_per_year=0.0,
        subsidy_as_percentage_of_investment_costs=0.0,
    )
    my_simple_heat_water_storage = simple_water_storage.SimpleHotWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    water_mass_flow_rate_hds = cp.ComponentOutput(
        "FakeWaterInputTemperatureFromHds",
        "WaterMassFlowRateFromHeatDistributionSystem",
        lt.LoadTypes.WARM_WATER,
        lt.Units.KG_PER_SEC,
        component_id=ComponentID("FakeWaterInputTemperatureFromHds"),
    )

    water_temperature_input_from_heat_distribution_system = cp.ComponentOutput(
        "FakeWaterInputTemperatureFromHds",
        "WaterTemperatureInputFromHeatDistributionSystem",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=ComponentID("FakeWaterInputTemperatureFromHds"),
    )

    water_temperature_input_from_heat_generator = cp.ComponentOutput(
        "FakeWaterInputTemperatureFromHeatGenerator",
        "WaterTemperatureInputFromHeatGenerator",
        lt.LoadTypes.TEMPERATURE,
        lt.Units.CELSIUS,
        component_id=ComponentID("FakeWaterInputTemperatureFromHeatGenerator"),
    )

    water_mass_flow_rate_from_heat_generator = cp.ComponentOutput(
        "FakeWaterMassFlowRateFromHeatGenerator",
        "WaterMassFlowRateFromHeatGenerator",
        lt.LoadTypes.WARM_WATER,
        lt.Units.KG_PER_SEC,
        component_id=ComponentID("FakeWaterMassFlowRateFromHeatGenerator"),
    )

    state_controller = cp.ComponentOutput(
        "FakeState", "State", lt.LoadTypes.ANY, lt.Units.ANY, component_id=ComponentID("FakeState")
    )

    # connect fake inputs to simple hot water storage
    my_simple_heat_water_storage.water_mass_flow_rate_heat_distribution_system_input_channel.source_output = (
        water_mass_flow_rate_hds
    )
    my_simple_heat_water_storage.water_temperature_heat_distribution_system_input_channel.source_output = (
        water_temperature_input_from_heat_distribution_system
    )
    my_simple_heat_water_storage.water_temperature_heat_generator_input_channel.source_output = (
        water_temperature_input_from_heat_generator
    )

    my_simple_heat_water_storage.water_mass_flow_rate_heat_generator_input_channel.source_output = (
        water_mass_flow_rate_from_heat_generator
    )

    my_simple_heat_water_storage.state_channel.source_output = state_controller

    number_of_outputs = fft.get_number_of_outputs(
        [
            water_mass_flow_rate_hds,
            water_temperature_input_from_heat_distribution_system,
            water_temperature_input_from_heat_generator,
            water_mass_flow_rate_from_heat_generator,
            state_controller,
            my_simple_heat_water_storage,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            water_mass_flow_rate_hds,
            water_temperature_input_from_heat_distribution_system,
            water_temperature_input_from_heat_generator,
            water_mass_flow_rate_from_heat_generator,
            state_controller,
            my_simple_heat_water_storage,
        ]
    )
    stsv.values[water_mass_flow_rate_hds.global_index] = 0.787
    stsv.values[water_temperature_input_from_heat_distribution_system.global_index] = 48
    stsv.values[water_temperature_input_from_heat_generator.global_index] = 52
    stsv.values[water_mass_flow_rate_from_heat_generator.global_index] = 0.59
    stsv.values[state_controller.global_index] = 1

    # Simulate for timestep 300
    timestep = 300
    my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius = 50
    my_simple_heat_water_storage.state.mean_water_temperature_in_celsius = 50
    previous_mean_temperature_in_celsius = 50.0
    my_simple_heat_water_storage.i_simulate(timestep, stsv, False)

    water_temperature_output_in_celsius_to_heat_distribution_system = stsv.values[5]
    water_temperature_output_in_celsius_to_heat_generator = stsv.values[6]

    # test mean water temperature calculation in storage
    mass_water_hds_in_kg = stsv.values[water_mass_flow_rate_hds.global_index] * sec_per_timesteps
    mass_water_hp_in_kg = stsv.values[water_mass_flow_rate_from_heat_generator.global_index] * sec_per_timesteps

    calculated_mean_water_temperature_in_celsius = (
        my_simple_heat_water_storage.water_mass_in_storage_in_kg * previous_mean_temperature_in_celsius
        + mass_water_hp_in_kg * stsv.values[water_temperature_input_from_heat_generator.global_index]
        + mass_water_hds_in_kg * stsv.values[water_temperature_input_from_heat_distribution_system.global_index]
    ) / (my_simple_heat_water_storage.water_mass_in_storage_in_kg + mass_water_hp_in_kg + mass_water_hds_in_kg)

    # test if calculated mean water temperature is equal to simulated water temperature.
    # A weighted average recomputed here from the same inputs may differ from the
    # component's i_simulate result by float-reassociation noise, so compare with a
    # tight tolerance instead of exact equality.
    np.testing.assert_allclose(
        calculated_mean_water_temperature_in_celsius,
        my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius,
        rtol=1e-6,
    )

    # test water output temperature for hp

    calculated_output_to_heat_generator_in_celsius = (
        factor_for_water_storage_portion
        * my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
        + (1 - factor_for_water_storage_portion)
        * stsv.values[water_temperature_input_from_heat_distribution_system.global_index]
    )

    np.testing.assert_allclose(
        calculated_output_to_heat_generator_in_celsius,
        water_temperature_output_in_celsius_to_heat_generator,
        rtol=0.01,
    )

    # test water output temperature for hds
    calculated_output_to_heat_distribution_system_in_celsius = (
        factor_for_water_storage_portion
        * my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius
        + (1 - factor_for_water_storage_portion) * stsv.values[water_temperature_input_from_heat_generator.global_index]
    )
    np.testing.assert_allclose(
        calculated_output_to_heat_distribution_system_in_celsius,
        water_temperature_output_in_celsius_to_heat_distribution_system,
        rtol=0.01,
    )


def _buffer_sized_at(
    power_in_watt: float, sizing_option: "simple_water_storage.HotWaterStorageSizingEnum"
) -> "simple_water_storage.SimpleHotWaterStorageConfig":
    """Returns the buffer preset resolved against one generator power and one sizing option.

    Every buffer test below states a power and a kind of generator and reads the volume back,
    so the three lines that build the preset, set the option on it and resolve it live here
    once. The option is assigned on the preset instance rather than passed to
    ``dataclasses.replace``, which would drop the preset's provenance stamp.

    Args:
        power_in_watt: The generator's maximal thermal power.
        sizing_option: The kind of generator the vessel buffers.

    Returns:
        SimpleHotWaterStorageConfig: The resolved configuration.
    """
    config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer(
        "SimpleHotWaterStorage"
    )
    config.sizing_option = sizing_option
    return config.resolve(SizingContext(maximal_thermal_power_in_watt=power_in_watt))


@pytest.mark.base
def test_buffer_volume_follows_the_generator_not_the_building_load() -> None:
    """The buffer volume is litres per kilowatt of the generator, not of the building load.

    P4 decision D-9 (plan item C11): every setup used to hand the building's heating load
    to the sizing argument, although the litres-per-kilowatt figures are per kilowatt of
    installed generator power. A condensing gas boiler that also prepares domestic hot water
    is sized at 1.1x the load (``GenericBoilerConfig.scale_thermal_power``), so for the
    7780.75 W load of the standard building it is an 8558.83 W boiler, and its buffer is
    171.18 l -- ten percent more than the 155.62 l the load used to produce. This pins that
    arithmetic, now through the law rather than through the deleted factory.
    """

    heating_load_of_building_in_watt = 7780.75
    # 8558.825 W is what GenericBoilerConfig.scale_thermal_power returns for that load and one
    # apartment; tests/test_sizing.py pins the 1.1x law itself.
    maximal_thermal_power_of_the_boiler_in_watt = 8558.825
    gas_heater = simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GAS_HEATER

    sized_from_the_generator = _buffer_sized_at(maximal_thermal_power_of_the_boiler_in_watt, gas_heater)
    assert sized_from_the_generator.volume_heating_water_storage_in_liter == 171.18

    # What the C11 defect produced, kept here as the measured size of the correction.
    sized_from_the_building_load = _buffer_sized_at(heating_load_of_building_in_watt, gas_heater)
    assert sized_from_the_building_load.volume_heating_water_storage_in_liter == 155.62


@pytest.mark.base
def test_the_buffer_law_reads_the_sizing_option_beside_it() -> None:
    """Test that each kind of generator gets its own litres-per-kilowatt figure.

    The five members of ``HotWaterStorageSizingEnum`` select 50, 20, 40, 50 and 20 l/kW, and
    the law reads the member off the configuration's own ``sizing_option`` field rather than
    off the context. These are the volumes the fleet's twins carry: 389.04 l for the heat-pump
    setups on their 7780.75 W heat pump, 342.35 l and 427.94 l for the pellet and wood chip
    sizers on their 8558.83 W boilers.
    """
    heat_pump_power_in_watt = 7780.75
    boiler_power_in_watt = 8558.825
    options = simple_water_storage.HotWaterStorageSizingEnum

    assert (
        _buffer_sized_at(heat_pump_power_in_watt, options.SIZE_ACCORDING_TO_HEAT_PUMP)
        .volume_heating_water_storage_in_liter
        == 389.04
    )
    assert (
        _buffer_sized_at(boiler_power_in_watt, options.SIZE_ACCORDING_TO_PELLET_HEATING)
        .volume_heating_water_storage_in_liter
        == 342.35
    )
    assert (
        _buffer_sized_at(boiler_power_in_watt, options.SIZE_ACCORDING_TO_WOOD_CHIP_HEATING)
        .volume_heating_water_storage_in_liter
        == 427.94
    )
    assert (
        _buffer_sized_at(boiler_power_in_watt, options.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM)
        .volume_heating_water_storage_in_liter
        == 171.18
    )
    assert (
        _buffer_sized_at(boiler_power_in_watt, options.SIZE_ACCORDING_TO_GAS_HEATER)
        .volume_heating_water_storage_in_liter
        == 171.18
    )


@pytest.mark.base
def test_the_buffer_preset_pins_the_vessel_and_leaves_the_volume_open() -> None:
    """Test that the buffer preset fixes the vessel's physics and wiring and nothing else.

    What the preset states is the heat loss coefficient, that a heat exchanger is fitted -- the
    stratified alternative still causes problems -- that the vessel stands parallel to the heat
    source, which is what gives the component its four heat-generator inputs, and that the
    generator is of no particular kind. What it does not state is how large the vessel is.
    """
    config = simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer(
        "SimpleHotWaterStorage"
    )

    assert config.component_id == ComponentID(name="SimpleHotWaterStorage")
    assert config.heat_transfer_coefficient_in_watt_per_m2_per_kelvin == 2.0
    assert config.heat_exchanger_is_present is True
    assert (
        config.position_hot_water_storage_in_system
        == simple_water_storage.PositionHotWaterStorageInSystemSetup.PARALLEL_TO_HEAT_SOURCE
    )
    assert (
        config.sizing_option
        == simple_water_storage.HotWaterStorageSizingEnum.SIZE_ACCORDING_TO_GENERAL_HEATING_SYSTEM
    )
    assert config.device_co2_footprint_in_kg is None
    assert config.investment_costs_in_euro is None
    assert config.lifetime_in_years is None
    assert config.maintenance_costs_in_euro_per_year is None
    assert config.subsidy_as_percentage_of_investment_costs is None
    assert set(auto_fields(config)) == {"volume_heating_water_storage_in_liter"}


@pytest.mark.base
def test_a_buffer_cannot_be_sized_without_a_generator_beside_it() -> None:
    """Test that a context carrying no generator power is refused, naming the missing fact.

    The vessel has no size of its own: its law reads ``maximal_thermal_power_in_watt``, which
    the heat generator contributes. Resolving against a context without it has to fail loudly
    rather than leave a buffer of unknown volume in the system.
    """
    with pytest.raises(ConfigSizingError, match="maximal_thermal_power_in_watt"):
        simple_water_storage.SimpleHotWaterStorageConfig.preset_buffer("SimpleHotWaterStorage").resolve(
            SizingContext()
        )


@pytest.mark.base
def test_the_dhw_preset_sizes_the_vessel_from_the_apartment_count() -> None:
    """Test that the DHW preset gives every apartment its 250 litres.

    The law is the arithmetic the deleted ``get_scaled_dhw_storage`` factory performed:
    ``SimpleDHWStorageConfig.VOLUME_PER_APARTMENT_IN_LITER`` per apartment. One apartment is the
    single-family house every recorded twin of the fleet stands in, and 250.0 l is the volume
    each of those twins carries; seventeen apartments is the multi-family archetype the building
    sizer and RenoVisor reach.
    """
    single_family_house = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(
        SizingContext(number_of_apartments=1)
    )
    assert single_family_house.volume_heating_water_storage_in_liter == 250.0

    multi_family_house = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(
        SizingContext(number_of_apartments=17)
    )
    assert multi_family_house.volume_heating_water_storage_in_liter == 4250.0


@pytest.mark.base
def test_the_dhw_volume_law_sizes_one_apartment_for_a_building_that_reports_none() -> None:
    """Test that the clamp of the DHW volume law survives an apartment count of zero.

    The deleted factory wrote the clamp as ``max(number_of_apartments, 1)``, and the law keeps it
    as ``.at_least(1)``. Without it a building reporting zero apartments would size a vessel of
    no volume, whose water mass is zero and whose temperature the storage would then divide by.
    """
    no_apartments = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(
        SizingContext(number_of_apartments=0)
    )
    assert no_apartments.volume_heating_water_storage_in_liter == 250.0


@pytest.mark.base
def test_the_dhw_preset_pins_the_losses_and_leaves_the_volume_open() -> None:
    """Test that the DHW preset fixes the vessel's constants and nothing else.

    What the preset states is how much heat the tank loses to its surroundings and that its
    capex is looked up rather than stated; what it deliberately does not state is how large the
    tank is. The volume stays unresolved until a sizing context supplies the apartment count.
    """
    config = simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage")

    assert config.component_id == ComponentID(name="DHWStorage")
    assert config.heat_transfer_coefficient_in_watt_per_m2_per_kelvin == 0.36
    assert config.device_co2_footprint_in_kg is None
    assert config.investment_costs_in_euro is None
    assert config.lifetime_in_years is None
    assert config.maintenance_costs_in_euro_per_year is None
    assert config.subsidy_as_percentage_of_investment_costs is None
    assert set(auto_fields(config)) == {"volume_heating_water_storage_in_liter"}


@pytest.mark.base
def test_a_dhw_vessel_cannot_be_sized_without_a_building_around_it() -> None:
    """Test that a context carrying no apartment count is refused, naming the missing fact.

    The vessel has no size of its own: its law reads ``number_of_apartments``, which the building
    configuration contributes. Resolving against a context without it has to fail loudly rather
    than leave a storage of unknown volume in the system.
    """
    with pytest.raises(ConfigSizingError, match="number_of_apartments"):
        simple_water_storage.SimpleDHWStorageConfig.preset_standard("DHWStorage").resolve(SizingContext())
