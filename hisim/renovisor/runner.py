"""In-process execution of the selected system setup (spec section 5).

This module is the simulation stage of the RenoVisor translator pipeline: it takes a resolved
``ModularHouseholdConfig`` and the chosen ``*_building_sizer`` setup file and drives one HiSim
simulation to completion in the current process. Scenario generation — selecting the setup and
building the config from the home inventory — happens upstream in
:mod:`hisim.renovisor.mapping`; result collection and REST upload happen downstream in
:mod:`hisim.renovisor.uploader`. Deriving a RenoVisor ``CalcResult`` from the outputs, and
therefore aggregating results across the ``base`` and ``measures`` scenarios, is explicitly out
of scope for v1 and left to the receiving server (see ``spec.md``).

Control flow for a single run (:func:`run_simulation`):

1. :func:`build_simulation_parameters` assembles a full-year :class:`SimulationParameters` from
   the spec defaults (year 2019, 900 s timestep) plus any :class:`SimulationOverrides`, pins
   ``result_directory`` so the building-sizer setups do not redirect via the
   ``ResultPathProviderSingleton``, and merges the override post-processing options on top of
   the defaults.
2. :func:`resolve_setup_path` locates the setup ``.py`` file under the repository's
   ``system_setups`` directory (requires a source checkout; raises :class:`FileNotFoundError`
   otherwise).
3. :func:`write_module_config` serializes the config to ``MODULE_CONFIG_FILENAME`` inside the
   result directory, creating the directory and parents if needed.
4. The runner callable — ``hisim_main.main`` by default, lazily imported to keep the heavy
   component library out of module load — is invoked with the resolved setup path, the
   simulation parameters and the module-config path. ``hisim_main.main`` instantiates the HiSim
   components defined by the setup, iterates every time step until each step converges, runs
   post-processing (KPIs, OPEX/CAPEX, CSV/JSON/PDF reports per the selected options) and writes
   everything into the result directory.
5. :func:`run_simulation` returns the runner's actual result directory as a :class:`Path`.

**Concurrency and isolation.** The module performs no internal concurrency: each call runs one
setup synchronously to completion. Concurrent runs are isolated by the caller writing each into
its own ``<jobId>_<variant>`` subdirectory (handled in :mod:`hisim.renovisor.__main__`),
because the module config and result files use fixed names and would otherwise overwrite each
other.

**Error handling.** :func:`resolve_setup_path` raises :class:`FileNotFoundError` for a missing
setup so the failure surfaces immediately rather than degrading to a wrong setup. The runner is
injected as a keyword so tests can stub it; an exception raised by the runner propagates
uncaught to the caller, which converts it into a failure report and exit code 3.

**Logging.** This module does not log directly; component instantiation, time-step execution
and post-processing diagnostics are emitted by ``hisim_main.main`` through HiSim's logger.

**Expected outputs.** Result files are written into the returned result directory; their exact
set depends on the selected :class:`PostProcessingOptions` (KPI JSON, OPEX/CAPEX,
building-sizer config, CSV exports, plots/PDF). The module config JSON
(``MODULE_CONFIG_FILENAME``) is always written alongside them.
"""

from pathlib import Path
from typing import Callable, List, Optional, Protocol, Tuple, runtime_checkable, cast

from hisim.building_sizer_utils.interface_configs.modular_household_config import ModularHouseholdConfig
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.renovisor.schema import SimulationOverrides
from hisim.simulationparameters import SimulationParameters


@runtime_checkable
class SimulationRunner(Protocol):
    """Protocol for the in-process simulation runner (e.g. ``hisim_main.main``).

    The ``__call__`` signature mirrors :func:`hisim.hisim_main.main` exactly
    (keyword names and ``Optional[...] = None`` defaults), so static checkers
    gain full visibility into the arguments the call site forwards while
    accepting the real implementation unchanged.
    """

    def __call__(
        self,
        path_to_module: str,
        my_simulation_parameters: Optional[SimulationParameters] = None,
        my_module_config: Optional[str] = None,
    ) -> str:
        """Run one simulation and return the path of its result directory."""


# Defaults per spec section 5; the year matches the Dublin NSRDB weather dataset.
DEFAULT_YEAR: int = 2019
DEFAULT_SECONDS_PER_TIMESTEP: int = 900
DEFAULT_POST_PROCESSING_OPTIONS: Tuple[PostProcessingOptions, ...] = (
    PostProcessingOptions.COMPUTE_KPIS,
    PostProcessingOptions.COMPUTE_OPEX,
    PostProcessingOptions.COMPUTE_CAPEX,
    PostProcessingOptions.WRITE_KPIS_TO_JSON,
    PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
)

MODULE_CONFIG_FILENAME: str = "renovisor_modular_household_config.json"


def build_simulation_parameters(overrides: SimulationOverrides, result_directory: Path) -> SimulationParameters:
    """Build full-year simulation parameters from the defaults plus *overrides*.

    Override post-processing options are added on top of the defaults, not replacing them.
    Pre-setting ``result_directory`` keeps the building-sizer setups from redirecting results
    via the ``ResultPathProviderSingleton``.
    """
    year = overrides.year if overrides.year is not None else DEFAULT_YEAR
    seconds_per_timestep = (
        overrides.seconds_per_timestep if overrides.seconds_per_timestep is not None else DEFAULT_SECONDS_PER_TIMESTEP
    )
    parameters = SimulationParameters.full_year(year=year, seconds_per_timestep=seconds_per_timestep)
    parameters.result_directory = str(result_directory)
    options: List[PostProcessingOptions] = list(DEFAULT_POST_PROCESSING_OPTIONS)
    for name in overrides.post_processing_options:
        option = PostProcessingOptions[name]
        if option not in options:
            options.append(option)
    parameters.post_processing_options = cast(List[int], options)
    return parameters


def resolve_setup_path(setup_filename: str) -> Path:
    """Locate a system setup file in the repository's ``system_setups`` directory.

    The building-sizer setups are not part of the installed package, so this requires HiSim
    to be installed from a source checkout (``pip install -e .``).
    """
    repository_root = Path(__file__).resolve().parent.parent.parent
    setup_path = repository_root / "system_setups" / setup_filename
    if not setup_path.is_file():
        raise FileNotFoundError(
            f"System setup '{setup_filename}' not found at {setup_path}. The RenoVisor translator needs a HiSim "
            "source checkout (pip install -e .) because the system setups are not shipped with the package."
        )
    return setup_path


def write_module_config(
    modular_household_config: ModularHouseholdConfig,
    result_directory: Path,
) -> Path:
    """Persist *modular_household_config* as JSON next to the results.

    Creates *result_directory* (including parents) if it does not yet exist and writes the
    serialized configuration to ``MODULE_CONFIG_FILENAME`` inside it. Returns the path to the
    written file so callers can forward it to the simulation runner.

    Args:
        modular_household_config: The configuration to serialize.
        result_directory: The directory the config file is written into.

    Returns:
        The path to the written module-config JSON file.
    """
    result_directory.mkdir(parents=True, exist_ok=True)
    config_path = result_directory / MODULE_CONFIG_FILENAME
    config_path.write_text(modular_household_config.to_json(), encoding="utf-8")  # type: ignore[attr-defined]
    return config_path


def run_simulation(
    setup_filename: str,
    modular_household_config: ModularHouseholdConfig,
    simulation_parameters: SimulationParameters,
    result_directory: Path,
    runner: Optional[SimulationRunner] = None,
    setup_path_resolver: Callable[[str], Path] = resolve_setup_path,
) -> Path:
    """Write the module config and run the setup in-process; return the actual result directory.

    Config persistence is delegated to :func:`write_module_config`; this function focuses on
    invoking the runner with the resolved setup path, simulation parameters and module-config
    path, and returning the runner's actual result directory.
    """
    if runner is None:
        # heavy import (pulls the full component library) kept out of module load
        from hisim import hisim_main  # pylint: disable=import-outside-toplevel

        runner = hisim_main.main

    config_path = write_module_config(modular_household_config, result_directory)

    actual_result_directory = runner(
        path_to_module=str(setup_path_resolver(setup_filename)),
        my_simulation_parameters=simulation_parameters,
        my_module_config=str(config_path),
    )
    return Path(actual_result_directory)
