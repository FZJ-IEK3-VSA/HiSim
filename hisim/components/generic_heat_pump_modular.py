""" Modular Heat Pump Class together with Configuration and State. """

# clean

# Generic/Built-in
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import numpy as np
from dataclasses_json import dataclass_json

import hisim.loadtypes as lt
from hisim import component as cp
from hisim.component import OpexCostDataClass, CapexCostDataClass

# Owned
from hisim import utils
from hisim.components import controller_l1_heatpump

from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiHelperClass, KpiTagEnumClass

__authors__ = "edited Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class HeatPumpConfig(cp.ConfigBase):

    """Configuration of a HeatPump."""

    building_name: str
    #: name of the device
    name: str
    #: priority of the device in energy management system: the higher the number the lower the priority
    source_weight: int
    #: manufacturer to search heat pump in data base
    manufacturer: str
    #: device name to search heat pump in data base
    device_name: str
    #: maximal thermal power of heat pump in W
    power_th: float
    #: usage of the heatpump: either for heating or for water heating
    water_vs_heating: lt.InandOutputType
    #: category of the heat pump: either heat pump or heating rod
    device_category: lt.HeatingSystems
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return ModularHeatPump.get_full_classname()

    @staticmethod
    def get_default_config_heating(
        building_name: str = "BUI1",
    ) -> "HeatPumpConfig":
        """Returns default configuration of a heat pump used for heating."""
        power_th: float = 6200  # W
        config = HeatPumpConfig(
            building_name=building_name,
            name="HeatingHeatPump",
            source_weight=1,
            manufacturer="Viessmann Werke GmbH & Co KG",
            device_name="Vitocal 300-A AWO-AC 301.B07",
            power_th=power_th,
            water_vs_heating=lt.InandOutputType.HEATING,
            device_category=lt.HeatingSystems.HEAT_PUMP,
            co2_footprint=power_th * 1e-3 * 165.84,  # value from emission_factros_and_costs_devices.csv
            cost=power_th * 1e-3 * 1513.74,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
        )
        return config

    @staticmethod
    def get_default_config_waterheating(
        building_name: str = "BUI1",
    ) -> "HeatPumpConfig":
        """Returns default configuration of a heat pump used for water heating."""
        power_th: float = 3000  # W
        config = HeatPumpConfig(
            building_name=building_name,
            name="DHWHeatPump",
            source_weight=1,
            manufacturer="Viessmann Werke GmbH & Co KG",
            device_name="Vitocal 300-A AWO-AC 301.B07",
            power_th=power_th,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            device_category=lt.HeatingSystems.HEAT_PUMP,
            co2_footprint=power_th * 1e-3 * 165.84,  # value from emission_factros_and_costs_devices.csv
            cost=power_th * 1e-3 * 1513.74,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
        )
        return config

    @staticmethod
    def get_default_config_heating_electric(building_name: str = "BUI1",) -> "HeatPumpConfig":
        """Returns default configuartion of simple electrical heating system with a COP of one."""
        power_th: float = 6200  # W
        config = HeatPumpConfig(
            building_name=building_name,
            name="HeatingHeatingRod",
            source_weight=1,
            manufacturer="dummy",
            device_name="HeatingRod",
            power_th=power_th,
            water_vs_heating=lt.InandOutputType.HEATING,
            device_category=lt.HeatingSystems.ELECTRIC_HEATING,
            co2_footprint=power_th * 1e-3 * 1.21,  # value from emission_factros_and_costs_devices.csv
            cost=4635,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
        )
        return config

    @staticmethod
    def get_default_config_waterheating_electric(
        building_name: str = "BUI1",
    ) -> "HeatPumpConfig":
        """Returns default configuration of electrical heating rod for boiler."""
        power_th: float = 3000  # W
        config = HeatPumpConfig(
            building_name=building_name,
            name="DHWHeatingRod",
            source_weight=1,
            manufacturer="dummy",
            device_name="HeatingRod",
            power_th=power_th,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            device_category=lt.HeatingSystems.ELECTRIC_HEATING,
            co2_footprint=power_th * 1e-3 * 1.21,  # value from emission_factros_and_costs_devices.csv
            cost=4635,  # value from emission_factros_and_costs_devices.csv
            lifetime=20,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
        )
        return config

    @classmethod
    def get_scaled_waterheating_to_number_of_apartments(
        cls,
        number_of_apartments: float,
        default_power_in_watt: float = 3000,
        name: str = "DHWHeatPump",
        building_name: str = "BUI1",
    ) -> "HeatPumpConfig":
        """Gets a default heat pump with scaling according to number of apartments."""

        # scale with number of apartments
        power_th_in_watt: float = default_power_in_watt * number_of_apartments
        config = HeatPumpConfig(
            building_name=building_name,
            name=name,
            source_weight=1,
            manufacturer="Viessmann Werke GmbH & Co KG",
            device_name="Vitocal 300-A AWO-AC 301.B07",
            power_th=power_th_in_watt,
            water_vs_heating=lt.InandOutputType.WATER_HEATING,
            device_category=lt.HeatingSystems.HEAT_PUMP,
            co2_footprint=power_th_in_watt * 1e-3 * 165.84,  # value from emission_factros_and_costs_devices.csv
            cost=power_th_in_watt * 1e-3 * 1513.74,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
        )
        return config


class ModularHeatPumpState:

    """Modular heat pump state saves the state of the heat pump."""

    def __init__(self, state: int = 0):
        """Initializes state."""
        self.state = state

    def clone(self) -> "ModularHeatPumpState":
        """Creates copy of state."""
        return ModularHeatPumpState(state=self.state)


class ModularHeatPump(cp.Component):

    """Heat pump implementation.

    The generic_heatpump_modular differs to generic_heatpump in the sense that the minimal runtime is not in the component,
    but in the related controller.
    This implementation does not consider cooling of building_names.

    Components to connect to:
    (1) Weather
    (2) Heat Pump Controller (controller_l1_heatpump)
    """

    # Inputs
    TemperatureOutside = "TemperatureOutside"
    HeatControllerTargetPercentage = "HeatControllerTargetPercentage"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    ElectricityOutput = "ElectricityOutput"
    PowerModifier = "PowerModifier"

    @utils.measure_execution_time
    def __init__(
        self,
        config: HeatPumpConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ):
        """Initialize the class."""
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.config = config
        self.build()
        self.state = ModularHeatPumpState()
        self.previous_state = ModularHeatPumpState()

        if my_simulation_parameters.surplus_control:
            postprocessing_flag = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                self.config.water_vs_heating,
                self.config.device_category,
            ]
        else:
            postprocessing_flag = [
                lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                self.config.water_vs_heating,
                self.config.device_category,
            ]

        # Inputs - Mandatories
        self.temperature_outside_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            lt.LoadTypes.ANY,
            lt.Units.CELSIUS,
            mandatory=True,
        )

        self.heat_controller_power_modifier_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HeatControllerTargetPercentage,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            mandatory=True,
        )

        # Outputs
        self.thermal_power_delicered_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDelivered,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            postprocessing_flag=[self.config.water_vs_heating],
            output_description="Thermal Power Delivered",
        )
        self.electricity_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            postprocessing_flag=postprocessing_flag,
            output_description="Electricity Output",
        )

        self.power_modifier_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.PowerModifier,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            postprocessing_flag=[],
            output_description="Power Modifier",
        )

        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_controller_l1_heatpump())

    def get_default_connections_from_weather(self):
        """Sets default connections of Weather."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            cp.ComponentConnection(
                ModularHeatPump.TemperatureOutside,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        return connections

    def get_default_connections_from_controller_l1_heatpump(self):
        """Sets default connections of heat pump controller."""

        connections = []
        controller_classname = controller_l1_heatpump.L1HeatPumpController.get_classname()
        connections.append(
            cp.ComponentConnection(
                ModularHeatPump.HeatControllerTargetPercentage,
                controller_classname,
                controller_l1_heatpump.L1HeatPumpController.HeatControllerTargetPercentage,
            )
        )
        return connections

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def build(self):
        """Initialization function of Modular Heat Pump."""
        # Retrieves heat pump from database - BEGIN
        heat_pumps_database = utils.load_smart_appliance("Heat Pump")

        heat_pump_found = False
        heat_pump = None
        for heat_pump in heat_pumps_database:
            if heat_pump["Manufacturer"] == self.config.manufacturer and heat_pump["Name"] == self.config.device_name:
                heat_pump_found = True
                break

        if not heat_pump_found or heat_pump is None:
            raise Exception("Heat pump model not registered in the database")

        # Interpolates COP data from the database
        self.cop_ref = []
        self.t_out_ref = []
        for heat_pump_cops in heat_pump["COP"]:
            self.t_out_ref.append(float([*heat_pump_cops][0][1:].split("/")[0]))
            self.cop_ref.append(float([*heat_pump_cops.values()][0]))
        self.cop_coef = np.polyfit(self.t_out_ref, self.cop_ref, 1)

        # Writes info to report
        self.write_to_report()

    def cal_cop(self, t_out: float) -> float:
        """Returns coefficient of performance of selected heat pump."""
        val: float = self.cop_coef[0] * t_out + self.cop_coef[1]
        return val

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def write_to_report(self) -> List[str]:
        """Writes relevant data to report."""
        return self.config.get_string_dict()

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Iteration of heat pump simulation."""

        # Inputs
        target_percentage = stsv.get_input_value(self.heat_controller_power_modifier_channel)

        temperature_outside: float = stsv.get_input_value(self.temperature_outside_channel)
        cop = self.cal_cop(temperature_outside)
        electric_power = self.config.power_th / cop

        # calculate modulation
        if target_percentage > 0:
            power_modifier = target_percentage
        elif target_percentage == 0:
            power_modifier = 0
        else:
            raise ValueError("`target_modifiert` needs to be a positive number.")

        power_modifier = min(1, power_modifier)

        stsv.set_output_value(self.thermal_power_delicered_channel, self.config.power_th * power_modifier)
        stsv.set_output_value(self.power_modifier_channel, power_modifier)

        stsv.set_output_value(self.electricity_output_channel, electric_power * power_modifier)

    @staticmethod
    def get_cost_capex(config: HeatPumpConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.cost / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_footprint / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.cost,
            device_co2_footprint_in_kg=config.co2_footprint,
            lifetime_in_years=config.lifetime,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of maintenance costs snd write total energy consumption to component-config.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV
        """
        #: consumption of the heatpump in kWh
        consumption_in_kwh: float

        for index, output in enumerate(all_outputs):
            if (
                    output.component_name == self.component_name
                    and output.load_type == lt.LoadTypes.ELECTRICITY and output.field_name == self.ElectricityOutput
            ):  # Todo: check component name from system_setups: find another way of using only heatpump-outputs
                consumption_in_kwh = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            consumption_in_kwh=consumption_in_kwh,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour: Optional[float] = None
        dhw_heat_pump_heating_energy_output_in_kilowatt_hour: Optional[float] = None
        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if output.field_name == self.ElectricityOutput:
                    dhw_heat_pump_total_electricity_consumption_in_watt_series = postprocessing_results.iloc[:, index]
                    dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_heat_pump_total_electricity_consumption_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                elif output.field_name == self.ThermalPowerDelivered:
                    dhw_heat_pump_heating_power_output_in_watt_series = postprocessing_results.iloc[:, index]
                    dhw_heat_pump_heating_energy_output_in_kilowatt_hour = (
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_heat_pump_heating_power_output_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )

        dhw_heatpump_total_electricity_consumption_entry = KpiEntry(
            name="DHW heat pump total electricity consumption",
            unit="kWh",
            value=dhw_heat_pump_total_electricity_consumption_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
            description=self.component_name
        )
        list_of_kpi_entries.append(dhw_heatpump_total_electricity_consumption_entry)

        dhw_heatpump_heating_energy_output_entry = KpiEntry(
            name="Heating output energy of DHW heat pump",
            unit="kWh",
            value=dhw_heat_pump_heating_energy_output_in_kilowatt_hour,
            tag=KpiTagEnumClass.HEATPUMP_DOMESTIC_HOT_WATER,
            description=self.component_name
        )
        list_of_kpi_entries.append(dhw_heatpump_heating_energy_output_entry)

        return list_of_kpi_entries
