from hisim import component as cp
#import components as cps
#import components
from hisim.components import generic_electrolyzer_and_h2_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log

import os
from hisim import utils
import numpy as np

def test_hydrogen_generator():

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017,seconds_per_timestep)

    # HydrogenStorageConfig
    min_capacity = 0  # [kg_H2]
    max_capacity = 500  # [kg_H2]
    starting_fill = 0  # [kg_H2]
    max_charging_rate_hour = 2  # [kg/h]
    max_discharging_rate_hour = 2  # [kg/h]
    max_charging_rate = max_charging_rate_hour / 3600
    max_discharging_rate = max_discharging_rate_hour / 3600
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
    min_hydrogen_production_rate = min_hydrogen_production_rate_hour / 3600  # [Nl/s]
    max_hydrogen_production_rate = max_hydrogen_production_rate_hour / 3600   # [Nl/s]
    pressure_hydrogen_output = 30       # [bar]     --> max pressure mode at 35 bar

    number_of_outputs = 18
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    #===================================================================================================================
    # Set Hydrogen Generator
    my_electrolyzer = generic_electrolyzer_and_h2_storage.Electrolyzer(
                                   my_simulation_parameters=my_simulation_parameters)
    my_hydrogen_storage = generic_electrolyzer_and_h2_storage.HydrogenStorage(
                                   my_simulation_parameters=my_simulation_parameters)

    # Set Fake Outputs for Gas Heater
    I_0 = cp.ComponentOutput("FakeElectricityInput",
                             "ElectricityInput",
                             lt.LoadTypes.Electricity,
                             lt.Units.Watt)
    I_1 = cp.ComponentOutput("FakeElectricityInput",
                             "ElectricityInput",
                             lt.LoadTypes.Hydrogen,
                             lt.Units.kg)
    I_2 = cp.ComponentOutput("DischargingHydrogenAmountTarget",
                             "DischargingHydrogenAmountTarget",
                             lt.LoadTypes.Hydrogen,
                             lt.Units.kg_per_sec)


    my_electrolyzer.electricity_input.SourceOutput = I_0
    my_electrolyzer.hydrogen_not_stored.SourceOutput = I_1
    my_hydrogen_storage.charging_hydrogen.SourceOutput = my_electrolyzer.hydrogen_output
    my_hydrogen_storage.discharging_hydrogen.SourceOutput = I_2

    # Link inputs and outputs
    I_0.GlobalIndex = 0
    I_1.GlobalIndex = 1
    I_2.GlobalIndex = 2
    stsv.values[0] = 4000
    stsv.values[1] = 0
    stsv.values[2] = 0

    my_electrolyzer.water_demand.GlobalIndex = 3
    my_electrolyzer.hydrogen_output.GlobalIndex = 4
    my_electrolyzer.oxygen_output.GlobalIndex = 5
    my_electrolyzer.energy_losses.GlobalIndex = 6
    my_electrolyzer.unused_power.GlobalIndex = 7
    my_electrolyzer.electricity_real_needed.GlobalIndex = 8
    my_electrolyzer.electrolyzer_efficiency.GlobalIndex = 9
    my_electrolyzer.power_level.GlobalIndex = 10

    my_hydrogen_storage.current_fill.GlobalIndex = 11
    my_hydrogen_storage.current_fill_percent.GlobalIndex = 12
    my_hydrogen_storage.storage_delta.GlobalIndex = 13
    my_hydrogen_storage.hydrogen_not_stored.GlobalIndex = 14
    my_hydrogen_storage.hydrogen_not_released.GlobalIndex = 15
    my_hydrogen_storage.hydrogen_storage_energy_demand.GlobalIndex = 16
    my_hydrogen_storage.hydrogen_losses.GlobalIndex = 17
    my_hydrogen_storage.discharging_hydrogen_real.GlobalIndex = 18

    j = 1000

    # Simulate
    my_electrolyzer.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))

    my_hydrogen_storage.i_simulate(j, stsv,  False)
    log.information(str(stsv.values))
    # Check if the delivered electricity indeed that corresponded to the battery model
    assert stsv.values[3] ==  0.001114707341269841
    assert stsv.values[7] ==  1600
    assert stsv.values[13] == 0.0001248472222222222