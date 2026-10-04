"""Observe and actuate: selectors, grid balance, double count, controller priorities (``assemblies_spec.md`` §4).

HiSim has no bus: a meter and an energy manager are dynamic components whose inputs are added as
their selectors match. These tests run the selection pass of the expansion on the mock library —
``mock/electricity_grid`` (a meter observing), ``mock/ems_self_consumption`` (an energy manager
observing and ranking by its priorities), ``mock/smart_heater`` (an output controllable through
its L1's ``ems_modifier``) and ``mock/home_battery`` (an output actuated directly through
``LoadingPowerInput``) — and check every refusal by its named error, the order of the feeds, the
weights §4.4 derives, the derived port names (hisim-lt0b.11), the two twin shapes item by item, and
one day of the composed house with the energy balance on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pytest
from dataclasses_json import dataclass_json

from hisim.cli import main
from hisim.component_interface import ClassInterface, DeclaredFeed, DeclaredPort
from hisim import loadtypes as lt
from hisim.config import ComponentID, ConfigBase, preset
from hisim.config.channels import ResolvedDynamicConnection
from hisim.config.names import NameSyntax
from hisim.energy_system.assemblies.expansion import expand_imports
from hisim.energy_system.assemblies.record import ImportRecord
from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.model import AggregatorFeed, DispatchSpec, EnergySystemFile
from hisim.simulationparameters import SimulationParameters
from tests.assemblies.mock_components import MockAggregator
from tests.assemblies.helpers import OCCUPANCY, WEATHER, Library, Mocks, expand_text, mock_resolver, site

MOCKS = Mocks.MOCKS

#: The grid import whose meter observes the energy manager's balance alone (twin ems_with_battery).
GRID_ON_BALANCE = "  grid: {assembly: mock/electricity_grid, observes: [{output: TotalElectricityToOrFromGrid}]}\n"


def imports(*lines: str) -> str:
    """The ``imports:`` block of the given lines."""
    return "imports:\n" + "".join(lines)


def expand(text: str, resolver: Optional[AssemblyResolver] = None) -> Tuple[EnergySystemFile, ImportRecord]:
    """Expands an inline file against the mock library (or the given one)."""
    return expand_text(text, resolver or mock_resolver())


def refusal(text: str, resolver: Optional[AssemblyResolver] = None) -> str:
    """The message the expansion refuses an inline file with."""
    with pytest.raises(EnergySystemError) as raised:
        expand(text, resolver)
    return str(raised.value)


def feeds(model: EnergySystemFile, component: str) -> List[AggregatorFeed]:
    """The aggregator feeds of one expanded component, in written order."""
    return [item for item in model.components[component].inputs if isinstance(item, AggregatorFeed)]


def ranked(model: EnergySystemFile, component: str) -> List[Tuple[str, int, Optional[DispatchSpec]]]:
    """``(source.output, weight, dispatch)`` of every feed of a component."""
    return [(f"{feed.source}.{feed.output}", feed.weight, feed.dispatch) for feed in feeds(model, component)]


# ------------------------------------------------------------------------------------- a test class


@dataclass_json
@dataclass
class RankingProbeConfig(ConfigBase):
    """A controller whose class ranks two flows high and without a component type."""

    MAIN_CLASS = "tests.assemblies.test_assembly_selectors.RankingProbe"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "RankingProbeConfig":
        """The probe."""
        return cls(component_id=ComponentID(name=name))


class RankingProbe(MockAggregator):
    """Declares the heater's and the residents' electricity ranked at 998, without component types."""

    CHANNELS = ()
    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort("Balance", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),),
        default_feeds=(
            DeclaredFeed("MockHeater", "ElectricityInput", ("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",), 998),
            DeclaredFeed("MockOccupancy", "ElectricityConsumption", ("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",), 998),
        ),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: RankingProbeConfig) -> None:
        """Builds the probe."""
        super().__init__(my_simulation_parameters, config)


PROBE_CONTROLLER = """
schema_version: 4
kind: assembly
name: control/probe
description: A probe controller.
parameters:
  priorities: {type: list, default: [{flow: ELECTRICITY_CONSUMPTION_EMS_CONTROLLED}], description: Priorities.}
presets: {standard: {}}
components:
  Probe:
    class: tests.assemblies.test_assembly_selectors.RankingProbe
    preset: standard
    inputs: [{$observes: flows}]
interface:
  observes:
    flows: {into: [Probe], default: declared}
  actuates:
    priorities: {$param: priorities}
tests: {bounds: [], monotone: []}
"""


# ---------------------------------------------------------------------------------------- selection


@pytest.mark.base
def test_the_default_selection_observes_every_declared_flow_in_written_order() -> None:
    """``default: declared``: every output the meter's class declares a feed from, with its tags and weight."""
    expanded, record = expand(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array, instances: {east: {}, west: {azimuth_in_degree: 270}}}\n",
            "  heater: {assembly: mock/smart_heater}\n",
            "  grid: {assembly: mock/electricity_grid}\n",
        )
    )

    assert feeds(expanded, "grid-Meter") == [
        AggregatorFeed(
            source="Occupancy",
            output="ElectricityConsumption",
            tags=("ELECTRICITY_CONSUMPTION_UNCONTROLLED",),
            weight=999,
        ),
        AggregatorFeed(
            source="pv-east-PVSystem",
            output="ElectricityOutput",
            component_type="PV",
            tags=("ELECTRICITY_PRODUCTION",),
            weight=999,
        ),
        AggregatorFeed(
            source="pv-west-PVSystem",
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
    observer = record.observer("grid-Meter")
    assert observer is not None and observer.selection == "declared"
    assert [feed.selected_by for feed in observer.feeds] == ["declared"] * 4


@pytest.mark.base
def test_the_feeds_follow_the_written_order_of_the_imports_never_their_names() -> None:
    """Shuffling the imports reorders the feeds the same way: written order, not sorted by name (§4.2)."""
    lines = {
        "zeta": "  zeta: {assembly: mock/smart_heater}\n",
        "alpha": "  alpha: {assembly: mock/pv_array}\n",
        "grid": "  grid: {assembly: mock/electricity_grid}\n",
    }
    sources = []
    for order in (("zeta", "alpha", "grid"), ("alpha", "zeta", "grid"), ("grid", "zeta", "alpha")):
        expanded, _record = expand(site(WEATHER, OCCUPANCY) + imports(*(lines[key] for key in order)))
        sources.append([feed.source for feed in feeds(expanded, "grid-Meter")])

    assert sources[0] == ["Occupancy", "zeta-Heater", "alpha-PVSystem"]
    assert sources[1] == ["Occupancy", "alpha-PVSystem", "zeta-Heater"]
    assert sources[2] == ["Occupancy", "zeta-Heater", "alpha-PVSystem"]


@pytest.mark.base
@pytest.mark.parametrize(
    "selection, expected",
    [
        ("[{component_type: PV}]", ["pv-PVSystem.ElectricityOutput"]),
        (
            "[{flow: ELECTRICITY_CONSUMPTION_UNCONTROLLED}]",
            ["Occupancy.ElectricityConsumption", "heater-Heater.ElectricityInput"],
        ),
        ("[{output: ElectricityInput}]", ["heater-Heater.ElectricityInput"]),
        (
            "[{component_type: ELECTRIC_HEATING_SH, flow: ELECTRICITY_PRODUCTION}, {output: ElectricityOutput}]",
            ["pv-PVSystem.ElectricityOutput"],
        ),
        (
            "[{output: ElectricityOutput}, {component_type: [PV, ELECTRIC_HEATING_SH]}]",
            ["pv-PVSystem.ElectricityOutput", "heater-Heater.ElectricityInput"],
        ),
    ],
)
def test_a_selection_is_the_union_of_its_selectors_matches(selection: str, expected: List[str]) -> None:
    """``component_type``, ``flow`` and ``output`` match; the keys of one selector hold together; a list unites."""
    expanded, _record = expand(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array}\n",
            "  heater: {assembly: mock/smart_heater}\n",
            f"  grid: {{assembly: mock/electricity_grid, observes: {selection}}}\n",
        )
    )

    assert [f"{feed.source}.{feed.output}" for feed in feeds(expanded, "grid-Meter")] == expected


@pytest.mark.base
def test_a_selectors_feed_block_overrides_the_declared_tags_and_weight() -> None:
    """``feed:`` replaces the declaration's tags (and, on a non-controller, keeps 999)."""
    expanded, record = expand(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array}\n",
            "  grid: {assembly: mock/electricity_grid, observes: [{component_type: PV, feed: {component_type: "
            "BATTERY, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]}}]}\n",
        )
    )

    assert feeds(expanded, "grid-Meter") == [
        AggregatorFeed(
            source="pv-PVSystem",
            output="ElectricityOutput",
            component_type="BATTERY",
            tags=("ELECTRICITY_CONSUMPTION_UNCONTROLLED",),
            weight=999,
        )
    ]
    observer = record.observer("grid-Meter")
    assert observer is not None and observer.feeds[0].selected_by.startswith("{component_type: PV, feed:")


@pytest.mark.base
def test_a_required_selector_matching_nothing_is_refused_with_the_candidates() -> None:
    """``required: true`` and no match: EF-7S, naming the selector and every candidate."""
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array}\n",
            "  grid: {assembly: mock/electricity_grid, observes: [{component_type: PV}, "
            "{component_type: BATTERY, required: true}]}\n",
        )
    )

    assert message.startswith(
        "EF-7S at import grid: the required selector {component_type: BATTERY, required: true} of grid-Meter "
        "(import grid, port reading) matches nothing"
    )
    assert "Candidates: Occupancy.ElectricityConsumption, pv-PVSystem.ElectricityOutput (" in message


@pytest.mark.base
def test_an_output_the_class_declares_no_feed_from_is_no_match() -> None:
    """The tank's heat loss carries energy, but the meter declares nothing from it: required fails naming it."""
    tank = f"Tank:\n  class: {MOCKS}.MockTank\n  preset: standard\n  inputs: [Occupancy]\n"
    message = refusal(
        site(WEATHER, OCCUPANCY, tank)
        + imports("  grid: {assembly: mock/electricity_grid, observes: [{output: HeatLoss, required: true}]}\n")
    )

    assert "EF-7S" in message and "the required selector {output: HeatLoss, required: true}" in message
    assert "Candidates: Occupancy.ElectricityConsumption (" in message


@pytest.mark.base
def test_an_observer_never_matches_its_own_outputs() -> None:
    """A logger declares a feed from its own class: alone it has no candidate and is refused as idle."""
    logger = (
        f"Logger:\n  class: {MOCKS}.MockLogger\n  preset: standard\n  observes: declared\n"
        "  inputs: [{$observes: observes}]\n"
    )
    other = f"Other:\n  class: {MOCKS}.MockLogger\n  preset: standard\n"

    message = refusal(site(WEATHER, logger))
    assert message.startswith(
        "EF-7S at component Logger: Logger (component Logger, port observes) is idle: its selection declared matches "
        "no output of the system (candidates: none)"
    )
    expanded, _record = expand(site(WEATHER, logger, other))
    assert [f"{feed.source}.{feed.output}" for feed in feeds(expanded, "Logger")] == ["Other.Reading"]


@pytest.mark.base
def test_a_class_that_declares_no_feeds_cannot_observe() -> None:
    """EF-7H: the tank declares no dynamic default connections, so it has nothing to select from."""
    tank = (
        f"Tank:\n  class: {MOCKS}.MockTank\n  preset: standard\n  observes: declared\n"
        "  inputs: [{$observes: observes}]\n"
    )

    message = refusal(site(WEATHER, OCCUPANCY, tank))

    assert message.startswith("EF-7H at component Tank: Tank (component Tank, port observes) observes, but")
    assert "declares no dynamic default connections (default_feeds)" in message


@pytest.mark.base
def test_a_site_entry_observes_at_its_placeholder() -> None:
    """A site entry's ``observes:`` lands at its ``{$observes: observes}``; without one, or without the key: refused."""
    meter = f"Meter:\n  class: {MOCKS}.MockElectricityMeter\n  preset: standard\n"
    expanded, _record = expand(
        site(
            WEATHER,
            OCCUPANCY,
            meter + "  observes: [{flow: ELECTRICITY_CONSUMPTION_UNCONTROLLED}]\n"
            "  inputs: [{$observes: observes}]\n",
        )
    )
    assert [feed.source for feed in feeds(expanded, "Meter")] == ["Occupancy"]

    assert "EF-7S at components.Meter: 'Meter' observes, so its feeds land where it writes exactly one" in refusal(
        site(WEATHER, OCCUPANCY, meter + "  observes: declared\n")
    )
    assert "carries an '{$observes: …}' placeholder but writes no 'observes:' selection" in refusal(
        site(WEATHER, OCCUPANCY, meter + "  inputs: [{$observes: observes}]\n")
    )


@pytest.mark.base
def test_an_output_selected_and_fed_explicitly_is_refused_as_a_duplicate_feed() -> None:
    """EF-25, as today's DUPLICATE_FEED: one output read twice by one observer."""
    meter = (
        f"Meter:\n  class: {MOCKS}.MockElectricityMeter\n  preset: standard\n  observes: declared\n"
        "  inputs:\n    - {$observes: observes}\n"
        "    - {from: Occupancy.ElectricityConsumption, tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED], weight: 999}\n"
    )

    message = refusal(site(WEATHER, OCCUPANCY, meter))

    assert message.startswith(
        "EF-25 at components.Meter.inputs: 'Meter' observes 'Occupancy.ElectricityConsumption' twice"
    )
    assert "written in its inputs" in message and "selected by component Meter.observes (declared)" in message


@pytest.mark.base
def test_an_import_observes_needs_an_observer_port_and_writes_no_actuates() -> None:
    """``observes:`` on an assembly without an observer port is refused (EF-7S); ``actuates:`` on any import (EF-7U)."""
    assert "EF-7S" in refusal(site(WEATHER) + imports("  pv: {assembly: mock/pv_array, observes: [{output: X}]}\n"))
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports("  control: {assembly: mock/ems_self_consumption, actuates: [{output: X}]}\n")
    )
    assert message.startswith("EF-7U at imports.control: the import 'control' writes 'actuates:'")


@pytest.mark.base
def test_no_verb_binds_an_observer_port() -> None:
    """An observer selects by tags; a verb on it is refused."""
    message = refusal(
        site(WEATHER, OCCUPANCY) + imports("  grid: {assembly: mock/electricity_grid, bind: {reading: Occupancy}}\n")
    )

    assert (
        "EF-7S at import grid: port 'reading' is an observer port, which selects by tags and is bound by no verb"
        in message
    )


@pytest.mark.base
def test_an_observer_that_is_no_controller_ranks_nothing() -> None:
    """A site energy manager has no priorities, so a ranked feed on it is refused (EF-7S)."""
    ems = (
        f"EMS:\n  class: {MOCKS}.MockEnergyManager\n  preset: optimize_own_consumption\n  observes: declared\n"
        "  inputs: [{$observes: observes}]\n"
    )

    message = refusal(site(WEATHER, OCCUPANCY, ems))

    assert (
        "EF-7S at component EMS: EMS (component EMS, port observes) would rank Occupancy.ElectricityConsumption at "
        "weight 1, but it is no controller" in message
    )


# ------------------------------------------------------------------------------- grid and double count


@pytest.mark.base
def test_the_meter_observing_the_balance_and_a_flow_the_ems_observes_is_a_double_count() -> None:
    """The grid left at its default beside a controller: EF-7T (§3.3, §4.3), with the paste-ready selection."""
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array}\n",
            "  control: {assembly: mock/ems_self_consumption}\n",
            "  grid: {assembly: mock/electricity_grid}\n",
        )
    )

    assert message.startswith(
        "EF-7T at components.grid-Meter.inputs: 'grid-Meter' observes control-EMS.TotalElectricityToOrFromGrid, the "
        "balance control-EMS sums over what it observes, and also Occupancy.ElectricityConsumption, "
        "pv-PVSystem.ElectricityOutput, which control-EMS observes"
    )
    assert "observes: [{output: TotalElectricityToOrFromGrid}]" in message


@pytest.mark.base
def test_the_grid_selects_the_ems_balance_by_name() -> None:
    """The meter's one feed is the manager's balance, on its production channel at 999 (twin :106-109)."""
    expanded, _record = expand(
        site(WEATHER, OCCUPANCY) + imports("  control: {assembly: mock/ems_self_consumption}\n", GRID_ON_BALANCE)
    )

    assert feeds(expanded, "grid-Meter") == [
        AggregatorFeed(
            source="control-EMS", output="TotalElectricityToOrFromGrid", tags=("ELECTRICITY_PRODUCTION",), weight=999
        )
    ]


@pytest.mark.base
def test_the_real_meter_declares_its_feed_from_the_real_ems() -> None:
    """Dry run G6: ElectricityMeter's dynamic default connection from L2GenericEnergyManagementSystem."""
    from hisim.components.controller_l2_energy_management_system import (  # pylint: disable=import-outside-toplevel
        L2GenericEnergyManagementSystem,
    )
    from hisim.components.electricity_meter import ElectricityMeter  # pylint: disable=import-outside-toplevel

    meter = ElectricityMeter.__new__(ElectricityMeter)
    connections = meter.get_default_connections_from_energy_management_system()
    assert len(connections) == 1
    connection = connections[0]

    assert connection.source_class_name == L2GenericEnergyManagementSystem.get_classname()
    assert connection.source_component_field_name == "TotalElectricityToOrFromGrid"
    assert connection.source_tags == [lt.InandOutputType.ELECTRICITY_PRODUCTION]
    assert connection.source_weight == 999


# ---------------------------------------------------------------------------- priorities and weights

SYSTEM_WITH_CONTROL = site(WEATHER, OCCUPANCY)


def controlled(*extra: str, control: str = "  control: {assembly: mock/ems_self_consumption}\n") -> str:
    """A house with PV, the smart heater bound to the controller, the given extra imports and the balance grid."""
    return SYSTEM_WITH_CONTROL + imports(
        "  pv: {assembly: mock/pv_array}\n",
        "  heater: {assembly: mock/smart_heater, optional-bind: {ems_modifier: control}}\n",
        *extra,
        control,
        GRID_ON_BALANCE,
    )


@pytest.mark.base
def test_a_default_list_with_one_device_each_gives_the_class_defaults() -> None:
    """Residents 1, space heating 2, battery 6: the controller class's own weights (§4.4)."""
    expanded, record = expand(controlled("  battery: {assembly: mock/home_battery}\n"))

    assert ranked(expanded, "control-EMS") == [
        ("Occupancy.ElectricityConsumption", 1, DispatchSpec()),
        ("pv-PVSystem.ElectricityOutput", 999, None),
        ("heater-Heater.ElectricityInput", 2, DispatchSpec()),
        ("battery-Battery.AcBatteryPowerUsed", 6, DispatchSpec(target_input="LoadingPowerInput")),
    ]
    observer = record.observer("control-EMS")
    assert observer is not None
    assert [feed.control for feed in observer.feeds] == [
        "rank-only",
        "measured",
        "via ems_modifier",
        "target_input LoadingPowerInput",
    ]
    assert [entry.ranked for entry in observer.priorities] == [
        ("Occupancy.ElectricityConsumption (RESIDENTS): class default 1 -> weight 1",),
        ("heater-Heater.ElectricityInput (ELECTRIC_HEATING_SH): class default 2 -> weight 2",),
        (),
        (),
        ("battery-Battery.AcBatteryPowerUsed (BATTERY): class default 6 -> weight 6",),
    ]
    assert [actuation.text() for actuation in record.actuations] == [
        "control-EMS actuates heater.ems_modifier (heater-Controller.inputs: control-EMS) for "
        "heater-Heater.ElectricityInput at weight 2 (via)",
        "control-EMS actuates battery-Battery.LoadingPowerInput for battery-Battery.AcBatteryPowerUsed at weight 6 "
        "(target_input)",
    ]


@pytest.mark.base
def test_the_class_defaults_are_the_real_controllers_weights() -> None:
    """The mock manager declares its weights from ``L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS``."""
    from hisim.components.controller_l2_energy_management_system import (  # pylint: disable=import-outside-toplevel
        L2GenericEnergyManagementSystem,
    )

    weights = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS
    assert (
        weights[lt.ComponentType.RESIDENTS],
        weights[lt.ComponentType.HEAT_PUMP_BUILDING],
        weights[lt.ComponentType.HEAT_PUMP_DHW],
        weights[lt.ComponentType.SOLAR_THERMAL_SYSTEM],
        weights[lt.ComponentType.BATTERY],
    ) == (1, 2, 3, 4, 6)


@pytest.mark.base
def test_a_second_battery_gets_the_next_weight() -> None:
    """The k-th further instance of a type gets ``default + k``: 6, then 7."""
    expanded, _record = expand(
        controlled("  battery: {assembly: mock/home_battery}\n", "  spare: {assembly: mock/home_battery}\n")
    )

    assert [(source, weight) for source, weight, _dispatch in ranked(expanded, "control-EMS")][2:] == [
        ("heater-Heater.ElectricityInput", 2),
        ("battery-Battery.AcBatteryPowerUsed", 6),
        ("spare-Battery.AcBatteryPowerUsed", 7),
    ]


@pytest.mark.base
def test_a_reordered_list_gets_weights_in_list_order() -> None:
    """The battery before space heating: an entry not above every earlier one is raised to the next free weight."""
    expanded, record = expand(
        controlled(
            "  battery: {assembly: mock/home_battery}\n",
            control="  control: {assembly: mock/ems_self_consumption, preset: battery_first}\n",
        )
    )

    weights = {source: weight for source, weight, _dispatch in ranked(expanded, "control-EMS")}
    assert (
        weights["Occupancy.ElectricityConsumption"],
        weights["battery-Battery.AcBatteryPowerUsed"],
        weights["heater-Heater.ElectricityInput"],
    ) == (1, 6, 7)
    observer = record.observer("control-EMS")
    assert observer is not None
    assert observer.priorities[2].ranked == (
        "heater-Heater.ElectricityInput (ELECTRIC_HEATING_SH): class default 2 -> weight 7",
    )


@pytest.mark.base
def test_a_ranked_output_no_priority_selects_is_refused() -> None:
    """EF-7V: the battery is observed and ranked by the class, but the priorities leave it out."""
    message = refusal(
        controlled(
            "  battery: {assembly: mock/home_battery}\n",
            control="  control: {assembly: mock/ems_self_consumption, parameters: {priorities: "
            "[{component_type: RESIDENTS}, {component_type: ELECTRIC_HEATING_SH}]}}\n",
        )
    )

    assert message.startswith(
        "EF-7V at import control: control-EMS (import control, port flows) observes "
        "battery-Battery.AcBatteryPowerUsed, "
        "which its class ranks, but no entry of its priorities selects it"
    )


@pytest.mark.base
@pytest.mark.parametrize(
    "priorities, fragment",
    [
        ("[{component_type: PV}]", "selects pv-PVSystem.ElectricityOutput, which MockEnergyManager only measures"),
        (
            "[{component_type: RESIDENTS}, {flow: ELECTRICITY_CONSUMPTION_EMS_CONTROLLED}]",
            "selects Occupancy.ElectricityConsumption, which an earlier entry already ranks at 1",
        ),
        (
            "[{component_type: RESIDENTS, feed: {weight: 3}}, {component_type: ELECTRIC_HEATING_SH}]",
            "the priority {component_type: RESIDENTS, feed: {weight: 3}} of control-EMS (import control, port flows) "
            "writes a feed: block",
        ),
        (
            "[{component_type: RESIDENTS}, {component_type: ELECTRIC_HEATING_SH}, {component_type: BATTERY, "
            "required: true}]",
            "the required priority {component_type: BATTERY, required: true} of control-EMS (import control, port "
            "flows) ranks nothing",
        ),
    ],
)
def test_a_priority_entry_ranks_ranked_outputs_once(priorities: str, fragment: str) -> None:
    """An entry selecting a measured output or one an earlier entry ranks, a feed: block, a required miss: EF-7V."""
    text = controlled(
        control=f"  control: {{assembly: mock/ems_self_consumption, parameters: {{priorities: {priorities}}}}}\n"
    )
    message = refusal(text)

    assert message.startswith("EF-7V") and fragment in message


@pytest.mark.base
def test_a_controller_selection_may_not_author_a_weight() -> None:
    """No file authors a weight: a ``feed: {weight}`` on a controller's selection is refused (EF-7V)."""
    message = refusal(
        controlled(
            control="  control: {assembly: mock/ems_self_consumption, observes: [{component_type: RESIDENTS, "
            "feed: {weight: 4}}, {component_type: ELECTRIC_HEATING_SH}]}\n"
        )
    )

    assert (
        "EF-7V" in message and "writes the weight 4; a controller derives every weight from its priorities" in message
    )


@pytest.mark.base
def test_a_weight_reaching_999_and_two_ports_of_one_type_at_one_weight_are_refused(tmp_path: Path) -> None:
    """The probe ranks at 998: a second heater would get 999; the residents and a heater share (no type, 998)."""
    library = Library(tmp_path)
    library.add("control/probe", PROBE_CONTROLLER)
    resolver = library.resolver()
    heaters = "  a: {assembly: mock/smart_heater}\n  b: {assembly: mock/smart_heater}\n"

    message = refusal(site(WEATHER) + imports(heaters, "  probe: {assembly: control/probe}\n"), resolver)
    assert "EF-7V" in message and "derive the weight 999 for b-Heater.ElectricityInput" in message

    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports("  a: {assembly: mock/smart_heater}\n", "  probe: {assembly: control/probe}\n"),
        resolver,
    )
    assert (
        "EF-7V" in message
        and "ranks Occupancy.ElectricityConsumption and a-Heater.ElectricityInput, both None" in message
    )


# ------------------------------------------------------------------------------------------ actuation


@pytest.mark.base
def test_a_battery_without_a_controller_is_refused() -> None:
    """``controllable: {target_input: …}`` binds the one controller: none is EF-7U."""
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports("  pv: {assembly: mock/pv_array}\n", "  battery: {assembly: mock/home_battery}\n")
    )

    assert message.startswith(
        "EF-7U at import battery: battery-Battery.AcBatteryPowerUsed (port battery.electricity, "
        "controllable: {target_input: LoadingPowerInput}) has no controller"
    )


@pytest.mark.base
def test_two_controllers_actuating_one_target_are_refused() -> None:
    """Each target is actuated exactly once (D21): two managers ranking the battery is EF-7U."""
    message = refusal(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array}\n",
            "  battery: {assembly: mock/home_battery}\n",
            "  first: {assembly: mock/ems_self_consumption}\n",
            "  second: {assembly: mock/ems_self_consumption}\n",
        )
    )

    assert "EF-7U" in message and "is actuated twice, by first-EMS (import first, port flows) and second-EMS" in message


@pytest.mark.base
def test_a_controllable_output_ranked_without_its_need_bound_is_refused() -> None:
    """The heater's draw and its modifier are bound together or not at all (§4.4)."""
    message = refusal(
        SYSTEM_WITH_CONTROL
        + imports(
            "  heater: {assembly: mock/smart_heater, none: [ems_modifier]}\n",
            "  control: {assembly: mock/ems_self_consumption}\n",
        )
    )

    assert message.startswith(
        "EF-7U at import control: control-EMS (import control, port flows) ranks "
        "heater-Heater.ElectricityInput (port heater.electricity, controllable: {via: "
        "ems_modifier}), but its need 'ems_modifier' is not bound"
    )


@pytest.mark.base
def test_a_bound_controllable_output_the_controller_does_not_rank_is_refused() -> None:
    """``ems_modifier`` bound to the manager, which observes only the residents: EF-7U."""
    message = refusal(
        SYSTEM_WITH_CONTROL
        + imports(
            "  heater: {assembly: mock/smart_heater, optional-bind: {ems_modifier: control}}\n",
            "  control: {assembly: mock/ems_self_consumption, observes: [{component_type: RESIDENTS}]}\n",
        )
    )

    assert "EF-7U at import heater: heater-Heater.ElectricityInput" in message
    assert "is bound to the controller control-EMS through 'ems_modifier', but control-EMS does not rank it" in message


@pytest.mark.base
def test_a_controllable_naming_an_input_the_controller_may_not_actuate_is_refused(tmp_path: Path) -> None:
    """D21: the manager actuates only L1 modifiers and the inputs its class declares, never the heater's signal."""
    library = Library(tmp_path)
    library.add(
        "generator/wired_heater",
        f"""
        schema_version: 4
        kind: assembly
        name: generator/wired_heater
        description: A heater whose signal the manager would write.
        presets: {{standard: {{}}}}
        components:
          Heater: {{class: {MOCKS}.MockHeater, preset: standard}}
        interface:
          provides:
            electricity: {{output: Heater.ElectricityInput, controllable: {{target_input: Signal}}}}
        tests: {{bounds: [], monotone: []}}
        """,
    )

    message = refusal(
        SYSTEM_WITH_CONTROL
        + imports(
            "  heater: {assembly: generator/wired_heater}\n", "  control: {assembly: mock/ems_self_consumption}\n"
        ),
        library.resolver(),
    )

    assert "EF-7U" in message and "names the device input Signal, but MockEnergyManager actuates no input of" in message


@pytest.mark.base
def test_a_target_input_actuated_and_wired_is_refused(tmp_path: Path) -> None:
    """The battery's ``LoadingPowerInput`` is the manager's: a wire into it as well is EF-7U."""
    library = Library(tmp_path)
    library.add(
        "storage/wired_battery",
        f"""
        schema_version: 4
        kind: assembly
        name: storage/wired_battery
        description: A battery whose input is also wired.
        presets: {{standard: {{}}}}
        components:
          Heater: {{class: {MOCKS}.MockHeater, preset: standard}}
          Battery:
            class: {MOCKS}.MockBattery
            preset: sized_to_pv
            inputs: [{{input: LoadingPowerInput, from: Heater.ElectricityInput}}]
        interface:
          needs:
            pv_power: {{fact: pv_peak_power_in_watt, into: [Battery]}}
          provides:
            electricity: {{output: Battery.AcBatteryPowerUsed, controllable: {{target_input: LoadingPowerInput}}}}
        tests: {{bounds: [], monotone: []}}
        """,
    )

    message = refusal(
        SYSTEM_WITH_CONTROL
        + imports(
            "  pv: {assembly: mock/pv_array}\n",
            "  battery: {assembly: storage/wired_battery}\n",
            "  control: {assembly: mock/ems_self_consumption, observes: [{component_type: [RESIDENTS, BATTERY]}]}\n",
        ),
        library.resolver(),
    )

    assert (
        "EF-7U at components.battery-Battery: battery-Battery.LoadingPowerInput is actuated by control-EMS and "
        "also wired from battery-Heater.ElectricityInput" in message
    )


# -------------------------------------------------------------------------------- derived port names


@pytest.mark.base
def test_the_port_name_scheme_is_one_function() -> None:
    """hisim-lt0b.11: ``-`` becomes ``_`` in every derived port name; an identifier passes unchanged."""
    assert NameSyntax.port_name_part("pv-east-PVSystem") == "pv_east_PVSystem"
    assert NameSyntax.port_name_part("PVSystem") == "PVSystem"
    assert ResolvedDynamicConnection.input_name_for("pv-east-PVSystem", "ElectricityOutput") == (
        "ElectricityOutputFrompv_east_PVSystem"
    )
    assert ResolvedDynamicConnection.dispatch_name_for(
        "battery-Battery", "AcBatteryPowerUsed", "LoadingPowerInput"
    ) == ("DispatchTobattery_Battery_LoadingPowerInput")
    assert ResolvedDynamicConnection.dispatch_name_for("heater-Heater", "ElectricityInput", None) == (
        "DispatchForheater_Heater_ElectricityInput"
    )


@pytest.mark.base
def test_two_instances_of_one_class_feeding_one_observer_get_distinct_ports() -> None:
    """Two PV arrays into one meter: two ports, named after their addresses, shown in the record."""
    _expanded, record = expand(
        site(WEATHER, OCCUPANCY)
        + imports(
            "  pv: {assembly: mock/pv_array, instances: {east: {}, west: {}}}\n",
            "  grid: {assembly: mock/electricity_grid, observes: [{component_type: PV}]}\n",
        )
    )

    observer = record.observer("grid-Meter")
    assert observer is not None
    assert [feed.input_port for feed in observer.feeds] == [
        "ElectricityOutputFrompv_east_PVSystem",
        "ElectricityOutputFrompv_west_PVSystem",
    ]


@pytest.mark.base
def test_two_participants_whose_port_names_collide_are_refused() -> None:
    """A site entry ``pv_east_PVSystem`` beside the member ``pv-east-PVSystem``: one port name, EF-7W."""
    roof = f"pv_east_PVSystem:\n  class: {MOCKS}.MockPVSystem\n  preset: rooftop\n  inputs: [Weather]\n"

    message = refusal(
        site(WEATHER, OCCUPANCY, roof)
        + imports(
            "  pv: {assembly: mock/pv_array, instances: {east: {}}}\n",
            "  grid: {assembly: mock/electricity_grid, observes: [{component_type: PV}]}\n",
        )
    )

    assert message.startswith(
        "EF-7W at components.grid-Meter.inputs: 'grid-Meter' would grow the port "
        "'ElectricityOutputFrompv_east_PVSystem' "
        "for pv_east_PVSystem.ElectricityOutput and for pv-east-PVSystem.ElectricityOutput"
    )


# -------------------------------------------------------------------------------- the twin shapes (E)


#: The twin's metered_directly meter (``household_heatpump_building_sizer.grouped``, :145-171), on mock assemblies.
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

#: The twin's ems_with_battery manager and meter (:100-144, :105-109), on mock assemblies.
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
GRID_BALANCE = [
    AggregatorFeed(
        source="control-EMS", output="TotalElectricityToOrFromGrid", tags=("ELECTRICITY_PRODUCTION",), weight=999
    ),
]


def expanded_system(name: str) -> EnergySystemFile:
    """One committed mock system, expanded."""
    expanded, _record = expand_imports(parse_energy_system(Mocks.SYSTEMS / name), mock_resolver())
    return expanded


@pytest.mark.base
def test_the_metered_directly_shape_writes_exactly_the_twins_feeds() -> None:
    """No manager: the meter's default selection is item by item the twin's hand-written feeds (§4.2 gate)."""
    expanded = expanded_system("metered_house.energy_system.yaml")

    assert feeds(expanded, "grid-Meter") == METERED_DIRECTLY
    assert expanded.components["heater-Controller"].inputs == ()


@pytest.mark.base
def test_the_ems_with_battery_shape_writes_exactly_the_twins_feeds() -> None:
    """With a manager: its feeds — tags, weights, dispatch — and the meter's one feed are the twin's, item by item."""
    expanded = expanded_system("ems_house.energy_system.yaml")

    assert feeds(expanded, "control-EMS") == EMS_WITH_BATTERY
    assert feeds(expanded, "grid-Meter") == GRID_BALANCE
    assert [item.source for item in expanded.components["heater-Controller"].inputs] == ["control-EMS"]
    assert expanded.components["battery-Battery"].inputs == ()


# ------------------------------------------------------------------------------------------ one day


@pytest.fixture(name="ems_run", scope="module")
def fixture_ems_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Runs the mock house with an energy manager and a battery for one day, with the energy balance."""
    result = tmp_path_factory.mktemp("ems_run")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(AssemblyResolver.ENVIRONMENT_VARIABLE, str(Mocks.LIBRARY))
    try:
        code = main(
            [
                "energy-system",
                "run",
                str(Mocks.SYSTEMS / "ems_house.energy_system.yaml"),
                str(Mocks.ROOT / "one_day_balance.simulation.yaml"),
                "--result-dir",
                str(result),
            ]
        )
    finally:
        monkeypatch.undo()
    assert code == 0
    return result


@pytest.mark.base
def test_the_ems_house_runs_one_day_with_the_balance_closed(ems_run: Path) -> None:
    """The composed house builds, wires its derived ports and closes its energy balance for one day."""
    import yaml  # pylint: disable=import-outside-toplevel

    balance = json.loads((ems_run / "balance_report.json").read_text(encoding="utf-8"))
    assert balance["verdict"] == "closes"
    heater = next(item for item in balance["components"] if item["component"] == "heater-Heater")
    assert heater["verdict"] == "closes" and heater["throughput_kwh"] > 0

    realized = yaml.safe_load((ems_run / "realized.energy_system.yaml").read_text(encoding="utf-8"))
    observers = realized["metadata"]["imports"]["observers"]
    assert [observer["observer"] for observer in observers] == ["control-EMS", "grid-Meter"]
    assert [feed["weight"] for feed in observers[0]["feeds"]] == [1, 999, 2, 6]
    assert observers[0]["feeds"][3]["dispatch_port"] == "DispatchTobattery_Battery_LoadingPowerInput"
    assert [actuation["kind"] for actuation in realized["metadata"]["imports"]["actuations"]] == ["via", "target_input"]
    connections = (ems_run / "component_connections.json").read_text(encoding="utf-8")
    assert "DispatchTobattery_Battery_LoadingPowerInput" in connections
