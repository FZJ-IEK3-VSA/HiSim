"""The assembly test harness (``assemblies_spec.md`` §9.4, D24): samples, partners, checks and the wrong mock.

The sampler is tested on an inline parameter box: the Latin hypercube puts one sample in each ``1/N``
stratum of every numeric dimension, draws each discrete value equally often (within one), is a
function of its seed, and splits an ``exactly_one_of`` into its branches. The monotone evaluation is
tested on synthetic series. The deliberately wrong mock under ``mock_assemblies/wrong`` must fail by
name; a port without a registered test partner refuses with the class it needs; a registry that
does not read is refused whole; the member contract names an unbounded output, a wrong unit and a
KPI the member does not report.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.energy_system.assemblies.model import MonotoneDirection
from hisim.energy_system.assemblies.parameters import ParameterChecks
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing import checks
from hisim.energy_system.assemblies.testing.checks import AssemblyCheckFailure, MemberContract, offending_pair
from hisim.energy_system.assemblies.testing.isolation import (
    SUBJECT,
    IsolationRun,
    IsolationRunError,
    isolation_document,
    run_isolation,
)
from hisim.energy_system.assemblies.testing.partners import (
    TestPartnerMissingError,
    TestPartnerRegistry,
    TestPartnerRegistryError,
)
from hisim.energy_system.assemblies.testing.samples import (
    Dimension,
    ParameterSpace,
    SamplerError,
    Tier,
    branches,
    deterministic_samples,
    hypercube_samples,
    range_value,
    sweep,
    sweeps,
)
from hisim.postprocessing.kpi_computation import tolerances
from scripts import golden_kpis
from tests.assemblies.helpers import MOCKS, Library, Mocks
from tests.assemblies.mock_components import MockHeater

#: A parameter box for the sampler: two numbers (one an integer), an enum, a boolean.
BOX = f"""
schema_version: 4
kind: assembly
name: sampled/box
description: A parameter box for the sampler.
parameters:
  power_in_watt: {{type: float, unit: WATT, default: 2000, range: {{min: 500, max: 6000}}, description: Power.}}
  share: {{type: float, unit: ANY, default: none, range: {{min: 0, max: 1}}, description: A share.}}
  count: {{type: int, unit: ANY, default: 2, range: {{min: 1, max: 5}}, description: A count.}}
  mode: {{type: enum, values: [a, b, c], default: a, description: A mode.}}
  boost: {{type: bool, default: false, description: A switch.}}
CONSTRAINTS
components:
  Heater:
    class: {MOCKS}.MockHeater
    preset: standard
    config:
      power_in_watt: {{$param: power_in_watt}}
"""


def box(tmp_path: Path, constraints: str = "") -> ParameterSpace:
    """The space of the inline box with a ``constraints:`` block (the library check is not its concern)."""
    library = Library(tmp_path)
    library.add("sampled/box", BOX.replace("CONSTRAINTS", constraints))
    return ParameterSpace(library.resolver().resolve("sampled/box", "test").model)


def mock(path: str, root: Path = Mocks.LIBRARY) -> Tuple[ResolvedAssembly, ParameterSpace]:
    """One mock assembly and its parameter space."""
    assembly = AssemblyResolver([root]).resolve(path, "test")
    return assembly, ParameterSpace(assembly.model)


@pytest.mark.base
def test_every_numeric_dimension_has_one_sample_per_stratum_and_discrete_values_are_even(tmp_path: Path) -> None:
    """With N samples each numeric dimension falls once into every 1/N stratum; an enum and a bool within one."""
    space = box(tmp_path)
    for size in (7, 10, 16):
        samples = hypercube_samples(space, size, 3)
        assert len(samples) == size
        powers = sorted(int(np.floor((sample.values["power_in_watt"] - 500) / 5500 * size)) for sample in samples)
        assert powers == list(range(size))
        assert all(isinstance(sample.values["count"], int) and 1 <= sample.values["count"] <= 5 for sample in samples)
        for name, values in (("mode", ("a", "b", "c")), ("boost", (True, False))):
            counts = [sum(1 for sample in samples if sample.values[name] == value) for value in values]
            assert max(counts) - min(counts) <= 1, (name, counts)


@pytest.mark.base
def test_the_same_seed_reproduces_the_sample_and_another_seed_does_not(tmp_path: Path) -> None:
    """The hypercube is a function of the seed."""
    space = box(tmp_path)
    first, again, other = ([sample.values for sample in hypercube_samples(space, 8, seed)] for seed in (3, 3, 4))
    assert first == again != other


@pytest.mark.base
def test_an_exactly_one_of_splits_the_box_into_its_branches(tmp_path: Path) -> None:
    """One hypercube per stated parameter; the other is fixed unstated, and every sample satisfies the constraint."""
    space = box(tmp_path, "constraints:\n  - {exactly_one_of: [power_in_watt, share]}")
    assert [dict(branch.stated) for branch in branches(space)] == [
        {"power_in_watt": True, "share": False},
        {"power_in_watt": False, "share": True},
    ]
    samples = hypercube_samples(space, 6, 1)
    assert len(samples) == 12 and not any(space.problems(sample.values) for sample in samples)
    assert sum(1 for sample in samples if sample.values["power_in_watt"] is None) == 6


@pytest.mark.base
def test_the_deterministic_samples_cover_boundaries_values_and_variants_within_the_constraints() -> None:
    """The heater's boundaries, values and options; the array's share at its minimum states it, so the power is none."""
    _, heater = mock("mock/electric_heater")
    origins = [origin for sample in deterministic_samples(heater) for origin in sample.origins]
    for origin in (
        "defaults",
        "min of power_in_watt",
        "max of set_temperature_in_celsius",
        "with_thermostat = False",
        "control = 'eco'",
        "variant thermostat: fitted",
        "variant thermostat: none",
    ):
        assert origin in origins, origin
    _, array = mock("mock/pv_array")
    (sample,) = [sample for sample in deterministic_samples(array) if "min of share_of_roof" in sample.origins]
    assert sample.values["share_of_roof"] == 0.0 and sample.values["power_in_watt"] is None
    assert ParameterChecks.is_stated(0.0) and not ParameterChecks.is_stated("AUTO")
    swept = sweeps(array, deterministic_samples(array), array.model.tests.monotone[0])  # type: ignore[union-attr]
    assert all(base.values["power_in_watt"] is not None for base, _ in swept)
    assert [point["power_in_watt"] for point in swept[0][1]] == pytest.approx([0.0, 20000 / 3, 40000 / 3, 20000.0])


@pytest.mark.base
@pytest.mark.parametrize(
    "values, direction, offending",
    [
        ([1.0, 2.0, 2.0, 3.0], MonotoneDirection.INCREASING, None),
        ([1.0, 2.0, 1.5, 3.0], MonotoneDirection.INCREASING, (1, 2)),
        ([1.0, 1.0 - 1e-12], MonotoneDirection.INCREASING, None),
        ([3.0, 2.0, 2.5], MonotoneDirection.DECREASING, (1, 2)),
        ([1.0, 1.001], MonotoneDirection.CONSTANT, (0, 1)),
        ([0.0, 1e-15, 0.0], MonotoneDirection.CONSTANT, None),
        ([0.0, 1e-6], MonotoneDirection.CONSTANT, (0, 1)),
        ([1e6, 1e6 + 1e-4, 1e6], MonotoneDirection.CONSTANT, None),
        ([1e6, 1e6 + 1.0], MonotoneDirection.CONSTANT, (0, 1)),
    ],
)
def test_the_monotone_evaluation_names_the_first_pair_moving_the_wrong_way(
    values: List[float], direction: MonotoneDirection, offending: Optional[Tuple[int, int]]
) -> None:
    """Within the gate's REL_TOL of the series' magnitude plus the floor; noise on a zero KPI is equal."""
    assert offending_pair(values, direction) == offending
    assert checks.REL_TOL is tolerances.REL_TOL and golden_kpis.REL_TOL is tolerances.REL_TOL


@pytest.mark.base
def test_the_wrong_mock_fails_by_name(tmp_path: Path) -> None:
    """A bounds band the output leaves and a monotone of the wrong sign fail, named; the rest of it holds."""
    wrong = Mocks.ROOT / "wrong"
    assembly, space = mock("mock/backwards_heater", wrong)
    registry = TestPartnerRegistry.from_directories([wrong])
    resolver = AssemblyResolver([wrong])
    (defaults, *_) = deterministic_samples(space)
    run = run_isolation(
        assembly, defaults.values, registry, resolver, tmp_path / "s000", "mock/backwards_heater sample s000"
    )
    tests = assembly.model.tests
    assert tests is not None
    checks.check_run(run)
    checks.check_member_contract(run, tests)
    checks.check_bounds(run, tests.bounds[1])
    with pytest.raises(AssemblyCheckFailure) as caught:
        checks.check_bounds(run, tests.bounds[0])
    assert str(caught.value) == (
        "mock/backwards_heater sample s000 bounds Heater.ThermalPower [WATT] in [0, 1000]: 24 of 96 steps leave the "
        "band; the first is step 0 with 2000; the column runs from 0 to 2000"
    )
    run.release()
    assert run.released and not run.directory.exists()
    with pytest.raises(
        IsolationRunError, match="mock/backwards_heater sample s000: the check 'finite .*' reads a run th"
    ):
        checks.check_finite(run)

    def run_point(index: int, values: Mapping[str, Any], label: str) -> IsolationRun:
        """One sweep point's run."""
        return run_isolation(assembly, values, registry, resolver, tmp_path / f"p{index}", label)

    with pytest.raises(AssemblyCheckFailure) as caught:
        checks.evaluate_monotone(run_point, space, defaults, tests.monotone[0])
    assert str(caught.value) == (
        "mock/backwards_heater sweep of power_in_watt from s000 monotone power_in_watt rises: Heater energy of Heater "
        "decreasing: power_in_watt 500.0 → 2333.333333333333 moves Heater energy 3 → 14, which is not decreasing"
    )
    assert not list(tmp_path.glob("p*")), "every sweep point's run is released, its directory deleted"


@pytest.mark.base
def test_the_isolation_system_partners_every_port_that_changes_what_the_assembly_computes() -> None:
    """A gas provider and a cylinder for the boiler, a consumer for the connection, a controller for the battery."""
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    for path, components, verbs in (
        ("mock/gas_boiler", ["GasMeter", "Occupancy", "Cylinder"], {"bind": {"dhw": "Cylinder"}}),
        ("mock/gas_connection", ["Occupancy", "GasCylinder", "GasBoiler"], {}),
        (
            "mock/electric_heater",
            ["Occupancy", "Tank", "Ems"],
            {"bind": {"tank_temperature": "Tank"}, "optional-bind": {"ems_modifier": "Ems"}},
        ),
        ("mock/home_battery", ["Weather", "PVArray", "EnergyManager"], {"bind": {"pv_power": "PVArray"}}),
        ("mock/electricity_grid", ["Occupancy"], {}),
    ):
        assembly, space = mock(path)
        document = isolation_document(assembly, space.defaults(), registry)
        assert list(document["components"]) == components, path
        entry = document["imports"][SUBJECT]
        assert {verb: entry[verb] for verb in ("bind", "optional-bind") if verb in entry} == verbs, path


@pytest.mark.base
def test_a_port_without_a_registered_test_partner_refuses_naming_the_class() -> None:
    """An empty registry: the array's weather port names MockWeather."""
    assembly, space = mock("mock/pv_array")
    with pytest.raises(
        TestPartnerMissingError,
        match="the port 'weather' of 'mock/pv_array' needs a test partner, a "
        "partner of the class MockWeather, and no test_partners.yaml serves it",
    ):
        isolation_document(assembly, space.defaults(), TestPartnerRegistry([], []))


@pytest.mark.base
@pytest.mark.parametrize(
    "text, message",
    [
        (
            "A: {serves: {partner: MockWeather}, component: W}\n  B: {serves: {partner: MockWeather}, component: W}",
            "a partner of the class MockWeather is served twice, by 'A' and by 'B'",
        ),
        ("A: {serves: {partner: MockWeather}, requires: [Nobody], component: W}", "requires Nobody, which is no"),
        (
            "A: {serves: {partner: MockWeather}, requires: [B], component: W}\n  B: {requires: [A], component: W}",
            "the test partners require each other in a cycle, A → B → A",
        ),
        ("A: {serves: {class: MockWeather}, component: W}", "write one of {partner: …}"),
        ("A: {serves: {carrier: steam}, component: W}", "'steam' is no energy carrier"),
        ("A: {serves: {partner: MockWeather}, component: {class: x.Y, bogus: 1}}", "does not read"),
    ],
)
def test_a_registry_that_does_not_read_is_refused_whole(tmp_path: Path, text: str, message: str) -> None:
    """A duplicate, an unknown requirement, a malformed serves, an unknown carrier, a site entry that does not read."""
    weather = f"{{class: {MOCKS}.MockWeather, preset: standard}}"
    (tmp_path / TestPartnerRegistry.FILENAME).write_text(f"partners:\n  {text.replace(': W}', f': {weather}}}')}\n")
    with pytest.raises(TestPartnerRegistryError, match=re.escape(message)):
        TestPartnerRegistry.from_directories([tmp_path])


@pytest.mark.base
def test_the_member_contract_names_unbounded_outputs_wrong_units_and_unreported_kpis(tmp_path: Path) -> None:
    """Outputs decide by unit and load type; an assembly with every fault fails its contract, each one named."""
    rule = MemberContract.carries_energy_or_temperature
    assert rule(lt.LoadTypes.ELECTRICITY, lt.Units.WATT) and rule(lt.LoadTypes.TEMPERATURE, lt.Units.ANY)
    assert not rule(lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP)
    library = Library(tmp_path)
    library.add(
        "broken/heater",
        f"""
        schema_version: 4
        kind: assembly
        name: broken/heater
        description: A heater.
        parameters:
          power_in_watt: {{type: float, unit: WATT, default: 2000, range: {{min: 500, max: 6000}}, description: P.}}
        components:
          Heater:
            class: {MOCKS}.MockHeater
            preset: standard
            config:
              power_in_watt: {{$param: power_in_watt}}
        tests:
          bounds:
            - {{output: Heater.ThermalPower, unit: KWH, min: 0}}
          monotone:
            - {{parameter: power_in_watt, kpi: Nope, member: Heater, direction: increasing}}
        """,
    )
    resolver = library.resolver()
    assembly = resolver.resolve("broken/heater", "test")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    run = run_isolation(assembly, ParameterSpace(assembly.model).defaults(), registry, resolver, tmp_path / "run", "x")
    assert assembly.model.tests is not None
    assert MemberContract.violations(run, assembly.model.tests) == [
        "the WATT output Heater.ElectricityInput (ELECTRICITY) has no bounds",
        "the bounds entry states KWH for Heater.ThermalPower, whose unit is WATT",
        "'Heater' reports no KPI 'Nope' (it reports: Heater energy)",
    ]


@pytest.mark.base
def test_finiteness_reads_numeric_columns_and_a_check_on_an_unfinished_run_names_why(tmp_path: Path) -> None:
    """A NaN in a number column is named, a column of text skipped; a run that raised fails every check reading it."""
    frame = pd.DataFrame({"Power": [1.0, float("nan"), 2.0], "State": ["on", "off", "on"]})
    with pytest.raises(AssemblyCheckFailure, match=re.escape("x finite every result column: Power is nan at step 1")):
        checks.check_finite(IsolationRun("x", "a/b", tmp_path, results=frame))
    broken = IsolationRun("x", "a/b", tmp_path, error=ValueError("boom"))
    with pytest.raises(
        AssemblyCheckFailure,
        match=re.escape("x energy balance the isolation run: the run did not finish (ValueError: boom)"),
    ):
        checks.check_energy_balance(broken)
    with pytest.raises(AssemblyCheckFailure, match=re.escape("x run the isolation run: ValueError: boom")):
        checks.check_run(broken)


@pytest.mark.base
def test_an_int_parameter_takes_the_nearest_integer_in_its_range_everywhere(tmp_path: Path) -> None:
    """Catches a truncated bound, a hypercube point or a sweep point leaving an int parameter's range."""
    assert [range_value(value, 1, 5, True) for value in (0.4, 1.5, 2.49, 3.67, 5.4)] == [1, 2, 2, 4, 5]
    assert range_value(7.0, 0.0, 6.0, False) == 6.0
    unstated = Library(tmp_path / "unstated")
    unstated.add("sampled/box", BOX.replace("CONSTRAINTS", "").replace("default: 2, range", "default: none, range"))
    assert ParameterSpace(unstated.resolver().resolve("sampled/box", "test").model).representative("count") == 1
    space = box(tmp_path)
    counts = [sample.values["count"] for sample in deterministic_samples(space) if "count" in " ".join(sample.origins)]
    assert counts == [1, 5]
    dimension = Dimension("count", (1.0, 5.0), integer=True)
    assert [dimension.value(unit, 10) for unit in (0.0, 0.19, 0.21, 0.99)] == [1, 1, 2, 5]
    (defaults,) = [sample for sample in deterministic_samples(space) if sample.origins[0] == "defaults"]
    points = sweep(space, defaults, "count")
    assert points is not None and [point["count"] for point in points] == [1, 2, 4, 5]
    assert defaults.tier == Tier.BASE and not defaults.nightly
    assert all(sample.tier == Tier.NIGHTLY and sample.nightly for sample in hypercube_samples(space, 4, 1))


@pytest.mark.base
@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"range": (2.0, 1.0)}, "the dimension 'x' runs from 2.0 down to 1.0"),
        ({"range": (1.0, 2.0), "choices": ("a",)}, "the dimension 'x' needs a range or choices, exactly one of them"),
        ({}, "the dimension 'x' needs a range or choices, exactly one of them"),
    ],
)
def test_a_dimension_is_a_rising_range_or_a_set_of_choices(arguments: Any, message: str) -> None:
    """Catches a hypercube dimension the sampler built backwards, or as both kinds at once."""
    with pytest.raises(SamplerError, match=re.escape(message)):
        Dimension("x", **arguments)


@pytest.mark.base
def test_the_contracts_collect_without_xdist() -> None:
    """Catches the xdist_group marker known only while pytest-xdist is loaded (--strict-markers)."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            str(Path(__file__).with_name("test_library_contracts.py")),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parents[2],
    )
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]
    assert re.search(r"^\d+ tests collected", completed.stdout, re.MULTILINE), completed.stdout[-2000:]


@pytest.mark.base
def test_a_column_a_bounds_entry_cannot_compare_fails_by_name(tmp_path: Path) -> None:
    """Catches a bounds entry passing on a column of text, or one holding a value that is not finite."""
    assembly, _ = mock("mock/electric_heater")
    declaration = assembly.model.tests.bounds[0]  # type: ignore[union-attr]

    class Output:  # pylint: disable=too-few-public-methods  # the one output the check looks up
        """The heater's output as the simulator lists it."""

        component_name, field_name = "subject-Heater", "ThermalPower"

        @staticmethod
        def get_pretty_name() -> str:
            """The result column's name."""
            return "Heater power"

    for column, problem in (
        (pd.Series(["1.0", "x", "2.0"], dtype=object), "the column Heater power holds object values, not numbers"),
        ([1.0, float("inf"), 2.0], "the column Heater power is inf at step 1 (1 steps not finite)"),
    ):
        run = IsolationRun(
            "x",
            "mock/electric_heater",
            tmp_path,
            results=pd.DataFrame({"Heater power": column}),
            outputs=[Output()],
            runtime={"Heater": "subject-Heater"},
        )
        with pytest.raises(
            AssemblyCheckFailure, match=re.escape(f"x bounds Heater.ThermalPower [WATT] in [0, 6000]: {problem}")
        ):
            checks.check_bounds(run, declaration)


@pytest.mark.base
def test_a_run_whose_component_raises_names_where_it_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches a failed run that does not say whether the assembly's member or the harness raised."""

    def _raise(*_: Any) -> None:
        raise RuntimeError("the heater broke")

    monkeypatch.setattr(MockHeater, "i_simulate", _raise)
    assembly, space = mock("mock/electric_heater")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    run = run_isolation(assembly, space.defaults(), registry, AssemblyResolver([Mocks.LIBRARY]), tmp_path / "r", "h s0")
    line = _raise.__code__.co_firstlineno + 1
    with pytest.raises(AssemblyCheckFailure) as caught:
        checks.check_run(run)
    assert (
        str(caught.value)
        == f"h s0 run the isolation run: RuntimeError: the heater broke (raised at test_harness.py:{line})"
    )


@pytest.mark.base
def test_a_check_reading_a_run_without_results_that_raised_nothing_is_a_harness_error(tmp_path: Path) -> None:
    """Catches a check passing vacuously on a run the harness left without results."""
    with pytest.raises(
        IsolationRunError,
        match=re.escape(
            "x: the check 'finite every result column' reads a run that has no results although it raised nothing"
        ),
    ):
        checks.check_finite(IsolationRun("x", "a/b", tmp_path))
