"""Reading assembly files and checking them against their schema (``assemblies_spec.md`` §2, §9.4).

The mockup of the spec (``tests/assemblies/mockup``, a snapshot of
``roadmap/declarative_energy_systems/assemblies_mockup`` at docs/assemblies df91d5f0, PR #881) is the
shape the format must accept. It predates the test contract of §9.4, so validated against the
assembly schema its assemblies report the missing ``tests`` block and the missing ``range`` of each
numeric parameter — the listing pinned here — and nothing else, except one defect of the mockup
itself, pinned as such. Of the three defects the snapshot of 087b3ce8 carried, the unquoted comma in
a flow mapping of ``control/ems_self_consumption`` is fixed; the constructor calls of
``mobility/electric_vehicle`` and of the composed RenoVisor house are now written
``constructor: {name: <name>, arguments: {...}}``, which is still not the format's spelling —
``constructor: {<name>: {<arguments>}}`` (``hisim/energy_system/entries.py``, the emitter writes the
same) — so both files keep that one defect.
"""

from pathlib import Path
from typing import Any, List, Set, Tuple

import jsonschema
import pytest
import yaml

from hisim.energy_system.assemblies.model import ParameterType, MonotoneDirection
from hisim.energy_system.assemblies.reader import AssemblyReader
from hisim.energy_system.assemblies.schema import AssemblySchemaBuilder
from hisim.energy_system.errors import EnergySystemFormatError
from hisim.energy_system.imports_model import PortKind
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.schema_classes import ComponentClassScan
from hisim.energy_system.schema_export import build_schema, build_structural_schema, default_schema_path, render_schema
from tests.assemblies.helpers import Fixtures


class MockupDefects:
    """The one problem of the mockup that is not about the test contract: the constructor spelling."""

    #: The files writing ``constructor: {name: …, arguments: …}``.
    CONSTRUCTOR_SPELLING: Tuple[str, ...] = (
        "mobility/electric_vehicle.assembly.yaml",
        "renovisor_full_house.energy_system.yaml",
    )

    @classmethod
    def is_defect(cls, name: str, where: str, message: str) -> bool:
        """Whether a schema problem is the pinned constructor spelling of that file."""
        return (
            name in cls.CONSTRUCTOR_SPELLING
            and where.endswith("/constructor")
            and message.endswith("has too many properties")
        )


def mockup_assemblies() -> List[Path]:
    """Every assembly of the mockup snapshot."""
    return sorted(Fixtures.MOCKUP.rglob("*.assembly.yaml"))


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
    """The snapshot is complete, so the listing below covers every file."""
    assert len(mockup_assemblies()) == 15
    assert len(list(Fixtures.MOCKUP.glob("*.energy_system.yaml"))) == 3


@pytest.mark.base
def test_the_mockup_assemblies_owe_only_their_test_contract(assembly_validator: Any) -> None:
    """Validated against the assembly schema, the mockup lists the test contract it predates, and nothing else.

    Every assembly misses ``tests``; every numeric parameter misses its ``range``.
    """
    contract: Set[Tuple[str, str]] = set()
    other: List[Tuple[str, str, str]] = []
    for path in mockup_assemblies():
        name = path.relative_to(Fixtures.MOCKUP).as_posix()
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for where, message in problems(assembly_validator, document):
            if message == "'tests' is a required property" and where == "":
                contract.add((name, "tests"))
            elif message == "'range' is a required property" and where.startswith("parameters/"):
                declared = document["parameters"][where.split("/")[1]]
                assert ParameterType(declared["type"]).is_numeric
                contract.add((name, where))
            elif not MockupDefects.is_defect(name, where, message):
                other.append((name, where, message))

    assert not other, other
    assert {name for name, item in contract if item == "tests"} == {
        path.relative_to(Fixtures.MOCKUP).as_posix() for path in mockup_assemblies()
    }
    assert ("pv/array.assembly.yaml", "parameters/azimuth_in_degree") in contract


@pytest.mark.base
def test_the_mockup_composed_files_have_the_shape_of_a_version_4_file() -> None:
    """Both composed files validate against the format-only schema; the heat-pump one also class by class."""
    structural = jsonschema.Draft202012Validator(build_structural_schema())
    full = jsonschema.Draft202012Validator(build_schema(ComponentClassScan.collect()))
    for name in ("composed_heatpump_default.energy_system.yaml", "renovisor_full_house.energy_system.yaml"):
        document = yaml.safe_load((Fixtures.MOCKUP / name).read_text(encoding="utf-8"))
        found = [
            (where, message)
            for where, message in problems(structural, document)
            if not MockupDefects.is_defect(name, where, message)
        ]
        assert found == [], name
    heat_pump = yaml.safe_load(
        (Fixtures.MOCKUP / "composed_heatpump_default.energy_system.yaml").read_text(encoding="utf-8")
    )
    assert problems(full, heat_pump) == []


@pytest.mark.base
def test_the_heat_pump_composed_file_reads_into_the_model() -> None:
    """The reader takes the composed file: imports, verbs, site ports, placeholders, order."""
    model = parse_energy_system(Fixtures.MOCKUP / "composed_heatpump_default.energy_system.yaml")

    assert model.schema_version == 4
    assert list(model.imports) == ["heating", "dhw", "pv", "battery", "control", "grid"]
    building = model.components["Building"]
    assert building.order == 1
    assert building.verbs.optional_bind == {"temperature_modifier": "control"}
    assert building.ports["temperature_modifier"].kind == PortKind.NEED
    assert [placed.position for placed in building.placeholders] == [1]
    assert model.imports["pv"].instances is not None and list(model.imports["pv"].instances) == ["pv_system"]
    assert model.imports["grid"].observes == [{"output": "TotalElectricityToOrFromGrid"}]


@pytest.mark.base
@pytest.mark.parametrize("path", mockup_assemblies(), ids=lambda path: path.relative_to(Fixtures.MOCKUP).as_posix())
def test_every_mockup_assembly_reads_except_its_pinned_defect(path: Path) -> None:
    """The reader takes the mockup's every construct: ports of all kinds, variants, placeholders, ``$`` values."""
    name = path.relative_to(Fixtures.MOCKUP).as_posix()
    if name in MockupDefects.CONSTRUCTOR_SPELLING:
        with pytest.raises(EnergySystemFormatError, match="names exactly one constructor"):
            AssemblyReader.read(path)
        return
    model, lines = AssemblyReader.read(path)

    assert model.name == name[: -len(".assembly.yaml")]
    assert model.tests is None
    assert lines.line("components") > 0


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
    assert [constraint.text() for constraint in pv.constraints] == ["at_most_one_of: [power_in_watt, share_of_roof]"]


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
