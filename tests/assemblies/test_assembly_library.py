"""The library check and the resolver (``assemblies_spec.md`` §2.6, §3.3, §9.3, §9.4, D24).

The library check lists every problem of an assembly at once and refuses it as a whole; the
resolver finds an assembly along the search path, refuses a name found twice and records the
content hash of what it read.
"""

import hashlib
import os
from pathlib import Path

import pytest

from hisim.component_interface import ClassInterface, DeclaredPort
from hisim import loadtypes as lt
from hisim.energy_system.assemblies.library import CheckStrength, check_assembly, require_valid
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemAssemblyError
from tests.assemblies.helpers import Fixtures, Library, fixture_resolver


def library_paths() -> list:
    """Every assembly of the fixture library, as library paths."""
    return sorted(
        path.relative_to(Fixtures.LIBRARY).as_posix()[: -len(".assembly.yaml")]
        for path in Fixtures.LIBRARY.rglob("*.assembly.yaml")
    )


@pytest.mark.base
@pytest.mark.parametrize("library_path", library_paths())
def test_every_fixture_assembly_passes_the_library_check(library_path: str) -> None:
    """Descriptions, ranges, the test contract, ports, units and order: nothing missing anywhere."""
    resolver = fixture_resolver()

    assert not check_assembly(resolver.resolve(library_path, "test"), resolver, CheckStrength.LIBRARY)


@pytest.mark.base
def test_the_library_check_lists_every_problem_of_a_file_at_once(tmp_path: Path) -> None:
    """One file with many faults gets one refusal naming each of them with its line."""
    library = Library(tmp_path)
    library.add(
        "broken/everything",
        """
        schema_version: 4
        kind: assembly
        name: broken/elsewhere
        parameters:
          power_in_watt: {type: float, unit: KILOWATT, default: 2000}
          mode: {type: enum, values: [a, b], default: a, description: Mode.}
          quote: {type: string, default: x, description: Reserved.}
        constraints:
          - {exactly_one_of: [power_in_watt, missing]}
        presets:
          bad: {nothing: 1}
        components:
          Heater:
            class: tests.assemblies.fixture_components.FakeHeater
            preset: standard
            config:
              power_in_watt: {$param: power_in_watt}
            inputs:
              - Outsider
              - {$port: undeclared}
        variants:
          v:
            selected_by: mode
            options:
              one: {when: [a], components: {}}
        interface:
          needs:
            weather: {into: [Nobody], partner: FakeWeather}
          provides:
            heat: {output: Heater.NoSuchOutput}
        """,
    )
    resolver = library.resolver()
    assembly = resolver.resolve("broken/everything", "test")

    found = check_assembly(assembly, resolver, CheckStrength.LIBRARY)
    text = "\n".join(found)

    for expected in (
        "named 'broken/elsewhere' but lives at 'broken/everything'",
        "'quote' is reserved",
        "'power_in_watt' has no description",
        "'power_in_watt' has no range",
        "names 'missing', which is no parameter",
        "the preset 'bad' sets 'nothing', which is no parameter",
        "covers no option for mode='b'",
        "takes an input from 'Outsider', which is no member",
        "'power_in_watt' is in KILOWATT, the field FakeHeaterConfig.power_in_watt it feeds in WATT",
        "lowers into 'Nobody', which is no member",
        "'Heater.NoSuchOutput', which is no output of 'Heater'",
        "a placeholder for 'undeclared', which is no port",
        "carries no test contract",
    ):
        assert expected in text, expected
    assert all(problem.startswith("broken/everything.assembly.yaml:") for problem in found)
    with pytest.raises(EnergySystemAssemblyError, match=r"EF-75 .*has \d+ problems"):
        require_valid(assembly, resolver, CheckStrength.LIBRARY)


@pytest.mark.base
def test_the_test_contract_must_bound_every_energy_and_temperature_output(tmp_path: Path) -> None:
    """D24: a ``bounds`` entry per energy-carrying or temperature output, at least one ``monotone``."""
    library = Library(tmp_path)
    library.add(
        "broken/unbounded",
        """
        schema_version: 4
        kind: assembly
        name: broken/unbounded
        parameters:
          volume_in_liter: {type: float, unit: LITER, default: 150, range: {min: 50, max: 500}, description: Volume.}
        components:
          Tank:
            class: tests.assemblies.fixture_components.FakeTank
            preset: standard
            config: {volume_in_liter: {$param: volume_in_liter}}
        tests:
          bounds:
            - {output: Tank.WaterTemperature, unit: KELVIN, min: 0, max: 400}
            - {kpi: No such KPI, member: Tank, max: 1}
          monotone: []
          expect:
            - {preset: missing, kpi: Standby heat losses, member: Tank, max: 1}
        """,
    )
    resolver = library.resolver()

    found = "\n".join(check_assembly(resolver.resolve("broken/unbounded", "test"), resolver, CheckStrength.LIBRARY))

    assert "the WATT output 'Tank.HeatLoss' has no bounds entry" in found
    assert "states KELVIN for 'Tank.WaterTemperature', whose unit is CELSIUS" in found
    assert "'Tank' reports no KPI 'No such KPI'" in found
    assert "no monotone entry" in found
    assert "the preset 'missing', which the assembly does not offer" in found


@pytest.mark.base
def test_energy_and_temperature_outputs_are_decided_by_unit_and_load_type() -> None:
    """The documented rule: a power or energy unit, a temperature load type or unit."""
    rule = ClassInterface.carries_energy_or_temperature

    assert rule(DeclaredPort("P", lt.LoadTypes.ELECTRICITY, lt.Units.WATT))
    assert rule(DeclaredPort("E", lt.LoadTypes.HEATING, lt.Units.KWH))
    assert rule(DeclaredPort("T", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS))
    assert rule(DeclaredPort("dT", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN))
    assert not rule(DeclaredPort("S", lt.LoadTypes.ON_OFF, lt.Units.ANY))
    assert not rule(DeclaredPort("W", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP))


@pytest.mark.base
def test_a_class_interface_is_held_to_at_construction() -> None:
    """A component adding a port its interface does not declare is refused by the component itself."""
    from hisim.component_interface import ClassInterfaceViolation  # noqa: PLC0415
    from hisim.simulationparameters import SimulationParameters  # noqa: PLC0415
    from tests.assemblies.fixture_components import FakeWeather, FakeWeatherConfig  # noqa: PLC0415

    weather = FakeWeather(SimulationParameters.one_day_only(2021, 900), FakeWeatherConfig.preset_standard("W"))
    with pytest.raises(ClassInterfaceViolation, match="adds the output 'Rogue'"):
        weather.add_output("W", "Rogue", lt.LoadTypes.ANY, lt.Units.ANY, output_description="x")
    with pytest.raises(ClassInterfaceViolation, match="default connections from 'FakeTank'"):
        from hisim.component import ComponentConnection  # noqa: PLC0415

        weather.add_default_connections([ComponentConnection("X", "FakeTank", "Y")])


@pytest.mark.base
def test_the_resolver_searches_the_library_directories_and_hashes_what_it_reads() -> None:
    """A library path resolves to one file; the record carries the sha256 of its bytes."""
    resolved = fixture_resolver().resolve("pv/array", "test")

    assert resolved.file == Fixtures.LIBRARY / "pv" / "array.assembly.yaml"
    assert resolved.sha256 == hashlib.sha256(resolved.file.read_bytes()).hexdigest()
    assert resolved.label == "pv/array.assembly.yaml"


@pytest.mark.base
def test_a_name_found_in_two_places_is_refused(tmp_path: Path) -> None:
    """A library path is never shadowed."""
    library = Library(tmp_path)
    library.add("pv/array", (Fixtures.LIBRARY / "pv" / "array.assembly.yaml").read_text(encoding="utf-8"))

    with pytest.raises(EnergySystemAssemblyError, match="EF-72 .*found in two places"):
        library.resolver().resolve("pv/array", "imports.pv")


@pytest.mark.base
def test_an_unknown_assembly_is_refused_with_the_ones_available() -> None:
    """The message lists the library and suggests the near miss."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-71 .*Did you mean: pv/array"):
        fixture_resolver().resolve("pv/arrays", "imports.pv")
    with pytest.raises(EnergySystemAssemblyError, match="EF-71 .*not an assembly path"):
        fixture_resolver().resolve("../pv", "imports.pv")


@pytest.mark.base
def test_the_default_resolver_reads_the_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``HISIM_ASSEMBLY_PATH`` adds directories after the repository's library; a missing one is refused."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, os.pathsep.join([str(Fixtures.LIBRARY)]))
    assert (
        AssemblyResolver.default().resolve("pv/array", "test").file == Fixtures.LIBRARY / "pv" / "array.assembly.yaml"
    )

    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(tmp_path / "missing"))
    with pytest.raises(EnergySystemAssemblyError, match="EF-71 .*does not exist"):
        AssemblyResolver.default()
