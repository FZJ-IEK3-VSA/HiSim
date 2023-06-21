from typing import List
import pandas as pd
import os

from hisim import loadtypes as lt
from hisim import utils
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
from dataclasses import dataclass
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class CSVLoaderConfig(cp.ConfigBase):
    component_name: str
    csv_filename: str
    column: int
    loadtype: lt.LoadTypes
    unit: lt.Units
    column_name: str
    sep: str
    decimal: str
    multiplier: float

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return CSVLoader.get_full_classname()


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

    def __init__(
        self, config: CSVLoaderConfig, my_simulation_parameters: SimulationParameters
    ):
        self.csvconfig = config
        super().__init__(
            name=self.csvconfig.component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.output1: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Output1,
            self.csvconfig.loadtype,
            self.csvconfig.unit,
        )
        self.output1.display_name = self.csvconfig.column_name
        self.multiplier = self.csvconfig.multiplier

        # ? self.column = column
        df = pd.read_csv(
            os.path.join(utils.HISIMPATH["inputs"], self.csvconfig.csv_filename),
            sep=self.csvconfig.sep,
            decimal=self.csvconfig.decimal,
        )
        if self.csvconfig.column >= len(df.columns):
            raise RuntimeError(
                f"Invalid column number for the csv file: {self.csvconfig.column}. Found {len(df.columns)} columns."
            )
        dfcolumn = df.iloc[:, [self.csvconfig.column]]
        self.column_name = self.csvconfig.column_name
        if len(dfcolumn) < self.my_simulation_parameters.timesteps:
            raise Exception(
                "Timesteps: "
                + str(self.my_simulation_parameters.timesteps)
                + " vs. Lines in CSV "
                + self.csvconfig.csv_filename
                + ": "
                + str(len(self.column_name))
            )

        self.column = dfcolumn.to_numpy(dtype=float)
        self.values: List[float] = []

    def i_restore_state(self) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        stsv.set_output_value(
            self.output1, float(self.column[timestep]) * self.multiplier
        )

    def i_save_state(self) -> None:
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass
