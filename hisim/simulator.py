from typing import List
import os
import time
import logging
import datetime
import numpy as np
import datetime
import copy

# Other Libraries
from typing import List
from typing import Tuple
import pandas as pd
import pickle
import warnings


import time

# Owned
from hisim.postprocessing import postprocessing_main as pp
import hisim.component as cp
from hisim.simulationparameters import SimulationParameters
from hisim import loadtypes as lt
#import utils



class ComponentWrapper:
    def __init__(self, component: cp.Component, is_cachable: bool):
        self.MyComponent = component
        self.component_inputs: List[cp.ComponentInput] = []
        self.component_outputs: List[cp.ComponentOutput] = []
        #self.cachedict: = {}
        self.is_cachable = is_cachable

    def register_component_outputs(self, all_outputs: List[cp.ComponentOutput]):
        logging.info("Registering component outputs on " + self.MyComponent.ComponentName)
        # register the output column
        output_columns = self.MyComponent.get_outputs()
        for col in output_columns:
            col.GlobalIndex = len(all_outputs)
            target_output: cp.ComponentOutput
            for output in all_outputs:
                if output.FullName == col.FullName:
                    raise Exception("trying to register the same key twice: " + col.FullName)
            all_outputs.append(col)
            self.component_outputs.append(col)

    def register_component_inputs(self, global_column_dict):
        logging.info("Registering component outputs " + self.MyComponent.ComponentName)
        # look up input columns and cache so we only have the correct columns saved
        inputColumns: List[cp.ComponentInput] = self.MyComponent.get_input_definitions()
        for col in inputColumns:
            globalcol = global_column_dict[col.FullName]
            self.component_inputs.append(globalcol)

    def save_state(self):
        # get called at the beginning of a timestep
        self.MyComponent.i_save_state()
        self.cachedict = {}
        # reset previous values
        # self.previous_iteration_values = [0] * len(self.output_column_list)

    def doublecheck(self, timestep: int,  stsv: cp.SingleTimeStepValues):
        # get called at the beginning of a timestep
        self.MyComponent.i_doublecheck(timestep, stsv)
        # doublecheck values

    def restore_state(self):
        self.MyComponent.i_restore_state()

    def calculate_component(self, timestep: int,  stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        #if(self.is_cachable)
        #cachekey building
        # key = ""
        # for ci in self.component_inputs:
        #     # build the key
        #     key += stsv[ci.]
        # if key in self.cachedict:
        #     for idx in range(len(self.component_outputs))
        #         stsv[self.component_outputs[idx]....] = self.cachedict[key][idx]
        #         #set outputs
        # # send to component
        # else
        self.MyComponent.i_simulate(timestep, stsv, seconds_per_timestep, force_convergence)
        # #save outputs to dict
        # myresults = []
        # for output in self.component_outputs:
        #     #build result list
        #     myresults.append(stsv[output. ...])
        # self.cachedict[key] = myresults

        # a function to decorate
        #self.MyComponent.i_simulate(timestep, stsv, seconds_per_timestep, force_convergence)



    def connect_inputs(self, all_outputs):
        """
        Connects cp.ComponentOutputs to ComponentInputs of
        WrapperComponent

        :key
        """
        # Returns a List of ComponentInputs
        self.MyComponent.get_input_definitions()

        # Loop through lists of inputs of self component
        for cinput in self.MyComponent.inputs:
            # Adds to the ComponentInput List of ComponentWrapper
            self.component_inputs.append(cinput)

            # Creates a ComponentOutput variable
            global_output: cp.ComponentOutput

            # Loop through all the existent component outputs in the current simulation
            for global_output in all_outputs:
                # Check if ComponentOutput and ComponentInput match
                if global_output.ObjectName == cinput.src_object_name and global_output.FieldName == cinput.src_field_name:
                    # Check if ComponentOutput and ComponentInput have the same units
                    if cinput.Unit != global_output.Unit:
                        # Check the use of "Units.Any"
                        if (cinput.Unit == lt.Units.Any and global_output.Unit != lt.Units.Any) or (cinput.Unit != lt.Units.Any and global_output.Unit == lt.Units.Any):
                            warnings.simplefilter("always")
                            warnings.warn("The input %s (cp: %s, unit: %s) and output %s(cp: %s, unit: %s) might not have compatible units." % ( cinput.FieldName,
                                                                                                                                               cinput.ObjectName,
                                                                                                                                               cinput.Unit,
                                                                                                                                               global_output.FieldName,
                                                                                                                                               global_output.ObjectName,
                                                                                                                                               global_output.Unit)) #
                            # ToDo: Dirty code! Look the second else with the same code. Too lazy to figure out sth better
                            # Connect, i.e, save ComponentOutput in ComponentInput
                            cinput.SourceOutput = global_output
                            logging.debug(
                                "connected input '" + cinput.FullName + "' to '" + global_output.FullName + "'")
                        else:
                            raise SystemError("The input %s (cp: %s, unit: %s) and output %s(cp: %s, unit: %s) do not have the same unit!" % ( cinput.FieldName,
                                                                                                                           cinput.ObjectName,
                                                                                                                           cinput.Unit,
                                                                                                                           global_output.FieldName,
                                                                                                                           global_output.ObjectName,
                                                                                                                           global_output.Unit)) #
                    else:
                        # ToDo: Dirty code! Too lazy to figure out sth better
                        # Connect, i.e, save ComponentOutput in ComponentInput
                        cinput.SourceOutput = global_output
                        logging.debug("connected input '" + cinput.FullName + "' to '" + global_output.FullName + "'")

            # Check if there are inputs that have been not connected
            if cinput.Mandatory and cinput.SourceOutput is None:
                raise SystemError(
                    "The ComponentInput %s (cp: %s, unit: %s) is not connected to any ComponentOutput." % (
                        cinput.FieldName,
                        cinput.ObjectName,
                        cinput.Unit))  #

class Simulator:
    def __init__(self, module_directory, setup_function):
        self.setup_function = setup_function
        self.SimulationParameters = None
        self.WrappedComponents: List[ComponentWrapper] = []
        self.all_outputs: List[cp.ComponentOutput] = []

        if os.path.isdir(os.path.join(module_directory, "results")) is False:
            os.mkdir(os.path.join(module_directory, "results"))
        directoryname = "{}_{}".format(setup_function.lower(), datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.dirpath = os.path.join(module_directory, "results", directoryname)
        self.resultsdir = os.path.join(module_directory, "results")
        os.mkdir(self.dirpath)

        # Creates and write result report
        self.report = pp.report.Report(dirpath=self.dirpath)


    def set_parameters(self, simulation_parameters: SimulationParameters) -> SimulationParameters:
        """
        Store the simulation parameters as an attribute of Simulator class.
        """
        self.SimulationParameters = simulation_parameters
        return simulation_parameters

    def add_component(self, component: cp.Component, is_cachable: bool = False):
        """
        Adds component to simulator and wraps it up
        the output in the register.
        """
        if self.SimulationParameters is None:
            raise Exception("Simulation Parameters were not initialized")
        wrap = ComponentWrapper(component, is_cachable)
        wrap.register_component_outputs(self.all_outputs)
        self.WrappedComponents.append(wrap)

    def connect_all_components(self):
        """
        Connects the inputs from every component to the corresponding outputs
        """
        for wc in self.WrappedComponents:
            wc.connect_inputs(self.all_outputs)

    def process_one_timestep(self, timestep: int) -> Tuple[cp.SingleTimeStepValues, int]:
        """
        Executes one simulation timestep. Some components are circurly connected.
        To solve the circular dependency, all components have their states restored
        and simulated until their values converge.

        Firstly, their previously converged state is saved as the current timestep state.
        Following up, all components have their states restored and simulated respectively.
        Convergence is dependent on the i_restore and i_simulate of the components and how they
        are connected to each other.
        """

        # Save states of all components
        # Executes save state in the component
        for wr in self.WrappedComponents:
            wr.save_state()

        continue_calculation = True

        # Verifies data existence
        if(len(self.all_outputs)) == 0:
            raise Exception("Not a single column was defined.")

        # Saves number of outputs
        number_of_outputs = len(self.all_outputs)

        # Creates List with values
        stsv            = cp.SingleTimeStepValues(number_of_outputs)
        # Creates a buffer List with values
        previous_values = cp.SingleTimeStepValues(number_of_outputs)
        iterative_tries = 0
        force_convergence = False

       # Starts loop
        while continue_calculation:
           # Loops through components
            for wr in self.WrappedComponents:
                #if timestep >= 10392:
                #    print("Stop here!")

                # Executes restore state for each component
                wr.restore_state()
                # Executes simulate for component
                wr.calculate_component(timestep, stsv, self.SimulationParameters.seconds_per_timestep, force_convergence)

            # Stops simulation for too small difference between
            # actual values and previous values
            if stsv.is_close_enough_to_previous(previous_values):
                #break
                # todo: replace with epsilon-based check
                continue_calculation = False



            if iterative_tries > 10:
                force_convergence = True
            if iterative_tries > 100:
                # return "error"
                list_of_changed_values = stsv.get_differences_for_error_msg(previous_values, self.all_outputs)
                raise Exception("More than 100 tries in time step " + str(timestep) + "\n" + list_of_changed_values)
            # Copies actual values to previous variable
            previous_values.copy_values_from_other(stsv)
            iterative_tries += 1

        for wr in self.WrappedComponents:
            wr.doublecheck(timestep, stsv)

        return (stsv, iterative_tries)

    def run_all_timesteps(self):
        """
        Performs all the timesteps of the simulation
        and saves the results in the attribute results
        """
        # Error Tests
        # Test if all parameters were initialized
        if self.SimulationParameters is None:
            raise Exception("Simulation Parameters were not initialized")

        # Tests if wrapper has any components at all
        if len(self.WrappedComponents) == 0:
            raise Exception("Not a single component was defined. Quitting.")

        # Starts time counter
        start_counter = time.perf_counter()

        # Connects all components
        self.connect_all_components()
        logging.info("finished connecting all components. A total of " + str(len(self.WrappedComponents)) + " components were defined. They have a total of "
                     + str(len(self.all_outputs)) + " outputs.")
        all_result_lines = []
        logging.info("Starting simulation for " + str(self.SimulationParameters.timesteps) + " timesteps")
        lastmessage = datetime.datetime.now()
        starttime = datetime.datetime.now()
        total_iteration_tries = 0


        for step in range(self.SimulationParameters.timesteps):
            if self.SimulationParameters.timesteps % 500 == 0:
                print("Starting step " + str(step))

            (result, iteration_tries) = self.process_one_timestep(step)

            # Accumulates iteration counter
            total_iteration_tries += iteration_tries

            # Appends
            all_result_lines.append(result.values)

            # Calculates time execution
            elapsed = datetime.datetime.now() - lastmessage

            # For simulation longer than 5 seconds
            if (elapsed.total_seconds() > 5):
                # Calculates time execution
                lastmessage = datetime.datetime.now()
                elapsed = datetime.datetime.now() - starttime
                e = {}
                e["minutes"], e["seconds"] = divmod(elapsed.seconds, 60)
                e["seconds"] = str(e["seconds"]).zfill(2)


                # Calculates steps achieved per time duration
                steps_per_second = step / elapsed.total_seconds()
                if step == 0:
                    average_iteration_tries = 1
                else:
                    average_iteration_tries = total_iteration_tries / step
                time_elapsed = datetime.timedelta( seconds=( (self.SimulationParameters.timesteps - step) / steps_per_second) )
                d = {}
                d["minutes"], d["seconds"] = divmod(time_elapsed.seconds, 60)
                d["seconds"] = str(d["seconds"]).zfill(2)

                simulation_status = "Simulating... {:.1f}% ".format((step/self.SimulationParameters.timesteps)*100)
                simulation_status += "| Total Time: {minutes}:{seconds} min ".format(**e)
                simulation_status += "| Speed: {:.0f} step/s ".format(steps_per_second)
                simulation_status += "| Time Left: {minutes}:{seconds} min".format(**d)
                simulation_status += "| Avg. iterations {:.1f}".format(average_iteration_tries)
                print(simulation_status)

        if len(all_result_lines) != self.SimulationParameters.timesteps:
            raise Exception("not all lines were generated")
        # npr = np.concatenate(all_result_lines, axis=0)
        columNames = []
        entry: cp.ComponentOutput
        np_results = np.array(all_result_lines)
        for index, entry in enumerate(self.all_outputs):
            column_name = entry.get_pretty_name()
            columNames.append(column_name)
            logging.debug("Output column: " + column_name)
            self.all_outputs[index].Results = np_results[:, index]
        self.results = pd.DataFrame(data=all_result_lines, columns=columNames)
        index = pd.date_range("2021-01-01 00:00:00", periods=len(self.results), freq="T")
        self.results.index = index
        end_counter = time.perf_counter()
        self.execution_time = end_counter - start_counter
        simulation_time = "Simulation took {:4.0f}s".format(self.execution_time)

        self.get_std_results()

        time_correction_factor = 1/self.SimulationParameters.seconds_per_timestep
        ppdt = pp.PostProcessingDataTransfer(
            time_correction_factor = time_correction_factor,
            directory_path = self.dirpath,
            results = self.results,
            all_outputs = self.all_outputs,
            simulation_parameters = self.SimulationParameters,
            wrapped_components = self.WrappedComponents,
            story = self.report.story,
            mode = 1,
            setup_function = self.setup_function,
            execution_time = self.execution_time,
            results_monthly = self.results_m,
        )

        #to_be_pickle = {"report": self.report,
        #                "directory_path": self.dirpath,
        #                "all_outputs": self.all_outputs,
        #                "results": self.results}

        # Perform postprocessing


        #with open(os.path.join(self.dirpath, "data.pkl"), "wb") as output:
         #   pickle.dump(ppdt, output, pickle.HIGHEST_PROTOCOL)


        my_post_processor = pp.PostProcessor(ppdt=ppdt)
        my_post_processor.run()

    def get_std_results(self):
        pd_timeline = pd.date_range(start=self.SimulationParameters.start_date,
                                    end=self.SimulationParameters.end_date,
                                    freq='{}S'.format(self.SimulationParameters.seconds_per_timestep))[:-1]
        n_columns = self.results.shape[1]
        df = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(self.results.values[:, i_column], index=pd_timeline, columns=[self.results.columns[i_column]])
            if 'Temperature' in self.results.columns[i_column] or 'Percent' in self.results.columns[i_column]:
                temp_df = temp_df.resample('H').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('H').sum()
            df[temp_df.columns[0]] = temp_df.values[:, 0]
            df.index = temp_df.index

        self.results.index = pd_timeline
        self.results_std = df

        dfm = pd.DataFrame()
        for i_column in range(n_columns):
            temp_df = pd.DataFrame(self.results.values[:, i_column], index=pd_timeline, columns=[self.results.columns[i_column]])
            if 'Temperature' in self.results.columns[i_column] or 'Percent' in self.results.columns[i_column]:
                temp_df = temp_df.resample('M').interpolate(method='linear')
            else:
                temp_df = temp_df.resample('M').sum()
            dfm[temp_df.columns[0]] = temp_df.values[:, 0]
            dfm.index = temp_df.index

        self.results_m = dfm















