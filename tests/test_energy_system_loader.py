"""Tests for reading and re-writing energy-system files.

The three mockups in the roadmap directory are the contract of the format, so they are
used here directly rather than being copied: a change to the format shows up as a failing
test in the same commit that changes a mockup. What this module covers is the read and
write path — the accepted file formats, the schema-version gate, the duplicate-key rule,
the classification of the three input shapes, and the canonical style — while the rules
that span more than one entry are covered by the structural-validation tests.

Every test names the failure mode it catches in its docstring, because an error-catalogue
test that only asserts "raises" is worth very little: what has to hold is that the right
condition is reported, that the message names the offending element, and that it lists the
alternatives whenever a closed set of them exists.
"""

# clean

from pathlib import Path
from typing import ClassVar, Tuple

import pytest

from hisim.energy_system import (
    AggregatorFeed,
    DefaultInputs,
    EnergySystemErrorId,
    EnergySystemFormatError,
    ExplicitWire,
    dump_energy_system,
    load_energy_system,
    parse_energy_system,
)


class Mockups:
    """Locates the three energy-system mockups that serve as this suite's fixtures.

    The mockups live with the format's design documents rather than under ``tests/``,
    because they are the normative description of the wire format and are edited whenever
    the format changes. Resolving them from the repository root keeps the tests working
    regardless of the directory pytest is started from.
    """

    #: File names of the three mockups, smallest first: a gas-boiler household with no
    #: groups, a heat-pump household with four groups, and a three-apartment building.
    NAMES: ClassVar[Tuple[str, ...]] = (
        "energy_system_mockup_minimal.yaml",
        "energy_system_mockup.yaml",
        "energy_system_mockup_mfh.yaml",
    )

    @classmethod
    def directory(cls) -> Path:
        """Returns the directory the mockups live in.

        Returns:
            The absolute path of the energy-system design directory.
        """
        return Path(__file__).resolve().parents[1] / "roadmap" / "declarative_energy_systems"

    @classmethod
    def path(cls, name: str) -> Path:
        """Returns the absolute path of one mockup.

        Args:
            name: The mockup's file name, one of :attr:`NAMES`.

        Returns:
            The absolute path of that mockup.
        """
        return cls.directory() / name


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES)
def test_every_mockup_loads_and_validates_structurally(name: str) -> None:
    """Catches a format change that the loader or the structural validator cannot follow.

    The mockups are the contract, so any of them failing to load means either the format
    moved without the code or the code became stricter than the format allows.
    """
    model = load_energy_system(Mockups.path(name))
    assert model.schema_version == 3
    assert model.all_components()


@pytest.mark.base
def test_the_minimal_mockup_classifies_its_input_items() -> None:
    """Catches an input item being read as the wrong one of the three shapes.

    Misclassification is silent at load time and only surfaces much later as a component
    that is wired to nothing, so the classification is pinned on a known file.
    """
    model = load_energy_system(Mockups.path("energy_system_mockup_minimal.yaml"))
    building = model.all_components()["building"]
    assert [item.source for item in building.inputs] == ["weather", "occupancy", "hds"]
    assert all(isinstance(item, DefaultInputs) for item in building.inputs)
    boiler_items = model.all_components()["boiler"].inputs
    assert [type(item).__name__ for item in boiler_items] == ["DefaultInputs", "ExplicitWire"]
    wire = boiler_items[1]
    assert isinstance(wire, ExplicitWire)
    assert (wire.input, wire.source, wire.output) == (
        "WaterInputTemperatureSh",
        "hds",
        "WaterTemperatureOutput",
    )
    meter_feeds = [item for item in model.all_components()["meter"].inputs if isinstance(item, AggregatorFeed)]
    assert len(meter_feeds) == len(model.all_components()["meter"].inputs)
    assert [(item.source, item.output, item.weight) for item in meter_feeds] == [
        ("occupancy", None, 999),
    ]


@pytest.mark.base
def test_an_explicit_wire_keeps_both_of_its_ports() -> None:
    """Catches an explicit wire losing its target port or its dotted source port.

    A wire that keeps only one end would be indistinguishable from a defaults item and
    would wire the wrong ports without any error being raised.
    """
    model = load_energy_system(Mockups.path("energy_system_mockup.yaml"))
    wires = [item for item in model.all_components()["heat_pump"].inputs if isinstance(item, ExplicitWire)]
    assert len(wires) == 1
    assert (wires[0].input, wires[0].source, wires[0].output) == (
        "TemperatureInputPrimary",
        "weather",
        "DailyAverageOutsideTemperatures",
    )


@pytest.mark.base
def test_a_constructor_entry_keeps_its_name_and_arguments() -> None:
    """Catches the constructor form being dropped or flattened into the config block.

    A file that names a constructor must keep both the constructor and the sparse
    overrides written next to it, since the two are applied in that order.
    """
    building = load_energy_system(Mockups.path("energy_system_mockup.yaml")).all_components()["building"]
    assert building.preset is None
    assert building.constructor is not None
    assert building.constructor.name == "for_tabula_code"
    assert building.constructor.arguments == {"building_code": "DE.N.SFH.05.Gen.ReEx.001.002"}
    assert building.config == {"number_of_apartments": 1, "enable_opening_windows": True}


@pytest.mark.base
def test_sizing_sources_keep_the_scalar_and_the_list_form_apart() -> None:
    """Catches a scalar sizing source being widened into a one-element list.

    The two forms mean different things to a reader and have to be written back the way
    they were read, so collapsing them would break the round-trip guarantee.
    """
    components = load_energy_system(Mockups.path("energy_system_mockup.yaml")).all_components()
    battery = components["battery"].sizing_sources["pv_peak_power_in_watt"]
    ems = components["ems"].sizing_sources["pv_peak_power_in_watt"]
    assert not isinstance(battery, tuple)
    assert battery.text == "pv_south.pv_peak_power_in_watt"
    assert isinstance(ems, tuple)
    assert [reference.text for reference in ems] == [
        "pv_south.pv_peak_power_in_watt",
        "pv_east.pv_peak_power_in_watt",
    ]


@pytest.mark.base
@pytest.mark.parametrize("name", Mockups.NAMES)
def test_dumping_a_loaded_mockup_is_a_fixed_point(name: str, tmp_path: Path) -> None:
    """Catches the emitter and the reader disagreeing about the canonical style.

    A hand-written file becomes canonical on the first pass; from then on loading and
    dumping must not change a single character, or a program that edits a file in place
    would rewrite parts it never touched.
    """
    canonical = dump_energy_system(load_energy_system(Mockups.path(name)))
    written = tmp_path / name
    written.write_text(canonical, encoding="utf-8")
    assert dump_energy_system(load_energy_system(written)) == canonical


@pytest.mark.base
def test_a_json_path_is_refused_as_an_unsupported_format() -> None:
    """Catches the JSON path creeping back in as a second accepted input format.

    A YAML parser accepts JSON, so nothing fails naturally; only the suffix check keeps
    the format single, and losing it would mean two canonical styles to maintain.
    """
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(Path("some_system.energy_system.json"))
    assert caught.value.error_id is EnergySystemErrorId.UNSUPPORTED_FORMAT
    assert ".yaml" in str(caught.value)
    assert "some_system.energy_system.json" in str(caught.value)


@pytest.mark.base
def test_a_duplicate_key_is_refused_with_its_key_path() -> None:
    """Catches YAML's last-one-wins rule silently changing the simulated system.

    Two blocks with the same key parse without complaint and the first one disappears, so
    the whole document is walked and the offender's key path has to appear in the message.
    """
    text = """
schema_version: 3
name: duplicate config
components:
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    config:
      a: 1
    config:
      b: 2
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.DUPLICATE_KEY
    assert "components.boiler.config" in str(caught.value)


@pytest.mark.base
def test_an_older_schema_version_is_refused_naming_the_supported_one() -> None:
    """Catches a file written for another format version being interpreted anyway.

    The same keys can mean different things across versions, so the gate has to run before
    anything else and the message has to say which version this code reads.
    """
    text = "schema_version: 2\nname: old\ncomponents: {}\n"
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.SCHEMA_VERSION
    assert "3" in str(caught.value)


@pytest.mark.base
def test_an_unknown_top_level_key_is_refused_listing_the_known_ones() -> None:
    """Catches a misspelled or invented top-level block being ignored instead of reported.

    A block the loader does not know would simply have no effect, which is the silent
    failure the format exists to remove; the message lists the keys that do exist.
    """
    text = "schema_version: 3\nname: x\ncomponents: {}\nconnections: []\n"
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.TOP_LEVEL_SHAPE
    assert "connections" in str(caught.value)
    assert "sizing_sources" not in str(caught.value)
    assert "components" in str(caught.value)


@pytest.mark.base
def test_an_unknown_entry_key_is_refused_listing_the_entry_keys() -> None:
    """Catches an entry key that does not exist being silently dropped.

    Entries have a closed key set, so a typo such as ``presets`` must be reported with the
    six keys an entry may carry rather than leaving the component unconfigured.
    """
    text = """
schema_version: 3
name: x
components:
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    presets: condensing_gas
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.UNKNOWN_ENTRY_KEY
    assert "presets" in str(caught.value)
    assert "preset" in str(caught.value)


@pytest.mark.base
def test_an_input_item_of_no_recognisable_shape_is_refused() -> None:
    """Catches an item that is neither defaults, wire nor feed being wired as something.

    Classification is by key presence and is total, so an item with none of the deciding
    keys has to be reported rather than treated as the nearest shape.
    """
    text = """
schema_version: 3
name: x
components:
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    inputs:
      - to: weather
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.UNCLASSIFIABLE_INPUT_ITEM
    assert "components.boiler.inputs[0]" in str(caught.value)


@pytest.mark.base
def test_a_dotted_bare_input_item_is_refused() -> None:
    """Catches a bare item that names a port being read as a component named ``a.b``.

    A bare item asks for the target's declared defaults and therefore names a component
    only; letting a dotted string through would produce a reference to nothing.
    """
    text = """
schema_version: 3
name: x
components:
  weather:
    class: hisim.components.weather.Weather
    preset: standard
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
    inputs:
      - weather.DailyAverageOutsideTemperatures
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.UNCLASSIFIABLE_INPUT_ITEM


@pytest.mark.base
def test_a_constructor_block_naming_two_constructors_is_refused() -> None:
    """Catches a constructor block that leaves it open which constructor runs.

    Two names in one block would make the executor pick one, which is exactly the kind of
    implicit choice the format forbids.
    """
    text = """
schema_version: 3
name: x
components:
  weather:
    class: hisim.components.weather.Weather
    constructor:
      for_location: {location: BERLIN}
      for_file: {source_path: "${inputs}/weather"}
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(text)
    assert caught.value.error_id is EnergySystemErrorId.MALFORMED_CONSTRUCTOR


@pytest.mark.base
def test_yaml_11_boolean_spellings_stay_strings() -> None:
    """Catches an upper-case enum member such as ``ON`` arriving as the boolean ``True``.

    PyYAML implements YAML 1.1, in which six more words resolve to booleans; a config value
    silently turning into a boolean would fail far from the line that caused it.
    """
    text = """
schema_version: 3
name: x
components:
  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    config:
      mode: ON
      other: NO
      really: true
"""
    config = parse_energy_system(text).all_components()["boiler"].config
    assert config["mode"] == "ON"
    assert config["other"] == "NO"
    assert config["really"] is True


@pytest.mark.base
def test_a_group_without_a_boolean_flag_and_an_empty_group_are_refused() -> None:
    """Catches a group that cannot be switched or that switches nothing.

    Both keys of a group are required: without the flag nothing can be toggled, and an
    empty group is dead text that suggests a toggle which does not exist.
    """
    without_flag = """
schema_version: 3
name: x
components: {}
groups:
  pv:
    components:
      pv_south:
        class: hisim.components.generic_pv_system.PVSystem
        preset: standard
"""
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(without_flag)
    assert caught.value.error_id is EnergySystemErrorId.GROUP_ENABLED_FLAG

    empty = "schema_version: 3\nname: x\ncomponents: {}\ngroups:\n  pv:\n    enabled: true\n    components: {}\n"
    with pytest.raises(EnergySystemFormatError) as caught:
        load_energy_system(empty)
    assert caught.value.error_id is EnergySystemErrorId.EMPTY_GROUP
