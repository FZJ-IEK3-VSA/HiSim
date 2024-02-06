"""Simple Car (LPG connected) and configuration.

Evaluates diesel or electricity consumption based on driven kilometers and processes Car Location for charging stations.
"""

# clean

import datetime as dt
import json
from dataclasses import dataclass
from os import listdir, path
# -*- coding: utf-8 -*-
from typing import List, Any, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import OpexCostDataClass
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = "development"


@dataclass_json
@dataclass
class CarConfig(cp.ConfigBase):
    """Definition of configuration of Car."""

    #: name of the car
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of fuel, either Electricity or Diesel
    fuel: lt.LoadTypes
    #: consumption per kilometer driven, either in kWh/km or l/km
    consumption_per_km: float
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime of car in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: consumption of the car in kWh or l
    consumption: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Car.get_full_classname()

    @classmethod
    def get_default_diesel_config(cls) -> Any:
        """Defines default configuration for diesel vehicle."""
        config = CarConfig(
            name="Car",
            source_weight=1,
            fuel=lt.LoadTypes.DIESEL,
            consumption_per_km=0.06,
            co2_footprint=9139.3,
            cost=32035.0,
            lifetime=18,
            maintenance_cost_as_percentage_of_investment=0.02,
            consumption=0,
        )
        return config

    @classmethod
    def get_default_ev_config(cls) -> Any:
        """Defines default configuration for electric vehicle."""
        config = CarConfig(
            name="Car",
            source_weight=1,
            fuel=lt.LoadTypes.ELECTRICITY,
            consumption_per_km=0.15,
            co2_footprint=8899.4,
            cost=44498.0,
            maintenance_cost_as_percentage_of_investment=0.02,
            lifetime=18,
            consumption=0,
        )
        return config


def most_frequent(input_list: List) -> Any:
    """Returns most frequent value - needed for down sampling Location information from 1 minute resoultion to lower."""
    counter = 0
    num = input_list[0]

    for i in input_list:
        curr_frequency = input_list.count(i)
        if curr_frequency > counter:
            counter = curr_frequency
            num = i
    return num


class Car(cp.Component):
    """Simulates car with constant consumption. Car usage (driven kilometers and state) orginate from LPG."""

    # Outputs
    FuelConsumption = "FuelConsumption"
    ElectricityOutput = "ElectricityOutput"
    CarLocation = "CarLocation"

    @staticmethod
    def get_cost_capex(config: CarConfig) -> Tuple[float, float, float]:
        """Returns investment cost, CO2 emissions and lifetime."""
        return config.cost, config.co2_footprint, config.lifetime

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CarConfig,
        occupancy_config: Any,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Initializes Car."""
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.build(config=config, occupancy_config=occupancy_config)

        if self.config.fuel == lt.LoadTypes.ELECTRICITY:
            self.electricity_output: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricityOutput,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                postprocessing_flag=[lt.ComponentType.CAR, lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL],
                output_description="Electricity Consumption of the car while driving. [W]",
            )
            self.car_location_output: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.CarLocation,
                load_type=lt.LoadTypes.ANY,
                unit=lt.Units.ANY,
                output_description="Location of the car as integer.",
            )
        elif self.config.fuel == lt.LoadTypes.DIESEL:
            self.fuel_consumption: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.FuelConsumption,
                load_type=lt.LoadTypes.DIESEL,
                unit=lt.Units.LITER,
                postprocessing_flag=[
                    lt.InandOutputType.FUEL_CONSUMPTION,
                    lt.LoadTypes.DIESEL,
                    lt.ComponentType.CAR,
                    lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                ],
                output_description="Diesel Consumption of the car while driving [l].",
            )

    def i_save_state(self) -> None:
        """Saves actual state."""
        pass

    def i_restore_state(self) -> None:
        """Restores previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Checks statements."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Returns consumption and location of car in each timestep."""

        if self.config.fuel == lt.LoadTypes.ELECTRICITY:
            watt_used = (
                self.meters_driven[timestep]
                * self.config.consumption_per_km
                * (3600 / self.my_simulation_parameters.seconds_per_timestep)
            )  # conversion Wh to W
            stsv.set_output_value(self.electricity_output, watt_used)
            stsv.set_output_value(self.car_location_output, self.car_location[timestep])

        # if not already running: check if activation makes sense
        elif self.config.fuel == lt.LoadTypes.DIESEL:
            liters_used = (
                self.meters_driven[timestep] * self.config.consumption_per_km * 1e-3
            )  # conversion meter to kilometer
            stsv.set_output_value(self.fuel_consumption, liters_used)

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        for index, output in enumerate(all_outputs):
            if output.component_name == self.config.name + "_w" + str(self.config.source_weight):
                if output.unit == lt.Units.LITER:
                    self.config.consumption = round(sum(postprocessing_results.iloc[:, index]), 1)
                    emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                        self.my_simulation_parameters.year
                    )
                    co2_per_unit = emissions_and_cost_factors.diesel_footprint_in_kg_per_l
                    euro_per_unit = emissions_and_cost_factors.diesel_costs_in_euro_per_l

                    opex_cost_per_simulated_period_in_euro = (
                        self.calc_maintenance_cost() + self.config.consumption * euro_per_unit
                    )
                    co2_per_simulated_period_in_kg = self.config.consumption * co2_per_unit

                elif output.unit == lt.Units.WATT:
                    self.config.consumption = round(
                        sum(postprocessing_results.iloc[:, index])
                        * self.my_simulation_parameters.seconds_per_timestep
                        / 3.6e6,
                        1,
                    )
                    # No electricity costs for components except for Electricity Meter, because part of electricity consumption is feed by PV
                    opex_cost_per_simulated_period_in_euro = self.calc_maintenance_cost()
                    co2_per_simulated_period_in_kg = 0.0

        opex_cost_data_class = OpexCostDataClass(
            opex_cost=opex_cost_per_simulated_period_in_euro,
            co2_footprint=co2_per_simulated_period_in_kg,
            consumption=self.config.consumption,
        )

        return opex_cost_data_class

    def build(self, config: CarConfig, occupancy_config: Any) -> None:
        """Loads necesary data and saves config to class."""
        self.config = config
        self.car_location = []
        self.meters_driven = []

        location_translator = {
            "School": 0,
            "Event Location": 0,
            "Shopping": 0,
            None: 0,
            "Home": 1,
            "Workplace": 2,
        }

        # check if caching is possible
        file_exists, cache_filepath = utils.get_cache_file(
            component_key=self.component_name,
            parameter_class=occupancy_config,
            my_simulation_parameters=self.my_simulation_parameters,
        )
        if file_exists:
            # load from cache
            dataframe = pd.read_csv(cache_filepath, sep=",", decimal=".", encoding="cp1252")
            self.car_location = dataframe["car_location"].tolist()
            self.meters_driven = dataframe["meters_driven"].tolist()
        else:
            # load car data from LPG output
            filepaths = listdir(utils.HISIMPATH["utsp_results"])
            filepath_location = [elem for elem in filepaths if "CarLocation." + self.config.name in elem][0]
            filepath_meters_driven = [elem for elem in filepaths if "DrivingDistance." + self.config.name in elem][0]
            with open(
                path.join(utils.HISIMPATH["utsp_results"], filepath_location),
                encoding="utf-8",
            ) as json_file:
                car_location = json.load(json_file)
            with open(
                path.join(utils.HISIMPATH["utsp_results"], filepath_meters_driven),
                encoding="utf-8",
            ) as json_file:
                meters_driven = json.load(json_file)

            # compare time resolution of LPG to time resolution of hisim
            time_resolution_original = dt.datetime.strptime(car_location["TimeResolution"], "%H:%M:%S")
            seconds_per_timestep_original = (
                time_resolution_original.hour * 3600
                + time_resolution_original.minute * 60
                + time_resolution_original.second
            )
            minutes_per_timestep = int(
                self.my_simulation_parameters.seconds_per_timestep / seconds_per_timestep_original
            )

            simulation_time_span = self.my_simulation_parameters.end_date - self.my_simulation_parameters.start_date
            minutes_per_timestep = int(self.my_simulation_parameters.seconds_per_timestep / 60)
            steps_desired = int(
                simulation_time_span.days * 24 * (3600 / self.my_simulation_parameters.seconds_per_timestep)
            )
            steps_desired_in_minutes = steps_desired * minutes_per_timestep

            # extract values for location and distance of car,
            # include time information and
            # translate car location to integers (according to location_translator)
            initial_data = pd.DataFrame(
                {
                    "Time": pd.date_range(
                        start=dt.datetime(year=self.my_simulation_parameters.year, month=1, day=1),
                        end=dt.datetime(year=self.my_simulation_parameters.year, month=1, day=1)
                        + dt.timedelta(days=simulation_time_span.days)
                        - dt.timedelta(seconds=60),
                        freq="T",
                    ),
                    "meters_driven": meters_driven["Values"][:steps_desired_in_minutes],
                    "car_location": [location_translator[elem] for elem in car_location["Values"]][
                        :steps_desired_in_minutes
                    ],
                }
            )
            initial_data = utils.convert_lpg_data_to_utc(data=initial_data, year=self.my_simulation_parameters.year)
            meters_driven = pd.to_numeric(initial_data["meters_driven"]).tolist()
            car_location = pd.to_numeric(initial_data["car_location"]).tolist()

            # sum / extract most common value from data to match hisim time resolution
            if minutes_per_timestep > 1:
                for i in range(steps_desired):
                    self.meters_driven.append(
                        sum(meters_driven[i * minutes_per_timestep : (i + 1) * minutes_per_timestep])
                    )  # sum
                    location_list = car_location[
                        i * minutes_per_timestep : (i + 1) * minutes_per_timestep
                    ]  # extract list
                    occurence_count = most_frequent(input_list=location_list)  # extract most common
                    self.car_location.append(occurence_count)
            else:
                self.meters_driven = meters_driven
                self.car_location = car_location

            # save data in cache
            database = pd.DataFrame(
                {
                    "car_location": self.car_location,
                    "meters_driven": self.meters_driven,
                }
            )
            database.to_csv(cache_filepath)
            del database

    def write_to_report(self) -> List[str]:
        """Writes Car values to report."""
        return self.config.get_string_dict()
