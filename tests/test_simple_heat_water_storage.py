from hisim import component as cp
from hisim.components import simple_heat_water_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft
from hisim import log

def test_simple_storage():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # Set Simple Heat Water Storage
    hws_name = "SimpleHeatWaterStorage"
    volume_heating_water_storage_in_liter = 100
    mean_water_temperature_in_storage_in_celsius = 50
    cool_water_temperature_in_storage_in_celsius = 50
    hot_water_temperature_in_storage_in_celsius = 50

    #===================================================================================================================
    # Build Heat Water Storage
    my_simple_heat_water_storage_config = simple_heat_water_storage.HeatingWaterStorageConfig(
        name=hws_name,
        volume_heating_water_storage_in_liter=volume_heating_water_storage_in_liter,
        mean_water_temperature_in_storage_in_celsius=mean_water_temperature_in_storage_in_celsius,
        cool_water_temperature_in_storage_in_celsius=cool_water_temperature_in_storage_in_celsius,
        hot_water_temperature_in_storage_in_celsius=hot_water_temperature_in_storage_in_celsius
    )
    my_simple_heat_water_storage = simple_heat_water_storage.HeatingWaterStorage(
        config=my_simple_heat_water_storage_config,
        my_simulation_parameters=my_simulation_parameters,
    )

    water_temperature_input_from_heat_distribution_system = cp.ComponentOutput("FakeWaterInputTemperatureFromHds",
                             "WaterTemperatureInputFromHeatDistributionSystem",
                                                      lt.LoadTypes.TEMPERATURE,
                                                   lt.Units.CELSIUS)

    water_temperature_input_from_heat_generator = cp.ComponentOutput("FakeWaterInputTemperatureFromHeatGenerator",
                              "WaterTemperatureInputFromHeatGenerator",
                                                   lt.LoadTypes.TEMPERATURE,
                                                   lt.Units.CELSIUS)

    water_mass_flow_rate_from_heat_distribution_system = cp.ComponentOutput("FakeWaterMassFlowRateFromHds",
                              "WaterMassFlowRateFromHeatDistributionSystem",
                                                       lt.LoadTypes.WARM_WATER,
                                                       lt.Units.KG_PER_SEC)
    
    water_mass_flow_rate_from_heat_generator = cp.ComponentOutput("FakeWaterMassFlowRateFromHeatGenerator",
                              "WaterMassFlowRateFromHeatGenerator",
                                                       lt.LoadTypes.WARM_WATER,
                                                       lt.Units.KG_PER_SEC)

    
    my_simple_heat_water_storage.water_temperature_heat_distribution_system_input_channel.source_output = water_temperature_input_from_heat_distribution_system
    my_simple_heat_water_storage.water_temperature_heat_generator_input_channel.source_output = water_temperature_input_from_heat_generator
    my_simple_heat_water_storage.water_mass_flow_rate_heat_distrution_system_input_channel.source_output = water_mass_flow_rate_from_heat_distribution_system
    my_simple_heat_water_storage.water_mass_flow_rate_heat_generator_input_channel.source_output = water_mass_flow_rate_from_heat_generator
    
    

    number_of_outputs = fft.get_number_of_outputs([water_temperature_input_from_heat_distribution_system,
                                        water_temperature_input_from_heat_generator,
                                        water_mass_flow_rate_from_heat_distribution_system,
                                        water_mass_flow_rate_from_heat_generator,
                                        my_simple_heat_water_storage])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([water_temperature_input_from_heat_distribution_system,
                                        water_temperature_input_from_heat_generator,
                                        water_mass_flow_rate_from_heat_distribution_system,
                                        water_mass_flow_rate_from_heat_generator,
                                        my_simple_heat_water_storage])


    stsv.values[water_temperature_input_from_heat_distribution_system.global_index] = 48
    stsv.values[water_temperature_input_from_heat_generator.global_index] = 52
    stsv.values[water_mass_flow_rate_from_heat_distribution_system.global_index] = 0.787
    stsv.values[water_mass_flow_rate_from_heat_generator.global_index] = 0.59

    timestep = 300
    # Simulate

    my_simple_heat_water_storage.i_restore_state()

    for seconds_per_timestep in [60, 60* 15, 60 * 30, 60* 60, 60* 120]:
        log.information("sec per timestep " + str(seconds_per_timestep))
        my_simple_heat_water_storage.seconds_per_timestep = seconds_per_timestep

        my_simple_heat_water_storage.i_simulate(timestep, stsv, False)

        log.information("storage mean temp " + str(my_simple_heat_water_storage.mean_water_temperature_in_water_storage_in_celsius))
        log.information("Stsv outputs " + str(stsv.values))

