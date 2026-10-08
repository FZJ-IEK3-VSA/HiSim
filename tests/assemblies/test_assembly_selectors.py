"""Observe and actuate: selectors, the grid balance, the double count and the controller's weights (§4, §13.1).

HiSim has no bus: a meter and an energy manager are dynamic components that declare in their
constructor which outputs they take. ``observes: declared`` is HiSim's ``connect_automatically`` for
those declarations over the components present, and a selector filters it; the selection runs in the
wiring planner, on the constructed observer. These tests build each system on the real library —
``pv/array``, ``storage/battery``, ``control/ems_self_consumption``, ``supply/electricity_grid`` and the
heat-pump composed file — and check the feeds item by item against the twin's ``metered_directly``
shape, the class's own weights (a second battery follows the first), every refusal by its code, the
derived port names, and one day of the heat-pump house whose record re-runs without selecting
anything. The twin's other shape, ``ems_with_battery``, is every composed file's, which the twin gates
compare feed by feed (``test_twin_gates.py``). A refusal the real classes cannot show — a weight
derived into another type's base weight, which needs two heaters of one type beside a heater of
another — runs on mock site entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import pytest
import yaml

from hisim import loadtypes as lt
from hisim.cli import main
from hisim.cli_exit import ExitCodes
from hisim.components.advanced_battery_bslib import Battery
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.electricity_meter import ElectricityMeter
from hisim.config.names import NameSyntax
from hisim.dynamic_component import DynamicComponentConnection
from hisim.energy_system.assemblies.selection import Observer, SelectionPlan
from hisim.energy_system.assemblies.twins import COMPOSED_TWINS, rename_reference
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import EnergySystemError, EnergySystemRecordError
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.imports_model import Selection
from hisim.energy_system.model import AggregatorFeed, DispatchSpec, EnergySystemFile
from tests.assemblies.helpers import MOCKS, OCCUPANCY, WEATHER, Real, build_text, expand_text, imports, site

#: The weight of a feed an observer only measures.
MEASURED = FeedRequest.MONITORED_ONLY_WEIGHT


class HeatPumpHouse:
    """The heat-pump composed file and the variants of it these tests build, as text.

    Example: ``HeatPumpHouse.without_energy_manager()`` is the composed file of the twin's
    ``metered_directly`` option: the file's header says how, dropping ``control``, ``battery``, the
    grid's ``observes:`` line and the ``optional-bind:`` lines naming ``control``.
    """

    #: The twin's grouped file, whose option ``metered_directly`` writes the meter's feeds by hand.
    GROUPED: ClassVar[Path] = Real.ENERGY_SYSTEMS / "household_heatpump_building_sizer.grouped.energy_system.yaml"

    @classmethod
    def document(cls) -> Dict[str, Any]:
        """The composed file as a document, to edit."""
        document: Dict[str, Any] = yaml.safe_load(Real.HEATPUMP_HOUSE.read_text(encoding="utf-8"))
        return document

    @classmethod
    def text(cls, document: Dict[str, Any]) -> str:
        """An edited document as the text of an energy-system file."""
        return str(yaml.safe_dump(document, sort_keys=False))

    @classmethod
    def without_energy_manager(cls) -> str:
        """The composed file without its energy manager and battery: the twin's ``metered_directly``."""
        document = cls.document()
        for key in ("control", "battery"):
            del document["imports"][key]
        del document["imports"]["grid"]["observes"]
        for entry in list(document["components"].values()) + list(document["imports"].values()):
            if "control" in entry.get("optional-bind", {}).values():
                del entry["optional-bind"]
        return cls.text(document)

    @classmethod
    def space_heating_only(cls) -> str:
        """The composed file with the heat pump that has no DHW side, and without the cylinder it would charge."""
        document = cls.document()
        heating = document["imports"]["heating"]
        heating["assembly"] = "heating/air_source_heat_pump_space_heating_only"
        heating["optional-bind"] = {"ems_modifier": "control"}
        del document["imports"]["dhw"]
        return cls.text(document)

    @classmethod
    def manager_observing(cls, selection: List[Dict[str, Any]]) -> str:
        """The composed file whose energy manager observes the given selection instead of what its class declares."""
        document = cls.document()
        document["imports"]["control"]["observes"] = selection
        return cls.text(document)

    @classmethod
    def metered_directly_feeds(cls) -> List[Tuple[Any, ...]]:
        """The meter's feeds the twin's ``metered_directly`` option writes by hand, sorted."""
        grouped = yaml.safe_load(cls.GROUPED.read_text(encoding="utf-8"))
        option = grouped["variants"]["electricity_management"]["options"]["metered_directly"]
        return sorted(
            (item["from"], item.get("component_type"), tuple(item["tags"]), item["weight"])
            for item in option["components"]["ElectricityMeter"]["inputs"]
        )


def light_house(*lines: str) -> str:
    """A house of the real weather station and residents with the given imports; it builds without a building."""
    return site(Real.WEATHER, Real.OCCUPANCY) + imports(*lines)


def build(text: str, directory: Path) -> EnergySystemFile:
    """Builds an inline file: the file with the selected feeds written in."""
    return build_text(text, directory).model


def refusal(text: str, directory: Path) -> str:
    """The message a build refuses an inline file with."""
    with pytest.raises(EnergySystemError) as raised:
        build_text(text, directory)
    return str(raised.value)


def feeds(model: EnergySystemFile, component: str) -> List[AggregatorFeed]:
    """The aggregator feeds of one built component, in written order."""
    return [item for item in model.components[component].inputs if isinstance(item, AggregatorFeed)]


# ---------------------------------------------------------------------------------- the twin shapes


@pytest.mark.assemblies
def test_the_metered_directly_shape_writes_exactly_the_twins_feeds(tmp_path: Path) -> None:
    """No manager: the meter's default selection is, item by item, the twin's hand-written feeds (§4.2 gate)."""
    built = build_text(HeatPumpHouse.without_energy_manager(), tmp_path)
    rename = COMPOSED_TWINS["household_heatpump_building_sizer"].rename
    selected = sorted(
        (rename_reference(f"{feed.source}.{feed.output}", rename), feed.component_type, feed.tags, feed.weight)
        for feed in feeds(built.model, "grid-ElectricityMeter")
    )
    assert selected == HeatPumpHouse.metered_directly_feeds()
    assert all(feed.dispatch is None for feed in feeds(built.model, "grid-ElectricityMeter"))
    heating = {port.port: port for port in built.imports.instance("heating").ports}
    assert heating["ems_modifier"].decision == "not bound: no candidate" and not heating["ems_modifier"].lowered_to


@pytest.mark.assemblies
def test_one_device_each_gives_the_class_defaults_and_a_second_battery_follows_the_first(tmp_path: Path) -> None:
    """The weights are the controller class's own (DEFAULT_WEIGHTS); the k-th further battery gets default + k."""
    batteries = "battery: {assembly: storage/battery, instances: {garage: {}, cellar: {}}}"
    model = build(light_house(Real.PV, batteries, Real.CONTROL, Real.GRID_ON_BALANCE), tmp_path)
    ranked = [(feed.source, feed.weight) for feed in feeds(model, "control-EMS") if feed.weight != MEASURED]
    defaults = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS
    assert ranked == [
        ("UTSPConnector", defaults[lt.ComponentType.RESIDENTS]),
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
        source_component_class=Battery,
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
    """EF-7V (D27): a second space heater at 2 + 1 = 3 would tie with the hot-water heater's base weight 3.

    Mock site entries: three electric heaters of two types beside one manager are a shape the real library
    shows only with two heating sites.
    """
    heaters = [
        f"{name}: {{class: {MOCKS}.{cls}, preset: standard}}"
        for name, cls in (("Floor", "MockHeater"), ("Attic", "MockHeater"), ("Water", "MockWaterHeater"))
    ]
    control = f"Control: {{class: {MOCKS}.MockEnergyManager, preset: optimize_own_consumption, observes: declared}}"
    message = refusal(site(WEATHER, OCCUPANCY, *heaters, control), tmp_path)
    assert message.startswith("EF-7V at components.Control")
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
        "grid: {assembly: supply/electricity_grid, observes: [{flow: ELECTRICITY_CONSUMPTION_UNCONTROLLED}, "
        "{component_type: PV}]}"
    )
    model = build(light_house(Real.PV, grid), tmp_path)
    assert [feed.source for feed in feeds(model, "grid-ElectricityMeter")] == ["UTSPConnector", "pv-PVSystem"]


@pytest.mark.assemblies
def test_a_declared_output_the_built_source_does_not_have_is_no_candidate(tmp_path: Path) -> None:
    """Catches the manager selecting an output its source was built without (EF-21) instead of binding the rest.

    The heat pump without a DHW side is built without ``ElectricalInputPowerDHW``, a feed the manager's class
    declares from every heat pump.
    """
    model = build(HeatPumpHouse.space_heating_only(), tmp_path)
    assert [(feed.source, feed.output) for feed in feeds(model, "control-EMS")] == [
        ("UTSPConnector", "ElectricalPowerConsumption"),
        ("pv-pv_system-PVSystem", "ElectricityOutput"),
        ("heating-HeatPump", "ElectricalInputPowerSH"),
        ("battery-battery-Battery", "AcBatteryPowerUsed"),
    ]


@pytest.mark.assemblies
def test_a_site_entry_observes_with_its_own_selection(tmp_path: Path) -> None:
    """``observes:`` on a site entry: its selected feeds follow its own inputs."""
    meter = f"Meter: {{class: {Real.METER_CLASS}, preset: standard, observes: [{{component_type: PV}}]}}"
    model = build(site(Real.WEATHER, Real.OCCUPANCY, meter) + imports(Real.PV), tmp_path)
    assert [feed.source for feed in feeds(model, "Meter")] == ["pv-PVSystem"]


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("observes", "code", "fragment"),
    [
        (
            "[{component_type: BATTERY}]",
            "EF-7S",
            "the selector {component_type: BATTERY} of 'grid-ElectricityMeter' matches no",
        ),
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
    grid = f"grid: {{assembly: supply/electricity_grid, observes: {observes}}}"
    message = refusal(light_house(Real.PV, grid), tmp_path)
    assert message.startswith(code) and fragment in message


@pytest.mark.assemblies
def test_an_observer_whose_class_declares_no_feeds_and_an_import_without_an_observer_port_are_refused(
    tmp_path: Path,
) -> None:
    """Catches an ``observes:`` that nothing can honour."""
    residents = Real.OCCUPANCY[:-1] + ", observes: declared}"
    message = refusal(site(Real.WEATHER, residents), tmp_path / "a")
    assert message.startswith("EF-7S at components.UTSPConnector")
    assert "declares no dynamic default connections" in message
    array = "pv: {assembly: pv/array, parameters: {power_in_watt: 5000}, observes: declared}"
    message = refusal(light_house(array), tmp_path / "b")
    assert message.startswith("EF-7J at import 'pv'") and "'pv/array' has 0" in message


@pytest.mark.assemblies
def test_an_output_selected_and_fed_explicitly_is_refused_as_a_duplicate_feed(tmp_path: Path) -> None:
    """Coexistence (§4.2): written feeds stay legal, the same output selected again is EF-25."""
    written = (
        "{from: UTSPConnector.ElectricalPowerConsumption, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}"
    )
    meter = f"Meter: {{class: {Real.METER_CLASS}, preset: standard, inputs: [{written}], observes: declared}}"
    message = refusal(site(Real.WEATHER, Real.OCCUPANCY, meter), tmp_path)
    assert message.startswith("EF-25") and "UTSPConnector" in message


# ---------------------------------------------------------------------------------- the double count


@pytest.mark.assemblies
def test_the_grid_left_at_its_default_beside_a_controller_counts_the_flows_twice(tmp_path: Path) -> None:
    """EF-7T: the meter reads the manager's balance and the flows the manager observes."""
    message = refusal(light_house(Real.PV, Real.CONTROL, "grid: {assembly: supply/electricity_grid}"), tmp_path)
    assert message.startswith("EF-7T at components.grid-ElectricityMeter.inputs")
    assert (
        "'grid-ElectricityMeter' reads an output of 'control-EMS' and both observe "
        "UTSPConnector.ElectricalPowerConsumption" in message
    )
    assert "[source: grid-ElectricityMeter (import grid" in message


@pytest.mark.assemblies
@pytest.mark.parametrize(
    "residents", ["UTSPConnector.ElectricalPowerConsumption", "UTSPConnector"], ids=["output", "no output"]
)
def test_the_double_count_check_covers_hand_written_files(tmp_path: Path, residents: str) -> None:
    """A flat file whose meter reads the manager's balance and a flow the manager reads: EF-7T.

    Catches a written feed that leaves its output to the meter's declaration passing as another flow.
    """
    text = f"""\
schema_version: 3
name: written
components:
  {Real.WEATHER}
  {Real.OCCUPANCY}
  Ems:
    class: {Real.ENERGY_MANAGER_CLASS}
    preset: optimize_own_consumption
    inputs:
      - from: UTSPConnector.ElectricalPowerConsumption
        component_type: RESIDENTS
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 1
        dispatch: {{}}
  Meter:
    class: {Real.METER_CLASS}
    preset: standard
    inputs:
      - {{from: Ems.TotalElectricityToOrFromGrid, tags: [ELECTRICITY_PRODUCTION], weight: 999}}
      - {{from: {residents}, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}}
"""
    message = refusal(text, tmp_path)
    assert message.startswith("EF-7T at components.Meter.inputs")
    assert "'Meter' reads an output of 'Ems' and both observe UTSPConnector.ElectricalPowerConsumption" in message


# --------------------------------------------------------------------------------------- actuation


@pytest.mark.assemblies
@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (light_house(Real.PV, "battery: {assembly: storage/battery}"), "is ranked by no controller"),
        (
            light_house(
                Real.PV,
                "battery: {assembly: storage/battery}",
                Real.CONTROL,
                "second: {assembly: control/ems_self_consumption}",
                Real.GRID_ON_BALANCE,
            ),
            "is ranked by control-EMS, second-EMS",
        ),
        (
            HeatPumpHouse.manager_observing([{"component_type": ["RESIDENTS", "PV", "BATTERY"]}]),
            "but its need is bound to control-EMS",
        ),
    ],
    ids=["no controller", "two controllers", "via bound, not ranked"],
)
def test_a_controllable_output_is_actuated_by_exactly_the_one_controller_it_binds(
    tmp_path: Path, text: str, fragment: str
) -> None:
    """EF-7U: a battery with no controller or two, a heat pump bound to a controller that does not rank it."""
    message = refusal(text, tmp_path)
    assert message.startswith("EF-7U") and fragment in message


# ------------------------------------------------------------------------------- derived port names


@pytest.mark.assemblies
def test_derived_port_names_replace_the_address_separator_and_a_collision_is_refused(tmp_path: Path) -> None:
    """``-`` becomes ``_`` in a derived port; a site entry named like the result collides (EF-32)."""
    assert NameSyntax.port_name_part("pv-east-PVSystem") == "pv_east_PVSystem"
    house = light_house(Real.PV, "battery: {assembly: storage/battery}", Real.CONTROL, Real.GRID_ON_BALANCE)
    ems = build_text(house, tmp_path / "a").wired.component_of("control-EMS")
    assert "DispatchTobattery_Battery_LoadingPowerInput" in [output.field_name for output in ems.outputs]
    roof = (
        "pv_PVSystem: {class: hisim.components.generic_pv_system.PVSystem, preset: rooftop, "
        "config: {power_in_watt: 3000}, inputs: [Weather]}"
    )
    grid = "grid: {assembly: supply/electricity_grid, observes: [{component_type: PV}]}"
    message = refusal(site(Real.WEATHER, Real.OCCUPANCY, roof) + imports(Real.PV, grid), tmp_path / "b")
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
    _, record = expand_text(Real.HEATPUMP_HOUSE.read_text(encoding="utf-8"))
    with pytest.raises(EnergySystemRecordError, match="EF-60") as raised:
        record.to_document()
    message = str(raised.value)
    for name in ("control-EMS", "before the wiring selected its feeds"):
        assert name in message, f"{name!r} is not in: {message}"


@pytest.mark.assemblies
def test_the_heat_pump_house_runs_a_day_with_the_balance_closed_and_its_record_reruns_selecting_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The record writes the selected feeds as ordinary feeds and the observers' feeds; a re-run reproduces it."""
    first = tmp_path / "first"
    arguments = [str(Real.HEATPUMP_HOUSE), str(Real.PARAMETERS), "--result-dir", str(first)]
    assert main(["energy-system", "run", *arguments]) == ExitCodes.OK
    balance = json.loads((first / "balance_report.json").read_text(encoding="utf-8"))
    assert balance["verdict"] == "closes"
    record = yaml.safe_load((first / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    written = record["components"]["control-EMS"]["inputs"]
    weights = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS
    assert [(item["from"], item["weight"]) for item in written] == [
        ("UTSPConnector.ElectricalPowerConsumption", weights[lt.ComponentType.RESIDENTS]),
        ("pv-pv_system-PVSystem.ElectricityOutput", MEASURED),
        ("heating-HeatPump.ElectricalInputPowerSH", weights[lt.ComponentType.HEAT_PUMP_BUILDING]),
        ("heating-HeatPump.ElectricalInputPowerDHW", weights[lt.ComponentType.HEAT_PUMP_DHW]),
        ("battery-battery-Battery.AcBatteryPowerUsed", weights[lt.ComponentType.BATTERY]),
    ]
    battery = written[4]
    assert (battery["component_type"], battery["tags"], battery["dispatch"]["target_input"]) == (
        "BATTERY",
        ["ELECTRICITY_CONSUMPTION_EMS_CONTROLLED"],
        "LoadingPowerInput",
    )
    observers = record["metadata"]["imports"]["observers"]
    assert [(item["observer"], item["selection"], len(item["feeds"])) for item in observers] == [
        ("control-EMS", "declared", len(written)),
        ("grid-ElectricityMeter", "[{output: TotalElectricityToOrFromGrid}]", 1),
    ]

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
