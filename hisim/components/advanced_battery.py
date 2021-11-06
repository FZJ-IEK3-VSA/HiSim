import numpy as np
import copy

# Owned
from component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
import loadtypes as lt

class AdvancedBatteryState:

    def __init__(self, soc: float, P_bs: float, _th):
        self.soc = soc
        self.P_bs = P_bs
        self._th = _th

class AdvancedBattery(Component):
    # Inputs
    LoadingPowerInput = "LoadingPowerInput"

    # Outputs
    #Pbat = "DCPower"
    ACBatteryPower = "AC Battery Power"
    StateOfCharge = "State Of Charge"

    def __init__(self, parameter, sim_params,capacity):
        super().__init__("AdvancedBattery")

        self.build(parameter, simulation_parameters=sim_params, capacity=capacity)

        self.state = AdvancedBatteryState(soc=0.0, P_bs=0.0, _th=False)
        self.previous_state = copy.copy(self.state)


        self.Pr_C: ComponentInput = self.add_input(self.ComponentName,
                                                   self.LoadingPowerInput,
                                                   lt.LoadTypes.Electricity,
                                                   lt.Units.Watt,
                                                   True)

        self.P_bs_C : ComponentOutput = self.add_output(self.ComponentName,
                                                       self.ACBatteryPower,
                                                       lt.LoadTypes.Electricity,
                                                       lt.Units.Watt)

        self.soc_C : ComponentOutput = self.add_output(self.ComponentName,
                                                       self.StateOfCharge,
                                                       lt.LoadTypes.Any,
                                                       lt.Units.Any)

    def build(self, parameter, sim_params,capacity):
        self.BatMod_AC(d=parameter, _dt=sim_params.seconds_per_timestep,cap=capacity)

    def BatMod_AC(self, d, _dt, cap):
        """Performance Simulation function for AC-coupled advanced_battery systems

        :param d: array containing parameters
        :type d: numpy array
        :param dt: time step width
        :type dt: integer
        """
        # Loading of particular variables
        self._dt = _dt
        self._E_BAT = cap
        self._eta_BAT = d[1]
        self._t_CONSTANT = d[2]
        self._P_SYS_SOC0_DC = d[3]
        self._P_SYS_SOC0_AC = d[4]
        self._P_SYS_SOC1_DC = d[5]
        self._P_SYS_SOC1_AC = d[6]
        self._AC2BAT_a_in = d[7]
        self._AC2BAT_b_in = d[8]
        self._AC2BAT_c_in = d[9]
        self._BAT2AC_a_out = d[10]
        self._BAT2AC_b_out = d[11]
        self._BAT2AC_c_out = d[12]
        self._P_AC2BAT_DEV = d[13]
        self._P_BAT2AC_DEV = d[14]
        self._P_BAT2AC_out = d[15]
        self._P_AC2BAT_in = d[16]
        self._t_DEAD = int(round(d[17]))
        self._SOC_h = d[18]

        self._P_AC2BAT_min = self._AC2BAT_c_in
        self._P_BAT2AC_min = self._BAT2AC_c_out

        # Correction factor to avoid over charge and discharge the advanced_battery
        self.corr = 0.1

        # Initialization of particular variables

        self._tde = self._t_CONSTANT > 0  # Binary variable to activate the first-order time delay element
        # Factor of the first-order time delay element
        self._ftde = 1 - np.exp(-_dt / self._t_CONSTANT)


        # Capacity of the advanced_battery, conversion from kWh to Wh
        self._E_BAT *= 1000

        # Effiency of the advanced_battery in percent
        self._eta_BAT /= 100

        # Check if the dead or settling time can be ignored and set flags accordingly
        if _dt >= (3 * self._t_CONSTANT) or self._tend == 1:
            _tstart = 1
            self.T_DEAD = False
        else:
            self.T_DEAD = True

        if _dt >= self._t_DEAD + 3 * self._t_CONSTANT:
            self.SETTLING = False
        else:
            self.SETTLING = True

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.ComponentName))
        lines.append("Power: {:3.0f} kWh".format(self._E_BAT*1E-3) )

        return lines
    def i_save_state(self):
        self.previous_state = copy.deepcopy(self.state)

    def i_restore_state(self):
        self.state = copy.deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        # Inputs

        Pr = stsv.get_input_value(self.Pr_C)
        t = timestep

        # Calculation
        # Energy content of the advanced_battery in the previous time step
        E_b0 = self.state.soc * self._E_BAT

        # Calculate the AC power of the advanced_battery system from the residual power
        P_bs = Pr

        # Check if the advanced_battery holds enough unused capacity for charging or discharging
        # Estimated amount of energy in Wh that is supplied to or discharged from the storage unit.
        E_bs_est = P_bs * self._dt / 3600

        # Reduce P_bs to avoid over charging of the advanced_battery
        if E_bs_est > 0 and E_bs_est > (self._E_BAT - E_b0):
            P_bs = (self._E_BAT - E_b0) * 3600 / self._dt
        # When discharging take the correction factor into account
        elif E_bs_est < 0 and np.abs(E_bs_est) > (E_b0):
            P_bs = (E_b0 * 3600 / self._dt) * (1 - self.corr)

        # Adjust the AC power of the advanced_battery system due to the stationary
        # deviations taking the minimum charging and discharging power into
        # account
        if P_bs > self._P_AC2BAT_min:
            P_bs = np.maximum(self._P_AC2BAT_min, P_bs + self._P_AC2BAT_DEV)

        elif P_bs < -self._P_BAT2AC_min:
            P_bs = np.minimum(-self._P_BAT2AC_min, P_bs - self._P_BAT2AC_DEV)

        else:
            P_bs = 0

        # Limit the AC power of the advanced_battery system to the rated power of the
        # advanced_battery converter
        P_bs = np.maximum(-self._P_BAT2AC_out * 1000,
                          np.minimum(self._P_AC2BAT_in * 1000, P_bs))

        # Decision if the advanced_battery should be charged or discharged
        if P_bs > 0 and self.state.soc < 1 - self.state._th * (1 - self._SOC_h):
            # The last term th*(1-SOC_h) avoids the alternation between
            # charging and standby mode due to the DC power consumption of the
            # advanced_battery converter when the advanced_battery is fully charged. The advanced_battery
            # will not be recharged until the SOC falls below the SOC-threshold
            # (SOC_h) for recharging from PV.

            # Normalized AC power of the advanced_battery system
            p_bs = P_bs / self._P_AC2BAT_in / 1000

            # DC power of the advanced_battery affected by the AC2BAT conversion losses
            # of the advanced_battery converter
            P_bat = np.maximum(
                0, P_bs - (self._AC2BAT_a_in * p_bs * p_bs + self._AC2BAT_b_in * p_bs + self._AC2BAT_c_in))

        elif P_bs < 0 and self.state.soc > 0:

            # Normalized AC power of the advanced_battery system
            p_bs = np.abs(P_bs / self._P_BAT2AC_out / 1000)

            # DC power of the advanced_battery affected by the BAT2AC conversion losses
            # of the advanced_battery converter
            P_bat = P_bs - (self._BAT2AC_a_out * p_bs * p_bs +
                            self._BAT2AC_b_out * p_bs + self._BAT2AC_c_out)

        else:  # Neither charging nor discharging of the advanced_battery

            # Set the DC power of the advanced_battery to zero
            P_bat = 0

        # Decision if the standby mode is active
        if P_bat == 0 and self.state.soc <= 0:  # Standby mode in discharged state

            # DC and AC power consumption of the advanced_battery converter
            P_bat = -np.maximum(0, self._P_SYS_SOC0_DC)
            P_bs = self._P_SYS_SOC0_AC

        elif P_bat == 0 and self.state.soc > 0:  # Standby mode in fully charged state

            # DC and AC power consumption of the advanced_battery converter
            P_bat = -np.maximum(0, self._P_SYS_SOC1_DC)
            P_bs = self._P_SYS_SOC1_AC


        # Change the energy content of the advanced_battery from Ws to Wh conversion
        if P_bat > 0:
            E_b = E_b0 + P_bat * np.sqrt(self._eta_BAT) * self._dt / 3600

        elif P_bat < 0:
            E_b = E_b0 + P_bat / np.sqrt(self._eta_BAT) * self._dt / 3600

        else:
            E_b = E_b0

        # Calculate the state of charge of the advanced_battery
        self.state.soc = E_b / (self._E_BAT)

        # Adjust the hysteresis threshold to avoid alternation
        # between charging and standby mode due to the DC power
        # consumption of the advanced_battery converter.
        if self.state._th and self.state.soc > self._SOC_h or self.state.soc > 1:
            self.state._th = True
        else:
            self.state._th = False

        self.state.P_bs = P_bs
        # Outputs
        stsv.set_output_value(self.P_bs_C, P_bs)
        stsv.set_output_value(self.soc_C, self.state.soc)

class AdvancedBatteryController(Component):
    ElectricityInput = "ElectricityInput"
    State = "State"

    def __init__(self):
        super().__init__(name="BatteryController")

        self.inputC : ComponentInput = self.add_input(self.ComponentName,
                                                      self.ElectricityInput,
                                                      lt.LoadTypes.Electricity,
                                                      lt.Units.Wh,
                                                      True)
        self.stateC : ComponentOutput = self.add_output(self.ComponentName,
                                                        self.State,
                                                        lt.LoadTypes.Any,
                                                        lt.Units.Any)

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        load = stsv.get_input_value(self.inputC)

        if load < 0.0:
            state = 1
        elif load > 0.0:
            state = - 1
        else:
            state = 0.0

        stsv.set_output_value(self.stateC, state)










