"""Test for running main python execution in all possible ways.

Python
├── 1 input: module.py
├── 2 inputs: module.py + module_config
├── 2 inputs: module.py + simulation_params.json
└── 3 inputs: module.py + module_config + simulation_params.json
"""

from pathlib import Path
import argparse
import time

import numpy
import pytest

from hisim import hisim_main
from hisim.simulationparameters import SimulationParameters


REPO_ROOT = Path(__file__).resolve().parent.parent

PYTHON_SETUP = str(
    REPO_ROOT / "system_setups" / "household_gas_building_sizer.py"
)
MODULE_CONFIG = str(
    REPO_ROOT
    / "hisim"
    / "building_sizer_utils"
    / "interface_configs"
    / "example_modular_household_config.json"
)
SIMULATION_PARAMS_JSON = str(
    REPO_ROOT
    / "system_setups"
    / "2021_15minutely_noplots_buildingsizer.simulation.json"
)
SIMULATION_PARAMS_OBJECT = SimulationParameters.one_day_only(2021, 60)


# ---------------------------------------------------------------------------
# initialize_from_python
# ---------------------------------------------------------------------------
@pytest.mark.base
def test_initialize_from_python_without_optional_arguments():
    """Initialize from python with only python module."""
    simulator = hisim_main.initialize_from_python(PYTHON_SETUP)

    assert simulator is not None


@pytest.mark.base
def test_the_scenario_name_and_the_description_reach_post_processing(tmp_path):
    """The run's two pieces of metadata travel on the simulator, not through a global.

    The scenario name is written by the setup function — the building-sizer setups hash their
    configuration into one — and the description by this entry point, from the first line of the
    setup module's docstring. Post-processing reads both off the transfer object: the scenario
    name becomes the pyam "scenario" column, the description lands in ``scenario.json``. Until
    2026-09-12 both went through the process-global ``SingletonSimRepository``, where a second
    simulation in the same process inherited the first one's name.
    """
    parameters = SimulationParameters.one_day_only(2021, 60)
    parameters.result_directory = str(tmp_path / "results")
    simulator = hisim_main.initialize_from_python(PYTHON_SETUP, my_simulation_parameters=parameters)

    assert simulator.description == "Basic household new system setup."
    assert simulator.scenario_name, "the setup function named no scenario"

    empty_line = numpy.zeros(len(simulator.all_outputs))
    ppdt = simulator.prepare_post_processing(
        all_result_lines=[empty_line] * parameters.timesteps,
        start_counter=time.perf_counter(),
    )

    assert ppdt.scenario_name == simulator.scenario_name
    assert ppdt.description == "Basic household new system setup."


@pytest.mark.base
def test_initialize_from_python_with_module_config():
    """Initialize from python with python module and module config."""
    simulator = hisim_main.initialize_from_python(
        PYTHON_SETUP,
        my_module_config=MODULE_CONFIG,
    )

    assert simulator is not None


@pytest.mark.base
def test_initialize_from_python_with_simulation_parameters():
    """Initialize from python with python module and simulation parameters object."""
    simulator = hisim_main.initialize_from_python(
        PYTHON_SETUP,
        my_simulation_parameters=SIMULATION_PARAMS_OBJECT,
    )

    assert simulator.get_simulation_parameters() is SIMULATION_PARAMS_OBJECT


@pytest.mark.base
def test_initialize_from_python_with_simulation_parameters_json():
    """Initialize from python with python module and simulation parameters json."""
    simulator = hisim_main.initialize_from_python(
        PYTHON_SETUP,
        my_simulation_parameters=SIMULATION_PARAMS_JSON,
    )

    simulation_parameters = simulator.get_simulation_parameters()

    assert simulation_parameters.start_date is not None
    assert simulation_parameters.end_date is not None


@pytest.mark.base
def test_initialize_from_python_with_all_inputs():
    """Initialize from python with all input arguments."""
    simulator = hisim_main.initialize_from_python(
        path_to_module=PYTHON_SETUP,
        my_simulation_parameters=SIMULATION_PARAMS_JSON,
        my_module_config=MODULE_CONFIG,
    )

    assert simulator is not None


@pytest.mark.base
def test_initialize_from_python_rejects_invalid_simulation_parameters():
    """Initialize from python with invalid argument."""
    with pytest.raises(TypeError, match="not recognized"):
        hisim_main.initialize_from_python(
            PYTHON_SETUP,
            my_simulation_parameters=123,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# validate_args - Python mode
# ---------------------------------------------------------------------------
@pytest.mark.base
@pytest.mark.parametrize(
    "inputs, expected_config",
    [
        (
            [PYTHON_SETUP],
            {
                "mode": "python",
                "module_file": PYTHON_SETUP,
                "module_config": None,
                "my_simulation_parameters": None,
            },
        ),
        (
            [PYTHON_SETUP, MODULE_CONFIG],
            {
                "mode": "python",
                "module_file": PYTHON_SETUP,
                "module_config": MODULE_CONFIG,
                "my_simulation_parameters": None,
            },
        ),
        (
            [PYTHON_SETUP, MODULE_CONFIG, SIMULATION_PARAMS_JSON],
            {
                "mode": "python",
                "module_file": PYTHON_SETUP,
                "module_config": MODULE_CONFIG,
                "my_simulation_parameters": SIMULATION_PARAMS_JSON,
            },
        ),
    ],
)
def test_validate_python_arguments(inputs, expected_config):
    """Validate python arguments."""
    args = argparse.Namespace(inputs=inputs)

    assert hisim_main.validate_args(args) == expected_config


def test_validate_python_arguments_rejects_four_files():
    """Validate four arguments rejection."""
    args = argparse.Namespace(
        inputs=[
            PYTHON_SETUP,
            MODULE_CONFIG,
            SIMULATION_PARAMS_JSON,
            "another.json",
        ]
    )

    with pytest.raises(ValueError, match="at most 3 arguments"):
        hisim_main.validate_args(args)
