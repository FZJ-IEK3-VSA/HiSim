"""The twin gates (``assemblies_spec.md`` §13 steps 4 and 5, D9): every composed file is its twin.

Each gate is a row of :data:`~hisim.energy_system.assemblies.twins.COMPOSED_TWINS`: a composed file in
``energy_systems/`` — the site plus imports of ``energy_systems/assemblies/`` — built and renamed to the twin's
names, which must equal the recorded twin of the Python setup outside exactly G7 and the neutral swaps of its sequence
(:mod:`tests.assemblies.twin_gate`). A generator with a space-heating buffer has one: the twin evaluates the DHW
cylinder before the buffer, the composed file the buffer before the cylinder, and the two read nothing from each
other. District heating and direct electric heating have no buffer, so their gates have no swap. The heat pump writes
its controllers in the twin's order, so the dry run's second swap does not arise; a fuel meter needs no swap, its
import is numbered between the heat distribution and the grid, where the twin evaluates it.
Both files of a gate then run for one day, and every result column and KPI is equal.
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Tuple

import pytest
import yaml

from hisim.components.simple_water_storage import SimpleDHWStorage, SimpleHotWaterStorage
from hisim.energy_system.assemblies.twins import COMPOSED_TWINS, ComposedTwin
from tests.assemblies.twin_gate import (
    ENERGY_SYSTEMS,
    Run,
    TwinGate,
    check_columns,
    check_components,
    check_kpis,
    check_sequence,
    run_both,
)


class BufferSwap:
    """Finds the neutral swap of a household twin: its DHW cylinder and its space-heating buffer, by their classes.

    The twin evaluates the cylinder before the buffer, the composed file the buffer before the cylinder; the two read
    nothing from each other, so the pair is a neutral swap. A twin without a buffer (district heating, direct electric
    heating) has none. The members are found by the class each twin component names, not by their names, so a twin
    calling its buffer differently still gets its swap. Example: the oil twin has ``DHWStorage``
    (``SimpleDHWStorage``) and ``SimpleHotWaterStorage`` (``SimpleHotWaterStorage``), which gives
    ``(("DHWStorage", "SimpleHotWaterStorage"),)``.
    """

    #: The DHW cylinder's class, which every household twin has.
    CYLINDER_CLASS: ClassVar[type] = SimpleDHWStorage
    #: The space-heating buffer's class, which only a generator with a buffer has.
    BUFFER_CLASS: ClassVar[type] = SimpleHotWaterStorage

    @staticmethod
    def class_path(component_class: type) -> str:
        """The dotted path a twin writes under ``class`` for a component class."""
        return f"{component_class.__module__}.{component_class.__qualname__}"

    @classmethod
    def of(cls, entry: ComposedTwin) -> Tuple[Tuple[str, str], ...]:
        """The neutral swaps of one twin: ``((cylinder, buffer),)`` when it has a buffer, else none.

        Args:
            entry: The twin's row of the table.

        Returns:
            The swaps, as pairs of twin names.

        Raises:
            AssertionError: When the twin has a buffer but not exactly one cylinder, or more than one buffer, for
                which the one swap is not determined.
        """
        components = yaml.safe_load((ENERGY_SYSTEMS / entry.twin).read_text(encoding="utf-8"))["components"]
        names_by_class: Dict[str, List[str]] = {}
        for name, component in components.items():
            names_by_class.setdefault(component["class"], []).append(name)
        buffers = names_by_class.get(cls.class_path(cls.BUFFER_CLASS), [])
        if not buffers:
            return ()
        cylinders = names_by_class.get(cls.class_path(cls.CYLINDER_CLASS), [])
        assert len(buffers) == 1 and len(cylinders) == 1, (entry.twin, buffers, cylinders)
        return ((cylinders[0], buffers[0]),)


def gate(stem: str) -> TwinGate:
    """The gate of the composed twin ``stem`` of :data:`~hisim.energy_system.assemblies.twins.COMPOSED_TWINS`.

    Example: ``gate("household_oil_building_sizer")`` compares the oil boiler's composed file with its twin under the
    table's rename map, with G7 (the battery's one-element list) and the cylinder and the buffer as its one neutral
    swap; ``gate("household_district_heating_building_sizer")`` has G7 and no swap. Every composed file is the same
    household site around another generator, so G7 is common to all of them. The swap exists only where the generator
    has a buffer, a twin component of the buffer's class (:class:`BufferSwap`).

    Args:
        stem: The Python setup's stem, a key of the table.

    Returns:
        The gate.
    """
    entry = COMPOSED_TWINS[stem]
    return TwinGate(
        entry,
        neutral_swaps=BufferSwap.of(entry),
        g7_component="Battery",
        g7_line={"pv_peak_power_in_watt": ["PVSystem.pv_peak_power_in_watt"]},
    )


@pytest.fixture(scope="module", name="twin_gate", params=sorted(COMPOSED_TWINS))
def fixture_twin_gate(request: pytest.FixtureRequest) -> TwinGate:
    """One gate per row of the table; the tests of one gate run next to each other."""
    return gate(request.param)


@pytest.fixture(scope="module", name="runs")
def fixture_runs(twin_gate: TwinGate, tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Run]:
    """Both files of the gate run for one day, each in its own result directory; once per gate."""
    return run_both(twin_gate, tmp_path_factory.mktemp(twin_gate.entry.stem))


@pytest.mark.assemblies
def test_the_composed_file_is_the_twin_outside_the_intended_differences(
    twin_gate: TwinGate, runs: Dict[str, Run]
) -> None:
    """Catches any class, preset, config line, input item, feed tag, weight or dispatch the assemblies get wrong."""
    check_components(twin_gate, runs)


@pytest.mark.assemblies
def test_the_sequence_is_the_twins_up_to_the_neutral_swap(twin_gate: TwinGate, runs: Dict[str, Run]) -> None:
    """Catches an evaluation order other than the twin's, or a swap of two components that read each other."""
    check_sequence(twin_gate, runs)


@pytest.mark.assemblies
def test_every_result_column_of_one_day_is_the_twins(runs: Dict[str, Run]) -> None:
    """Catches a composed system that computes anything else than the twin, to the last bit."""
    check_columns(runs)


@pytest.mark.assemblies
def test_every_kpi_of_one_day_is_the_twins(runs: Dict[str, Run]) -> None:
    """Catches a KPI that differs from the twin's under the address rename."""
    check_kpis(runs)
