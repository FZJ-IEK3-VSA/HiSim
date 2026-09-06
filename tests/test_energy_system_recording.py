"""Tests for the recorder: a Python setup in, an energy-system file out, and the file works.

The recorder is the only part of this format that runs backwards — everything else reads a file and
builds a system, and this reads a system and writes the file. That makes its failure mode a quiet
one: a file that looks plausible, loads, and describes something slightly different from the setup
it came from. So the tests here are almost all comparisons against the run itself rather than
against an expected text, and the three committed fixtures are compared against a fresh recording
rather than against a copy of themselves.

Three setups are recorded, chosen for the three shapes an input item can take: ``basic_household``
for bare defaults and explicit wires, ``dynamic_components`` for aggregator feeds carrying a control
back-channel, and ``automatic_default_connections`` for a system wired entirely by the simulator's
own automatic pass. Recording each of them runs its constructors — weather, load profiles — so it
happens once for the whole module and every test after that is an assertion on plain data.

Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple

import pytest
import yaml

from hisim.cli import ExitCodes, main
from hisim.config import ComponentID, ConfigBase, preset_provenance
from hisim.energy_system.errors import EnergySystemRecordingError
from hisim.energy_system.executor import (
    SimulationParametersReader,
    build_energy_system,
    write_records,
)
from hisim.energy_system.loader import dump_energy_system, load_energy_system
from hisim.energy_system.metadata import RunMetadata
from hisim.energy_system.model import AggregatorFeed, DefaultInputs, ExplicitWire
from hisim.energy_system.parity import ResolvedWire, WiringSnapshot
from hisim.energy_system.record import assert_no_sentinels
from hisim.energy_system.recording import (
    InputItemWriter,
    ObservedComponent,
    ObservedDispatch,
    ObservedFeed,
    ParameterFileLibrary,
    ParameterFileWriter,
    PortablePathGuard,
    RecordedSystem,
    RecordingResult,
    build,
    observe,
    record_setup,
)
from hisim.energy_system.recording.parameters import ParameterNormalisation
from hisim.energy_system.recording.session import RecordedFileWriter, RecordingSession
from hisim.energy_system.resolution import ResolvedDynamicConnection
from hisim.simulationparameters import SimulationParameters


class Fixtures:
    """The setups these tests record, the parameters they run under, and where the twins live.

    The three setups are the smallest set covering the three input shapes; recording more would cost
    minutes and prove nothing this module does not already assert. The parameters are the shipped
    one-day file rather than a private copy, so that the committed twins and the command line
    documented in the README are produced by the same pair of inputs.
    """

    #: The repository root, from which every other path here is derived.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: Where the Python setups live.
    SETUPS: ClassVar[Path] = ROOT / "system_setups"

    #: Where the committed twins and the shared parameter files live.
    ENERGY_SYSTEMS: ClassVar[Path] = ROOT / "energy_systems"

    #: One January day at a quarter-hour resolution, the pair every twin is recorded with.
    PARAMETERS: ClassVar[Path] = ENERGY_SYSTEMS / "one_day_15min.simulation.yaml"

    #: The setups recorded for this module, and committed as twins beside the exemplar.
    RECORDED: ClassVar[Tuple[str, ...]] = (
        "basic_household",
        "dynamic_components",
        "automatic_default_connections",
    )

    #: The one energy system that is already a file, used where a wired simulator is needed and its
    #: provenance is beside the point.
    MOCKUP: ClassVar[Path] = ENERGY_SYSTEMS / "gas_boiler_household.energy_system.yaml"

    @classmethod
    def parameters(cls, result_directory: Path) -> SimulationParameters:
        """Reads the shipped parameters and points them at a directory the test owns.

        Args:
            result_directory: The test's temporary directory.

        Returns:
            The parameters of the run.
        """
        parameters = SimulationParametersReader.read(cls.PARAMETERS)
        parameters.result_directory = str(result_directory)
        return parameters

    @classmethod
    def committed(cls, setup: str) -> Path:
        """The checked-in twin of one setup.

        Args:
            setup: The setup's stem.

        Returns:
            Its path under ``energy_systems/``.
        """
        return cls.ENERGY_SYSTEMS / f"{setup}{RecordedFileWriter.SUFFIX}"


class Synthetic:
    """Hand-written observations, for the judgements that must not need a whole simulation to check.

    Two of the recorder's rules are decisions about data rather than translations of it: whether a
    set of wires may be written as a bare item, and whether a name can be written at all. Both are
    reachable from a three-line observation, and checking them there is what makes the tests state
    the rule instead of hoping a fleet-sized recording happens to contain an instance of it.
    """

    #: Name of the consuming component in every synthetic observation.
    TARGET: ClassVar[str] = "Consumer"

    #: Name of the producing component in every synthetic observation.
    SOURCE: ClassVar[str] = "Producer"

    #: The class the consumer declares its defaults for, matching the producer's own class name.
    SOURCE_CLASS: ClassVar[str] = "Producer"

    #: Name of the aggregator in the observations that exercise the back-channel pairing.
    AGGREGATOR: ClassVar[str] = "Aggregator"

    #: The input port the aggregator grew for its one participant.
    GROWN_PORT: ClassVar[str] = "GrownInput"

    #: The producer output that feeds that port.
    MEASURED_OUTPUT: ClassVar[str] = "Power"

    @classmethod
    def config(cls, name: str) -> ConfigBase:
        """Builds the smallest configuration a component entry can carry.

        Args:
            name: The component's name, which is its whole identity here.

        Returns:
            A bare configuration object.
        """
        return ConfigBase(component_id=ComponentID(name=name))

    @classmethod
    def system(cls, wires: Tuple[Tuple[str, str], ...], declared: Tuple[Tuple[str, str], ...]) -> RecordedSystem:
        """Builds an observation of one producer feeding one consumer.

        Args:
            wires: The ``(target input, source output)`` pairs actually connected.
            declared: The pairs the consumer declares as its defaults for the producer's class.

        Returns:
            The observation.
        """
        producer = ObservedComponent(
            name=cls.SOURCE,
            class_path="tests.Producer",
            class_name=cls.SOURCE_CLASS,
            config=cls.config(cls.SOURCE),
            connect_automatically=False,
            default_connections={},
        )
        consumer = ObservedComponent(
            name=cls.TARGET,
            class_path="tests.Consumer",
            class_name="Consumer",
            config=cls.config(cls.TARGET),
            connect_automatically=True,
            default_connections={cls.SOURCE_CLASS: declared},
        )
        snapshot = WiringSnapshot(
            components=(cls.SOURCE, cls.TARGET),
            wires=tuple(
                sorted(
                    ResolvedWire(cls.TARGET, target_input, cls.SOURCE, source_output)
                    for target_input, source_output in wires
                )
            ),
            unconnected_inputs=(),
        )
        return RecordedSystem(
            setup="tests/synthetic.py",
            components=(producer, consumer),
            wiring=snapshot,
            simulation_parameters=SimulationParameters.one_day_only(2021, 900),
        )

    @classmethod
    def aggregator(cls, feed_weight: int, dispatch_weights: Tuple[int, ...]) -> RecordedSystem:
        """Builds an observation of one producer feeding an aggregator that grew a port for it.

        The aggregator carries one feed and as many unread control outputs as ``dispatch_weights``
        names, which is the shape the back-channel pairing has to decide: none of them names a
        consumer, so the pairing falls back to the participant's class and the feed's weight.

        Args:
            feed_weight: The weight of the aggregator's feed from the producer.
            dispatch_weights: One weight per control output the aggregator grew, all of them
                created for the producer's class and read by nobody.

        Returns:
            The observation, with the aggregator second so that it is the component asked.
        """
        producer = ObservedComponent(
            name=cls.SOURCE,
            class_path="tests.Producer",
            class_name=cls.SOURCE_CLASS,
            config=cls.config(cls.SOURCE),
            connect_automatically=False,
            default_connections={},
        )
        aggregator = ObservedComponent(
            name=cls.AGGREGATOR,
            class_path="tests.Aggregator",
            class_name="Aggregator",
            config=cls.config(cls.AGGREGATOR),
            connect_automatically=False,
            default_connections={},
            feeds=(
                ObservedFeed(
                    port_label=cls.GROWN_PORT, source_output=cls.MEASURED_OUTPUT, tags=(), weight=feed_weight
                ),
            ),
            dispatches=tuple(
                ObservedDispatch(
                    port_label=f"Dispatch{index}",
                    weight=weight,
                    source_component_class=cls.SOURCE_CLASS,
                    consumer=None,
                    target_input=None,
                )
                for index, weight in enumerate(dispatch_weights, start=1)
            ),
        )
        snapshot = WiringSnapshot(
            components=(cls.SOURCE, cls.AGGREGATOR),
            wires=(ResolvedWire(cls.AGGREGATOR, cls.GROWN_PORT, cls.SOURCE, cls.MEASURED_OUTPUT),),
            unconnected_inputs=(),
        )
        return RecordedSystem(
            setup="tests/synthetic.py",
            components=(producer, aggregator),
            wiring=snapshot,
            simulation_parameters=SimulationParameters.one_day_only(2021, 900),
        )

    @classmethod
    def items(cls, wires: Tuple[Tuple[str, str], ...], declared: Tuple[Tuple[str, str], ...]) -> Tuple[Any, ...]:
        """Builds the consumer's input items for one synthetic observation.

        Args:
            wires: The pairs actually connected.
            declared: The pairs the consumer declares as its defaults.

        Returns:
            The consumer's input items.
        """
        system = cls.system(wires, declared)
        return InputItemWriter(system).items(system.components[1])


def fingerprint(simulator: Any) -> List[Any]:
    """Renders everything about a wired simulator that an observation could possibly disturb.

    Component order, every configuration's own dump, every port's name and the source it resolved
    to, and an aggregator's grown-port bookkeeping: if reading a simulator changed any of it, the
    run afterwards would produce different numbers than the run without the reading, and the
    recorder would be measuring its own footprint.

    Args:
        simulator: The wired simulator.

    Returns:
        A plain, comparable rendering of its whole state.
    """
    rendered: List[Any] = []
    for wrapper in simulator.wrapped_components:
        component = wrapper.my_component
        rendered.append(
            (
                component.component_name,
                sorted(component.config.to_dict().items(), key=repr),
                [(port.field_name, port.src_object_name, port.src_field_name) for port in component.inputs],
                [port.field_name for port in component.outputs],
                [dataclasses.astuple(entry) for entry in getattr(component, "my_component_inputs", [])],
                [dataclasses.astuple(entry) for entry in getattr(component, "my_component_outputs", [])],
                wrapper.connect_automatically,
            )
        )
    return rendered


@pytest.fixture(name="recordings", scope="module")
def recordings_fixture(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, RecordingResult]:
    """Records the three setups once for the whole module.

    Every recording runs the setup's constructors and then builds the file it produced a second
    time, which costs real seconds; the assertions afterwards cost none. Recording into a temporary
    directory rather than over the committed twins is what lets one of the tests compare the two.

    Recording three setups in one interpreter is exactly the case ``session.py`` warns about: a
    setup mutates module state, singletons and the local LPG calculation index, so two of them in
    one process can record each other's leftovers. It is acceptable here for one reason — the
    byte comparison against the committed twins runs in this same module, so a recording carrying
    a previous setup's leftovers would fail it rather than pass quietly — and fleet-wide recording,
    where the risk is real and the twins are the output rather than the reference, is driven one
    process per setup.

    Args:
        tmp_path_factory: pytest's per-module temporary directory factory.

    Returns:
        The recordings, keyed by setup stem.
    """
    directory = tmp_path_factory.mktemp("recorded")
    results: Dict[str, RecordingResult] = {}
    for setup in Fixtures.RECORDED:
        parameters = Fixtures.parameters(directory / "results" / setup)
        results[setup] = record_setup(Fixtures.SETUPS / f"{setup}.py", parameters, directory)
    return results


@pytest.mark.base
def test_observing_a_wired_system_changes_nothing_about_it(tmp_path: Path) -> None:
    """Catches the observation writing to the very objects it is supposed to only read.

    The recorder is normally used on a simulator that is about to run, so an observation that
    sorted a list in place, appended a port or replaced a configuration would change the numbers the
    run produces — and the recording would then describe a system nobody else ever sees.
    """
    built = build_energy_system(Fixtures.MOCKUP, Fixtures.parameters(tmp_path / "results"))
    before = fingerprint(built.simulator)

    recorded = observe(built.simulator, setup="tests")

    assert fingerprint(built.simulator) == before
    assert recorded.wiring == WiringSnapshot.from_simulator(built.simulator)
    assert isinstance(recorded.components, tuple)


@pytest.mark.base
@pytest.mark.parametrize("setup", Fixtures.RECORDED)
def test_the_recorded_components_are_the_ones_the_setup_built(
    setup: str, recordings: Dict[str, RecordingResult]
) -> None:
    """Catches a component being dropped, renamed, reordered or invented on the way into the file.

    The entry's key is the component's whole identity in this format — the string every input item,
    every reference and every result column uses — so a key that is not the runtime name verbatim
    would silently rewire the system rather than describe it.
    """
    result = recordings[setup]

    assert list(result.model.components) == list(result.observed.wiring.components)
    assert not result.model.groups
    for name, observed in zip(result.model.components, result.observed.components):
        assert name == observed.name
        assert observed.config.component_id.building is None
        assert observed.config.component_id.unit is None
        assert result.model.components[name].class_path == observed.class_path


@pytest.mark.base
@pytest.mark.parametrize("setup", Fixtures.RECORDED)
def test_a_recording_states_values_and_claims_nothing_else(
    setup: str, recordings: Dict[str, RecordingResult]
) -> None:
    """Catches a recording growing an intent it cannot have observed.

    A sentinel would make the file size itself again instead of reproducing the run; a sizing source
    would claim a provenance no observation can see; a group or a variant would claim that some
    parts of the household belong together, which is a person's judgement. All four are absent by
    construction, and this is where that construction is checked rather than assumed.
    """
    result = recordings[setup]

    assert_no_sentinels(result.model)
    assert result.model.groups == {}
    assert "variants" not in result.text
    for name, entry in result.model.all_components().items():
        assert entry.sizing_sources == {}, name
        assert entry.constructor is None, name
        assert entry.preset is not None or entry.config, name


@pytest.mark.base
def test_a_preset_appears_as_a_preset_and_an_unconverted_class_as_a_full_block(
    recordings: Dict[str, RecordingResult],
) -> None:
    """Catches the two configuration branches collapsing into one.

    A class carrying preset provenance must be written as that preset plus what the setup changed,
    because that is the whole point of converting it; a class carrying none must be written out in
    full, because guessing which preset a value set came from would be an inference. A recording
    that wrote every class out in full would keep passing every other test in this module.
    """
    result = recordings["basic_household"]
    entries = result.model.all_components()
    stamped = {observed.name for observed in result.observed.components if preset_provenance(observed.config)}

    assert stamped, "the fixture no longer contains a converted class, so this test proves nothing"
    for name, entry in entries.items():
        assert (entry.preset is not None) == (name in stamped), name
        if name not in stamped:
            assert entry.config, name


@pytest.mark.base
def test_a_preset_the_setup_did_not_touch_is_written_as_the_preset_plus_what_it_left_open(
    recordings: Dict[str, RecordingResult],
) -> None:
    """Catches the sparse diff degenerating into a full dump under a preset name.

    An entry whose configuration is the preset verbatim is complete with the preset alone, and
    writing the block anyway would make every future preset change invisible in the diff — which is
    exactly what the per-batch re-recording of the conversion work is supposed to show. One field is
    the deliberate exception: the preset leaves ``weather_identity`` open because no preset can know
    which weather component a system carries, the setup realizes it before construction, and an
    override of a field the preset left open is auditable by decision — so the sparse diff writes
    exactly that field and nothing else. A second key appearing here means the diff started dumping.
    """
    entry = recordings["basic_household"].model.all_components()["Building"]

    assert entry.preset == "standard"
    assert set(entry.config) == {"weather_identity"}


@pytest.mark.base
def test_a_bare_item_is_written_only_where_the_wires_are_the_declared_defaults() -> None:
    """Catches the bare item being written from the automatic-connection flag instead of the wires.

    A setup may ask the simulator for a component's defaults and then add a wire on top. Writing the
    bare item because the flag was set would drop that wire on the next run, and the file would
    still load, still build and describe a different system.
    """
    declared = (("First", "Out1"), ("Second", "Out2"))

    exact = Synthetic.items(declared, declared)
    reduced = Synthetic.items((("First", "Out1"),), declared)
    extended = Synthetic.items(declared + (("Third", "Out3"),), declared)

    assert exact == (DefaultInputs(source=Synthetic.SOURCE),)
    assert reduced == (ExplicitWire(source=Synthetic.SOURCE, input="First", output="Out1"),)
    assert all(isinstance(item, ExplicitWire) for item in extended)
    assert len(extended) == 3


@pytest.mark.base
def test_an_aggregator_feed_carries_its_tags_weight_kind_and_back_channel(
    recordings: Dict[str, RecordingResult], tmp_path: Path
) -> None:
    """Catches a feed losing what the aggregator ranks it by, or its control signal.

    A feed is the one item whose meaning is entirely in its qualifiers: the tags choose the channel,
    the weight the rank, the component type the participant's kind and the dispatch block the
    back-channel. The derived port names must not be written — they are the format's own — so the
    check that they are right is that building the file creates exactly them.
    """
    result = recordings["dynamic_components"]
    feeds = result.model.all_components()["L2EMSElectricityController"].inputs
    battery = next(item for item in feeds if item.source == "Battery1")

    assert isinstance(battery, AggregatorFeed)
    assert battery.output == "AcBatteryPowerUsed"
    assert battery.component_type == "BATTERY"
    assert battery.tags == ("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",)
    assert battery.weight == 1
    assert battery.dispatch is not None and battery.dispatch.target_input == "LoadingPowerInput"

    built = build_energy_system(result.path, Fixtures.parameters(tmp_path / "results"))
    aggregator = next(
        wrapper.my_component
        for wrapper in built.simulator.wrapped_components
        if wrapper.my_component.component_name == "L2EMSElectricityController"
    )
    ports = {port.field_name for port in aggregator.inputs} | {port.field_name for port in aggregator.outputs}
    expected_input = ResolvedDynamicConnection.AGGREGATOR_INPUT_TEMPLATE.format(
        source_output=battery.output, source_name=battery.source
    )
    expected_output = ResolvedDynamicConnection.DISPATCH_OUTPUT_TEMPLATE.format(
        source_name=battery.source, target_input=battery.dispatch.target_input
    )
    assert {expected_input, expected_output} <= ports


@pytest.mark.base
@pytest.mark.parametrize("setup", Fixtures.RECORDED)
def test_a_recorded_file_is_written_in_the_one_canonical_style(
    setup: str, recordings: Dict[str, RecordingResult]
) -> None:
    """Catches the recorder writing a file the format's own writer would write differently.

    The rule of this format is that re-emitting a file reproduces it, and a generated file has no
    excuse for being the exception. It holds of the body: the two comment lines above it name what
    produced the file, and no YAML writer emits comments.
    """
    result = recordings[setup]
    header, body = RecordedFileWriter.split(result.text)

    assert header.count("\n") == 2
    assert dump_energy_system(load_energy_system(result.path)) == body


@pytest.mark.base
@pytest.mark.parametrize("setup", Fixtures.RECORDED)
def test_the_committed_twin_is_what_recording_produces_today(
    setup: str, recordings: Dict[str, RecordingResult]
) -> None:
    """Catches a committed twin drifting away from the setup it is the twin of.

    This is the freshness rule in miniature, and it is also the determinism rule: the comparison is
    of bytes, so a mapping whose order depended on a hash, a float written through a format string
    or a timestamp anywhere in the file would fail it.
    """
    _, body = RecordedFileWriter.split(recordings[setup].text)
    _, committed = RecordedFileWriter.split(Fixtures.committed(setup).read_text(encoding="utf-8"))

    assert body == committed, f"re-record {setup} with 'hisim energy-system record'"


@pytest.mark.base
def test_recording_the_same_setup_twice_produces_the_same_bytes(tmp_path: Path) -> None:
    """Catches non-determinism that a single recording cannot show.

    Set iteration, dictionary order under a different hash seed and a float rendered through a
    format string all produce a stable file within one process and an unstable one across two. The
    freshness job compares across machines, so the cheapest approximation of it is two recordings.
    """
    setup = Fixtures.SETUPS / "dynamic_components.py"
    first = record_setup(setup, Fixtures.parameters(tmp_path / "one" / "results"), tmp_path / "one")
    second = record_setup(setup, Fixtures.parameters(tmp_path / "two" / "results"), tmp_path / "two")

    assert RecordedFileWriter.split(first.text)[1] == RecordedFileWriter.split(second.text)[1]
    for value in first.model.all_components()["Battery1"].config.values():
        if isinstance(value, float):
            assert float(repr(value)) == value


@pytest.mark.base
def test_the_realized_record_of_a_recording_re_executes_unchanged(
    recordings: Dict[str, RecordingResult], tmp_path: Path
) -> None:
    """Catches a recorded file that builds but does not reproduce itself.

    Building a file proves it is legal; re-executing its own realized record proves it decides
    nothing on the second run. A recording that left one field for the sizing kernel would pass
    every other test here and quietly produce a different number a year later.

    The production ``EF-61`` check inside ``write_records`` already compares the re-run against
    the record it was handed, but a test that relies on it alone asserts nothing of its own and
    would keep passing if that check were weakened or skipped. So the two written records are
    compared here as well, on the part that decides reproduction: the component entries, with
    every value the second run realized. The two ``source_`` metadata keys are excluded, because
    the second run legitimately started from a different file than the first.
    """
    built = build_energy_system(recordings["basic_household"].path, Fixtures.parameters(tmp_path / "results"))
    record_path, _, _ = write_records(built, str(tmp_path / "record"))

    rerun = build_energy_system(Path(record_path), Fixtures.parameters(tmp_path / "again"), rerun=True)
    rerun_path, _, _ = write_records(rerun, str(tmp_path / "record-again"))

    first = load_energy_system(Path(record_path))
    second = load_energy_system(Path(rerun_path))
    assert second.all_components() == first.all_components()
    assert RunMetadata.without_sources(second.metadata) == RunMetadata.without_sources(first.metadata)


@pytest.mark.base
def test_a_port_name_that_cannot_be_referenced_is_refused_rather_than_written() -> None:
    """Catches an unwritable name being sanitized into something the runtime does not answer to.

    An input item names the producing port as the dotted half of a reference, and the format's
    reference grammar accepts identifiers only. Rewriting such a name inside the recorder would make
    the file's ports and the run's result columns disagree, which is precisely what the migration's
    parity comparison exists to detect.
    """
    with pytest.raises(EnergySystemRecordingError) as failure:
        Synthetic.items((("Target", "Random Numbers"),), ())

    assert failure.value.error_id.value == "EF-R1"
    assert "Random Numbers" in str(failure.value)


@pytest.mark.base
def test_a_component_carrying_a_building_identity_is_refused() -> None:
    """Catches a district system being recorded into a file that cannot express it.

    An entry's key is rebuilt into a plain ``ComponentID`` with no building and no unit, so a
    component that had one would come back a different component — and the electricity meter
    branches on exactly that field. Refusing is what turns a silent behaviour change into a stop.
    """
    system = Synthetic.system((), ())
    qualified = dataclasses.replace(
        system.components[0], config=ConfigBase(component_id=ComponentID(name="Meter", building="BUI2"))
    )
    system = dataclasses.replace(system, components=(qualified,) + system.components[1:])

    with pytest.raises(EnergySystemRecordingError) as failure:
        build(system, "synthetic")

    assert failure.value.error_id.value == "EF-R2"
    assert "BUI2" in str(failure.value)


@pytest.mark.base
def test_two_control_outputs_the_weight_cannot_tell_apart_are_refused_rather_than_guessed() -> None:
    """Catches an arbitrary pairing wiring one participant's control signal to another.

    An aggregator that grew two control outputs for one participant class names neither of their
    consumers when nobody reads them, so the feed's weight is the only thing left to tell them
    apart. Where it does not, writing whichever came first would produce a twin that loads, builds
    and controls the wrong device, and writing no back-channel at all would turn a controlled
    participant into a measured one. Both are silent, so the recording stops instead.
    """
    system = Synthetic.aggregator(feed_weight=1, dispatch_weights=(1, 1))

    with pytest.raises(EnergySystemRecordingError) as failure:
        InputItemWriter(system)

    assert failure.value.error_id.value == "EF-R11"
    assert Synthetic.AGGREGATOR in str(failure.value)
    assert Synthetic.SOURCE in str(failure.value)


@pytest.mark.base
def test_a_feed_with_no_control_output_at_all_is_recorded_as_a_measured_participant() -> None:
    """Catches the ambiguity refusal swallowing the participant an aggregator only measures.

    Nothing to pair is not the same as too much to pair: an aggregator that publishes no control
    output for a participant really does only measure it, whatever the feed's weight says, and the
    format spells that as a feed without a dispatch block. Refusing it would make every such system
    unrecordable.
    """
    system = Synthetic.aggregator(feed_weight=1, dispatch_weights=())

    items = InputItemWriter(system).items(system.components[1])

    assert len(items) == 1
    feed = items[0]
    assert isinstance(feed, AggregatorFeed)
    assert feed.source == Synthetic.SOURCE
    assert feed.weight == 1
    assert feed.dispatch is None


@pytest.mark.base
def test_an_absolute_path_under_a_path_named_key_is_refused_naming_the_setup_and_component() -> None:
    """Catches the recorder writing a file that resolves on the machine that produced it alone.

    Symbolisation turns every path below a registered root into its ``${var}`` spelling, so a value
    still absolute afterwards lies below no root and would make the twin unusable everywhere else.
    The guard runs the structural validator's own scan, so this also catches the two coming to
    disagree about which keys name a location; what it adds is the setup and the component, which
    a person re-recording a fleet needs and a load-time failure cannot give.
    """
    with pytest.raises(EnergySystemRecordingError) as failure:
        PortablePathGuard.check(
            {"nested": {"cache_directory": "/scratch/somewhere"}}, "Battery", "tests/synthetic.py"
        )

    assert failure.value.error_id.value == "EF-R3"
    assert "tests/synthetic.py" in str(failure.value)
    assert "Battery" in str(failure.value)
    assert "/scratch/somewhere" in str(failure.value)


@pytest.mark.base
def test_a_recording_refuses_to_overwrite_a_file_it_did_not_write(tmp_path: Path) -> None:
    """Catches a hand-authored energy system being destroyed by a recording that shares its stem.

    Re-recording a twin over its predecessor has to keep working, because that is how the fleet is
    refreshed, and the recorder's own header line is what tells the two cases apart. A file without
    it is somebody's own system, and it cannot be brought back by re-recording anything.
    """
    session = RecordingSession(Fixtures.SETUPS / "basic_household.py", tmp_path)
    session.path.parent.mkdir(parents=True, exist_ok=True)
    session.path.write_text("# a system somebody wrote\nschema_version: 1\n", encoding="utf-8")

    with pytest.raises(EnergySystemRecordingError) as failure:
        session.refuse_to_overwrite_a_hand_authored_file()

    assert failure.value.error_id.value == "EF-R12"
    assert str(session.path) in str(failure.value)

    session.path.write_text(
        RecordedFileWriter.header("system_setups/basic_household.py", "p.simulation.yaml", tmp_path),
        encoding="utf-8",
    )
    session.refuse_to_overwrite_a_hand_authored_file()


@pytest.mark.base
def test_a_written_parameter_file_reads_its_country_back_as_the_string_it_was(tmp_path: Path) -> None:
    """Catches a string scalar being written bare and coming back as a different type.

    YAML resolves an unquoted scalar by its spelling, so a country of ``none`` comes back as
    ``None`` and one of ``2022`` as an integer. The recorder compares a run's normalised parameters
    against those of every file beside it to decide whether it may share one, and a file that does
    not read back as what was written would never match itself: every recording would write another
    copy of the same parameters.
    """
    for country in ("none", "DE"):
        parameters = SimulationParameters.one_day_only(2021, 900)
        parameters.country = country
        normalised = ParameterNormalisation.normalise(parameters)
        path = tmp_path / f"{country}.simulation.yaml"
        path.write_text(ParameterFileWriter.text(normalised), encoding="utf-8")

        assert yaml.safe_load(path.read_text(encoding="utf-8"))["country"] == country
        assert ParameterFileLibrary.read(path) == normalised


@pytest.mark.base
def test_the_command_line_records_a_setup_and_defaults_to_the_shipped_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Catches the verb being unreachable, or writing its twins somewhere nobody looks for them.

    The recorder is only useful as a command, and the default output directory is part of the
    contract: a twin belongs beside the exemplar and the shared parameter files, because that is
    where every consumer of this format — the freshness job, the golden runs, a person — looks.
    """
    code = main(
        [
            "energy-system",
            "record",
            str(Fixtures.SETUPS / "dynamic_components.py"),
            str(Fixtures.PARAMETERS),
            "--out",
            str(tmp_path),
        ]
    )

    written = tmp_path / f"dynamic_components{RecordedFileWriter.SUFFIX}"
    assert code == ExitCodes.OK
    assert written.exists()
    assert str(written) in capsys.readouterr().out
    assert RecordingSession.default_output_directory(Fixtures.SETUPS / "any.py") == Fixtures.ENERGY_SYSTEMS
