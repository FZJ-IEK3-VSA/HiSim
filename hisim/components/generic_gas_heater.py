"""Gas Heater Module."""

# clean
# Owned
import importlib
from dataclasses import dataclass
from typing import List, Any, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentConnection,
    SingleTimeStepValues,
    ComponentInput,
    ComponentOutput,
    ConfigBase,
    OpexCostDataClass,
    DisplayConfig,
)
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.simulationparameters import SimulationParameters

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = ""


@dataclass_json
@dataclass
class GenericGasHeaterConfig(ConfigBase):
    """Configuration of the GasHeater class."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return GasHeater.get_full_classname()

    name: str
    is_modulating: bool
    minimal_thermal_power_in_watt: float  # [W]
    maximal_thermal_power_in_watt: float  # [W]
    eff_th_min: float  # [-]
    eff_th_max: float  # [-]
    delta_temperature_in_celsius: float  # [°C]
    maximal_mass_flow_in_kilogram_per_second: float  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
    maximal_temperature_in_celsius: float  # [°C]
    temperature_delta_in_celsius: float  # [°C]
    maximal_power_in_watt: float  # [W]
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: consumption of the car in kWh or l
    consumption: float

    @classmethod
    def get_default_gasheater_config(
        cls,
    ) -> Any:
        """Get a default Building."""
        maximal_power_in_watt: float = 12_000  # W
        config = GenericGasHeaterConfig(
            name="GenericGasHeater",
            temperature_delta_in_celsius=10,
            maximal_power_in_watt=maximal_power_in_watt,
            is_modulating=True,
            minimal_thermal_power_in_watt=1_000,  # [W]
            maximal_thermal_power_in_watt=maximal_power_in_watt,  # [W]
            eff_th_min=0.60,  # [-]
            eff_th_max=0.90,  # [-]
            delta_temperature_in_celsius=25,
            maximal_mass_flow_in_kilogram_per_second=maximal_power_in_watt
            / (4180 * 25),  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
            maximal_temperature_in_celsius=80,  # [°C])
            co2_footprint=maximal_power_in_watt * 1e-3 * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=7416,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption=0,
        )
        return config

    @classmethod
    def get_scaled_gasheater_config(cls, heating_load_of_building_in_watt: float) -> "GenericGasHeaterConfig":
        """Get a default Building."""
        maximal_power_in_watt: float = heating_load_of_building_in_watt  # W
        config = GenericGasHeaterConfig(
            name="GenericGasHeater",
            temperature_delta_in_celsius=10,
            maximal_power_in_watt=maximal_power_in_watt,
            is_modulating=True,
            minimal_thermal_power_in_watt=1_000,  # [W]
            maximal_thermal_power_in_watt=maximal_power_in_watt,  # [W]
            eff_th_min=0.60,  # [-]
            eff_th_max=0.90,  # [-]
            delta_temperature_in_celsius=25,
            maximal_mass_flow_in_kilogram_per_second=maximal_power_in_watt
            / (4180 * 25),  # kg/s ## -> ~0.07 P_th_max / (4180 * delta_T)
            maximal_temperature_in_celsius=80,  # [°C])
            co2_footprint=maximal_power_in_watt * 1e-3 * 49.47,  # value from emission_factros_and_costs_devices.csv
            cost=7416,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.03,  # source: VDI2067-1
            consumption=0,
        )
        return config


class GasHeater(Component):
    """GasHeater class.

    Get Control Signal and calculate on base of it Massflow and Temperature of Massflow.
    """

    # Input
    ControlSignal = "ControlSignal"  # at which Procentage is the GasHeater modulating [0..1]
    MassflowInputTemperature = "MassflowInputTemperature"

    # Output
    MassflowOutput = "Hot Water Energy Output"
    MassflowOutputTemperature = "MassflowOutputTemperature"
    GasDemand = "GasDemand"
    ThermalOutputPower = "ThermalOutputPower"

    # @utils.graph_call_path_factory(max_depth=2, memory_flag=True, file_name="call_path")
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericGasHeaterConfig,
        my_display_config: DisplayConfig = DisplayConfig(display_in_webtool=True),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.gasheater_config = config
        super().__init__(
            name=self.gasheater_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            GasHeater.ControlSignal,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            True,
        )
        self.mass_flow_input_tempertaure_channel: ComponentInput = self.add_input(
            self.component_name,
            GasHeater.MassflowInputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            True,
        )

        self.mass_flow_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            GasHeater.MassflowOutput,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description=f"here a description for {self.MassflowOutput} will follow.",
        )
        self.mass_flow_output_temperature_channel: ComponentOutput = self.add_output(
            self.component_name,
            GasHeater.MassflowOutputTemperature,
            lt.LoadTypes.WATER,
            lt.Units.CELSIUS,
            output_description=f"here a description for {self.MassflowOutputTemperature} will follow.",
        )
        self.gas_demand_channel: ComponentOutput = self.add_output(
            self.component_name,
            GasHeater.GasDemand,
            lt.LoadTypes.GAS,
            lt.Units.KWH,
            output_description=f"here a description for {self.GasDemand} will follow.",
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
        )
        self.thermal_output_power_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPower,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description=f"here a description for {self.ThermalOutputPower} will follow.",
            postprocessing_flag=[
                lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
            ],
        )

        self.minimal_thermal_power_in_watt = self.gasheater_config.minimal_thermal_power_in_watt
        self.maximal_thermal_power_in_watt = self.gasheater_config.maximal_power_in_watt
        self.eff_th_min = self.gasheater_config.eff_th_min
        self.eff_th_max = self.gasheater_config.eff_th_max
        self.maximal_temperature_in_celsius = self.gasheater_config.maximal_temperature_in_celsius
        self.temperature_delta_in_celsius = self.gasheater_config.temperature_delta_in_celsius

        self.add_default_connections(self.get_default_connections_from_controller_l1_generic_gas_heater())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())

    def get_default_connections_from_controller_l1_generic_gas_heater(
        self,
    ):
        """Get Controller L1 Gas Heater default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.controller_l1_generic_gas_heater"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "GenericGasHeaterControllerL1")
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GasHeater.ControlSignal,
                l1_controller_classname,
                component_class.ControlSignalToGasHeater,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get Simple hot water storage default connections."""
        # use importlib for importing the other component in order to avoid circular-import errors
        component_module_name = "hisim.components.simple_hot_water_storage"
        component_module = importlib.import_module(name=component_module_name)
        component_class = getattr(component_module, "SimpleHotWaterStorage")
        connections = []
        hws_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                GasHeater.MassflowInputTemperature,
                hws_classname,
                component_class.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        """Write a report."""
        return self.gasheater_config.get_string_dict()

    def i_save_state(self) -> None:
        """Save the current state."""
        pass

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulate the gas heater."""
        control_signal = stsv.get_input_value(self.control_signal_channel)
        if control_signal > 1:
            raise Exception("Expected a control signal between 0 and 1")
        if control_signal < 0:
            raise Exception("Expected a control signal between 0 and 1")

        # Calculate Eff
        d_eff_th = self.eff_th_max - self.eff_th_min

        if control_signal * self.maximal_thermal_power_in_watt < self.minimal_thermal_power_in_watt:
            maximum_power = self.minimal_thermal_power_in_watt
            eff_th_real = self.eff_th_min
        else:
            maximum_power = control_signal * self.maximal_thermal_power_in_watt
            eff_th_real = self.eff_th_min + d_eff_th * control_signal

        gas_power_in_watt = maximum_power * eff_th_real * control_signal
        c_w = 4182
        mass_flow_out_temperature_in_celsius = self.temperature_delta_in_celsius + stsv.get_input_value(
            self.mass_flow_input_tempertaure_channel
        )
        mass_flow_out_in_kg_per_s = gas_power_in_watt / (c_w * self.temperature_delta_in_celsius)
        # p_th = (
        #     c_w * mass_flow_out_in_kg_per_s * (mass_flow_out_temperature_in_celsius - stsv.get_input_value(self.mass_flow_input_tempertaure_channel))
        # )
        gas_demand_in_kwh = gas_power_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3.6e6

        stsv.set_output_value(self.thermal_output_power_channel, gas_power_in_watt)  # efficiency
        stsv.set_output_value(
            self.mass_flow_output_temperature_channel,
            mass_flow_out_temperature_in_celsius,
        )  # efficiency
        stsv.set_output_value(self.mass_flow_output_channel, mass_flow_out_in_kg_per_s)  # efficiency
        stsv.set_output_value(self.gas_demand_channel, gas_demand_in_kwh)  # gas consumption

    @staticmethod
    def get_cost_capex(config: GenericGasHeaterConfig) -> Tuple[float, float, float]:
        """Returns investment cost, CO2 emissions and lifetime."""
        return config.cost, config.co2_footprint, config.lifetime

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of energy and maintenance costs."""
        for index, output in enumerate(all_outputs):
            if output.component_name == self.config.name and output.load_type == lt.LoadTypes.GAS:
                self.config.consumption = round(sum(postprocessing_results.iloc[:, index]), 1)
        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        co2_per_unit = emissions_and_cost_factors.gas_footprint_in_kg_per_kwh
        euro_per_unit = emissions_and_cost_factors.gas_costs_in_euro_per_kwh

        opex_cost_per_simulated_period_in_euro = self.config.consumption * euro_per_unit
        co2_per_simulated_period_in_kg = self.config.consumption * co2_per_unit

        opex_cost_per_simulated_period_in_euro += self.calc_maintenance_cost()
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=opex_cost_per_simulated_period_in_euro,
            co2_footprint=co2_per_simulated_period_in_kg,
            consumption=self.config.consumption,
        )

        return opex_cost_data_class
