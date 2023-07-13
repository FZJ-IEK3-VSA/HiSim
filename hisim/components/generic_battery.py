# Generic/Built-in
import copy
from dataclasses import dataclass
from dataclasses_json import dataclass_json

# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils
from hisim.components.generic_ev_charger import SimpleStorageState
from hisim.simulationparameters import SimulationParameters

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class GenericBatteryState:
    def __init__(
        self,
        init_stored_energy=0,
        max_stored_energy=None,
        min_stored_energy=None,
        max_var_stored_energy=None,
        min_var_stored_energy=None,
    ):
        self.stored_energy = init_stored_energy
        self.max_stored_energy = max_stored_energy
        self.min_stored_energy = min_stored_energy
        self.max_var_stored_energy = max_var_stored_energy
        self.min_var_stored_energy = min_var_stored_energy
        self.chargeWh: float

    def charge(self, energy):
        energy = abs(energy)
        if self.stored_energy + energy < self.max_stored_energy:
            charge = energy
        else:
            charge = self.max_stored_energy - self.stored_energy

        if charge > self.max_var_stored_energy:
            charge = self.max_var_stored_energy

        self.stored_energy = charge + self.stored_energy
        self.chargeWh = charge

    def discharge(self, energy):
        energy = -abs(energy)
        if self.stored_energy + energy > self.min_stored_energy:
            discharge = energy
        else:
            discharge = self.stored_energy - self.min_stored_energy

        if discharge < self.min_var_stored_energy:
            discharge = self.min_var_stored_energy

        self.stored_energy = discharge + self.stored_energy
        self.chargeWh = discharge


@dataclass_json
@dataclass
class GenericBatteryConfig(cp.ConfigBase):

    """Configuration of the Generic Battery."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return GenericBattery.get_full_classname()

    name: str
    manufacturer: str
    model: str
    soc: float
    base: bool

    @classmethod
    def get_default_config(cls):
        """Gets a default config."""
        return GenericBatteryConfig(
            name="Generic Battery",
            manufacturer="sonnen",
            model="sonnenBatterie 10 - 11,5 kWh",
            soc=10 / 15,
            base=False,
        )


@dataclass_json
@dataclass
class BatteryControllerConfig(cp.ConfigBase):

    """Configuration of the Generic Battery Controller."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return BatteryController.get_full_classname()

    name: str

    @classmethod
    def get_default_config(cls):
        """Gets a default config."""
        return BatteryControllerConfig(
            name="Battery Controller",
        )


class GenericBattery(cp.Component):
    # Imports
    ElectricityInput = "ElectricityInput"
    State = "State"

    # Outputs
    StoredEnergy = "StoredEnergy"
    StateOfCharge = "StateOfCharge"
    ElectricityOutput = "ElectricityOutput"
    
    # simulation repository
    MaximumBatteryCapacity="MaximumBatteryCapacity"
    MinimumBatteryCapacity="MinimumBatteryCapacity"
    MaximalChargingPower="MaximalChargingPower"
    MaximalDischargingPower="MaximalDischargingPower"
    BatteryEfficiency="BatteryEfficiency"
    InverterEfficiency="InverterEfficiency"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: GenericBatteryConfig,
    ) -> None:
        super().__init__("Battery", my_simulation_parameters, my_config=config)

        self.build(
            manufacturer=config.manufacturer, model=config.model, base=config.base
        )

        self.state = SimpleStorageState(
            max_var_val=self.max_var_stored_energy,
            min_var_val=self.min_var_stored_energy,
            stored_energy=self.max_stored_energy * config.soc,
        )
        self.previous_state = copy.deepcopy(self.state)

        self.inputC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )
        self.stateC: cp.ComponentInput = self.add_input(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY, True
        )

        self.state_of_chargeC: cp.ComponentOutput = self.add_output(
            self.component_name, self.StateOfCharge, lt.LoadTypes.ANY, lt.Units.ANY
        )

        self.stored_energyC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.StoredEnergy,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT_HOUR,
        )

        self.electricity_outputC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricityOutput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
        )

    def build(self, manufacturer, model, base):
        self.base = base
        self.time_correction_factor = (
            1 / self.my_simulation_parameters.seconds_per_timestep
        )
        self.seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep

        # Gets flexibilities, including heat pump
        battery_database = utils.load_smart_appliance("Battery")

        battery_found = False
        battery = None
        for battery in battery_database:
            if battery["Manufacturer"] == manufacturer and battery["Model"] == model:
                battery_found = True
                break

        if battery is None or not battery_found:
            raise Exception("Heat pump model not registered in the database")

        self.max_stored_energy = battery["Capacity"] * 1e3
        self.min_stored_energy = self.max_stored_energy * 0.0
        self.efficiency = battery["Efficiency"]
        self.efficiency_inverter = battery["Inverter Efficiency"]
        self.max_var_stored_energy = (
            battery["Maximal Charging Power"] * 1e3 * self.time_correction_factor
        )
        if "Maximal Discharging Power" in battery:
            self.min_var_stored_energy = (
                -battery["Maximal Discharging Power"]
                * 1e3
                * self.time_correction_factor
            )
        else:
            self.min_var_stored_energy = -self.max_var_stored_energy

    def write_to_report(self):
        lines = []
        lines.append("MaxStoredEnergy: {}".format(self.max_stored_energy))
        return lines

    # def i_save_state(self):
    #    self.previous_state = copy.copy(self.state)

    # def i_restore_state(self):
    #    self.state = copy.copy(self.previous_state)
    def i_save_state(self) -> None:
        self.previous_state = copy.deepcopy(self.state)

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        if self.my_simulation_parameters.system_config.predictive:
            # send battery specification to the mpc controller for planning the cost optimal operation 
            self.simulation_repository.set_entry(self.MaximumBatteryCapacity, self.max_stored_energy)
            self.simulation_repository.set_entry(self.MinimumBatteryCapacity, self.min_stored_energy)
            self.simulation_repository.set_entry(self.MaximalChargingPower, self.max_var_stored_energy/self.time_correction_factor)
            self.simulation_repository.set_entry(self.MaximalDischargingPower, -self.min_var_stored_energy/self.time_correction_factor)
            self.simulation_repository.set_entry(self.BatteryEfficiency, self.efficiency)
            self.simulation_repository.set_entry(self.InverterEfficiency, self.efficiency_inverter)

    def i_restore_state(self) -> None:
        self.state = copy.deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        load = stsv.get_input_value(self.inputC)
        state = stsv.get_input_value(self.stateC)

        load = -load / self.seconds_per_timestep

        capacity = self.state.stored_energy
        max_capacity = self.max_stored_energy
        min_capacity = self.min_stored_energy

        if state == 1:
            charging_delta, after_capacity = self.state.store(
                max_capacity=max_capacity,
                current_capacity=capacity,
                val=load,
                efficiency=self.efficiency_inverter,
            )
        elif state == -1:
            charging_delta, after_capacity = self.state.withdraw(
                min_capacity=min_capacity,
                current_capacity=capacity,
                val=load,
                efficiency=self.efficiency_inverter * self.efficiency,
            )
        else:
            charging_delta = 0
            after_capacity = capacity

        stsv.set_output_value(self.state_of_chargeC, after_capacity / max_capacity)
        stsv.set_output_value(self.stored_energyC, after_capacity)
        stsv.set_output_value(self.electricity_outputC, charging_delta)


class BatteryController(cp.Component):
    ElectricityInput = "ElectricityInput"
    State = "State"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: BatteryControllerConfig,
    ) -> None:
        super().__init__(
            name=config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.inputC: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ElectricityInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )
        self.stateC: cp.ComponentOutput = self.add_output(
            self.component_name, self.State, lt.LoadTypes.ANY, lt.Units.ANY
        )

    def i_save_state(self) -> None:
        pass

    def i_restore_state(self) -> None:
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        load = stsv.get_input_value(self.inputC)
        state: float = 0
        if load < 0.0:
            state = 1
        elif load > 0.0:
            state = -1
        else:
            state = 0.0

        stsv.set_output_value(self.stateC, state)
