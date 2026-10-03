"""Sizing across assemblies (``assemblies_spec.md`` §6, §13 step 3, D10; bead hisim-lt0b.4).

Four things are proven here on fixture classes. A ``many: true`` fact port binds every provider of its
fact in scope and lowers to a ``sizing_sources`` list in instance order, which a ``Sum(Many(...))`` law
adds up; reordering the instances reorders the list and never the sum, and one array gives exactly the
number the one-provider law gives, down to the CSVs (and, for the real battery, the heat-pump twin's
sized values). A member's contribution is internal to its assembly unless the assembly exports it: a
reader outside binds to its previous provider by an explicit line the expansion writes and records, an
exported contribution joins the provider set, and a read inside an assembly binds to its own member
first. A fuel provider whose consumers would need different fuel constants is refused (D10).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

from hisim.cli import main
from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.configure import ConfiguredSystem, configure_energy_system
from hisim.energy_system.errors import EnergySystemAssemblyError, EnergySystemErrorId, EnergySystemSizingError
from hisim.energy_system.groups import expand_groups
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.model import EnergySystemFile, SourceReference
from tests.assemblies.helpers import WEATHER, Fixtures, Library, expand_text, fixture_resolver, site

FACT = "pv_peak_power_in_watt"
POWER = "maximal_thermal_power_in_watt"
TWO_ARRAYS = Fixtures.SYSTEMS / "two_array_house.energy_system.yaml"
CSV_PARAMETERS = Fixtures.ROOT / "one_day_csv.simulation.yaml"
REPOSITORY = Path(__file__).resolve().parents[2]


def configure(model: EnergySystemFile) -> ConfiguredSystem:
    """Expands the groups of an expanded file and sizes it, as a run does before building."""
    expanded, _record = expand_groups(model)
    return configure_energy_system(expanded)


def two_arrays(east_first: bool = True) -> str:
    """The two-array house, its instances written east first or west first."""
    east = "east: {azimuth_in_degree: 90, facing: east}"
    west = "west: {azimuth_in_degree: 270, facing: west, power_in_watt: 3000}"
    first, second = (east, west) if east_first else (west, east)
    return (
        site(WEATHER)
        + "imports:\n  pv:\n    assembly: pv/array\n    instances:\n"
        + f"      {first}\n      {second}\n"
        + "  battery: {assembly: storage/array_battery}\n"
    )


def run(system: Path, parameters: Path, result: Path, *extra: str) -> Path:
    """Runs one fixture system through the console command against the fixture library."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Fixtures.LIBRARY))
    try:
        code = main(["energy-system", "run", str(system), str(parameters), "--result-dir", str(result), *extra])
    finally:
        monkeypatch.undo()
    assert code == 0
    return result


def load(path: Path) -> Dict[str, Any]:
    """A YAML file a run wrote."""
    loaded: Dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


# --------------------------------------------------------------------------------- many: true, Sum


@pytest.mark.base
def test_a_many_port_binds_every_array_in_instance_order_and_the_record_lists_them() -> None:
    """``many: true`` lowers to a list of every provider in scope, in the order the instances are written."""
    expanded, record = expand_text(TWO_ARRAYS.read_text(encoding="utf-8"))

    assert expanded.components["battery-Battery"].sizing_sources[FACT] == (
        SourceReference(component="pv-east-PVSystem", fact=FACT),
        SourceReference(component="pv-west-PVSystem", fact=FACT),
    )
    port = record.instance("battery").port("pv_power")  # type: ignore[union-attr]
    assert port is not None and port.partner == "pv-east-PVSystem, pv-west-PVSystem"
    assert "battery.pv_power -> [pv-east-PVSystem, pv-west-PVSystem] (fact pv_peak_power_in_watt, many, default)" in (
        record.decisions
    )
    note = record.source_map.of("battery-Battery", f"sizing_sources.{FACT}")
    assert note is not None and "port pv_power (many) bound to pv-east-PVSystem, pv-west-PVSystem" in note.note


@pytest.mark.base
def test_the_battery_is_sized_to_the_sum_of_both_arrays_and_the_audit_names_each() -> None:
    """The capacity is (5 + 3) kWp at 1 kWh/kWp; the sizing record and the report name both arrays."""
    expanded, _record = expand_text(TWO_ARRAYS.read_text(encoding="utf-8"))
    configured = configure(expanded)
    battery = configured.config_of("battery-Battery")

    assert battery.capacity_in_kwh == 8.0
    (entry,) = battery.sizing_record
    assert entry.law == "(0.001 * Sum(Many(Size.PV_PEAK_POWER_IN_WATT))).rounded(2)"
    assert entry.inputs == (
        ("pv-east-PVSystem.pv_peak_power_in_watt", 5000.0),
        ("pv-west-PVSystem.pv_peak_power_in_watt", 3000.0),
    )
    lookups = [
        (item.source, item.value, item.many) for item in configured.report.lookups if item.consumer == "battery-Battery"
    ]
    assert lookups == [("pv-east-PVSystem", 5000.0, True), ("pv-west-PVSystem", 3000.0, True)]


@pytest.mark.base
def test_a_shuffled_written_order_reorders_the_list_but_not_the_sum() -> None:
    """Writing the west array first lists it first; the battery's capacity is the same number."""
    sizes: List[float] = []
    orders: List[Tuple[str, ...]] = []
    for east_first in (True, False):
        expanded, _record = expand_text(two_arrays(east_first))
        references = expanded.components["battery-Battery"].sizing_sources[FACT]
        assert isinstance(references, tuple)
        orders.append(tuple(item.component for item in references))
        sizes.append(configure(expanded).config_of("battery-Battery").capacity_in_kwh)

    assert orders == [("pv-east-PVSystem", "pv-west-PVSystem"), ("pv-west-PVSystem", "pv-east-PVSystem")]
    assert sizes == [8.0, 8.0]


@pytest.mark.base
def test_the_two_array_house_runs_and_its_record_re_runs_with_the_list(tmp_path: Path) -> None:
    """The realized record writes the list and both inputs; re-running it reproduces it without assemblies."""
    first = run(TWO_ARRAYS, Fixtures.PARAMETERS, tmp_path / "first")
    record = load(first / "realized.energy_system.yaml")
    audit = load(first / "realized.audit.yaml")

    assert record["components"]["battery-Battery"]["sizing_sources"][FACT] == [
        "pv-east-PVSystem.pv_peak_power_in_watt",
        "pv-west-PVSystem.pv_peak_power_in_watt",
    ]
    sized = audit["components"]["battery-Battery"]["sized_fields"]
    assert sized == [
        {
            "field": "capacity_in_kwh",
            "law": "(0.001 * Sum(Many(Size.PV_PEAK_POWER_IN_WATT))).rounded(2)",
            "kind": "law",
            "inputs": [
                ["pv-east-PVSystem.pv_peak_power_in_watt", 5000.0],
                ["pv-west-PVSystem.pv_peak_power_in_watt", 3000.0],
            ],
            "value": 8.0,
        }
    ]
    run(first / "realized.energy_system.yaml", Fixtures.PARAMETERS, tmp_path / "second", "--rerun")


@pytest.mark.base
def test_one_array_sized_by_the_sum_is_byte_identical_to_the_one_provider_law(tmp_path: Path) -> None:
    """The sum of one provider is that provider's value: every CSV of the run is the same, byte for byte."""

    def one_array(assembly: str) -> Path:
        system = tmp_path / f"{assembly.replace('/', '_')}.energy_system.yaml"
        system.write_text(
            site(WEATHER) + "imports:\n  pv: {assembly: pv/array}\n" + f"  battery: {{assembly: {assembly}}}\n",
            encoding="utf-8",
        )
        return run(system, CSV_PARAMETERS, tmp_path / assembly.replace("/", "_"))

    scalar, summed = one_array("storage/battery"), one_array("storage/array_battery")
    csvs = sorted(path.name for path in scalar.glob("*.csv"))

    assert "battery-Battery_Capacity.csv" in csvs
    assert csvs == sorted(path.name for path in summed.glob("*.csv"))
    for name in csvs:
        assert (scalar / name).read_bytes() == (summed / name).read_bytes(), name


@pytest.mark.base
def test_the_real_battery_sums_one_array_to_the_twins_numbers() -> None:
    """The heat-pump twin's battery, its fields set AUTO (the summing class laws), sizes to the preset's numbers."""
    twin = parse_energy_system(REPOSITORY / "energy_systems" / "household_heatpump_building_sizer.energy_system.yaml")
    battery = twin.components["Battery"]
    summing = twin.model_copy(
        update={
            "components": {
                **dict(twin.components),
                "Battery": battery.model_copy(
                    update={
                        "config": {
                            "custom_battery_capacity_generic_in_kilowatt_hour": "AUTO",
                            "custom_pv_inverter_power_generic_in_watt": "AUTO",
                        }
                    }
                ),
            }
        }
    )

    one, many = configure(twin).config_of("Battery"), configure(summing).config_of("Battery")

    for field in ("custom_battery_capacity_generic_in_kilowatt_hour", "custom_pv_inverter_power_generic_in_watt"):
        assert getattr(many, field) == getattr(one, field)
        assert type(getattr(many, field)) is type(getattr(one, field))
    assert [entry.law for entry in one.sizing_record] == [
        "(0.5 * Size.PV_PEAK_POWER_IN_WATT).rounded(2)",
        "(0.001 * Size.PV_PEAK_POWER_IN_WATT).rounded(2)",
    ]
    assert [entry.law for entry in many.sizing_record] == [
        "(0.5 * Sum(Many(Size.PV_PEAK_POWER_IN_WATT))).rounded(2)",
        "(0.001 * Sum(Many(Size.PV_PEAK_POWER_IN_WATT))).rounded(2)",
    ]


ONE_ARRAY_BATTERY = """
schema_version: 4
kind: assembly
name: sizing/{name}
parameters:
  capacity_in_kwh: {{type: float, unit: KWH, default: AUTO}}
components:
  Battery:
    class: tests.assemblies.fixture_components.{cls}
    preset: {preset}
    config: {{capacity_in_kwh: {{$param: capacity_in_kwh}}}}
interface:
  needs:
    pv_power: {{fact: pv_peak_power_in_watt, into: [Battery]{many}}}
"""


def battery_library(tmp_path: Path) -> Library:
    """The two mismatched batteries: a list into a one-provider law, one provider into a sum."""
    library = Library(tmp_path)
    library.add(
        "sizing/list_into_one",
        ONE_ARRAY_BATTERY.format(name="list_into_one", cls="FakeBattery", preset="sized_to_pv", many=", many: true"),
    )
    library.add(
        "sizing/one_into_sum",
        ONE_ARRAY_BATTERY.format(name="one_into_sum", cls="FakeArrayBattery", preset="sized_to_all_arrays", many=""),
    )
    return library


@pytest.mark.base
def test_a_fact_port_whose_cardinality_is_not_the_laws_is_refused(tmp_path: Path) -> None:
    """A list into a one-provider law and one provider into a sum are both EF-7X, naming field and law."""
    library = battery_library(tmp_path)
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(two_arrays().replace("storage/array_battery", "sizing/list_into_one"), library.resolver())
    assert raised.value.error_id is EnergySystemErrorId.FACT_READ_CARDINALITY
    assert (
        "fact port 'pv_power' (many: true) lowers the list [pv-east-PVSystem, pv-west-PVSystem] into battery-Battery, "
        "but FakeBatteryConfig.capacity_in_kwh <- (0.001 * Size.PV_PEAK_POWER_IN_WATT).rounded(2) reads "
        "pv_peak_power_in_watt from one provider"
    ) in str(raised.value)

    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(
            site(WEATHER) + "imports:\n  pv: {assembly: pv/array}\n  battery: {assembly: sizing/one_into_sum}\n",
            library.resolver(),
        )
    assert raised.value.error_id is EnergySystemErrorId.FACT_READ_CARDINALITY
    assert "sums every provider of pv_peak_power_in_watt" in str(raised.value)


@pytest.mark.base
def test_a_pinned_field_reads_nothing_so_its_cardinality_is_not_checked(tmp_path: Path) -> None:
    """A list into a one-provider law is fine while the file pins the field: nothing reads the list."""
    library = battery_library(tmp_path)
    text = two_arrays().replace(
        "battery: {assembly: storage/array_battery}",
        "battery: {assembly: sizing/list_into_one, parameters: {capacity_in_kwh: 4.0}}",
    )
    expanded, _record = expand_text(text, library.resolver())

    assert configure(expanded).config_of("battery-Battery").capacity_in_kwh == 4.0


@pytest.mark.base
def test_a_many_port_takes_no_verb_and_needs_a_provider() -> None:
    """A verb cannot choose among the providers; without any the port is refused like a required one."""
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(
            two_arrays().replace(
                "{assembly: storage/array_battery}", "{assembly: storage/array_battery, bind: {pv_power: pv.east}}"
            )
        )
    assert raised.value.error_id is EnergySystemErrorId.PORT_CONTRACT
    assert "binds every provider of pv_peak_power_in_watt in scope (pv-east-PVSystem, pv-west-PVSystem)" in str(
        raised.value
    )

    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(site(WEATHER) + "imports:\n  battery: {assembly: storage/array_battery}\n")
    assert raised.value.error_id is EnergySystemErrorId.FACT_NOT_PROVIDED


# ------------------------------------------------------------------------------------- fact exports


BURNER = """
schema_version: 4
kind: assembly
name: dhw/{name}
components:
  Burner: {{class: tests.assemblies.fixture_components.FakeBurner, preset: condensing}}
{extra}"""

SITE_BOILER = """
Boiler:
  class: tests.assemblies.fixture_components.FakeBurner
  preset: condensing
  config: {power_in_watt: 20000.0}
"""

SITE_BUFFER = """
Buffer:
  class: tests.assemblies.fixture_components.FakeBuffer
  preset: sized_to_generator
"""


def export_library(tmp_path: Path) -> Library:
    """The DHW assemblies of the export rule: a burner kept internal, exported, re-exported, and one beside a buffer."""
    library = Library(tmp_path)
    library.add("dhw/internal_burner", BURNER.format(name="internal_burner", extra=""))
    library.add(
        "dhw/exported_burner",
        BURNER.format(
            name="exported_burner",
            extra="interface:\n  provides:\n    burner_power: {fact: maximal_thermal_power_in_watt, member: Burner}\n",
        ),
    )
    library.add(
        "dhw/buffered_burner",
        BURNER.format(
            name="buffered_burner",
            extra="  Buffer: {class: tests.assemblies.fixture_components.FakeBuffer, preset: sized_to_generator}\n",
        ),
    )
    for name, interface in (
        ("reexporting", "interface:\n  provides:\n    burner_power: {from: burner.burner_power}\n"),
        ("enclosing", ""),
    ):
        library.add(
            f"dhw/{name}",
            f"schema_version: 4\nkind: assembly\nname: dhw/{name}\n"
            "imports:\n  burner: {assembly: dhw/exported_burner}\n" + interface,
        )
    return library


def house(*entries: str, dhw: str) -> str:
    """A site with the given entries and one DHW import."""
    return site(*entries) + f"imports:\n  dhw: {{assembly: {dhw}}}\n"


@pytest.mark.base
def test_an_unexported_burner_leaves_the_site_buffer_on_its_previous_provider(tmp_path: Path) -> None:
    """Without the export the buffer stays on the site boiler: the expansion writes the line and records why."""
    library = export_library(tmp_path)
    expanded, record = expand_text(house(SITE_BOILER, SITE_BUFFER, dhw="dhw/internal_burner"), library.resolver())

    assert expanded.components["Buffer"].sizing_sources == {POWER: SourceReference(component="Boiler", fact=POWER)}
    (scoped,) = record.scoped_sizing
    assert (scoped.reader, scoped.fact, scoped.providers, scoped.internal) == (
        "Buffer",
        POWER,
        ("Boiler",),
        ("dhw-Burner",),
    )
    assert scoped.reason == "dhw-Burner (import dhw) is internal, not exported"
    assert record.to_document()["scoped_sizing_sources"][0]["providers"] == ["Boiler"]
    assert configure(expanded).config_of("Buffer").volume_in_liter == 400.0


@pytest.mark.base
def test_an_exported_burner_joins_the_provider_set_and_makes_the_buffer_ambiguous(tmp_path: Path) -> None:
    """With the export the burner is a provider like the boiler: the bare read is the engine's ambiguity."""
    library = export_library(tmp_path)
    expanded, record = expand_text(house(SITE_BOILER, SITE_BUFFER, dhw="dhw/exported_burner"), library.resolver())

    assert expanded.components["Buffer"].sizing_sources == {}
    assert record.scoped_sizing == []
    with pytest.raises(EnergySystemSizingError) as raised:
        configure(expanded)
    assert raised.value.error_id is EnergySystemErrorId.SIZING_AMBIGUOUS
    assert "is provided by Boiler, dhw-Burner" in str(raised.value)


@pytest.mark.base
def test_an_export_reaches_the_site_only_when_every_enclosing_assembly_re_exports_it(tmp_path: Path) -> None:
    """An inner import's export stops at the enclosing assembly unless that one re-exports it with ``from:``."""
    library = export_library(tmp_path)
    kept, record = expand_text(house(SITE_BOILER, SITE_BUFFER, dhw="dhw/enclosing"), library.resolver())
    assert kept.components["Buffer"].sizing_sources == {POWER: SourceReference(component="Boiler", fact=POWER)}
    assert record.scoped_sizing[0].internal == ("dhw-burner-Burner",)

    passed, record = expand_text(house(SITE_BOILER, SITE_BUFFER, dhw="dhw/reexporting"), library.resolver())
    assert passed.components["Buffer"].sizing_sources == {}
    assert record.scoped_sizing == []


@pytest.mark.base
def test_a_read_inside_an_assembly_binds_its_own_member_first(tmp_path: Path) -> None:
    """The buffer inside the DHW assembly takes its own burner, though the site's boiler provides the fact too."""
    library = export_library(tmp_path)
    expanded, record = expand_text(house(SITE_BOILER, dhw="dhw/buffered_burner"), library.resolver())

    assert expanded.components["dhw-Buffer"].sizing_sources == {
        POWER: SourceReference(component="dhw-Burner", fact=POWER)
    }
    (scoped,) = record.scoped_sizing
    assert scoped.reason == "the read binds inside its own import dhw first"
    assert configure(expanded).config_of("dhw-Buffer").volume_in_liter == 200.0


@pytest.mark.base
def test_a_read_only_an_internal_contribution_answers_is_refused_unless_nothing_reads_it(tmp_path: Path) -> None:
    """No site boiler: the site buffer would bind to the burner inside the DHW assembly, which is EF-7Y."""
    library = export_library(tmp_path)
    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(house(SITE_BUFFER, dhw="dhw/internal_burner"), library.resolver())
    assert raised.value.error_id is EnergySystemErrorId.FACT_NOT_EXPORTED
    assert "'Buffer' reads maximal_thermal_power_in_watt, and its only providers dhw-Burner (import dhw)" in str(
        raised.value
    )

    pinned = SITE_BUFFER + "  config: {volume_in_liter: 300.0}\n"
    expanded, record = expand_text(house(pinned, dhw="dhw/internal_burner"), library.resolver())
    assert expanded.components["Buffer"].sizing_sources == {} and record.scoped_sizing == []


@pytest.mark.base
def test_a_reader_in_an_enabled_group_gets_its_line_in_the_group(tmp_path: Path) -> None:
    """The live set is what the engine sizes: an enabled group's buffer gets the line in its group entry."""
    library = export_library(tmp_path)
    text = (
        site(SITE_BOILER)
        + "groups:\n  storage:\n    enabled: true\n    components:\n"
        + "      Buffer: {class: tests.assemblies.fixture_components.FakeBuffer, preset: sized_to_generator}\n"
        + "imports:\n  dhw: {assembly: dhw/internal_burner}\n"
    )
    expanded, record = expand_text(text, library.resolver())

    assert expanded.groups["storage"].components["Buffer"].sizing_sources == {
        POWER: SourceReference(component="Boiler", fact=POWER)
    }
    assert [scoped.reader for scoped in record.scoped_sizing] == ["Buffer"]
    assert configure(expanded).config_of("Buffer").volume_in_liter == 400.0


# ---------------------------------------------------------------------------------- fuel constants


BURNER_HEATING = """
schema_version: 4
kind: assembly
name: heating/{preset}_burner
components:
  Burner: {{class: tests.assemblies.fixture_components.FakeBurner, preset: {preset}}}
interface:
  needs:
    fuel: {{carrier: natural_gas, outputs: [Burner.FuelUse]}}
"""


@pytest.mark.base
def test_a_gas_provider_serving_burners_of_different_fuel_constants_is_refused(tmp_path: Path) -> None:
    """A condensing and a conventional burner on one meter: different heating values, EF-7Z naming both."""
    library = Library(tmp_path)
    for preset in ("condensing", "conventional"):
        library.add(f"heating/{preset}_burner", BURNER_HEATING.format(preset=preset))
    text = site(WEATHER) + (
        "imports:\n  gas: {assembly: supply/gas_connection}\n"
        "  space: {assembly: heating/condensing_burner}\n  water: {assembly: heating/conventional_burner}\n"
    )

    with pytest.raises(EnergySystemAssemblyError) as raised:
        expand_text(text, library.resolver())
    message = str(raised.value)
    assert raised.value.error_id is EnergySystemErrorId.FUEL_CONSTANTS_DIFFER
    assert "provider gas.connection (meter gas-Meter) of natural_gas serves space-Burner (GAS, " in message
    assert " kWh/l, " in message and ") and water-Burner (GAS, " in message

    same, record = expand_text(text.replace("conventional_burner", "condensing_burner"), library.resolver())
    assert [consumer.owner for consumer in record.carriers[0].consumers] == ["space", "water"]
    assert "space-Burner" in same.components and "water-Burner" in same.components


@pytest.mark.base
def test_a_file_without_imports_is_untouched_by_the_scope_rules() -> None:
    """Every committed energy-system file still expands to itself, the very same object."""
    paths = sorted((REPOSITORY / "energy_systems").glob("*.energy_system.yaml"))
    assert paths
    for path in paths:
        model = parse_energy_system(path)
        assert expand_imports(model, fixture_resolver())[0] is model, path.name
