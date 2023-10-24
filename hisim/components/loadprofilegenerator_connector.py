"""LoadProfile Generator Connector Module."""

# Generic/Built-in
import json
from typing import Any, Tuple
from os import path, makedirs
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd
import datetime as dt
import numpy as np

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim import log
from hisim.simulationparameters import SimulationParameters
from hisim.sim_repository_singleton import SingletonDictKeyEnum, SingletonSimRepository

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class OccupancyConfig(cp.ConfigBase):
    @classmethod
    def get_main_classname(cls):
        """Returns the fully qualified class name for the class that is getting configured. Used for Json."""
        return Occupancy.get_full_classname()

    name: str
    profile_name: str
    country_name: str
    profile_with_washing_machine_and_dishwasher: bool
    number_of_apartments: float
    predictive: bool
    predictive_control: bool

    @classmethod
    def get_default_CHS01(cls) -> Any:
        config = OccupancyConfig(
            name="Occupancy_1",
            profile_name="CHR01 Couple both at Work",
            country_name="DE",
            profile_with_washing_machine_and_dishwasher=True,
            number_of_apartments=1,
            predictive=False,
            predictive_control=False,
        )
        return config

    @classmethod
    def get_scaled_CHS01_according_to_number_of_apartments(
        cls, number_of_apartments: float
    ) -> Any:
        config = OccupancyConfig(
            name="Occupancy_1",
            profile_name="CHR01 Couple both at Work",
            country_name="DE",
            profile_with_washing_machine_and_dishwasher=True,
            number_of_apartments=number_of_apartments,
            predictive=False,
            predictive_control=False,
        )
        return config

    def get_factors_from_country_and_profile(self) -> Tuple[float, float]:
        """Evaluates country specific scaling factors of profiles.

        This is especially relevant when european average profile (AVG) is used.
        """
        if self.profile_name != "AVG":
            return (1, 1)
        scaling_factors = pd.read_csv(
            utils.HISIMPATH["occupancy_scaling_factors_per_country"],
            encoding="utf-8",
            sep=";",
            index_col=1,
        )
        if self.country_name in scaling_factors.index:
            scaling_factor_line = scaling_factors.loc[self.country_name]
        else:
            scaling_factor_line = scaling_factors.loc["EU"]
            log.warning(
                "Scaling Factor for "
                + self.country_name
                + "is not available, EU average is used per default."
            )
        factor_electricity_consumption = float(
            scaling_factor_line["Unit consumption per dwelling for cooking (toe/dw)"]
        ) * 1.163e4 + float(
            scaling_factor_line[
                "Unit consumption per dwelling for lighting and electrical appliances (kWh/dw)"
            ]
        )  # 1 toe = 1.163e4 kWh
        factor_hot_water_consumption = (
            float(
                scaling_factor_line[
                    "Unit consumption of hot water per dwelling (toe/dw)"
                ]
            )
            * 4.1868e7
            / ((40 - 10) * 0.977 * 4.182)
        )  # 1 toe = 4.1868e7 kJ, than Joule to liter with given temperature difference
        return (factor_electricity_consumption, factor_hot_water_consumption)


class Occupancy(cp.Component):

    """
    Class component that provides heating generated, the electricity consumed by the residents.

    Data provided or based on LPG exports.

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
    WW_MassInput = "Warm Water Mass Input"  # kg/s
    WW_TemperatureInput = "Warm Water Temperature Input"  # °C

    # Outputs
    # output
    # WW_MassOutput = "Mass Output"  # kg/s
    # WW_TemperatureOutput = "Temperature Output"  # °C
    # EnergyDischarged = "Energy Discharged"  # W
    # DemandSatisfied = "Demand Satisfied"  # 0 or 1

    NumberByResidents = "NumberByResidents"
    HeatingByResidents = "HeatingByResidents"
    HeatingByDevices = "HeatingByDevices"
    ElectricityOutput = "ElectricityOutput"
    WaterConsumption = "WaterConsumption"

    Electricity_Demand_Forecast_24h = "Electricity_Demand_Forecast_24h"

    # Similar components to connect to:
    # None
    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: OccupancyConfig
    ) -> None:
        super().__init__(
            name="Occupancy",
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.profile_name = config.profile_name
        self.occupancy_config = config

        if self.my_simulation_parameters.year != 2021:
            raise Exception(
                "LPG data is only available for 2021, if other years are needed, "
                + "use loadprofilegenerator_utsp_connector instead."
            )
        self.build()

        real_number_of_apartments_from_building = (
            self.occupancy_config.number_of_apartments
        )

        self.scaling_factor_according_to_number_of_apartments = (
            self.get_scaling_factor_according_to_number_of_apartments(
                real_number_of_apartments=real_number_of_apartments_from_building
            )
        )
        # Inputs - Not Mandatories
        self.ww_mass_input: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WW_MassInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.KG_PER_SEC,
            False,
        )
        self.ww_temperature_input: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WW_TemperatureInput,
            lt.LoadTypes.WARM_WATER,
            lt.Units.CELSIUS,
            False,
        )

        # Outputs
        # self.ww_mass_output: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                       self.WW_MassOutput,
        #                                                       lt.LoadTypes.WarmWater, lt.Units.kg_per_sec)
        # self.ww_temperature_output: cp.ComponentOutput = self.add_output(self.ComponentName,
        #                                                              self.WW_TemperatureOutput,
        #                                                              lt.LoadTypes.WarmWater,
        #                                                              lt.Units.Celsius)

        # self.energy_discharged: cp.ComponentOutput = self.add_output(self.ComponentName, self.EnergyDischarged, lt.LoadTypes.WarmWater, lt.Units.Watt)
        # self.demand_satisfied: cp.ComponentOutput = self.add_output(self.ComponentName, self.DemandSatisfied, lt.LoadTypes.WarmWater, lt.Units.Any)

        self.number_of_residentsC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.NumberByResidents,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for {self.NumberByResidents} will follow.",
        )
        self.heating_by_residentsC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingByResidents,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for {self.HeatingByResidents} will follow.",
        )
        self.heating_by_devices_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingByDevices,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="Inner device heat gains, which heat the building (not intentionally)",
        )
        self.electricity_outputC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
            ],
            output_description=f"here a description for Occupancy {self.ElectricityOutput} will follow.",
        )

        self.water_consumptionC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterConsumption,
            lt.LoadTypes.WARM_WATER,
            lt.Units.LITER,
            output_description=f"here a description for {self.WaterConsumption} will follow.",
        )

    def i_save_state(self) -> None:
        pass

    def i_restore_state(self) -> None:
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        # if self.ww_mass_input.source_output is not None:
        # ww demand
        # ww_temperature_demand = HouseholdWarmWaterDemandConfig.ww_temperature_demand

        # From Thermal Energy Storage
        # ww_mass_input_per_sec = stsv.get_input_value(self.ww_mass_input)  # kg/s
        # ww_mass_input = ww_mass_input_per_sec * self.seconds_per_timestep           # kg
        # ww_mass_input: float = ww_mass_input_per_sec
        # ww_temperature_input = stsv.get_input_value(self.ww_temperature_input)  # °C

        # Information import
        # freshwater_temperature = (HouseholdWarmWaterDemandConfig.freshwater_temperature)
        # temperature_difference_hot = (HouseholdWarmWaterDemandConfig.temperature_difference_hot)  # Grädigkeit
        # temperature_difference_cold = (HouseholdWarmWaterDemandConfig.temperature_difference_cold)
        # energy_losses_watt = HouseholdWarmWaterDemandConfig.heat_exchanger_losses
        # energy_losses = energy_losses_watt * self.seconds_per_timestep
        # energy_losses = 0
        # specific_heat = 4180 / 3600

        # ww_energy_demand = (specific_heat * self.water_consumption[timestep] * (ww_temperature_demand - freshwater_temperature))

        # if (ww_temperature_input > (ww_temperature_demand + temperature_difference_hot) or ww_energy_demand == 0):
        #     demand_satisfied = 1
        # else:
        #     demand_satisfied = 0

        # if ww_energy_demand > 0 and (ww_mass_input == 0 and ww_temperature_input == 0):
        # """first iteration --> random numbers"""
        # ww_temperature_input = 40.45
        # ww_mass_input = 9.3

        # """
        # Warm water is provided by the warmwater stoage.
        # The household needs water at a certain temperature. To get the correct temperature the amount of water from
        # the wws is regulated and is depending on the temperature provided by the wws. The backflowing water to wws
        # is cooled down to the temperature of (freshwater+temperature_difference_cold) --> ww_temperature_output.
        # """
        # if ww_energy_demand > 0:
        #     # heating up the freshwater. The mass is consistent
        #     energy_discharged = ww_energy_demand + energy_losses
        #     ww_temperature_output: float = (freshwater_temperature + temperature_difference_cold)
        #     # ww_mass_input = energy_discharged / (PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin \
        #     # * (ww_temperature_input - ww_temperature_output))
        # else:
        #     ww_temperature_output = ww_temperature_input
        #     # ww_mass_input = 0
        #     # energy_discharged = 0

        # ww_mass_output = ww_mass_input

        # stsv.set_output_value(self.ww_mass_output, ww_mass_output)  # stsv.set_output_value(self.ww_temperature_output, ww_temperature_output)
        # stsv.set_output_value(self.demand_satisfied, demand_satisfied)  # stsv.set_output_value(self.energy_discharged, energy_discharged)

        stsv.set_output_value(
            self.number_of_residentsC,
            self.number_of_residents[timestep]
            * self.scaling_factor_according_to_number_of_apartments,
        )

        stsv.set_output_value(
            self.heating_by_residentsC,
            self.heating_by_residents[timestep]
            * self.scaling_factor_according_to_number_of_apartments,
        )
        stsv.set_output_value(
            self.heating_by_devices_channel,
            self.heating_by_devices[timestep]
            * self.scaling_factor_according_to_number_of_apartments,
        )
        stsv.set_output_value(
            self.electricity_outputC,
            self.electricity_consumption[timestep]
            * self.scaling_factor_according_to_number_of_apartments,
        )
        stsv.set_output_value(
            self.water_consumptionC,
            self.water_consumption[timestep]
            * self.scaling_factor_according_to_number_of_apartments,
        )

        if self.occupancy_config.predictive_control:
            last_forecast_timestep = int(
                timestep
                + 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            )
            if last_forecast_timestep > len(self.electricity_consumption):
                last_forecast_timestep = len(self.electricity_consumption)

            demandforecast = self.electricity_consumption[
                timestep:last_forecast_timestep
            ]
            self.simulation_repository.set_entry(
                self.Electricity_Demand_Forecast_24h, demandforecast
            )

        if self.occupancy_config.predictive_control:
            last_forecast_timestep = int(
                timestep
                + 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            )
            if last_forecast_timestep > len(self.electricity_consumption):
                last_forecast_timestep = len(self.electricity_consumption)

            demandforecast = self.electricity_consumption[
                timestep:last_forecast_timestep
            ]
            self.simulation_repository.set_entry(
                self.Electricity_Demand_Forecast_24h, demandforecast
            )

    def build(self):
        """Loads relevant consumption data (electricity consumption, hot water consumption and activity profiles of persons).
        Also does the averaging to the desired time resolution conversion to the output format desired."""
        file_exists, cache_filepath = utils.get_cache_file(
            component_key=self.component_name,
            parameter_class=self.occupancy_config,
            my_simulation_parameters=self.my_simulation_parameters,
        )

        # create directories to put in files for cars and smart devices
        for tag in ["utsp_reports", "utsp_results"]:
            isExist = path.exists(utils.HISIMPATH[tag])
            if not isExist:
                # Create a new directory because it does not exist
                makedirs(utils.HISIMPATH[tag])

        if file_exists:
            dataframe = pd.read_csv(
                cache_filepath, sep=",", decimal=".", encoding="cp1252"
            )
            self.number_of_residents = dataframe["number_of_residents"].tolist()
            self.heating_by_residents = dataframe["heating_by_residents"].tolist()
            self.heating_by_devices = dataframe["heating_by_devices"].tolist()
            self.electricity_consumption = dataframe["electricity_consumption"].tolist()
            self.water_consumption = dataframe["water_consumption"].tolist()

        else:
            ################################
            # Calculates heating generated by residents and loads number of residents
            # Heat power generated per resident in W
            # mode 1: awake
            # mode 2: sleeping
            gain_per_person = [150, 100]

            (
                scaling_electricity_consumption,
                scaling_water_consumption,
            ) = self.occupancy_config.get_factors_from_country_and_profile()
            # load occupancy profile
            occupancy_profile = []
            filepaths = utils.HISIMPATH["occupancy"][self.profile_name][
                "number_of_residents"
            ]
            for filepath in filepaths:
                with open(filepath, encoding="utf-8") as json_file:
                    json_filex = json.load(json_file)
                occupancy_profile.append(json_filex)

            # see how long csv files from LPG are to check if averaging has to be done and calculate desired length
            simulation_time_span = (
                self.my_simulation_parameters.end_date
                - self.my_simulation_parameters.start_date
            )
            minutes_per_timestep = int(
                self.my_simulation_parameters.seconds_per_timestep / 60
            )
            steps_desired = int(
                simulation_time_span.days
                * 24
                * (3600 / self.my_simulation_parameters.seconds_per_timestep)
            )
            steps_desired_in_minutes = steps_desired * minutes_per_timestep

            # initialize number of residence and heating by residents
            heating_by_residents = [0] * steps_desired_in_minutes
            number_of_residents = [0] * steps_desired_in_minutes

            # compute heat gains and number of persons
            for mode, gain in enumerate(gain_per_person):
                for timestep in range(steps_desired_in_minutes):
                    number_of_residents[timestep] += occupancy_profile[mode]["Values"][
                        timestep
                    ]
                    heating_by_residents[timestep] = (
                        heating_by_residents[timestep]
                        + gain * occupancy_profile[mode]["Values"][timestep]
                    )

            if self.occupancy_config.profile_with_washing_machine_and_dishwasher:
                profile_path = utils.HISIMPATH["occupancy"][self.profile_name][
                    "electricity_consumption"
                ]
            else:
                profile_path = utils.HISIMPATH["occupancy"][self.profile_name][
                    "electricity_consumption_without_washing_machine_and_dishwasher"
                ]

            # load electricity consumption and water consumption
            pre_electricity_consumption = pd.read_csv(
                profile_path,
                sep=";",
                decimal=".",
                encoding="utf-8",
                usecols=["Sum [kWh]"],
            ).loc[: (steps_desired_in_minutes - 1)]
            electricity_consumption = pd.to_numeric(
                pre_electricity_consumption.loc[:, "Sum [kWh]"]
                * 1000
                * 60
                * scaling_electricity_consumption
            ).tolist()  # 1 kWh/min == 60 000 W / min

            pre_water_consumption = pd.read_csv(
                utils.HISIMPATH["occupancy"][self.profile_name]["water_consumption"],
                sep=";",
                decimal=".",
                encoding="utf-8",
                usecols=["Sum [L]"],
            ).loc[: (steps_desired_in_minutes - 1)]
            water_consumption = pd.to_numeric(
                pre_water_consumption.loc[:, "Sum [L]"] * scaling_water_consumption
            ).tolist()

            pre_heating_by_devices = pd.read_csv(
                utils.HISIMPATH["occupancy"][self.profile_name]["heating_by_devices"],
                sep=";",
                decimal=".",
                encoding="utf-8",
                usecols=["Time", "Sum [kWh]"],
            ).loc[: (steps_desired_in_minutes - 1)]
            heating_by_devices = pd.to_numeric(
                pre_heating_by_devices.loc[:, "Sum [kWh]"] * 1000 * 60
            ).tolist()  # 1 kWh/min == 60W / min

            # convert heat gains and number of persons to data frame and evaluate
            initial_data = pd.DataFrame(
                {
                    "Time": pd.date_range(
                        start=dt.datetime(
                            year=self.my_simulation_parameters.year, month=1, day=1
                        ),
                        end=dt.datetime(
                            year=self.my_simulation_parameters.year, month=1, day=1
                        )
                        + dt.timedelta(days=simulation_time_span.days)
                        - dt.timedelta(seconds=60),
                        freq="T",
                    ),
                    "number_of_residents": number_of_residents,
                    "heating_by_residents": heating_by_residents,
                    "electricity_consumption": electricity_consumption,
                    "water_consumption": water_consumption,
                    "heating_by_devices": heating_by_devices,
                }
            )
            initial_data = utils.convert_lpg_data_to_utc(
                data=initial_data, year=self.my_simulation_parameters.year
            )

            # extract everything from data frame
            self.electricity_consumption = initial_data[
                "electricity_consumption"
            ].tolist()
            self.heating_by_residents = initial_data["heating_by_residents"].tolist()
            self.number_of_residents = initial_data["number_of_residents"].tolist()
            self.water_consumption = initial_data["water_consumption"].tolist()
            self.heating_by_devices = initial_data["heating_by_devices"].tolist()

            # average data, when time resolution of inputs is coarser than time resolution of simulation
            if minutes_per_timestep > 1:
                # power needs averaging, not sum
                self.electricity_consumption = [
                    sum(self.electricity_consumption[n : n + minutes_per_timestep])
                    / minutes_per_timestep
                    for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
                ]
                self.heating_by_devices = [
                    sum(self.heating_by_devices[n : n + minutes_per_timestep])
                    / minutes_per_timestep
                    for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
                ]
                self.water_consumption = [
                    sum(self.water_consumption[n : n + minutes_per_timestep])
                    for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
                ]
                self.heating_by_residents = [
                    sum(self.heating_by_residents[n : n + minutes_per_timestep])
                    / minutes_per_timestep
                    for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
                ]
                self.number_of_residents = [
                    sum(self.number_of_residents[n : n + minutes_per_timestep])
                    / minutes_per_timestep
                    for n in range(0, steps_desired_in_minutes, minutes_per_timestep)
                ]

            # Saves data in cache
            database = pd.DataFrame(
                {
                    "number_of_residents": self.number_of_residents,
                    "heating_by_residents": self.heating_by_residents,
                    "electricity_consumption": self.electricity_consumption,
                    "water_consumption": self.water_consumption,
                    "heating_by_devices": self.heating_by_devices,
                }
            )

            database.to_csv(cache_filepath)
            del database  # utils.save_cache("Occupancy", parameters, database)

        if self.occupancy_config.predictive:
            SingletonSimRepository().set_entry(
                key=SingletonDictKeyEnum.heating_by_residents_yearly_forecast,
                entry=self.heating_by_residents
            )

        self.max_hot_water_demand = max(self.water_consumption)

    def write_to_report(self):
        """Writes a report."""
        return self.occupancy_config.get_string_dict()

    def get_scaling_factor_according_to_number_of_apartments(
        self, real_number_of_apartments: float
    ) -> float:
        """Get scaling factor according to the real number of apartments which is given by the building component."""

        if real_number_of_apartments is not None and real_number_of_apartments > 0:
            scaling_factor = real_number_of_apartments
            log.information(
                f"Occupancy outputs will be scaled with the factor {scaling_factor} according to the number of apartments"
            )

        else:
            scaling_factor = 1.0

        return scaling_factor
