"""Simple Car (LPG connected) and configuration.

Evaluates diesel or electricity consumption based on driven kilometers and processes Car Location for charging stations.
"""

# clean

import datetime as dt
from dataclasses import dataclass

# -*- coding: utf-8 -*-
from typing import List, Any, Tuple, Dict
import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils, log
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass

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
class GenericCarInformation:

    """Class for collecting important generic car parameters from occupancy."""

    def __init__(self, my_occupancy_instance: UtspLpgConnector):
        """Initialize the class."""

        self.my_occupancy_instance = my_occupancy_instance
        self.build(my_occupancy_instance=my_occupancy_instance)
        self.data_dict_for_car_component = self.prepare_data_dict_for_car_component(
            car_names=self.car_names,
            household_names=self.household_names,
            time_resolutions=self.time_resolutions,
            car_locations=self.car_location_value_list,
            driven_meters=self.driven_meters,
        )

    def build(self, my_occupancy_instance: UtspLpgConnector) -> None:
        """Get important values from occupancy instance."""
        # get names of all available cars
        car_data_dict = my_occupancy_instance.car_data_dict
        if all(isinstance(value_list, list) and all(not bool(car_info_dict) for car_info_dict in value_list) for value_list in car_data_dict.values()):
            raise ValueError("The car data from occupancy contains only empty dictionaries in its value lists. "
                             "If you are using the predefined occupancy profile, no car data is currently available. ")

        # get car names and household names
        (
            self.car_names,
            self.household_names,
            self.time_resolutions,
            self.car_location_value_list,
        ) = self.get_important_parameters_from_occupancy_car_data(car_data_dict=car_data_dict)

        # get driven meters
        self.driven_meters = self.get_meters_driven_from_occupancy_car_data(car_data_dict=car_data_dict)

    def get_important_parameters_from_occupancy_car_data(self, car_data_dict: Dict) -> Tuple[List, List, List, List]:
        """Get car names and household names from occupancy car data."""
        car_location_list = car_data_dict["car_locations"]

        car_names = []
        household_names = []
        time_resolutions = []
        car_location_value_list = []
        for car_location in car_location_list:
            # get car names
            car_name = (
                car_location["LoadTypeName"]
                .split(" - ")[1]
                .translate(str.maketrans({" ": "_", ",": "", "/": "", ".": ""}))
            )
            car_names.append(car_name)

            # get household names
            household_key_dict = car_location["HouseKey"]
            household_name = household_key_dict["HouseholdName"].translate(
                str.maketrans({" ": "_", ",": "", "/": "", ".": ""})
            )
            household_names.append(household_name)

            # get time resolutions
            time_resolution = car_location["TimeResolution"]
            time_resolutions.append(time_resolution)

            # get car location values
            car_location_values = car_location["Values"]
            car_location_value_list.append(car_location_values)

        return car_names, household_names, time_resolutions, car_location_value_list

    def get_meters_driven_from_occupancy_car_data(self, car_data_dict: Dict) -> List:
        """Get meters driven from occupancy car data."""
        driven_distances_list = car_data_dict["driving_distances"]
        driven_meter_list = []
        for driven_distance_data in driven_distances_list:
            driven_meter_values = driven_distance_data["Values"]
            driven_meter_list.append(driven_meter_values)

        return driven_meter_list

    def prepare_data_dict_for_car_component(
        self, car_names: List, household_names: List, time_resolutions: List, car_locations: List, driven_meters: List
    ) -> Dict:
        """Prepare data for car component."""
        data_dict_for_car_component: Dict = {}
        for index, household_name in enumerate(household_names):
            data_dict_for_car_component.update(
                {
                    household_name: {
                        "car_name": car_names[index],
                        "household_name": household_name,
                        "time_resolution": time_resolutions[index],
                        "car_location": car_locations[index],
                        "driven_meters": driven_meters[index],
                    }
                }
            )
        return data_dict_for_car_component


@dataclass_json
@dataclass
class CarConfig(cp.ConfigBase):
    """Definition of configuration of Car."""

    building_name: str
    #: name of the car
    name: str
    #: priority of the component in hierachy: the higher the number the lower the priority
    source_weight: int
    #: type of fuel, either Electricity or Diesel
    fuel: lt.LoadTypes
    #: consumption per kilometer driven, either in kWh/km or l/km
    consumption_per_km: float
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: float
    #: cost for investment in Euro
    investment_costs_in_euro: float
    #: lifetime of car in years
    lifetime_in_years: float
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: float
    #: consumption of the car in kWh or l

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Car.get_full_classname()

    @classmethod
    def get_default_diesel_config(
        cls,
        name: str = "Car",
        building_name: str = "BUI1",
    ) -> Any:
        """Defines default configuration for diesel vehicle."""
        config = CarConfig(
            building_name=building_name,
            name=name,
            source_weight=1,
            fuel=lt.LoadTypes.DIESEL,
            consumption_per_km=0.06,
            device_co2_footprint_in_kg=9139.3,
            investment_costs_in_euro=32035.0,
            lifetime_in_years=18,
            maintenance_costs_in_euro_per_year=0.02 * 32035.0,
        )
        return config

    @classmethod
    def get_default_ev_config(
        cls,
        building_name: str = "BUI1",
    ) -> Any:
        """Defines default configuration for electric vehicle."""
        config = CarConfig(
            building_name=building_name,
            name="Car",
            source_weight=1,
            fuel=lt.LoadTypes.ELECTRICITY,
            consumption_per_km=0.15,
            device_co2_footprint_in_kg=8899.4,
            investment_costs_in_euro=44498.0,
            maintenance_costs_in_euro_per_year=0.02 * 44498.0,
            lifetime_in_years=18,
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
    DrivenMeters = "DrivenMeters"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CarConfig,
        data_dict_with_car_information: Dict,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Initializes Car."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.build(config=config, car_information_dict=data_dict_with_car_information)

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
        self.driven_meters_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DrivenMeters,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.METER,
            output_description="Driven distance in meters.",
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
        stsv.set_output_value(self.driven_meters_output, self.meters_driven[timestep])

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        co2_per_simulated_period_in_kg = None
        consumption_in_kwh: float
        consumption_in_liter: float
        energy_costs_in_euro = 0.0
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if (
                        output.field_name == self.FuelConsumption
                        and output.unit == lt.Units.LITER
                        and output.load_type == lt.LoadTypes.DIESEL
                ):
                    consumption_in_liter = round(sum(postprocessing_results.iloc[:, index]), 1)
                    # heating value: https://nachhaltigmobil.schule/leistung-energie-verbrauch/#:~:text=Benzin%20hat%20einen%20Heizwert%20von,9%2C8%20kWh%20pro%20Liter.
                    heating_value_of_diesel_in_kwh_per_liter = 9.8
                    consumption_in_kwh = heating_value_of_diesel_in_kwh_per_liter * consumption_in_liter

                    emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                        self.my_simulation_parameters.year
                    )
                    co2_per_unit = emissions_and_cost_factors.diesel_footprint_in_kg_per_l
                    euro_per_unit = emissions_and_cost_factors.diesel_costs_in_euro_per_l

                    energy_costs_in_euro = consumption_in_liter * euro_per_unit
                    co2_per_simulated_period_in_kg = consumption_in_liter * co2_per_unit

                elif (
                    output.field_name == self.ElectricityOutput
                    and output.unit == lt.Units.WATT
                    and output.load_type == lt.LoadTypes.ELECTRICITY
                ):
                    consumption_in_kwh = round(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        ),
                        1,
                    )
                    consumption_in_liter = 0
                    # No electricity costs for components except for Electricity Meter, because part of electricity consumption is feed by PV
                    energy_costs_in_euro = 0
                    co2_per_simulated_period_in_kg = 0.0

        if co2_per_simulated_period_in_kg is None:
            raise ValueError("Could not calculate OPEX for Car component.")

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=energy_costs_in_euro,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=co2_per_simulated_period_in_kg,
            total_consumption_in_kwh=consumption_in_kwh,
            loadtype=self.config.fuel,
            kpi_tag=KpiTagEnumClass.CAR
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.ElectricityOutput
                and output.load_type == lt.LoadTypes.ELECTRICITY
            ):
                total_electricity_demand_in_kilowatt_hour = round(
                    KpiHelperClass.compute_total_energy_from_power_timeseries(
                        power_timeseries_in_watt=postprocessing_results.iloc[:, index],
                        timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                    ),
                    1,
                )
                my_kpi_entry = KpiEntry(
                    name="Electricity demand for driving",
                    unit="kWh",
                    value=total_electricity_demand_in_kilowatt_hour,
                    tag=KpiTagEnumClass.CAR,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(my_kpi_entry)
                break
            if (
                output.component_name == self.component_name
                and output.field_name == self.FuelConsumption
                and output.load_type == lt.LoadTypes.DIESEL
                and output.unit == lt.Units.LITER
            ):
                consumption_in_liter = round(sum(postprocessing_results.iloc[:, index]), 1)
                # heating value: https://nachhaltigmobil.schule/leistung-energie-verbrauch/#:~:text=Benzin%20hat%20einen%20Heizwert%20von,9%2C8%20kWh%20pro%20Liter.
                heating_value_of_diesel_in_kwh_per_liter = 9.8
                consumption_in_kwh = round((heating_value_of_diesel_in_kwh_per_liter * consumption_in_liter), 1)

                my_kpi_entry = KpiEntry(
                    name="Diesel demand for driving",
                    unit="liter",
                    value=consumption_in_liter,
                    tag=KpiTagEnumClass.CAR,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(my_kpi_entry)
                my_kpi_entry_2 = KpiEntry(
                    name="Diesel demand for driving",
                    unit="kWh",
                    value=consumption_in_kwh,
                    tag=KpiTagEnumClass.CAR,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(my_kpi_entry_2)
                break

        distance_driven_in_km = round(sum(self.meters_driven) / 1000, 1)
        my_kpi_entry_3 = KpiEntry(
            name="Distance driven",
            unit="km",
            value=distance_driven_in_km,
            tag=KpiTagEnumClass.CAR,
            description=self.component_name,
        )
        list_of_kpi_entries.append(my_kpi_entry_3)

        return list_of_kpi_entries

    @staticmethod
    def get_cost_capex(config: CarConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.investment_costs_in_euro / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_footprint / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.device_co2_footprint_in_kg,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.CAR,
        )
        return capex_cost_data_class

    def build(self, config: CarConfig, car_information_dict: Dict) -> None:
        """Loads necesary data and saves config to class."""
        self.car_information_dict = car_information_dict
        self.car_location = car_information_dict["car_location"]
        self.meters_driven = car_information_dict["driven_meters"]
        self.time_resolution = car_information_dict["time_resolution"]

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
            component_key=self.config.name,
            parameter_class=config,
            my_simulation_parameters=self.my_simulation_parameters,
        )
        if file_exists:
            # load from cache
            log.information("Generic car data is taken from cache.")
            dataframe = pd.read_csv(cache_filepath, sep=",", decimal=".", encoding="cp1252")
            self.car_location = dataframe["car_location"].tolist()
            self.meters_driven = dataframe["meters_driven"].tolist()

        else:
            # compare time resolution of LPG to time resolution of hisim
            time_resolution_original = dt.datetime.strptime(self.time_resolution, "%H:%M:%S")
            seconds_per_timestep_original = (
                time_resolution_original.hour * 3600
                + time_resolution_original.minute * 60
                + time_resolution_original.second
            )
            minutes_per_timestep = int(
                self.my_simulation_parameters.seconds_per_timestep / seconds_per_timestep_original
            )

            simulation_time_span = self.my_simulation_parameters.end_date - self.my_simulation_parameters.start_date
            # minutes_per_timestep = int(self.my_simulation_parameters.seconds_per_timestep / 60)
            steps_desired = self.my_simulation_parameters.timesteps
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
                    "meters_driven": self.meters_driven[:steps_desired_in_minutes],
                    "car_location": [location_translator[elem] for elem in self.car_location][
                        :steps_desired_in_minutes
                    ],
                }
            )
            initial_data = utils.convert_lpg_data_to_utc(data=initial_data, year=self.my_simulation_parameters.year)
            self.meters_driven = pd.to_numeric(initial_data["meters_driven"]).tolist()
            self.car_location = pd.to_numeric(initial_data["car_location"]).tolist()

            # sum / extract most common value from data to match hisim time resolution
            if minutes_per_timestep > 1:
                self.meters_driven = self.resample_meters_driven(
                    meters_driven=self.meters_driven,
                    seconds_per_timestep=self.my_simulation_parameters.seconds_per_timestep,
                )
                self.car_location = [
                    most_frequent(
                        input_list=self.car_location[i * minutes_per_timestep : (i + 1) * minutes_per_timestep]
                    )
                    for i in range(steps_desired)
                ]
            else:
                self.meters_driven = self.meters_driven
                self.car_location = self.car_location

            # save data in cache
            database = pd.DataFrame({"car_location": self.car_location, "meters_driven": self.meters_driven})
            database.to_csv(cache_filepath)
            del database

    def resample_meters_driven(self, meters_driven: List, seconds_per_timestep: int) -> Any:
        """Resample meters driven according to simulation time resolution."""
        # Convert seconds per timestep to minutes per timestep
        minutes_per_timestep = seconds_per_timestep // 60

        # Check the length of the input list
        total_minutes = len(meters_driven)

        # Calculate the number of complete timesteps
        num_timesteps = total_minutes // minutes_per_timestep

        # Trim the list to be a multiple of minutes_per_timestep
        trimmed_meters_driven = meters_driven[: num_timesteps * minutes_per_timestep]

        # Reshape and sum the data
        reshaped_meters: np.ndarray = np.reshape(trimmed_meters_driven, (num_timesteps, minutes_per_timestep))
        resampled_meters = np.sum(reshaped_meters, axis=1)
        return resampled_meters

    def write_to_report(self) -> List[str]:
        """Writes Car values to report."""
        return self.config.get_string_dict()
