"""The assembly test harness (``assemblies_spec.md`` §9.4, D24): samples, partners, checks, refusals.

The sampler is tested on an inline parameter box: the Latin hypercube puts exactly one sample in
each ``1/N`` stratum of every numeric dimension, a constraint splits the box into its feasible
branches, a seed reproduces its sample, and a discrete dimension draws each value equally often
(within one). The monotone evaluation is tested on synthetic series. The deliberately wrong mock
under ``mock_assemblies/wrong`` must fail by name, an assembly the library check refuses must not run at
all, one whose constructed members break the member contract must have no declaration evaluated, and
a port without a registered test partner must refuse with the class it needs.
"""

import io
import json
import re
import subprocess
import sys
import textwrap
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

from hisim.cli import main
from hisim.energy_system.assemblies.model import MonotoneDirection
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing import checks
from hisim.energy_system.assemblies.testing.checks import MonotoneEvaluation
from hisim.energy_system.assemblies.testing.contract import MemberContract
from hisim import loadtypes as lt
from hisim.energy_system.assemblies.testing.errors import (
    HarnessUsageError,
    TestPartnerMissingError,
    TestPartnerRegistryError,
)
from hisim.energy_system.assemblies.testing.harness import (
    AssemblyHarness,
    library_paths,
    parse_shard,
    require_passed,
    shard_of,
)
from hisim.energy_system.assemblies.testing.isolation import SUBJECT, IsolationBuilder
from hisim.energy_system.assemblies.testing.partners import TestPartnerRegistry
from hisim.energy_system.assemblies.testing.report import AssemblyTestFailure, CheckKind
from hisim.energy_system.assemblies.parameters import ParameterChecks
from hisim.energy_system.assemblies.testing.samples import (
    DeterministicSamples,
    HypercubeSampler,
    ParameterSpace,
    SampleBook,
)
from hisim.postprocessing.kpi_computation import tolerances
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder
from scripts import golden_kpis
from tests.assemblies.helpers import Library, Mocks, mock_resolver

#: The mock library that must fail the harness.
WRONG = Mocks.ROOT / "wrong"

#: A parameter box for the sampler: two numbers (one an integer), an enum, a boolean.
BOX = """
schema_version: 4
kind: assembly
name: sampled/box
description: A parameter box for the sampler.
parameters:
  power_in_watt: {{type: float, unit: WATT, default: 2000, range: {{min: 500, max: 6000}}, description: Power.}}
  share: {{type: float, unit: ANY, default: none, range: {{min: 0, max: 1}}, description: A share.}}
  count: {{type: int, default: 2, range: {{min: 1, max: 5}}, description: A count.}}
  mode: {{type: enum, values: [a, b, c], default: a, description: A mode.}}
  boost: {{type: bool, default: false, description: A switch.}}
{constraints}
components:
  Heater:
    class: tests.assemblies.mock_components.MockHeater
    preset: standard
    config:
      power_in_watt: {{$param: power_in_watt}}
"""


def box(tmp_path: Path, constraints: str = "") -> ResolvedAssembly:
    """The inline box with the given ``constraints:`` block, resolved."""
    library = Library(tmp_path)
    library.add("sampled/box", BOX.format(constraints=constraints))
    return library.resolver(with_mocks=False).resolve("sampled/box", "test")


def sampler(assembly: ResolvedAssembly, size: int = 10, seed: int = 7) -> HypercubeSampler:
    """A hypercube sampler over one assembly."""
    return HypercubeSampler(ParameterSpace(assembly), size, seed)


# ------------------------------------------------------------------------------------- the sampler


@pytest.mark.base
def test_every_numeric_dimension_has_one_sample_per_stratum(tmp_path: Path) -> None:
    """With N samples, each numeric dimension's points fall one into each 1/N stratum of its range."""
    hypercube = sampler(box(tmp_path), size=10)
    branches = hypercube.branches()
    assert len(branches) == 1
    branch = branches[0]
    points = hypercube.unit_points(branch)
    values = hypercube.values(branch)

    assert points.shape == (10, 5)
    for column in range(points.shape[1]):
        assert sorted(np.floor(points[:, column] * 10).astype(int)) == list(range(10))
    powers = [(sample["power_in_watt"] - 500) / 5500 for sample in values]
    assert sorted(int(np.floor(unit * 10)) for unit in powers) == list(range(10))
    assert all(isinstance(sample["count"], int) and 1 <= sample["count"] <= 5 for sample in values)


@pytest.mark.base
def test_discrete_dimensions_are_covered_evenly(tmp_path: Path) -> None:
    """An enum of three values over ten samples: 3, 3 and 4 in some order; a boolean 5 and 5."""
    for size in (7, 10, 16):
        hypercube = sampler(box(tmp_path / str(size)), size=size)
        branches = hypercube.branches()
        assert len(branches) == 1
        branch = branches[0]
        values = hypercube.values(branch)
        modes = [sum(1 for sample in values if sample["mode"] == mode) for mode in ("a", "b", "c")]
        boosts = [sum(1 for sample in values if sample["boost"] is flag) for flag in (True, False)]
        assert sum(modes) == size and max(modes) - min(modes) <= 1
        assert sum(boosts) == size and max(boosts) - min(boosts) <= 1


@pytest.mark.base
def test_the_same_seed_reproduces_the_sample_and_another_seed_does_not(tmp_path: Path) -> None:
    """The hypercube is a function of the seed."""
    assembly = box(tmp_path)
    first = [sampler(assembly, seed=3).values(branch) for branch in sampler(assembly, seed=3).branches()]
    again = [sampler(assembly, seed=3).values(branch) for branch in sampler(assembly, seed=3).branches()]
    other = [sampler(assembly, seed=4).values(branch) for branch in sampler(assembly, seed=4).branches()]

    assert first == again
    assert first != other


@pytest.mark.base
@pytest.mark.parametrize(
    "constraint, expected",
    [
        ("exactly_one_of: [power_in_watt, share]", [{"power_in_watt": True, "share": False},
                                                    {"power_in_watt": False, "share": True}]),
        ("at_most_one_of: [power_in_watt, share]", [{"power_in_watt": True, "share": False},
                                                    {"power_in_watt": False, "share": True},
                                                    {"power_in_watt": False, "share": False}]),
        ("requires: {boost: [share]}", [{"share": True, "boost": True},
                                        {"share": True, "boost": False},
                                        {"share": False, "boost": False}]),
    ],
)
def test_a_constraint_splits_the_box_into_its_feasible_branches(
    tmp_path: Path, constraint: str, expected: List[dict]
) -> None:
    """One hypercube per branch; an unstated parameter is fixed, every sample satisfies every constraint."""
    assembly = box(tmp_path, f"constraints:\n  - {{{constraint}}}")
    hypercube = sampler(assembly, size=6)
    branches = hypercube.branches()

    assert [dict(branch.stated) for branch in branches] == expected
    space = ParameterSpace(assembly)
    book = SampleBook(space)
    hypercube.add_to(book)
    assert len(book.samples) == 6 * len(branches)
    for branch in branches:
        for values in hypercube.values(branch):
            assert not space.problems(values)
            for name, is_stated in branch.stated.items():
                assert ParameterChecks.is_stated(values[name]) == is_stated, (branch.label, name, values[name])


@pytest.mark.base
def test_the_deterministic_samples_cover_presets_boundaries_values_and_variants() -> None:
    """The electric heater: its presets, both ends of each range, each value, each thermostat option."""
    resolver = mock_resolver()
    book = SampleBook(ParameterSpace(resolver.resolve("mock/electric_heater", "test")))
    DeterministicSamples.add_to(book)
    origins = [origin for sample in book.samples for origin in sample.origins]

    for origin in (
        "defaults",
        "preset standard",
        "preset eco",
        "min of power_in_watt",
        "max of set_temperature_in_celsius",
        "with_thermostat = False",
        "control = 'eco'",
        "variant thermostat: fitted",
        "variant thermostat: none",
    ):
        assert origin in origins, origin
    eco = book.with_preset("eco")
    assert eco is not None and eco.values["set_temperature_in_celsius"] == 40.0


@pytest.mark.base
def test_a_boundary_of_an_alternative_moves_into_its_branch() -> None:
    """``share_of_roof`` at its minimum states it, so the array's power becomes ``none`` (exactly_one_of)."""
    resolver = mock_resolver()
    book = SampleBook(ParameterSpace(resolver.resolve("mock/pv_array", "test")))
    DeterministicSamples.add_to(book)

    (sample,) = [sample for sample in book.samples if "min of share_of_roof" in sample.origins]
    assert sample.values["share_of_roof"] == 0.0 and sample.values["power_in_watt"] is None


# ---------------------------------------------------------------------------------- the evaluation


@pytest.mark.base
def test_the_monotone_tolerance_is_the_golden_gates_relative_one() -> None:
    """One source: the gate's scripts and the harness read hisim's tolerance module."""
    assert checks.REL_TOL is tolerances.REL_TOL
    assert (golden_kpis.REL_TOL, golden_kpis.ABS_TOL) == (tolerances.REL_TOL, tolerances.ABS_TOL)


@pytest.mark.base
@pytest.mark.parametrize(
    "values, direction, offending",
    [
        ([1.0, 2.0, 2.0, 3.0], MonotoneDirection.INCREASING, None),
        ([1.0, 2.0, 1.5, 3.0], MonotoneDirection.INCREASING, (1, 2)),
        ([1.0, 1.0 - 1e-12], MonotoneDirection.INCREASING, None),
        ([3.0, 2.0, 2.0], MonotoneDirection.DECREASING, None),
        ([3.0, 2.0, 2.5], MonotoneDirection.DECREASING, (1, 2)),
        ([1.0, 1.0, 1.0 + 1e-12], MonotoneDirection.CONSTANT, None),
        ([1.0, 1.001], MonotoneDirection.CONSTANT, (0, 1)),
        ([0.0, 0.0, 0.0], MonotoneDirection.INCREASING, None),
        ([0.0, 1e-15, 0.0], MonotoneDirection.CONSTANT, None),
        ([0.0, 1e-15, 0.0], MonotoneDirection.INCREASING, None),
        ([0.0, 1e-6], MonotoneDirection.CONSTANT, (0, 1)),
        ([1e6, 1e6 + 1e-4, 1e6], MonotoneDirection.CONSTANT, None),
        ([1e6, 1e6 + 1.0], MonotoneDirection.CONSTANT, (0, 1)),
    ],
)
def test_the_monotone_evaluation_names_the_first_pair_moving_the_wrong_way(
    values: List[float], direction: MonotoneDirection, offending: tuple
) -> None:
    """Within REL_TOL of the series' magnitude plus the floor; the offending pair by index.

    Float noise on a KPI that is zero everywhere is equal; a real step near zero, or one large for the
    series' magnitude, is not.
    """
    assert MonotoneEvaluation.offending_pair(values, direction) == offending


# ------------------------------------------------------------------------------- whole assemblies


@pytest.mark.base
def test_the_wrong_mock_fails_by_name(tmp_path: Path) -> None:
    """The wrong sign of a monotone and a bounds band the output leaves both fail, named; the rest passes."""
    resolver = AssemblyResolver([WRONG])
    report = AssemblyHarness(resolver, tmp_path).test_library(library_paths(resolver))

    with pytest.raises(AssemblyTestFailure) as caught:
        require_passed(report)
    message = str(caught.value)
    assert message.startswith("5 assembly test checks failed")
    assert (
        "mock/backwards_heater [monotone] s001 → s003 (from s000): monotone power_in_watt rises: Heater energy "
        "of Heater decreasing: power_in_watt 500.0 → 2333.333333333333 moves Heater energy 3 → 14, which is not "
        "decreasing"
    ) in message
    assert (
        "mock/backwards_heater [bounds] s000 (defaults): bounds Heater.ThermalPower [WATT] in [0, 1000]: 24 of "
        "96 steps leave the band; the first is step 0 with 2000"
    ) in message
    assert "ElectricityInput" not in message
    assert (tmp_path / "assembly_test_report.json").is_file()


@pytest.mark.base
def test_energy_and_temperature_outputs_are_decided_by_unit_and_load_type() -> None:
    """The completeness rule of the member contract: a power or energy unit, a temperature load type or unit."""
    rule = MemberContract.carries_energy_or_temperature

    assert rule(lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
    assert rule(lt.LoadTypes.HEATING, lt.Units.KWH)
    assert rule(lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
    assert rule(lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN)
    assert rule(lt.LoadTypes.TEMPERATURE, lt.Units.ANY)
    assert not rule(lt.LoadTypes.ON_OFF, lt.Units.ANY)
    assert not rule(lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP)


@pytest.mark.base
def test_the_member_contract_reads_the_constructed_members_outputs(tmp_path: Path) -> None:
    """An unbounded energy output and a bounds unit that is not the output's: contract failures by member and output."""
    library = Library(tmp_path)
    library.add(
        "broken/half_bounded",
        """
        schema_version: 4
        kind: assembly
        name: broken/half_bounded
        description: A heater.
        parameters:
          power_in_watt: {type: float, unit: WATT, default: 2000, range: {min: 500, max: 6000}, description: P.}
        components:
          Heater:
            class: tests.assemblies.mock_components.MockHeater
            preset: standard
            config:
              power_in_watt: {$param: power_in_watt}
        tests:
          bounds:
            - {output: Heater.ThermalPower, unit: KWH, min: 0}
          monotone:
            - {parameter: power_in_watt, kpi: Heater energy, member: Heater, direction: increasing}
        """,
    )
    report = AssemblyHarness(library.resolver(with_mocks=False), tmp_path / "out").test("broken/half_bounded")

    assert [(failure.check, failure.declaration, failure.message) for failure in report.failures] == [
        (
            CheckKind.CONTRACT,
            "Heater.ElectricityInput",
            "the WATT output 'Heater.ElectricityInput' (ELECTRICITY) has no bounds entry.",
        ),
        (
            CheckKind.CONTRACT,
            "Heater.ThermalPower",
            "the bounds entry states KWH for 'Heater.ThermalPower', whose unit is WATT.",
        ),
    ]
    assert report.checks_passed == 0 and report.runs


@pytest.mark.base
def test_an_assembly_the_contract_test_refuses_has_no_declaration_evaluated(tmp_path: Path) -> None:
    """Refused by the library check, no run; refused by the member contract, no declaration evaluated.

    No test contract at all is the library check's refusal. A KPI its member does not report is a
    member-contract failure on the constructed member: the base runs are recorded, nothing else checked.
    """
    library = Library(tmp_path)
    text = """
        schema_version: 4
        kind: assembly
        name: broken/{name}
        description: A heater.
        parameters:
          power_in_watt: {{type: float, unit: WATT, default: 2000, range: {{min: 500, max: 6000}}, description: P.}}
        components:
          Heater:
            class: tests.assemblies.mock_components.MockHeater
            preset: standard
            config:
              power_in_watt: {{$param: power_in_watt}}
        {tests}
        """
    bounds = (
        "tests:\n  bounds:\n    - {output: Heater.ThermalPower, unit: WATT, min: 0}\n"
        "    - {output: Heater.ElectricityInput, unit: WATT, min: 0}\n"
    )
    library.add("broken/untested", textwrap.dedent(text).format(name="untested", tests=""))
    library.add(
        "broken/nameless",
        textwrap.dedent(text).format(
            name="nameless",
            tests=bounds + "  monotone:\n    - {parameter: power_in_watt, kpi: Nope, member: Heater, "
            "direction: increasing}\n",
        ),
    )
    harness = AssemblyHarness(library.resolver(with_mocks=False), tmp_path / "out")

    untested = harness.test("broken/untested")
    nameless = harness.test("broken/nameless")

    assert not untested.runs
    assert {failure.check for failure in untested.failures + nameless.failures} == {CheckKind.CONTRACT}
    assert any("carries no test contract" in failure.message for failure in untested.failures)
    assert [(failure.declaration, failure.message) for failure in nameless.failures] == [
        ("Heater: Nope", "'Heater' reports no KPI 'Nope' (it reports: Heater energy).")
    ]
    assert nameless.runs and all(run.failed_checks == 0 for run in nameless.runs)
    assert nameless.checks_passed == 0


@pytest.mark.base
def test_a_port_without_a_registered_test_partner_refuses_naming_the_class(tmp_path: Path) -> None:
    """An empty registry: the array's weather port names MockWeather."""
    harness = AssemblyHarness(mock_resolver(), tmp_path, registry=TestPartnerRegistry([], []))

    with pytest.raises(
        TestPartnerMissingError,
        match="the port 'weather' of 'mock/pv_array' needs a test partner of the class MockWeather, and no "
        "registry serves it",
    ):
        harness.test("mock/pv_array")

    aborted = AssemblyHarness(mock_resolver(), tmp_path / "aborted", registry=TestPartnerRegistry([], []))
    with pytest.raises(TestPartnerMissingError):
        aborted.test_library(["mock/pv_array"])
    document = json.loads((tmp_path / "aborted" / "assembly_test_report.json").read_text(encoding="utf-8"))
    assert not document["passed"] and document["aborted"].startswith("TestPartnerMissingError: the port 'weather'")
    assert "aborted after 0 assemblies" in (tmp_path / "aborted" / "assembly_test_summary.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.base
def test_only_numeric_columns_are_checked_for_finiteness() -> None:
    """A NaN in a number column is named; a column of text has no finiteness to check."""
    frame = pd.DataFrame({"Power": [1.0, float("nan"), 2.0], "State": ["on", "off", "on"]})

    assert checks.nonfinite_columns(frame) == ["Power is nan at step 1 (1 steps not finite)"]


@pytest.mark.base
@pytest.mark.parametrize(
    "entries, found",
    [
        ({"General": {"PV production": {"name": "PV production", "unit": "kWh", "value": 5.0, "tag": "General", "source": None}}}, 5.0),
        ({"General": {}}, "0 derived KPIs (without a source) are named 'PV production'"),
    ],
)
def test_a_monotone_without_a_member_reads_the_derived_kpi_by_name(entries: dict, found: object) -> None:
    """``member: None`` names a derived KPI: one entry of that name without a source; a member's is not it."""
    member_kpi = {
        "PV production (subject-east-PVSystem)": {
            "name": "PV production",
            "unit": "kWh",
            "value": 7.0,
            "tag": "Rooftop PV",
            "source": {"import": SUBJECT, "instance": "east", "path": [{"import": SUBJECT, "instance": "east"}],
                       "member": "PVSystem", "assembly": "mock/pv_array", "name": "subject-east-PVSystem"},
        }
    }
    finder = KpiFinder({"BUI1": {**entries, "Rooftop PV": member_kpi}})

    if isinstance(found, float):
        assert checks.kpi_value(finder, "PV production", SUBJECT, None, "mock/pv_pair") == found
    else:
        with pytest.raises(ValueError, match=re.escape(str(found))):
            checks.kpi_value(finder, "PV production", SUBJECT, None, "mock/pv_pair")


@pytest.mark.base
def test_the_isolation_system_partners_every_port_that_changes_what_the_assembly_computes() -> None:
    """The boiler: a gas provider for its fuel, a cylinder for its optional dhw end; the connection: a consumer."""
    resolver = mock_resolver()
    builder = IsolationBuilder(resolver, TestPartnerRegistry.from_directories([Mocks.LIBRARY]))
    for path, components, entry in (
        (
            "mock/gas_boiler",
            ["GasMeter", "Occupancy", "Cylinder"],
            {"assembly": "mock/gas_boiler", "preset": "standard", "bind": {"fuel": "GasMeter"},
             "optional-bind": {"dhw": "Cylinder"}},
        ),
        (
            "mock/gas_connection",
            ["Occupancy", "GasCylinder", "GasBoiler"],
            {"assembly": "mock/gas_connection", "preset": "standard"},
        ),
        (
            "mock/electric_heater",
            ["Occupancy", "Tank", "Ems"],
            {"assembly": "mock/electric_heater", "preset": "standard", "bind": {"tank_temperature": "Tank"},
             "optional-bind": {"ems_modifier": "Ems"}},
        ),
    ):
        assembly = resolver.resolve(path, "test")
        space = ParameterSpace(assembly)
        book = SampleBook(space)
        DeterministicSamples.add_to(book)
        system = builder.build(assembly, space, book.samples[0])
        assert list(system.document["components"]) == components, path
        assert system.document["imports"][SUBJECT] == entry, path


@pytest.mark.base
def test_a_registry_that_does_not_read_is_refused_whole(tmp_path: Path) -> None:
    """A duplicate served class, an unknown requirement and a malformed serves block each refuse the file."""
    weather = "{class: tests.assemblies.mock_components.MockWeather, preset: standard}"
    cases = {
        "twice": f"partners:\n  A: {{serves: {{partner: MockWeather}}, component: {weather}}}\n"
        f"  B: {{serves: {{partner: MockWeather}}, component: {weather}}}\n",
        "unknown": f"partners:\n  A: {{serves: {{partner: MockWeather}}, requires: [Nobody], component: {weather}}}\n",
        "malformed": f"partners:\n  A: {{serves: {{class: MockWeather}}, component: {weather}}}\n",
        "carrier": f"partners:\n  A: {{serves: {{carrier: steam}}, component: {weather}}}\n",
    }
    messages = {
        "twice": "the partner class MockWeather is served twice, by 'A'",
        "unknown": "requires 'Nobody', which is no registered partner",
        "malformed": "write one of {partner: <class>}",
        "carrier": "names the carrier 'steam', which is none of",
    }
    for name, text in cases.items():
        directory = tmp_path / name
        directory.mkdir()
        (directory / TestPartnerRegistry.FILENAME).write_text(text, encoding="utf-8")
        with pytest.raises(TestPartnerRegistryError, match=messages[name]):
            TestPartnerRegistry.from_directories([directory])


# ------------------------------------------------------------------------------ shards and the CLI


@pytest.mark.base
def test_the_shards_are_disjoint_deterministic_and_cover_the_library() -> None:
    """Every n-th assembly of the sorted list from the i-th; an empty shard and a bad spelling are refused."""
    paths = ["e/e", "a/a", "c/c", "b/b", "d/d"]

    assert shard_of(paths, 1, 2) == ["a/a", "c/c", "e/e"]
    assert shard_of(paths, 2, 2) == ["b/b", "d/d"]
    assert sorted(shard_of(paths, 1, 3) + shard_of(paths, 2, 3) + shard_of(paths, 3, 3)) == sorted(paths)
    assert parse_shard("2/3") == (2, 3)
    for text in ("0/2", "3/2", "1-2", "a/b"):
        with pytest.raises(HarnessUsageError):
            parse_shard(text)
    with pytest.raises(HarnessUsageError, match="holds none of the 2 assemblies"):
        shard_of(["a/a", "b/b"], 3, 3)


@pytest.mark.base
def test_the_command_fails_on_the_wrong_mock_and_refuses_samples_in_the_pr_tier(tmp_path: Path) -> None:
    """Exit 1 with every failed check on the standard error stream; exit 2 for --samples with --tier pr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(
            ["energy-system", "test-assemblies", "--library", str(WRONG), "--out", str(tmp_path / "wrong")]
        )
    assert code == 1
    assert "5 checks failed:" in out.getvalue()
    assert "5 assembly test checks failed" in err.getvalue()

    err = io.StringIO()
    with redirect_stderr(err):
        code = main(["energy-system", "test-assemblies", "--samples", "3", "--out", str(tmp_path / "pr")])
    assert code == 2
    assert "--samples and --seed set the nightly hypercube" in err.getvalue()

    err = io.StringIO()
    with redirect_stderr(err):
        code = main(
            ["energy-system", "test-assemblies", "--library", str(tmp_path / "missing"), "--out", str(tmp_path / "m")]
        )
    assert code == 2
    assert "the library cannot be searched: EF-71" in err.getvalue()


@pytest.mark.base
def test_the_nightly_tier_runs_the_hypercube_through_the_command(tmp_path: Path) -> None:
    """``--tier nightly --samples 2`` on one mock assembly: the hypercube runs beside the deterministic samples."""
    paths = library_paths(mock_resolver())
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(
            [
                "energy-system",
                "test-assemblies",
                "--tier",
                "nightly",
                "--samples",
                "2",
                "--library",
                str(Mocks.LIBRARY),
                "--shard",
                f"{paths.index('mock/hot_water_tank') + 1}/{len(paths)}",
                "--out",
                str(tmp_path),
            ]
        )

    assert code == 0, out.getvalue()
    document = json.loads((tmp_path / "assembly_test_report.json").read_text(encoding="utf-8"))
    assert (document["tier"], document["hypercube_samples_per_branch"], document["seed"]) == ("nightly", 2, 20261003)
    (assembly,) = document["assemblies"]
    assert assembly["assembly"] == "mock/hot_water_tank" and assembly["passed"]
    assert assembly["branches"] == ["the whole box"]
    assert assembly["samples"]["hypercube"] == 2 and assembly["samples"]["deterministic"] >= 1
    assert "tier nightly, 2 hypercube samples per branch, seed 20261003" in out.getvalue()


@pytest.mark.base
def test_an_integer_sweep_narrower_than_its_steps_states_its_count(tmp_path: Path) -> None:
    """An integer range of 3 values swept in 4 steps takes 3, and the report says so."""
    library = Library(tmp_path)
    library.add(
        "narrow/heater",
        """
        schema_version: 4
        kind: assembly
        name: narrow/heater
        parameters:
          power_in_watt: {type: int, unit: WATT, default: 1000, range: {min: 1000, max: 1002}, description: P.}
        components:
          Heater:
            class: tests.assemblies.mock_components.MockHeater
            preset: standard
            config:
              power_in_watt: {$param: power_in_watt}
        tests:
          bounds:
            - {output: Heater.ThermalPower, unit: WATT, min: 0, max: 2000}
            - {output: Heater.ElectricityInput, unit: WATT, min: 0, max: 2000}
          monotone:
            - {parameter: power_in_watt, kpi: Heater energy, member: Heater, direction: increasing}
        """,
    )

    report = AssemblyHarness(library.resolver(with_mocks=False), tmp_path / "out").test("narrow/heater")

    assert report.passed, [failure.text() for failure in report.failures]
    assert report.notes == [
        "monotone power_in_watt rises: Heater energy of Heater increasing: the integer range of 'power_in_watt' "
        "holds 3 values, so the sweep takes 3 of the 4 steps asked for"
    ]


@pytest.mark.base
def test_the_console_script_loads_the_harness_only_for_its_verb() -> None:
    """``import hisim.cli`` pulls in neither the harness nor scipy; only ``test-assemblies`` imports them."""
    probe = (
        "import sys, hisim.cli; "
        "print('scipy' in sys.modules, any(name.startswith('hisim.energy_system.assemblies.testing') "
        "for name in sys.modules))"
    )
    loaded = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

    assert loaded.stdout.split() == ["False", "False"]
