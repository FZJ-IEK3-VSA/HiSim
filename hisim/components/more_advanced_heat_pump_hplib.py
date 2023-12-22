"""Advanced heat pump module.

See library on https://github.com/FZJ-IEK3-VSA/hplib/tree/main/hplib

two controller: one dhw controller and one building heating controller

priority on dhw, if there is a demand from both in one timestep

preparation on district heating for water/water heatpumps

don't work with regulated generic hp group --> todo

"""
from typing import Any, List, Optional, Tuple, Dict
import hashlib
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from dataclass_wizard import JSONWizard
import pandas as pd
from hplib import hplib as hpl
from hisim.component import (
    Component,
    ComponentInput,
    ComponentOutput,
    SingleTimeStepValues,
    ConfigBase,
    ComponentConnection,
    OpexCostDataClass,
)
from hisim.components import weather, simple_hot_water_storage,generic_hot_water_storage_modular, heat_distribution_system
from hisim.loadtypes import LoadTypes, Units, InandOutputType
from hisim.simulationparameters import SimulationParameters
from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim import log
from hisim.components.configuration import PhysicsConfig

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = ["Jonas Hoppe"]
__license__ = "-"
__version__ = ""
__maintainer__ = ""
__status__ = ""


@dataclass_json
@dataclass
class HeatPumpHplibConfig(ConfigBase):

    """HeatPumpHPLibConfig."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplib.get_full_classname()

    name: str
    model: str
    heat_source: str
    group_id: int
    heating_reference_temperature_in_celsius: float  # before t_in
    flow_temperature_in_celsius: float  # before t_out_val
    set_thermal_output_power_in_watt: float  # before p_th_set
    cycling_mode: bool
    minimum_running_time_in_seconds: Optional[int]
    minimum_idle_time_in_seconds: Optional[int]
    hx_building_temp_diff: Optional[float]          #to be used, if water/water or brine/water heatpump
    #: CO2 footprint of investment in kg
    co2_footprint: float
    #: cost for investment in Euro
    cost: float
    #: lifetime in years
    lifetime: float
    # maintenance cost as share of investment [0..1]
    maintenance_cost_as_percentage_of_investment: float
    #: consumption of the heatpump in kWh
    consumption: float

    @classmethod
    def get_default_generic_advanced_hp_lib(
        cls,
        set_thermal_output_power_in_watt: float = 8000,
        heating_reference_temperature_in_celsius: float = -7.0,
    ) -> "HeatPumpHplibConfig":
        """Gets a default HPLib Heat Pump.

        see default values for air/water hp on:
        https://github.com/FZJ-IEK3-VSA/hplib/blob/main/hplib/hplib.py l.135 "fit_p_th_ref.
        """
        return HeatPumpHplibConfig(
            name="AdvancedHeatPumpHPLib",
            model="Generic",
            heat_source="air",
            group_id=4,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            flow_temperature_in_celsius=52,
            set_thermal_output_power_in_watt=set_thermal_output_power_in_watt,
            cycling_mode=True,
            minimum_running_time_in_seconds=600,
            minimum_idle_time_in_seconds=600,
            hx_building_temp_diff=2,
            co2_footprint=set_thermal_output_power_in_watt
            * 1e-3
            * 165.84,  # value from emission_factros_and_costs_devices.csv
            cost=set_thermal_output_power_in_watt
            * 1e-3
            * 1513.74,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
            consumption=0,
        )

    @classmethod
    def get_scaled_advanced_hp_lib(
        cls,
        heating_load_of_building_in_watt: float,
        heating_reference_temperature_in_celsius: float = -7.0,
    ) -> "HeatPumpHplibConfig":
        """Gets a default heat pump with scaling according to heating load of the building."""

        set_thermal_output_power_in_watt = heating_load_of_building_in_watt

        return HeatPumpHplibConfig(
            name="AdvancedHeatPumpHPLib",
            model="Generic",
            heat_source="air",
            group_id=4,
            heating_reference_temperature_in_celsius=heating_reference_temperature_in_celsius,
            flow_temperature_in_celsius=52,
            set_thermal_output_power_in_watt=set_thermal_output_power_in_watt,
            cycling_mode=True,
            minimum_running_time_in_seconds=600,
            minimum_idle_time_in_seconds=600,
            hx_building_temp_diff=2,
            co2_footprint=set_thermal_output_power_in_watt
            * 1e-3
            * 165.84,  # value from emission_factros_and_costs_devices.csv
            cost=set_thermal_output_power_in_watt
            * 1e-3
            * 1513.74,  # value from emission_factros_and_costs_devices.csv
            lifetime=10,  # value from emission_factros_and_costs_devices.csv
            maintenance_cost_as_percentage_of_investment=0.025,  # source:  VDI2067-1
            consumption=0,
        )


class HeatPumpHplib(Component):

    # Inputs
    OnOffSwitchHotWater = "OnOffSwitchHotWater"  # 1 = on hot Water,  0 = 0ff
    OnOffSwitchDHW = "OnOffSwitchDHW"  #2 = on DHW , 0 = 0ff
    ThermalPowerIsConstantforDHW = "ThermalPowerisConstantforDHW"  # true/false
    MaxThermalPowerforDHW = "MaxThermalPowerforDHW"  # max. Leistungswert
    TemperatureInputPrimary = "TemperatureInputPrimary"  # °C
    TemperatureInputSecondary_HotWater = "TemperatureInputSecondaryHotWater"  # °C
    TemperatureInputSecondary_DHW = "TemperatureInputSecondaryDWH"  # °C
    TemperatureAmbient = "TemperatureAmbient"  # °C


    # Outputs
    ThermalOutputPowerHotWater = "ThermalOutputPowerHotWater"  # W
    ThermalOutputPowerDHW = "ThermalOutputPowerDHW"  # W
    ThermalOutputPowerGesamt = "ThermalOutputPowerWholeHeatpump" #W
    ElectricalInputPowerHotWater = "ElectricalInputPowerHotWater"  # W
    ElectricalInputPowerDHW = "ElectricalInputPowerDHW"  # W
    ElectricalInputPowerGesamt = "ElectricalInputPowerWholeHeatpump"
    COP = "COP"  # -
    EER = "EER"  # -
    heatpumpOnOffState = "OnOffStateHeatpump"
    TemperatureOutputHotWater = "TemperatureOutputHotWater"  # °C
    TemperatureOutputDHW = "TemperatureOutputDHW"  # °C
    MassFlowOutputHotWater = "MassFlowOutputHotWater"  # kg/s
    MassFlowOutputDHW = "MassFlowOutputDHW"  # kg/s
    TimeOn = "TimeOn"  # s
    TimeOff = "TimeOff"  # s
    ThermalPowerFromEnvironment = "ThermalPowerInputFromEnvironment"   #W
    mdotWaterPrimary = "MassflowPrimary"                  # kg/s
    WaterTemperaturePrimaryIn = "TemperaturePrimaryIn"          # °C
    WaterTemperaturePrimaryOut = "TemperaturePrimaryOut"          # °C

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpHplibConfig,
    ):

        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        # caching for hplib simulation
        self.calculation_cache: Dict = {}

        self.model = config.model

        self.group_id = config.group_id

        self.t_in = config.heating_reference_temperature_in_celsius

        self.t_out_val = config.flow_temperature_in_celsius

        self.p_th_set = config.set_thermal_output_power_in_watt

        self.cycling_mode = config.cycling_mode

        self.minimum_running_time_in_seconds = config.minimum_running_time_in_seconds

        self.minimum_idle_time_in_seconds = config.minimum_idle_time_in_seconds

        self.hx_building_temp_diff = config.hx_building_temp_diff

        self.heat_source = config.heat_source

        #self.on_off: int = 0

        postprocessing_flag = [InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]

        # Component has states
        self.state = HeatPumpState(
            time_on=0, time_off=0, time_on_cooling=0, on_off_previous=0
        )
        self.previous_state = self.state.self_copy()

        # Load parameters from heat pump database
        self.parameters = hpl.get_parameters(
            self.model, self.group_id, self.t_in, self.t_out_val, self.p_th_set
        )

        self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius = (
            PhysicsConfig.water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin
        )

        #protect erros for Water/Water Heatpumps
        if self.parameters['Group'].iloc[0] == 1.0 or self.parameters['Group'].iloc[0] == 4.0:
            if self.heat_source != "air":
                raise KeyError("WP Modell passt nicht zu in Eingangsparameter angegebenen HeatSource!")
        if self.parameters['Group'].iloc[0] == 2.0 or self.parameters['Group'].iloc[0] == 5.0:
            if self.heat_source != "brine":
                raise KeyError("WP Modell passt nicht zu in Eingangsparameter angegebenen HeatSource!")
        if self.parameters['Group'].iloc[0] == 3.0 or self.parameters['Group'].iloc[0] == 6.0:
            if self.heat_source != "water":
                raise  KeyError("WP Modell passt nicht zu in Eingangsparameter angegebenen HeatSource!")




        # Define component inputs
        self.on_off_switch_hotWater: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.OnOffSwitchHotWater,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            mandatory=True,
        )

        # Define component inputs
        self.on_off_switch_DHW: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.OnOffSwitchDHW,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            mandatory=True,
        )

        self.const_thermal_power_truefalse_DHW: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ThermalPowerIsConstantforDHW,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            mandatory=True,
        )

        self.const_thermal_power_value_DHW: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.MaxThermalPowerforDHW,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            mandatory=True,
        )


        self.t_in_primary: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputPrimary,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.t_in_secondary_hot_water: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputSecondary_HotWater,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.t_in_secondary_dhw: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInputSecondary_DHW,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.t_amb: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureAmbient,
            load_type=LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        # Define component outputs
        self.p_th_hot_water: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerHotWater,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description=("Thermal output power hot Water Storage in Watt"),
        )

        self.p_th_dhw: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerDHW,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description=("Thermal output power dhw Storage in Watt"),
        )

        self.p_th_ges: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalOutputPowerGesamt,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Thermal output power for whole HP in Watt",
        )

        self.p_el_hot_water: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerHotWater,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Electricity input power for Hot Water in Watt",
        )

        self.p_el_dhw: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerDHW,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            output_description="Electricity input power for DHW in Watt",
        )

        self.p_el_ges: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricalInputPowerGesamt,
            load_type=LoadTypes.ELECTRICITY,
            unit=Units.WATT,
            postprocessing_flag=postprocessing_flag,
            output_description="Electricity input power for whole HP in Watt",
        )

        self.cop: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.COP,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="COP",
        )
        self.eer: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.EER,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="EER",
        )

        self.heatpump_state: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.heatpumpOnOffState,
            load_type=LoadTypes.ANY,
            unit=Units.ANY,
            output_description="OnOffState",
        )

        self.t_out_hot_water: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutputHotWater,
            load_type=LoadTypes.HEATING,
            unit=Units.CELSIUS,
            output_description="Temperature Output hot Water in °C",
        )
        self.t_out_dhw: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutputDHW,
            load_type=LoadTypes.HEATING,
            unit=Units.CELSIUS,
            output_description="Temperature Output DHW Water in °C",
        )

        self.m_dot_hot_water: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutputHotWater,
            load_type=LoadTypes.VOLUME,
            unit=Units.KG_PER_SEC,
            output_description="Mass flow output",
        )
        self.m_dot_dhw: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutputDHW,
            load_type=LoadTypes.VOLUME,
            unit=Units.KG_PER_SEC,
            output_description="Mass flow output",
        )

        self.time_on: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOn,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned on",
        )

        self.time_off: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TimeOff,
            load_type=LoadTypes.TIME,
            unit=Units.SECONDS,
            output_description="Time turned off",
        )

        self.thermal_power_from_environment: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerFromEnvironment,
            load_type=LoadTypes.HEATING,
            unit=Units.WATT,
            output_description="Thermal Input Power from Environment",
        )

        if self.parameters['Group'].iloc[0] == 3.0 or self.parameters['Group'].iloc[0] == 6.0:
            self.m_dot_water_primary_dhnet: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.mdotWaterPrimary,
                load_type=LoadTypes.VOLUME,
                unit=Units.KG_PER_SEC,
                output_description="Massflow of Water from District Heating Net",
            )
            self.temp_water_primary_hx_in: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.WaterTemperaturePrimaryIn,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature of Water from District Heating Net In HX",
            )
            self.temp_water_primary_hx_out: ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.WaterTemperaturePrimaryOut,
                load_type=LoadTypes.TEMPERATURE,
                unit=Units.CELSIUS,
                output_description="Temperature of Water to District Heating Net Out HX",
            )


        self.add_default_connections(
            self.get_default_connections_from_heat_pump_controller_hot_water()
        )
        self.add_default_connections(
            self.get_default_connections_from_heat_pump_controller_dhw()
        )
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_simple_hot_water_storage())
        self.add_default_connections(self.get_default_connections_from_dhw_storage())

    def get_default_connections_from_heat_pump_controller_hot_water(self,):
        """Get default connections."""
        connections = []
        hpc_classname = HeatPumpHplibControllerHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplib.OnOffSwitchHotWater, hpc_classname, HeatPumpHplibControllerHotWaterStorage.State_HotWater,
            )
        )
        return connections

    def get_default_connections_from_heat_pump_controller_dhw(self,):
        """Get default connections."""
        connections = []
        hpc_dhw_classname = HeatPumpHplibControllerDHW.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplib.OnOffSwitchDHW, hpc_dhw_classname, HeatPumpHplibControllerDHW.State_dhw,
            )
        )
        connections.append(
            ComponentConnection(
                HeatPumpHplib.ThermalPowerIsConstantforDHW, hpc_dhw_classname, HeatPumpHplibControllerDHW.ThermalPower_Bedingung_for_konst_dhw,
            )
        )
        connections.append(
            ComponentConnection(
                HeatPumpHplib.MaxThermalPowerforDHW, hpc_dhw_classname, HeatPumpHplibControllerDHW.Value_Max_Thermal_Power_for_dhw,
            )
        )
        return connections

    def get_default_connections_from_weather(self,):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplib.TemperatureAmbient,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(self,):
        """Get simple hot water storage default connections."""
        connections = []
        hws_classname = simple_hot_water_storage.SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplib.TemperatureInputSecondary_HotWater,
                hws_classname,
                simple_hot_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_dhw_storage(self,):
        """Get simple hot water storage default connections."""
        connections = []
        dhw_classname = generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplib.TemperatureInputSecondary_DHW,
                dhw_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def write_to_report(self):
        """Write configuration to the report."""
        return self.config.get_string_dict()

    def i_save_state(self) -> None:
        """Save state."""
        self.previous_state = self.state.self_copy()
        # pass

    def i_restore_state(self) -> None:
        """Restore state."""
        self.state = self.previous_state.self_copy()
        # pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doubelcheck."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare simulation."""
        pass

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the component."""

        # Load input values
        on_off: float
        on_off_HotWater: float = stsv.get_input_value(self.on_off_switch_hotWater)
        on_off_DHW: float = stsv.get_input_value(self.on_off_switch_DHW)
        const_thermal_power_truefalse_DHW: bool = stsv.get_input_value(self.const_thermal_power_truefalse_DHW)
        const_thermal_power_value_DHW: float = stsv.get_input_value(self.const_thermal_power_value_DHW)
        t_in_primary = stsv.get_input_value(self.t_in_primary)
        t_in_secondary_hot_water = stsv.get_input_value(self.t_in_secondary_hot_water)
        t_in_secondary_dhw = stsv.get_input_value(self.t_in_secondary_dhw)
        t_amb = stsv.get_input_value(self.t_amb)
        time_on_heating = self.state.time_on
        time_on_cooling = self.state.time_on_cooling
        time_off = self.state.time_off


        if on_off_DHW != 0:
            on_off = on_off_DHW
        else:
            on_off = on_off_HotWater

        # cycling means periodic turning on and off of the heat pump
        if self.cycling_mode is True:

            # Parameter
            time_on_min = self.minimum_running_time_in_seconds  # [s]
            time_off_min = self.minimum_idle_time_in_seconds
            on_off_previous = self.state.on_off_previous

            if time_on_min is None or time_off_min is None:
                raise ValueError(
                    "When the cycling mode is true, the minimum running time and minimum idle time of the heat pump must be given an integer value."
                )

            # Overwrite on_off to realize minimum time of or time off
            if on_off_previous == 1 and time_on_heating < time_on_min:
                on_off = 1
            elif on_off_previous == 2 and time_on_heating < time_on_min:
                on_off = 2
            elif on_off_previous == -1 and time_on_cooling < time_on_min:
                on_off = -1
            elif on_off_previous == 0 and time_off < time_off_min:
                on_off = 0

        # heat pump is turned on and off only according to heat pump controller
        elif self.cycling_mode is False:
            pass
        else:
            raise ValueError("Cycling mode of the advanced hplib unknown.")


        if on_off_DHW != 0:
            on_off = on_off_DHW


        # OnOffSwitch
        if on_off == 1:
            # Calulate outputs for heating mode
            results = hpl.simulate(         #self.get_cached_results_or_run_hplib_simulation(
                t_in_primary=t_in_primary,
                t_in_secondary=t_in_secondary_hot_water,
                parameters=self.parameters,
                t_amb=t_amb,
                mode=1,
            )
            p_th_hot_water = results["P_th"].values[0]
            p_th_dhw = 0
            p_el_hot_water = results["P_el"].values[0]
            p_el_dhw = 0
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out_hot_water = results["T_out"].values[0]
            t_out_dhw = t_in_secondary_dhw
            m_dot_hot_water = results["m_dot"].values[0]
            m_dot_dhw = 0
            time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
            time_on_cooling = 0
            time_off = 0

        elif on_off == 2:                                  # Calculate outputs for dhw mode
            if const_thermal_power_truefalse_DHW == False:    # False=modulation
                results = hpl.simulate(             #self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_dhw,
                    parameters=self.parameters,
                    t_amb=t_amb,
                    mode=1,
                )
                p_th_dhw = results["P_th"].values[0]
                p_th_hot_water = 0
                p_el_dhw = results["P_el"].values[0]
                p_el_hot_water = 0
                cop = results["COP"].values[0]
                eer = results["EER"].values[0]
                t_out_hot_water = t_in_secondary_hot_water
                t_out_dhw =  results["T_out"].values[0]
                m_dot_hot_water = 0
                m_dot_dhw = results["m_dot"].values[0]
                time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
                time_on_cooling = 0
                time_off = 0
             #   print("t_out_WP_dhw  " + str(t_out_dhw))

            elif const_thermal_power_truefalse_DHW == True:             # True = constante thermische Leistung
                results = hpl.simulate(                     #self.get_cached_results_or_run_hplib_simulation(
                    t_in_primary=t_in_primary,
                    t_in_secondary=t_in_secondary_dhw,
                    parameters=self.parameters,
                    t_amb=t_amb,
                    mode=1,
                )
                p_th_dhw = const_thermal_power_value_DHW
                p_th_hot_water = 0
                cop = results["COP"].values[0]
                p_el_dhw = p_th_dhw/cop
                p_el_hot_water = 0
                eer = results["EER"].values[0]
                t_out_hot_water = t_in_secondary_hot_water
                t_out_dhw =  results["T_out"].values[0]
                m_dot_hot_water = 0
                m_dot_dhw = results["m_dot"].values[0]
                time_on_heating = time_on_heating + self.my_simulation_parameters.seconds_per_timestep
                time_on_cooling = 0
                time_off = 0


        elif on_off == -1:
            # Calulate outputs for cooling mode
            results = hpl.simulate(                             #self.get_cached_results_or_run_hplib_simulation(
                t_in_primary=t_in_primary,
                t_in_secondary=t_in_secondary_hot_water,
                parameters=self.parameters,
                t_amb=t_amb,
                mode=2,
            )
            p_th_hot_water = results["P_th"].values[0]
            p_th_dhw = 0
            p_el_hot_water = results["P_el"].values[0]
            p_el_dhw =  0
            cop = results["COP"].values[0]
            eer = results["EER"].values[0]
            t_out_hot_water = results["T_out"].values[0]
            t_out_dhw = t_out_hot_water
            m_dot_hot_water = results["m_dot"].values[0]
            m_dot_dhw = 0
            time_on_cooling = (
                time_on_cooling + self.my_simulation_parameters.seconds_per_timestep
            )
            time_on_heating = 0
            time_off = 0

        elif on_off == 0:
            # Calulate outputs for off mode
            p_th_hot_water = 0
            p_th_dhw = 0
            p_el_hot_water = 0
            p_el_dhw = 0
            # None values or nans will cause troubles in post processing, that is why there are not used here
            # cop = None
            # t_out = None
            cop = 0
            eer = 0
            t_out_hot_water = t_in_secondary_hot_water
            t_out_dhw = t_in_secondary_dhw
            m_dot_hot_water = 0
            m_dot_dhw = 0
            time_off = time_off + self.my_simulation_parameters.seconds_per_timestep
            time_on_heating = 0
            time_on_cooling = 0

        else:
            raise ValueError("Unknown mode for Advanced HPLib On_Off.")


        if self.parameters['Group'].iloc[0] == 3.0 or self.parameters['Group'].iloc[0] == 6.0:
            #todo: variability of massflow. now there is a fix temperaturdiffernz between inlet and outlet which calculate the massflow

            q_dot_entzugsleistung = ((p_th_dhw + p_th_hot_water) - (p_el_dhw + p_el_hot_water))
            m_dot_water_primary = q_dot_entzugsleistung/(self.specific_heat_capacity_of_water_in_joule_per_kilogram_per_celsius*self.hx_building_temp_diff)
            t_out_primary = t_in_primary-self.hx_building_temp_diff
            stsv.set_output_value(self.m_dot_water_primary_dhnet, m_dot_water_primary)
            stsv.set_output_value(self.temp_water_primary_hx_in, t_in_primary)
            stsv.set_output_value(self.temp_water_primary_hx_out, t_out_primary)


        # write values for output time series
        stsv.set_output_value(self.p_th_hot_water, p_th_hot_water)
        stsv.set_output_value(self.p_th_dhw, p_th_dhw)
        stsv.set_output_value(self.p_th_ges, p_th_dhw+p_th_hot_water)
        stsv.set_output_value(self.p_el_hot_water, p_el_hot_water)
        stsv.set_output_value(self.p_el_dhw, p_el_dhw)
        stsv.set_output_value(self.p_el_ges, p_el_dhw+p_el_hot_water)
        stsv.set_output_value(self.cop, cop)
        stsv.set_output_value(self.eer, eer)
        stsv.set_output_value(self.heatpump_state, on_off)
        stsv.set_output_value(self.t_out_hot_water, t_out_hot_water)
        stsv.set_output_value(self.t_out_dhw, t_out_dhw)
        stsv.set_output_value(self.m_dot_hot_water, m_dot_hot_water)
        stsv.set_output_value(self.m_dot_dhw, m_dot_dhw)
        stsv.set_output_value(self.time_on, time_on_heating)
        stsv.set_output_value(self.time_off, time_off)
        stsv.set_output_value(self.thermal_power_from_environment, (p_th_dhw+p_th_hot_water)-(p_el_dhw+p_el_hot_water))


        # write values to state
        self.state.time_on = time_on_heating
        self.state.time_on_cooling = time_on_cooling
        self.state.time_off = time_off
        self.state.on_off_previous = on_off

    @staticmethod
    def get_cost_capex(config: HeatPumpHplibConfig) -> Tuple[float, float, float]:
        """Returns investment cost, CO2 emissions and lifetime."""
        return config.cost, config.co2_footprint, config.lifetime

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of maintenance costs.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV
        """
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == "HeatPumpHPLib"
                and output.load_type == LoadTypes.ELECTRICITY
            ):  # Todo: check component name from system_setups: find another way of using only heatpump-outputs
                self.config.consumption = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
        opex_cost_data_class = OpexCostDataClass(
            opex_cost=self.calc_maintenance_cost(),
            co2_footprint=0,
            consumption=self.config.consumption,
        )

        return opex_cost_data_class

    def get_cached_results_or_run_hplib_simulation(
        self,
        t_in_primary: float,
        t_in_secondary: float,
        parameters: pd.DataFrame,
        t_amb: float,
        mode: int,
    ) -> Any:
        """Use caching of results of hplib simulation."""

        # rounding of variable values
        t_in_primary = round(t_in_primary, 1)
        t_in_secondary = round(t_in_secondary, 1)
        t_amb = round(t_amb, 1)

        my_data_class = CalculationRequest(
            t_in_primary=t_in_primary,
            t_in_secondary=t_in_secondary,
            t_amb=t_amb,
            mode=mode,
        )
        my_json_key = my_data_class.get_key()
        my_hash_key = hashlib.sha256(my_json_key.encode("utf-8")).hexdigest()

        if my_hash_key in self.calculation_cache:
            results = self.calculation_cache[my_hash_key]

        else:
            results = hpl.simulate(
                t_in_primary, t_in_secondary, parameters, t_amb, mode=mode
            )

            self.calculation_cache[my_hash_key] = results

        return results


@dataclass
class HeatPumpState:                                                        # anpassen, dass automatisch dhw priorisiert wird
    """HeatPumpState class."""

    time_on: int = 0
    time_off: int = 0
    time_on_cooling: int = 0
    on_off_previous: float = 0

    def self_copy(
        self,
    ):
        """Copy the Heat Pump State."""
        return HeatPumpState(
            self.time_on, self.time_off, self.time_on_cooling, self.on_off_previous
        )



@dataclass
class CalculationRequest(JSONWizard):

    """Class for caching hplib parameters so that hplib.simulate does not need to run so often."""

    t_in_primary: float
    t_in_secondary: float
    t_amb: float
    mode: int

    def get_key(self):
        """Get key of class with important parameters."""

        return (
            str(self.t_in_primary)
            + " "
            + str(self.t_in_secondary)
            + " "
            + str(self.t_amb)
            + " "
            + str(self.mode)
        )


# ===========================================================================
# try to implement a hplib controller l1
@dataclass_json
@dataclass
class HeatPumpHplibControllerHotWaterStorageL1Config(ConfigBase):

    """HeatPump Controller Config Class for building heating."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplibControllerHotWaterStorage.get_full_classname()

    name: str
    mode: int
    set_heating_threshold_outside_temperature_in_celsius: Optional[float]
    set_cooling_threshold_outside_temperature_in_celsius: Optional[float]
    temperature_offset_for_state_conditions_in_celsius: float
    heat_distribution_system_type: Any

    @classmethod
    def get_default_generic_heat_pump_controller_config(
            cls, heat_distribution_system_type: Any
    )-> "HeatPumpHplibControllerHotWaterStorageL1Config" :
        """Gets a default Generic Heat Pump Controller."""
        return HeatPumpHplibControllerHotWaterStorageL1Config(
            name="HeatPumpControllerHotWaterStorage",
            mode=1,
            set_heating_threshold_outside_temperature_in_celsius=None,
            set_cooling_threshold_outside_temperature_in_celsius=20.0,
            temperature_offset_for_state_conditions_in_celsius=5.0,
            heat_distribution_system_type=heat_distribution_system_type,
        )


class HeatPumpHplibControllerHotWaterStorage(Component):

    """Heat Pump Controller.

    It takes data from other
    components and sends signal to the heat pump for
    activation or deactivation.
    On/off Switch with respect to water temperature from storage.

    Parameters
    ----------
    t_air_heating: float
        Minimum comfortable temperature for residents
    t_air_cooling: float
        Maximum comfortable temperature for residents
    offset: float
        Temperature offset to compensate the hysteresis
        correction for the building temperature change
    mode : int
        Mode index for operation type for this heat pump

    """

    # Inputs
    WaterTemperatureInputFromHeatWaterStorage = (
        "WaterTemperatureInputFromHeatWaterStorage"
    )
    HeatingFlowTemperatureFromHeatDistributionSystem = (
        "HeatingFlowTemperatureFromHeatDistributionSystem"
    )

    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"

    SimpleHotWaterStorageTemperatureModifier = (
        "SimpleHotWaterStorageTemperatureModifier"
    )

    # Outputs
    State_HotWater = "State_HotWater"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatPumpHplibControllerHotWaterStorageL1Config,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_config = config
        super().__init__(
            self.heatpump_controller_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.heat_distribution_system_type = (
            self.heatpump_controller_config.heat_distribution_system_type
        )
        self.build(
            mode=self.heatpump_controller_config.mode,
            temperature_offset_for_state_conditions_in_celsius=self.heatpump_controller_config.temperature_offset_for_state_conditions_in_celsius,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromHeatWaterStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.heating_flow_temperature_from_heat_distribution_system_channel: ComponentInput = self.add_input(
            self.component_name,
            self.HeatingFlowTemperatureFromHeatDistributionSystem,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.daily_avg_outside_temperature_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.DailyAverageOutsideTemperature,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                True,
            )
        )

        self.simple_hot_water_storage_temperature_modifier_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.SimpleHotWaterStorageTemperatureModifier,
                LoadTypes.TEMPERATURE,
                Units.CELSIUS,
                mandatory=False,
            )
        )

        self.state_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_HotWater,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State_HotWater} will follow.",
        )

        self.controller_heatpumpmode: Any
        self.previous_heatpump_mode: Any

        self.add_default_connections(
            self.get_default_connections_from_heat_distribution_controller()
        )
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(
            self.get_default_connections_from_simple_hot_water_storage()
        )

    def get_default_connections_from_heat_distribution_controller(self,):
        """Get default connections."""
        connections = []
        hdsc_classname = (
            heat_distribution_system.HeatDistributionController.get_classname()
        )
        connections.append(
            ComponentConnection(
                HeatPumpHplibControllerHotWaterStorage.HeatingFlowTemperatureFromHeatDistributionSystem,
                hdsc_classname,
                heat_distribution_system.HeatDistributionController.HeatingFlowTemperature,
            )
        )
        return connections

    def get_default_connections_from_weather(self,):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibControllerHotWaterStorage.DailyAverageOutsideTemperature,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def get_default_connections_from_simple_hot_water_storage(self,):
        """Get simple hot water storage default connections."""
        connections = []
        hws_classname = simple_hot_water_storage.SimpleHotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibControllerHotWaterStorage.WaterTemperatureInputFromHeatWaterStorage,
                hws_classname,
                simple_hot_water_storage.SimpleHotWaterStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def build(
        self,
        mode: float,
        temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heatpumpmode = "off"
        self.previous_heatpump_mode = self.controller_heatpumpmode

        # Configuration
        self.mode = mode
        self.temperature_offset_for_state_conditions_in_celsius = (
            temperature_offset_for_state_conditions_in_celsius
        )

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_heatpump_mode = self.controller_heatpumpmode

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heatpumpmode = self.previous_heatpump_mode

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_config.get_string_dict()

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:
            # Retrieves inputs

            water_temperature_input_from_heat_water_storage_in_celsius = (
                stsv.get_input_value(self.water_temperature_input_channel)
            )

            heating_flow_temperature_from_heat_distribution_system = (
                stsv.get_input_value(
                    self.heating_flow_temperature_from_heat_distribution_system_channel
                )
            )

            daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
                self.daily_avg_outside_temperature_input_channel
            )

            storage_temperature_modifier = stsv.get_input_value(
                self.simple_hot_water_storage_temperature_modifier_channel
            )

            # turning heat pump off when the average daily outside temperature is above a certain threshold (if threshold is set in the config)
            summer_heating_mode = self.summer_heating_condition(
                daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                set_heating_threshold_temperature_in_celsius=self.heatpump_controller_config.set_heating_threshold_outside_temperature_in_celsius,
            )

            # mode 1 is on/off controller
            if self.mode == 1:
                self.conditions_on_off(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                    summer_heating_mode=summer_heating_mode,
                    storage_temperature_modifier=storage_temperature_modifier,
                    temperature_offset_for_state_conditions_in_celsius=self.temperature_offset_for_state_conditions_in_celsius,
                )

            # mode 2 is regulated controller (meaning heating, cooling, off). this is only possible if heating system is floor heating
            elif (
                self.mode == 2
                and self.heat_distribution_system_type
                == HeatDistributionSystemType.FLOORHEATING
            ):
                # turning heat pump cooling mode off when the average daily outside temperature is below a certain threshold
                summer_cooling_mode = self.summer_cooling_condition(
                    daily_average_outside_temperature_in_celsius=daily_avg_outside_temperature_in_celsius,
                    set_cooling_threshold_temperature_in_celsius=self.heatpump_controller_config.set_cooling_threshold_outside_temperature_in_celsius,
                )
                self.conditions_heating_cooling_off(
                    water_temperature_input_in_celsius=water_temperature_input_from_heat_water_storage_in_celsius,
                    set_heating_flow_temperature_in_celsius=heating_flow_temperature_from_heat_distribution_system,
                    summer_heating_mode=summer_heating_mode,
                    summer_cooling_mode=summer_cooling_mode,
                    storage_temperature_modifier=storage_temperature_modifier,
                    temperature_offset_for_state_conditions_in_celsius=self.temperature_offset_for_state_conditions_in_celsius,
                )

            else:
                raise ValueError(
                    "Either the Advanced HP Lib Controller Mode is neither 1 nor 2,"
                    "or the heating system is not floor heating which is the condition for cooling (mode 2)."
                )

            if self.controller_heatpumpmode == "heating":
                state = 1
            elif self.controller_heatpumpmode == "cooling":
                state = -1
            elif self.controller_heatpumpmode == "off":
                state = 0
            else:
                raise ValueError("Advanced HP Lib Controller State unknown.")

            stsv.set_output_value(self.state_channel, state)

    def conditions_on_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
        storage_temperature_modifier: float,
        temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode."""

        if self.controller_heatpumpmode == "heating":
            if (
                water_temperature_input_in_celsius
                > (
                    set_heating_flow_temperature_in_celsius
                    # + 0.5
                    + temperature_offset_for_state_conditions_in_celsius
                    + storage_temperature_modifier
                )
                or summer_heating_mode == "off"
            ):  # + 1:
                self.controller_heatpumpmode = "off"
                return

        elif self.controller_heatpumpmode == "off":

            # heat pump is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer mode on)
            if (
                water_temperature_input_in_celsius
                < (
                    set_heating_flow_temperature_in_celsius
                    # - 1.0
                    - temperature_offset_for_state_conditions_in_celsius
                    + storage_temperature_modifier
                )
                and summer_heating_mode == "on"
            ):  # - 1:
                self.controller_heatpumpmode = "heating"
                return

        else:
            raise ValueError("unknown mode")

    def conditions_heating_cooling_off(
        self,
        water_temperature_input_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        summer_heating_mode: str,
        summer_cooling_mode: str,
        storage_temperature_modifier: float,
        temperature_offset_for_state_conditions_in_celsius: float,
    ) -> None:
        """Set conditions for the heat pump controller mode according to the flow temperature."""
        # Todo: storage temperature modifier is only working for heating so far. Implement for cooling similar
        heating_set_temperature = set_heating_flow_temperature_in_celsius
        cooling_set_temperature = set_heating_flow_temperature_in_celsius

        if self.controller_heatpumpmode == "heating":
            if (
                water_temperature_input_in_celsius
                >= heating_set_temperature
                + storage_temperature_modifier  # Todo: Check if storage_temperature_modifier is neccessary here
                or summer_heating_mode == "off"
            ):
                self.controller_heatpumpmode = "off"
                return
        elif self.controller_heatpumpmode == "cooling":
            if (
                water_temperature_input_in_celsius <= cooling_set_temperature
                or summer_cooling_mode == "off"
            ):
                self.controller_heatpumpmode = "off"
                return

        elif self.controller_heatpumpmode == "off":

            # heat pump is only turned on if the water temperature is below the flow temperature
            # and if the avg daily outside temperature is cold enough (summer heating mode on)
            if (
                water_temperature_input_in_celsius
                < (
                    heating_set_temperature
                    - temperature_offset_for_state_conditions_in_celsius
                    + storage_temperature_modifier
                )
                and summer_heating_mode == "on"
            ):
                self.controller_heatpumpmode = "heating"
                return

            # heat pump is only turned on for cooling if the water temperature is above a certain flow temperature
            # and if the avg daily outside temperature is warm enough (summer cooling mode on)
            if (
                water_temperature_input_in_celsius
                > (
                    cooling_set_temperature
                    + temperature_offset_for_state_conditions_in_celsius
                )
                and summer_cooling_mode == "on"
            ):
                self.controller_heatpumpmode = "cooling"
                return

        else:
            raise ValueError("unknown mode")

    def summer_heating_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_heating_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the heat pump."""

        # if no heating threshold is set, the heat pump is always on
        if set_heating_threshold_temperature_in_celsius is None:
            heating_mode = "on"

        # it is too hot for heating
        elif (
            daily_average_outside_temperature_in_celsius
            > set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "off"

        # it is cold enough for heating
        elif (
            daily_average_outside_temperature_in_celsius
            < set_heating_threshold_temperature_in_celsius
        ):
            heating_mode = "on"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or heating threshold temperature {set_heating_threshold_temperature_in_celsius}°C is not acceptable."
            )
        return heating_mode

    def summer_cooling_condition(
        self,
        daily_average_outside_temperature_in_celsius: float,
        set_cooling_threshold_temperature_in_celsius: Optional[float],
    ) -> str:
        """Set conditions for the heat pump."""

        # if no cooling threshold is set, cooling is always possible no matter what daily outside temperature
        if set_cooling_threshold_temperature_in_celsius is None:
            cooling_mode = "on"

        # it is hot enough for cooling
        elif (
            daily_average_outside_temperature_in_celsius
            > set_cooling_threshold_temperature_in_celsius
        ):
            cooling_mode = "on"

        # it is too cold for cooling
        elif (
            daily_average_outside_temperature_in_celsius
            < set_cooling_threshold_temperature_in_celsius
        ):
            cooling_mode = "off"

        else:
            raise ValueError(
                f"daily average temperature {daily_average_outside_temperature_in_celsius}°C"
                f"or cooling threshold temperature {set_cooling_threshold_temperature_in_celsius}°C is not acceptable."
            )

        return cooling_mode


# ===========================================================================
# implement a hplib controller l1 for dhw storage (tww)
@dataclass_json
@dataclass
class HeatPumpHplibControllerDHWL1Config(ConfigBase):

    """HeatPump Controller Config Class."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return HeatPumpHplibControllerDHW.get_full_classname()

    name: str
    #: lower set temperature of DHW Storage, given in °C
    t_min_dhw_storage_in_celsius: float
    #: upper set temperature of DHW Storage, given in °C
    t_max_dhw_storage_in_celsius: float
    #: set thermal power delivered for dhw on constant value --> max. Value of heatpump
    thermalPower_is_constant_for_dhw: bool
    #: max. Power of Heatpump for not modulation dhw production
    p_th_max_dhw_in_W: float


    @classmethod
    def get_default_generic_heat_pump_controller_config(cls):
        """Gets a default Generic Heat Pump Controller."""
        return HeatPumpHplibControllerDHWL1Config(
            name="HeatPumpControllerDHW",
            t_min_dhw_storage_in_celsius=40.0,
            t_max_dhw_storage_in_celsius=60.0,
            thermalPower_is_constant_for_dhw = False,      #false: modulation, true: constant power for dhw
            p_th_max_dhw_in_W = 5000,                       # only if true
        )


class HeatPumpHplibControllerDHW(Component):

    """Heat Pump Controller for DHW.

    It takes data from DHW Storage --> generic hot water storage modular
    sends signal to the heat pump for activation or deactivation.

    """


    # in state mit aufnehmen das hier priorisiert wird

    # Inputs
    WaterTemperatureInputFromDHWStorage ="WaterTemperatureInputFromDHWStorage"
    DWHStorageTemperatureModifier = "StorageTemperatureModifier"

    # Outputs
    State_dhw = "State DHW"
    ThermalPower_Bedingung_for_konst_dhw = "BedingungfuerKonstDHW"          #if heatpump has fix power for dhw
    Value_Max_Thermal_Power_for_dhw = "ThermalPowerHPForDHWConst"           #if heatpump has fix power for dhw




    def __init__(
            self,
            my_simulation_parameters: SimulationParameters,
            config: HeatPumpHplibControllerDHWL1Config,
    ) -> None:
        """Construct all the neccessary attributes."""
        self.heatpump_controller_dhw_config = config
        super().__init__(
            self.heatpump_controller_dhw_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.config: HeatPumpHplibControllerDHWL1Config = config

        self.build()

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.WaterTemperatureInputFromDHWStorage,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        self.storage_temperature_modifier_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DWHStorageTemperatureModifier,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            mandatory=False,
        )

        self.state_dhw_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.State_dhw,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.State_dhw} will follow.",
        )

        self.thermalPower_const_bedingung_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPower_Bedingung_for_konst_dhw,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.ThermalPower_Bedingung_for_konst_dhw} will follow.",
        )

        self.thermalPower_max_value_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.Value_Max_Thermal_Power_for_dhw,
            LoadTypes.ANY,
            Units.ANY,
            output_description=f"here a description for {self.Value_Max_Thermal_Power_for_dhw} will follow.",
        )

        self.controller_heatpumpmode_dhw: Any
        self.previous_heatpump_mode_dhw: Any
        self.controller_signal: int
        self.previous_controller_signal: int
        self.thermalPower_constant_for_dhw: bool
        self.p_th_max_dhw: float


        self.add_default_connections(self.get_default_connections_from_dhw_storage())

    def get_default_connections_from_dhw_storage(self, ):
        """Get simple hot water storage default connections."""
        log.information("setting dhw storage default connections")
        connections = []
        dhw_classname = generic_hot_water_storage_modular.HotWaterStorage.get_classname()
        connections.append(
            ComponentConnection(
                HeatPumpHplibControllerDHW.WaterTemperatureInputFromDHWStorage,
                dhw_classname,
                generic_hot_water_storage_modular.HotWaterStorage.TemperatureMean,
            )
        )
        return connections

    def build(self, ) -> None:
        """Build function.

        The function sets important constants and parameters for the calculations.
        """
        # Sth
        self.controller_heatpumpmode_dhw = "off_dhw_heating"
        self.previous_heatpump_mode_dhw = self.controller_heatpumpmode_dhw
        self.controller_signal = 0
        self.previous_controller_signal = self.controller_signal
        self.thermalPower_constant_for_dhw = self.config.thermalPower_is_constant_for_dhw
        self.p_th_max_dhw = self.config.p_th_max_dhw_in_W

        if self.thermalPower_constant_for_dhw == True:
            print("INFO: DHW Power ist constant with " + str(self.p_th_max_dhw) + "W")
        elif self.thermalPower_constant_for_dhw == False:
            print("INFO: DHW Power is modulating")
            self.p_th_max_dhw=0


    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_heatpump_mode_dhw = self.controller_heatpumpmode_dhw
        self.previous_controller_signal = self.controller_signal

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.controller_heatpumpmode_dhw = self.previous_heatpump_mode_dhw
        self.controller_signal = self.previous_controller_signal

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublecheck."""
        pass

    def write_to_report(self) -> List[str]:
        """Write important variables to report."""
        return self.heatpump_controller_dhw_config.get_string_dict()

    def i_simulate(
            self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate the heat pump comtroller."""

        if force_convergence:
            pass
        else:

            water_temperature_input_from_dhw_storage_in_celsius = stsv.get_input_value(self.water_temperature_input_channel)
            temperature_modifier = stsv.get_input_value(self.storage_temperature_modifier_channel)

            t_min_dhw_storage_in_celsius = self.config.t_min_dhw_storage_in_celsius
            t_max_dhw_storage_in_celsius = self.config.t_max_dhw_storage_in_celsius


            if water_temperature_input_from_dhw_storage_in_celsius < t_min_dhw_storage_in_celsius:            #an
                #self.controller_heatpumpmode_dhw = "heating_dhw"
                self.controller_signal = 1
            elif water_temperature_input_from_dhw_storage_in_celsius > t_max_dhw_storage_in_celsius + temperature_modifier:      #aus
              #  self.controller_heatpumpmode_dhw = "off_dhw_heating"
                self.controller_signal = 0
            elif temperature_modifier > 0 and water_temperature_input_from_dhw_storage_in_celsius < t_max_dhw_storage_in_celsius:   #aktiviren wenn strom überschuss
                self.controller_signal = 1
            else:
                # self.controller_signal=630
                pass


            if self.controller_signal == 1:
                state_dhw = 2
               # print("heating dhw on")
            elif self.controller_signal == 0:
                state_dhw = 0
               # print("heating dhw off")
            else:
                raise ValueError("Advanced HP Lib DHW Controller State unknown.")


    #        print("state controller neu " + str(state_dhw))
            stsv.set_output_value(self.state_dhw_channel, state_dhw)
            stsv.set_output_value(self.thermalPower_const_bedingung_channel, self.thermalPower_constant_for_dhw)
            if self.thermalPower_constant_for_dhw == True:
                stsv.set_output_value(self.thermalPower_max_value_channel, self.p_th_max_dhw)
            elif self.thermalPower_constant_for_dhw == False:
                stsv.set_output_value(self.thermalPower_max_value_channel, 0)


