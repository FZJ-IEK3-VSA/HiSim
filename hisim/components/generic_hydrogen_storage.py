# -*- coding: utf-8 -*-
# Owned
from dataclasses import dataclass
from typing import List, Any, Tuple

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import generic_CHP
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
class GenericHydrogenStorageConfig:
    name: str
    source_weight: int
    min_capacity: float  # [kg_H2]
    max_capacity: float  # [kg_H2]
    max_charging_rate_hour: float  # [kg/h]
    max_discharging_rate_hour: float  # [kg/h]
    energy_for_charge: float  # [kWh/kg]
    energy_for_discharge: float  # [kWh/kg]
    loss_factor_per_day: float  # [lost_%/day]

    # todo: discuss with Johanna
    def init(
        self,
        name: str,
        source_weight: int,
        min_capacity: float,
        max_capacity: float,
        max_charging_rate_hour: float,
        max_discharging_rate_hour: float,
        energy_for_charge: float,
        energy_for_discharge: float,
        loss_factor_per_day: float,
    ) -> None:
        self.name = name
        self.source_weight = source_weight
        self.min_capacity = min_capacity
        self.max_capacity = max_capacity
        self.max_charging_rate_hour = max_charging_rate_hour
        self.max_discharging_rate_hour = max_discharging_rate_hour
        self.energy_for_charge = energy_for_charge
        self.energy_for_discharge = energy_for_discharge
        self.loss_factor_per_day = loss_factor_per_day

    @staticmethod
    def get_default_config(
        capacity: float = 200,
        max_charging_rate: float = 2,
        max_discharging_rate: float = 2,
        source_weight: int = 1,
    ) -> Any:
        config = GenericHydrogenStorageConfig(
            name="HydrogenStorage",
            source_weight=source_weight,
            min_capacity=0,
            max_capacity=capacity,
            max_charging_rate_hour=max_charging_rate,
            max_discharging_rate_hour=max_discharging_rate,
            energy_for_charge=0,
            energy_for_discharge=0,
            loss_factor_per_day=0,
        )
        return config


class GenericHydrogenStorageState:
    """
    This data class saves the state of the electrolyzer.
    """

    def __init__(self, fill: float = 0) -> None:
        self.fill = fill

    def clone(self) -> Any:
        return GenericHydrogenStorageState(fill=self.fill)


class GenericHydrogenStorage(cp.Component):
    # input
    HydrogenInput = "HydrogenInput"  # kg/s
    HydrogenOutput = "HydrogenOutput"  # kg/s

    # output
    HydrogenSOC = "HydrogenSOC"  # kg/s

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericHydrogenStorageConfig,
    ) -> None:
        super().__init__(
            name=config.name + "_w" + str(config.source_weight),
            my_simulation_parameters=my_simulation_parameters,
        )

        self.build(config)

        self.HydrogenInputC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenInput,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            True,
        )
        self.HydrogenOutputC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.HydrogenOutput,
            lt.LoadTypes.HYDROGEN,
            lt.Units.KG_PER_SEC,
            True,
        )

        self.HydrogenSOCC: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.HydrogenSOC,
            load_type=lt.LoadTypes.HYDROGEN,
            unit=lt.Units.PERCENT,
            postprocessing_flag=[lt.InandOutputType.STORAGE_CONTENT],
            output_description="Hydrogen SOC"
        )

        self.add_default_connections(
            self.get_default_connections_from_generic_electrolyzer()
        )
        self.add_default_connections(self.get_default_connections_from_generic_CHP())
        self.state: Any
        self.previous_state: Any

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def get_default_connections_from_generic_CHP(self) -> List[cp.ComponentConnection]:
        log.information("setting fuel cell default connections in generic H2 storage")
        connections: List[cp.ComponentConnection] = []
        chp_classname = generic_CHP.GCHP.get_classname()
        connections.append(
            cp.ComponentConnection(
                GenericHydrogenStorage.HydrogenOutput,
                chp_classname,
                generic_CHP.GCHP.FuelDelivered,
            )
        )
        return connections

    def get_default_connections_from_generic_electrolyzer(
        self,
    ) -> List[cp.ComponentConnection]:
        log.information(
            "setting electrolyzer default connections in generic H2 storage"
        )
        connections: List[cp.ComponentConnection] = []
        electrolyzer_classname = (
            generic_electrolyzer.GenericElectrolyzer.get_classname()
        )
        connections.append(
            cp.ComponentConnection(
                GenericHydrogenStorage.HydrogenInput,
                electrolyzer_classname,
                generic_electrolyzer.GenericElectrolyzer.HydrogenOutput,
            )
        )
        return connections

    def store(self, charging_rate: float) -> Tuple[float, float, float]:

        # limitation of charging rate
        delta_not_stored: float = 0
        if charging_rate > self.max_charging_rate:
            delta_not_stored = delta_not_stored + charging_rate - self.max_charging_rate
            charging_rate = self.max_charging_rate

        # limitation of storage size
        if (
            self.state.fill
            + charging_rate * self.my_simulation_parameters.seconds_per_timestep
            < self.max_capacity
        ):
            # fits completely
            self.state.fill = (
                self.state.fill
                + charging_rate * self.my_simulation_parameters.seconds_per_timestep
            )

        elif self.state.fill >= self.max_capacity:
            # tank is already full
            delta_not_stored += charging_rate
            charging_rate = 0

        else:
            # fits partially
            # returns amount which an be put in
            amount_stored = self.max_capacity - self.state.fill
            self.state.fill += amount_stored
            delta_not_stored = (
                charging_rate
                - amount_stored / self.my_simulation_parameters.seconds_per_timestep
            )
            charging_rate = (
                amount_stored / self.my_simulation_parameters.seconds_per_timestep
            )

        power_demand = charging_rate * self.energy_for_charge * 3.6e3
        return charging_rate, power_demand, delta_not_stored

    def withdraw(self, discharging_rate: float) -> Tuple[float, float, float]:

        # limitations of discharging rate
        delta_not_released: float = 0
        if discharging_rate > self.max_discharging_rate:
            delta_not_released = (
                delta_not_released + discharging_rate - self.max_discharging_rate
            )
            discharging_rate = self.max_discharging_rate

        if (
            self.state.fill
            > self.min_capacity
            + discharging_rate * self.my_simulation_parameters.seconds_per_timestep
        ):
            # has enough
            self.state.fill -= (
                discharging_rate * self.my_simulation_parameters.seconds_per_timestep
            )

        elif self.state.fill <= self.min_capacity:
            # empty
            delta_not_released = delta_not_released + discharging_rate
            discharging_rate = 0

        else:
            # can provide hydrogen partially,
            # added recently :but in this case to simplify work of CHP, say that no hydrogen can be provided
            amount_released = self.state.fill - self.min_capacity
            self.state.fill -= amount_released
            delta_not_released = (
                discharging_rate
                - amount_released / self.my_simulation_parameters.seconds_per_timestep
            )
            discharging_rate = (
                amount_released / self.my_simulation_parameters.seconds_per_timestep
            )

        power_demand = discharging_rate * self.energy_for_discharge * 3.6e3
        return discharging_rate, power_demand, delta_not_released

    def storage_losses(self) -> None:
        self.state.fill -= self.state.fill * self.loss_factor

    def build(self, config: GenericHydrogenStorageConfig) -> None:
        self.name = config.name
        self.source_weight = config.source_weight
        self.min_capacity = config.min_capacity
        self.max_capacity = config.max_capacity
        self.max_charging_rate = config.max_charging_rate_hour / 3.6e3
        self.max_discharging_rate = config.max_discharging_rate_hour / 3.6e3
        self.loss_factor = (
            (config.loss_factor_per_day / 100)
            * self.my_simulation_parameters.seconds_per_timestep
            / (24 * 3600)
        )
        self.energy_for_charge = config.energy_for_charge
        self.energy_for_discharge = config.energy_for_discharge

        self.state = GenericHydrogenStorageState()
        self.previous_state = GenericHydrogenStorageState()

    def i_save_state(self) -> None:
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        self.state = self.previous_state.clone()

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:

        # get input values
        charging_rate = stsv.get_input_value(self.HydrogenInputC)
        discharging_rate = stsv.get_input_value(self.HydrogenOutputC)

        if charging_rate < 0:
            raise Exception(
                "trying to charge with negative amount" + str(charging_rate)
            )
        if discharging_rate < 0:
            raise Exception(
                "trying to discharge with negative amount: " + str(discharging_rate)
            )

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
        percent_fill = 100 * self.state.fill / self.max_capacity

        stsv.set_output_value(self.HydrogenSOCC, percent_fill)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.name + str(self.source_weight)))
        lines.append("capacity: {:4.0f} kg hydrogen".format(self.max_capacity))
        return lines
