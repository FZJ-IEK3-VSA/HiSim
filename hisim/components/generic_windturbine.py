"""Windturbine."""

import hashlib

# clean

from dataclasses import dataclass
from typing import Any, List, Optional, Dict


import numpy as np
import pandas as pd
from dataclass_wizard import JSONWizard
from dataclasses_json import dataclass_json
from windpowerlib import ModelChain, WindTurbine

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import ConfigBase, OpexCostDataClass, CapexCostDataClass
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = [" Jonas Hoppe "]
__license__ = ""
__version__ = ""
__maintainer__ = "  "
__email__ = ""
__status__ = ""


"""
Based on: https://github.com/wind-python/windpowerlib/tree/dev
Use of windpowerlib for calculation of electrical output power

"""


@dataclass_json
@dataclass
class WindturbineConfig(ConfigBase):
    """Windturbine Configclass."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Windturbine.get_full_classname()

    building_name: str
    name: str
    # Typename of the wind turbine.
    turbine_type: str
    # Hub height of the wind turbine in m.
    hub_height: Optional[float]
    # The nominal output of the wind turbine in W
    nominal_power: Optional[float]
    # Diameter of the rotor in m
    rotor_diameter: Optional[float]
    # Power coefficient curve of the wind turbine
    power_coefficient_curve: None
    # Power curve of the wind turbine.
    power_curve: None
    # Defines which model is used to calculate the wind speed at hub height
    wind_speed_model: str
    # Defines which model is used to calculate the temperature of air at hub height
    temperature_model: str
    # Defines which model is used to calculate the density of air at hub height.
    density_model: str
    # Defines which model is used to calculate the turbine power output.
    power_output_model: str
    density_correction: bool
    obstacle_height: float
    measuring_height_wind_speed: float
    measuring_height_temperature: float
    measuring_height_pressure: float
    measuring_height_roughness_length: float
    hellman_exp: float

    source_weight: int
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: float
    #: cost for investment in Euro
    investment_costs_in_euro: float
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: float
    #: lifetime in years
    lifetime_in_years: float
    predictive: bool
    predictive_control: bool
    prediction_horizon: Optional[int]

    @classmethod
    def get_default_windturbine_config(
        cls,
        building_name: str = "BUI1",
    ) -> "WindturbineConfig":
        """Gets a default windturbine."""
        return WindturbineConfig(
            building_name=building_name,
            name="Windturbine",
            turbine_type="V126/3300",
            hub_height=137,
            nominal_power=None,
            rotor_diameter=126,
            power_coefficient_curve=None,
            power_curve=None,
            wind_speed_model="logarithmic",  # 'logarithmic' ,'hellman', 'interpolation_extrapolation' , 'log_interpolation_extrapolation'
            temperature_model="linear_gradient",  # 'linear_gradient','interpolation_extrapolation'
            density_model="barometric",  # 'barometric','ideal_gas','interpolation_extrapolation'
            power_output_model="power_curve",  # power_curve','power_coefficient_curve'
            density_correction=False,
            obstacle_height=0,
            measuring_height_wind_speed=10,
            measuring_height_temperature=2,
            measuring_height_pressure=125,
            measuring_height_roughness_length=0,
            hellman_exp=0,  # This parameter is only used if the parameter `wind_speed_model` is 'hellman'.
            source_weight=999,
            device_co2_footprint_in_kg=0,
            investment_costs_in_euro=0,
            maintenance_costs_in_euro_per_year=0,
            lifetime_in_years=0,
            predictive=False,
            predictive_control=False,
            prediction_horizon=None,
        )


class Windturbine(cp.Component):
    """windturbine calculates electrical output power based on windturbine type and weather data."""

    # Inputs
    TemperatureOutside = "TemperatureOutside"
    WindSpeed = "WindSpeed"
    Pressure = "Pressure"

    # Outputs
    ElectricityOutput = "ElectricityOutput"
    ElectricitityEnergyOutput = "ElectricitityEnergyOutput"
    CumulativeProduction = "CumulativeProduction"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: WindturbineConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the class."""
        self.windturbineconfig = config

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # caching for windpowerlib simulation
        self.calculation_cache: Dict = {}

        self.turbine_type = self.windturbineconfig.turbine_type
        self.hub_height = self.windturbineconfig.hub_height
        self.rotor_diameter = self.windturbineconfig.rotor_diameter
        self.power_coefficient_curve = self.windturbineconfig.power_coefficient_curve
        self.power_curve = self.windturbineconfig.power_curve
        self.nominal_power = self.windturbineconfig.nominal_power

        self.wind_speed_model = self.windturbineconfig.wind_speed_model
        self.temperature_model = self.windturbineconfig.temperature_model
        self.density_model = self.windturbineconfig.density_model
        self.power_output_model = self.windturbineconfig.power_output_model
        self.density_correction = self.windturbineconfig.density_correction
        self.obstacle_height = self.windturbineconfig.obstacle_height
        self.measuring_height_wind_speed = self.windturbineconfig.measuring_height_wind_speed
        self.measuring_height_temperature = self.windturbineconfig.measuring_height_temperature
        self.measuring_height_pressure = self.windturbineconfig.measuring_height_pressure
        self.measuring_height_roughness_length = self.windturbineconfig.measuring_height_roughness_length
        self.hellman_exp = self.windturbineconfig.hellman_exp

        # Inistialisieren Windkraftanlage
        self.windturbine_module = WindTurbine(
            hub_height=self.hub_height,
            nominal_power=self.nominal_power,
            path="oedb",
            power_curve=self.power_curve,
            power_coefficient_curve=self.power_coefficient_curve,
            rotor_diameter=self.rotor_diameter,
            turbine_type=self.turbine_type,
        )

        # Berechnungsmethoden Winddaten
        self.calculation_setup = ModelChain(
            power_plant=self.windturbine_module,
            wind_speed_model=self.wind_speed_model,
            temperature_model=self.temperature_model,
            density_model=self.density_model,
            power_output_model=self.power_output_model,
            density_correction=self.density_correction,
            obstacle_height=self.obstacle_height,
            hellman_exp=self.hellman_exp,
        )

        self.state = WindturbineState(cumulative_production_in_watt_hour=0.0)
        self.previous_state = self.state.self_copy()
        # inputs
        self.t_out_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.TEMPERATURE,
            lt.Units.CELSIUS,
            True,
        )

        self.wind_speed_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.WindSpeed,
            lt.LoadTypes.SPEED,
            lt.Units.METER_PER_SECOND,
            True,
        )

        self.pressure_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.Pressure,
            lt.LoadTypes.PRESSURE,
            lt.Units.PASCAL,
            True,
        )

        # outputs
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=[
                lt.ComponentType.WINDTURBINE,
                lt.InandOutputType.ELECTRICITY_PRODUCTION,
            ],
            output_description=f"here a description for Windturbine {self.ElectricityOutput} will follow.",
        )

        self.electricity_energy_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricitityEnergyOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
            output_description=f"Here a description for PV {self.ElectricitityEnergyOutput} will follow.",
        )

        self.cumulative_electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CumulativeProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT_HOUR,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CumulativeProduction} will follow.",
        )

        self.add_default_connections(self.get_default_connections_from_weather())

    def get_default_connections_from_weather(self):
        """Get default connections from weather."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                Windturbine.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )

        connections.append(cp.ComponentConnection(Windturbine.WindSpeed, weather_classname, Weather.WindSpeed))

        connections.append(cp.ComponentConnection(Windturbine.Pressure, weather_classname, Weather.Pressure))
        return connections

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.self_copy()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.self_copy()

    def write_to_report(self):
        """Write to the report."""
        return self.windturbineconfig.get_string_dict()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the component for the simulation."""

        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the component."""

        wind_speed_10m_in_m_per_sec = stsv.get_input_value(self.wind_speed_channel)
        temperature_2m_in_celsius = stsv.get_input_value(self.t_out_channel)
        pressure_standorthoehe_in_pascal = stsv.get_input_value(self.pressure_channel)

        temperature_2m_in_kelvin = temperature_2m_in_celsius + 273.15

        roughness_length_in_m = 0.15

        data = [
            [
                wind_speed_10m_in_m_per_sec,
                temperature_2m_in_kelvin,
                pressure_standorthoehe_in_pascal,
                roughness_length_in_m,
            ]
        ]

        # height of measuring points
        columns = [
            np.array(["wind_speed", "temperature", "pressure", "roughness_length"]),
            np.array(
                [
                    self.measuring_height_wind_speed,
                    self.measuring_height_temperature,
                    self.measuring_height_pressure,
                    self.measuring_height_roughness_length,
                ]
            ),
        ]

        # calculation of windturbine power
        windturbine_power = self.get_cached_results_or_run_windpowerlib_simulation(data=data, columns=columns)

        # write power output time series to WindTurbine object
        windturbine_power.power_output = windturbine_power.power_output

        power_output_windturbine_in_watt = windturbine_power.power_output

        df_electric_power_output_windturbine_in_watt = pd.DataFrame(power_output_windturbine_in_watt)

        electric_power_output_windturbine_in_watt = df_electric_power_output_windturbine_in_watt.iloc[0].iloc[0]

        production_in_watt_hour = (electric_power_output_windturbine_in_watt *
                                   self.my_simulation_parameters.seconds_per_timestep / 3600)

        cumulative_production_in_watt_hour = self.state.cumulative_production_in_watt_hour + production_in_watt_hour

        stsv.set_output_value(self.electricity_energy_output_channel, production_in_watt_hour)
        stsv.set_output_value(self.electricity_output_channel, electric_power_output_windturbine_in_watt)
        stsv.set_output_value(self.cumulative_electricity_production_channel, cumulative_production_in_watt_hour)

        self.state.cumulative_production_in_watt_hour = cumulative_production_in_watt_hour

    @staticmethod
    def get_cost_capex(config: WindturbineConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
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
            kpi_tag=KpiTagEnumClass.WINDTURBINE
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for PV."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.WINDTURBINE
        )
        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []

    def get_cached_results_or_run_windpowerlib_simulation(
        self,
        data: list,
        columns: list,
    ) -> Any:
        """Use caching of results of windpowerlib simulation."""

        # rounding of variable values
        wind_speed_10m_in_m_per_sec = round(data[0][0], 2)
        temperature_2m_in_kelvin = round(data[0][1], 2)
        pressure_standorthoehe_in_pascal = round(data[0][2], 2)
        roughness_length_in_m = round(data[0][3], 2)

        my_data_class = CalculationRequest(
            wind_speed_10m_in_m_per_sec=wind_speed_10m_in_m_per_sec,
            temperature_2m_in_kelvin=temperature_2m_in_kelvin,
            pressure_standorthoehe_in_pascal=pressure_standorthoehe_in_pascal,
            roughness_length_in_m=roughness_length_in_m,
        )
        my_json_key = my_data_class.get_key()
        my_hash_key = hashlib.sha256(my_json_key.encode("utf-8")).hexdigest()

        if my_hash_key in self.calculation_cache:
            windturbine_power = self.calculation_cache[my_hash_key]

        else:
            weather_df = pd.DataFrame(data, columns=columns)  # dataframe, due to package windpowerlib only work with it
            windturbine_power = self.calculation_setup.run_model(weather_df)

            self.calculation_cache[my_hash_key] = windturbine_power

        return windturbine_power


@dataclass
class CalculationRequest(JSONWizard):
    """Class for caching windtubine parameters so that simulation does not need to run so often."""

    wind_speed_10m_in_m_per_sec: float
    temperature_2m_in_kelvin: float
    pressure_standorthoehe_in_pascal: float
    roughness_length_in_m: float

    def get_key(self):
        """Get key of class with important parameters."""

        return (
            str(self.wind_speed_10m_in_m_per_sec)
            + " "
            + str(self.temperature_2m_in_kelvin)
            + " "
            + str(self.pressure_standorthoehe_in_pascal)
            + " "
            + str(self.roughness_length_in_m)
        )


@dataclass
class WindturbineState:
    """Windturbine class."""

    cumulative_production_in_watt_hour: float

    def self_copy(
        self,
    ):
        """Copy the WindturbineState."""
        return WindturbineState(
            self.cumulative_production_in_watt_hour,
        )
