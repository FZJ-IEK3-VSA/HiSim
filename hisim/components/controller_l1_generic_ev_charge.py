# -*- coding: utf-8 -*-
""" Controller of EV battery with configuration and state. """

from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from utspclient.helpers.lpgpythonbindings import JsonReference
from utspclient.helpers.lpgdata import ChargingStationSets

from hisim.simulationparameters import SimulationParameters
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_car
from hisim.components import advanced_ev_battery_bslib

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

    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: definition of the charging station, in line with definitions from LoadProfileGenerator
    charging_station_set: JsonReference
    #: set point for state of charge of battery
    battery_set: float

    @staticmethod
    def get_default_config(
        charging_station_set: JsonReference = ChargingStationSets.Charging_At_Home_with_03_7_kW,
    ) -> "ChargingStationConfig":
        """Returns default configuration of charging station and desired SOC Level."""
        config = ChargingStationConfig(
            name="L1EVChargeControl",
            source_weight=1,
            charging_station_set=charging_station_set,
            battery_set=0.8,
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

    # Outputs
    ToOrFromBattery = "ToOrFromBattery"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ChargingStationConfig,
    ) -> None:
        """Initializes Car."""
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )
        self.state = L1ControllerState(power=0)
        self.previous_state = self.state.clone()
        self.processed_state = self.state.clone()
        self.build(config=config, my_simulation_parameters=my_simulation_parameters)

        # add inputs
        self.car_consumption: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            mandatory=True,
        )
        self.car_location: cp.ComponentInput = self.add_input(
            self.component_name,
            self.CarLocation,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            mandatory=True,
        )
        self.state_of_charge: cp.ComponentInput = self.add_input(
            self.component_name,
            self.StateOfCharge,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            mandatory=True,
        )

        if self.clever:
            self.electricity_target: cp.ComponentInput = self.add_input(
                self.component_name,
                self.ElectricityTarget,
                lt.LoadTypes.ELECTRICITY,
                lt.Units.WATT,
                mandatory=True,
            )

        # add outputs
        self.p_set: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ToOrFromBattery,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            output_description="Set power for EV charging in Watt.",
        )

        self.add_default_connections(self.get_default_connections_from_generic_car())
        self.add_default_connections(self.get_default_connections_advanced_battery())

    def get_default_connections_from_generic_car(self) -> List[cp.ComponentConnection]:
        """Default connections of car in ev charge controller."""
        log.information("setting car default connections in ev charge controler")
        connections: List[cp.ComponentConnection] = []
        car_classname = generic_car.Car.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1Controller.ElectricityOutput,
                car_classname,
                generic_car.Car.ElectricityOutput,
            )
        )
        connections.append(
            cp.ComponentConnection(
                L1Controller.CarLocation, car_classname, generic_car.Car.CarLocation
            )
        )
        return connections

    def get_default_connections_advanced_battery(self) -> List[cp.ComponentConnection]:
        """Default connections of car battery in ev charge controller."""
        log.information("setting battery default connections in ev charge controler")
        connections: List[cp.ComponentConnection] = []
        battery_classname = advanced_ev_battery_bslib.CarBattery.get_classname()
        connections.append(
            cp.ComponentConnection(
                L1Controller.StateOfCharge,
                battery_classname,
                advanced_ev_battery_bslib.CarBattery.StateOfCharge,
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

    def control(self, car_consumption: float, car_location: int, soc: float, electricity_target: float) -> float:
        if car_consumption > 0:
            return car_consumption * (-1)
        if car_location != self.charging_location:
            return 0
        if soc < self.battery_set:
            return self.power
        if electricity_target > 0:
            return min(electricity_target, self.power)
        return 0

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Returns battery charge and discharge (energy consumption of car) of battery at each timestep."""
        if force_convergence:
            self.state = self.processed_state.clone()
        else:
            car_location = int(stsv.get_input_value(self.car_location))
            car_consumption = stsv.get_input_value(self.car_consumption)
            soc = stsv.get_input_value(self.state_of_charge)
            if self.clever:
                electricity_target = stsv.get_input_value(self.electricity_target)
            else:
                electricity_target = 0
            self.state.power = self.control(car_consumption, car_location=car_location, soc=soc, electricity_target=electricity_target)
            self.processed_state = self.state.clone()
        stsv.set_output_value(self.p_set, self.state.power)

    def build(
        self,
        config: ChargingStationConfig,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        """Translates and assigns config parameters to controller class and completes initialization."""
        self.name = config.name
        self.source_weight = config.source_weight
        # get charging station location and charging station power out of ChargingStationSet
        if config.charging_station_set.Name is not None:
            charging_station_string = config.charging_station_set.Name.partition("At ")[
                2
            ]
            location = charging_station_string.partition(" with")[0]
            if location == "Home":
                self.charging_location = 1
            elif location == "Work":
                self.charging_location = 2
        else:
            log.error(
                'Charging location not known, check the input on the charging station set. It was set to "charging at home per default.'
            )
        power = (
            float(charging_station_string.partition("with ")[2].partition(" kW")[0])
            * 1e3
        )
        self.power = power
        self.battery_set = config.battery_set
        self.clever = my_simulation_parameters.surplus_control

    def write_to_report(self) -> List[str]:
        """Writes EV charge controller values to report."""
        lines = []
        lines.append(
            self.name + "_w" + str(self.source_weight) + "charging controller: "
        )
        lines.append(f"Power [kW]: {self.power * 1e-3:2.1f}")
        if self.charging_location == 1:
            lines.append("At Home")
        elif self.charging_location == 2:
            lines.append("At Work")
        return lines
