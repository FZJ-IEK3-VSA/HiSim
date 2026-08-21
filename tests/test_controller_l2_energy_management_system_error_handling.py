"""Tests for error handling in the L2 energy management system controller.

Verifies that ``sort_source_weights_and_components`` raises a specific
``ValueError`` (not a bare ``Exception``) when a dynamic input is not connected
to a dynamic output, so callers can catch this wiring error distinctly.
"""

from __future__ import annotations

# clean

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.controller_l2_energy_management_system import (
    L2GenericEnergyManagementSystem,
)
from hisim.dynamic_component import DynamicConnectionInput, DynamicConnectionOutput
from hisim.config import ComponentID


def _make_ems() -> L2GenericEnergyManagementSystem:
    """Build an L2GenericEnergyManagementSystem without running the heavy __init__.

    ``sort_source_weights_and_components`` only reads ``my_component_inputs``,
    ``my_component_outputs``, and label attributes set on the instance, so
    bypassing ``__init__`` keeps the test fast and isolated.
    """
    ems: L2GenericEnergyManagementSystem = L2GenericEnergyManagementSystem.__new__(L2GenericEnergyManagementSystem)
    return ems


def _add_electrolyzer_input(ems: L2GenericEnergyManagementSystem, weight: int, label: str) -> None:
    """Add a single dynamic ELECTROLYZER input with the given source weight."""
    ems.my_component_inputs = [
        DynamicConnectionInput(
            source_component_class=label,
            source_component_field_name="ElectricityReal",
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_tags=[lt.ComponentType.ELECTROLYZER],
            source_weight=weight,
        )
    ]
    setattr(
        ems,
        label,
        cp.ComponentInput("EMS", "ElectricityReal", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True),
    )


def _add_electrolyzer_output(
    ems: L2GenericEnergyManagementSystem, weight: int, label: str, value: cp.ComponentOutput | None
) -> None:
    """Add a single dynamic ELECTRICITY_TARGET output with the given source weight.

    ``value`` is what ``getattr(ems, label)`` will resolve to — a connected
    ``ComponentOutput`` in the happy path, or ``None`` for the error path.
    """
    ems.my_component_outputs = [
        DynamicConnectionOutput(
            source_component_label=label,
            source_output_field_name="ElectricityTarget",
            source_tags=[lt.ComponentType.ELECTROLYZER, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=weight,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            source_component_class=None,
        )
    ]
    setattr(ems, label, value)


@pytest.mark.base
def test_sort_source_weights_raises_value_error_when_output_is_none() -> None:
    """A None dynamic output must raise ValueError, not a bare Exception."""
    ems = _make_ems()
    _add_electrolyzer_input(ems, weight=5, label="Input_Test_Electrolyzer")
    _add_electrolyzer_output(ems, weight=5, label="Output_Test_Electrolyzer", value=None)

    with pytest.raises(ValueError, match="Dynamic input is not connected to dynamic output"):
        ems.sort_source_weights_and_components()


@pytest.mark.base
def test_sort_source_weights_returns_outputs_when_connected() -> None:
    """A properly connected dynamic output must not raise."""
    ems = _make_ems()
    _add_electrolyzer_input(ems, weight=5, label="Input_Test_Electrolyzer")
    output = cp.ComponentOutput(
        "Electrolyzer",
        "ElectricityTarget",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID("Electrolyzer"),
    )
    _add_electrolyzer_output(ems, weight=5, label="Output_Test_Electrolyzer", value=output)

    result = ems.sort_source_weights_and_components()

    # The third element of the returned tuple is the sorted outputs list.
    outputs_sorted = result[2]
    assert output in outputs_sorted
