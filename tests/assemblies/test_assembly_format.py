"""Reading assembly files and checking them against their schema (``assemblies_spec.md`` §2, §9.4).

The mockup of the spec (``tests/assemblies/mockup``, a snapshot of
``roadmap/declarative_energy_systems/assemblies_mockup`` at docs/assemblies d40ff770, PR #881) is the
shape the format must accept. Since that commit every mockup assembly carries its test contract and a
``range`` on every numeric parameter, and the constructor calls are spelled as the format reads them,
so the whole mockup validates against the assembly schema and reads into the model without a defect.

Run through the library check, it lists exactly what the repository still owes it, pinned here by
kind so the lists shrink as the classes catch up: the real classes that declare no ``CLASS_INTERFACE``
yet (hisim-lt0b.12), the classes that do not exist yet (†), and the carriers that are no
``lt.EnergyBalanceCarrier`` yet (†: ``wood_logs``, ``lpg``, ``hvo``). What the expansion refuses by
design until a later step (``EF-7L``: ``$fact``, ``$derived``, ``many: true``, fact exports) is pinned
beside them.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import jsonschema
import pytest
import yaml

from hisim.energy_system.assemblies.library import CheckStrength, check_assembly
from hisim.energy_system.assemblies.model import MonotoneDirection
from hisim.energy_system.assemblies.parameters import ParameterSubstitution
from hisim.energy_system.assemblies.reader import AssemblyReader
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.assemblies.schema import AssemblySchemaBuilder
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.imports_model import PortKind, Selection, Selector
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import build_schema, build_structural_schema, default_schema_path, render_schema
from tests.assemblies.helpers import Fixtures


class MockupOwes:
    """What the library check of the mockup lists, by kind: what the repository owes the mockup."""

    #: Real classes the mockup names that declare no ``CLASS_INTERFACE`` yet (hisim-lt0b.12).
    WITHOUT_CLASS_INTERFACE: Set[str] = {
        "hisim.components.advanced_battery_bslib.Battery",
        "hisim.components.advanced_ev_battery_bslib.CarBattery",
        "hisim.components.controller_l1_generic_ev_charge.L1Controller",
        "hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem",
        "hisim.components.electricity_meter.ElectricityMeter",
        "hisim.components.fuel_meter.FuelMeter",
        "hisim.components.gas_meter.GasMeter",
        "hisim.components.generic_boiler.GenericBoiler",
        "hisim.components.generic_car.Car",
        "hisim.components.generic_pv_system.PVSystem",
        "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib",
        "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerDHW",
        "hisim.components.more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibControllerSpaceHeating",
        "hisim.components.simple_air_conditioner.SimpleAirConditioner",
        "hisim.components.simple_air_conditioner.SimpleAirConditionerController",
        "hisim.components.simple_water_storage.SimpleDHWStorage",
        "hisim.components.simple_water_storage.SimpleHotWaterStorage",
        "hisim.components.solar_thermal_system.SolarThermalSystem",
        "hisim.components.solar_thermal_system.SolarThermalSystemController",
    }

    #: Classes the mockup names that do not exist yet (†); they import from no module.
    NOT_YET_WRITTEN: Set[str] = {
        "hisim.components.gas_cooker.GasCooker",
        "hisim.components.immersion_heater.ImmersionHeater",
        "hisim.components.immersion_heater.StorageHeaterController",
        "hisim.components.mechanical_ventilation.MechanicalVentilation",
        "hisim.components.wood_stove.WoodStove",
        "hisim.components.wood_stove.WoodStoveController",
    }

    #: Carriers the mockup writes that are no ``lt.EnergyBalanceCarrier`` yet (†), by assembly.
    CARRIERS_NOT_YET: Set[Tuple[str, str]] = {
        ("dhw/storage_water_heater", "lpg"),
        ("heating_secondary/wood_stove", "wood_logs"),
        ("supply/delivered_fuel", "hvo"),
        ("supply/delivered_fuel", "lpg"),
        ("supply/delivered_fuel", "wood_logs"),
    }

    #: Constructs the expansion refuses by design until their step (``EF-7L``), by assembly.
    REFUSED_UNTIL_THEIR_STEP: Set[Tuple[str, str]] = {
        ("mobility/electric_vehicle", "$derived"),
        ("mobility/electric_vehicle", "$fact"),
        ("storage/battery", "fact, many: true"),
    }


def mockup_assemblies() -> List[Path]:
    """Every assembly of the mockup snapshot."""
    return sorted(Fixtures.MOCKUP.rglob("*.assembly.yaml"))


def library_path(path: Path) -> str:
    """The library path of a mockup assembly."""
    return path.relative_to(Fixtures.MOCKUP).as_posix()[: -len(".assembly.yaml")]


def problems(validator: Any, document: Any) -> List[Tuple[str, str]]:
    """Every validation problem of a document as ``(path, message)``."""
    return sorted(
        ("/".join(str(part) for part in error.absolute_path), error.message)
        for error in validator.iter_errors(document)
    )


@pytest.fixture(name="assembly_validator", scope="module")
def fixture_assembly_validator() -> Any:
    """A validator over the assembly schema, checked against its meta-schema first."""
    schema = AssemblySchemaBuilder().build()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.base
def test_the_committed_assembly_schema_is_what_an_export_writes_today() -> None:
    """The second file ``hisim energy-system schema`` writes is committed and current."""
    committed = default_schema_path().parent / AssemblySchemaBuilder.FILENAME
    assert committed.read_text(encoding="utf-8") == render_schema(AssemblySchemaBuilder().build())


@pytest.mark.base
def test_the_mockup_has_fifteen_assemblies_and_three_energy_systems() -> None:
    """The snapshot is complete, so the listings below cover every file."""
    assert len(mockup_assemblies()) == 15
    assert len(list(Fixtures.MOCKUP.glob("*.energy_system.yaml"))) == 3


@pytest.mark.base
def test_every_mockup_assembly_validates_against_the_assembly_schema(assembly_validator: Any) -> None:
    """The mockup carries the whole contract: tests, ranges, descriptions, and selectors of the right shape.

    The bare supply connections declare no numeric parameter and an empty ``monotone`` list, which the
    schema accepts exactly because they have nothing to sweep (D24 as amended).
    """
    found = {
        path.relative_to(Fixtures.MOCKUP).as_posix(): problems(
            assembly_validator, yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        for path in mockup_assemblies()
    }
    assert {name: listed for name, listed in found.items() if listed} == {}


@pytest.mark.base
def test_the_schema_requires_a_monotone_entry_only_beside_a_numeric_parameter(assembly_validator: Any) -> None:
    """``monotone: []`` is legal without a numeric parameter and refused with one."""
    base: Dict[str, Any] = {
        "schema_version": 4,
        "kind": "assembly",
        "name": "x/y",
        "tests": {"bounds": [], "monotone": []},
    }
    assert problems(assembly_validator, {**base, "parameters": {}}) == []
    assert problems(assembly_validator, {**base, "parameters": {"p": {"type": "bool", "description": "d"}}}) == []
    numeric = {"p": {"type": "float", "description": "d", "default": 1, "range": {"min": 0, "max": 2}}}
    assert problems(assembly_validator, {**base, "parameters": numeric}) == [
        ("tests/monotone", "[] should be non-empty")
    ]


@pytest.mark.base
def test_the_library_check_of_the_mockup_lists_only_what_the_repository_owes_it() -> None:
    """Missing class interfaces (lt0b.12), † classes and † carriers, and nothing else."""
    resolver = AssemblyResolver([Fixtures.MOCKUP])
    without_interface: Set[str] = set()
    not_written: Set[str] = set()
    carriers: Set[Tuple[str, str]] = set()
    other: List[str] = []
    for path in mockup_assemblies():
        name = library_path(path)
        for problem in check_assembly(resolver.resolve(name, "test"), resolver, CheckStrength.LIBRARY):
            interface = re.search(
                r"\((hisim\.[\w.]+)\)(?: in option '\w+' of the variant '\w+')? declares no CLASS_INTERFACE", problem
            )
            missing = re.search(r"the module '([\w.]+)' of '\w+' cannot be imported", problem)
            carrier = re.search(
                r"(?:names|takes the carrier) '(\w+)'(?: with some parameters)?, which is no carrier", problem
            )
            if interface is not None:
                without_interface.add(interface.group(1))
            elif missing is not None:
                not_written.add(missing.group(1))
            elif carrier is not None:
                carriers.add((name, carrier.group(1)))
            else:
                other.append(problem)

    assert not other, other
    assert without_interface == MockupOwes.WITHOUT_CLASS_INTERFACE | MockupOwes.NOT_YET_WRITTEN
    assert not_written == {class_path.rsplit(".", 1)[0] for class_path in MockupOwes.NOT_YET_WRITTEN}
    assert carriers == MockupOwes.CARRIERS_NOT_YET


@pytest.mark.base
def test_a_member_written_in_two_options_is_checked_against_each_options_class() -> None:
    """hisim-lt0b.13 on the mockup's storage water heater: ``Heater`` is checked as each option's class."""
    resolver = AssemblyResolver([Fixtures.MOCKUP])
    listed = check_assembly(resolver.resolve("dhw/storage_water_heater", "test"), resolver, CheckStrength.LIBRARY)

    for option, class_path in (
        ("immersion", "hisim.components.immersion_heater.ImmersionHeater"),
        ("burner", "hisim.components.generic_boiler.GenericBoiler"),
    ):
        assert any(
            f"'Heater' ({class_path}) in option '{option}' of the variant 'heater' declares no CLASS_INTERFACE, so "
            "the outputs its test contract must bound are unknown." in problem
            for problem in listed
        ), option


@pytest.mark.base
def test_the_mockup_constructs_refused_until_their_step_are_exactly_the_pinned_ones() -> None:
    """``$fact``, ``$derived``, ``many: true`` and fact exports: what the expansion refuses with ``EF-7L``."""
    found: Set[Tuple[str, str]] = set()
    for path in mockup_assemblies():
        model, _lines = AssemblyReader.read(path)
        for member in model.declared_members().values():
            trees = {"config": dict(member.entry.config)}
            if member.entry.constructor is not None:
                trees["arguments"] = dict(member.entry.constructor.arguments)
            for key, _value_path in ParameterSubstitution.unlowered_in(trees):
                found.add((library_path(path), key))
        for port in model.ports.values():
            if port.kind == PortKind.FACT and port.many:
                found.add((library_path(path), "fact, many: true"))
            if port.kind == PortKind.FACT and "export" in port.raw:
                found.add((library_path(path), "fact export"))

    assert found == MockupOwes.REFUSED_UNTIL_THEIR_STEP


@pytest.mark.base
def test_the_mockup_composed_files_have_the_shape_of_a_version_4_file() -> None:
    """Both composed files validate against the format-only schema; the heat-pump one also class by class."""
    structural = jsonschema.Draft202012Validator(build_structural_schema())
    full = jsonschema.Draft202012Validator(build_schema(ComponentClassScan.collect()))
    for name in ("composed_heatpump_default.energy_system.yaml", "renovisor_full_house.energy_system.yaml"):
        document = yaml.safe_load((Fixtures.MOCKUP / name).read_text(encoding="utf-8"))
        assert problems(structural, document) == [], name
    heat_pump = yaml.safe_load(
        (Fixtures.MOCKUP / "composed_heatpump_default.energy_system.yaml").read_text(encoding="utf-8")
    )
    assert problems(full, heat_pump) == []


@pytest.mark.base
def test_the_heat_pump_composed_file_reads_into_the_model() -> None:
    """The reader takes the composed file: imports, verbs, site ports, placeholders, order, the grid's selection."""
    model = parse_energy_system(Fixtures.MOCKUP / "composed_heatpump_default.energy_system.yaml")

    assert model.schema_version == 4
    assert list(model.imports) == ["heating", "dhw", "pv", "battery", "control", "grid"]
    building = model.components["Building"]
    assert building.order == 1
    assert building.verbs.optional_bind == {"temperature_modifier": "control"}
    assert building.ports["temperature_modifier"].kind == PortKind.NEED
    assert [placed.position for placed in building.placeholders] == [1]
    assert model.imports["pv"].instances is not None and list(model.imports["pv"].instances) == ["pv_system"]
    observes = model.imports["grid"].observes
    assert observes is not None and observes.raw == [{"output": "TotalElectricityToOrFromGrid"}]
    assert observes.selection == Selection(selectors=(Selector(output="TotalElectricityToOrFromGrid"),))


@pytest.mark.base
@pytest.mark.parametrize("path", mockup_assemblies(), ids=lambda path: path.relative_to(Fixtures.MOCKUP).as_posix())
def test_every_mockup_assembly_reads(path: Path) -> None:
    """The reader takes the mockup's every construct: ports of all kinds, selectors, variants, placeholders, tests."""
    name = path.relative_to(Fixtures.MOCKUP).as_posix()
    model, lines = AssemblyReader.read(path)

    assert model.name == name[: -len(".assembly.yaml")]
    assert model.tests is not None and model.tests.bounds
    assert lines.line("components") > 0


@pytest.mark.base
def test_the_controller_mockup_reads_its_observer_port_and_its_priorities() -> None:
    """``control/ems_self_consumption``: an observer port with ``default: declared``, priorities from a parameter."""
    model, _lines = AssemblyReader.read(Fixtures.MOCKUP / "control" / "ems_self_consumption.assembly.yaml")
    battery, _lines = AssemblyReader.read(Fixtures.MOCKUP / "storage" / "battery.assembly.yaml")
    heat_pump, _lines = AssemblyReader.read(Fixtures.MOCKUP / "heating" / "air_source_heat_pump.assembly.yaml")

    assert model.ports["flows"].kind == PortKind.OBSERVER
    assert model.ports["flows"].selection == Selection(declared=True)
    assert model.ports["priorities"].kind == PortKind.ACTUATES
    assert model.ports["priorities"].priorities == {"$param": "priorities"}
    assert battery.ports["electricity"].controllable_target == "LoadingPowerInput"
    assert heat_pump.ports["electricity_sh"].controllable_via == "ems_modifier"


@pytest.mark.base
def test_the_reader_keeps_the_line_of_every_block() -> None:
    """The line index the source maps cite points at the written lines."""
    path = Fixtures.LIBRARY / "pv" / "array.assembly.yaml"
    text = path.read_text(encoding="utf-8").splitlines()
    _model, lines = AssemblyReader.read(path)

    assert text[lines.line("components", "PVSystem") - 1].strip() == "PVSystem:"
    assert text[lines.line("components", "PVSystem", "inputs", 0) - 1].strip() == "- {$port: weather}"
    assert text[lines.line("interface", "needs", "weather") - 1].strip().startswith("weather:")


@pytest.mark.base
def test_the_fixture_assembly_reads_every_block() -> None:
    """Parameters, constraints, presets, members with display and ``$param`` preset, variants, ports, tests."""
    heater, _ = AssemblyReader.read(Fixtures.LIBRARY / "generator" / "electric_heater.assembly.yaml")
    pv, _ = AssemblyReader.read(Fixtures.LIBRARY / "pv" / "array.assembly.yaml")

    assert heater.parameters["with_thermostat"].allowed_values == (True, False)
    assert heater.variants["thermostat"].options["fitted"].components["Controller"].preset_parameter == "control"
    assert heater.ports["ems_modifier"].active_when == {"with_thermostat": (True,)}
    assert pv.components["PVSystem"].display == "PV array, {facing}, azimuth {azimuth_in_degree}"
    assert pv.parameters["share_of_roof"].default is None
    assert pv.parameters["azimuth_in_degree"].range == (0.0, 360.0)
    assert pv.tests is not None and pv.tests.monotone[0].direction == MonotoneDirection.INCREASING
    assert [constraint.text() for constraint in pv.constraints] == ["exactly_one_of: [power_in_watt, share_of_roof]"]


@pytest.mark.base
@pytest.mark.parametrize(
    "fragment, marker",
    [
        ("kind: fragment", "EF-70"),
        ("schema_version: 3", "EF-01"),
        ("parameters: {p: {type: number, description: x}}", "EF-70"),
        ("interface: {needs: {p: {into: [A]}}}", "EF-70"),
        ("tests: {bounds: [{output: A.B, min: 0}]}", "EF-70"),
        ("tests: {monotone: [{parameter: p, kpi: k, member: A, direction: up}]}", "EF-70"),
        ("constraints: [{one_of: [a, b]}]", "EF-70"),
    ],
)
def test_a_malformed_block_is_refused_with_its_key_path(fragment: str, marker: str) -> None:
    """The reader refuses a block of the wrong shape at its first problem, naming the key path."""
    base = {"schema_version": 4, "kind": "assembly", "name": "x/y"}
    base.update(yaml.safe_load(fragment))
    text = yaml.safe_dump(base)

    with pytest.raises(EnergySystemFormatError, match=marker):
        AssemblyReader.read(text, origin="x/y.assembly.yaml")
