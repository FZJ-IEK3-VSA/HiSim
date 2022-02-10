# Generic/Built-in
import json
import copy
import sqlite3
import datetime
import os
import pandas as pd
from typing import List
from hisim.simulationparameters import SimulationParameters
# Owned
from hisim import component as cp
from hisim import loadtypes as lt
from hisim import utils

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class VehiclePure(cp.Component):
    """
    Vehicle component class

    Parameters
    ----------
    manufacturer : str
        Electric vehicle Manufacturer
    model : str
        Electric vehicle model
    soc : float
        Initial state of charge of battery before
        simulation
    profile : str
        Family profile imported from LPG. The family
        denomination defines the electric vehicle usage
        throughout the simulation duration
    """
    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 manufacturer="Tesla",
                 model="Model 3 v3",
                 soc=1.0,
                 profile="CH01"):
        super().__init__(name="EV_charger", my_simulation_parameters=my_simulation_parameters)
        self.build(manufacturer=manufacturer, model=model, soc=soc, target=profile)

    def build(self, manufacturer, model, soc, target):
        """:key

        Defines ...
        max_capacity
        min_capacity
        capacity
        discharing
        car_location
        """
        if soc > 1 or soc < 0:
            raise Exception("Invalid State Of Charge.")

        # Gets flexibilities, including heat pump
        electric_vehicle_database = utils.load_smart_appliance("Electric Vehicle")

        electric_vehicle_found = False
        for electric_vehicle in electric_vehicle_database:
            if electric_vehicle["Manufacturer"] == manufacturer and electric_vehicle["Model"] == model:
                electric_vehicle_found = True
                break

        if electric_vehicle_found == False:
            raise Exception("Electric Vehicle not registered in the database")

        self.max_capacity = electric_vehicle['Battery Capacity'] * 1E3
        if "Battery Useful Capacity" in electric_vehicle:
            self.min_capacity = self.max_capacity - electric_vehicle["Battery Useful Capacity"] * 1E3
        else:
            self.min_capacity = self.max_capacity * 0.1

        capacity = self.max_capacity * soc
        if capacity < self.min_capacity:
            capacity = self.min_capacity

        self.model = model
        self.capacity = capacity

        cache_filepath = utils.get_cache("Vehicle", [target])
        if cache_filepath is not None:
            self.car_in_charging_station = pd.read_csv(cache_filepath, sep=',', decimal='.')[
                'CarInChargingStation'].tolist()
            self.discharge = pd.read_csv(cache_filepath, sep=',', decimal='.')[
                'Discharge'].tolist()
        else:
            def open_sql(path, table_name):
                sql_file = sqlite3.connect(path)
                return pd.read_sql("SELECT * FROM {};".format(table_name), sql_file)
            def open_ev_json(filepath):
                with open(filepath) as f:
                    data = json.load(f)
                return data["Values"]

            FILEPATH = utils.load_export_load_profile_generator(target=target)
            if FILEPATH is None:
                FILEPATH = utils.HISIMPATH

            ev_files = dict()
            filepaths = open_sql(FILEPATH["electric_vehicle"][1], "ResultFileEntries")
            list_columns = []
            list_values = []
            for index, row in filepaths.iterrows():
                json_info = json.loads(row["Json"])
                if "Charging" in json_info["FileName"] and "png" not in json_info["FileName"]:
                    filepath = os.path.normpath(json_info["FullFileName"])
                    filepath_list = filepath.split(os.sep)
                    ev_files[filepath_list[-1].split(".")[0]] = json_info["FullFileName"]
                    list_columns.append(filepath_list[-1].split(".")[0])
                    list_values.append(open_ev_json(json_info["FullFileName"]))
            list_values = list(map(list, zip(*list_values)))
            ev_pd = pd.DataFrame(list_values, columns=list_columns)


            # Gets battery information to calculate discharging while not at home
            transportation_devices = open_sql(FILEPATH["electric_vehicle"][0], "TransportationDevices")
            for index, vehicle in transportation_devices.iterrows():
                if "Charging" in vehicle["Name"]:
                    vehicle_info = json.loads(vehicle['Json'])
                    battery_stored_energy_meters = vehicle_info["FullRangeInMeters"]
                    convert_factor = vehicle_info["EnergyToDistanceFactor"]
                    battery_stored_energy_wh = battery_stored_energy_meters * convert_factor


            convert_factor_meters_by_wh = convert_factor * 3600
            soc = []
            load_stats = []
            car_in_charging_station = []
            car_state = []
            discharge_stats = [0]
            # Gets transportation stats
            transportation_devices_stats = open_sql(FILEPATH["electric_vehicle"][0], "TransportationDeviceStates")
            for index, column in transportation_devices_stats.iterrows():
                if datetime.datetime.strptime(column["DateTime"], '%d/%m/%Y %H:%M') > datetime.datetime.strptime('31/12/2018 23:59', '%d/%m/%Y %H:%M'):
                    if "ParkingAndFullyCharged" in column["DeviceState"] or "ParkingAndCharging" in column["DeviceState"]:
                        car_in_charging_station.append(True)
                    else:
                        car_in_charging_station.append(False)
                    load_stats.append(float(column["CurrentRange"]))
                    soc.append(float(column["CurrentRange"])/battery_stored_energy_meters)
                    if len(load_stats) > 1:
                        diff = load_stats[-1] - load_stats[-2]
                        if diff < 0:
                            discharge_stats.append(diff / convert_factor_meters_by_wh)
                        else:
                            discharge_stats.append(0)

            #discharge = []
            #load = []
            #for index, row in ev_pd.iterrows():
            #    load.append(row["Soc"] * battery_stored_energy_wh)
            #    if index == 0:
            #        discharge.append(0)
            #    else:
            #        diff = load[-1] - load[-2]
            #        if diff < 0:
            #            discharge.append(diff)
            #        else:
            #            discharge.append(0)

            #ev_pd["Discharge"] = discharge
            #data = [ev_pd["CarLocation"].tolist()]
            #data.append(discharge)
            #data.append(car_state)
            #data_parameters = ["CarLocation", "Discharging","CarInChargingStation","RealDischarge","CarState"]

            data:List = []
            data.append(car_in_charging_station)
            data.append(discharge_stats)
            data = list(map(list, zip(*data)))

            data_parameters = ["CarInChargingStation", "Discharge"]
            database = pd.DataFrame(data, columns=data_parameters)

            self.car_in_charging_station = car_in_charging_station
            self.discharge = discharge_stats

            utils.save_cache("Vehicle", [target], database)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        pass

class Vehicle(cp.Component):
    """
    Electric Vehicle Component. This is a
    alternative implementation, not fully working,
    to be used in future releases

    Parameters
    ----------
    manufacturer : str
        Electric vehicle Manufacturer
    model : str
        Electric vehicle model
    soc : float
        Initial state of charge of battery before
        simulation
    """
    BeforeCapacity = "BeforeCapacity"

    AfterCapacity = "AfterCapacity"
    MaxCapacity = "MaxCapacity"
    Discharge = "Discharge"

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 manufacturer="Renault",
                 model="Zoe v3",
                 soc=0.8):
        super().__init__(name="ElectricVehicle", my_simulation_parameters=my_simulation_parameters)

        self.build(manufacturer=manufacturer, model=model, soc=soc)

        self.before_capacityC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                               self.BeforeCapacity,
                                                               lt.LoadTypes.Electricity, lt.Units.Watt, True)

        self.after_capacityC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                self.AfterCapacity,
                                                                lt.LoadTypes.Any,
                                                                lt.Units.Watt)
        self.max_capacityC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                self.MaxCapacity,
                                                                lt.LoadTypes.Any,
                                                                lt.Units.Watt)
        self.dischargeC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                            self.Discharge,
                                                            lt.LoadTypes.Electricity,
                                                            lt.Units.Watt)

    def build(self, manufacturer, model, soc):
        if soc > 1 or soc < 0:
            raise Exception("Invalid State Of Charge.")

        # Gets flexibilities, including heat pump
        electric_vehicle_database = utils.load_smart_appliance("Electric Vehicle")

        electric_vehicle_found = False
        for electric_vehicle in electric_vehicle_database:
            if electric_vehicle["Manufacturer"] == manufacturer and electric_vehicle["Model"] == model:
                electric_vehicle_found = True
                break

        if electric_vehicle_found == False:
            raise Exception("Electric Vehicle not registered in the database")

        self.max_capacity = electric_vehicle['Battery Capacity'] * 1E3
        if "Battery Useful Capacity" in electric_vehicle:
            self.min_capacity = self.max_capacity - electric_vehicle["Battery Useful Capacity"] * 1E3
        else:
            self.min_capacity = self.max_capacity * 0.1

        capacity = self.max_capacity * soc
        if capacity < self.min_capacity:
            capacity = self.min_capacity

        self.capacity = capacity

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        if timestep == 0:
            capacity = self.capacity
        else:
            capacity = stsv.get_input_value(self.before_capacityC)
        stsv.set_output_value(self.after_capacityC, capacity)
        stsv.set_output_value(self.max_capacityC, self.max_capacity)

class SimpleStorageState:
    """
    Simplistic implementation for any type
    of energy state storage. Relevant for battery,
    electric vehicles, etc, to store or withdraw
    certain amount of energy of a predefined storage

    Parameters
    ----------
    max_var_val: float
        Maximum variation energy, i.e., maximum power
    min_var_val: float
        Minimum variation energy, i.e., minimum power
    stored_energy : float
        Initial storage energy
    time_correction_factor : float
        Time correction factor to convert power into energy
        the time step duration used in the simulation. This
        factor is equivalent of the invert of seconds per time
        step.
    seconds_per_timestep : int
        Duration in seconds of one time step.
    """
    def __init__(self,
                 max_var_val: float,
                 min_var_val: float,
                 stored_energy=None,
                 time_correction_factor=None,
                 seconds_per_timestep=None):
        self.max_var_val = max_var_val
        self.min_var_val = min_var_val
        self.stored_energy = stored_energy
        self.time_correction_factor = time_correction_factor
        self.seconds_per_timestep = seconds_per_timestep

    def store(self, max_capacity: float, current_capacity: float, val: float, efficiency: float = 1.0):
        if val < max_capacity - current_capacity:
            amount = val
        else:
            amount = max_capacity - current_capacity

        # Check if the theoretical capacity is larger than maximum capacity
        if current_capacity >= max_capacity:
            amount = 0

        # Check if the charging variation is larger than physical possible
        if amount > self.max_var_val:
            amount = self.max_var_val

        amount = amount * efficiency

        current_capacity += amount
        self.stored_energy = current_capacity
        return amount, current_capacity

    def force_store(self, max_capacity: float, current_capacity: float):
        amount = self.max_var_val
        if amount > max_capacity - current_capacity:
            amount = max_capacity - current_capacity

        # Check if the theoretical capacity is larger than maximum capacity
        if current_capacity >= max_capacity:
            amount = 0

        current_capacity += amount
        self.stored_energy = current_capacity
        return amount, current_capacity

    def withdraw(self, min_capacity: float, current_capacity: float, val: float, efficiency: float = 1.0):
        val = abs(val)
        if current_capacity - min_capacity > val:
            amount = -val
        else:
            amount = min_capacity - current_capacity

        if current_capacity <= min_capacity:
            amount = 0

        if amount < self.min_var_val:
            amount = self.min_var_val

        amount = amount / efficiency

        current_capacity += amount
        self.stored_energy = current_capacity
        return amount, current_capacity

    def keep_state(self, capacity):
        charging_delta = 0
        after_capacity = capacity
        return charging_delta, capacity

class EVCharger(cp.Component):
    """
    Electric Vehicle Charger Component
    Parameters:
    ----------------
        manufacturer : str
        name : str
        electric_vehicle : Vehicle_Pure
        sim_params : cp.SimulationParameters

    """
    # Inputs
    ElectricityInput = "ElectricityInput"
    #StoredEnergy = "StoredEnergy"
    #MaxStoredEnergy = "MaxStoredEnergy"
    EVChargerState = "EVChargerState"
    EVChargerMode = "EVChargerMode"
    MinimumStateOfCharge = "MinimumStateOfCharge"

    # Outputs
    StateOfCharge = "StateOfCharge"
    AfterStoredEnergy = "AfterStoredEnergy"
    Driving = "Driving"
    AtChargingStation = "AtChargingStation"
    ElectricityOutput = "ElectricityOutput"

    # Similar components to connect to:
    # 1. EVChargerController
    # 2. Some ChargingInput

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 manufacturer="myenergi",
                 name="Wallbox ZAPPI 222TW",
                 electric_vehicle=None,
                 sim_params=None):
        super().__init__(name="EVCharger", my_simulation_parameters=my_simulation_parameters)

        self.build(manufacturer=manufacturer, name=name, electric_vehicle=electric_vehicle, sim_params=sim_params)

        self.state = SimpleStorageState(max_var_val=self.charging_power,
                                        min_var_val=-self.charging_power,
                                        stored_energy=self.electric_vehicle.capacity,
                                        time_correction_factor=self.time_correction_factor,
                                        seconds_per_timestep=self.seconds_per_timestep)


        self.previous_state = copy.deepcopy(self.state)

        self.charging_inputC : cp.ComponentInput = self.add_input(self.ComponentName,
                                                               self.ElectricityInput,
                                                               lt.LoadTypes.Electricity,
                                                               lt.Units.Watt,
                                                               True)
        self.stateC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                     self.EVChargerState,
                                                     lt.LoadTypes.Any,
                                                     lt.Units.Any,
                                                     True)
        self.modeC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                    self.EVChargerMode,
                                                    lt.LoadTypes.Any,
                                                    lt.Units.Any,
                                                    True)
        self.min_socC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                          self.MinimumStateOfCharge,
                                                          lt.LoadTypes.Any,
                                                          lt.Units.Any,
                                                          True)

        self.socC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                    self.StateOfCharge,
                                                    lt.LoadTypes.Any,
                                                    lt.Units.Any)
        self.after_capacityC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                self.AfterStoredEnergy,
                                                                lt.LoadTypes.Any,
                                                                lt.Units.Any)
        self.drivingC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                          self.Driving,
                                                          lt.LoadTypes.Any,
                                                          lt.Units.Any)
        self.at_charging_stationC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                            self.AtChargingStation,
                                                            lt.LoadTypes.Any,
                                                            lt.Units.Any)
        self.electricity_outputC : cp.ComponentOutput = self.add_output(self.ComponentName,
                                                                        self.ElectricityOutput,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt,
                                                                        True)

    def build(self, manufacturer, name, electric_vehicle, sim_params):
        self.time_correction_factor = 1 / sim_params.seconds_per_timestep
        self.seconds_per_timestep = sim_params.seconds_per_timestep

        electric_vehicle_charger_database = utils.load_smart_appliance("Wallbox")
        evcharger_found = False
        for evcharger in electric_vehicle_charger_database:
            if evcharger["Manufacturer"] == manufacturer and evcharger["Name"] == name:
                charging_power = evcharger["Charging Power"] * 1E3
                evcharger_found = True
                break

        if evcharger_found == False:
            raise Exception("EV Charging Station has not been found in the database.")

        self.manufacturer = manufacturer
        self.name = name
        self.charging_power_original = charging_power * 1E-3
        self.charging_power = charging_power * self.time_correction_factor
        self.electric_vehicle = electric_vehicle

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.ComponentName))
        lines.append("Manufacturer: {}".format(self.manufacturer))
        lines.append("Model: {}".format(self.name))
        lines.append("Charging Power: {} kW".format(self.charging_power_original))
        lines.append("EV Battery Capacity: {}".format(self.electric_vehicle.max_capacity*1E-3))
        lines.append("Vehicle: {}".format(self.electric_vehicle.model))
        return lines

    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        if force_convergence:
            return

        # Gets inputs
        charging = stsv.get_input_value(self.charging_inputC)
        state = stsv.get_input_value(self.stateC)
        mode = stsv.get_input_value(self.modeC)
        min_soc = stsv.get_input_value(self.min_socC)

        capacity = self.state.stored_energy
        max_capacity = self.electric_vehicle.max_capacity

        min_capacity = max_capacity * min_soc

        if bool(self.electric_vehicle.car_in_charging_station[timestep]):
            connected_to_charging_station = 1
            driving = 0
            to_be_charged = -charging

            if state == 0:
                charging_delta, after_capacity = self.state.keep_state(capacity=capacity)
            elif state == 1:
                charging_delta, after_capacity = self.charge_only_from_the_grid(max_capacity=max_capacity,
                                                                                capacity=capacity)
            elif state == 2:
                charging_delta, after_capacity = self.charge_only_electricity_surplus(to_be_charged=to_be_charged,
                                                                                      max_capacity=max_capacity,
                                                                                      capacity=capacity)
            elif state == 3:
                charging_delta, after_capacity = self.operate_on_vehicle_to_grid(to_be_charged=to_be_charged,
                                                                                 max_capacity=max_capacity,
                                                                                 min_capacity=min_capacity,
                                                                                 capacity=capacity)
            else:
                raise Exception("State {} has not been implemented! "
                                "Please check EVCharger and EVChargerController".format(state))

        # Discharges battery according to electric vehicle use
        elif self.electric_vehicle.discharge[timestep] != 0.0:
            connected_to_charging_station = 0
            driving = 1

            charging_delta, after_capacity = self.state.withdraw(min_capacity=0,
                                                                 current_capacity=capacity,
                                                                 val=self.electric_vehicle.discharge[timestep])
            charging_delta = 0
        else:
            connected_to_charging_station = 0
            driving = 0
            charging_delta, after_capacity = self.state.keep_state(capacity=capacity)


        stsv.set_output_value(self.socC, after_capacity/max_capacity)
        stsv.set_output_value(self.after_capacityC, after_capacity)
        stsv.set_output_value(self.drivingC, driving)
        stsv.set_output_value(self.electricity_outputC, charging_delta)
        stsv.set_output_value(self.at_charging_stationC, connected_to_charging_station)

    def charge_only_electricity_surplus(self, to_be_charged, max_capacity, capacity):
        if to_be_charged >= 0:
            charging_delta, after_capacity = self.state.store(max_capacity=max_capacity,
                                                              current_capacity=capacity,
                                                              val=to_be_charged)
        else:
            charging_delta, after_capacity = self.state.keep_state(capacity=capacity)
        return charging_delta, after_capacity

    def operate_on_vehicle_to_grid(self, to_be_charged, max_capacity, min_capacity, capacity):
        if to_be_charged >= 0:
            charging_delta, after_capacity = self.state.store(max_capacity=max_capacity,
                                                              current_capacity=capacity,
                                                              val=to_be_charged)
        elif to_be_charged < 0:
            charging_delta, after_capacity = self.state.withdraw(min_capacity=min_capacity,
                                                                 current_capacity=capacity,
                                                                 val=to_be_charged)
        else:
            charging_delta, after_capacity = self.state.keep_state(capacity=capacity)
        return charging_delta, after_capacity

    def charge_only_from_the_grid(self, max_capacity, capacity):
        charging_delta, after_capacity = self.state.force_store(max_capacity=max_capacity, current_capacity=capacity)
        return charging_delta, after_capacity

class EVChargerController(cp.Component):
    """
    Imports data from Load Profile Generator and
    uses as a Component Class in hisim
    """
    # Inputs
    ElectricityInput = "ElectricityInput"
    StateOfCharge = "StateOfCharge"

    # Outputs
    EVChargerState = "EVChargerState"
    EVChargerMode = "EVChargerMode"
    MinimumStateOfCharge = "MinimumStateOfCharge"

    # Similar components to connect to:
    # 1. Some ChargingInput

    def __init__(self,
                 my_simulation_parameters: SimulationParameters,
                 mode=1):
        super().__init__(name="EVChargerController", my_simulation_parameters=my_simulation_parameters)
        self.mode = mode

        if mode == 1:
            self.mode_description = "Straight Charging"
            self.mode_extended_description = "Charge the Electric Vehicle whenever is connected to EV Charger"
        elif mode == 2:
            self.mode_description = "Charge only on Electricity Surplus"
            self.mode_extended_description = "Charge the Electric Vehicle whenever the home grid is on electricity surplus"
        elif mode == 3:
            self.mode_description = "Operate on Vehicle-to-Grid"
            self.mode_extended_description = "Charge the Electric Vehicle whenever the home grid is on electricity surplus. " \
                                             "Discharge the Electric Vehicle whenenver the home grid is running on electricity deficit."
        elif mode == 4:
            self.mode_description = "Stepped Prioritized Charging"
            self.mode_extended_description = "Charge only from the grid for SoC from 0% up to 60%. " \
                                             "Charge only from the grid only on the electricity surplus for SoC from 60% up to 80%. " \
                                             "Operate on Vehicle-to-Grid for SoC from 80% up to 100%"
        elif mode == 5:
            self.mode_description = "Tight Stepped Prioritized Charging"
            self.mode_extended_description = "Charge only from the grid for SoC from 0% up to 40%. " \
                                             "Charge only from the grid only on the electricity surplus for SoC from 40% up to 70%. " \
                                             "Operate on Vehicle-to-Grid for SoC from 70% up to 100%"
        elif mode == 6:
            self.mode_description = "Super Tight Stepped Prioritized Charging"
            self.mode_extended_description = "Charge only from the grid for SoC from 0% up to 20%. " \
                                         "Charge only from the grid only on the electricity surplus for SoC from 20% up to 60%. " \
                                         "Operate on Vehicle-to-Grid for SoC from 60% up to 100%"
        else:
            self.mode_description = "WRITE MODE DESCRIPTION HERE!"
            self.mode_extended_description = "WRITE MODE EXTENDED DESCRIPTION HERE!"

        # Inputs
        self.charging_inputC: cp.ComponentInput        = self.add_input(self.ComponentName,
                                                                        self.ElectricityInput,
                                                                        lt.LoadTypes.Electricity,
                                                                        lt.Units.Watt,
                                                                        True)
        self.socC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                        self.StateOfCharge,
                                                        lt.LoadTypes.Any,
                                                        lt.Units.Any,
                                                        True)


        self.stateC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                       self.EVChargerState,
                                                       lt.LoadTypes.Any,
                                                       lt.Units.Any)
        self.modeC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                       self.EVChargerMode,
                                                       lt.LoadTypes.Any,
                                                       lt.Units.Any)
        self.min_socC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                            self.MinimumStateOfCharge,
                                                            lt.LoadTypes.Any,
                                                            lt.Units.Any)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        # Gets inputs
        charging = stsv.get_input_value(self.charging_inputC)
        state_of_charge = stsv.get_input_value(self.socC)

        minimum_state_of_charge = 0.1

        # Straight Charging
        if self.mode == 1:
            state = 1
        # Charge only on Electricity Surplus
        elif self.mode == 2:
            state = 2
        # Operate on Vehicle-to-Grid
        elif self.mode == 3:
            state = 3
        # Stepped Prioritized Charging
        elif self.mode == 4:
            # Under this condition, charge only from the grid
            if state_of_charge < 0.6:
                state = 1
            # Under this condition, charge only on electricity surplus
            elif state_of_charge >= 0.6 and state_of_charge < 0.8:
                state = 2
            # Under this condition, charge on Vehicle-to-Grid basis
            elif state_of_charge >= 0.8:
                minimum_state_of_charge = 0.8
                state = 3

        # Tight Stepped Prioritized Charging
        elif self.mode == 5:
            # Under this condition, charge only from the grid
            if state_of_charge < 0.4:
                state = 1
            # Under this condition, charge only on electricity surplus
            elif state_of_charge >= 0.4 and state_of_charge < 0.7:
                state = 2
            # Under this condition, charge on Vehicle-to-Grid basis
            elif state_of_charge >= 0.7:
                minimum_state_of_charge = 0.7
                state = 3

        # Super Tight Stepped Prioritized Charging
        elif self.mode == 6:
            # Under this condition, charge only from the grid
            if state_of_charge < 0.2:
                state = 1
            # Under this condition, charge only on electricity surplus
            elif state_of_charge >= 0.2 and state_of_charge < 0.6:
                state = 2
            # Under this condition, charge on Vehicle-to-Grid basis
            elif state_of_charge >= 0.6:
                minimum_state_of_charge = 0.6
                state = 3
        else:
            if self.mode is None:
                raise Exception("No mode has been implemented.")
            else:
                raise Exception("Mode {} has not been implemented yet.".format(self.mode))

        stsv.set_output_value(self.stateC, state)
        stsv.set_output_value(self.min_socC, minimum_state_of_charge)
        stsv.set_output_value(self.modeC, self.mode)


    def write_to_report(self):
        lines = []
        lines.append("Mode Number: {}".format(self.mode))
        lines.append("Mode: {}".format(self.mode_description))
        try:
            lines.append("Extend Mode Description: {}".format(self.mode_extended_description))
        except AttributeError:
            lines.append("Extend Mode Description: TO BE IMPLEMENTED")
        return lines
