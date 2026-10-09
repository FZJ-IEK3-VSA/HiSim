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

import functools
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.config import ConfigurationRefusedError
from hisim.energy_system.assemblies.model import MonotoneDirection
from hisim.energy_system.assemblies.parameters import ParameterChecks
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing import checks
from hisim.energy_system.assemblies.testing.checks import AssemblyCheckFailure, MemberContract, offending_pair
from hisim.energy_system.assemblies.testing.isolation import (
    SUBJECT,
    IsolationRun,
    IsolationRunError,
    brought_by_circuit_end,
    facts_needed,
    facts_of_partners,
    isolation_document,
    refusal_in,
    run_isolation,
)
from hisim.energy_system.assemblies.model import MemberTemplate
from hisim.energy_system.assemblies.testing.partners import (
    ServedKey,
    TestPartner,
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
from hisim.energy_system.model import ComponentEntry
from hisim.postprocessing.kpi_computation import tolerances
from scripts import golden_kpis
from tests.assemblies.helpers import Library, Mocks, Real
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
    class: {Mocks.CLASSES}.MockHeater
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
    """One assembly of a library (the mock one unless given) and its parameter space."""
    assembly = AssemblyResolver([root]).resolve(path, "test")
    return assembly, ParameterSpace(assembly.model)


@pytest.mark.assemblies
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


@pytest.mark.assemblies
def test_the_same_seed_reproduces_the_sample_and_another_seed_does_not(tmp_path: Path) -> None:
    """The hypercube is a function of the seed."""
    space = box(tmp_path)
    first, again, other = ([sample.values for sample in hypercube_samples(space, 8, seed)] for seed in (3, 3, 4))
    assert first == again != other


@pytest.mark.assemblies
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


@pytest.mark.assemblies
def test_the_deterministic_samples_cover_boundaries_values_and_variants_within_the_constraints() -> None:
    """The heater's boundaries, values and options; the array's share at its minimum states it, the power stays AUTO."""
    _, heater = mock("mock/variant_heater")
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
    _, array = mock("pv/array", Real.LIBRARY)
    (sample,) = [
        sample for sample in deterministic_samples(array) if "min of share_of_maximum_pv_potential" in sample.origins
    ]
    assert sample.values["share_of_maximum_pv_potential"] == 0.0 and sample.values["power_in_watt"] == "AUTO"
    assert ParameterChecks.is_stated(0.0) and not ParameterChecks.is_stated("AUTO")
    swept = sweeps(array, deterministic_samples(array), array.model.tests.monotone[0])  # type: ignore[union-attr]
    assert swept and all(ParameterChecks.is_stated(base.values["power_in_watt"]) for base, _ in swept)
    assert all(not ParameterChecks.is_stated(base.values["share_of_maximum_pv_potential"]) for base, _ in swept)
    points = [point["power_in_watt"] for point in swept[0][1]]
    assert points == pytest.approx([500.0, 500 + 29500 / 3, 500 + 59000 / 3, 30000.0])


@pytest.mark.assemblies
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
def test_the_isolation_system_partners_every_port_that_changes_what_the_assembly_computes() -> None:
    """The site and both circuit ends for a boiler, a consumer for a connection, an array and a manager for a battery.

    The real library's assemblies and partners, and the mock heater for the ports a variant switches.
    """
    real = AssemblyResolver([Real.LIBRARY])
    registries = {
        Real.LIBRARY: TestPartnerRegistry.from_directories(real.directories),
        Mocks.LIBRARY: TestPartnerRegistry.from_directories([Mocks.LIBRARY]),
    }
    heating_site = ["Weather", "UTSPConnector", "Building", "HeatDistributionController"]
    for path, root, components, verbs in (
        (
            "heating/gas_condensing_boiler",
            Real.LIBRARY,
            heating_site + ["DHWStorage", "GasMeter", "HeatDistributionSystem"],
            {
                "bind": {
                    "weather": "Weather",
                    "flow_temperature": "HeatDistributionController",
                    "dhw_temperature": "DHWStorage",
                    "space_heating": "HeatDistributionSystem",
                    "dhw": "DHWStorage",
                }
            },
        ),
        (
            "supply/gas_connection",
            Real.LIBRARY,
            heating_site + ["GasBoilerController", "HeatDistributionSystem", "GasBuffer", "GasCylinder", "GasBoiler"],
            {},
        ),
        ("storage/battery", Real.LIBRARY, ["Weather", "UTSPConnector", "Building", "PVArray", "EnergyManager"], {}),
        ("supply/electricity_grid", Real.LIBRARY, ["UTSPConnector"], {}),
        (
            "mock/variant_heater",
            Mocks.LIBRARY,
            ["Occupancy", "Tank", "Ems"],
            {"bind": {"tank_temperature": "Tank"}, "optional-bind": {"ems_modifier": "Ems"}},
        ),
    ):
        assembly, space = mock(path, root)
        document = isolation_document(assembly, space.defaults(), registries[root])
        assert list(document["components"]) == components, path
        entry = document["imports"][SUBJECT]
        assert {verb: entry[verb] for verb in ("bind", "optional-bind") if verb in entry} == verbs, path


@pytest.mark.assemblies
def test_the_isolation_system_binds_a_single_provider_fact_need_to_its_partner(tmp_path: Path) -> None:
    """Catches a scalar fact need left without its verb, so a second array in a system would leave it ambiguous.

    Example: a battery whose ``pv_power`` need reads one array's peak power gets the ``PVArray`` partner and
    ``bind: {pv_power: PVArray}``. The real battery's fact need is ``many: true`` and takes no verb, so the
    scalar shape is an inline assembly of mock classes.
    """
    library = Library(tmp_path)
    library.add(
        "sampled/scalar_battery",
        f"""
        schema_version: 4
        kind: assembly
        name: sampled/scalar_battery
        description: A battery whose fact need reads the peak power of one array.
        components:
          Battery: {{class: {Mocks.CLASSES}.MockBattery, preset: sized_to_pv}}
        interface:
          needs:
            pv_power: {{fact: pv_peak_power_in_watt, into: [Battery]}}
        tests: {{bounds: [], monotone: []}}
        """,
    )
    assembly = library.resolver().resolve("sampled/scalar_battery", "test")
    document = isolation_document(assembly, {}, TestPartnerRegistry.from_directories([Mocks.LIBRARY]))
    assert list(document["components"]) == ["Weather", "PVArray"]
    entry = document["imports"][SUBJECT]
    assert {verb: entry[verb] for verb in ("bind", "optional-bind") if verb in entry} == {
        "bind": {"pv_power": "PVArray"}
    }


@pytest.mark.assemblies
def test_a_fact_a_member_reads_without_a_port_gets_its_registered_provider(tmp_path: Path) -> None:
    """A battery whose law reads the arrays' peak power by the bare-fact rule, with no fact port, gets the array."""
    library = Library(tmp_path)
    library.add(
        "sampled/bare_battery",
        f"""
        schema_version: 4
        kind: assembly
        name: sampled/bare_battery
        description: A battery reading its sizing fact from the site without a fact port.
        components:
          Battery: {{class: {Mocks.CLASSES}.MockArrayBattery, preset: sized_to_all_arrays}}
        tests: {{bounds: [], monotone: []}}
        """,
    )
    assembly = library.resolver().resolve("sampled/bare_battery", "test")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    document = isolation_document(assembly, {}, registry)
    assert list(document["components"]) == ["Weather", "PVArray"]
    with pytest.raises(
        TestPartnerMissingError,
        match="the fact read 'pv_peak_power_in_watt' of 'sampled/bare_battery' needs a test partner, the provider of "
        "the fact pv_peak_power_in_watt",
    ):
        isolation_document(assembly, {}, TestPartnerRegistry([], []))


@pytest.mark.assemblies
def test_a_fact_read_behind_an_inactive_fact_port_gets_its_registered_provider(tmp_path: Path) -> None:
    """Catches an inactive conditional fact port hiding its fact from the bare-fact rule, leaving the read unserved.

    With ``pinned: false`` the port is active and its fact need brings the array; with ``pinned: true`` the
    port is inactive, the battery's law still reads the fact, and the harness gives the read its provider.
    """
    library = Library(tmp_path)
    library.add(
        "sampled/conditional_battery",
        f"""
        schema_version: 4
        kind: assembly
        name: sampled/conditional_battery
        description: A battery whose fact port is active only while it is not pinned.
        parameters:
          pinned: {{type: bool, default: false, description: Whether the fact port is switched off.}}
        components:
          Battery: {{class: {Mocks.CLASSES}.MockArrayBattery, preset: sized_to_all_arrays}}
        interface:
          needs:
            pv_peak_power:
              {{fact: pv_peak_power_in_watt, many: true, into: [Battery], active_when: {{pinned: [false]}}}}
        tests: {{bounds: [], monotone: []}}
        """,
    )
    assembly = library.resolver().resolve("sampled/conditional_battery", "test")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    for pinned in (False, True):
        document = isolation_document(assembly, {"pinned": pinned}, registry)
        assert list(document["components"]) == ["Weather", "PVArray"], pinned


@pytest.mark.assemblies
def test_a_fact_a_partner_in_the_system_contributes_gets_no_second_provider() -> None:
    """Catches the harness adding a second energy_carrier provider beside the oil tank's consumer partner.

    The oil boiler partner contributes energy_carrier by its class, and the meter copies its carrier from it, so no
    partner serves the fact. A second provider of the fact, of any value (a second burner of the same fuel included),
    leaves the meter's law no one provider to read, and sizing refuses the system.
    """
    library = Path(__file__).resolve().parents[2] / "energy_systems" / "assemblies"
    resolver = AssemblyResolver([library])
    registry = TestPartnerRegistry.from_directories(resolver.directories)
    assembly = resolver.resolve("supply/oil_tank", "test")
    document = isolation_document(assembly, {}, registry)
    classes = [entry["class"].rsplit(".", 1)[-1] for entry in document["components"].values()]
    assert classes.count("GenericBoiler") == 1 and "OilBoiler" in document["components"]
    assert ("fact", "energy_carrier") not in registry.served


def fact_partner(
    name: str, mock_class: str, serves: Tuple[str, ...] = (), requires: Tuple[str, ...] = ()
) -> TestPartner:
    """A test partner of a mock class at its ``standard`` preset, serving the given facts."""
    return TestPartner(
        name=name,
        serves=tuple(("fact", fact) for fact in serves),
        requires=requires,
        component={"class": f"{Mocks.CLASSES}.{mock_class}", "preset": "standard"},
        origin="inline",
    )


def fact_members(*members: Tuple[str, str]) -> Mapping[str, MemberTemplate]:
    """Assembly members by name, each an entry of a mock class."""
    return {
        name: MemberTemplate(entry=ComponentEntry(name=name, class_path=f"{Mocks.CLASSES}.{mock_class}"))
        for name, mock_class in members
    }


@pytest.mark.parametrize(
    ["members", "port_facts", "partner_facts", "needed"],
    [
        ((("Reader", "MockLoadReader"),), (), (), ["heating_load_in_watt", "number_of_apartments"]),
        ((("Reader", "MockLoadReader"), ("House", "MockHouseFacts")), (), (), []),
        ((("Reader", "MockLoadReader"),), ("heating_load_in_watt",), (), ["number_of_apartments"]),
        ((("Reader", "MockLoadReader"),), (), ("number_of_apartments",), ["heating_load_in_watt"]),
        ((("House", "MockHouseFacts"),), (), (), []),
    ],
    ids=["nothing_provides", "a_member_provides", "a_fact_port_names", "a_partner_contributes", "nothing_read"],
)
def test_facts_needed_leaves_out_what_a_member_a_fact_port_or_a_partner_provides(
    members: Tuple[Tuple[str, str], ...], port_facts: Tuple[str, ...], partner_facts: Tuple[str, ...], needed: List[str]
) -> None:
    """Catches a fact read given a provider although a member, an active fact port or a partner already provides it."""
    result = facts_needed(port_facts, fact_members(*members), partner_facts)
    assert result == [(("fact", fact),) for fact in needed]


def test_facts_of_partners_reads_the_classes_of_the_partners_and_their_requirements() -> None:
    """Catches a fact a required partner contributes being missed, or a fact read from a partner not in the system."""
    registry = TestPartnerRegistry(
        [
            fact_partner("House", "MockHouseFacts", serves=("heating_load_in_watt",)),
            fact_partner("Count", "MockApartmentCount", serves=("number_of_apartments",)),
            fact_partner("Device", "MockBareDevice", requires=("Count",)),
        ],
        [],
    )
    assert facts_of_partners(registry, []) == set()
    assert facts_of_partners(registry, ["Device"]) == {"number_of_apartments"}
    assert facts_of_partners(registry, ["House"]) == {"heating_load_in_watt", "number_of_apartments"}
    assert registry.config_class_of("House").__name__ == "MockHouseFactsConfig"


def load_reader(tmp_path: Path) -> ResolvedAssembly:
    """An assembly of one member whose laws read the heating load and the number of apartments, with no fact port."""
    library = Library(tmp_path)
    library.add(
        "sampled/load_reader",
        f"""
        schema_version: 4
        kind: assembly
        name: sampled/load_reader
        description: A device reading two sizing facts from the site without a fact port.
        components:
          Reader: {{class: {Mocks.CLASSES}.MockLoadReader, preset: sized}}
        tests: {{bounds: [], monotone: []}}
        """,
    )
    return library.resolver().resolve("sampled/load_reader", "test")


def test_a_provider_found_for_one_fact_also_serves_the_other_facts_its_class_contributes(tmp_path: Path) -> None:
    """Catches a second provider of a fact the first provider added already contributes, which sizing would refuse.

    The member reads the heating load and the number of apartments. The house, registered for the heating load,
    contributes both, so the partner registered for the number of apartments must not join beside it.
    """
    registry = TestPartnerRegistry(
        [
            fact_partner("House", "MockHouseFacts", serves=("heating_load_in_watt",)),
            fact_partner("Count", "MockApartmentCount", serves=("number_of_apartments",)),
        ],
        [],
    )
    document = isolation_document(load_reader(tmp_path), {}, registry)
    assert list(document["components"]) == ["House"]


def test_a_partner_registered_for_a_fact_its_class_does_not_contribute_is_refused(tmp_path: Path) -> None:
    """Catches the harness adding the same partner again and again for a fact it never contributes."""
    registry = TestPartnerRegistry(
        [
            fact_partner("Device", "MockBareDevice", serves=("heating_load_in_watt",)),
            fact_partner("Count", "MockApartmentCount", serves=("number_of_apartments",)),
        ],
        [],
    )
    with pytest.raises(TestPartnerRegistryError, match="'Device' is registered as the provider of the fact"):
        isolation_document(load_reader(tmp_path), {}, registry)


def test_a_need_binds_the_partner_of_its_class_a_circuit_end_brings() -> None:
    """The collector's controller reads the cylinder the collector charges, never the heat pump's cylinder.

    The registry serves the need's class, SimpleDHWStorage, by DHWStorage, the cylinder at the other end of a
    generator's dhw circuit, which requires only UTSPConnector; the solar_dhw circuit end brings SolarCylinder, a
    cylinder of the same class, so the need binds it and the system holds one cylinder.
    """
    library = Path(__file__).resolve().parents[2] / "energy_systems" / "assemblies"
    resolver = AssemblyResolver([library])
    registry = TestPartnerRegistry.from_directories(resolver.directories)
    assembly = resolver.resolve("heating/solar_thermal", "test")
    document = isolation_document(assembly, {}, registry)
    classes = [entry["class"].rsplit(".", 1)[-1] for entry in document["components"].values()]
    assert classes.count("SimpleDHWStorage") == 1 and "SolarCylinder" in document["components"]
    assert document["imports"][SUBJECT]["bind"]["cylinder_temperature"] == "SolarCylinder"
    assert registry.served[("partner", "SimpleDHWStorage")].name == "DHWStorage"


def circuit_end(name: str, circuit: str) -> TestPartner:
    """A mock cylinder registered as the end of one circuit whose other end is a mock boiler."""
    return TestPartner(
        name=name,
        serves=(("circuit", circuit, frozenset({"MockBoiler"})),),
        requires=(),
        component={"class": f"{Mocks.CLASSES}.MockCylinder", "preset": "standard"},
        origin="inline",
    )


def test_a_need_no_circuit_end_brings_a_partner_for_falls_back_to_the_registry() -> None:
    """Catches the circuit-end rule taking over a need whose class no circuit end brings.

    A circuit end brings a MockCylinder. A need for a Weather, and a need served only by a fact, find no brought
    partner, so the rule returns None and the registry serves them as before.
    """
    cylinder = circuit_end("CylinderA", "dhw")
    ends: Dict[Tuple[str, Tuple[ServedKey, ...]], TestPartner] = {
        ("first", (("circuit", "dhw", frozenset({"MockBoiler"})),)): cylinder
    }
    assert brought_by_circuit_end("weather", (("partner", "MockWeather"),), ends, "sampled/one_end") is None
    assert brought_by_circuit_end("load", (("fact", "heating_load_in_watt"),), ends, "sampled/one_end") is None
    assert brought_by_circuit_end("cylinder", (("partner", "MockCylinder"),), ends, "sampled/one_end") is cylinder


def test_the_solar_collectors_weather_need_still_binds_the_registry_partner() -> None:
    """The circuit-end rule changes only the cylinder need: the weather need binds the registry's Weather partner."""
    library = Path(__file__).resolve().parents[2] / "energy_systems" / "assemblies"
    resolver = AssemblyResolver([library])
    registry = TestPartnerRegistry.from_directories(resolver.directories)
    document = isolation_document(resolver.resolve("heating/solar_thermal", "test"), {}, registry)
    assert document["imports"][SUBJECT]["bind"]["weather"] == registry.served[("partner", "Weather")].name


def test_a_need_two_circuit_ends_bring_partners_of_its_class_for_is_refused_naming_both(tmp_path: Path) -> None:
    """Catches a need silently binding one of two cylinders its assembly's circuit ends bring.

    Two boilers are the ends of two circuits, and each circuit's other end is a MockCylinder of its own. A need
    for a MockCylinder has two candidates; the harness refuses, as the engine's default rule does, naming both ports.
    """
    library = Library(tmp_path)
    library.add(
        "sampled/two_ends",
        f"""
        schema_version: 4
        kind: assembly
        name: sampled/two_ends
        description: Two boilers, each charging a cylinder of its own, and a need for a cylinder.
        components:
          First: {{class: {Mocks.CLASSES}.MockBoiler, preset: condensing, inputs: [{{$port: first}}]}}
          Second: {{class: {Mocks.CLASSES}.MockBoiler, preset: condensing, inputs: [{{$port: second}}]}}
        interface:
          needs:
            cylinder: {{into: [First], partner: MockCylinder}}
          provides:
            first: {{circuit: dhw, member: First}}
            second: {{circuit: solar_dhw, member: Second}}
        tests: {{bounds: [], monotone: []}}
        """,
    )
    assembly = library.resolver().resolve("sampled/two_ends", "test")
    registry = TestPartnerRegistry([circuit_end("CylinderA", "dhw"), circuit_end("CylinderB", "solar_dhw")], [])
    with pytest.raises(TestPartnerRegistryError, match="the class MockCylinder") as refusal:
        isolation_document(assembly, {}, registry)
    assert "'CylinderA' through the port 'first'" in str(refusal.value)
    assert "'CylinderB' through the port 'second'" in str(refusal.value)


@pytest.mark.assemblies
def test_a_port_without_a_registered_test_partner_refuses_naming_the_class() -> None:
    """An empty registry: the array's weather port names Weather."""
    assembly, space = mock("pv/array", Real.LIBRARY)
    with pytest.raises(
        TestPartnerMissingError,
        match="the port 'weather' of 'pv/array' needs a test partner, a "
        "partner of the class Weather, and no test_partners.yaml serves it",
    ):
        isolation_document(assembly, space.defaults(), TestPartnerRegistry([], []))


@pytest.mark.assemblies
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
    weather = f"{{class: {Mocks.CLASSES}.MockWeather, preset: standard}}"
    (tmp_path / TestPartnerRegistry.FILENAME).write_text(f"partners:\n  {text.replace(': W}', f': {weather}}}')}\n")
    with pytest.raises(TestPartnerRegistryError, match=re.escape(message)):
        TestPartnerRegistry.from_directories([tmp_path])


@pytest.mark.assemblies
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
            class: {Mocks.CLASSES}.MockHeater
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
def test_a_column_a_bounds_entry_cannot_compare_fails_by_name(tmp_path: Path) -> None:
    """Catches a bounds entry passing on a column of text, or one holding a value that is not finite."""
    assembly, _ = mock("mock/variant_heater")
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
            "mock/variant_heater",
            tmp_path,
            results=pd.DataFrame({"Heater power": column}),
            outputs=[Output()],
            runtime={"Heater": "subject-Heater"},
        )
        with pytest.raises(
            AssemblyCheckFailure, match=re.escape(f"x bounds Heater.ThermalPower [WATT] in [0, 6000]: {problem}")
        ):
            checks.check_bounds(run, declaration)


@pytest.mark.assemblies
def test_a_run_whose_component_raises_names_where_it_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches a failed run that does not say whether the assembly's member or the harness raised."""

    def _raise(*_: Any) -> None:
        raise RuntimeError("the heater broke")

    monkeypatch.setattr(MockHeater, "i_simulate", _raise)
    assembly, space = mock("mock/variant_heater")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    run = run_isolation(assembly, space.defaults(), registry, AssemblyResolver([Mocks.LIBRARY]), tmp_path / "r", "h s0")
    line = _raise.__code__.co_firstlineno + 1
    with pytest.raises(AssemblyCheckFailure) as caught:
        checks.check_run(run)
    assert (
        str(caught.value)
        == f"h s0 run the isolation run: RuntimeError: the heater broke (raised at test_harness.py:{line})"
    )


@pytest.mark.assemblies
def test_a_check_reading_a_run_without_results_that_raised_nothing_is_a_harness_error(tmp_path: Path) -> None:
    """Catches a check passing vacuously on a run the harness left without results."""
    with pytest.raises(
        IsolationRunError,
        match=re.escape(
            "x: the check 'finite every result column' reads a run that has no results although it raised nothing"
        ),
    ):
        checks.check_finite(IsolationRun("x", "a/b", tmp_path))


def refusing_heater(monkeypatch: pytest.MonkeyPatch, above_watt: float, error: type) -> None:
    """Makes ``MockHeater`` raise ``error`` at construction when its power lies above ``above_watt``."""
    original = MockHeater.__init__

    # wraps keeps the constructor's annotations, from which the build reads the config class.
    @functools.wraps(original)
    def construct(self: MockHeater, my_simulation_parameters: Any, config: Any) -> None:
        """The heater's constructor, raising above the threshold."""
        if config.power_in_watt > above_watt:
            raise error(f"{config.component_id.name}: {config.power_in_watt} W is refused here")
        original(self, my_simulation_parameters, config)

    monkeypatch.setattr(MockHeater, "__init__", construct)


@pytest.mark.assemblies
def test_a_member_refusing_its_configuration_is_handled_and_any_other_error_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a refused sample failing the contract, and a crash passing as a refusal.

    The heater's constructor raises ``ConfigurationRefusedError``: the build wraps it (EF-33), the run
    finds it as the cause, and every check raises ``SampleRefused``. The same constructor raising a
    plain ``ValueError`` is a failure of every check, named.
    """
    assembly, space = mock("mock/variant_heater")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    resolver = AssemblyResolver([Mocks.LIBRARY])
    tests = assembly.model.tests
    assert tests is not None
    refusing_heater(monkeypatch, 1000.0, ConfigurationRefusedError)
    run = run_isolation(assembly, space.defaults(), registry, resolver, tmp_path / "refused", "h s0")
    assert isinstance(run.refusal, ConfigurationRefusedError)
    assert run.error is not None and run.refusal is run.error.__cause__
    for check in (
        checks.check_run,
        checks.check_energy_balance,
        checks.check_finite,
        lambda run: checks.check_member_contract(run, tests),
        lambda run: checks.check_bounds(run, tests.bounds[0]),
    ):
        with pytest.raises(checks.SampleRefused, match="^h s0 refused by a member: Heater: 2000.0 W is refused here$"):
            check(run)
    refusing_heater(monkeypatch, 1000.0, ValueError)
    crashed = run_isolation(assembly, space.defaults(), registry, resolver, tmp_path / "crashed", "h s0")
    assert crashed.refusal is None
    with pytest.raises(AssemblyCheckFailure, match="^h s0 run the isolation run: EnergySystemWiringError: EF-33"):
        checks.check_run(crashed)
    with pytest.raises(AssemblyCheckFailure, match="^h s0 finite every result column: the run did not finish"):
        checks.check_finite(crashed)


@pytest.mark.assemblies
def test_an_error_raised_while_handling_a_refusal_is_no_refusal() -> None:
    """Catches a crash in an ``except`` block passing as a refusal: only the explicit cause counts."""
    try:
        try:
            raise ConfigurationRefusedError("refused")
        except ConfigurationRefusedError:
            raise RuntimeError("crashed while handling it")  # pylint: disable=raise-missing-from
    except RuntimeError as error:
        assert refusal_in(error) is None
    refusal = ConfigurationRefusedError("refused")
    wrapped = ValueError("wrapped")
    wrapped.__cause__ = refusal
    assert refusal_in(wrapped) is refusal and refusal_in(None) is None


@pytest.mark.assemblies
def test_a_monotone_sweep_drops_the_points_a_member_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches a sweep failing on its refused points, or skipping when two points remain to compare.

    The heater's power sweeps 500, 2333, 4167 and 6000 W. Refused above 4000 W the last two points
    are dropped and the first two still rise; refused above 1000 W one point remains and the sweep is
    skipped (``SampleRefused``), naming the dropped points.
    """
    assembly, space = mock("mock/variant_heater")
    registry = TestPartnerRegistry.from_directories([Mocks.LIBRARY])
    resolver = AssemblyResolver([Mocks.LIBRARY])
    (defaults, *_) = deterministic_samples(space)
    tests = assembly.model.tests
    assert tests is not None

    def run_point(index: int, values: Mapping[str, Any], label: str) -> IsolationRun:
        """One sweep point's run."""
        return run_isolation(assembly, values, registry, resolver, tmp_path / f"{label[-1]}{index}", label)

    refusing_heater(monkeypatch, 4000.0, ConfigurationRefusedError)
    assert checks.evaluate_monotone(run_point, space, defaults, tests.monotone[0]) == pytest.approx(
        [12500 / 3, 6000.0]
    )
    refusing_heater(monkeypatch, 1000.0, ConfigurationRefusedError)
    with pytest.raises(
        checks.SampleRefused,
        match=re.escape("mock/variant_heater sweep of power_in_watt from s000: a member refuses 3 of 4 points"),
    ):
        checks.evaluate_monotone(run_point, space, defaults, tests.monotone[0])
    assert not list(tmp_path.glob("*")), "every sweep point's run is released, refused or not"


@pytest.mark.assemblies
def test_the_real_heat_pump_refuses_a_w55_scop_above_its_w35_scop_as_a_configuration_refusal(tmp_path: Path) -> None:
    """Catches the box's refused corner failing the contract: both SCOPs in range, W55 above W35, is handled.

    The heat pump's ranges overlap (W35 2.5 to 6, W55 2 to 4.5); the component alone refuses a W55
    above the W35, and the isolation run carries that refusal as the cause of its build error.
    """
    library = Path(__file__).resolve().parents[2] / "energy_systems" / "assemblies"
    resolver = AssemblyResolver([library])
    assembly = resolver.resolve("heating/air_source_heat_pump", "test")
    values = {**ParameterSpace(assembly.model).defaults(), "scop_en14825_w35": 3.0, "scop_en14825_w55": 4.0}
    registry = TestPartnerRegistry.from_directories([library])
    run = run_isolation(assembly, values, registry, resolver, tmp_path / "r", "hp s0")
    assert isinstance(run.refusal, ConfigurationRefusedError), run.error
    assert "the W55 SCOP 4.0 is above the W35 SCOP 3.0" in str(run.refusal)
    with pytest.raises(checks.SampleRefused, match="^hp s0 refused by a member: HeatPump"):
        checks.check_run(run)
    run.release()
