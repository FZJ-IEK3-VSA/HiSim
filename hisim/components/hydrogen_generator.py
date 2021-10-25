# Owned

from components.configuration import HydrogenStorageConfig, ElectrolyzerConfig
from components.configuration import PhysicsConfig
import components.chp_system as chp

from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from components.configuration import ElectrolyzerConfig
import loadtypes as lt

from components.configuration import PhysicsConfig


class ElectrolyzerSimulation:
    def __init__(self, config: ElectrolyzerConfig):
        """
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
        self.min_power = config.min_power
        self.max_power = config.max_power
        self.min_power_percent = config.min_power_percent
        self.max_power_percent = config.max_power_percent

        self.min_hydrogen_production_rate = config.min_hydrogen_production_rate
        self.max_hydrogen_production_rate = config.max_hydrogen_production_rate

        self.waste_energy = config.waste_energy
        self.pressure_hydrogen_output = config.pressure_hydrogen_output     # not used so far

    def convert_electricity(self, electricity_input, seconds_per_timestep):
        """
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
        power_level = self.min_power_percent + (100 - self.min_power_percent) * (electricity_input- self.min_power) / (self.max_power - self.min_power)
        assert self.min_power_percent <= power_level <= self.max_power_percent
        # Nl / s
        hydrogen_output_liter = self.min_hydrogen_production_rate + ((self.max_hydrogen_production_rate - self.min_hydrogen_production_rate) * (power_level - self.min_power_percent) / (self.max_power_percent - self.min_power_percent))
        assert self.min_hydrogen_production_rate <= hydrogen_output_liter <= self.max_hydrogen_production_rate
        # kg/s = l/s / 1000 * kg/m³
        hydrogen_output = (hydrogen_output_liter / 1000) * PhysicsConfig.hydrogen_density

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

class Electrolyzer(Component):
    # input
    ElectricityInput = "Electricity Input"              # W

    # output
    WaterDemand = "Water Demand"                        # kg/s
    HydrogenOutput = "Hydrogen Output"                  # kg/s
    OxygenOutput = "Oxygen Output"                      # kg/s
    EnergyLosses = "Energy Losses"                      # W
    UnusedPower = "Unused Power"                        # W
    ElectrolyzerEfficiency = "Electrolyzer Efficiency"  # -
    PowerLevel = "Power Level"                          # %

    def __init__(self, component_name, config: ElectrolyzerConfig, seconds_per_timestep):
        super().__init__(component_name)
        # input
        self.electricity_input: ComponentInput = self.add_input(self.ComponentName, Electrolyzer.ElectricityInput, lt.LoadTypes.Electricity, lt.Units.Watt, True)
        # output
        self.water_demand: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.WaterDemand, lt.LoadTypes.Water, lt.Units.kg_per_sec)
        self.hydrogen_output: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.HydrogenOutput, lt.LoadTypes.Hydrogen, lt.Units.kg_per_sec)
        self.oxygen_output: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.OxygenOutput, lt.LoadTypes.Oxygen, lt.Units.kg_per_sec)
        self.energy_losses: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.EnergyLosses, lt.LoadTypes.Electricity, lt.Units.Watt)
        self.unused_power: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.UnusedPower, lt.LoadTypes.Electricity, lt.Units.Watt)
        self.electrolyzer_efficiency: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.ElectrolyzerEfficiency, lt.LoadTypes.Any, lt.Units.Any)
        self.power_level: ComponentOutput = self.add_output(self.ComponentName, Electrolyzer.PowerLevel, lt.LoadTypes.Any, lt.Units.Percent)

        # self
        self.electrolyzer = ElectrolyzerSimulation(config)
        self.seconds_per_timestep = seconds_per_timestep
        self.previous_state = 0

    def i_save_state(self):
        self.previous_state = self.electrolyzer.state

    def i_restore_state(self):
        self.electrolyzer.state = self.previous_state

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        electricity_input = stsv.get_input_value(self.electricity_input)

        if electricity_input < 0:
            raise Exception("trying to run electrolyzer with negative amount" + str(electricity_input))

        # operations can be skipped if there is no action (electricity_input == 0) in this timestep
        # maybe advance this to electricity_input >= ElectrolyzerConfig.min_power
        hydrogen_output = 0
        oxygen_output = 0
        losses_this_timestep = 0
        unused_power = 0
        power_level = 0

        # the following is already regulated in the electricity distributor
        if electricity_input > ElectrolyzerConfig.max_power:
            unused_power = electricity_input - ElectrolyzerConfig.max_power
            electricity_input = ElectrolyzerConfig.max_power
        elif 0 <= electricity_input < ElectrolyzerConfig.min_power:
            unused_power = electricity_input
            electricity_input = 0
        elif electricity_input < 0:
            print("Electricity to electrolyzer is negative")
            raise ValueError

        if electricity_input >= ElectrolyzerConfig.min_power:
            hydrogen_output, oxygen_output, power_level = self.electrolyzer.convert_electricity(electricity_input, self.seconds_per_timestep)
            # the losses ae included in the efficiency providedd by supplyer and are not calculated separately
            losses_this_timestep = ElectrolyzerConfig.waste_energy
            # unused_hydrogen = charging_amount - hydrogen_input  # add if needed?
        # water is split into these products
        water_consumption = hydrogen_output + oxygen_output
        try:
            # -/- = kg/s * J/kg / W
            electrolyzer_efficiency = (hydrogen_output * PhysicsConfig.hydrogen_specific_fuel_value_per_kg) / electricity_input
            assert ElectrolyzerConfig.min_power <= electricity_input <= ElectrolyzerConfig.max_power
        except ZeroDivisionError:
            electrolyzer_efficiency = 0
            assert electricity_input < ElectrolyzerConfig.min_power

        stsv.set_output_value(self.water_demand, water_consumption)
        stsv.set_output_value(self.hydrogen_output, hydrogen_output)
        stsv.set_output_value(self.oxygen_output, oxygen_output)
        stsv.set_output_value(self.energy_losses, losses_this_timestep)
        stsv.set_output_value(self.unused_power, unused_power)
        stsv.set_output_value(self.electrolyzer_efficiency, electrolyzer_efficiency)
        stsv.set_output_value(self.power_level, power_level)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

class HydrogenStorageSimulation:
    """
    Hydrogen storage to store hydrogen which is produced by the electrolyzer.
    The charging and sometimes discharging of the tank is connected with an electricity (energy) consumption. (Compressor, cooling,...). This energy is given by a fixed value which is not related to the actual fill level.
    Losses are related to the fill level and are given in lost%/24h.
    """
    def __init__(self, config: HydrogenStorageConfig):
        """
        The storage has a minimum and maximum storage level and a current fill status
        The fill status must be between the min and max capacity
        A maximum charging and discharging rate restrict these processes.
        """
        self.fill = config.starting_fill
        self.max_capacity = config.max_capacity                 # kg
        self.min_capacity = config.min_capacity                 # kg
        self.max_charging_rate = config.max_charging_rate       # kg/s
        self.max_discharging_rate = config.max_discharging_rate # kg/s
        self.energy_to_charge = config.energy_for_charge        # kWh/kg
        self.energy_to_discharge = config.energy_for_discharge  # kWh/kg
        self.loss_factor = config.loss_factor_per_day           # %/day

        assert self.max_charging_rate > (ElectrolyzerConfig.max_hydrogen_production_rate / 1000 * PhysicsConfig.hydrogen_density)
        assert self.max_discharging_rate > chp.CHPConfig.P_total_max / PhysicsConfig.hydrogen_specific_fuel_value_per_kg

    def store(self, hydrogen_input: float, seconds_per_timestep):
        """
        Notice: Write return statement and the function goes back to to the caller method immediately
        Storage function:
        The maximum possible charging amount is calculated.
        The hydrogen inflow is restricted by this. If its too high the delta cant be stored.

        Calculate the new fill level.
        Possible restrictions due to a full tank.
            Nothing can be loaded
            Parts of the hydrogen inflow can be loaded.
        Calculate the energy consumption which is needed for the process.

        :param      hydrogen_input:         Hydrogen to the tank [kg_H2]
        :param      seconds_per_timestep:   Seconds in this timestep [s]
        :return:    hydrogen_input [kg]     Amount of hydrogen which was stored in this timestep
                    energy_demand [W]       Consumed Energy for storing
                    delta_not_stored [kg]   Amount of hydrogen which was not stored in this timestep
        """
        max_charging = self.max_charging_rate * seconds_per_timestep

        delta_not_stored = 0
        if hydrogen_input > max_charging:
            delta_not_stored = hydrogen_input - max_charging
            hydrogen_input = max_charging

        if self.fill + hydrogen_input < self.max_capacity:
            # fits completely
            self.fill += hydrogen_input
            # W = kg * kWh/kg * 3600_s/h * 1000_1/k / s
            energy_demand = hydrogen_input * self.energy_to_charge * 3600 * 1000 / seconds_per_timestep
            return hydrogen_input, energy_demand, delta_not_stored
        elif self.fill >= self.max_capacity:
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
            energy_demand = amount_stored * self.energy_to_charge * 3600 * 1000 / seconds_per_timestep    # w
            return amount_stored, energy_demand, delta_not_stored
        raise Exception("forgotten case")

    def withdraw(self, hydrogen_output: float, seconds_per_timestep):
        """
        Discharging function. Functionality its the reverse of the store function.
        :param      hydrogen_output:        Demand on hydrogen [kg_H2]
        :param      seconds_per_timestep:   Seconds in this timestep [s]
        :return:    hydrogen_output [kg]    Amount of hydrogen which was released in this timestep
                    energy_demand [W]       Consumed Energy for release
                    delta_not_released [kg] Amount of hydrogen which couldn't be released in this timestep
        """
        max_discharging = self.max_discharging_rate * seconds_per_timestep
        delta_not_released = 0

        if hydrogen_output > max_discharging:
            delta_not_released = hydrogen_output - max_discharging
            hydrogen_output = max_discharging

        if self.fill > hydrogen_output:
            # has enough
            self.fill -= hydrogen_output
            energy_demand = hydrogen_output * self.energy_to_discharge * 3600 * 1000 / seconds_per_timestep   # W
            return hydrogen_output, energy_demand, delta_not_released
        if self.fill <= self.min_capacity:
            # empty
            energy_demand = 0
            delta_not_released = hydrogen_output
            return 0, energy_demand, delta_not_released
        if self.fill < hydrogen_output:
            # can provide hydrogen partially
            amount = self.fill
            delta_not_released = hydrogen_output - self.fill
            self.fill = 0
            energy_demand = amount * self.energy_to_discharge * 3600 * 1000 / seconds_per_timestep    # W
            return amount, energy_demand, delta_not_released
        raise Exception("forgotten case")

    def storage_losses(self, seconds_per_timestep):
        """
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
    # input
    ChargingHydrogenAmount = "Charging Hydrogen Amount"                     # kg/s
    DischargingHydrogenAmount = "Discharging Hydrogen Amount"               # kg/s
    # output
    CurrentHydrogenFillLevel = "Current Hydrogen Fill Level Absolute"       # kg
    CurrentHydrogenFillLevelPercent = "Current Hydrogen Fill Level Percent" # %
    StorageDelta = "Storage Delta"                                          # kg
    HydrogenNotStored = "Hydrogen Not Stored"                               # kg
    HydrogenNotReleased = "Hydrogen Not Released"                           # kg
    HydrogenStorageEnergyDemand = "Hydrogen Storage Energy Demand"          # W
    HydrogenLosses = "Hydrogen Losses"                                      # kg

    def __init__(self, component_name, config: HydrogenStorageConfig, seconds_per_timestep):
        super().__init__(component_name)
        self.charging_hydrogen: ComponentInput = self.add_input(self.ComponentName, HydrogenStorage.ChargingHydrogenAmount, lt.LoadTypes.Hydrogen, lt.Units.kg_per_sec, True)
        self.discharging_hydrogen: ComponentInput = self.add_input(self.ComponentName, HydrogenStorage.DischargingHydrogenAmount, lt.LoadTypes.Hydrogen, lt.Units.kg_per_sec, False)

        self.current_fill: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.CurrentHydrogenFillLevel, lt.LoadTypes.Hydrogen, lt.Units.kg)
        self.current_fill_percent: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.CurrentHydrogenFillLevelPercent, lt.LoadTypes.Hydrogen, lt.Units.Percent)
        self.storage_delta: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.StorageDelta, lt.LoadTypes.Hydrogen, lt.Units.kg_per_sec)
        self.hydrogen_not_stored: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.HydrogenNotStored, lt.LoadTypes.Hydrogen, lt.Units.kg)
        self.hydrogen_not_released: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.HydrogenNotReleased, lt.LoadTypes.Hydrogen, lt.Units.kg)
        self.hydrogen_storage_energy_demand: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.HydrogenStorageEnergyDemand, lt.LoadTypes.Electricity, lt.Units.Watt)
        self.hydrogen_losses: ComponentOutput = self.add_output(self.ComponentName, HydrogenStorage.HydrogenLosses, lt.LoadTypes.Hydrogen, lt.Units.kg)

        self.hydrogenstorage = HydrogenStorageSimulation(config)

        self.seconds_per_timestep = seconds_per_timestep
        self.previous_state = 0

    def i_save_state(self):
        self.previous_state = self.hydrogenstorage.fill

    def i_restore_state(self):
        self.hydrogenstorage.fill = self.previous_state

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
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
        hydrogen_not_stored = 0
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
            raise Exception("Tank cant be charged and discharged in the same timestep. Use existing Hydrogen! Delta:" + str(delta))

        if charging_amount > 0:
            hydrogen_input, charging_energy_demand, hydrogen_not_stored = self.hydrogenstorage.store(charging_amount, self.seconds_per_timestep)
            # unused_hydrogen = charging_amount - hydrogen_input  # add if needed?
        if discharging_amount > 0:
            hydrogen_output, discharging_energy_demand, hydrogen_not_released = self.hydrogenstorage.withdraw(discharging_amount, self.seconds_per_timestep)

        losses_this_timestep = self.hydrogenstorage.storage_losses(self.seconds_per_timestep)

        energy_demand = charging_energy_demand + discharging_energy_demand          # W
        actual_delta = (hydrogen_input - hydrogen_output - losses_this_timestep) / self.seconds_per_timestep      # kg/s
        percent_fill = self.hydrogenstorage.fill / HydrogenStorageConfig.max_capacity

        stsv.set_output_value(self.hydrogen_storage_energy_demand, energy_demand)
        stsv.set_output_value(self.hydrogen_losses, losses_this_timestep)
        stsv.set_output_value(self.storage_delta, actual_delta)
        stsv.set_output_value(self.hydrogen_not_stored, hydrogen_not_stored)
        stsv.set_output_value(self.hydrogen_not_released, hydrogen_not_released)
        stsv.set_output_value(self.current_fill, self.hydrogenstorage.fill)
        stsv.set_output_value(self.current_fill_percent, percent_fill)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass

class ElectricityDistributor(Component):
    PowerPV = "PowerPV"
    PowerCHP = "PowerCHP"
    DemandHousehold = "DemandHousehold"

    PowerToElectrolyzer = "PowerToElectrolyzer"
    PowerToFromGrid = "PowerToFromGrid"

    def __init__(self, component_name):
        super().__init__(component_name)
        # input
        self.power_PV: ComponentInput = self.add_input(self.ComponentName, ElectricityDistributor.PowerPV, lt.LoadTypes.Electricity, lt.Units.Watt, True)
        self.power_CHP: ComponentInput = self.add_input(self.ComponentName, ElectricityDistributor.PowerCHP, lt.LoadTypes.Electricity, lt.Units.Watt, True)
        self.demand_household: ComponentInput = self.add_input(self.ComponentName, ElectricityDistributor.DemandHousehold, lt.LoadTypes.Electricity, lt.Units.Watt, True)

        # output
        self.power_to_electrolyzer: ComponentOutput = self.add_output(self.ComponentName, ElectricityDistributor.PowerToElectrolyzer,  lt.LoadTypes.Electricity, lt.Units.Watt)
        self.power_from_to_grid: ComponentOutput = self.add_output(self.ComponentName, ElectricityDistributor.PowerToFromGrid,  lt.LoadTypes.Electricity, lt.Units.Watt)


    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        """
        Distributor to control the electricity flow in the system.
        Energy is generated by PV and CHP
        Energy goes to the Electrolyzer if its in its range, otherwise into the grid.

        Only one unit (W) so no influence of the timestep
        """
        power_pv = stsv.get_input_value(self.power_PV)
        power_chp = stsv.get_input_value(self.power_CHP)
        demand_household = stsv.get_input_value(self.demand_household)

        power_supply = power_pv + power_chp
        power_after_own_consumtion = power_supply - demand_household
        # new calculation in each timestep
        power_from_to_grid = 0
        power_to_electrolyzer = 0

        if power_after_own_consumtion < 0:
            # adding a negative value gives a negative result --> electricity from grid
            power_from_to_grid += power_after_own_consumtion
        elif power_after_own_consumtion < ElectrolyzerConfig.min_power:
            # Electrolyzer cant operate at this low power
            power_from_to_grid = power_after_own_consumtion
        elif ElectrolyzerConfig.min_power <= power_after_own_consumtion <= ElectrolyzerConfig.max_power:
            # power in range of Electrolyzer so all the power goes into it
            power_to_electrolyzer = power_after_own_consumtion

        elif power_after_own_consumtion > ElectrolyzerConfig.max_power:
            # Power goes to Electrolyzer and to grid
            power_to_electrolyzer = ElectrolyzerConfig.max_power
            power_after_own_consumtion -= ElectrolyzerConfig.max_power
            power_from_to_grid = power_after_own_consumtion

        stsv.set_output_value(self.power_to_electrolyzer, power_to_electrolyzer)
        stsv.set_output_value(self.power_from_to_grid, power_from_to_grid)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        # alle ausgabewerte die zu überprüfen sind können hiermit fehlerausgabeüberprüft werden
        pass
