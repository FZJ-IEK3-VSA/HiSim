"""The v1 format: round trips, the cut constructs refused by name, part-2 constructs read and refused, the schemas."""

from __future__ import annotations

import textwrap
from pathlib import Path

import jsonschema
import pydantic
import pytest
import yaml

from hisim.energy_system.assemblies.reader import AssemblyReader
from hisim.energy_system.assemblies.schema import AssemblySchemaBuilder, assembly_schema_is_current
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemFormatError
from hisim.energy_system.imports_model import BindingVerbs, Port, PortKind
from hisim.energy_system.loader import dump_energy_system, parse_energy_system
from tests.assemblies.helpers import (
    EMPTY_CONTRACT,
    OCCUPANCY,
    WEATHER,
    Library,
    Mocks,
    expand_text,
    read_system,
    site,
)

#: A group's component carrying ``order:``, which only a top-level entry or an import may.
ORDERED_OCCUPANCY = OCCUPANCY.replace("}", ", order: 1}")

#: A minimal assembly every refusal test changes in one place.
MINIMAL = f"""\
schema_version: 4
kind: assembly
name: test/minimal
components:
  Device: {{class: {Mocks.CLASSES}.MockWeather, preset: standard}}
{EMPTY_CONTRACT}"""


def read_assembly(text: str) -> None:
    """Reads an assembly text."""
    AssemblyReader.read_text(textwrap.dedent(text), "test/minimal.assembly.yaml")


@pytest.mark.base
def test_a_version_4_file_round_trips_with_its_imports_verbs_ports_and_placeholders() -> None:
    """Catches the emitter dropping or moving a block the reader reads (imports, verbs, ports, a placeholder)."""
    model = parse_energy_system(Mocks.HOUSE)
    text = dump_energy_system(model)
    again = parse_energy_system(text)
    assert again == model and dump_energy_system(again) == text
    assert yaml.safe_load(text)["components"]["Monitor"]["inputs"] == [{"$port": "tank"}, "Ems"]
    assert model.components["Monitor"].placeholders[0].position == 0
    assert model.imports["pv"].instances is not None and model.imports["pv"].instances["west"].installation_year == 2026
    assert model.imports["tank"].verbs.bind == {"heat": "heater.heat"}


@pytest.mark.base
def test_a_version_3_file_that_imports_is_refused() -> None:
    """Catches a flat file silently gaining imports without declaring the version that reads them."""
    with pytest.raises(EnergySystemFormatError, match="EF-01") as refusal:
        read_system("schema_version: 3\nname: x\nimports: {pv: {assembly: mock/pv_array}}\n")
    assert "imports" in str(refusal.value)


@pytest.mark.base
def test_a_version_3_entry_with_ports_or_verbs_is_refused() -> None:
    """Catches the keys of version 4 leaking into a version-3 file or into a group's entry."""
    with pytest.raises(EnergySystemFormatError, match="EF-18"):
        read_system(f"schema_version: 3\nname: x\ncomponents:\n  {WEATHER.replace('}', ', bind: {a: b}}')}\n")


@pytest.mark.base
def test_an_authored_hyphenated_name_is_refused() -> None:
    """Catches the address separator being admitted in a name nobody's expansion produced."""
    with pytest.raises(EnergySystemFormatError, match="EF-08"):
        read_system(site(WEATHER.replace("Weather:", "pv-east-PVSystem:", 1)))


@pytest.mark.base
@pytest.mark.parametrize(
    ("construct", "text"),
    [
        ("imports", MINIMAL + "imports: {inner: {assembly: mock/pv_array}}\n"),
        ("presets", MINIMAL + "presets: {standard: {}}\n"),
        ("from", MINIMAL + "interface: {needs: {demand: {from: tank.hot_water_demand}}}\n"),
        ("internal", MINIMAL + "interface: {internal: {link: {bind: [a, b]}}}\n"),
        ("actuates", MINIMAL + "interface: {actuates: {priorities: []}}\n"),
        ("export", MINIMAL + "interface: {provides: {peak: {fact: pv_peak_power_in_watt, export: true}}}\n"),
        ("order", MINIMAL.replace("preset: standard}", "preset: standard, order: 1}")),
        ("$switch", MINIMAL.replace("preset: standard}", "preset: standard, config: {a: {$switch: x, y: 1}}}")),
        ("$fact", MINIMAL.replace("preset: standard}", "preset: standard, config: {a: {$fact: area}}}")),
        ("$derived", MINIMAL.replace("preset: standard}", "preset: standard, config: {a: {$derived: x}}}")),
        ("at_most_one_of", MINIMAL + "constraints: [{at_most_one_of: [a, b]}]\n"),
        ("requires", MINIMAL + "constraints: [{requires: {a: [b]}}]\n"),
        ("priorities", MINIMAL + "parameters: {priorities: {type: string, default: x, description: d}}\n"),
        ("preset", MINIMAL.replace("monotone: []}", "monotone: [], expect: [{preset: x, kpi: k, min: 0}]}")),
    ],
)
def test_every_cut_construct_of_an_assembly_is_refused_by_name(construct: str, text: str) -> None:
    """Catches a construct D26 cut being read as an unknown key, or worse, accepted and ignored."""
    with pytest.raises(EnergySystemFormatError, match="EF-73") as refusal:
        read_assembly(text)
    assert f"'{construct}'" in str(refusal.value) and "D26" in str(refusal.value)


@pytest.mark.base
@pytest.mark.parametrize(
    ("construct", "text"),
    [
        ("preset", site(WEATHER, imports="pv: {assembly: mock/pv_array, preset: south}")),
        ("order", site(WEATHER, imports="pv: {assembly: mock/pv_array, instances: {east: {order: 1}}}")),
        ("order", site(WEATHER) + f"groups:\n  extra:\n    enabled: true\n    components: {{{ORDERED_OCCUPANCY}}}\n"),
        ("actuates", site(WEATHER, imports="pv: {assembly: mock/pv_array, actuates: {}}")),
        ("$switch", site(WEATHER, imports="pv: {assembly: mock/pv_array, parameters: {tilt_in_degree: {$switch: a}}}")),
        ("preset", site(WEATHER, imports="pv: {assembly: mock/pv_array, instances: {east: {preset: south}}}")),
    ],
)
def test_every_cut_construct_of_an_energy_system_file_is_refused_by_name(construct: str, text: str) -> None:
    """Catches ``order:`` off a top-level entry or import, an import preset or ``$switch`` slipping through."""
    with pytest.raises(EnergySystemFormatError, match="EF-73") as refusal:
        read_system(text)
    assert f"'{construct}'" in str(refusal.value)


@pytest.mark.base
@pytest.mark.parametrize("value", ["first", "true", "1.5", "[1]"])
def test_an_order_that_is_no_integer_is_refused(value: str) -> None:
    """Catches ``order: true`` or ``order: 1.5`` being read as a position."""
    with pytest.raises(EnergySystemFormatError, match="EF-07") as refusal:
        read_system(site(WEATHER, imports=f"pv: {{assembly: mock/pv_array, order: {value}}}"))
    assert "imports.pv.order" in str(refusal.value)


@pytest.mark.base
def test_a_port_both_optional_and_required_when_is_refused() -> None:
    """Catches ``optional: true`` being silently ignored on a port that also states ``required_when``."""
    port = "    weather: {into: [Device], partner: MockWeather, optional: true, required_when: {fitted: [true]}}\n"
    with pytest.raises(EnergySystemFormatError, match="EF-70") as refusal:
        read_assembly(MINIMAL + "interface:\n  needs:\n" + port)
    for name in ("weather", "optional: true", "required_when", "active_when"):
        assert name in str(refusal.value)


@pytest.mark.base
def test_the_verbs_model_refuses_a_port_under_two_verbs() -> None:
    """Catches a verbs model built in code holding a port the reader would refuse, which then dumps a bad file."""
    with pytest.raises(pydantic.ValidationError, match="the ports p carry two verbs"):
        BindingVerbs(bind={"p": "Heater"}, none=("p",))


@pytest.mark.base
@pytest.mark.parametrize(
    ("fields", "names"),
    [
        ({"kind": PortKind.PROVIDED}, ("provided port 'x'", "None", "Member.Output")),
        ({"kind": PortKind.PROVIDED, "output": "Heater"}, ("provided port 'x'", "'Heater'")),
        ({"kind": PortKind.NEED, "partner": ("MockTank",)}, ("need 'x'", "no member")),
        ({"kind": PortKind.NEED, "into": ("Tank",)}, ("need 'x'", "no partner class")),
    ],
)
def test_the_port_model_refuses_a_shape_its_kind_does_not_have(fields: dict, names: tuple) -> None:
    """Catches a provided port without ``Member.Output`` or a need without members or partner, built in code."""
    with pytest.raises(pydantic.ValidationError) as refusal:
        Port(name="x", section="needs", **fields)
    for name in names:
        assert name in str(refusal.value)


@pytest.mark.base
def test_the_constructs_lowered_in_part_2_are_read_and_refused_together(tmp_path: Path) -> None:
    """Catches a circuit, carrier, fact or observer port, ``controllable`` or ``observes:`` being silently ignored."""
    library = Library(tmp_path)
    library.add(
        "test/later",
        f"""\
        schema_version: 4
        kind: assembly
        name: test/later
        components:
          Device:
            class: {Mocks.CLASSES}.MockPVSystem
            preset: rooftop
            inputs: [{{$observes: flows}}]
        interface:
          needs:
            fuel: {{carrier: natural_gas, outputs: [Device.ElectricityOutput]}}
            area: {{fact: roof_area_in_m2, into: [Device]}}
          provides:
            power: {{output: Device.ElectricityOutput, controllable: {{target_input: Setpoint}}}}
            sh: {{circuit: sh, member: Device}}
          observes:
            flows: {{into: [Device], default: declared}}
        tests:
          bounds: []
          monotone: []
        """,
    )
    text = site(WEATHER, imports="later: {assembly: test/later, observes: [{output: X}]}")
    with pytest.raises(EnergySystemAssemblyError, match="EF-74") as refusal:
        expand_text(text, library.resolver())
    message = str(refusal.value)
    assert "part 2" in message
    for named in (
        "port fuel (carrier)",
        "port area (fact)",
        "port sh (circuit)",
        "port flows (observer)",
        "port power (controllable)",
        "{$observes: flows}",
        "import later: observes",
    ):
        assert named in message


@pytest.mark.base
def test_both_committed_schemas_are_current() -> None:
    """Catches a reader change that the committed assembly schema does not follow (``hisim energy-system schema``)."""
    assert assembly_schema_is_current(), "run `hisim energy-system schema` and commit both schema files"


@pytest.mark.base
@pytest.mark.parametrize("path", sorted(Mocks.LIBRARY.rglob("*.assembly.yaml")), ids=lambda path: path.stem)
def test_every_mock_assembly_validates_against_the_assembly_schema(path: Path) -> None:
    """Catches the assembly schema refusing a file the reader accepts."""
    jsonschema.validate(yaml.safe_load(path.read_text(encoding="utf-8")), AssemblySchemaBuilder().build())


@pytest.mark.base
@pytest.mark.parametrize(
    "extra",
    [
        "presets: {standard: {}}\n",
        "imports: {inner: {assembly: mock/pv_array}}\n",
        "constraints: [{requires: {a: [b]}}]\n",
    ],
)
def test_the_assembly_schema_admits_no_cut_construct(extra: str) -> None:
    """Catches the assembly schema admitting what the v1 reader refuses."""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(yaml.safe_load(MINIMAL + extra), AssemblySchemaBuilder().build())
