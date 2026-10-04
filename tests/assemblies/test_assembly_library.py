"""The library check and the resolver (``assemblies_spec.md`` §2.6, §3.3, §9.3, §9.4, D24).

The library check lists every problem of an assembly at once and refuses it as a whole; the
resolver finds an assembly along the search path, refuses a name found twice and records the
content hash of what it read.
"""

import hashlib
import os
from pathlib import Path
from typing import Any, Tuple

import pytest

from hisim.energy_system.assemblies.library import CheckStrength, check_assembly, require_valid
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemAssemblyError
from hisim.energy_system.assemblies.testing.contract import MemberContract
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.helpers import Library, Mocks, mock_resolver
from tests.assemblies.mock_components import (
    MockBoiler,
    MockBoilerConfig,
    MockHeater,
    MockHeaterConfig,
    MockTank,
    MockTankConfig,
)


def library_paths() -> list:
    """Every assembly of the mock library, as library paths."""
    return sorted(
        path.relative_to(Mocks.LIBRARY).as_posix()[: -len(".assembly.yaml")]
        for path in Mocks.LIBRARY.rglob("*.assembly.yaml")
    )


@pytest.mark.base
@pytest.mark.parametrize("library_path", library_paths())
def test_every_mock_assembly_passes_the_library_check(library_path: str) -> None:
    """Descriptions, ranges, the test contract, ports, units and order: nothing missing anywhere."""
    resolver = mock_resolver()

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
            class: tests.assemblies.mock_components.MockHeater
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
            weather: {into: [Nobody], partner: MockWeather}
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
        "'power_in_watt' is in KILOWATT, the field MockHeaterConfig.power_in_watt it feeds in WATT",
        "lowers into 'Nobody', which is no member",
        "a placeholder for 'undeclared', which is no port",
        "carries no test contract",
    ):
        assert expected in text, expected
    assert all(problem.startswith("broken/everything.assembly.yaml:") for problem in found)
    with pytest.raises(EnergySystemAssemblyError, match=r"EF-75 .*has \d+ problems"):
        require_valid(assembly, resolver, CheckStrength.LIBRARY)


@pytest.mark.base
def test_the_test_contract_is_checked_from_the_file(tmp_path: Path) -> None:
    """D24 from the file: at least one ``monotone``, units of ``lt.Units``, names resolving to members and presets.

    Which outputs need a bounds entry, a bounded output's unit and the KPIs a member reports the
    constructed members say; the assembly test harness checks those in its isolation run, so the
    library check, which constructs nothing, reports none of them.
    """
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
            class: tests.assemblies.mock_components.MockTank
            preset: standard
            config: {volume_in_liter: {$param: volume_in_liter}}
        tests:
          bounds:
            - {output: Tank.WaterTemperature, unit: KELVIN, min: 0, max: 400}
            - {output: Pump.Flow, unit: METER_PER_SECOND, min: 0, max: 1}
            - {output: Tank.HeatLoss, unit: FURLONG, min: 0, max: 1}
            - {kpi: No such KPI, member: Pipe, max: 1}
          monotone: []
          expect:
            - {preset: missing, kpi: Standby heat losses, member: Tank, max: 1}
        """,
    )
    resolver = library.resolver()

    found = "\n".join(check_assembly(resolver.resolve("broken/unbounded", "test"), resolver, CheckStrength.LIBRARY))

    assert "the bounds entry names 'Pump.Flow', but 'Pump' is no member" in found
    assert "the unit 'FURLONG' is no member of lt.Units" in found
    assert "the entry names the member 'Pipe', which does not exist" in found
    assert "no monotone entry" in found
    assert "the preset 'missing', which the assembly does not offer" in found
    assert "KELVIN" not in found and "No such KPI" not in found


@pytest.mark.base
def test_the_resolver_searches_the_library_directories_and_hashes_what_it_reads() -> None:
    """A library path resolves to one file; the record carries the sha256 of its bytes."""
    resolved = mock_resolver().resolve("mock/pv_array", "test")

    assert resolved.file == Mocks.LIBRARY / "mock" / "pv_array.assembly.yaml"
    assert resolved.sha256 == hashlib.sha256(resolved.file.read_bytes()).hexdigest()
    assert resolved.label == "mock/pv_array.assembly.yaml"


@pytest.mark.base
def test_a_name_found_in_two_places_is_refused(tmp_path: Path) -> None:
    """A library path is never shadowed."""
    library = Library(tmp_path)
    library.add("mock/pv_array", (Mocks.LIBRARY / "mock" / "pv_array.assembly.yaml").read_text(encoding="utf-8"))

    with pytest.raises(EnergySystemAssemblyError, match="EF-72 .*found in two places"):
        library.resolver().resolve("mock/pv_array", "imports.pv")


@pytest.mark.base
def test_an_unknown_assembly_is_refused_with_the_ones_available() -> None:
    """The message lists the library and suggests the near miss."""
    with pytest.raises(EnergySystemAssemblyError, match="EF-71 .*Did you mean: mock/pv_array"):
        mock_resolver().resolve("mock/pv_arrays", "imports.pv")
    with pytest.raises(EnergySystemAssemblyError, match="EF-71 .*not an assembly path"):
        mock_resolver().resolve("../pv", "imports.pv")


@pytest.mark.base
def test_the_default_resolver_reads_the_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``HISIM_ASSEMBLY_PATH`` adds directories after the repository's library; a missing one is refused."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, os.pathsep.join([str(Mocks.LIBRARY)]))
    resolved = AssemblyResolver.default().resolve("mock/pv_array", "test")
    assert resolved.file == Mocks.LIBRARY / "mock" / "pv_array.assembly.yaml"

    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(tmp_path / "missing"))
    with pytest.raises(EnergySystemAssemblyError, match="EF-71 .*does not exist"):
        AssemblyResolver.default()


#: A storage water heater of the mockup's shape (``dhw/storage_water_heater``) on mock classes: the
#: member ``Heater`` is an electric heater in one option and a gas boiler in the other.
TWO_OPTION_HEATER = """
schema_version: 4
kind: assembly
name: dhw/two_heaters
description: A heater that is electric or gas-fired.
parameters:
  energy_carrier: {type: enum, values: [electricity, natural_gas], default: electricity, description: Carrier.}
presets:
  standard: {}
components:
  Tank:
    class: tests.assemblies.mock_components.MockTank
    preset: standard
variants:
  heater:
    selected_by: energy_carrier
    options:
      immersion:
        when: [electricity]
        components:
          Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard}
      burner:
        when: [natural_gas]
        components:
          Heater: {class: tests.assemblies.mock_components.MockBoiler, preset: condensing}
interface:
  provides:
    electricity: {output: Heater.ElectricityInput, active_when: {energy_carrier: [electricity]}}
tests:
  bounds:
{bounds}
  monotone: []
"""

#: Every energy or temperature output both heaters and the tank declare.
EVERY_BOUND = [
    "Tank.WaterTemperature, unit: CELSIUS",
    "Tank.HeatLoss, unit: WATT",
    "Heater.ThermalPower, unit: WATT",
    "Heater.ElectricityInput, unit: WATT",
    "Heater.SupplyTemperatureDhw, unit: CELSIUS",
    "Heater.ThermalPowerDhw, unit: WATT",
    "Heater.FuelUse, unit: WATT_HOUR",
    "Heater.FlueLoss, unit: WATT",
]


def two_heaters(tmp_path: Path, bounds: list) -> Tuple[list, Any]:
    """The library problems of the two-option heater with the given bounds entries, and its test contract."""
    library = Library(tmp_path)
    text = TWO_OPTION_HEATER.replace(
        "{bounds}", "\n".join(f"    - {{output: {item}, min: -100000, max: 100000}}" for item in bounds)
    )
    library.add("dhw/two_heaters", text)
    resolver = library.resolver()
    assembly = resolver.resolve("dhw/two_heaters", "test")
    return check_assembly(assembly, resolver, CheckStrength.LIBRARY), assembly.model.tests


def option_members(option: str) -> dict:
    """The constructed members of one option of the two-option heater, as an isolation run of it builds them."""
    parameters = SimulationParameters.one_day_only(2021, 900)
    heater: Any = (
        MockHeater(parameters, MockHeaterConfig.preset_standard("Heater"))
        if option == "immersion"
        else MockBoiler(parameters, MockBoilerConfig.preset_condensing("Heater"))
    )
    return {"Tank": MockTank(parameters, MockTankConfig.preset_standard("Tank")), "Heater": heater}


@pytest.mark.base
def test_a_member_in_two_options_is_checked_against_each_options_class(tmp_path: Path) -> None:
    """hisim-lt0b.13: a bounds entry naming an output only one option's member has fails in the other option.

    The outputs exist on the constructed members only, so the library check, which constructs nothing,
    finds no problem; the member contract of the harness checks every option's sample on its own
    constructed members, and a bounds entry naming an output the option's class does not have fails
    there, as the run would fail it ("no result column"). The provided port is active only with
    electricity, so the boiler owes it nothing.
    """
    listed, tests = two_heaters(tmp_path, EVERY_BOUND)
    assert not listed, listed

    for option, missing in (
        ("burner", ["ThermalPower", "ElectricityInput"]),
        ("immersion", ["SupplyTemperatureDhw", "ThermalPowerDhw", "FuelUse", "FlueLoss"]),
    ):
        contract = MemberContract(tests)
        contract.check_outputs(option_members(option))
        assert [(violation.subject, violation.rule) for violation in contract.violations] == [
            (f"Heater.{output}", "bounds output") for output in missing
        ], option
        assert all(
            "which is no output of the constructed Heater" in violation.message for violation in contract.violations
        )


@pytest.mark.base
def test_an_output_only_one_option_declares_must_be_bounded_for_that_option(tmp_path: Path) -> None:
    """Every energy or temperature output of every option's constructed member owes a bounds entry."""
    listed, tests = two_heaters(tmp_path, [item for item in EVERY_BOUND if not item.startswith("Heater.ThermalPower,")])
    assert not listed, listed

    contract = MemberContract(tests)
    contract.check_outputs(option_members("immersion"))

    assert "the WATT output 'Heater.ThermalPower' (HEATING) has no bounds entry." in [
        violation.message for violation in contract.violations
    ]


@pytest.mark.base
def test_monotone_is_required_only_beside_a_numeric_parameter(tmp_path: Path) -> None:
    """D24 as amended: a bare connection with no numeric parameter has nothing to sweep."""
    library = Library(tmp_path)
    for name, parameters in (("x/bare", "{}"), ("x/numeric", "{p: {type: float, unit: WATT, default: 1, "
                                                             "range: {min: 0, max: 2}, description: P.}}")):
        library.add(
            name,
            f"""
            schema_version: 4
            kind: assembly
            name: {name}
            description: A weather station.
            parameters: {parameters}
            presets: {{standard: {{}}}}
            components:
              Weather: {{class: tests.assemblies.mock_components.MockWeather, preset: standard}}
            tests:
              bounds: [{{output: Weather.TemperatureOutside, unit: CELSIUS, min: -30, max: 50}}]
              monotone: []
            """,
        )
    resolver = library.resolver()

    assert not check_assembly(resolver.resolve("x/bare", "test"), resolver, CheckStrength.LIBRARY)
    assert [
        problem.split(": ", 1)[1]
        for problem in check_assembly(resolver.resolve("x/numeric", "test"), resolver, CheckStrength.LIBRARY)
    ] == ["the test contract has no monotone entry; one is required while the assembly has a numeric parameter (p)."]


@pytest.mark.base
def test_the_contract_check_of_observers_controllables_and_priorities(tmp_path: Path) -> None:
    """From the file: an observer port's member carries its placeholder, ``via`` names a need, priorities a list.

    Whether the observer's constructed component declares feeds, and whether a ``target_input`` is an input of
    the constructed member, the build decides (test_assembly_selectors.py).
    """
    library = Library(tmp_path)
    library.add(
        "control/broken",
        """
        schema_version: 4
        kind: assembly
        name: control/broken
        description: Broken observers.
        parameters:
          order: {type: string, default: x, description: Not a list.}
        presets: {standard: {}}
        components:
          EMS:
            class: tests.assemblies.mock_components.MockEnergyManager
            preset: optimize_own_consumption
          Tank:
            class: tests.assemblies.mock_components.MockTank
            preset: standard
            inputs: [{$observes: flows}]
          Battery:
            class: tests.assemblies.mock_components.MockBattery
            preset: sized_to_pv
        interface:
          observes:
            flows: {into: [EMS, Tank], default: declared}
          actuates:
            priorities: {$param: order}
          provides:
            charge: {output: Battery.AcBatteryPowerUsed, controllable: {target_input: NoSuchInput}}
            heat: {output: Tank.HeatLoss, controllable: {via: nothing}}
        tests:
          bounds: []
          monotone: []
        """,
    )
    resolver = library.resolver()
    listed = [
        problem.split(": ", 1)[1]
        for problem in check_assembly(resolver.resolve("control/broken", "test"), resolver, CheckStrength.EXPANSION)
    ]

    for expected in (
        "'EMS' carries 0 '{$observes: flows}' placeholders; the feeds of the observer port 'flows' land at exactly "
        "one.",
        "the priorities of 'priorities' are taken from 'order', which is no list parameter of the assembly.",
        "the provided output 'heat' is controllable via 'nothing', which is no need of the assembly; 'via' names the "
        "need whose binding lowers to the L1 controller's modifier.",
    ):
        assert expected in listed, (expected, listed)
    assert not any("NoSuchInput" in problem or "default_feeds" in problem for problem in listed), listed
