"""Test for running main json execution in all possible ways.

JSON
├── 2 inputs: scenario.json + simulation.json
└── 3 inputs: scenario.json + simulation.json + delta.json
"""

from pathlib import Path
import argparse

import pytest

from hisim import hisim_main


REPO_ROOT = Path(__file__).resolve().parent.parent

SCENARIO_JSON = str(
    REPO_ROOT
    / "system_setups"
    / "household_gas_building_sizer.scenario.json"
)
SIMULATION_PARAMS_JSON = str(
    REPO_ROOT
    / "system_setups"
    / "2021_15minutely_noplots_buildingsizer.simulation.json"
)


# ---------------------------------------------------------------------------
# initialize_from_json
# ---------------------------------------------------------------------------
@pytest.mark.base
def test_initialize_from_json_without_delta():
    """Initialize from json without delta.json file."""
    simulator = hisim_main.initialize_from_json(
        scenario=SCENARIO_JSON,
        simulation_parameters=SIMULATION_PARAMS_JSON,
        path_to_module=SCENARIO_JSON,
        delta=None,
    )

    assert simulator is not None


# ---------------------------------------------------------------------------
# validate_args - JSON mode
# ---------------------------------------------------------------------------
@pytest.mark.base
def test_validate_json_arguments_without_delta():
    """Validate json arguments without delta."""
    args = argparse.Namespace(
        inputs=[
            SCENARIO_JSON,
            SIMULATION_PARAMS_JSON,
        ]
    )

    config = hisim_main.validate_args(args)

    assert config == {
        "mode": "json",
        "scenario": SCENARIO_JSON,
        "simulation": SIMULATION_PARAMS_JSON,
        "delta": None,
    }


@pytest.mark.base
def test_validate_json_arguments_with_delta():
    """Validate json arguments with delta.json file."""
    # Use a real delta JSON here once one is available.
    delta_json = REPO_ROOT / "path" / "to" / "delta.json"

    if not delta_json.is_file():
        pytest.skip("No delta JSON fixture available")

    args = argparse.Namespace(
        inputs=[
            SCENARIO_JSON,
            SIMULATION_PARAMS_JSON,
            delta_json,
        ]
    )

    config = hisim_main.validate_args(args)

    assert config == {
        "mode": "json",
        "scenario": SCENARIO_JSON,
        "simulation": SIMULATION_PARAMS_JSON,
        "delta": delta_json,
    }


@pytest.mark.base
def test_validate_json_arguments_requires_simulation_file():
    """Validate json arguments with only one argument."""
    args = argparse.Namespace(
        inputs=[SCENARIO_JSON]
    )

    with pytest.raises(ValueError, match="requires at least 2 files"):
        hisim_main.validate_args(args)


@pytest.mark.base
def test_validate_json_arguments_rejects_four_files():
    """Validate json arguments with four arguments."""
    args = argparse.Namespace(
        inputs=[
            SCENARIO_JSON,
            SIMULATION_PARAMS_JSON,
            "delta.json",
            "another.json",
        ]
    )

    with pytest.raises(ValueError, match="at most 3 files"):
        hisim_main.validate_args(args)
