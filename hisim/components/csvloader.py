"""Csvloader module."""

# clean

import warnings
from pathlib import Path
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import numpy as np
import pandas as pd


from hisim import loadtypes as lt
from hisim import utils
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class CSVLoaderConfig(cp.ConfigBase):
    """Csvloader config class."""

    building_name: str
    name: str
    csv_filename: str
    column: int
    loadtype: lt.LoadTypes
    unit: lt.Units
    column_name: str
    sep: str
    decimal: str
    multiplier: float
    output_description: str

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the base class."""
        return CSVLoader.get_full_classname()


class CSVLoader(cp.Component):
    r"""Csvloader class.

    Class component loads CSV file containing some
    load profile relevant to the applied setup
    function.

    Parameters
    ----------
    name: str
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

    Attributes
    ----------
    column_values: np.ndarray
        The loaded profile values as a float array, indexed by the
        simulation timestep. Read in :meth:`i_simulate` as
        ``self.column_values[timestep] * self.multiplier``.
    column_name: str
        Name of the column the profile was read from (mirrors
        ``CSVLoaderConfig.column_name``).
    multiplier: float
        Multiplication factor applied to every value in
        :attr:`column_values` when producing the output (mirrors
        ``CSVLoaderConfig.multiplier``).

    Notes
    -----
    ``column_values`` was previously named ``self.column``. That bare
    name was misleading because it collided with
    ``self.csvconfig.column`` (an ``int`` column index) -- see issue
    #758. A repo-wide audit (``grep -rn "\.column\b" --include="*.py"``
    excluding ``csvconfig.column``, ``.columns``, and ``column_values``)
    found no remaining reads of ``.column`` on :class:`CSVLoader`
    instances, so the rename is safe in-tree. A deprecated ``column``
    property is kept as a backward-compatible alias for any downstream
    code that may still introspect component state.

    """

    Output1: str = "CSV Profile"

    def __init__(
        self,
        config: CSVLoaderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
        inputs_dir: Path | None = None,
        dataframe: pd.DataFrame | None = None,
    ) -> None:
        """Initialize the class.

        Parameters
        ----------
        config:
            Configuration of the CSV loader.
        my_simulation_parameters:
            Simulation parameters used by the setup function.
        my_display_config:
            Display configuration for the component.
        inputs_dir:
            Optional directory that holds the CSV input file. Defaults to
            ``Path(utils.HISIMPATH["inputs"])`` when ``dataframe`` is not
            supplied. Ignored when ``dataframe`` is given.
        dataframe:
            Optional pre-loaded :class:`pandas.DataFrame` used instead of
            reading the CSV file from disk. When provided, ``inputs_dir`` and
            ``self.csvconfig.csv_filename`` are ignored and no file system
            access is performed. This is the seam used by unit tests so that
            :class:`CSVLoader` can be constructed without a real CSV file.
        """
        self.csvconfig: CSVLoaderConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: CSVLoaderConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.output1_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.Output1,
            self.csvconfig.loadtype,
            self.csvconfig.unit,
            output_description="CSV loader output 1",
        )
        self.output1_channel.display_name = self.csvconfig.column_name
        self.multiplier: float = self.csvconfig.multiplier

        if dataframe is not None:
            loaded_dataframe = dataframe
        else:
            if inputs_dir is None:
                inputs_dir = Path(utils.HISIMPATH["inputs"])
            loaded_dataframe = self._load_dataframe(inputs_dir)
        if self.csvconfig.column >= len(loaded_dataframe.columns):
            raise RuntimeError(
                f"Invalid column number for the csv file: {self.csvconfig.column}. Found {len(loaded_dataframe.columns)} columns."
            )
        dfcolumn = loaded_dataframe.iloc[:, self.csvconfig.column]
        self.column_name: str = self.csvconfig.column_name
        if len(dfcolumn) < self.my_simulation_parameters.timesteps:
            raise ValueError(
                f"CSV \'{self.csvconfig.csv_filename}\' has {len(dfcolumn)} rows, "
                f"which is fewer than the {self.my_simulation_parameters.timesteps} "
                "simulation timesteps."
            )

        self.column_values: np.ndarray = dfcolumn.to_numpy(dtype=float)
        self.values: List[float] = []

    @property
    def column(self) -> np.ndarray:
        """Deprecated alias for :attr:`column_values`.

        The instance attribute previously named ``self.column`` held the
        loaded profile values as a :class:`numpy.ndarray` (indexed by
        timestep), which collided with ``self.csvconfig.column`` (an
        ``int`` column index). It was renamed to :attr:`column_values`
        for clarity (see issue #758). A repo-wide audit found no
        remaining references to ``.column`` on :class:`CSVLoader`
        instances, but this alias is kept as a backward-compatible shim
        for any downstream code that may still introspect component
        state.

        Returns
        -------
        np.ndarray
            The loaded profile values (same object as
            :attr:`column_values`).

        Warns
        -----
        DeprecationWarning
            Always, on every access. Use :attr:`column_values` instead.
        """
        warnings.warn(
            "CSVLoader.column is deprecated; use CSVLoader.column_values "
            "instead. The attribute was renamed because the bare name "
            "'column' was confused with the integer column index "
            "CSVLoaderConfig.column.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.column_values

    @staticmethod
    def _read_csv(config: CSVLoaderConfig, inputs_dir: Path) -> pd.DataFrame:
        """Read the CSV referenced by *config* from *inputs_dir*.

        Shared helper so the file-system read lives in exactly one place and
        can be reused by both :meth:`from_config_file` and
        :meth:`_load_dataframe` without duplicating the ``pandas.read_csv``
        arguments.
        """
        return pd.read_csv(
            inputs_dir / config.csv_filename,
            sep=config.sep,
            decimal=config.decimal,
        )

    @classmethod
    def from_config_file(
        cls,
        config: CSVLoaderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
        inputs_dir: Path | None = None,
    ) -> "CSVLoader":
        """Construct a :class:`CSVLoader` by reading its CSV file from disk.

        This is the thin I/O factory that keeps file-system access out of
        :meth:`__init__`: it reads the bytes referenced by *config* and then
        delegates to :meth:`__init__` with the already-loaded
        :class:`pandas.DataFrame`. Callers -- and in particular unit tests --
        that already hold a loaded dataframe can pass it straight to
        ``__init__`` via the ``dataframe`` argument and skip disk access
        entirely, making :class:`CSVLoader` cheaply constructible without a
        real CSV file.

        Parameters
        ----------
        config:
            Configuration of the CSV loader.
        my_simulation_parameters:
            Simulation parameters used by the setup function.
        my_display_config:
            Display configuration for the component.
        inputs_dir:
            Optional directory that holds the CSV input file. Defaults to
            ``Path(utils.HISIMPATH["inputs"])`` when not supplied.
        """
        if inputs_dir is None:
            inputs_dir = Path(utils.HISIMPATH["inputs"])
        dataframe = cls._read_csv(config, inputs_dir)
        return cls(
            config=config,
            my_simulation_parameters=my_simulation_parameters,
            my_display_config=my_display_config,
            dataframe=dataframe,
        )

    def _load_dataframe(self, inputs_dir: Path) -> pd.DataFrame:
        """Read the configured CSV file into a :class:`pandas.DataFrame`.

        Thin seam around :meth:`_read_csv` so the disk read can be replaced
        (for example in unit tests) without touching the file system. Kept for
        backward compatibility with :meth:`__init__`'s default read path.
        """
        return self._read_csv(self.csvconfig, inputs_dir)

    def i_restore_state(self) -> None:
        """Restores the state."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        stsv.set_output_value(self.output1_channel, float(self.column_values[timestep]) * self.multiplier)

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def write_to_report(self) -> List[str]:
        """Writes a report."""
        return self.csvconfig.get_string_dict()
