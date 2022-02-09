from typing import List
import pandas as pd
import os

from hisim import loadtypes as lt
from hisim import utils
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
class CSVLoader(cp.Component):
    """
    Class component loads CSV file containing some
    load profile relevant to the applied setup
    function

    Parameters
    --------------------------
    component_name: str
        Name of load profile from CSV file
    csv_filename: str
        Name of CSV filename containing the load profile data
    column: int
        Column number where the load profile data is stored
        inside of the CSV file
    loadtype: LoadTypes,
        Load type corresponded to the data loaded
    unit: lt.Units
        Units of data loaded
    column_name: str
        Name of column where the load profile data is stored
        inside of the CSV File
    simulation_parameters: cp.SimulationParameters
        Simulation parameters used by the setup function
    sep: str
        Separator used CSV file
    decimal: str
        Decimal indicator used in the CSV file
    multiplier: float
        Multiplication factor, in case an amplification of
        the data is required
    """
    Output1: str = "CSV Profile"

    def __init__(self,
                 component_name: str,
                 csv_filename: str,
                 column: int,
                 loadtype: lt.LoadTypes,
                 unit: lt.Units,
                 column_name: str,
                 simulation_parameters: SimulationParameters,
                 sep: str = ";",
                 decimal: str = ".",
                 multiplier: float = 1):
        super().__init__(name=component_name)

        self.output1 : cp.ComponentOutput = self.add_output(self.ComponentName,
                                            self.Output1,
                                            loadtype,
                                            unit)
        self.output1.DisplayName = column_name
        self.multiplier = multiplier

        # ? self.column = column
        df = pd.read_csv(os.path.join(utils.HISIMPATH["inputs"], csv_filename), sep=sep, decimal=decimal) # type: ignore
        dfcolumn = df.iloc[:, [column]]
        self.column_name = column_name
        if len(dfcolumn) < simulation_parameters.timesteps:
            raise Exception("Timesteps: " + str(simulation_parameters.timesteps) + " vs. Lines in CSV " + csv_filename + ": " + str(len(self.column_name)))

        self.column = dfcolumn.to_numpy(dtype=float)
        self.values: List[float] = []

    def i_restore_state(self):
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, seconds_per_timestep: int, force_convergence: bool):
        stsv.set_output_value(self.output1, float(self.column[timestep]) * self.multiplier)

    def i_save_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass
