"""CSV loader component for HiSim simulations.

This module provides the :class:`CSVLoader` component, which reads a load
profile from a CSV file and exposes it as a time-indexed output channel for
HiSim simulations. The accompanying :class:`CSVLoaderConfig` dataclass
collects the file path, column index, separator, multiplier, and other
parameters needed to read a profile and feed it into a simulation as a
single output channel.
"""

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
from hisim.config import ConfigBase, ComponentID, DisplayConfig


@dataclass_json
@dataclass
class CSVLoaderConfig(ConfigBase):
    """Configuration for the :class:`CSVLoader` component.

    Args:
        component_id: Structured identity (name, building, unit) of this loader.
        name: Display name of the load profile.
        csv_filename: Filename of the CSV file containing the profile data.
        column: Zero-based index of the column holding the profile values.
        loadtype: Physical load type of the data (e.g. electricity, heat).
        unit: Unit of the loaded data.
        column_name: Human-readable name of the profile column.
        sep: Column separator used in the CSV file.
        decimal: Decimal separator used in the CSV file.
        multiplier: Factor applied to every loaded value.
        output_description: Description text for the output channel.
    """

    component_id: ComponentID
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
    column_values_in_loaded_unit: np.ndarray
        The loaded profile values as a float array, indexed by the
        simulation timestep. The values carry the physical unit
        declared in :attr:`CSVLoaderConfig.unit`
        (``self.csvconfig.unit``); the ``_in_loaded_unit`` suffix
        signals that a unit applies even though it is not statically
        determinable from the attribute name. Read in
        :meth:`i_simulate` as
        ``self.column_values_in_loaded_unit[timestep] * self.multiplier``.
    column_name: str
        Name of the column the profile was read from (mirrors
        ``CSVLoaderConfig.column_name``).
    multiplier: float
        Multiplication factor applied to every value in
        :attr:`column_values_in_loaded_unit` when producing the output (mirrors
        ``CSVLoaderConfig.multiplier``).

    Notes
    -----
    ``column_values_in_loaded_unit`` was previously named
    ``self.column`` (issue #758) and then ``self.column_values``. The
    bare ``column`` name was misleading because it collided with
    ``self.csvconfig.column`` (an ``int`` column index), and
    ``column_values`` carried no unit suffix despite holding physical
    quantities (issue #1924). Because the unit is runtime-determined
    via :attr:`CSVLoaderConfig.unit`, the suffix ``_in_loaded_unit``
    is used instead of a fixed ``_in_<unit>`` suffix. A repo-wide
    audit (``grep -rn "\.column\b" --include="*.py"`` excluding
    ``csvconfig.column``, ``.columns``, and ``column_values``) found
    no remaining reads of ``.column`` on :class:`CSVLoader``
    instances, so the rename is safe in-tree. Deprecated ``column``
    and ``column_values`` properties are kept as backward-compatible
    aliases for any downstream code that may still introspect
    component state.

    """

    Output1: str = "CSV Profile"

    def __init__(
        self,
        config: CSVLoaderConfig,
        my_simulation_parameters: SimulationParameters,
        my_display_config: DisplayConfig = DisplayConfig(),
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
                f"CSV '{self.csvconfig.csv_filename}' has {len(dfcolumn)} rows, "
                f"which is fewer than the {self.my_simulation_parameters.timesteps} "
                "simulation timesteps."
            )

        self.column_values_in_loaded_unit: np.ndarray = dfcolumn.to_numpy(dtype=float)
        if not np.all(np.isfinite(self.column_values_in_loaded_unit)):
            bad_rows = np.where(~np.isfinite(self.column_values_in_loaded_unit))[0]
            raise ValueError(
                f"CSV '{self.csvconfig.csv_filename}' column "
                f"'{self.column_name}' contains non-finite values "
                f"(NaN/inf) at rows {bad_rows[:10].tolist()}"
            )
        self.values: List[float] = []

    @property
    def column(self) -> np.ndarray:
        """Deprecated alias for :attr:`column_values_in_loaded_unit`.

        The instance attribute previously named ``self.column`` held the
        loaded profile values as a :class:`numpy.ndarray` (indexed by
        timestep), which collided with ``self.csvconfig.column`` (an
        ``int`` column index). It was renamed to ``column_values``
        for clarity (see issue #758) and later to
        :attr:`column_values_in_loaded_unit` to carry its physical
        unit (see issue #1924). A repo-wide audit found no remaining
        references to ``.column`` on :class:`CSVLoader` instances,
        but this alias is kept as a backward-compatible shim for any
        downstream code that may still introspect component state.

        Returns
        -------
        np.ndarray
            The loaded profile values (same object as
            :attr:`column_values_in_loaded_unit`).

        Warns
        -----
        DeprecationWarning
            Always, on every access. Use :attr:`column_values_in_loaded_unit` instead.
        """
        warnings.warn(
            "CSVLoader.column is deprecated; use CSVLoader.column_values_in_loaded_unit "
            "instead. The attribute was renamed because the bare name "
            "'column' was confused with the integer column index "
            "CSVLoaderConfig.column.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.column_values_in_loaded_unit

    @property
    def column_values(self) -> np.ndarray:
        """Deprecated alias for :attr:`column_values_in_loaded_unit`.

        The attribute was renamed from ``column_values`` to
        :attr:`column_values_in_loaded_unit` to signal that the loaded
        profile values carry a physical unit (the one declared in
        :attr:`CSVLoaderConfig.unit`), which is not statically
        determinable from the attribute name alone (see issue #1924).
        This alias is kept as a backward-compatible shim for any
        downstream code that may still reference ``column_values``.

        Returns
        -------
        np.ndarray
            The loaded profile values (same object as
            :attr:`column_values_in_loaded_unit`).

        Warns
        -----
        DeprecationWarning
            Always, on every access. Use
            :attr:`column_values_in_loaded_unit` instead.
        """
        warnings.warn(
            "CSVLoader.column_values is deprecated; use "
            "CSVLoader.column_values_in_loaded_unit instead. The "
            "attribute was renamed to carry its physical unit, which "
            "is runtime-determined via CSVLoaderConfig.unit.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.column_values_in_loaded_unit

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
        my_display_config: DisplayConfig = DisplayConfig(),
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
        """No-op override of the component state-restore lifecycle hook.

        CSVLoader holds no mutable runtime state across timesteps, so
        restoring a previously saved state requires no action.
        """
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Write the profile value for *timestep* to the output channel.

        Looks up ``self.column_values_in_loaded_unit[timestep]``, multiplies it by
        ``self.multiplier``, and stores the result in *stsv* via
        ``self.output1_channel``.

        Args:
            timestep: Index of the current simulation timestep.
            stsv: Single-time-step values container to write the output into.
            force_convergence: Unused; accepted for interface compatibility
                with the component lifecycle.
        """
        stsv.set_output_value(self.output1_channel, float(self.column_values_in_loaded_unit[timestep]) * self.multiplier)

    def i_prepare_simulation(self) -> None:
        """No-op override of the pre-simulation preparation hook.

        All profile data is loaded in ``__init__``, so no additional
        preparation is needed before the simulation loop begins.
        """
        pass

    def i_save_state(self) -> None:
        """No-op override of the component state-save lifecycle hook.

        CSVLoader holds no mutable runtime state across timesteps, so
        there is nothing to snapshot.
        """
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """No-op override of the post-step consistency-check hook.

        Args:
            timestep: Index of the current simulation timestep.
            stsv: Single-time-step values container (unused).
        """
        pass

    def write_to_report(self) -> List[str]:
        """Return the loader configuration as a string dictionary for reports.

        Returns:
            The string dictionary produced by
            ``self.csvconfig.get_string_dict()``, suitable for inclusion in
            the simulation report.
        """
        return self.csvconfig.get_string_dict()
