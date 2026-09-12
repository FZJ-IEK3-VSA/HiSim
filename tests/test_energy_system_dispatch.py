"""The controlled-participant path of an aggregator, driven end to end from a file.

An energy management system does two things with a battery: it measures what the battery draws
and it tells the battery what to draw. The first is an ordinary feed. The second is the dispatch
back-channel — an output the aggregator grows for exactly this participant and a wire from that
output into the participant's own input — and it is the one part of the wiring stage that a
minimal household never exercises, because a meter only measures.

The tests here build the smallest system that has a controlled participant: a weather station,
an occupancy, a photovoltaic array, a battery, the energy management system that ranks them, and
the meter that measures the grid exchange. Neither the array nor the battery has presets yet, so
both are written as complete ``config`` blocks, which the format allows for exactly this reason.
The positive tests run a simulated day and look at the wires and the results; the negative tests
pin the rejections that make the rule unambiguous — a controlled participant whose signal nobody
consumes leaves a mandatory input unfed, an author may not reach the derived output from the
participant's side because that name is not in the file they are reading, and no two participants
may end up sharing one control signal, which an aggregator matches by tags and weight and could
therefore never tell apart. The ambiguity rejections — a shared claim, a signal another class
owns, an aggregator already publishing two answers, and a published superset of a claim — are
provoked on the resolver, because no realistic file arranges an aggregator's published signals
that way today.

Each test states the failure mode it catches.
"""

# clean

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Tuple

import pandas as pd
import pytest
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, SingleTimeStepValues
from hisim.components.advanced_battery_bslib import Battery, BatteryConfig
from hisim.components.controller_l2_energy_management_system import (
    EMSConfig,
    L2GenericEnergyManagementSystem,
)
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.config.channels import DispatchRule, DynamicConnectionChannel
from hisim.dynamic_component import DynamicComponent
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import EnergySystemWiringError
from hisim.energy_system.executor import build_energy_system, run_energy_system
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.simulationparameters import SimulationParameters


class Household:
    """The PV-battery-EMS household the tests vary one line at a time.

    The array and the battery are complete ``config`` blocks copied from their legacy default
    factories, so that the test depends on nothing the component sweep has yet to deliver. The
    aggregator's participants are written out in full — production, uncontrolled consumption and
    the controlled battery — because a controlled participant cannot be a bare item: the class's
    default feeds carry no dispatch target, and the storage channel requires one.
    """

    #: The battery's controlled feed as it must be written: the dispatch block names the input.
    BATTERY_FEED_WITH_TARGET: ClassVar[str] = """      - from: battery.AcBatteryPowerUsed
        component_type: BATTERY
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 6
        dispatch: {target_input: LoadingPowerInput}
"""

    #: The same feed with a dispatch signal nobody consumes.
    BATTERY_FEED_WITHOUT_TARGET: ClassVar[str] = """      - from: battery.AcBatteryPowerUsed
        component_type: BATTERY
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 6
        dispatch: {}
"""

    #: An author trying to wire the derived output from the battery's side.
    BATTERY_WIRE_ONTO_DERIVED_PORT: ClassVar[str] = """    inputs:
      - input: LoadingPowerInput
        from: ems.DispatchTobattery_LoadingPowerInput
"""

    @classmethod
    def text(cls, battery_feed: str, battery_inputs: str = "") -> str:
        """Assembles the file.

        Args:
            battery_feed: The battery's item in the aggregator's ``inputs`` list.
            battery_inputs: An optional ``inputs`` block on the battery entry itself.

        Returns:
            The complete energy-system text.
        """
        return f"""schema_version: 3
name: pv battery ems household
components:
  weather:
    class: hisim.components.weather.Weather
    preset: standard
  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
  pv:
    class: hisim.components.generic_pv_system.PVSystem
    config:
      time: 2019
      location: Aachen
      module_name: Trina Solar TSM-435NE09RC.05
      integrate_inverter: true
      inverter_name: "Enphase Energy Inc : IQ8P-3P-72-E-DOM-US [208V]"
      module_database: CEC_MODULE_DATABASE
      inverter_database: CEC_INVERTER_DATABASE
      power_in_watt: 10000.0
      azimuth: 180
      tilt: 30
      share_of_maximum_pv_potential: 1.0
      load_module_data: false
      source_weight: 0
      device_co2_footprint_in_kg: null
      investment_costs_in_euro: null
      lifetime_in_years: null
      maintenance_costs_in_euro_per_year: null
      subsidy_as_percentage_of_investment_costs: null
      predictive_control: false
      prediction_horizon: null
    inputs:
      - weather
  battery:
    class: hisim.components.advanced_battery_bslib.Battery
    config:
      source_weight: 1
      system_id: SG1
      custom_pv_inverter_power_generic_in_watt: 5000.0
      custom_battery_capacity_generic_in_kilowatt_hour: 10
      charge_in_kwh: 0
      discharge_in_kwh: 0
      device_co2_footprint_in_kg: null
      investment_costs_in_euro: null
      lifetime_in_years: null
      maintenance_costs_in_euro_per_year: null
      subsidy_as_percentage_of_investment_costs: null
      lifetime_in_cycles: 5000.0
{battery_inputs}  ems:
    class: hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    preset: optimize_own_consumption
    inputs:
      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
      - from: pv.ElectricityOutput
        component_type: PV
        tags: [ELECTRICITY_PRODUCTION]
        weight: 999
{battery_feed}  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    preset: standard
    inputs:
      - from: ems.TotalElectricityToOrFromGrid
        tags: [ELECTRICITY_PRODUCTION]
        weight: 999
"""

    @classmethod
    def write(cls, directory: Path, battery_feed: str, battery_inputs: str = "") -> Path:
        """Writes the file into a test directory.

        Args:
            directory: The test's temporary directory.
            battery_feed: See :meth:`text`.
            battery_inputs: See :meth:`text`.

        Returns:
            The path of the written file.
        """
        path = directory / "pv_battery_ems.energy_system.yaml"
        path.write_text(cls.text(battery_feed, battery_inputs), encoding="utf-8")
        return path

    #: The shipped one-day parameters, resolved from this file rather than from the working
    #: directory: the test suite is run from ``tests/`` in CI and from the repository root
    #: locally, so a relative path would find the file in only one of the two.
    SHIPPED_PARAMETERS: ClassVar[Path] = (
        Path(__file__).resolve().parents[1] / "energy_systems" / "one_day_15min.simulation.yaml"
    )

    @classmethod
    def parameters(cls, directory: Path) -> SimulationParameters:
        """One simulated day at a quarter-hour, writing into the test directory.

        Args:
            directory: The test's temporary directory.

        Returns:
            The parameters.
        """
        parameters = SimulationParameters.one_day_only(2021, 900)
        parameters.result_directory = str(directory / "results")
        return parameters


@pytest.mark.base
def test_a_controlled_battery_is_ranked_told_what_to_draw_and_simulated(tmp_path: Path) -> None:
    """Catches the dispatch back-channel existing on paper only.

    The wiring tests prove that a dispatch block grows an output; nothing else proves that the
    output reaches the participant and that the aggregator's weight-matched lookups find it at
    run time. If the back wire were missing, or the derived ports carried the wrong tags or
    weight, the battery would be ranked, never told anything, and the day would still simulate.
    """
    path = Household.write(tmp_path, Household.BATTERY_FEED_WITH_TARGET)
    parameters_path = Household.SHIPPED_PARAMETERS

    built = run_energy_system(path, parameters_path, result_directory=str(tmp_path / "results"))

    wires = {
        f"{wire.source_name}.{wire.source_output} -> {wire.target_name}.{wire.target_input}"
        for wire in built.wired.wires
    }
    assert "battery.AcBatteryPowerUsed -> ems.AcBatteryPowerUsedFrombattery" in wires
    assert "ems.DispatchTobattery_LoadingPowerInput -> battery.LoadingPowerInput" in wires
    ems = built.wired.component_of("ems")
    dispatch = next(port for port in ems.outputs if port.field_name == "DispatchTobattery_LoadingPowerInput")
    assert dispatch is not None
    resolved = dict(built.wired.resolved_feeds)["ems"]
    assert [connection.weight for connection in resolved if connection.dispatch is not None] == [6]
    assert (tmp_path / "results" / "finished.flag").is_file()
    assert list((tmp_path / "results").glob("*.csv")), "the run wrote no result table"


@pytest.mark.base
def test_a_controlled_battery_whose_signal_nobody_consumes_leaves_its_input_unfed(
    tmp_path: Path,
) -> None:
    """Catches a battery that is ranked and served but never told anything.

    ``dispatch: {}`` is legal — it records a signal — but the battery's power input is
    mandatory, so a file that stops there describes a battery that cannot work. The rejection
    has to name the battery and its input, because the author's mistake is on the aggregator's
    line, three entries away.
    """
    path = Household.write(tmp_path, Household.BATTERY_FEED_WITHOUT_TARGET)

    with pytest.raises(EnergySystemWiringError) as caught:
        build_energy_system(path, Household.parameters(tmp_path))

    message = str(caught.value)
    assert "battery" in message
    assert "LoadingPowerInput" in message


@pytest.mark.base
def test_an_author_may_not_wire_the_derived_dispatch_output_from_the_battery(
    tmp_path: Path,
) -> None:
    """Catches the derived port names leaking into hand-written files.

    The aggregator's outputs are named by a template the author never sees, so a wire that
    names one binds the file to an implementation detail; the rule is that the back-channel is
    written once, on the aggregator's feed. Both spellings at once would also feed one input
    twice.
    """
    path = Household.write(
        tmp_path,
        Household.BATTERY_FEED_WITH_TARGET,
        battery_inputs=Household.BATTERY_WIRE_ONTO_DERIVED_PORT,
    )

    with pytest.raises(EnergySystemWiringError) as caught:
        build_energy_system(path, Household.parameters(tmp_path))

    assert "DispatchTobattery_LoadingPowerInput" in str(caught.value)


class SignalCollisions:
    """Builds the aggregator and the participants the two ambiguity tests provoke by hand.

    Both conditions below need an aggregator whose published signals are arranged in a way no
    realistic file produces yet, so they are provoked on the resolver rather than through a
    document — the same route the port-name collision takes in the wiring tests. Sharing the
    construction here keeps the two tests about the rule and not about the fixture.
    """

    #: Weight the battery channel is exercised at, chosen because the energy management system
    #: publishes no signal of its own at it and the tests therefore control what is there.
    BATTERY_WEIGHT: ClassVar[int] = 6

    @classmethod
    def battery(cls, name: str) -> Battery:
        """Builds one battery participant.

        Args:
            name: Its instance name, which is also its key in the resolver's index.

        Returns:
            The battery.
        """
        return Battery(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 900),
            config=BatteryConfig.get_default_config(name=name),
        )

    @classmethod
    def ems(cls) -> L2GenericEnergyManagementSystem:
        """Builds the aggregator, carrying only the signals its own constructor publishes.

        Returns:
            The energy management system.
        """
        built: L2GenericEnergyManagementSystem = L2GenericEnergyManagementSystem(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, 900),
            config=EMSConfig.preset_optimize_own_consumption("ems"),
        )
        return built

    @classmethod
    def feed(cls, source: str) -> FeedRequest:
        """Builds the controlled battery feed both tests address at the aggregator.

        Args:
            source: Name of the battery the feed measures.

        Returns:
            The feed request, dispatching into the battery's own power input.
        """
        return FeedRequest(
            consumer="ems",
            source=source,
            output=Battery.AcBatteryPowerUsed,
            component_type=lt.ComponentType.BATTERY,
            flow_tags=(lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,),
            weight=cls.BATTERY_WEIGHT,
            has_dispatch=True,
            dispatch_target_input="LoadingPowerInput",
        )


@pytest.mark.base
def test_two_participants_may_not_claim_one_control_signal() -> None:
    """Catches two ranked participants being steered through a single output.

    An aggregator finds a participant's signal by that participant's tags and weight, so two
    feeds agreeing on both describe two participants and one signal. Nothing about the port names
    would notice: the derived names differ, the file reads fine, and at run time the aggregator
    zips one participant list against a signal list of a different length, steering everything
    after the duplicate through somebody else's port while the numbers stay plausible. The
    rejection names the tags and the weight, because that pair is the whole of what collided.
    """
    resolver = DynamicConnectionResolver(
        {
            "battery": SignalCollisions.battery("battery"),
            "battery2": SignalCollisions.battery("battery2"),
            "ems": SignalCollisions.ems(),
        }
    )

    with pytest.raises(EnergySystemWiringError) as caught:
        resolver.resolve_target(
            "ems", [SignalCollisions.feed("battery"), SignalCollisions.feed("battery2")]
        )

    assert caught.value.error_id.value == "EF-2B"
    assert "ELECTRICITY_TARGET" in str(caught.value)
    assert f"weight {SignalCollisions.BATTERY_WEIGHT}" in str(caught.value)


@pytest.mark.base
def test_a_signal_another_participant_class_already_owns_is_refused() -> None:
    """Catches a file quietly duplicating a signal an aggregator publishes for somebody else.

    Adoption is what keeps a file from growing a second output beside one the aggregator's own
    constructor made, and it is deliberately narrow: an output created *for* a participant class
    serves that class only, because the registry drops the outputs of classes absent from the
    system and adopting one across classes would hand a participant a port about to be pruned.
    Where adoption is therefore impossible and creation would duplicate, the only honest answer
    is a refusal that names the port and the class it belongs to.
    """
    ems = SignalCollisions.ems()
    ems.add_component_output(
        source_output_name="SignalOfSomebodyElse",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_component_class="SomeOtherParticipant",
        source_weight=SignalCollisions.BATTERY_WEIGHT,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="A signal the battery's feed must not take over.",
    )
    resolver = DynamicConnectionResolver({"battery": SignalCollisions.battery("battery"), "ems": ems})

    with pytest.raises(EnergySystemWiringError) as caught:
        resolver.resolve_target("ems", [SignalCollisions.feed("battery")])

    assert caught.value.error_id.value == "EF-2B"
    assert "SignalOfSomebodyElse" in str(caught.value)
    assert "SomeOtherParticipant" in str(caught.value)


@pytest.mark.base
def test_an_aggregator_already_publishing_two_answers_is_refused() -> None:
    """Catches adoption blessing an ambiguity the aggregator already carries.

    Two published ports at one (tags, weight) are indistinguishable to the runtime's lookup
    before any file arrives; adopting the first would leave the second in the pairing lists,
    which is the very duplicate the planner exists to refuse. The refusal names both ports so
    the constructor that grew them can be fixed.
    """
    ems = SignalCollisions.ems()
    for port_name in ("SignalA", "SignalB"):
        ems.add_component_output(
            source_output_name=port_name,
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
            source_component_class=None,
            source_weight=SignalCollisions.BATTERY_WEIGHT,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="one of two ports the runtime cannot tell apart",
        )
    resolver = DynamicConnectionResolver({"battery": SignalCollisions.battery("battery"), "ems": ems})

    with pytest.raises(EnergySystemWiringError) as caught:
        resolver.resolve_target("ems", [SignalCollisions.feed("battery")])

    assert caught.value.error_id.value == "EF-2B"
    assert "SignalA" in str(caught.value)
    assert "SignalB" in str(caught.value)


@pytest.mark.base
def test_a_published_superset_of_a_claim_is_refused_instead_of_shadowed() -> None:
    """Catches the planner's exact identity silently diverging from the runtime's containment.

    The planner treats two signals as one only when their tag sets are equal, but the runtime
    lookup matches by containment: a published port whose tags strictly contain a claim's set at
    the same weight is found by that lookup alongside whatever the claim produces, and the
    pairing lists come out one entry too long. Such a port cannot come out of the format itself,
    so meeting one means an imperative constructor built it — and the answer is a refusal naming
    both tag sets, never a quietly grown duplicate.
    """
    ems = SignalCollisions.ems()
    ems.add_component_output(
        source_output_name="SupersetSignal",
        source_tags=[
            lt.ComponentType.BATTERY,
            lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
            lt.InandOutputType.ELECTRICITY_TARGET,
        ],
        source_component_class=None,
        source_weight=SignalCollisions.BATTERY_WEIGHT,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="a port carrying more tags than the claim, at the claim's weight",
    )
    resolver = DynamicConnectionResolver({"battery": SignalCollisions.battery("battery"), "ems": ems})

    with pytest.raises(EnergySystemWiringError) as caught:
        resolver.resolve_target("ems", [SignalCollisions.feed("battery")])

    assert caught.value.error_id.value == "EF-2B"
    assert "SupersetSignal" in str(caught.value)
    assert "containment" in str(caught.value)


@pytest.mark.base
def test_a_port_published_for_a_class_object_still_serves_that_class() -> None:
    """Catches adoption being defeated by the type of the stored participant class.

    Some imperative call sites hand the participant class over as the class object rather than
    its name; stored verbatim it would never equal the class-name string the planner compares
    against, the port would refuse every adoption, and a duplicate would grow beside it. The
    planner normalises the identity to the name, so the object and the string mean one class.
    """
    ems = SignalCollisions.ems()
    ems.add_component_output(
        source_output_name="SignalStoredByClassObject",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_component_class=Battery,  # type: ignore[arg-type] # the imperative sites do this
        source_weight=SignalCollisions.BATTERY_WEIGHT,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="a port whose participant class arrived as the class object",
    )
    resolver = DynamicConnectionResolver({"battery": SignalCollisions.battery("battery"), "ems": ems})

    resolved = resolver.resolve_target("ems", [SignalCollisions.feed("battery")])

    adopted = resolved[0].adopted_dispatch_output
    assert adopted is not None
    assert adopted.startswith("SignalStoredByClassObject"), (
        "the numeric suffix is the aggregator's internal output counter, not a contract"
    )


@pytest.mark.base
def test_an_adoption_without_a_dispatch_block_cannot_be_constructed() -> None:
    """Catches the dangling state both name properties would silently hide.

    An adopted port means something only for a dispatching connection: with the dispatch block
    gone, dispatch_output_name and created_dispatch_output_name both answer None and the adopted
    name sits on the record invisible to every consumer. The record refuses the combination at
    construction, which also guards the dataclasses.replace escape hatch the planner uses.
    """
    ems = SignalCollisions.ems()
    ems.add_component_output(
        source_output_name="TheSignal",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_component_class=None,
        source_weight=SignalCollisions.BATTERY_WEIGHT,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="the port the feed adopts",
    )
    resolver = DynamicConnectionResolver({"battery": SignalCollisions.battery("battery"), "ems": ems})
    resolved = resolver.resolve_target("ems", [SignalCollisions.feed("battery")])
    assert resolved[0].adopted_dispatch_output is not None

    with pytest.raises(ValueError, match="dispatch"):
        dataclasses.replace(resolved[0], dispatch=None)


@dataclass_json
@dataclass
class MiniActuatorConfig(ConfigBase):
    """Configuration of the echoing actuator of the adoption round-trip test.

    The actuator draws one constant power and repeats whatever command it is given, so the
    only knob it has is that constant draw. The class exists because the executor pairs a
    component with the dataclass its constructor annotates, and a component without a config
    class therefore cannot appear in a file at all.
    """

    component_id: ComponentID
    idle_power_in_watt: float

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the component this configuration belongs to."""
        return MiniActuator.get_full_classname()


class MiniActuator(Component):
    """The smallest controllable participant: one measured draw, one command it echoes.

    Its power output is what the aggregator measures and its command input is what the
    dispatch back-channel must reach; the echo output turns the received command into a result
    column, which is the only way a file-driven test can observe what arrived at run time
    without reaching into simulator internals.
    """

    #: The mandatory input the ``dispatch`` block of the aggregator's feed must end up feeding.
    ControlInput: ClassVar[str] = "ControlInput"

    #: The received command, written back out so the run's result table proves delivery.
    ControlEcho: ClassVar[str] = "ControlEcho"

    #: The constant draw the aggregator's feed measures.
    PowerUsed: ClassVar[str] = "PowerUsed"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MiniActuatorConfig) -> None:
        """Builds the actuator with its one input and two outputs.

        Args:
            my_simulation_parameters: The run's simulation parameters.
            config: The actuator's configuration, carrying its identity and constant draw.
        """
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.actuator_config = config
        self.control_input = self.add_input(
            self.component_name, self.ControlInput, lt.LoadTypes.ELECTRICITY, lt.Units.WATT, mandatory=True
        )
        self.echo_output = self.add_output(
            self.component_name,
            self.ControlEcho,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="The command the aggregator's adopted port delivered, repeated verbatim.",
        )
        self.power_output = self.add_output(
            self.component_name,
            self.PowerUsed,
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            output_description="The constant draw the aggregator's feed measures.",
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Echoes the received command and reports the constant draw."""
        stsv.set_output_value(self.echo_output, stsv.get_input_value(self.control_input))
        stsv.set_output_value(self.power_output, self.actuator_config.idle_power_in_watt)

    def i_prepare_simulation(self) -> None:
        """No-op: the actuator needs no preparation."""

    def i_save_state(self) -> None:
        """No-op: the actuator is stateless."""

    def i_restore_state(self) -> None:
        """No-op: the actuator is stateless."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """No-op: there is nothing to double-check about an echo."""


@dataclass_json
@dataclass
class MiniAggregatorConfig(ConfigBase):
    """Configuration of the steering aggregator of the adoption round-trip test.

    The aggregator's whole behaviour is to send one constant command to every participant it
    controls, so that constant is its only field beside the identity every configuration
    carries.
    """

    component_id: ComponentID
    steering_command_in_watt: float

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the component this configuration belongs to."""
        return MiniAggregator.get_full_classname()


class MiniAggregator(DynamicComponent):
    """An aggregator whose constructor already publishes the actuator's control signal.

    A realistic aggregator grows its dispatch outputs from the file's feeds; this one instead
    publishes the port in ``__init__``, tagged and weighted exactly as the actuator's claim
    will be and reserved for the actuator's class — the arrangement adoption exists for. Its
    simulation code then finds the port the way every aggregator does, by tags and weight
    through the shared bookkeeping, so a port that was adopted rather than created must behave
    identically or the run fails.
    """

    #: The one channel this aggregator declares: a controlled consumer it must steer.
    ACTUATION_CHANNEL: ClassVar[str] = "actuation"

    #: The weight both the constructor's published port and the file's feed use; they must
    #: agree for the port to be adoptable at all.
    PARTICIPANT_WEIGHT: ClassVar[int] = 4

    #: Base name of the constructor-published port; ``add_component_output`` appends its
    #: internal ``Output<n>`` counter to it.
    PUBLISHED_PORT_BASENAME: ClassVar[str] = "ConstructorGrownCommand"

    CHANNELS: ClassVar[Tuple[DynamicConnectionChannel, ...]] = (
        DynamicConnectionChannel(
            key=ACTUATION_CHANNEL,
            tags=frozenset(
                {
                    lt.ComponentType.BATTERY,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                }
            ),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.REQUIRED,
            dispatch_tags=frozenset({lt.InandOutputType.ELECTRICITY_TARGET}),
        ),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MiniAggregatorConfig) -> None:
        """Builds the aggregator and publishes the port the file's feed is meant to adopt.

        Args:
            my_simulation_parameters: The run's simulation parameters.
            config: The aggregator's configuration, carrying its identity and the command.
        """
        super().__init__(
            my_component_inputs=[],
            my_component_outputs=[],
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.aggregator_config = config
        self.add_component_output(
            source_output_name=self.PUBLISHED_PORT_BASENAME,
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
            source_component_class=MiniActuator.get_classname(),
            source_weight=self.PARTICIPANT_WEIGHT,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="The control signal, published before any file is read.",
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Steers every controlled participant with the configured constant command.

        The lookup goes through the same tag-and-weight search the real aggregators use, so it
        finds the constructor-published port only through the bookkeeping the planner promised
        to keep truthful when it adopted the port instead of growing a second one.
        """
        channel = self.get_channel(self.ACTUATION_CHANNEL)
        signals = self.get_all_dynamic_outputs(
            tags=[lt.ComponentType.BATTERY, *channel.dispatch_tags],
            weight_counter=self.PARTICIPANT_WEIGHT,
        )
        for signal in signals:
            stsv.set_output_value(signal, self.aggregator_config.steering_command_in_watt)

    def i_prepare_simulation(self) -> None:
        """No-op: the aggregator needs no preparation."""

    def i_save_state(self) -> None:
        """No-op: the aggregator is stateless."""

    def i_restore_state(self) -> None:
        """No-op: the aggregator is stateless."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """No-op: a constant command needs no double-check."""


class AdoptionRoundTrip:
    """The two-component file whose dispatch block adopts a constructor-published port.

    The file is the smallest one in which adoption can happen at all: the actuator, and the
    aggregator whose constructor already publishes the matching signal. Everything the test
    asserts — the adopted wire and the delivered command — is decided by this text plus the
    two classes above.
    """

    #: The command the aggregator sends; chosen to be unmistakable among the zeros
    #: an unwired signal would produce.
    COMMAND_IN_WATT: ClassVar[float] = 321.0

    #: The complete energy-system file.
    TEXT: ClassVar[str] = f"""schema_version: 3
name: adoption round trip
components:
  actuator:
    class: tests.test_energy_system_dispatch.MiniActuator
    config:
      idle_power_in_watt: 100.0
  mini_ems:
    class: tests.test_energy_system_dispatch.MiniAggregator
    config:
      steering_command_in_watt: {COMMAND_IN_WATT}
    inputs:
      - from: actuator.PowerUsed
        component_type: BATTERY
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: {MiniAggregator.PARTICIPANT_WEIGHT}
        dispatch: {{target_input: ControlInput}}
"""

    @classmethod
    def write(cls, directory: Path) -> Path:
        """Writes the file into a test directory.

        Args:
            directory: The test's temporary directory.

        Returns:
            The path of the written file.
        """
        path = directory / "adoption_round_trip.energy_system.yaml"
        path.write_text(cls.TEXT, encoding="utf-8")
        return path


@pytest.mark.base
def test_an_adopted_dispatch_port_steers_its_participant_through_a_whole_run(tmp_path: Path) -> None:
    """Catches an adopted port that exists on paper but carries nothing at run time.

    The resolver tests above prove that adoption is decided correctly; none of them prove that
    an adopted port is then actually wired to the participant and found by the aggregator's own
    tag-and-weight lookup during simulation. If adoption dropped the back-wire, or the adopted
    port's bookkeeping diverged from what the runtime searches, the actuator's mandatory input
    would either fail the build or silently read zero — and the echo column is where either
    mistake becomes visible as a number.
    """
    path = AdoptionRoundTrip.write(tmp_path)

    built = run_energy_system(
        path, Household.SHIPPED_PARAMETERS, result_directory=str(tmp_path / "results")
    )

    resolved = dict(built.wired.resolved_feeds)["mini_ems"]
    assert len(resolved) == 1
    connection = resolved[0]
    assert connection.adopted_dispatch_output is not None
    assert connection.adopted_dispatch_output.startswith(MiniAggregator.PUBLISHED_PORT_BASENAME)
    assert connection.created_dispatch_output_name is None, "adoption must not also grow a port"
    wires = {
        f"{wire.source_name}.{wire.source_output} -> {wire.target_name}.{wire.target_input}"
        for wire in built.wired.wires
    }
    assert f"mini_ems.{connection.dispatch_output_name} -> actuator.ControlInput" in wires

    echo_table = pd.read_csv(tmp_path / "results" / "actuator_ControlEcho.csv", index_col=0)
    echoed = echo_table[echo_table.columns[0]]
    assert (echoed == AdoptionRoundTrip.COMMAND_IN_WATT).all(), (
        "the actuator did not hear the command through the adopted port"
    )
