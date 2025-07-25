"""Generic hydrogen storage module."""

# clean
# -*- coding: utf-8 -*-
# Owned
from typing import List, Any, Tuple
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import generic_chp
from hisim.components import generic_electrolyzer
from hisim.simulationparameters import SimulationParameters

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = ""
__version__ = ""
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = ""


@dataclass_json
@dataclass
class GenericHydrogenStorageConfig(cp.ConfigBase):
    """Generic hydrogen storage config class."""

    building_name: str
    #: name of the device
    name: str
    #: priority of the device in hierachy: the higher the number the lower the priority
    source_weight: int
    #: minimal fill state of the hydrogen storage in kg of hydrogen
    min_capacity: float
    #: maximal capacity of the hydrogen storage in kg of hydrogen
    max_capacity: float
    #: maximal charge rate of the hydrgoen storage in kg/s
    max_charging_rate: float
    #: maximal discharge rate of the hydrgoen storage in kg/s
    max_discharging_rate: float
    #: energy demand for the charging process in Wh/kg
    energy_for_charge: float
    #: energy demand for the discharging process in Wh/kg
    energy_for_discharge: float
    #: permanent hydrogen loss in % per day
    loss_factor_per_day: float

    @staticmethod
    def get_default_config(
        capacity: float = 200,
        max_charging_rate: float = 2 / 3600,
        max_discharging_rate: float = 2 / 3600,
        source_weight: int = 1,
        building_name: str = "BUI1",
    ) -> Any:
        """Returns default configuration for hydrogen storage."""
        config = GenericHydrogenStorageConfig(
            building_name=building_name,
            name="HydrogenStorage",
            source_weight=source_weight,
            min_capacity=0,
            max_capacity=capacity,
            max_charging_rate=max_charging_rate,
            max_discharging_rate=max_discharging_rate,
            energy_for_charge=0,
            energy_for_discharge=0,
            loss_factor_per_day=0,
        )
        return config


class GenericHydrogenStorageState:
    """Generic hydrogen storage state that saves the state of the hydrogen storage."""

    def __init__(self, fill: float = 0) -> None:
        """Initialize the class."""
        self.fill = fill

    def clone(self) -> Any:
        """Clones the state."""
        return GenericHydrogenStorageState(fill=self.fill)


class GenericHydrogenStorage(cp.Component):
    """Generic hydrogen storage is a simple implementation of a hydrogen storage.

    Components to connect to:
    (1) Fuel cell (generic_CHP)
    (2) Electrolyzer (generic_electrolyzer)
    """

    # input
    HydrogenInput = "HydrogenInput"  # kg/s
    HydrogenOutput = "HydrogenOutput"  # kg/s

    # output
    HydrogenSOC = "HydrogenSOC"  # kg/s

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericHydrogenStorageConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
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
        self.loss_factor = (
            (config.loss_factor_per_day / 100) * self.my_simulation_parameters.seconds_per_timestep / (24 * 3600)
        )
        self.state = GenericHydrogenStorageState()
        self.previous_state = GenericHydrogenStorageState()

        self.hydrogen_input_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenInput,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            True,
        )
        self.hydrogen_output_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenOutput,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            True,
        )

        self.hydrogen_soc: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HydrogenSOC,
            load_type=lt.LoadTypes.GREEN_HYDROGEN,
            unit=lt.Units.PERCENT,
            postprocessing_flag=[lt.InandOutputType.STORAGE_CONTENT],
            output_description="Hydrogen SOC",
        )

        self.add_default_connections(self.get_default_connections_from_generic_electrolyzer())
        self.add_default_connections(self.get_default_connections_from_generic_chp())

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def get_default_connections_from_generic_chp(self) -> List[cp.ComponentConnection]:
        """Get default connections from generic chp."""

        connections: List[cp.ComponentConnection] = []
        chp_classname = generic_chp.SimpleCHP.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericHydrogenStorage.HydrogenOutput,
                chp_classname,
                generic_chp.SimpleCHP.FuelDelivered,
            )
        )
        return connections

    def get_default_connections_from_generic_electrolyzer(
        self,
    ) -> List[cp.ComponentConnection]:
        """Get default connections from generic electrolyzer."""

        connections: List[cp.ComponentConnection] = []
        electrolyzer_classname = generic_electrolyzer.GenericElectrolyzer.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericHydrogenStorage.HydrogenInput,
                electrolyzer_classname,
                generic_electrolyzer.GenericElectrolyzer.HydrogenOutput,
            )
        )
        return connections

    def store(self, charging_rate: float) -> Tuple[float, float, float]:
        """Store."""

        # limitation of charging rate
        delta_not_stored: float = 0
        if charging_rate > self.config.max_charging_rate:
            delta_not_stored = delta_not_stored + charging_rate - self.config.max_charging_rate
            charging_rate = self.config.max_charging_rate

        # limitation of storage size
        if (
            self.state.fill + charging_rate * self.my_simulation_parameters.seconds_per_timestep
            < self.config.max_capacity
        ):
            # fits completely
            self.state.fill = self.state.fill + charging_rate * self.my_simulation_parameters.seconds_per_timestep

        elif self.state.fill >= self.config.max_capacity:
            # tank is already full
            delta_not_stored += charging_rate
            charging_rate = 0

        else:
            # fits partially
            # returns amount which an be put in
            amount_stored = self.config.max_capacity - self.state.fill
            self.state.fill += amount_stored
            delta_not_stored = charging_rate - amount_stored / self.my_simulation_parameters.seconds_per_timestep
            charging_rate = amount_stored / self.my_simulation_parameters.seconds_per_timestep

        power_demand = charging_rate * self.config.energy_for_charge * 3.6e3
        return charging_rate, power_demand, delta_not_stored

    def withdraw(self, discharging_rate: float) -> Tuple[float, float, float]:
        """Withdraw."""

        # limitations of discharging rate
        delta_not_released: float = 0
        if discharging_rate > self.config.max_discharging_rate:
            delta_not_released = delta_not_released + discharging_rate - self.config.max_discharging_rate
            discharging_rate = self.config.max_discharging_rate

        if (
            self.state.fill
            > self.config.min_capacity + discharging_rate * self.my_simulation_parameters.seconds_per_timestep
        ):
            # has enough
            self.state.fill -= discharging_rate * self.my_simulation_parameters.seconds_per_timestep

        elif self.state.fill <= self.config.min_capacity:
            # empty
            delta_not_released = delta_not_released + discharging_rate
            discharging_rate = 0

        else:
            # can provide hydrogen partially,
            # added recently :but in this case to simplify work of CHP, say that no hydrogen can be provided
            amount_released = self.state.fill - self.config.min_capacity
            self.state.fill -= amount_released
            delta_not_released = discharging_rate - amount_released / self.my_simulation_parameters.seconds_per_timestep
            discharging_rate = amount_released / self.my_simulation_parameters.seconds_per_timestep

        power_demand = discharging_rate * self.config.energy_for_discharge * 3.6e3
        return discharging_rate, power_demand, delta_not_released

    def storage_losses(self) -> None:
        """Storage losses."""
        self.state.fill -= self.state.fill * self.loss_factor

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""

        # get input values
        charging_rate = stsv.get_input_value(self.hydrogen_input_channel)
        discharging_rate = stsv.get_input_value(self.hydrogen_output_channel)

        if charging_rate < 0:
            raise Exception("trying to charge with negative amount" + str(charging_rate))
        if discharging_rate < 0:
            raise Exception("trying to discharge with negative amount: " + str(discharging_rate))

        if charging_rate > 0 and discharging_rate > 0:
            # simultaneous charging and discharging has to be prevented
            # hydrogen can be used directly
            delta = charging_rate - discharging_rate
            if delta >= 0:
                charging_rate = delta
                discharging_rate = 0
            else:
                charging_rate = 0
                discharging_rate = -delta

        if charging_rate > 0:
            _, _, _ = self.store(charging_rate)

        if discharging_rate > 0:
            _, _, _ = self.withdraw(discharging_rate)

        self.storage_losses()

        # delta not released and delta not stored is not used so far -> must be taken into account later
        percent_fill = 100 * self.state.fill / self.config.max_capacity

        stsv.set_output_value(self.hydrogen_soc, percent_fill)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

    def write_to_report(self):
        """Writes the information of the current component to the report."""
        return self.config.get_string_dict()
