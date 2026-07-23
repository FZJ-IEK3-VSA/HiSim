"""In-process execution of the selected system setup (spec section 5).

Builds the :class:`SimulationParameters` from the defaults plus any overrides, writes the
generated ``ModularHouseholdConfig`` next to the results, and runs the setup through
``hisim_main.main`` — the same code path as ``python hisim_main.py <setup.py> <config.json>``.
"""

from pathlib import Path
from typing import List, cast

from hisim.building_sizer_utils.interface_configs.modular_household_config import ModularHouseholdConfig
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.renovisor.schema import SimulationOverrides
from hisim.simulationparameters import SimulationParameters

# Defaults per spec section 5; the year matches the Dublin NSRDB weather dataset.
DEFAULT_YEAR = 2019
DEFAULT_SECONDS_PER_TIMESTEP = 900
DEFAULT_POST_PROCESSING_OPTIONS = (
    PostProcessingOptions.COMPUTE_KPIS,
    PostProcessingOptions.COMPUTE_OPEX,
    PostProcessingOptions.COMPUTE_CAPEX,
    PostProcessingOptions.WRITE_KPIS_TO_JSON,
    PostProcessingOptions.WRITE_KPIS_TO_JSON_FOR_BUILDING_SIZER,
)

MODULE_CONFIG_FILENAME = "renovisor_modular_household_config.json"


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


def run_simulation(
    setup_filename: str,
    modular_household_config: ModularHouseholdConfig,
    simulation_parameters: SimulationParameters,
    result_directory: Path,
) -> Path:
    """Write the module config and run the setup in-process; return the actual result directory."""
    # heavy import (pulls the full component library) kept out of module load
    from hisim import hisim_main  # pylint: disable=import-outside-toplevel

    result_directory.mkdir(parents=True, exist_ok=True)
    config_path = result_directory / MODULE_CONFIG_FILENAME
    config_path.write_text(modular_household_config.to_json(), encoding="utf-8")  # type: ignore[attr-defined]

    actual_result_directory = hisim_main.main(
        path_to_module=str(resolve_setup_path(setup_filename)),
        my_simulation_parameters=simulation_parameters,
        my_module_config=str(config_path),
    )
    return Path(actual_result_directory)
