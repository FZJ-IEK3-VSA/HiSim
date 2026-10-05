"""The import record and the source maps (``assemblies_spec.md`` §2.3 item 5, §9.1, §9.2).

The record states what the expansion did — assembly, hash, preset, parameters as given and as
resolved, variants, members with their addresses and order paths, every port's state and partner,
the final sequence — and the source map says where every produced item came from. Both reach the
realized record's metadata, and every downstream error naming a produced component prints its
entry.
"""

import copy
import hashlib
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemBindingError, EnergySystemFormatError, EnergySystemWiringError
from hisim.energy_system.executor import EnergySystemExecutor
from hisim.energy_system.imports_model import PortState
from hisim.energy_system.loader import EnergySystemReader, dump_energy_system, parse_energy_system
from hisim.energy_system.record import realize
from hisim.energy_system.source_lines import LineIndex
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.helpers import WEATHER, Library, Mocks, mock_resolver, read_system, site


def expand_house():
    """The mock house, expanded with its line index."""
    text = Mocks.HOUSE.read_text(encoding="utf-8")
    return expand_imports(
        parse_energy_system(Mocks.HOUSE), mock_resolver(), lines=LineIndex.from_text(text, Mocks.HOUSE.name)
    )


@pytest.mark.base
def test_the_import_record_states_what_each_instance_became() -> None:
    """Assembly, hash, preset, parameters, variants, members, ports — per instance at every depth."""
    _expanded, record = expand_house()

    west = record.instance("pv[west]")
    assert west is not None
    assert west.assembly == "mock/pv_array"
    assert west.sha256 == hashlib.sha256((Mocks.LIBRARY / "mock" / "pv_array.assembly.yaml").read_bytes()).hexdigest()
    assert west.parameters_given == {"azimuth_in_degree": 270, "facing": "west", "power_in_watt": 3000}
    assert west.parameters_resolved["tilt_in_degree"] == 30
    assert west.reserved == {"installation_year": 2026}
    assert west.members == {"pv-west-PVSystem": {"member": "PVSystem", "display_name": "PV array, west, azimuth 270"}}
    weather = west.port("weather")
    assert weather is not None
    assert (weather.state, weather.partner, weather.verb) == (PortState.REQUIRED, "Weather", "default")
    assert weather.lowered_to == ("pv-west-PVSystem.inputs: Weather",)

    backup = record.instance("backup")
    assert backup is not None and backup.variants == {"thermostat": "none"}
    package = record.instance("hot_water")
    assert package is not None and package.preset == "standard"
    modifier = package.port("ems_modifier")
    assert modifier is not None and (modifier.partner, modifier.verb) == ("Ems", "optional-bind")
    tank = record.instance("hot_water → heater → tank")
    assert tank is not None
    assert {port.port: port.state for port in tank.ports} == {
        "hot_water_demand": PortState.REEXPORTED,
        "heat": PortState.REQUIRED,
        "temperature": PortState.INTERNAL,
    }


@pytest.mark.base
def test_the_record_document_carries_the_addresses_and_the_sequence() -> None:
    """What the metadata block holds: the address table a re-run reads back, and the order paths."""
    _expanded, record = expand_house()

    document = record.to_document()

    assert document["addresses"]["pv-east-PVSystem"] == {
        "path": [{"import": "pv", "instance": "east"}],
        "member": "PVSystem",
        "assembly": "mock/pv_array",
        "display_name": "PV array, east, azimuth 90",
    }
    assert document["sequence"][6] == {"component": "hot_water-heater-tank-Tank", "order": "4.1.3.1"}
    assert document["not_lowered"] == []


@pytest.mark.base
def test_every_produced_item_has_a_source_map_entry() -> None:
    """Component, every input item, every sizing line and every config value of every member."""
    expanded, record = expand_house()

    for name, identity in expanded.addresses.items():
        entry = expanded.components[name]
        assert record.source_map.of(name) is not None, name
        for index in range(len(entry.inputs)):
            assert record.source_map.of(name, f"inputs[{index}]") is not None, (name, index)
        for fact in entry.sizing_sources:
            assert record.source_map.of(name, f"sizing_sources.{fact}") is not None, (name, fact)
        for key in entry.config:
            assert record.source_map.of(name, f"config.{key}") is not None, (name, key)
        assert record.source_map.of(name).member == identity.name  # type: ignore[union-attr]


@pytest.mark.base
def test_a_source_map_entry_names_the_import_path_and_every_file_and_line() -> None:
    """The shape every downstream error prints: ``name (import a → b, file:line → file:line)``."""
    _expanded, record = expand_house()

    entry = record.source_map.of("hot_water-heater-tank-Tank")
    assert entry is not None
    assert entry.text() == (
        "hot_water-heater-tank-Tank (import hot_water → heater → tank, house.energy_system.yaml:31 → "
        "mock/dhw_package.assembly.yaml:18 → mock/storage_water_heater.assembly.yaml:20 → "
        "mock/hot_water_tank.assembly.yaml:15)"
    )
    lowered = record.source_map.of("hot_water-heater-tank-Tank", "inputs[1]")
    assert lowered is not None and lowered.note == "port heat bound to hot_water-heater-Heater (bind)"
    tank_file = (Mocks.LIBRARY / "mock" / "hot_water_tank.assembly.yaml").read_text(encoding="utf-8").splitlines()
    assert tank_file[lowered.chain[-1].line - 1].strip() == "- {$port: heat}"


@pytest.mark.base
def test_the_realized_record_carries_the_import_record_and_the_source_maps(tmp_path: Path) -> None:
    """§9.1/§9.2: the metadata holds both, and the record reads back as a flat file with addresses."""
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(tmp_path)
    built = EnergySystemExecutor(
        parse_energy_system(Mocks.HOUSE), parameters, assembly_resolver=mock_resolver()
    ).build()

    record = realize(built)

    assert record.metadata is not None
    assert record.metadata["imports"]["instances"][0]["path"] == "pv[east]"
    assert "hot_water-heater-tank-Tank" in record.metadata["source_map"]
    text = dump_energy_system(record)
    reread = EnergySystemReader.build(RawDocument.parse_text(text, "record"), "record")
    assert reread.addresses == built.model.addresses
    assert list(reread.components) == list(built.model.components)


@pytest.mark.base
def test_a_plain_file_writes_no_import_block_into_its_record(tmp_path: Path) -> None:
    """A file without imports: the record's metadata is what it always was."""
    model, _lines = read_system(site(WEATHER).replace("schema_version: 4", "schema_version: 3"))
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(tmp_path)

    record = realize(EnergySystemExecutor(model, parameters).build())

    assert record.metadata is not None
    assert set(record.metadata) == {
        "hisim_version",
        "git_commit",
        "source_energy_system",
        "source_simulation_parameters",
    }


MISCONFIGURED = """
schema_version: 4
kind: assembly
name: broken/misconfigured
components:
  Tank:
    class: tests.assemblies.mock_components.MockTank
    preset: standard
    config: {volum_in_liter: 100}
"""

MISWIRED = """
schema_version: 4
kind: assembly
name: broken/miswired
components:
  Sky:
    class: tests.assemblies.mock_components.MockWeather
    preset: standard
  PVSystem:
    class: tests.assemblies.mock_components.MockPVSystem
    preset: rooftop
    inputs:
      - {input: TemperatureOutside, from: Sky.Temperature}
"""


@pytest.mark.base
@pytest.mark.parametrize(
    "assembly, error_class, marker",
    [
        (MISCONFIGURED, EnergySystemBindingError, "EF-17 at components.x-Tank"),
        (MISWIRED, EnergySystemWiringError, "EF-21 at components.x-PVSystem"),
    ],
)
def test_a_downstream_error_prints_the_source_map_entry_of_what_it_names(
    tmp_path: Path, assembly: str, error_class: type, marker: str
) -> None:
    """Class validation and wiring errors about a produced component say where it came from."""
    library = Library(tmp_path)
    name = assembly.split("name: ")[1].split("\n")[0]
    library.add(name, assembly, contract=True)
    model, lines = read_system(site(WEATHER) + f"imports:\n  x: {{assembly: {name}}}\n")
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(tmp_path / "results")

    with pytest.raises(error_class) as raised:  # type: ignore[call-overload]
        EnergySystemExecutor(model, parameters, assembly_resolver=library.resolver(), source_lines=lines).build()

    message = str(raised.value)
    assert marker in message
    assert "[source: x-" in message
    assert f"(import x, inline.energy_system.yaml:8 → {name}.assembly.yaml:" in message


#: A realized record's metadata with one member's address, as the record writer states it.
ADDRESSED: Dict[str, Any] = {
    "schema_version": 3,
    "name": "addressed",
    "components": {"pv-PVSystem": {"class": f"{Mocks.MOCKS}.MockPVSystem", "preset": "rooftop"}},
    "metadata": {
        "imports": {
            "addresses": {
                "pv-PVSystem": {"path": [{"import": "pv"}], "member": "PVSystem", "assembly": "mock/pv_array"}
            }
        }
    },
}


@pytest.mark.base
@pytest.mark.parametrize(
    "imports, marker",
    [
        ([], "metadata.imports.addresses: the record carries an import record without its address table"),
        ({"port_provenance": []}, "metadata.imports.addresses: the record carries an import record without its"),
        ({"addresses": {"pv-PVSystem": "pv"}}, "metadata.imports.addresses.pv-PVSystem: an address is a mapping"),
        (
            {"addresses": {"pv-PVSystem": {"path": [{"import": "pv"}], "member": "PVSystem", "order": 1}}},
            "metadata.imports.addresses.pv-PVSystem: an address is a mapping of path, member, assembly, display_name",
        ),
        (
            {"addresses": {"pv-PVSystem": {"path": "pv", "member": "PVSystem"}}},
            "metadata.imports.addresses.pv-PVSystem: an address has a list 'path'",
        ),
        (
            {"addresses": {"pv-PVSystem": {"path": [{"import": "p v"}], "member": "PVSystem"}}},
            r"metadata.imports.addresses.pv-PVSystem.path\[0\]: the step .* is no address step",
        ),
        (
            {"addresses": {"pv-PVSystem": {"path": [{"import": "pv"}], "member": "Other"}}},
            "metadata.imports.addresses.pv-PVSystem: 'pv-PVSystem' is listed with an address that serializes to "
            "'pv-Other'",
        ),
    ],
)
def test_a_malformed_address_table_is_refused_by_name(imports: Any, marker: str) -> None:
    """EF-07: the table is generated; any entry it would not have written means a hand edit."""
    document = copy.deepcopy(ADDRESSED)
    assert EnergySystemReader.build(document, "record").addresses["pv-PVSystem"].path[0].import_key == "pv"
    document["metadata"]["imports"] = imports

    with pytest.raises(EnergySystemFormatError, match=f"EF-07 at {marker}"):
        EnergySystemReader.build(document, "record")


@pytest.mark.base
def test_the_line_index_refuses_text_that_is_not_yaml() -> None:
    """EF-03 as the reader says it: an index without lines would drop every source-map line silently."""
    with pytest.raises(EnergySystemFormatError, match="EF-03 at broken.yaml: the document is not valid YAML"):
        LineIndex.from_text("components: [", "broken.yaml")
