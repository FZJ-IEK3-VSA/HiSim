# Generic/Built-in
import pandas as pd
import json
import numpy as np

# Owned
import component as cp
import loadtypes as lt
import globals

from components.configuration import HouseholdWarmWaterDemandConfig
from components.configuration import PhysicsConfig

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class Occupancy(cp.Component):
    """
    Class component that provides heating generated, the electricity consumed
    by the residents. Data provided or based on LPG exports.

    Parameters
    -----------------------------------------------
    profile: string
        profile code corresponded to the family or residents configuration

    ComponentInputs:
    -----------------------------------------------
       None

    ComponentOutputs:
    -----------------------------------------------
       Number of Residents: Any
       Heating by Residents: W
       Electricity Consumption: kWh
       Water Consumption: L
    """
    # Inputs
    WW_MassInput = "Warm Water Mass Input"                  # kg/s
    WW_TemperatureInput = "Warm Water Temperature Input"    # °C

    # Outputs
    # output
    WW_MassOutput = "Mass Output"                           # kg/s
    WW_TemperatureOutput = "Temperature Output"             # °C
    EnergyDischarged = "Energy Discharged"                          # W
    DemandSatisfied = "Demand Satisfied"                    # 0 or 1

    NumberByResidents = "NumberByResidents"
    HeatingByResidents = "HeatingByResidents"
    ElectricityOutput = "ElectricityOutput"
    WaterConsumption = "WaterConsumption"

    # Similar components to connect to:
    # None

    def __init__(self,
                 profile="CH01"):
        super().__init__(name="Occupancy")

        self.build(profile=profile)

        # Inputs - Not Mandatories
        self.ww_mass_input: cp.ComponentInput = self.add_input(self.ComponentName,
                                                            self.WW_MassInput,
                                                            lt.LoadTypes.WarmWater,
                                                            lt.Units.kg_per_sec,
                                                            False)
        self.ww_temperature_input: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                   self.WW_TemperatureInput,
                                                                   lt.LoadTypes.WarmWater,
                                                                   lt.Units.Celsius,
                                                                   False)

        # Outputs
        #self.ww_mass_output: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                       self.WW_MassOutput,
        #                                                       lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        #self.ww_temperature_output: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                              self.WW_TemperatureOutput,
        #                                                              lt.LoadTypes.WarmWater,
        #                                                              lt.Units.Celsius)

        #self.energy_discharged: cp.ComponentOutput = self.add_output(self.ComponentName, self.EnergyDischarged, lt.LoadTypes.WarmWater, lt.Units.Watt)
        #self.demand_satisfied: cp.ComponentOutput = self.add_output(self.ComponentName, self.DemandSatisfied, lt.LoadTypes.WarmWater, lt.Units.Any)

        self.number_of_residentsC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                     self.NumberByResidents,
                                                                     lt.LoadTypes.Any,
                                                                     lt.Units.Any)
        self.heating_by_residentsC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                      self.HeatingByResidents,
                                                                      lt.LoadTypes.Heating,
                                                                      lt.Units.Watt)
        self.electricity_outputC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                         self.ElectricityOutput,
                                                                         lt.LoadTypes.Electricity,
                                                                         lt.Units.Watt,
                                                                         True)

        self.water_consumptionC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                   self.WaterConsumption,
                                                                   lt.LoadTypes.WarmWater,
                                                                   lt.Units.Liter)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_conversion: bool):
        if self.ww_mass_input.SourceOutput is not None:
            # ww demand
            ww_temperature_demand = HouseholdWarmWaterDemandConfig.ww_temperature_demand

            # From Thermal Energy Storage
            ww_mass_input_per_sec = stsv.get_input_value(self.ww_mass_input)            # kg/s
            #ww_mass_input = ww_mass_input_per_sec * self.seconds_per_timestep           # kg
            ww_mass_input = ww_mass_input_per_sec
            ww_temperature_input = stsv.get_input_value(self.ww_temperature_input)      # °C

            # Information import
            freshwater_temperature = HouseholdWarmWaterDemandConfig.freshwater_temperature
            temperature_difference_hot = HouseholdWarmWaterDemandConfig.temperature_difference_hot      # Grädigkeit
            temperature_difference_cold = HouseholdWarmWaterDemandConfig.temperature_difference_cold
            energy_losses_watt = HouseholdWarmWaterDemandConfig.heat_exchanger_losses
            #energy_losses = energy_losses_watt * self.seconds_per_timestep
            energy_losses = 0
            specific_heat = 4180/3600

            ww_energy_demand = specific_heat * \
                                      self.water_consumption[timestep] * \
                                      (ww_temperature_demand - freshwater_temperature)

            if ww_temperature_input > (ww_temperature_demand + temperature_difference_hot) or ww_energy_demand == 0:
                demand_satisfied = 1
            else:
                demand_satisfied = 0

            if ww_energy_demand > 0 and (ww_mass_input == 0 and ww_temperature_input == 0):
                """first iteration --> random numbers"""
                ww_temperature_input = 40.45
                ww_mass_input = 9.3

            """
            Warm water is provided by the warmwater stoage.
            The household needs water at a certain temperature. To get the correct temperature the amount of water from
            the wws is regulated and is depending on the temperature provided by the wws. The backflowing water to wws
            is cooled down to the temperature of (freshwater+temperature_difference_cold) --> ww_temperature_output.
            """
            if ww_energy_demand > 0:
                # heating up the freshwater. The mass is consistent
                energy_discharged = ww_energy_demand + energy_losses
                ww_temperature_output = freshwater_temperature + temperature_difference_cold
                ww_mass_input = energy_discharged / (PhysicsConfig.water_specific_heat_capacity * (ww_temperature_input - ww_temperature_output))
            else:
                ww_temperature_output = ww_temperature_input
                ww_mass_input = 0
                energy_discharged = 0

            ww_mass_output = ww_mass_input

            #stsv.set_output_value(self.ww_mass_output, ww_mass_output)
            #stsv.set_output_value(self.ww_temperature_output, ww_temperature_output)
            #stsv.set_output_value(self.demand_satisfied, demand_satisfied)
            #stsv.set_output_value(self.energy_discharged, energy_discharged)


        stsv.set_output_value(self.number_of_residentsC, self.number_of_residents[timestep])
        stsv.set_output_value(self.heating_by_residentsC, self.heating_by_residents[timestep])
        stsv.set_output_value(self.electricity_outputC, self.electricity_consumption[timestep])
        stsv.set_output_value(self.water_consumptionC, self.water_consumption[timestep])

    def build(self, profile):
        self.profile = profile
        parameters = [profile]

        cache_filepath = globals.get_cache(classname="Occupancy", parameters=parameters)
        if cache_filepath is not None:
            self.number_of_residents = pd.read_csv(cache_filepath, sep=',', decimal='.', encoding = "cp1252")[
                'number_of_residents'].tolist()
            self.heating_by_residents = pd.read_csv(cache_filepath, sep=',', decimal='.', encoding = "cp1252")[
                'heating_by_residents'].tolist()
            self.electricity_consumption = pd.read_csv(cache_filepath, sep=',', decimal='.', encoding = "cp1252")[
                'electricity_consumption'].tolist()
            self.water_consumption = pd.read_csv(cache_filepath, sep=',', decimal='.', encoding = "cp1252")[
                'water_consumption'].tolist()
        else:
            ################################
            # Calculates heating generated by residents and loads number of residents
            # Heat power generated per resident in W
            # mode 1: awake
            # mode 2: sleeping
            gain_per_person = [150, 100]

            occupancy_profile = []
            filepaths = globals.HISIMPATH["occupancy"][profile]['number_of_residents']
            for filepath in filepaths:
                with open(filepath) as json_file:
                    json_filex = json.load(json_file)
                occupancy_profile.append(json_filex)

            self.heating_by_residents = [0] * len(occupancy_profile[0]['Values'])
            self.number_of_residents = [0] * len(occupancy_profile[0]['Values'])
            for mode in range(len(gain_per_person)):
                for timestep in range(len(occupancy_profile[0]['Values'])):
                    self.number_of_residents[timestep] += occupancy_profile[mode]['Values'][timestep]
                    self.heating_by_residents[timestep] = self.heating_by_residents[timestep] + \
                                                          gain_per_person[mode] * occupancy_profile[mode]['Values'][
                                                              timestep]

            # Loads electricity consumption
            pre_electricity_consumption = pd.read_csv(globals.HISIMPATH["occupancy"][profile]["electricity_consumption"],
                                                      sep = ";", decimal = ",", encoding = "cp1252")
            self.electricity_consumption = pd.to_numeric(pre_electricity_consumption["Sum [kWh]"] * 1000).tolist()


            # Loads water consumption
            pre_water_consumption = pd.read_csv(globals.HISIMPATH["occupancy"][profile]["water_consumption"],
                                                sep=";", decimal=",", encoding = "cp1252")
            self.water_consumption = pd.to_numeric(pre_water_consumption["Sum [L]"]).tolist()

            # Saves data in cache
            data = np.transpose([self.number_of_residents,
                                 self.heating_by_residents,
                                 self.electricity_consumption,
                                 self.water_consumption])
            database = pd.DataFrame(data, columns=['number_of_residents',
                                                   'heating_by_residents',
                                                   'electricity_consumption',
                                                   'water_consumption'])
            globals.save_cache("Occupancy", parameters, database)

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.ComponentName))
        lines.append("Profile: {}".format(self.profile))
        return lines
