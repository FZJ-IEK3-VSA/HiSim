import pytest
from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_electrolyzer_and_h2_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from tests import functions_for_testing as fft


@pytest.mark.base
def test_hydrogen_generator():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # HydrogenStorageConfig
    min_capacity = 0  # [kg_H2]
    max_capacity = 500  # [kg_H2]
    starting_fill = 0  # [kg_H2]
    max_charging_rate_hour = 2  # [kg/h]
    max_discharging_rate_hour = 2  # [kg/h]
    # max_charging_rate = max_charging_rate_hour / 3600
    # max_discharging_rate = max_discharging_rate_hour / 3600
    energy_for_charge = 0  # [kWh/kg]
    energy_for_discharge = 0  # [kWh/kg]
    loss_factor_per_day = 0  # [lost_%/day]

    #ElectrolyzerConfig
    waste_energy = 400                    # [W]   # 400
    min_power = 1_200                     # [W]   # 1400
    max_power = 2_400                  # [W]   # 2400
    min_power_percent = 60            # [%]
    max_power_percent = 100             # [%]
    min_hydrogen_production_rate_hour = 300  # [Nl/h]
    max_hydrogen_production_rate_hour = 5000   # [Nl/h]   #500

    # min_hydrogen_production_rate = min_hydrogen_production_rate_hour / 3600  # [Nl/s]
    # max_hydrogen_production_rate = max_hydrogen_production_rate_hour / 3600   # [Nl/s]
    pressure_hydrogen_output = 30       # [bar]     --> max pressure mode at 35 bar


    #===================================================================================================================
    # Set Hydrogen Generator
    my_electrolyzer_config=generic_electrolyzer_and_h2_storage.ElectrolyzerWithStorageConfig(
                                                            name="ElectrolyzerWithStorage",
                                                            waste_energy=waste_energy,
                                                            min_power=min_power,
                                                            max_power=max_power,
                                                            min_power_percent=min_power_percent,
                                                            max_power_percent=max_power_percent,
                                                            min_hydrogen_production_rate_hour=min_hydrogen_production_rate_hour,
                                                            max_hydrogen_production_rate_hour=max_hydrogen_production_rate_hour,
                                                            pressure_hydrogen_output=pressure_hydrogen_output
                                                            )
    my_electrolyzer = generic_electrolyzer_and_h2_storage.AdvancedElectrolyzer(
                                    my_simulation_parameters=my_simulation_parameters,
                                    config=my_electrolyzer_config)
    my_hydrogen_storage_config= generic_electrolyzer_and_h2_storage.ElectrolyzerWithHydrogenStorageConfig(
                                    name="ElectrolyzerWithHydrogenStorage",
                                    min_capacity=min_capacity,
                                    max_capacity=max_capacity,
                                    starting_fill=starting_fill,
                                    max_charging_rate_hour=max_charging_rate_hour,
                                    max_discharging_rate_hour=max_discharging_rate_hour,
                                    energy_for_charge=energy_for_charge,
                                    energy_for_discharge=energy_for_discharge,
                                    loss_factor_per_day=loss_factor_per_day)

    my_hydrogen_storage = generic_electrolyzer_and_h2_storage.HydrogenStorage(
                                    my_simulation_parameters=my_simulation_parameters,
                                    config=my_hydrogen_storage_config)

    # Set Fake Outputs for Gas Heater
    electricity_input = cp.ComponentOutput("FakeElectricityInput",
                             "ElectricityInput",
                                           lt.LoadTypes.ELECTRICITY,
                                           lt.Units.WATT)
    hydrogen_not_stored = cp.ComponentOutput("FakeHydrogenNotStored",
                             "HydrogenNotStored",
                                             lt.LoadTypes.HYDROGEN,
                                             lt.Units.KG)
    discharging_hydrogen_amount_target = cp.ComponentOutput("DischargingHydrogenAmountTarget",
                             "DischargingHydrogenAmountTarget",
                                                            lt.LoadTypes.HYDROGEN,
                                                            lt.Units.KG_PER_SEC)

    number_of_outputs = fft.get_number_of_outputs([electricity_input,hydrogen_not_stored,discharging_hydrogen_amount_target,my_electrolyzer,my_hydrogen_storage])
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_electrolyzer.electricity_input.source_output = electricity_input
    my_electrolyzer.hydrogen_not_stored.source_output = hydrogen_not_stored
    my_hydrogen_storage.discharging_hydrogen.source_output = discharging_hydrogen_amount_target
    my_hydrogen_storage.charging_hydrogen.source_output = my_electrolyzer.hydrogen_output

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components([electricity_input,hydrogen_not_stored,discharging_hydrogen_amount_target,my_electrolyzer,my_hydrogen_storage])
    stsv.values[electricity_input.global_index] = 4000
    stsv.values[hydrogen_not_stored.global_index] = 0
    stsv.values[discharging_hydrogen_amount_target.global_index] = 0

    timestep = 1000

    # Simulate
    my_electrolyzer.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    my_hydrogen_storage.i_simulate(timestep, stsv,  False)
    log.information(str(stsv.values))

    # Water Demand to produce Hydrogen
    assert stsv.values[my_electrolyzer.water_demand.global_index] == 0.001114707341269841
    # Unused Power of Electrolyzer
    assert stsv.values[my_electrolyzer.unused_power.global_index] == 1600
    # Amount of Hydrogen that is stored in Hydrogen-Storage
    assert stsv.values[my_hydrogen_storage.storage_delta.global_index] == 0.0001248472222222222
