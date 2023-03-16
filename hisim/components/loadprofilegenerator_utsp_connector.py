""" Contains a component that uses the UTSP to provide LoadProfileGenerator data. """
import datetime
import errno
import io
import itertools
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

from utspclient import client, datastructures, result_file_filters
from utspclient.helpers import lpg_helper
from utspclient.helpers.lpgdata import (
    ChargingStationSets,
    Households,
    HouseTypes,
    LoadTypes,
    TransportationDeviceSets,
    TravelRouteSets,
)
from utspclient.helpers.lpgpythonbindings import CalcOption, JsonReference

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log, utils
from hisim.components.configuration import HouseholdWarmWaterDemandConfig, PhysicsConfig
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class UtspLpgConnectorConfig(cp.ConfigBase):

    """Config class for UtspLpgConnector. Contains LPG parameters and UTSP connection parameters."""

    name: str
    url: str
    api_key: str
    household: JsonReference
    result_path: str
    travel_route_set: JsonReference
    transportation_device_set: JsonReference
    charging_station_set: JsonReference

    @classmethod
    def get_default_UTSP_connector_config(cls) -> Any:

        """Creates a default configuration. Chooses default values for the LPG parameters."""

        config = UtspLpgConnectorConfig(
            name="UTSPConnector",
            url="http://localhost:443/api/v1/profilerequest",
            api_key="",
            household=Households.CHR01_Couple_both_at_Work,
            result_path=os.path.join(utils.get_input_directory(), "lpg_profiles"),
            travel_route_set=TravelRouteSets.Travel_Route_Set_for_10km_Commuting_Distance,
            transportation_device_set=TransportationDeviceSets.Bus_and_one_30_km_h_Car,
            charging_station_set=ChargingStationSets.Charging_At_Home_with_03_7_kW,
        )
        return config


class UtspLpgConnector(cp.Component):

    """Component that provides data from the LoadProfileGenerator.

    This component provides the heating generated, the electricity and water consumed
    by the residents. Furthermore, transportation and device flexibility data is stored
    in separate files, configurable via the config object.
    The data is retrieved from the UTSP, which executes the LoadProfileGenerator to simulate
    the specified household.
    """

    # Inputs
    WW_MassInput = "Warm Water Mass Input"  # kg/s
    WW_TemperatureInput = "Warm Water Temperature Input"  # °C

    # Outputs
    # output
    WW_MassOutput = "Mass Output"  # kg/s
    WW_TemperatureOutput = "Temperature Output"  # °C
    EnergyDischarged = "Energy Discharged"  # W
    DemandSatisfied = "Demand Satisfied"  # 0 or 1

    NumberByResidents = "NumberByResidents"
    HeatingByResidents = "HeatingByResidents"
    ElectricityOutput = "ElectricityOutput"
    WaterConsumption = "WaterConsumption"

    Electricity_Demand_Forecast_24h = "Electricity_Demand_Forecast_24h"

    # Similar components to connect to:
    # None
    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: UtspLpgConnectorConfig,
    ) -> None:
        """Initializes the component and retrieves the LPG data."""
        self.utsp_config = config
        super().__init__(
            name=self.utsp_config.name,
            my_simulation_parameters=my_simulation_parameters,
        )
        self.build()

        # Inputs - Not Mandatory
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

        self.number_of_residents_c: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.NumberByResidents,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description=f"here a description for LPG UTSP {self.NumberByResidents} will follow.",
        )
        self.heating_by_residents_c: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.HeatingByResidents,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description=f"here a description for LPG UTSP {self.HeatingByResidents} will follow.",
        )
        self.electricity_output_c: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
            ],
            output_description=f"here a description for LPG UTSP {self.ElectricityOutput} will follow.",
        )

        self.water_consumption_c: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.WaterConsumption,
            lt.LoadTypes.WARM_WATER,
            lt.Units.LITER,
            output_description=f"here a description for LPG UTSP {self.WaterConsumption} will follow.",
        )

    def i_save_state(self) -> None:
        """Empty method as component has no state."""
        pass

    def i_restore_state(self) -> None:
        """Empty method as component has no state."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Gets called after the iterations are finished at each time step for potential debugging purposes."""
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Sets the current output values with data retrieved during initialization."""
        if self.ww_mass_input.source_output is not None:
            # ww demand
            ww_temperature_demand = HouseholdWarmWaterDemandConfig.ww_temperature_demand

            # From Thermal Energy Storage
            ww_mass_input_per_sec = stsv.get_input_value(self.ww_mass_input)  # kg/s
            # ww_mass_input = ww_mass_input_per_sec * self.seconds_per_timestep           # kg
            ww_mass_input: float = ww_mass_input_per_sec
            ww_temperature_input = stsv.get_input_value(self.ww_temperature_input)  # °C

            # Information import
            freshwater_temperature = (
                HouseholdWarmWaterDemandConfig.freshwater_temperature
            )
            temperature_difference_cold = (
                HouseholdWarmWaterDemandConfig.temperature_difference_cold
            )
            energy_losses = 0
            specific_heat = 4180 / 3600

            ww_energy_demand = (
                specific_heat
                * self.water_consumption[timestep]
                * (ww_temperature_demand - freshwater_temperature)
            )

            if ww_energy_demand > 0 and (
                ww_mass_input == 0 and ww_temperature_input == 0
            ):
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
                ww_temperature_output: float = (
                    freshwater_temperature + temperature_difference_cold
                )
                ww_mass_input = energy_discharged / (
                    PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
                    * (ww_temperature_input - ww_temperature_output)
                )
            else:
                ww_temperature_output = ww_temperature_input
                ww_mass_input = 0
                energy_discharged = 0

        stsv.set_output_value(
            self.number_of_residents_c, self.number_of_residents[timestep]
        )
        stsv.set_output_value(
            self.heating_by_residents_c, self.heating_by_residents[timestep]
        )
        stsv.set_output_value(
            self.electricity_output_c, self.electricity_consumption[timestep]
        )
        stsv.set_output_value(
            self.water_consumption_c, self.water_consumption[timestep]
        )

        if self.my_simulation_parameters.predictive_control:
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

    def get_resolution(self) -> str:
        """Gets the temporal resolution of the simulation as a string in the format hh:mm:ss.

        :return: resolution of the simulation
        :rtype: str
        """
        seconds = self.my_simulation_parameters.seconds_per_timestep
        resolution = datetime.timedelta(seconds=seconds)
        return str(resolution)

    def get_profiles_from_utsp(self) -> Tuple[Tuple[str, str, str, str], List[str]]:
        """Requests the required load profiles from a UTSP server. Returns raw, unparsed result file contents.

        :return: a tuple of all result file contents (electricity, warm water, high bodily activity and low bodily activity),
                 and a list of filenames of all additionally saved files
        :rtype: Tuple[Tuple[str, str, str, str], List[str]]
        """
        # Create an LPG configuration and set the simulation parameters
        start_date = self.my_simulation_parameters.start_date.strftime("%Y-%m-%d")
        # Unlike HiSim the LPG includes the specified end day in the simulation --> subtract one day
        last_day = self.my_simulation_parameters.end_date - datetime.timedelta(days=1)
        end_date = last_day.strftime("%Y-%m-%d")
        simulation_config = lpg_helper.create_basic_lpg_config(
            self.utsp_config.household,
            HouseTypes.HT23_No_Infrastructure_at_all,
            start_date,
            end_date,
            self.get_resolution(),
            travel_route_set=self.utsp_config.travel_route_set,
            transportation_device_set=self.utsp_config.transportation_device_set,
            charging_station_set=self.utsp_config.charging_station_set,
            calc_options=[
                CalcOption.HouseholdSumProfilesCsvNoFlex,
                CalcOption.BodilyActivityStatistics,
                CalcOption.TansportationDeviceJsons,
                CalcOption.FlexibilityEvents,
            ],
        )
        assert simulation_config.CalcSpec is not None

        # Enable simulation of transportation and flexible devices
        simulation_config.CalcSpec.EnableTransportation = True
        simulation_config.CalcSpec.EnableFlexibility = True

        # Define required result files
        electricity = result_file_filters.LPGFilters.sum_hh1(
            LoadTypes.Electricity, no_flex=True
        )
        warm_water = result_file_filters.LPGFilters.sum_hh1(
            LoadTypes.Warm_Water, no_flex=True
        )
        high_activity = result_file_filters.LPGFilters.BodilyActivity.HIGH
        low_activity = result_file_filters.LPGFilters.BodilyActivity.LOW
        flexibility = result_file_filters.LPGFilters.FLEXIBILITY_EVENTS
        required_files = {
            f: datastructures.ResultFileRequirement.REQUIRED
            for f in [
                electricity,
                warm_water,
                high_activity,
                low_activity,
                flexibility,
            ]
        }
        # Define transportation result files
        car_states = result_file_filters.LPGFilters.all_car_states_optional()
        car_locations = result_file_filters.LPGFilters.all_car_locations_optional()
        driving_distances = (
            result_file_filters.LPGFilters.all_driving_distances_optional()
        )
        result_files: Dict[str, Optional[datastructures.ResultFileRequirement]] = {
            **required_files,
            **car_states,
            **car_locations,
            **driving_distances,
        }

        # Prepare the time series request
        request = datastructures.TimeSeriesRequest(
            simulation_config.to_json(), "LPG", required_result_files=result_files  # type: ignore
        )

        log.information("Requesting LPG profiles from the UTSP.")
        # Request the time series
        result = client.request_time_series_and_wait_for_delivery(
            self.utsp_config.url, request, self.utsp_config.api_key
        )

        electricity_file = result.data[electricity].decode()
        warm_water_file = result.data[warm_water].decode()
        high_activity_file = result.data[high_activity].decode()
        low_activity_file = result.data[low_activity].decode()
        flexibility_file = result.data[flexibility].decode()

        # Save flexibility and transportation files
        saved_files: List[str] = []
        path = self.save_result_file(flexibility, flexibility_file)
        saved_files.append(path)
        for filename in itertools.chain(
            car_states.keys(), car_locations.keys(), driving_distances.keys()
        ):
            if filename in result.data:
                path = self.save_result_file(filename, result.data[filename].decode())
                saved_files.append(path)

        return (
            electricity_file,
            warm_water_file,
            high_activity_file,
            low_activity_file,
        ), saved_files

    def save_result_file(self, name: str, content: str) -> str:
        """
        Saves a result file in the folder specified in the config object.

        :param name: the name for the file
        :type name: str
        :param content: the content that will be written into the file
        :type content: str
        :return: path of the file that was saved
        :rtype: str
        """
        filepath = os.path.join(self.utsp_config.result_path, name)
        directory = os.path.dirname(filepath)
        # Create the directory if it does not exist
        try:
            os.makedirs(directory)
        except OSError as exc:
            if exc.errno == errno.EEXIST and os.path.isdir(directory):
                pass
            else:
                raise
        # Create the result file
        with open(filepath, "w", encoding="utf-8") as result_file:
            result_file.write(content)
        return filepath

    def build(self):
        """Retrieves and preprocesses all data for this component."""
        file_exists, cache_filepath = utils.get_cache_file(
            component_key=self.component_name,
            parameter_class=self.utsp_config,
            my_simulation_parameters=self.my_simulation_parameters,
        )
        cache_complete = False
        if file_exists:
            with open(cache_filepath, "r", encoding="utf-8") as file:
                cache_content: Dict = json.load(file)
            saved_files = cache_content["saved_files"]
            cache_complete = True
            # check if all of the additionally saved files that belong to the cached results
            # are also still there
            for filename in saved_files:
                if not os.path.isfile(filename):
                    log.information(
                        f"A cache file for {self.component_name} was found, "
                        "but some of the additional result files from the UTSP could not be "
                        "found anymore, so the cache is discarded."
                    )
                    cache_complete = False
                    break
            if cache_complete:
                cached_data = io.StringIO(cache_content["data"])
                dataframe = pd.read_csv(
                    cached_data, sep=",", decimal=".", encoding="cp1252"
                )
                self.number_of_residents = dataframe["number_of_residents"].tolist()
                self.heating_by_residents = dataframe["heating_by_residents"].tolist()
                self.electricity_consumption = dataframe[
                    "electricity_consumption"
                ].tolist()
                self.water_consumption = dataframe["water_consumption"].tolist()

        if not cache_complete:
            result_data, saved_files = self.get_profiles_from_utsp()
            (
                electricity,
                warm_water,
                high_activity,
                low_activity,
            ) = result_data

            ################################
            # Calculates heating generated by residents and loads number of residents
            # Heat power generated per resident in W
            # mode 1: awake
            # mode 2: sleeping
            gain_per_person = [150, 100]

            # load occupancy profile
            occupancy_profile = []
            bodily_activity_files = [high_activity, low_activity]
            for filecontent in bodily_activity_files:
                json_filex = json.loads(filecontent)

                occupancy_profile.append(json_filex)

            # see how long csv files from LPG are to check if averaging has to be done and calculate desired length
            steps_original = len(occupancy_profile[0]["Values"])
            simulation_time_span = (
                self.my_simulation_parameters.end_date
                - self.my_simulation_parameters.start_date
            )
            steps_desired = int(
                simulation_time_span.days
                * 24
                * (3600 / self.my_simulation_parameters.seconds_per_timestep)
            )
            steps_ratio = int(steps_original / steps_desired)

            # initialize number of residence and heating by residents
            self.heating_by_residents = [0] * steps_desired
            self.number_of_residents = [0] * steps_desired

            # load electricity consumption and water consumption
            electricity_data = io.StringIO(electricity)
            pre_electricity_consumption = pd.read_csv(
                electricity_data,
                sep=";",
                decimal=".",
                encoding="cp1252",
            )
            water_data = io.StringIO(warm_water)
            pre_water_consumption = pd.read_csv(
                water_data,
                sep=";",
                decimal=".",
                encoding="cp1252",
            )

            # convert electricity consumption and water consumption to desired format and unit
            self.electricity_consumption = pd.to_numeric(
                pre_electricity_consumption["Sum [kWh]"] * 1000 * 60
            ).tolist()  # 1 kWh/min == 60W / min
            self.water_consumption = pd.to_numeric(
                pre_water_consumption["Sum [L]"]
            ).tolist()

            # process data when time resolution of inputs matches timeresolution of simulation
            if steps_original == steps_desired:
                for mode, gain in enumerate(gain_per_person):
                    for timestep in range(steps_original):
                        self.number_of_residents[timestep] += occupancy_profile[mode][
                            "Values"
                        ][timestep]
                        self.heating_by_residents[timestep] = (
                            self.heating_by_residents[timestep]
                            + gain * occupancy_profile[mode]["Values"][timestep]
                        )

            # average data, when time resolution of inputs is coarser than time resolution of simulation
            elif steps_original > steps_desired:
                for mode, gain in enumerate(gain_per_person):
                    for timestep in range(steps_desired):
                        number_of_residents_av = (
                            sum(
                                occupancy_profile[mode]["Values"][
                                    timestep
                                    * steps_ratio : (timestep + 1)
                                    * steps_ratio
                                ]
                            )
                            / steps_ratio
                        )
                        self.number_of_residents[timestep] += np.round(
                            number_of_residents_av
                        )
                        self.heating_by_residents[timestep] = (
                            self.heating_by_residents[timestep]
                            + gain * number_of_residents_av
                        )
                # power needs averaging, not sum
                self.electricity_consumption = [
                    sum(self.electricity_consumption[n : n + steps_ratio]) / steps_ratio
                    for n in range(0, steps_original, steps_ratio)
                ]
                self.water_consumption = [
                    sum(self.water_consumption[n : n + steps_ratio])
                    for n in range(0, steps_original, steps_ratio)
                ]

            else:
                raise Exception(
                    "input from LPG is given in wrong time resolution - or at least cannot be interpolated correctly"
                )

            # Saves data in cache
            data = np.transpose(
                [
                    self.number_of_residents,
                    self.heating_by_residents,
                    self.electricity_consumption,
                    self.water_consumption,
                ]
            )
            database = pd.DataFrame(
                data,
                columns=[
                    "number_of_residents",
                    "heating_by_residents",
                    "electricity_consumption",
                    "water_consumption",
                ],
            )
            # dump the dataframe to str
            cache_file = io.StringIO()
            database.to_csv(cache_file)
            database_str = cache_file.getvalue()
            # save the dataframe and the list of additional files in the cache
            cache_content = {"saved_files": saved_files, "data": database_str}
            with open(cache_filepath, "w", encoding="utf-8") as file:
                json.dump(cache_content, file)
            del data
            del database
        self.max_hot_water_demand = max(self.water_consumption)

    def write_to_report(self):
        """Adds a report entry for this component."""
        return self.utsp_config.get_string_dict()
