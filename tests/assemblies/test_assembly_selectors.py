"""Observe and actuate: selectors, the grid balance, the double count and the controller's weights (§4, §13.1).

HiSim has no bus: a meter and an energy manager are dynamic components that declare in their
constructor which outputs they take. ``observes: declared`` is HiSim's ``connect_automatically`` for
those declarations over the components present, and a selector filters it; the selection runs in the
wiring planner, on the constructed observer. These tests build each system on the mock library and
check the feeds item by item against the twins' two shapes, the class's own weights (a second
battery follows the first), every refusal by its code, the derived port names, and one day of the
energy-manager house whose record re-runs without selecting anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest
import yaml

from hisim import loadtypes as lt
from hisim.cli import main
from hisim.cli_exit import ExitCodes
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.electricity_meter import ElectricityMeter
from hisim.config.names import NameSyntax
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.model import AggregatorFeed, DispatchSpec, EnergySystemFile
from tests.assemblies.helpers import MOCKS, OCCUPANCY, WEATHER, Mocks, build_text, site, system_text

#: The grid import whose meter observes the energy manager's balance alone (twin ems_with_battery).
GRID_ON_BALANCE = "grid: {assembly: mock/electricity_grid, observes: [{output: TotalElectricityToOrFromGrid}]}"

#: The twin's metered_directly meter, on mock assemblies.
METERED_DIRECTLY = [
    AggregatorFeed(
        source="Occupancy", output="ElectricityConsumption", tags=("ELECTRICITY_CONSUMPTION_UNCONTROLLED",), weight=999
    ),
    AggregatorFeed(
        source="pv-PVSystem",
        output="ElectricityOutput",
        component_type="PV",
        tags=("ELECTRICITY_PRODUCTION",),
        weight=999,
    ),
    AggregatorFeed(
        source="heater-Heater",
        output="ElectricityInput",
        component_type="ELECTRIC_HEATING_SH",
        tags=("ELECTRICITY_CONSUMPTION_UNCONTROLLED",),
        weight=999,
    ),
]

#: The twin's ems_with_battery manager, on mock assemblies: the class's weights, the dispatches.
EMS_WITH_BATTERY = [
    AggregatorFeed(
        source="Occupancy",
        output="ElectricityConsumption",
        component_type="RESIDENTS",
        tags=("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",),
        weight=1,
        dispatch=DispatchSpec(),
    ),
    AggregatorFeed(
        source="pv-PVSystem",
        output="ElectricityOutput",
        component_type="PV",
        tags=("ELECTRICITY_PRODUCTION",),
        weight=999,
    ),
    AggregatorFeed(
        source="heater-Heater",
        output="ElectricityInput",
        component_type="ELECTRIC_HEATING_SH",
        tags=("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",),
        weight=2,
        dispatch=DispatchSpec(),
    ),
    AggregatorFeed(
        source="battery-Battery",
        output="AcBatteryPowerUsed",
        component_type="BATTERY",
        tags=("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",),
        weight=6,
        dispatch=DispatchSpec(target_input="LoadingPowerInput"),
    ),
]

#: The twin's ems_with_battery meter: the manager's balance alone.
GRID_BALANCE = [
    AggregatorFeed(
        source="control-EMS", output="TotalElectricityToOrFromGrid", tags=("ELECTRICITY_PRODUCTION",), weight=999
    ),
]


def imports(*lines: str) -> str:
    """An ``imports:`` block of one-line imports."""
    return "imports:\n" + "".join(f"  {line}\n" for line in lines)


def build(text: str, directory: Path) -> EnergySystemFile:
    """Builds an inline file on the mock library: the file with the selected feeds written in."""
    return build_text(text, directory).model


def refusal(text: str, directory: Path) -> str:
    """The message a build refuses an inline file with."""
    with pytest.raises(EnergySystemError) as raised:
        build_text(text, directory)
    return str(raised.value)


def feeds(model: EnergySystemFile, component: str) -> List[AggregatorFeed]:
    """The aggregator feeds of one built component, in written order."""
    return [item for item in model.components[component].inputs if isinstance(item, AggregatorFeed)]


def controlled(*extra: str) -> str:
    """A house with PV, the heater bound to the controller, the controller, the extra imports and the balance grid."""
    return site(WEATHER, OCCUPANCY) + imports(
        "pv: {assembly: mock/pv_array}",
        "heater: {assembly: mock/smart_heater, optional-bind: {ems_modifier: control}}",
        *extra,
        "control: {assembly: mock/ems_self_consumption}",
        GRID_ON_BALANCE,
    )


# ---------------------------------------------------------------------------------- the twin shapes


@pytest.mark.base
def test_the_metered_directly_shape_writes_exactly_the_twins_feeds(tmp_path: Path) -> None:
    """No manager: the meter's default selection is, item by item, the twin's hand-written feeds (§4.2 gate)."""
    model = build(system_text("metered_house.energy_system.yaml"), tmp_path)
    assert feeds(model, "grid-Meter") == METERED_DIRECTLY
    assert model.components["heater-Controller"].inputs == ()


@pytest.mark.base
def test_the_ems_with_battery_shape_writes_exactly_the_twins_feeds(tmp_path: Path) -> None:
    """With a manager: its feeds — tags, weights, dispatches — and the meter's one feed are the twin's, item by item."""
    model = build(system_text("ems_house.energy_system.yaml"), tmp_path)
    assert feeds(model, "control-EMS") == EMS_WITH_BATTERY
    assert feeds(model, "grid-Meter") == GRID_BALANCE
    assert [item.source for item in model.components["heater-Controller"].inputs] == ["control-EMS"]
    assert model.components["battery-Battery"].inputs == ()


@pytest.mark.base
def test_one_device_each_gives_the_class_defaults_and_a_second_battery_follows_the_first(tmp_path: Path) -> None:
    """The weights are the controller class's own (DEFAULT_WEIGHTS); the k-th further battery gets 6 + k."""
    model = build(controlled("battery: {assembly: mock/home_battery, instances: {garage: {}, cellar: {}}}"), tmp_path)
    ranked = [(feed.source, feed.weight) for feed in feeds(model, "control-EMS") if feed.weight != 999]
    defaults = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS
    assert ranked == [
        ("Occupancy", defaults[lt.ComponentType.RESIDENTS]),
        ("heater-Heater", defaults[lt.ComponentType.ELECTRIC_HEATING_SH]),
        ("battery-garage-Battery", 6),
        ("battery-cellar-Battery", 7),
    ]
    assert [feed.dispatch for feed in feeds(model, "control-EMS")][-2:] == [
        DispatchSpec(target_input="LoadingPowerInput"),
        DispatchSpec(target_input="LoadingPowerInput"),
    ]


# ------------------------------------------------------------------------------------ the selection


@pytest.mark.base
def test_a_selection_is_the_union_of_its_selectors_matches_in_candidate_order(tmp_path: Path) -> None:
    """``{component_type: PV}`` and ``{flow: …}`` together; the order is the file's, not the selectors'."""
    grid = (
        "grid: {assembly: mock/electricity_grid, observes: [{flow: ELECTRICITY_CONSUMPTION_UNCONTROLLED}, "
        "{component_type: PV}]}"
    )
    model = build(site(WEATHER, OCCUPANCY) + imports("pv: {assembly: mock/pv_array}", grid), tmp_path)
    assert [feed.source for feed in feeds(model, "grid-Meter")] == ["Occupancy", "pv-PVSystem"]


@pytest.mark.base
def test_a_site_entry_observes_with_its_own_selection(tmp_path: Path) -> None:
    """``observes:`` on a site entry: its selected feeds follow its own inputs."""
    meter = f"Meter: {{class: {MOCKS}.MockElectricityMeter, preset: standard, observes: [{{component_type: PV}}]}}"
    model = build(site(WEATHER, OCCUPANCY, meter) + imports("pv: {assembly: mock/pv_array}"), tmp_path)
    assert [feed.source for feed in feeds(model, "Meter")] == ["pv-PVSystem"]


@pytest.mark.base
@pytest.mark.parametrize(
    ("observes", "code", "fragment"),
    [
        ("[{component_type: BATTERY}]", "EF-7S", "the selector {component_type: BATTERY} of 'grid-Meter' matches no"),
        ("[{}]", "EF-70", "an empty selector selects everything"),
        ("[{component_type: PV, feed: {weight: 3}}]", "EF-73", "'feed': feed: overrides are not in v1"),
        ("[{component_type: PV, required: true}]", "EF-73", "'required': required: is not in v1"),
        ("[{component_type: SOLAR_PANEL}]", "EF-70", "'SOLAR_PANEL' is no component_type tag"),
    ],
)
def test_a_selection_matching_nothing_and_every_cut_selector_key_are_refused(
    tmp_path: Path, observes: str, code: str, fragment: str
) -> None:
    """Catches a selector that finds nothing passing silently, and the cut feed:/required: keys being read."""
    grid = f"grid: {{assembly: mock/electricity_grid, observes: {observes}}}"
    message = refusal(site(WEATHER, OCCUPANCY) + imports("pv: {assembly: mock/pv_array}", grid), tmp_path)
    assert message.startswith(code) and fragment in message


@pytest.mark.base
def test_an_observer_whose_class_declares_no_feeds_and_an_import_without_an_observer_port_are_refused(
    tmp_path: Path,
) -> None:
    """Catches an ``observes:`` that nothing can honour."""
    residents = f"Residents: {{class: {MOCKS}.MockOccupancy, preset: standard, observes: declared}}"
    message = refusal(site(WEATHER, residents), tmp_path / "a")
    assert message.startswith("EF-7S at components.Residents") and "declares no dynamic default connections" in message
    message = refusal(site(WEATHER) + imports("pv: {assembly: mock/pv_array, observes: declared}"), tmp_path / "b")
    assert message.startswith("EF-7J at import 'pv'") and "'mock/pv_array' has 0" in message


@pytest.mark.base
def test_an_output_selected_and_fed_explicitly_is_refused_as_a_duplicate_feed(tmp_path: Path) -> None:
    """Coexistence (§4.2): written feeds stay legal, the same output selected again is EF-25."""
    written = "{from: Occupancy.ElectricityConsumption, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}"
    meter = f"Meter: {{class: {MOCKS}.MockElectricityMeter, preset: standard, inputs: [{written}], observes: declared}}"
    message = refusal(site(WEATHER, OCCUPANCY, meter), tmp_path)
    assert message.startswith("EF-25") and "Occupancy" in message


# ---------------------------------------------------------------------------------- the double count


@pytest.mark.base
def test_the_grid_left_at_its_default_beside_a_controller_counts_the_flows_twice(tmp_path: Path) -> None:
    """EF-7T: the meter reads the manager's balance and the flows the manager observes."""
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports(
            "pv: {assembly: mock/pv_array}",
            "control: {assembly: mock/ems_self_consumption}",
            "grid: {assembly: mock/electricity_grid}",
        ),
        tmp_path,
    )
    assert message.startswith("EF-7T at components.grid-Meter.inputs")
    assert "observes the balance of 'control-EMS' and also Occupancy.ElectricityConsumption" in message
    assert "[source: grid-Meter (import grid" in message


@pytest.mark.base
def test_the_double_count_check_covers_hand_written_files(tmp_path: Path) -> None:
    """A flat file whose meter is written to read the manager's balance and a flow the manager reads: EF-7T."""
    text = f"""\
schema_version: 3
name: written
components:
  Weather: {{class: {MOCKS}.MockWeather, preset: standard}}
  Occupancy: {{class: {MOCKS}.MockOccupancy, preset: standard}}
  Ems:
    class: {MOCKS}.MockEnergyManager
    preset: optimize_own_consumption
    inputs:
      - from: Occupancy.ElectricityConsumption
        component_type: RESIDENTS
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 1
        dispatch: {{}}
  Meter:
    class: {MOCKS}.MockElectricityMeter
    preset: standard
    inputs:
      - {{from: Ems.TotalElectricityToOrFromGrid, tags: [ELECTRICITY_PRODUCTION], weight: 999}}
      - {{from: Occupancy.ElectricityConsumption, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}}
"""
    message = refusal(text, tmp_path)
    assert message.startswith("EF-7T at components.Meter.inputs") and "Occupancy.ElectricityConsumption" in message


# --------------------------------------------------------------------------------------- actuation


@pytest.mark.base
@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (
            site(WEATHER, OCCUPANCY)
            + imports("pv: {assembly: mock/pv_array}", "battery: {assembly: mock/home_battery}"),
            "is ranked by no controller",
        ),
        (
            controlled("battery: {assembly: mock/home_battery}", "second: {assembly: mock/ems_self_consumption}"),
            "is ranked by second-EMS, control-EMS",
        ),
        (
            site(WEATHER, OCCUPANCY)
            + imports(
                "pv: {assembly: mock/pv_array}",
                "heater: {assembly: mock/smart_heater, optional-bind: {ems_modifier: control}}",
                "control: {assembly: mock/ems_self_consumption, observes: [{component_type: [RESIDENTS, PV]}]}",
                GRID_ON_BALANCE,
            ),
            "but its need is bound to control-EMS",
        ),
    ],
    ids=["no controller", "two controllers", "via bound, not ranked"],
)
def test_a_controllable_output_is_actuated_by_exactly_the_one_controller_it_binds(
    tmp_path: Path, text: str, fragment: str
) -> None:
    """EF-7U: a battery with no controller or two, a heater bound to a controller that does not rank it."""
    message = refusal(text, tmp_path)
    assert message.startswith("EF-7U") and fragment in message


# ------------------------------------------------------------------------------- derived port names


@pytest.mark.base
def test_derived_port_names_replace_the_address_separator_and_a_collision_is_refused(tmp_path: Path) -> None:
    """``-`` becomes ``_`` in a derived port; a site entry named like the result collides (EF-32)."""
    assert NameSyntax.port_name_part("pv-east-PVSystem") == "pv_east_PVSystem"
    built = build_text(system_text("ems_house.energy_system.yaml"), tmp_path / "a")
    ems = built.wired.component_of("control-EMS")
    assert "DispatchTobattery_Battery_LoadingPowerInput" in [output.field_name for output in ems.outputs]
    roof = f"pv_PVSystem: {{class: {MOCKS}.MockPVSystem, preset: rooftop, inputs: [Weather]}}"
    grid = "grid: {assembly: mock/electricity_grid, observes: [{component_type: PV}]}"
    message = refusal(site(WEATHER, OCCUPANCY, roof) + imports("pv: {assembly: mock/pv_array}", grid), tmp_path / "b")
    assert message.startswith("EF-32") and "ElectricityOutputFrompv_PVSystem" in message


@pytest.mark.base
def test_the_real_meter_declares_its_feed_from_the_real_ems() -> None:
    """The ElectricityMeter's dynamic default connection from the EMS's grid balance (§4.3, dry run G6)."""
    meter = ElectricityMeter.__new__(ElectricityMeter)
    connections = meter.get_default_connections_from_energy_management_system()
    assert len(connections) == 1
    connection = connections[0]
    assert connection.source_class_name == L2GenericEnergyManagementSystem.get_classname()
    assert connection.source_component_field_name == "TotalElectricityToOrFromGrid"
    assert connection.source_tags == [lt.InandOutputType.ELECTRICITY_PRODUCTION]
    assert connection.source_weight == 999


# ------------------------------------------------------------------------------------------ one day


@pytest.mark.base
def test_the_ems_house_runs_a_day_with_the_balance_closed_and_its_record_reruns_selecting_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The record writes the selected feeds as ordinary feeds and the observers' feeds; a re-run reproduces it."""
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    first = tmp_path / "first"
    system = str(Mocks.ROOT / "systems" / "ems_house.energy_system.yaml")
    parameters = str(Mocks.ROOT / "one_day_balance.simulation.yaml")
    assert main(["energy-system", "run", system, parameters, "--result-dir", str(first)]) == ExitCodes.OK

    balance = json.loads((first / "balance_report.json").read_text(encoding="utf-8"))
    assert balance["verdict"] == "closes"
    record = yaml.safe_load((first / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    assert [item["weight"] for item in record["components"]["control-EMS"]["inputs"]] == [1, 999, 2, 6]
    observers = record["metadata"]["imports"]["observers"]
    assert [(item["observer"], item["selection"]) for item in observers] == [
        ("control-EMS", "declared"),
        ("grid-Meter", "[{output: TotalElectricityToOrFromGrid}]"),
    ]
    assert observers[0]["feeds"][3] == (
        "battery-Battery.AcBatteryPowerUsed [BATTERY; ELECTRICITY_CONSUMPTION_EMS_CONTROLLED] weight 6, "
        "dispatch LoadingPowerInput"
    )

    monkeypatch.delenv(AssemblyResolver.ENVIRONMENT_VARIABLE)
    second = tmp_path / "second"
    code = main(
        [
            "energy-system",
            "run",
            str(first / "realized.energy_system.yaml"),
            str(first / "realized.simulation.yaml"),
            "--result-dir",
            str(second),
            "--rerun",
        ]
    )
    capsys.readouterr()
    assert code == ExitCodes.OK
    again = yaml.safe_load((second / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    assert again["components"] == record["components"]
    assert again["metadata"]["imports"] == record["metadata"]["imports"]
