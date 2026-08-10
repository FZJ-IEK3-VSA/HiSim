"""Unit tests for which dynamic outputs :mod:`hisim.json_generator` exports.

A ``DynamicComponent`` can end up with two kinds of dynamic outputs: the ones its
own constructor creates (the EMS builds one per default connection) and the ones a
system setup adds afterwards via ``add_component_output``. Only the latter belong in
the scenario JSON -- the constructor makes its own again when the JSON is reloaded, so
exporting those would duplicate them, while dropping a setup-added one leaves the
scenario JSON with a connection pointing at an output that no longer exists.

The boundary between the two used to be a hard-coded output index in
``convert_component_to_json``. Removing a single default connection from the EMS
constructor shifted every index by one and silently pushed the first setup-added
output below that threshold, so it was no longer exported. These tests pin the
dynamic replacement (``number_of_outputs_created_during_construction``) instead, and
run against the real EMS so that changing its constructor cannot re-open the gap
without a test failing.

These are construction-only tests: no simulation is run and no input data is read.
"""

# clean

import hisim.loadtypes as lt
from hisim.components.controller_l2_energy_management_system import (
    EMSConfig,
    L2GenericEnergyManagementSystem,
)
from hisim.json_generator import convert_component_to_json
from hisim.simulationparameters import SimulationParameters

import pytest


def _make_ems() -> L2GenericEnergyManagementSystem:
    """Construct an EMS from its default config, as a system setup would."""
    return L2GenericEnergyManagementSystem(
        my_simulation_parameters=SimulationParameters.one_week_only(year=2021, seconds_per_timestep=60 * 60),
        config=EMSConfig.get_default_config_ems(),
    )


def _add_setup_output(ems: L2GenericEnergyManagementSystem) -> str:
    """Add one dynamic output the way a system setup does and return its field name."""
    return ems.add_component_output(
        source_output_name="LoadingPowerInputForBattery_",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=6,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    ).field_name


@pytest.mark.base
def test_ems_records_its_own_output_count() -> None:
    """The EMS records the output count its constructor left behind.

    The recorded value is compared against ``len(ems.outputs)`` rather than a literal,
    so adding or removing a default connection keeps the test passing while a
    constructor that forgets to call ``mark_end_of_construction()`` fails it.
    """
    ems = _make_ems()

    assert ems.number_of_outputs_created_during_construction == len(ems.outputs)
    assert ems.number_of_outputs_created_during_construction > 0


@pytest.mark.base
def test_constructor_made_outputs_are_not_exported() -> None:
    """An untouched EMS exports no dynamic outputs at all.

    Every output it has at this point is recreated by the constructor on reload, so
    exporting any of them would duplicate it.
    """
    ems = _make_ems()

    _, _, outs = convert_component_to_json(config=ems.config, component=ems)

    assert outs == []


@pytest.mark.base
def test_setup_added_output_is_exported() -> None:
    """An output added after construction is exported, and only that one.

    This is the case the hard-coded threshold used to drop: the export stayed empty
    while the scenario JSON still contained a connection referencing the missing
    output, which made the reloaded simulator fail to connect.
    """
    ems = _make_ems()
    field_name = _add_setup_output(ems)

    _, _, outs = convert_component_to_json(config=ems.config, component=ems)

    assert len(outs) == 1
    assert outs[0]["source_output_name"] == "LoadingPowerInputForBattery_"
    assert outs[0]["source_weight"] == 6
    # The exported name carries no output index; the index is re-derived on reload and
    # only matches again because the constructor-made outputs were left out above.
    assert field_name.endswith(f"Output{len(ems.outputs)}")


@pytest.mark.base
def test_export_tracks_a_shrinking_constructor() -> None:
    """Fewer constructor outputs must not push a setup-added output out of the export.

    This simulates the regression directly: a constructor that creates one output less
    shifts the setup-added output down by one index. Since the boundary is recorded
    rather than hard-coded, the output is still exported.
    """
    ems = _make_ems()
    ems.outputs.pop()
    ems.my_component_outputs.pop()
    ems.mark_end_of_construction()
    _add_setup_output(ems)

    _, _, outs = convert_component_to_json(config=ems.config, component=ems)

    assert len(outs) == 1
    assert outs[0]["source_output_name"] == "LoadingPowerInputForBattery_"
