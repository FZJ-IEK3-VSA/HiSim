# -*- coding: utf-8 -*-
""" Controller of EV battery with configuration and state. """
# clean

from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json

import pandas as pd

from utspclient.helpers.lpgpythonbindings import JsonReference
from utspclient.helpers.lpgdata import ChargingStationSets

from hisim.simulationparameters import SimulationParameters
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_car
from hisim.components import advanced_ev_battery_bslib
from hisim.component import OpexCostDataClass, CapexCostDataClass
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry

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
class ChargingStationConfig(cp.ConfigBase):
    """Definition of the configuration of Charging Station and the set point for the control."""

    building_name: str
    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: definition of the charging station, in line with definitions from LoadProfileGenerator
    charging_station_set: JsonReference
    #: set point for state of charge of battery
    battery_set: float
    #: lower threshold for charging power (below efficiency goes down)
    lower_threshold_charging_power: float
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: float
    #: cost for investment in Euro
    investment_costs_in_euro: float
    #: lifetime of charging station in years
    lifetime_in_years: float
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return L1Controller.get_full_classname()

    @staticmethod
    def get_default_config(
        charging_station_set: JsonReference = ChargingStationSets.Charging_At_Home_with_03_7_kW,
        building_name: str = "BUI1",
    ) -> "ChargingStationConfig":
        """Returns default configuration of charging station and desired SOC Level."""
        charging_power = float((charging_station_set.Name or "").split("with ")[1].split(" kW")[0])
        lower_threshold_charging_power = (
            charging_power * 1e3 * 0.1
        )  # 10 % of charging power for acceptable efficiencies
        config = ChargingStationConfig(
            building_name=building_name,
            name="L1EVChargeControl",
            source_weight=1,
            charging_station_set=charging_station_set,
            battery_set=0.8,
            lower_threshold_charging_power=lower_threshold_charging_power,
            device_co2_footprint_in_kg=100,  # estimated value  # Todo: check value
            investment_costs_in_euro=1000,  # Todo: check value
            lifetime_in_years=10,  # estimated value  # Todo: check value
            maintenance_costs_in_euro_per_year=0.05 * 1000,  # SOURCE: https://photovoltaik.one/wallbox-kosten (estimated value)
        )
        return config


class L1ControllerState:
    """Data class, which saves the state of the controller."""

    def __init__(self, power: float) -> None:
        """Initializes power for battery charge/discharge in state."""
        self.power = power

    def clone(self) -> "L1ControllerState":
        """Copy state efficiently."""
        return L1ControllerState(power=self.power)


class L1Controller(cp.Component):
    """Simulates EV charging and battery losses due to driving. Control according to constant SOC threshold and optional surplus control.

    Components to connect to:
    (1) Car (generic_car)
    (2) Car Battery (advanced_ev_battery_bslib)
    (3) EMS (controller_l2_energy_management_system) - optional
    """

    # Inputs
    ElectricityOutput = "ElectricityOutput"
    CarLocation = "CarLocation"
    StateOfCharge = "StateOfCharge"
    ElectricityTarget = "ElectricityTarget"
    AcBatteryPower = "AcBatteryPower"

    # Outputs
    ToOrFromBattery = "ToOrFromBattery"
    BatteryChargingPowerToEMS = "BatteryChargingPowerToEMS"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ChargingStationConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
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
        self.state = L1ControllerState(power=0)
        self.previous_state = self.state.clone()
        self.processed_state = self.state.clone()
        self.build(config=config)

        # add inputs
        self.car_consumption_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )
        self.car_location_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CarLocation,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            mandatory=True,
        )
        self.state_of_charge_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            mandatory=True,
        )

        self.ac_battery_power_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.AcBatteryPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )

        self.electricity_target_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityTarget,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=False,
        )

        # add outputs
        self.p_set_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ToOrFromBattery,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description="Set power for EV charging (and discharging) in Watt.",
        )

        self.battery_charging_power_to_ems_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BatteryChargingPowerToEMS,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description="Real Power for EV charging in Watt. Signal send to L2EMSElectricityController",
        )

        self.add_default_connections(self.get_default_connections_from_generic_car())
        self.add_default_connections(self.get_default_connections_advanced_battery())

    def get_default_connections_from_generic_car(self) -> List[cp.ComponentConnection]:
        """Default connections of car in ev charge controller."""

        connections: List[cp.ComponentConnection] = []
        car_classname = generic_car.Car.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1Controller.ElectricityOutput,
                car_classname,
                generic_car.Car.ElectricityOutput,
            )
        )
        connections.append(cp.ComponentConnection(L1Controller.CarLocation, car_classname, generic_car.Car.CarLocation))
        return connections

    def get_default_connections_advanced_battery(self) -> List[cp.ComponentConnection]:
        """Default connections of car battery in ev charge controller."""

        connections: List[cp.ComponentConnection] = []
        battery_classname = advanced_ev_battery_bslib.CarBattery.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1Controller.StateOfCharge,
                battery_classname,
                advanced_ev_battery_bslib.CarBattery.StateOfCharge,
            )
        )
        connections.append(
            cp.ComponentConnection(
                L1Controller.AcBatteryPower,
                battery_classname,
                advanced_ev_battery_bslib.CarBattery.AcBatteryPower,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves actual state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Checks statements."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def control(
        self,
        car_consumption: float,
        car_location: int,
        soc: float,
        electricity_target: float,
    ) -> float:
        """Control."""

        if car_consumption > 0:
            return car_consumption * (-1)
        if car_location != self.charging_location:
            return 0
        if soc < self.config.battery_set:
            return self.power
        if electricity_target > self.config.lower_threshold_charging_power:
            return min(electricity_target, self.power)
        return 0

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Returns battery charge and discharge (energy consumption of car) of battery at each timestep."""
        if force_convergence:
            self.state = self.processed_state.clone()
        else:
            car_location = int(stsv.get_input_value(self.car_location_channel))
            car_consumption = stsv.get_input_value(self.car_consumption_channel)
            soc = stsv.get_input_value(self.state_of_charge_channel)
            if self.electricity_target_channel.source_output is not None:
                electricity_target = stsv.get_input_value(self.electricity_target_channel)
            else:
                electricity_target = 0
            self.state.power = self.control(
                car_consumption,
                car_location=car_location,
                soc=soc,
                electricity_target=electricity_target,
            )
            self.processed_state = self.state.clone()
        stsv.set_output_value(self.p_set_channel, self.state.power)

        ac_battery_power = stsv.get_input_value(self.ac_battery_power_channel)
        if ac_battery_power > 0:
            # charging of EV
            stsv.set_output_value(self.battery_charging_power_to_ems_channel, ac_battery_power)
        else:
            # no charging of EV
            stsv.set_output_value(self.battery_charging_power_to_ems_channel, 0)

    def build(
        self,
        config: ChargingStationConfig,
    ) -> None:
        """Translates and assigns config parameters to controller class and completes initialization."""
        self.name = config.name
        self.source_weight = config.source_weight
        self.config = config
        # get charging station location and charging station power out of ChargingStationSet
        if config.charging_station_set.Name is not None:
            charging_station_string = config.charging_station_set.Name.partition("At ")[2]
            location = charging_station_string.partition(" with")[0]
            if location == "Home":
                self.charging_location = 1
            elif location == "Work":
                self.charging_location = 2
        else:
            log.error(
                'Charging location not known, check the input on the charging station set. It was set to "charging at home per default.'
            )
        power = float(charging_station_string.partition("with ")[2].partition(" kW")[0]) * 1e3
        self.power = power

    def write_to_report(self) -> List[str]:
        """Writes EV charge controller values to report."""
        lines = []
        lines.append(self.name + "_w" + str(self.source_weight) + "charging controller: ")
        lines.append(f"Power [kW]: {self.power * 1e-3:2.1f}")
        if self.charging_location == 1:
            lines.append("At Home")
        elif self.charging_location == 2:
            lines.append("At Work")
        return lines

    @staticmethod
    def get_cost_capex(config: ChargingStationConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
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
            kpi_tag=KpiTagEnumClass.CAR_BATTERY
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs snd write total energy consumption to component-config.

        No electricity costs for components except for Electricity Meter,
        because part of electricity consumption is feed by PV

        elecricity_consumption is calculated for generic_car already
        """
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.CAR_BATTERY
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
