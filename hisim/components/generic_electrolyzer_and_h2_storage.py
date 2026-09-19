"""Generic electrolyzer and h2 storage module."""

# Owned
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters

from hisim.components.configuration import PhysicsConfig
from hisim import log
from hisim.economics.facts import CostRelevance


@dataclass_json
@dataclass
class ElectrolyzerWithStorageConfig(ConfigBase):
    """Power band and hydrogen output of the electrolyzer that feeds a hydrogen storage.

    The machine runs between ``min_power`` and ``max_power``, produces between the two
    hydrogen rates in proportion to where in that band it sits, and loses ``waste_energy`` as
    heat nothing in the household uses. The one machine this library states is
    :meth:`preset_standard`::

        ElectrolyzerWithStorageConfig.preset_standard("ElectrolyzerWithStorage")
    """

    MAIN_CLASS = "hisim.components.generic_electrolyzer_and_h2_storage.AdvancedElectrolyzer"

    component_id: ComponentID
    #: Electrical power lost as heat while the machine runs, in W.
    waste_energy: float = 400  # [W]
    #: Lowest electrical power the machine may be run at, in W.
    min_power: float = 1_200  # [W]
    #: Highest electrical power the machine accepts, in W.
    max_power: float = 2_400  # [W]
    #: Part load ``min_power`` corresponds to, in percent of the machine's rating.
    min_power_percent: float = 60  # [%]
    #: Part load ``max_power`` corresponds to, in percent of the machine's rating.
    max_power_percent: float = 100  # [%]
    #: Hydrogen produced at ``min_power``, in normal litres per hour.
    min_hydrogen_production_rate_hour: float = 300  # [Nl/h]
    #: Hydrogen produced at ``max_power``, in normal litres per hour.
    max_hydrogen_production_rate_hour: float = 5000  # [Nl/h]
    #: Pressure the hydrogen leaves at, in bar. Recorded, not simulated.
    pressure_hydrogen_output: float = 30  # [bar]

    @preset(note="the 2.4 kW household machine")
    @classmethod
    def preset_standard(cls, name: str) -> "ElectrolyzerWithStorageConfig":
        """The 2.4 kW household electrolyzer, the only one this module has ever built.

        The field defaults are that machine: 1.2 to 2.4 kW, which is 60 to 100 % of its
        rating, 300 to 5000 normal litres of hydrogen an hour, 400 W of waste heat and 30 bar
        at the outlet. ``standard`` is the name -- the machine names no manufacturer, no
        technology and no catalogue row, and nothing here is derived from the scenario.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            ElectrolyzerWithStorageConfig: The preset configuration.
        """
        return cls(component_id=ComponentID(name=name))


@dataclass_json
@dataclass
class ElectrolyzerWithHydrogenStorageConfig(ConfigBase):
    """Capacity, fill and charging rates of the hydrogen tank behind the electrolyzer.

    The tank holds between ``min_capacity`` and ``max_capacity`` kilograms of hydrogen,
    accepts and releases at most the two rates per hour, and starts a simulation holding
    ``starting_fill``. The one tank this library states is :meth:`preset_standard`::

        ElectrolyzerWithHydrogenStorageConfig.preset_standard("HydrogenStorage")
    """

    MAIN_CLASS = "hisim.components.generic_electrolyzer_and_h2_storage.HydrogenStorage"

    component_id: ComponentID
    #: Hydrogen that always stays in the tank, in kg; withdrawal stops here.
    min_capacity: float = 0  # [kg_H2]
    #: Hydrogen the tank holds when it is full, in kg.
    max_capacity: float = 500  # [kg_H2]
    #: Hydrogen in the tank when the simulation starts, in kg.
    starting_fill: float = 400  # [kg_H2]
    #: Most hydrogen the tank takes in an hour, in kg/h.
    max_charging_rate_hour: float = 2  # [kg/h]
    #: Most hydrogen the tank gives out in an hour, in kg/h.
    max_discharging_rate_hour: float = 2  # [kg/h]
    #: Electricity spent compressing a kilogram into the tank, in kWh/kg.
    energy_for_charge: float = 0  # [kWh/kg]
    #: Electricity spent releasing a kilogram from the tank, in kWh/kg.
    energy_for_discharge: float = 0  # [kWh/kg]
    #: Share of the stored hydrogen lost per day, in percent.
    loss_factor_per_day: float = 0  # [lost_%/day]

    @preset(note="the 500 kg tank, four fifths full at the start")
    @classmethod
    def preset_standard(cls, name: str) -> "ElectrolyzerWithHydrogenStorageConfig":
        """The 500 kg hydrogen tank, the only one this module has ever built.

        The field defaults are that tank: 500 kg of capacity down to an empty floor, 400 kg in
        it when the simulation starts, 2 kg an hour in and out, and neither compression work
        nor a daily loss. ``standard`` is the name -- a tank of this size names no
        manufacturer and nothing here is derived from the scenario.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            ElectrolyzerWithHydrogenStorageConfig: The preset configuration.
        """
        return cls(component_id=ComponentID(name=name))


class ElectrolyzerSimulation:
    """Electrolyzer simulation class."""

    def __init__(
        self,
        waste_energy: float,
        min_power: float,
        max_power: float,
        min_power_percent: float,
        max_power_percent: float,
        min_hydrogen_production_rate: float,
        max_hydrogen_production_rate: float,
        pressure_hydrogen_output: float,
    ):
        """Initialze the class.

        The electrolyzer converts electrical energy [kWh] into hydrogen [kg]
        It can work in a certain range from x to 100% or be switched off = 0%
        The conversion rate is given by the supplier and is directly used
            maybe a change to efficiency can be made but its just making things more complex with no benefit
        Between the given values, the values are calculated by an interpolation.
            --> If the load curve is linear a fixed factor could be calculated.

        Therefore it has an operational state
        All the min values and  all the max values are connected and the electrolyzer can operate between them.

        The waste energy in electolyzers is not used to provide heat for the households demand
        Output pressure may be used in the future for the
        """
        self.state = 0
        self.min_power = min_power
        self.max_power = max_power
        self.min_power_percent = min_power_percent
        self.max_power_percent = max_power_percent

        self.min_hydrogen_production_rate = min_hydrogen_production_rate
        self.max_hydrogen_production_rate = max_hydrogen_production_rate
        self.min_hydrogen_production_rate_hour = min_hydrogen_production_rate
        self.max_hydrogen_production_rate_hour = max_hydrogen_production_rate
        self.waste_energy = waste_energy
        self.pressure_hydrogen_output = pressure_hydrogen_output  # not used so far
        # Density of green hydrogen is a physical constant (standard conditions);
        # cache it once instead of calling get_properties_for_energy_carrier on every timestep.
        self.hydrogen_density = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=lt.LoadTypes.GREEN_HYDROGEN
        ).density_in_kg_per_m3

    def convert_electricity(self, electricity_input, hydrogen_not_stored):
        """Convert electricity.

        Electricity from electricity distributor (combination of PV, Grid, CHP and demand) will be converted to hydrogen

        Check if electricity input is inside the power range of electrolyzer and eventually subtract the unusable power.
        Calculate the current power level of the electrolyzer in this timestep
        Calculate the current hydrogen output in liter
        Conversion to kg_H2
        calculate the Oxygen output by mass fraction in the water molecule

        :param      electricity_input:      Electricity from electricity distributor [W]
        :return:    hydrogen_output [kg]    Amount of produced hydrogen
                    oxygen_output [kg]      Amount of produced oxygen
                    unused_power [W]        Unused power if electricity_input is outside the operation range
        """

        # interpolation between two points. Future: add input data with higher resolution.. but not provided
        # interpolation for current power level & hydrogen output
        power_level = self.min_power_percent + (100 - self.min_power_percent) * (electricity_input - self.min_power) / (
            self.max_power - self.min_power
        )
        assert self.min_power_percent <= power_level <= self.max_power_percent
        # Nl / s
        hydrogen_output_liter = self.min_hydrogen_production_rate + (
            (self.max_hydrogen_production_rate - self.min_hydrogen_production_rate)
            * (power_level - self.min_power_percent)
            / (self.max_power_percent - self.min_power_percent)
        )
        assert self.min_hydrogen_production_rate <= hydrogen_output_liter <= self.max_hydrogen_production_rate
        # kg/s = l/s / 1000 * kg/m³
        hydrogen_output = (hydrogen_output_liter / 1000) * self.hydrogen_density
        oxygen_output = hydrogen_output * (88.8 / 11.2)

        if hydrogen_not_stored > 0:
            hydrogen_real = hydrogen_output - hydrogen_not_stored
            if hydrogen_real == 0:
                return hydrogen_output, 0, 0, 0
            hydrogen_output_liter_real = hydrogen_real * 1000 / self.hydrogen_density
            power_level_real = self.min_power_percent + (
                hydrogen_output_liter_real - self.min_hydrogen_production_rate
            ) * (self.max_power_percent - self.min_power_percent) / (
                self.max_hydrogen_production_rate - self.min_hydrogen_production_rate
            )
            electricity_input_real = self.min_power + (self.max_power - self.min_power) * (
                power_level_real - self.min_power_percent
            ) / (100 - self.min_power_percent)
            oxygen_output = hydrogen_real * (88.8 / 11.2)

            return (
                hydrogen_output,
                oxygen_output,
                power_level_real,
                electricity_input_real,
            )
        if hydrogen_not_stored < 0:
            log.error("Error")
            raise ValueError("hydrogen was leftover")
        if hydrogen_not_stored == 0:
            electricity_input_real = electricity_input
            return hydrogen_output, oxygen_output, power_level, electricity_input_real

        """
        # if there are more datapoints given
        # power_datapoints [60%, 70%,...,100]
        # output_datapoints [300Nl/h, 350Nl/h, 500Nl/h]

        if power_level in power_datapoints:
            for i in power_datapoints:
                if power_level == power_datapoints[i]:
                    hydrogen_output = output_datapiont[i]
        else:
            # get closest values
            for i in power_datapoints:
                if power_level > power_datapoints[i]:
                    lower_datapoint = i
                if power_level < power_datapoints[i]:
                    upper_datapoint = i
                    break
            # interpolation
            hydrogen_output = output_datapoints[lower_datapoint] + (
                    output_datapoints[upper_datapoint] - output_datapoints[lower_datapoint]) * (
                                      power_datapoints[upper_datapoint] - power_level) / (
                                      power_datapoints[upper_datapoint] - (power_datapoints[lower_datapoint])
        """

        # Water consists out of H2O. The molecular weight is 11.2% Hydrogen and 88.8% Oxygen.
        # This means: From the calculated hydrogen mass, the oxygen mass can be inferred
        # this is just a fun fact and is not needed for simulation
        oxygen_output = hydrogen_output * (88.8 / 11.2)

        return hydrogen_output, oxygen_output, power_level


class AdvancedElectrolyzer(Component):
    """Advanced Electrolyzer class."""

    cost_relevance = CostRelevance.PRICED

    # input
    ElectricityInput = "ElectricityInput"  # W
    HydrogenNotStored = "HydrogenNotStored"  # kg/s
    # output
    WaterDemand = "WaterDemand"  # kg/s
    HydrogenOutput = "HydrogenOutput"  # kg/s
    OxygenOutput = "OxygenOutput"  # kg/s
    EnergyLosses = "EnergyLosses"  # W
    UnusedPower = "UnusedPower"  # W
    ElectrolyzerEfficiency = "ElectrolyzerEfficiency"  # -
    PowerLevel = "PowerLevel"  # %
    ElectricityRealNeeded = "ElectricityRealNeeded"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectrolyzerWithStorageConfig,
        my_display_config: DisplayConfig | None = None,
    ):
        """Initialize the class."""
        if my_display_config is None:
            my_display_config = DisplayConfig()

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # input
        self.hydrogen_not_stored_channel: ComponentInput = self.add_input(
            self.component_name,
            AdvancedElectrolyzer.HydrogenNotStored,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG,
            True,
        )

        self.electricity_input_channel: ComponentInput = self.add_input(
            self.component_name,
            AdvancedElectrolyzer.ElectricityInput,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            True,
        )
        # output
        self.water_demand_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.WaterDemand,
            lt.LoadTypes.WATER,
            lt.Units.KG_PER_SEC,
            output_description="Water Demand",
        )
        self.hydrogen_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.HydrogenOutput,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Hydrogen Output",
        )
        self.oxygen_output_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.OxygenOutput,
            lt.LoadTypes.OXYGEN,
            lt.Units.KG_PER_SEC,
            output_description="Oxygen Output",
        )
        self.energy_losses_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.EnergyLosses,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Energy Losses",
        )
        self.unused_power_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.UnusedPower,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Unused Power",
        )
        self.electricity_real_needed_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.ElectricityRealNeeded,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Electricity Real Needed",
        )

        self.electrolyzer_efficiency_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.ElectrolyzerEfficiency,
            lt.LoadTypes.ANY,
            lt.Units.ANY,
            output_description="Electrolyzer Efficiency",
        )
        self.power_level_channel: ComponentOutput = self.add_output(
            self.component_name,
            AdvancedElectrolyzer.PowerLevel,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Power Level",
        )

        self.max_power: float = config.max_power
        self.min_power = config.min_power
        self.waste_energy = config.waste_energy
        min_hydrogen_production_rate = config.min_hydrogen_production_rate_hour / 3600  # [Nl/s]
        max_hydrogen_production_rate = config.max_hydrogen_production_rate_hour / 3600  # [Nl/s]

        self.electrolyzer = ElectrolyzerSimulation(
            waste_energy=config.waste_energy,
            min_power=config.min_power,
            max_power=config.max_power,
            min_power_percent=config.min_power_percent,
            max_power_percent=config.max_power_percent,
            min_hydrogen_production_rate=min_hydrogen_production_rate,
            max_hydrogen_production_rate=max_hydrogen_production_rate,
            pressure_hydrogen_output=config.pressure_hydrogen_output,
        )
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.previous_state = 0

    def write_to_report(self):
        """Write to report."""
        lines = []
        lines.append("CHP operation with constant electical and thermal power: " + self.component_name)
        return lines

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self):
        """Saves the state."""
        self.previous_state = self.electrolyzer.state

    def i_restore_state(self):
        """Restores the state."""
        self.electrolyzer.state = self.previous_state

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        electricity_input: float = stsv.get_input_value(self.electricity_input_channel)
        hydrogen_input = stsv.get_input_value(self.hydrogen_not_stored_channel)

        if electricity_input < 0:
            raise Exception("trying to run electrolyzer with negative amount" + str(electricity_input))

        # operations can be skipped if there is no action (electricity_input == 0) in this timestep
        # maybe advance this to electricity_input >= ElectrolyzerConfig.min_power
        hydrogen_output = 0
        oxygen_output = 0
        waste_energy_output: float = 0
        unused_power: float = 0
        power_level = 0
        # the following is already regulated in the electricity distributor

        if electricity_input > self.max_power:
            unused_power = electricity_input - self.max_power
            electricity_input = self.max_power
        elif 0 <= electricity_input < self.min_power:
            unused_power = electricity_input
            electricity_input = 0
        if electricity_input >= self.min_power:
            (
                hydrogen_output,
                oxygen_output,
                power_level,
                electricity_needed,
            ) = self.electrolyzer.convert_electricity(electricity_input, hydrogen_input / self.seconds_per_timestep)
            # the losses ae included in the efficiency providedd by supplyer and are not calculated separately
            waste_energy_output = self.waste_energy
            # unused_hydrogen = charging_amount - hydrogen_input  # add if needed?
            unused_power = unused_power + (electricity_input - electricity_needed)
        electricity_real_needed = stsv.get_input_value(self.electricity_input_channel) - unused_power
        # water is split into these products
        if oxygen_output == 0:
            water_consumption = 0
        else:
            water_consumption = hydrogen_output + oxygen_output
        try:
            # -/- = kg/s * J/kg / W
            electrolyzer_efficiency = (
                hydrogen_output
                * PhysicsConfig.get_properties_for_energy_carrier(
                    energy_carrier=lt.LoadTypes.GREEN_HYDROGEN
                ).lower_heating_value_in_joule_per_kg
            ) / electricity_input
            assert self.min_power <= electricity_input <= self.max_power
        except ZeroDivisionError:
            electrolyzer_efficiency = 0
            assert electricity_input < self.min_power

        stsv.set_output_value(self.water_demand_channel, water_consumption)
        stsv.set_output_value(self.hydrogen_output_channel, hydrogen_output)
        stsv.set_output_value(self.oxygen_output_channel, oxygen_output)
        stsv.set_output_value(self.energy_losses_channel, waste_energy_output)

        stsv.set_output_value(self.electricity_real_needed_channel, electricity_real_needed)
        stsv.set_output_value(self.unused_power_channel, unused_power)
        stsv.set_output_value(self.electrolyzer_efficiency_channel, electrolyzer_efficiency)
        stsv.set_output_value(self.power_level_channel, power_level)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass


class HydrogenStorageSimulation:
    """Hydrogen storage simulation class.

    Hydrogen storage to store hydrogen which is produced by the electrolyzer.
    The charging and sometimes discharging of the tank is connected with an electricity (energy) consumption.
    (Compressor, cooling,...). This energy is given by a fixed value which is not related to the actual fill level.
    Losses are related to the fill level and are given in lost%/24h.
    """

    def __init__(
        self,
        fill: float,
        max_capacity: float,
        min_capacity: float,
        max_charging_rate: float,
        max_discharging_rate: float,
        energy_to_charge: float,
        energy_to_discharge: float,
        loss_factor: float,
    ):
        """Initialize the class.

        The storage has a minimum and maximum storage level and a current fill status
        The fill status must be between the min and max capacity
        A maximum charging and discharging rate restrict these processes.
        """
        self.fill = fill
        self.max_capacity = max_capacity
        self.min_capacity = min_capacity
        self.max_charging_rate = max_charging_rate
        self.max_discharging_rate = max_discharging_rate
        self.energy_to_charge = energy_to_charge
        self.energy_to_discharge = energy_to_discharge
        self.loss_factor = loss_factor

        # assert self.max_charging_rate > (ElectrolyzerConfig.max_hydrogen_production_rate / 1000 * PhysicsConfig.hydrogen_density)
        # assert self.max_discharging_rate > chp.CHPConfig.P_total_max / PhysicsConfig.hydrogen_specific_fuel_value_per_kg

    def store(self, hydrogen_input: float, seconds_per_timestep: int) -> Any:
        """Store.

        Notice: Write return statement and the function goes back to to the caller method immediately
        Storage function:
        The maximum possible charging amount is calculated.
        The hydrogen inflow is restricted by this. If its too high the delta cant be stored.

        Calculate the new fill level.
        Possible restrictions due to a full tank:

            Nothing can be loaded.
            Parts of the hydrogen inflow can be loaded.

        Calculate the energy consumption which is needed for the process.

        :param      hydrogen_input:         Hydrogen to the tank [kg_H2]
        :param      seconds_per_timestep:   Seconds in this timestep [s]
        :return:    hydrogen_input [kg]     Amount of hydrogen which was stored in this timestep
                    energy_demand [W]       Consumed Energy for storing
                    delta_not_stored [kg]   Amount of hydrogen which was not stored in this timestep

        """
        max_charging = self.max_charging_rate * seconds_per_timestep

        delta_not_stored: float = 0
        if hydrogen_input > max_charging:
            delta_not_stored = hydrogen_input - max_charging
            hydrogen_input = max_charging

        if self.fill + hydrogen_input < self.max_capacity:
            # fits completely
            self.fill += hydrogen_input
            # W = kg * kWh/kg * 3600_s/h * 1000_1/k / s
            energy_demand = hydrogen_input * self.energy_to_charge * 3600 * 1000 / seconds_per_timestep
            return hydrogen_input, energy_demand, delta_not_stored
        if self.fill >= self.max_capacity:
            # tank is already full
            hydrogen_input = 0
            energy_demand = 0
            # limitation by tanksize and charging rate
            delta_not_stored += hydrogen_input
            return hydrogen_input, energy_demand, delta_not_stored
        if self.fill < self.max_capacity:
            # fits partially
            # returns amount which an be put in
            amount_stored = self.max_capacity - self.fill
            self.fill += amount_stored
            delta_not_stored = hydrogen_input - amount_stored
            energy_demand = amount_stored * self.energy_to_charge * 3600 * 1000 / seconds_per_timestep  # w
            return amount_stored, energy_demand, delta_not_stored
        raise Exception("forgotten case")

    def withdraw(self, hydrogen_output: float, seconds_per_timestep: int) -> Any:
        """Withdraw.

        Discharging function. Functionality its the reverse of the store function.

        :param      hydrogen_output:        Demand on hydrogen [kg_H2]
        :param      seconds_per_timestep:   Seconds in this timestep [s]
        :return:    hydrogen_output [kg]    Amount of hydrogen which was released in this timestep
                    energy_demand [W]       Consumed Energy for release
                    delta_not_released [kg] Amount of hydrogen which couldn't be released in this timestep
        """
        max_discharging = self.max_discharging_rate * seconds_per_timestep
        delta_not_released: float = 0
        if hydrogen_output > max_discharging:
            delta_not_released = hydrogen_output - max_discharging
            hydrogen_output = max_discharging

        if self.fill > hydrogen_output:
            # has enough
            self.fill -= hydrogen_output
            energy_demand = hydrogen_output * self.energy_to_discharge * 3600 * 1000 / seconds_per_timestep  # W
            return hydrogen_output, energy_demand, delta_not_released
        if self.fill <= self.min_capacity:
            # empty
            energy_demand = 0
            delta_not_released = hydrogen_output
            return 0, energy_demand, delta_not_released
        if self.fill < hydrogen_output:
            # can provide hydrogen partially,
            # added recently :but in this case to simplify work of CHP, say that no hydrogen can be provided
            amount = self.fill
            delta_not_released = hydrogen_output - self.fill
            self.fill = 0
            energy_demand = amount * self.energy_to_discharge * 3600 * 1000 / seconds_per_timestep  # W
            return 0, 0, delta_not_released
        raise Exception("forgotten case")

    def storage_losses(self, seconds_per_timestep):
        """Storage losses.

        How much hydrogen is lost in the time period:
        Liquid tanks:  evaporation
        Pressure tanks: leakages
        metalhydride:   suppliers say there are no losses (??)

        The losses per day are given in %. This means a full tank has more losses compared to a half full tank.
        First, the given loss factor is converted into the losses in the timestep.
        Loses this timestep are calculated and subtracted from the hydrogen storage
        :param      seconds_per_timestep:   Seconds in this timestep [s]
        :return:    losses in this timestep in kg
        """
        # conversion from lost_%/24h to lost_%/timestep

        hydrogen_losses_this_timestep = self.loss_factor / 24 * (seconds_per_timestep / 3600)
        # % have to be divided by 100
        losses_this_timestep = self.fill * (hydrogen_losses_this_timestep / 100)
        self.fill -= losses_this_timestep
        return losses_this_timestep


class HydrogenStorage(Component):
    """Hydrogen storage class."""

    cost_relevance = CostRelevance.PRICED

    # input
    ChargingHydrogenAmount = "ChargingHydrogenAmount"  # kg/s
    DischargingHydrogenAmountTarget = "DischargingHydrogenAmountTarget"  # kg/s
    # output
    CurrentHydrogenFillLevel = "CurrentHydrogenFillLevelAbsolute"  # kg
    CurrentHydrogenFillLevelPercent = "CurrentHydrogenFillLevelPercent"  # %
    StorageDelta = "StorageDelta"  # kg
    HydrogenNotStored = "HydrogenNotStored"  # kg
    HydrogenNotReleased = "HydrogenNotReleased"  # kg

    HydrogenStorageEnergyDemand = "HydrogenStorageEnergyDemand"  # W
    HydrogenLosses = "HydrogenLosses"  # kg
    DischargingHydrogenAmountReal = "DischargingHydrogenAmountReal"  # kg/s

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: ElectrolyzerWithHydrogenStorageConfig,
        my_display_config: DisplayConfig | None = None,
    ):
        """Initialize the class."""
        if my_display_config is None:
            my_display_config = DisplayConfig()

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.charging_hydrogen: ComponentInput = self.add_input(
            self.component_name,
            HydrogenStorage.ChargingHydrogenAmount,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            True,
        )
        self.discharging_hydrogen: ComponentInput = self.add_input(
            self.component_name,
            HydrogenStorage.DischargingHydrogenAmountTarget,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            False,
        )

        self.current_fill: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.CurrentHydrogenFillLevel,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG,
            output_description="Current Hydrogen Fill Level",
        )
        self.current_fill_percent: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.CurrentHydrogenFillLevelPercent,
            lt.LoadTypes.ANY,
            lt.Units.PERCENT,
            output_description="Current Hydrogen Fill Level Percent",
        )
        self.storage_delta: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.StorageDelta,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG_PER_SEC,
            output_description="Storage Delta",
        )
        self.hydrogen_not_stored: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.HydrogenNotStored,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG,
            output_description="Hydrogen not Stored",
        )
        self.hydrogen_not_released: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.HydrogenNotReleased,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG,
            output_description="Hydrogen not Released",
        )
        self.hydrogen_storage_energy_demand: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.HydrogenStorageEnergyDemand,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="Hydrogen Storage Energy Demand",
        )
        self.hydrogen_losses: ComponentOutput = self.add_output(
            self.component_name,
            HydrogenStorage.HydrogenLosses,
            lt.LoadTypes.GREEN_HYDROGEN,
            lt.Units.KG,
            output_description="Hydrogen Losses",
        )
        self.discharging_hydrogen_real: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=HydrogenStorage.DischargingHydrogenAmountReal,
            load_type=lt.LoadTypes.GREEN_HYDROGEN,
            unit=lt.Units.KG_PER_SEC,
            sankey_flow_direction=False,
            output_description="Discharging Hydrogen Amount Real",
        )

        self.max_capacity = config.max_capacity
        self.seconds_per_timestep = my_simulation_parameters.seconds_per_timestep
        self.previous_state: float = 0
        self.max_charging_rate = config.max_charging_rate_hour / 3600
        self.max_discharging_rate = config.max_discharging_rate_hour / 3600

        self.hydrogenstorage = HydrogenStorageSimulation(
            fill=config.starting_fill,
            max_capacity=config.max_capacity,
            min_capacity=config.min_capacity,
            max_charging_rate=self.max_charging_rate,
            max_discharging_rate=self.max_discharging_rate,
            energy_to_charge=config.energy_for_charge,
            energy_to_discharge=config.energy_for_discharge,
            loss_factor=config.loss_factor_per_day,
        )

    def i_save_state(self):
        """Saves the state."""
        self.previous_state = self.hydrogenstorage.fill

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_restore_state(self):
        """Restores the state."""
        self.hydrogenstorage.fill = self.previous_state

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # Setting up the internal values
        charging_amount_sec = stsv.get_input_value(self.charging_hydrogen)
        charging_amount = charging_amount_sec * self.seconds_per_timestep
        # takes positive value for the discharge
        discharging_amount_sec = stsv.get_input_value(self.discharging_hydrogen)
        discharging_amount = discharging_amount_sec * self.seconds_per_timestep

        if charging_amount < 0:
            raise Exception("trying to charge with negative amount" + str(charging_amount))
        if discharging_amount < 0:
            raise Exception("trying to discharge with negative amount: " + str(discharging_amount))
        # operations can be skipped if there is no action in this timestep
        hydrogen_input = 0
        hydrogen_output = 0
        charging_energy_demand = 0
        discharging_energy_demand = 0
        hydrogen_not_stored: float = 0
        hydrogen_not_released = 0

        if charging_amount > 0 and discharging_amount > 0:
            # simultaneous charging and discharging has to be prevented
            # hydrogen can be used directly
            delta = charging_amount - discharging_amount
            if delta >= 0:
                charging_amount = delta
                discharging_amount = 0
            else:
                charging_amount = 0
                discharging_amount = -delta
        if charging_amount > 0 and discharging_amount > 0:
            # to check previous if command
            # a heat stick in the storage tank would be better --> direct use of el.Energy
            raise Exception(
                "Tank cant be charged and discharged in the same timestep. Use existing Hydrogen! Delta:" + str(delta)
            )

        if charging_amount > 0:
            (
                hydrogen_input,
                charging_energy_demand,
                hydrogen_not_stored,
            ) = self.hydrogenstorage.store(charging_amount, self.seconds_per_timestep)
            # unused_hydrogen = charging_amount - hydrogen_input  # add if needed?
        if discharging_amount > 0:
            (
                hydrogen_output,
                discharging_energy_demand,
                hydrogen_not_released,
            ) = self.hydrogenstorage.withdraw(discharging_amount, self.seconds_per_timestep)
            if hydrogen_not_released > 0:
                hydrogen_output = 0
                discharging_energy_demand = 0
        losses_this_timestep = self.hydrogenstorage.storage_losses(self.seconds_per_timestep)
        # discharging amount target
        energy_demand = charging_energy_demand + discharging_energy_demand  # W
        actual_delta = (hydrogen_input - hydrogen_output - losses_this_timestep) / self.seconds_per_timestep  # kg/s
        percent_fill = self.hydrogenstorage.fill / self.max_capacity

        hydrogen_not_stored = stsv.get_input_value(self.charging_hydrogen) * self.seconds_per_timestep - hydrogen_input

        stsv.set_output_value(self.hydrogen_storage_energy_demand, energy_demand)
        stsv.set_output_value(self.hydrogen_losses, losses_this_timestep)
        stsv.set_output_value(self.storage_delta, actual_delta)
        stsv.set_output_value(self.hydrogen_not_stored, hydrogen_not_stored)
        stsv.set_output_value(self.hydrogen_not_released, hydrogen_not_released)
        stsv.set_output_value(self.current_fill, self.hydrogenstorage.fill)
        stsv.set_output_value(self.current_fill_percent, percent_fill)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

    def write_to_report(self) -> List[str]:
        """Write to report."""
        lines = []
        lines.append("Hydrogen Storage: " + self.component_name)
        return lines
