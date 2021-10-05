# Generic/Built-in

# Owned
import component as cp
import loadtypes as lt

class Controller(cp.Component):
    ElectricityInput = "ElectricityInput"
    State = "State"

    def __init__(self, mode=1):
        super().__init__("FlexibleController")

        self.build(mode)

        # Retrieves Electricity SUM
        self.electricity_inputC: cp.ComponentInput = self.add_input(self.ComponentName,
                                                                 self.ElectricityInput,
                                                                 lt.LoadTypes.Electricity,
                                                                 lt.Units.Watt,
                                                                 True)
        # Returns boolean based on control condition
        self.stateC: cp.ComponentOutput = self.add_output(self.ComponentName,
                                                        self.State,
                                                        lt.LoadTypes.Any,
                                                        lt.Units.Any)

    def build(self, mode):
        self.mode = mode

    def i_save_state(self):
        pass
        #self.previous_state = self.state

    def i_restore_state(self):
        pass
        #self.state = self.previous_state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        if force_convergence:
            return
        val1 = stsv.get_input_value(self.electricity_inputC)
        if self.mode == 1:
            state = 1
        elif self.mode == 2:
            if (val1 < 0.0):
                state = 1
            else:
                state = 0
        else:
            raise Exception("Mode not defined!")
        stsv.set_output_value(self.stateC, state)





















































