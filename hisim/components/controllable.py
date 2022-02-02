# Generic/Built-in
import json
import numpy as np
import copy
import matplotlib.pyplot as plt
from typing import List

# Owned
from hisim.component import Component, SingleTimeStepValues, ComponentInput, ComponentOutput
from hisim.utils import HISIMPATH
from hisim import loadtypes as lt


__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

class ControllableState:
    def __init__(self, start=0, currentprofile=None, toRun=False, stateRun=False):
        self.starttimestep = start
        self.profile = currentprofile
        if self.profile != None:
            self.profile_length = len(self.profile)
        self.isRunning = toRun
        self.alreadyRun = stateRun

    def cal_profile(self,timestep):
        if self.profile == None:
            return 0.0
        elif timestep >= len(self.profile) + self.starttimestep:
            return 0.0
        else:
            try:
                return self.profile[timestep-self.starttimestep]
            except:
                print('why')

class Flexibility:
    """
    Represents an execution of a flexibility device
    """

    def __init__(self,name):
        self.name = name
        self.InitialStep : List[int] = []
        self.LastestStep : List[int] = []
        self.ElecProfiles : List[List[float]] = []

        self.ProfilesLen : List[int] = []
        self.HasExec : List[int] = []
        self.NumExecutions : int = 0

class Controllable(Component):
    """
    Imports data from Load Profile Generator. Data contains
    time-shiftable appliances
    :key
    """
    State = "State"

    ElectricityOutput = "ElectricityOutput"
    Task="Task"

    def __init__(self, name):
        super().__init__(name)
        self.values: List[float] = []

        # Imports flexibilities from LPG
        self.flexibility_names : List[str] = []
        self.flexibilities : List[Flexibility] = []

        with open(HISIMPATH["tasks"][1]) as f:
            data = json.load(f)
        self.data_size = len(data)

        start = 1

        self.add_new_flexibility(data[start])

        for arg in data[(start+1):]:
            has_been_found = False
            for i_flex in range(len(self.flexibilities)):
                if self.flexibilities[i_flex].name in arg["Device"]["Name"]:
                    self.append_flex_data(arg, i_flex)
                    has_been_found = True
            if has_been_found == False:
                self.add_new_flexibility(arg)

        # Create entire timeline for
        self.itask = []
        for i_flex in range(len(self.flexibilities)):
            self.itask.append(np.zeros((self.flexibilities[i_flex].LastestStep[-1]), dtype=int))

        # Fills profiles with corresponded execution
        for ytask in range(len(self.itask)):
            for step in range(len(self.flexibilities[ytask].InitialStep)):
                for timestep in range(self.flexibilities[ytask].InitialStep[step], self.flexibilities[ytask].LastestStep[step]):
                        self.itask[ytask][timestep] = step + 1

        self.is_flex_in_database()

        self.itask = self.itask[self.i_xtask]
        self.InitialStep = self.flexibilities[self.i_xtask].InitialStep
        self.LastestStep = self.flexibilities[self.i_xtask].LastestStep
        self.ElecProfiles = self.flexibilities[self.i_xtask].ElecProfiles

        #print("ComponentName: {}".format(self.ComponentName.split()[0]))
        #print("InitialStep: {}".format(self.InitialStep))
        #print("Lastest Step: {}".format(self.LastestStep))

        self.ProfilesLen = self.flexibilities[self.i_xtask].ProfilesLen
        self.HasExec = self.flexibilities[self.i_xtask].HasExec

        self.ApplianceRun: ComponentInput = self.add_input(self.ComponentName,
                                                             self.State,
                                                             lt.LoadTypes.Any,
                                                             lt.Units.Any,
                                                             True)

        self.electricity_outputC: ComponentOutput = self.add_output(self.ComponentName,
                                                                  self.ElectricityOutput,
                                                                  lt.LoadTypes.Electricity,
                                                                  lt.Units.Watt)

        self.taskC: ComponentOutput = self.add_output(self.ComponentName,
                                                      self.Task,
                                                      lt.LoadTypes.Any,
                                                      lt.Units.Any)

        self.state = ControllableState()
        self.previous_state = copy.copy(self.state)

        self.calc_total_load()


    def add_new_flexibility(self,arg):
        self.flexibility_names.append(arg["Device"]["Name"])
        self.flexibilities.append(Flexibility(name=arg["Device"]["Name"]))
        self.append_flex_data(arg, len(self.flexibilities) - 1)

    def append_flex_data(self, arg, i_flex):
        """
        Appends the data about the flexibility execution. This
        includes earlist start, latest start, the conversion factor,
        profile and execution boolean.
        """
        self.flexibilities[i_flex].InitialStep.append(arg['EarliestStart']['ExternalStep'] - 10)
        self.flexibilities[i_flex].LastestStep.append(arg['LatestStart']['ExternalStep'])
        self.flexibilities[i_flex].ElecProfiles.append(
            [i*arg['Profiles'][2]['LoadType']['ConversionFactor']*1E3 for i in arg['Profiles'][2]['Values']])
        # self.flexibilities[i_flex].ElecProfiles.append(
        #     [i for i in arg['Profiles'][2]['Values']])
        self.flexibilities[i_flex].ProfilesLen.append(len(arg['Profiles'][2]['Values']))
        self.flexibilities[i_flex].HasExec.append(0)

    def is_flex_in_database(self):
        flex_found = False
        for i_flex in range(len(self.flexibilities)):
            if self.ComponentName.split()[0] in self.flexibilities[i_flex].name:
                self.i_xtask = i_flex
                flex_found = True
        if flex_found is False:
            raise Exception("No such flexibility was found.")

    def calc_total_load(self):
        sum_total_load = 0
        sum_num_flex = 0
        for index, flex in enumerate(self.flexibilities):
            sum_local = 0
            for elec_profile in flex.ElecProfiles:
                sum_local = sum_local + sum(elec_profile)
            self.flexibilities[index].sum_local = sum_local
            self.flexibilities[index].NumExecutions = len(flex.InitialStep)
            sum_num_flex += self.flexibilities[index].NumExecutions
            sum_total_load += sum_local
        self.number_of_flexibilities = sum_num_flex
        self.sum_total_load = sum_total_load


    def i_save_state(self):
        self.previous_state = copy.copy(self.state)

    def i_restore_state(self):
        self.state = copy.copy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues):
        pass

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        stsv.set_output_value(self.taskC, self.itask[timestep])
        #print(self.itask[timestep])
        #print(self.flexibilities[0].InitialStep)
        #print(self.flexibilities[0].LastestStep)
        #plt.plot(self.itask[2155:6490])
        #plt.show()

        # Check if flexibility is still running
        if self.state.isRunning:
            # Check if flexibility finished already
            if timestep > self.state.starttimestep + self.state.profile_length:
                # Deactivate current flexibility
                self.itask[self.itask == self.itask[self.state.starttimestep]] = 0
                self.state = ControllableState()
            return stsv.set_output_value(self.electricity_outputC, float(self.state.cal_profile(timestep)))
        # Check if controller sent signal to run flexibility and if a flexibility can be run in this timestep
        if stsv.get_input_value(self.ApplianceRun) and (self.itask[timestep] != 0):
            self.state = ControllableState(timestep, self.ElecProfiles[int(self.itask[timestep]-1)], toRun=True)
            return stsv.set_output_value(self.electricity_outputC, self.state.profile[0])

    def write_to_report(self):
        lines = []
        lines.append("Name: {}".format(self.ComponentName))
        lines.append("Number of flexibility of the simulation timeline: {}".format(self.number_of_flexibilities))
        lines.append("Number of unexecuted flexibility: {}".format(len(list(dict.fromkeys(self.itask)))-1))
        lines.append("Non executed flexibility: {}".format(list(dict.fromkeys(self.itask))[1:]))
        lines.append("Total electricity load: {:.0f} kWh".format(self.sum_total_load * 1E-3))
        lines.append("----- Flexibilities -----")
        for index, flex in enumerate(self.flexibilities):
            lines.append("Name: {}".format(self.flexibility_names[index]))
            lines.append("Number of executions: {}".format(flex.NumExecutions))
            lines.append("Total: {:.0f} kWh".format(flex.sum_local * 1E-3))
            lines.append(" ")
        return lines
