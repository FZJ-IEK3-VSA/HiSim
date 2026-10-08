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
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

from hisim import loadtypes as lt
from hisim.cli import main
from hisim.cli_exit import ExitCodes
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.electricity_meter import ElectricityMeter
from hisim.config.names import NameSyntax
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemError, EnergySystemRecordError
from hisim.energy_system.model import AggregatorFeed, DispatchSpec, EnergySystemFile
from hisim.dynamic_component import DynamicComponentConnection
from hisim.energy_system.assemblies.selection import Observer, SelectionPlan
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.imports_model import Selection
from tests.assemblies.helpers import MOCKS, OCCUPANCY, WEATHER, Mocks, build_text, expand_text, site, system_text
from tests.assemblies.mock_components import MockBattery

#: The weight of a feed an observer only measures.
MEASURED = FeedRequest.MONITORED_ONLY_WEIGHT

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


@pytest.mark.assemblies
def test_the_metered_directly_shape_writes_exactly_the_twins_feeds(tmp_path: Path) -> None:
    """No manager: the meter's default selection is, item by item, the twin's hand-written feeds (§4.2 gate)."""
    model = build(system_text("metered_house.energy_system.yaml"), tmp_path)
    assert feeds(model, "grid-Meter") == METERED_DIRECTLY
    assert model.components["heater-Controller"].inputs == ()


@pytest.mark.assemblies
def test_the_ems_with_battery_shape_writes_exactly_the_twins_feeds(tmp_path: Path) -> None:
    """With a manager: its feeds — tags, weights, dispatches — and the meter's one feed are the twin's, item by item."""
    model = build(system_text("ems_house.energy_system.yaml"), tmp_path)
    assert feeds(model, "control-EMS") == EMS_WITH_BATTERY
    assert feeds(model, "grid-Meter") == GRID_BALANCE
    assert [item.source for item in model.components["heater-Controller"].inputs] == ["control-EMS"]
    assert model.components["battery-Battery"].inputs == ()


@pytest.mark.assemblies
def test_one_device_each_gives_the_class_defaults_and_a_second_battery_follows_the_first(tmp_path: Path) -> None:
    """The weights are the controller class's own (DEFAULT_WEIGHTS); the k-th further battery gets default + k."""
    model = build(controlled("battery: {assembly: mock/home_battery, instances: {garage: {}, cellar: {}}}"), tmp_path)
    ranked = [(feed.source, feed.weight) for feed in feeds(model, "control-EMS") if feed.weight != MEASURED]
    defaults = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS
    assert ranked == [
        ("Occupancy", defaults[lt.ComponentType.RESIDENTS]),
        ("heater-Heater", defaults[lt.ComponentType.ELECTRIC_HEATING_SH]),
        ("battery-garage-Battery", defaults[lt.ComponentType.BATTERY]),
        ("battery-cellar-Battery", defaults[lt.ComponentType.BATTERY] + 1),
    ]
    assert [feed.dispatch for feed in feeds(model, "control-EMS")][-2:] == [
        DispatchSpec(target_input="LoadingPowerInput"),
        DispatchSpec(target_input="LoadingPowerInput"),
    ]


class FakeComponent:
    """A constructed component as the selection sees it: a class name, its outputs and, for an observer, its feeds."""

    def __init__(
        self,
        classname: str,
        declared: Optional[Dict[str, List[DynamicComponentConnection]]] = None,
        outputs: Tuple[str, ...] = (),
    ) -> None:
        """Names the class and the outputs it was built with; an observer also carries its default feeds."""
        self.classname = classname
        self.outputs = [SimpleNamespace(field_name=output) for output in outputs]
        setattr(self, DynamicConnectionResolver.DEFAULT_FEEDS_ATTRIBUTE, declared or {})

    def get_classname(self) -> str:
        """The class name the observer's declarations are keyed by."""
        return self.classname


def declared_feed(source_class: str, output: str, tags: List[Any], weight: int) -> DynamicComponentConnection:
    """One dynamic default connection of an observer from a source class's output."""
    return DynamicComponentConnection(
        source_component_class=MockBattery,
        source_class_name=source_class,
        source_component_field_name=output,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=tags,
        source_weight=weight,
    )


def ranked_by_fake_controller(
    declarations: List[DynamicComponentConnection], participants: Dict[str, str]
) -> List[Tuple[str, str, int]]:
    """The (source, output, weight) a fake controller selects from fake participants by their class names."""
    declared: Dict[str, List[DynamicComponentConnection]] = {}
    for declaration in declarations:
        declared.setdefault(declaration.source_class_name, []).append(declaration)
    components = {
        name: FakeComponent(
            classname, outputs=tuple(item.source_component_field_name for item in declared.get(classname, ()))
        )
        for name, classname in participants.items()
    }
    components["Controller"] = FakeComponent("FakeController", declared)
    plan = SelectionPlan(observers=[Observer("Controller", Selection(), "Controller")])
    return [(feed.source, feed.output or "", feed.weight) for feed in plan(components)["Controller"]]


@pytest.mark.assemblies
def test_a_participant_with_two_ranked_feeds_of_one_type_is_one_participant() -> None:
    """Catches the counter advancing per feed: both feeds of the first device rank at 6, the second device's at 7."""
    controlled_tags: List[Any] = [lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
    declarations = [
        declared_feed("Device", "Charge", controlled_tags, 6),
        declared_feed("Device", "Discharge", controlled_tags, 6),
    ]
    assert ranked_by_fake_controller(declarations, {"First": "Device", "Second": "Device"}) == [
        ("First", "Charge", 6),
        ("First", "Discharge", 6),
        ("Second", "Charge", 7),
        ("Second", "Discharge", 7),
    ]


@pytest.mark.assemblies
def test_participants_without_a_component_type_each_rank_at_the_declared_weight() -> None:
    """A feed without a component type counts under its source alone: two such participants keep the declared 5."""
    declarations = [declared_feed("Gadget", "Draw", [lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED], 5)]
    assert ranked_by_fake_controller(declarations, {"One": "Gadget", "Two": "Gadget"}) == [
        ("One", "Draw", 5),
        ("Two", "Draw", 5),
    ]


@pytest.mark.assemblies
def test_a_derived_weight_reaching_another_types_base_weight_is_refused(tmp_path: Path) -> None:
    """EF-7V (D27): a second space heater at 2 + 1 = 3 would tie with the hot-water heater's base weight 3."""
    heaters = [
        f"{name}: {{class: {MOCKS}.{cls}, preset: standard}}"
        for name, cls in (("Floor", "MockHeater"), ("Attic", "MockHeater"), ("Water", "MockWaterHeater"))
    ]
    message = refusal(
        site(WEATHER, OCCUPANCY, *heaters) + imports("control: {assembly: mock/ems_self_consumption}"), tmp_path
    )
    assert message.startswith("EF-7V at components.control-EMS")
    for name in (
        "Attic.ElectricityInput",
        "participant 2 of ELECTRIC_HEATING_SH",
        "2 + 1 = 3",
        "the base weight of ELECTRIC_HEATING_DHW (Water)",
        "Pin the weight on the feed",
    ):
        assert name in message, f"{name!r} is not in: {message}"


# ------------------------------------------------------------------------------------ the selection


@pytest.mark.assemblies
def test_a_selection_is_the_union_of_its_selectors_matches_in_candidate_order(tmp_path: Path) -> None:
    """``{component_type: PV}`` and ``{flow: …}`` together; the order is the file's, not the selectors'."""
    grid = (
        "grid: {assembly: mock/electricity_grid, observes: [{flow: ELECTRICITY_CONSUMPTION_UNCONTROLLED}, "
        "{component_type: PV}]}"
    )
    model = build(site(WEATHER, OCCUPANCY) + imports("pv: {assembly: mock/pv_array}", grid), tmp_path)
    assert [feed.source for feed in feeds(model, "grid-Meter")] == ["Occupancy", "pv-PVSystem"]


@pytest.mark.assemblies
def test_a_declared_output_the_built_source_does_not_have_is_no_candidate(tmp_path: Path) -> None:
    """Catches the manager selecting an output its source was built without (EF-21) instead of binding the rest."""
    occupancy = f"Occupancy: {{class: {MOCKS}.MockOccupancy, preset: standard, config: {{with_electricity: false}}}}"
    control = "control: {assembly: mock/ems_self_consumption}"
    model = build(site(WEATHER, occupancy) + imports("pv: {assembly: mock/pv_array}", control), tmp_path)
    assert [feed.source for feed in feeds(model, "control-EMS")] == ["pv-PVSystem"]


@pytest.mark.assemblies
def test_a_site_entry_observes_with_its_own_selection(tmp_path: Path) -> None:
    """``observes:`` on a site entry: its selected feeds follow its own inputs."""
    meter = f"Meter: {{class: {MOCKS}.MockElectricityMeter, preset: standard, observes: [{{component_type: PV}}]}}"
    model = build(site(WEATHER, OCCUPANCY, meter) + imports("pv: {assembly: mock/pv_array}"), tmp_path)
    assert [feed.source for feed in feeds(model, "Meter")] == ["pv-PVSystem"]


@pytest.mark.assemblies
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


@pytest.mark.assemblies
def test_an_observer_whose_class_declares_no_feeds_and_an_import_without_an_observer_port_are_refused(
    tmp_path: Path,
) -> None:
    """Catches an ``observes:`` that nothing can honour."""
    residents = f"Residents: {{class: {MOCKS}.MockOccupancy, preset: standard, observes: declared}}"
    message = refusal(site(WEATHER, residents), tmp_path / "a")
    assert message.startswith("EF-7S at components.Residents") and "declares no dynamic default connections" in message
    message = refusal(site(WEATHER) + imports("pv: {assembly: mock/pv_array, observes: declared}"), tmp_path / "b")
    assert message.startswith("EF-7J at import 'pv'") and "'mock/pv_array' has 0" in message


@pytest.mark.assemblies
def test_an_output_selected_and_fed_explicitly_is_refused_as_a_duplicate_feed(tmp_path: Path) -> None:
    """Coexistence (§4.2): written feeds stay legal, the same output selected again is EF-25."""
    written = "{from: Occupancy.ElectricityConsumption, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}"
    meter = f"Meter: {{class: {MOCKS}.MockElectricityMeter, preset: standard, inputs: [{written}], observes: declared}}"
    message = refusal(site(WEATHER, OCCUPANCY, meter), tmp_path)
    assert message.startswith("EF-25") and "Occupancy" in message


# ---------------------------------------------------------------------------------- the double count


@pytest.mark.assemblies
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
    assert "'grid-Meter' reads an output of 'control-EMS' and both observe Occupancy.ElectricityConsumption" in message
    assert "[source: grid-Meter (import grid" in message


@pytest.mark.assemblies
@pytest.mark.parametrize("occupancy", ["Occupancy.ElectricityConsumption", "Occupancy"], ids=["output", "no output"])
def test_the_double_count_check_covers_hand_written_files(tmp_path: Path, occupancy: str) -> None:
    """A flat file whose meter reads the manager's balance and a flow the manager reads: EF-7T.

    Catches a written feed that leaves its output to the meter's declaration passing as another flow.
    """
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
      - {{from: {occupancy}, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}}
"""
    message = refusal(text, tmp_path)
    assert message.startswith("EF-7T at components.Meter.inputs")
    assert "'Meter' reads an output of 'Ems' and both observe Occupancy.ElectricityConsumption" in message


# --------------------------------------------------------------------------------------- actuation


@pytest.mark.assemblies
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
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


@pytest.mark.assemblies
def test_an_import_record_written_before_the_wiring_selected_its_observers_feeds_is_refused() -> None:
    """Catches the record silently writing an observer with no feeds when serialized before the wiring."""
    _, record = expand_text(system_text("ems_house.energy_system.yaml"))
    with pytest.raises(EnergySystemRecordError, match="EF-60") as raised:
        record.to_document()
    message = str(raised.value)
    for name in ("control-EMS", "before the wiring selected its feeds"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
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
    written = record["components"]["control-EMS"]["inputs"]
    assert [item["weight"] for item in written] == [feed.weight for feed in EMS_WITH_BATTERY]
    battery = written[3]
    assert (
        battery["from"],
        battery["component_type"],
        battery["tags"],
        battery["weight"],
        battery["dispatch"]["target_input"],
    ) == (
        "battery-Battery.AcBatteryPowerUsed",
        "BATTERY",
        ["ELECTRICITY_CONSUMPTION_EMS_CONTROLLED"],
        L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS[lt.ComponentType.BATTERY],
        "LoadingPowerInput",
    )
    observers = record["metadata"]["imports"]["observers"]
    assert [(item["observer"], item["selection"], len(item["feeds"])) for item in observers] == [
        ("control-EMS", "declared", len(EMS_WITH_BATTERY)),
        ("grid-Meter", "[{output: TotalElectricityToOrFromGrid}]", len(GRID_BALANCE)),
    ]

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
