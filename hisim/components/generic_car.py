"""Simple Car (LPG connected) and configuration.

Evaluates diesel or electricity consumption based on driven kilometers and processes Car Location for charging stations.
"""

# clean

import datetime as dt
from dataclasses import dataclass

# -*- coding: utf-8 -*-
from typing import Any, ClassVar, Dict, List, Tuple
import numpy as np
import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils, log
from hisim.caching import atomic_cache_write
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.config import ConfigBase, ComponentID, DisplayConfig, Sizable, Size, constructor, sized_field
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.loadtypes import Units, ComponentType

from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass
from hisim.components.lpg_car_information import CarProfileHandover

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
class CarConfig(ConfigBase):
    """Definition of configuration of Car, including the identity of the driving profile it uses.

    Everything the car needs that a file can state lives here; the two time series it also needs —
    where the car is and how far it moved in each minute — cannot, because they are a
    LoadProfileGenerator result rather than a catalogue value. What the configuration carries
    instead is the *identity* of that result: ``household_name`` and ``car_name`` are what the car
    looks its profile up under in the simulation repository, once the occupancy has published it.
    """

    #: Consumption per kilometre in kWh/km, CO2 footprint of manufacture in kg and investment cost
    #: in Euro of the electric vehicle :meth:`for_household` configures for an electric car.
    ELECTRIC_VEHICLE: ClassVar[Tuple[float, float, float]] = (0.15, 8899.4, 44498.0)

    #: The same three numbers for a diesel car, where the consumption is litres per kilometre.
    DIESEL_VEHICLE: ClassVar[Tuple[float, float, float]] = (0.06, 9139.3, 32035.0)

    #: Share of the investment cost assumed to be spent on maintenance every year.
    MAINTENANCE_SHARE_OF_INVESTMENT: ClassVar[float] = 0.02

    #: Assumed service life of a car in years, the same for both fuels.
    SERVICE_LIFE_IN_YEARS: ClassVar[float] = 18.0

    component_id: ComponentID
    #: name of the LoadProfileGenerator household the car belongs to; half of the identity under
    #: which the occupancy publishes this car's driving profile
    household_name: str
    #: name of the car within its household; the other half of that identity
    car_name: str
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
    # subsidies as percentage of investment costs
    subsidy_as_percentage_of_investment_costs: float
    #: The occupancy whose driving profile this car uses, as ``UtspLpgConnectorConfig.identity()``
    #: spells it. Sized from the occupancy by the sizing engine, so the cache key includes it; the
    #: household name alone does not determine the profile. See ``roadmap/pylpg_flakiness.md`` F7.
    occupancy_identity: Sizable[str] = sized_field(rule=Size.OCCUPANCY_IDENTITY, value_type=str)

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Car.get_full_classname()

    @constructor(note="the two vehicles the HiSim cost database carries, an EV and a diesel car")
    @classmethod
    def for_household(
        cls,
        name: str,
        household_name: str,
        car_name: str,
        fuel: lt.LoadTypes = lt.LoadTypes.ELECTRICITY,
        source_weight: int = 1,
    ) -> "CarConfig":
        """Builds the configuration of one car of one LoadProfileGenerator household.

        This is a named constructor rather than a preset because the household and the car it
        names are an open identifier space — whatever the LoadProfileGenerator happened to produce
        for the occupancy of this system — and no fixed set of wire names could enumerate it. The
        fuel, by contrast, is a genuine two-way choice, so it is a parameter with the electric car
        as the default rather than two nearly identical constructors.

        The cost and emission figures are the ones the two former ``get_default_*`` factories
        carried, unchanged; maintenance is derived from the investment cost rather than restated,
        because the two numbers were always a fixed ratio apart and restating them invited them to
        drift.

        Args:
            name: Instance name of the car being configured; it becomes its identity.
            household_name: Normalised name of the LPG household whose profile this car drives by.
            car_name: Normalised name of the car within that household.
            fuel: Whether the car burns diesel or draws electricity.
            source_weight: Priority of the car among several; the higher the number the lower the
                priority. It is what a charging station and a car battery bind to, so several cars
                in one system must not share it.

        Returns:
            A configuration naming that car's profile and costed for the requested fuel.

        Raises:
            ValueError: If the fuel is neither electricity nor diesel, since no other car is
                costed.
        """
        if fuel == lt.LoadTypes.ELECTRICITY:
            consumption_per_km, device_co2_footprint_in_kg, investment_costs_in_euro = cls.ELECTRIC_VEHICLE
        elif fuel == lt.LoadTypes.DIESEL:
            consumption_per_km, device_co2_footprint_in_kg, investment_costs_in_euro = cls.DIESEL_VEHICLE
        else:
            raise ValueError(
                f"A car can run on {lt.LoadTypes.ELECTRICITY} or on {lt.LoadTypes.DIESEL}, "
                f"but '{fuel}' was requested; no other vehicle is costed."
            )
        return cls(
            component_id=ComponentID(name=name),
            household_name=household_name,
            car_name=car_name,
            source_weight=source_weight,
            fuel=fuel,
            consumption_per_km=consumption_per_km,
            device_co2_footprint_in_kg=device_co2_footprint_in_kg,
            investment_costs_in_euro=investment_costs_in_euro,
            lifetime_in_years=cls.SERVICE_LIFE_IN_YEARS,
            maintenance_costs_in_euro_per_year=cls.MAINTENANCE_SHARE_OF_INVESTMENT * investment_costs_in_euro,
            subsidy_as_percentage_of_investment_costs=0,
        )


def most_frequent(input_list: List) -> Any:
    """Returns most frequent value - needed for down sampling Location information from 1 minute resoultion to lower."""
    max_count = 0
    most_frequent_value = input_list[0]

    for value in input_list:
        curr_frequency = input_list.count(value)
        if curr_frequency > max_count:
            max_count = curr_frequency
            most_frequent_value = value
    return most_frequent_value


class Car(cp.Component):
    """Simulates car with constant consumption. Car usage (driven kilometers and state) orginate from LPG.

    The car is built from its configuration alone, like every other HiSim component. The driving
    profile it needs is not in that configuration and cannot be — it is a full-year time series the
    LoadProfileGenerator produced for the occupancy — so the configuration carries the *identity*
    of the profile and the car fetches the profile itself from the simulation repository in
    :meth:`i_prepare_simulation`, after the occupancy has published it there.

    That makes component order load-bearing: preparation runs in registration order, so the
    occupancy must be registered before any car. When it was not, or when no occupancy has car data
    at all, the lookup raises and names the household and the car rather than letting the run
    continue with a vehicle that never moves.
    """

    # Outputs
    FuelConsumption = "FuelConsumption"
    ElectricityOutput = "ElectricityOutput"
    CarLocation = "CarLocation"
    DrivenMeters = "DrivenMeters"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: CarConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Initializes Car.

        Args:
            my_simulation_parameters: Parameters of the run.
            config: The car's configuration, naming the household and car whose driving profile it
                drives by.
            my_display_config: What the webtool and the report show of this component.
        """
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        #: The payload the occupancy published, filled in :meth:`i_prepare_simulation`.
        self.car_information_dict: Dict[str, Any] = {}
        #: Location of the car per timestep, as the integers of the location translator.
        self.car_location: List[Any] = []
        #: Metres driven per timestep.
        self.meters_driven: Any = []
        #: Time resolution of the published profile, as an ``%H:%M:%S`` string.
        self.time_resolution: str = ""

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
        """Fetches the car's driving profile from the repository and resamples it for the run.

        This is where the hand-off is completed: the occupancy published every profile it produced
        during its own preparation, and the car takes the one its configuration names. Doing it
        here rather than in the constructor is what lets the executor build the car from
        ``(my_simulation_parameters, config)`` like every other component.

        Raises:
            CarProfileNotPublishedError: If no occupancy published a profile for this car — either
                because none has car data, or because the car was prepared before its occupancy.
        """
        car_information_dict = CarProfileHandover.lookup(
            repository=getattr(self, "simulation_repository", None),
            household_name=self.config.household_name,
            car_name=self.config.car_name,
        )
        self.build(car_information_dict=car_information_dict)

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
                        self.my_simulation_parameters.year, self.my_simulation_parameters.country
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
                            time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
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
            kpi_tag=KpiTagEnumClass.CAR,
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
                        time_resolution_in_seconds=self.my_simulation_parameters.seconds_per_timestep,
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

                diesel_demand_in_liter_kpi_entry = KpiEntry(
                    name="Diesel demand for driving",
                    unit="liter",
                    value=consumption_in_liter,
                    tag=KpiTagEnumClass.CAR,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(diesel_demand_in_liter_kpi_entry)
                diesel_demand_in_kwh_kpi_entry = KpiEntry(
                    name="Diesel demand for driving",
                    unit="kWh",
                    value=consumption_in_kwh,
                    tag=KpiTagEnumClass.CAR,
                    description=self.component_name,
                )
                list_of_kpi_entries.append(diesel_demand_in_kwh_kpi_entry)
                break

        distance_driven_in_km = round(sum(self.meters_driven) / 1000, 1)
        distance_driven_kpi_entry = KpiEntry(
            name="Distance driven",
            unit="km",
            value=distance_driven_in_km,
            tag=KpiTagEnumClass.CAR,
            description=self.component_name,
        )
        list_of_kpi_entries.append(distance_driven_kpi_entry)

        return list_of_kpi_entries

    @staticmethod
    def get_cost_capex(config: CarConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        # set variables
        component_type = ComponentType.CAR
        kpi_tag = KpiTagEnumClass.CAR
        unit = Units.ANY
        size_of_energy_system = 1

        capex_cost_data_class = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
            simulation_parameters=simulation_parameters,
            component_type=component_type,
            unit=unit,
            size_of_energy_system=size_of_energy_system,
            config=config,
            kpi_tag=kpi_tag,
        )
        config = CapexComputationHelperFunctions.overwrite_config_values_with_new_capex_values(
            config=config, capex_cost_data_class=capex_cost_data_class
        )
        return capex_cost_data_class

    def build(self, car_information_dict: Dict) -> None:
        """Turns the published minute-resolution profile into the series this run's timesteps need.

        The LoadProfileGenerator always reports per minute and in local time, so the profile has to
        be converted to UTC and then aggregated — summed for distances, most-frequent for the
        location — down to the run's own resolution. The result is cached under the car's
        configuration, because the conversion is the expensive part of preparing a car and nothing
        about it changes between two runs of the same car under the same parameters.

        Args:
            car_information_dict: The payload the occupancy published for this car.
        """
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
            component_key=self.config.component_id.name,
            parameter_class=self.config,
            my_simulation_parameters=self.my_simulation_parameters,
        )
        if file_exists:
            # load from cache
            log.information("Generic car data is taken from cache.")
            # float_precision="round_trip" is what makes a cached run and an uncached one the same
            # run. pandas' default CSV reader uses a fast, inexact float parser and loses the last
            # bit of a fifth of the distances in this file -- measured on a committed year-long
            # entry: 334 of the 1789 non-zero values of meters_driven come back different from
            # what was written, and none do with this argument.
            dataframe = pd.read_csv(
                cache_filepath, sep=",", decimal=".", encoding="cp1252", float_precision="round_trip"
            )
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
                        freq="min",
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

            # save data in cache
            car_cache_dataframe = pd.DataFrame({"car_location": self.car_location, "meters_driven": self.meters_driven})
            with atomic_cache_write(
                cache_filepath, utils.build_cache_key_string(self.config, self.my_simulation_parameters)
            ) as temporary_cache_filepath:
                car_cache_dataframe.to_csv(temporary_cache_filepath)
            del car_cache_dataframe

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
