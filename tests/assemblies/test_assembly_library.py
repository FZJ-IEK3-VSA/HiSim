"""The library: the resolver, the library check (every problem at once), and ``describe``."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Tuple

import pytest

from hisim import cli
from hisim import loadtypes as lt
from hisim.config import ComponentID, ConfigBase, preset, sized_field
from hisim.config.sizing import declared_field_unit
from hisim.energy_system.assemblies.library import check_assembly
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemAssemblyError
from tests.assemblies.helpers import OCCUPANCY, WEATHER, Library, Mocks, expand_text, site

#: A valid assembly every case of the library check changes in one place.
BASE = f"""\
schema_version: 4
kind: assembly
name: test/base
parameters:
  volume_in_liter: {{type: float, unit: LITER, default: 150, range: {{min: 50, max: 500}}, description: Volume.}}
components:
  Tank:
    class: {Mocks.CLASSES}.MockTank
    preset: standard
    config:
      volume_in_liter: {{$param: volume_in_liter}}
    inputs:
      - {{$port: demand}}
interface:
  needs:
    demand: {{into: [Tank], partner: MockOccupancy}}
tests:
  bounds: [{{output: Tank.HeatLoss, unit: WATT, min: 0}}]
  monotone: [{{parameter: volume_in_liter, kpi: Standby heat losses, member: Tank, direction: increasing}}]
"""

#: A parameter line to add, for the parameter cases.
PARAMETERS = "parameters:\n"
PARAMETER = (
    "  volume_in_liter: {type: float, unit: LITER, default: 150, range: {min: 50, max: 500}, description: Volume.}\n"
)


def problems_of(tmp_path: Path, text: str, library_path: str = "test/base") -> str:
    """The library check's problems of one file, joined."""
    library = Library(tmp_path)
    library.add(library_path, text)
    return " | ".join(check_assembly(library.resolver().resolve(library_path, "test")))


def with_parameter(line: str) -> str:
    """The base assembly with one more parameter."""
    return BASE.replace(PARAMETER, PARAMETER + f"  {line}\n")


@pytest.mark.assemblies
def test_the_base_case_passes(tmp_path: Path) -> None:
    """Catches a base case that would make every refusal below vacuous."""
    assert problems_of(tmp_path, BASE) == ""


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("text", "names"),
    [
        (BASE.replace("name: test/base", "name: test/other"), ("test/other", "test/base")),
        (BASE.replace(" description: Volume.}", "}"), ("volume_in_liter", "no description")),
        (BASE.replace(" default: 150,", ""), ("volume_in_liter", "no default")),
        (BASE.replace(" unit: LITER,", ""), ("volume_in_liter", "no unit")),
        (BASE.replace("unit: LITER", "unit: LITRE"), ("LITRE", "lt.Units")),
        (BASE.replace(" range: {min: 50, max: 500},", ""), ("volume_in_liter", "no range")),
        (BASE.replace("default: 150", "default: 900"), ("default of 'volume_in_liter'", "outside the range")),
        (
            with_parameter(
                "installation_year: "
                "{type: int, unit: ANY, default: 2020, range: {min: 2000, max: 2050}, description: y.}"
            ),
            ("'installation_year' is reserved",),
        ),
        (with_parameter("label: {type: string, unit: WATT, default: x, description: l.}"), ("label", "only a number")),
        (with_parameter("mode: {type: enum, default: a, description: m.}"), ("mode", "an enum, and only an enum")),
        (with_parameter("unused: {type: bool, default: true, description: u.}"), ("unused", "feeds no field")),
        (
            with_parameter("mode: {type: enum, values: [none, eco], default: eco, description: m.}"),
            ("the enum 'mode' lists 'none', which spells no value",),
        ),
        (BASE.replace("unit: LITER", "unit: WATT"), ("EF-78", "volume_in_liter", "LITER", "WATT")),
        (
            with_parameter("count: {type: int, unit: ANY, default: 2, range: {min: 1.5, max: 5}, description: c.}"),
            ("the int parameter 'count' has the range [1.5, 5], whose bounds are not both integers",),
        ),
        (
            with_parameter("count: {type: int, unit: ANY, default: 2.5, range: {min: 1, max: 5}, description: c.}"),
            ("the default of 'count' is 2.5 is not an integer",),
        ),
    ],
)
def test_the_parameter_rules(tmp_path: Path, text: str, names: Tuple[str, ...]) -> None:
    """Catches a parameter without its documentation, unit, range or default, or one that does nothing."""
    problems = problems_of(tmp_path, text)
    for name in names:
        assert name in problems, f"{name!r} is not in: {problems}"


@pytest.mark.assemblies
def test_a_parameter_feeding_a_field_without_a_unit_is_refused(tmp_path: Path) -> None:
    """Catches D16 b: a numeric parameter feeding a field that declares no unit, or a nested value."""
    pv = textwrap.dedent(
        f"""\
        schema_version: 4
        kind: assembly
        name: test/pv
        parameters:
          shading: {{type: float, unit: ANY, default: 1, range: {{min: 0, max: 1}}, description: Shading.}}
          tilt: {{type: float, unit: DEGREES, default: 30, range: {{min: 0, max: 90}}, description: Tilt.}}
        components:
          PV:
            class: {Mocks.CLASSES}.MockPVSystem
            preset: rooftop
            config:
              shading_factor: {{$param: shading}}
              nested: {{tilt: {{$param: tilt}}}}
        tests:
          bounds: []
          monotone: [{{parameter: tilt, kpi: PV production, member: PV, direction: increasing}}]
        """
    )
    problems = problems_of(tmp_path, pv, "test/pv")
    assert "EF-79: the parameter 'shading' feeds MockPVSystemConfig.shading_factor" in problems
    assert "EF-79: the numeric parameter 'tilt' feeds 'config.nested.tilt'" in problems


@pytest.mark.assemblies
def test_a_value_of_an_enum_that_selects_nothing_of_its_own_is_refused(tmp_path: Path) -> None:
    """Catches a knob value that behaves exactly like another one (§2.6: every value selects something)."""
    text = with_parameter("mode: {type: enum, values: [eco, normal, boost], default: eco, description: m.}").replace(
        "demand: {into: [Tank], partner: MockOccupancy}",
        "demand: {into: [Tank], partner: MockOccupancy, required_when: {mode: [eco, normal]}}",
    )
    problems = problems_of(tmp_path, text)
    assert "the value 'normal' of 'mode' selects nothing 'eco' does not" in problems
    assert "'boost'" not in problems


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("text", "names"),
    [
        (BASE + "constraints: [{exactly_one_of: [volume_in_liter, power]}]\n", ("exactly_one_of names power",)),
        (
            BASE.replace(
                PARAMETER,
                PARAMETER
                + "  low: {type: float, unit: LITER, default: none, range: {min: 1, max: 2}, description: l.}\n"
                + "  high: {type: float, unit: LITER, default: none, range: {min: 1, max: 2}, description: h.}\n",
            )
            + "constraints: [{exactly_one_of: [low, volume_in_liter]}, {exactly_one_of: [volume_in_liter, high]}]\n",
            ("'volume_in_liter' is named by the exactly_one_of constraints 0 and 1",),
        ),
        (BASE.replace("{$param: volume_in_liter}", "{$param: volume}"), ("'volume', which is no parameter",)),
        (
            BASE.replace("      - {$port: demand}", "      - {$port: demand}\n      - Heater"),
            ("'Heater', which is no member",),
        ),
        (BASE.replace("      - {$port: demand}", "      - Occupancy"), ("carries no '{$port: demand}' placeholder",)),
        (BASE.replace("into: [Tank]", "into: [Boiler]"), ("'Boiler', which is no member",)),
        (
            BASE.replace("      - {$port: demand}", "      - {$port: demand}\n      - {$port: heat}"),
            ("placeholder for 'heat'",),
        ),
        (
            BASE.replace("partner: MockOccupancy}", "partner: MockOccupancy, active_when: {colour: [red]}}"),
            ("'colour'",),
        ),
        (BASE.replace("preset: standard", "preset: standard\n    display: '{size}'"), ("names 'size'",)),
        (BASE.replace("preset: standard", "preset: standard\n    display: '{volume_in_liter:d}'"), ("does not fit",)),
        (BASE.replace(f"{Mocks.CLASSES}.MockTank", f"{Mocks.CLASSES}.Nothing"), ("does not load",)),
        (
            BASE.replace(
                "partner: MockOccupancy}",
                "partner: MockOccupancy}\n  provides:\n    loss: {output: "
                "Tank.HeatLoss, controllable: {via: nothing}}",
            ),
            ("controllable via 'nothing', which is no need",),
        ),
        (
            BASE.replace(
                "partner: MockOccupancy}",
                "partner: MockOccupancy}\n  provides:\n    gas: {carrier: natural_gas, meter: Tank}",
            ),
            ("the port 'gas' lowers into 'Tank', which carries no '{$port: gas}' placeholder",),
        ),
        (
            BASE.replace(
                "partner: MockOccupancy}",
                "partner: MockOccupancy}\n  provides:\n    peak: {fact: pv_peak_power_in_watt, member: Tank}",
            ),
            ("does not declare in its SIZING_CONTRIBUTIONS",),
        ),
        (
            BASE.replace("partner: MockOccupancy}", "partner: MockOccupancy}\n    loop: {circuit: dhw, member: Pump}"),
            ("the port 'loop' names 'Pump', which is no member",),
        ),
    ],
)
def test_the_member_and_port_rules(tmp_path: Path, text: str, names: Tuple[str, ...]) -> None:
    """Catches a reference out of the assembly, a placeholder and its port apart, a broken display template."""
    problems = problems_of(tmp_path, text)
    for name in names:
        assert name in problems, f"{name!r} is not in: {problems}"


#: An assembly with an internal variant, which the variant cases change.
VARIANT = (
    BASE.replace(PARAMETER, PARAMETER + "  heated: {type: bool, default: true, description: Heated.}\n")
    + f"""\
variants:
  heating:
    selected_by: heated
    options:
      on:
        when: [true]
        components:
          Heater: {{class: {Mocks.CLASSES}.MockHeater, preset: standard}}
      off:
        when: [false]
"""
)


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("text", "names"),
    [
        (VARIANT.replace("when: [false]", "when: [true]"), ("covered twice", "no option covers heated=False")),
        (VARIANT.replace("selected_by: heated", "selected_by: volume_in_liter"), ("no enum or bool parameter",)),
        (VARIANT.replace("Heater: {", "Tank: {"), ("'Tank', a member outside the variant",)),
        (
            VARIANT.replace(
                "      off:\n        when: [false]\n",
                "      off:\n        when: [false]\n        components:\n          Heater: "
                f"{{class: {Mocks.CLASSES}.MockTank, preset: standard}}\n",
            ),
            ("'Heater' is written with two classes",),
        ),
        (VARIANT.replace("when: [false]", "when: [0]"), ("heated=0 is not allowed", "no option covers heated=False")),
        (
            VARIANT.replace("partner: MockOccupancy}", "partner: MockOccupancy, active_when: {heated: [1]}}"),
            ("lists heated=1, which it does not allow",),
        ),
        (
            VARIANT.replace("heated: {type: bool, default: true", "heated: {type: bool, default: none"),
            ("'heated' selects the variant 'heating', so its default is one of its values, not None",),
        ),
    ],
)
def test_the_variant_rules(tmp_path: Path, text: str, names: Tuple[str, ...]) -> None:
    """Catches options that do not partition their selector, or one member name with two classes."""
    assert problems_of(tmp_path / "valid", VARIANT) == ""
    problems = problems_of(tmp_path, text)
    for name in names:
        assert name in problems, f"{name!r} is not in: {problems}"


@pytest.mark.assemblies
def test_a_port_into_a_member_an_active_option_lacks_is_refused(tmp_path: Path) -> None:
    """Catches a need into a member that one option leaves out while the port is still active there."""
    text = VARIANT.replace(
        "    demand: {into: [Tank], partner: MockOccupancy}",
        "    demand: {into: [Tank], partner: MockOccupancy}\n    signal: {into: [Heater], partner: MockController}",
    ).replace(
        "Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard}",
        "Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard, inputs: [{$port: signal}]}",
    )
    problems = problems_of(tmp_path, text)
    assert "the option 'off' of the variant 'heating' does not have" in problems
    switched_off = text.replace("partner: MockController}", "partner: MockController, active_when: {heated: [true]}}")
    assert problems_of(tmp_path / "off", switched_off) == ""


@pytest.mark.assemblies
def test_a_provided_output_of_a_member_an_active_option_lacks_is_refused(tmp_path: Path) -> None:
    """Catches a provided output naming a member one option leaves out while the port is still provided there."""
    text = VARIANT.replace(
        "    demand: {into: [Tank], partner: MockOccupancy}\n",
        "    demand: {into: [Tank], partner: MockOccupancy}\n  provides:\n    heat: {output: Heater.ThermalPower}\n",
    )
    problems = problems_of(tmp_path, text)
    assert "the port 'heat' names 'Heater', which the option 'off' of the variant 'heating' does not have" in problems
    switched_off = text.replace("Heater.ThermalPower}", "Heater.ThermalPower, active_when: {heated: [true]}}")
    assert problems_of(tmp_path / "off", switched_off) == ""


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("member", "port"),
    [
        ("PV: {class: CLASSES.MockPVSystem, preset: rooftop}", "peak: {fact: pv_peak_power_in_watt, member: PV}"),
        (
            "PV: {class: CLASSES.MockGasMeter, preset: standard, inputs: [{$port: peak}]}",
            "peak: {carrier: natural_gas, meter: PV}",
        ),
    ],
    ids=["fact", "fuel"],
)
def test_a_provision_whose_member_an_option_drops_is_refused(tmp_path: Path, member: str, port: str) -> None:
    """Catches a provided fact or fuel whose member one option leaves out while the port is still provided there."""
    text = VARIANT.replace(
        "    demand: {into: [Tank], partner: MockOccupancy}\n",
        f"    demand: {{into: [Tank], partner: MockOccupancy}}\n  provides:\n    {port}\n",
    ).replace(
        "Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard}",
        "Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard}\n          "
        + member.replace("CLASSES", Mocks.CLASSES),
    )
    problems = problems_of(tmp_path, text)
    assert "the port 'peak' names 'PV', which the option 'off' of the variant 'heating' does not have" in problems
    switched_off = text.replace(port[:-1], port[:-1] + ", active_when: {heated: [true]}")
    assert problems_of(tmp_path / "off", switched_off) == ""


@pytest.mark.assemblies
def test_an_observer_port_into_a_member_an_active_option_lacks_is_refused(tmp_path: Path) -> None:
    """Catches an observer port whose member one option leaves out while the port is still active there."""
    text = VARIANT.replace(
        "    demand: {into: [Tank], partner: MockOccupancy}\n",
        "    demand: {into: [Tank], partner: MockOccupancy}\n"
        "  observes:\n    reading: {into: [Meter], default: declared}\n",
    ).replace(
        "Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard}",
        "Heater: {class: tests.assemblies.mock_components.MockHeater, preset: standard}\n          "
        f"Meter: {{class: {Mocks.CLASSES}.MockEnergyManager, preset: optimize_own_consumption}}",
    )
    problems = problems_of(tmp_path, text)
    assert "the port 'reading' names 'Meter', which the option 'off' of the variant 'heating' does not have" in problems
    switched_off = text.replace("default: declared}", "default: declared, active_when: {heated: [true]}}")
    assert problems_of(tmp_path / "off", switched_off) == ""


@pytest.mark.assemblies
def test_one_member_name_in_the_options_of_two_variants_is_refused(tmp_path: Path) -> None:
    """Catches two selected options of two variants writing one member, which would make one component of two."""
    text = VARIANT.replace(
        PARAMETER, PARAMETER + "  level: {type: enum, values: [low, high], default: low, description: Level.}\n"
    ) + (
        "  mode:\n    selected_by: level\n    options:\n      low:\n        when: [low]\n        components:\n"
        f"          Heater: {{class: {Mocks.CLASSES}.MockHeater, preset: standard}}\n"
        "      high:\n        when: [high]\n"
    )
    problems = problems_of(tmp_path, text)
    assert "'Heater' is written in the options of the variants 'heating' and 'mode'" in problems


@pytest.mark.assemblies
def test_a_member_module_raising_at_import_is_one_problem_and_the_listing_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a module that raises something other than an ImportError aborting the whole library check."""
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "raising_at_import_module.py").write_text("raise RuntimeError('boom at import')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(modules))
    text = BASE.replace(f"{Mocks.CLASSES}.MockTank", "raising_at_import_module.Tank").replace(
        " description: Volume.}", "}"
    )
    problems = problems_of(tmp_path, text)
    assert "the class of 'Tank' does not load: boom at import" in problems
    assert "the parameter 'volume_in_liter' has no description" in problems


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("text", "names"),
    [
        (BASE[: BASE.index("tests:")], ("no test contract",)),
        (
            BASE.replace(
                "  monotone: "
                "[{parameter: volume_in_liter, kpi: Standby heat losses, member: Tank, direction: increasing}]",
                "  monotone: []",
            ),
            ("no monotone entry", "volume_in_liter"),
        ),
        (BASE.replace("unit: WATT, min: 0", "unit: WATTS, min: 0"), ("'WATTS' is no member of lt.Units",)),
        (BASE.replace("output: Tank.HeatLoss", "output: Pump.HeatLoss"), ("'Pump', which does not exist",)),
        (BASE.replace("member: Tank, direction", "member: Pump, direction"), ("'Pump', which does not exist",)),
        (
            BASE.replace("parameter: volume_in_liter, kpi", "parameter: colour, kpi"),
            ("'colour', which is no numeric parameter",),
        ),
        (BASE + "  expect: [{kpi: Standby heat losses, member: Pump, max: 5}]\n", ("'Pump', which does not exist",)),
    ],
)
def test_the_test_contract_rules(tmp_path: Path, text: str, names: Tuple[str, ...]) -> None:
    """Catches an assembly without its test contract (D24) or one naming members and parameters it lacks."""
    problems = problems_of(tmp_path, text)
    for name in names:
        assert name in problems, f"{name!r} is not in: {problems}"


@pytest.mark.assemblies
def test_the_check_lists_every_problem_at_once_and_the_expansion_runs_it(tmp_path: Path) -> None:
    """Catches a check that stops at the first problem, or an expansion that skips the check."""
    library = Library(tmp_path)
    library.add("test/base", BASE.replace(" description: Volume.}", "}").replace("unit: LITER", "unit: WATT"))
    with pytest.raises(EnergySystemAssemblyError, match="EF-75") as refusal:
        expand_text(site(WEATHER, OCCUPANCY, imports="tank: {assembly: test/base}"), library.resolver())
    assert "2 problems" in str(refusal.value)
    assert "test/base.assembly.yaml:5" in str(refusal.value)


@pytest.mark.assemblies
def test_a_sized_field_and_a_plain_field_declare_their_units_alike() -> None:
    """Catches ``sized_field(unit=)`` and ``field(metadata={"unit"})`` being read differently."""
    import dataclasses  # pylint: disable=import-outside-toplevel

    @dataclasses.dataclass
    class UnitConfig(ConfigBase):
        """A configuration with one sized and one plain field."""

        MAIN_CLASS = "tests.assemblies.mock_components.MockTank"

        component_id: ComponentID
        power: float = sized_field(rule=1000.0, default=500.0, unit=lt.Units.WATT)
        volume: float = dataclasses.field(default=1.0, metadata={"unit": lt.Units.LITER})
        bare: float = 1.0

        @preset
        @classmethod
        def preset_standard(cls, name: str) -> "UnitConfig":
            """Defaults."""
            return cls(component_id=ComponentID(name))

    assert declared_field_unit(UnitConfig, "power") is lt.Units.WATT
    assert declared_field_unit(UnitConfig, "volume") is lt.Units.LITER
    assert declared_field_unit(UnitConfig, "bare") is None


@pytest.mark.assemblies
def test_the_resolver_refuses_a_missing_a_doubled_and_a_malformed_assembly(tmp_path: Path) -> None:
    """Catches a library path shadowed by a second directory, or a missing one failing late."""
    first, second = Library(tmp_path / "a"), Library(tmp_path / "b")
    first.add("test/base", BASE)
    second.add("test/base", BASE)
    with pytest.raises(EnergySystemAssemblyError, match="EF-72"):
        AssemblyResolver([first.directory, second.directory]).resolve("test/base", "here")
    with pytest.raises(EnergySystemAssemblyError, match="EF-71") as missing:
        AssemblyResolver([first.directory]).resolve("test/bse", "here")
    assert "test/base" in str(missing.value)
    with pytest.raises(EnergySystemAssemblyError, match="EF-71"):
        AssemblyResolver([first.directory]).resolve("base.assembly.yaml", "here")
    with pytest.raises(EnergySystemAssemblyError, match="EF-71"):
        AssemblyResolver([tmp_path / "nowhere"])


@pytest.mark.assemblies
def test_the_resolver_searches_the_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catches ``HISIM_ASSEMBLY_PATH`` not being searched."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    assert AssemblyResolver.default().resolve("mock/labelled_array", "here").path == "mock/labelled_array"


@pytest.mark.assemblies
def test_describe_prints_the_interface_the_parameters_and_the_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Catches ``describe <family>/<name>`` leaving out a port, a parameter's unit or the test contract."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    assert cli.main(["energy-system", "describe", "mock/variant_heater"]) == 0
    out = capsys.readouterr().out
    for line in (
        "mock/variant_heater — A mock electric heater and its thermostat.",
        "needs: tank_temperature need from MockTank into Controller; required when with_thermostat in [True]",
        "needs: ems_modifier     need from MockEms into Controller; optional (bind:, optional-bind: or none:), "
        "active when with_thermostat in [True]",
        "provides: heat          provides Heater.ThermalPower; provided",
        "power_in_watt           float  WATT  range [500, 6000]  default 2000 — Rated power.",
        "variant thermostat, selected by with_thermostat:",
        "fitted when [True]: Controller (tests.assemblies.mock_components.MockController)",
        "test contract: 2 bounds, 1 monotone, 0 expect",
        "monotone  power_in_watt rises: Heater energy of Heater increasing",
        "bounds    Heater.ThermalPower [WATT]: [0, 6000]",
    ):
        assert line in out, f"{line!r} is not in:\n{out}"
    assert cli.main(["energy-system", "describe", "mock/labelled_array"]) == 0
    out = capsys.readouterr().out
    for line in (
        "bounds    PV production of PVSystem: [0, …]",
        "expect    at the defaults: PV production of PVSystem [0, 100]",
    ):
        assert line in out, f"{line!r} is not in:\n{out}"
    assert cli.main(["energy-system", "describe", "pv/array"]) == 0
    out = capsys.readouterr().out
    for line in (
        "pv/array — One PV array on the roof, with its inverter (pvlib, CEC databases).",
        "needs: weather          need from Weather into PVSystem; required",
        "exactly_one_of: [power_in_watt, share_of_maximum_pv_potential]",
        "monotone  power_in_watt rises: PV production of the building (derived) increasing",
        "expect    at the defaults: PV production of the building (derived) [3, 7]",
    ):
        assert line in out, f"{line!r} is not in:\n{out}"
    assert cli.main(["energy-system", "describe", "mock/nothing"]) != 0


@pytest.mark.assemblies
def test_facts_lists_an_imports_parameters_and_selected_variants_as_knobs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Catches ``facts`` on a version-4 file showing no knobs although its imports' parameters drive it."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    assert cli.main(["energy-system", "facts", str(Mocks.HOUSE)]) == 0
    knobs = capsys.readouterr().out.split("knobs", 1)[1].split("facts provided", 1)[0]
    assert "(none)" not in knobs.split("imports.heater", 1)[0]
    for text in (
        "imports.pv.east",
        "imports.pv.west",
        "mock/labelled_array",
        "given: azimuth_in_degree=270, facing='west', power_in_watt=3000",
        "resolved: azimuth_in_degree=90, tilt_in_degree=30, power_in_watt=5000, facing='east'",
        "imports.tank",
        "given: volume_in_liter=200",
        "imports.heater",
        "variant thermostat: fitted",
    ):
        assert text in knobs, f"{text!r} is not in:\n{knobs}"
