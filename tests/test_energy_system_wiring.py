"""Tests for the wiring stage: components built from a file and connected the way it says.

Every rule here needs the components in memory rather than only their classes, because HiSim
creates a component's ports inside its constructor: whether ``TemperatureOutside`` exists on a
building, and whether it carries degrees or watts, is a property of the object a configuration
produced. That is why these checks cannot live with the class-bound validation, and why a test
of one of them has to build a small but real system.

There is one test per rejection, each built from the smallest file that can trigger it, and each
asserting the catalogue identifier plus the names of both ends in the message. Alongside them
sit the three positive cases the identifiers only make sense against: a bare item expanding
through the consumer's declared defaults, an explicit wire naming both ports itself, and an
aggregator feed growing a derived input on a meter.

Only converted classes appear, because a fixture built on a class without presets would fail for
the wrong reason. Every fixture is deliberately tiny — a weather and a building, an occupancy
and a meter — so a failure points at the rule under test rather than at a household.

Each test states the failure mode it catches.
"""

# clean

from pathlib import Path
from typing import ClassVar, Tuple, cast

import pytest

from hisim import loadtypes as lt
from hisim.components.electricity_meter import ElectricityMeter, ElectricityMeterConfig
from hisim.dynamic_component import DynamicComponent
from hisim.components.loadprofilegenerator_utsp_connector import (
    UtspLpgConnector,
    UtspLpgConnectorConfig,
)
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import (
    EnergySystemBindingError,
    EnergySystemFormatError,
    EnergySystemWiringError,
)
from hisim.energy_system.executor import EnergySystemExecutor
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.wiring import WiredSystem
from hisim.simulationparameters import SimulationParameters


class Systems:
    """Renders the small energy systems the wiring rules are exercised on.

    Every test needs a system that is well formed, configures cleanly and is wrong in exactly one
    way the wiring stage decides, so the helpers here assemble one out of a handful of fixed
    blocks. The blocks are the cheapest converted components that still have real ports: a
    weather station, the building that reads it, an occupancy profile and the meter that measures
    it.
    """

    #: A weather station, which needs nothing and provides the building's mandatory inputs.
    WEATHER: ClassVar[str] = """  weather:
    class: hisim.components.weather.Weather
    preset: standard
"""

    #: A second weather station, for the one rule that needs two sources of the same port.
    OTHER_WEATHER: ClassVar[str] = """  other_weather:
    class: hisim.components.weather.Weather
    preset: standard
"""

    #: A building taking the weather through its declared defaults. Its remaining inputs — the
    #: occupancy's heat gains, the heat distribution's delivered power — are optional, so this
    #: pair is a complete, wireable system on its own.
    BUILDING: ClassVar[str] = """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - weather
"""

    #: An occupancy profile, the household's only electrical consumer and the meter's one
    #: participant.
    OCCUPANCY: ClassVar[str] = """  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
"""

    @classmethod
    def meter(cls, items: str = "") -> str:
        """Renders an electricity meter, optionally with a block of input items.

        Args:
            items: The body of the meter's ``inputs`` list, indented by six spaces.

        Returns:
            The entry text.
        """
        entry = """  meter:
    class: hisim.components.electricity_meter.ElectricityMeter
    preset: standard
"""
        return entry + ("    inputs:\n" + items if items else "")

    @classmethod
    def build(cls, entries: str, result_directory: Path) -> WiredSystem:
        """Builds and wires one inline energy system made of the given entries.

        Args:
            entries: The body of the ``components`` block, indented by two spaces.
            result_directory: Where the simulator should put its results, so that a test
                leaves nothing behind in the repository.

        Returns:
            The wired system.

        Raises:
            EnergySystemError: Whatever the file under test provokes.
        """
        text = f"schema_version: 3\nname: wiring rule under test\ncomponents:\n{entries}"
        model = EnergySystemReader.build(RawDocument.parse_text(text, "inline"), "inline")
        parameters = SimulationParameters.one_day_only(2021, 900)
        parameters.result_directory = str(result_directory)
        return EnergySystemExecutor(model, parameters).build().wired

    @classmethod
    def wire_names(cls, wired: WiredSystem) -> Tuple[str, ...]:
        """Renders the applied wires as ``source.Output -> target.Input`` strings.

        Args:
            wired: The wired system.

        Returns:
            One string per wire, in the order the wires were applied.
        """
        return tuple(
            f"{wire.source_name}.{wire.source_output} -> {wire.target_name}.{wire.target_input}"
            for wire in wired.wires
        )


@pytest.mark.base
def test_a_bare_item_expands_through_the_consumers_declared_defaults(tmp_path: Path) -> None:
    """Catches a bare item that wires nothing, or wires something other than the defaults.

    The bare spelling is the whole point of the format's brevity: a building says it takes the
    weather and gets eight ports. If the expansion silently produced fewer wires, the building
    would run on default-initialised irradiance and still produce a plausible-looking year.
    """
    wired = Systems.build(Systems.WEATHER + Systems.BUILDING, tmp_path)

    names = Systems.wire_names(wired)
    assert "weather.TemperatureOutside -> building.TemperatureOutside" in names
    assert "weather.GlobalHorizontalIrradiance -> building.GlobalHorizontalIrradiance" in names
    assert all(wire.source_name == "weather" for wire in wired.wires)


@pytest.mark.base
def test_an_explicit_wire_connects_exactly_the_two_ports_it_names(tmp_path: Path) -> None:
    """Catches an explicit wire being ignored, or being expanded into the defaults instead.

    An explicit wire exists for the connections the defaults do not cover, so it must connect
    the pair it names and nothing else; expanding it would silently add ports the author chose
    not to connect.
    """
    entries = (
        Systems.WEATHER
        + Systems.BUILDING
        + """  hds_controller:
    class: hisim.components.heat_distribution_system.HeatDistributionController
    preset: standard
    inputs:
      - building
      - input: DailyAverageOutsideTemperature
        from: weather.DailyAverageOutsideTemperatures
"""
    )

    wired = Systems.build(entries, tmp_path)

    names = Systems.wire_names(wired)
    assert (
        "weather.DailyAverageOutsideTemperatures -> hds_controller.DailyAverageOutsideTemperature"
        in names
    )


@pytest.mark.base
def test_a_feed_grows_a_derived_input_on_the_aggregator(tmp_path: Path) -> None:
    """Catches a feed that creates no port, or one whose name is not the derived one.

    A derived port name ends up in result files and in every lookup built on them, so it is wire
    format in its own right: if the template changed, a stored analysis would silently address a
    column that no longer exists.
    """
    entries = Systems.OCCUPANCY + Systems.meter(
        """      - from: occupancy
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
"""
    )

    wired = Systems.build(entries, tmp_path)

    assert Systems.wire_names(wired) == (
        "occupancy.ElectricalPowerConsumption -> meter.ElectricalPowerConsumptionFromoccupancy",
    )
    meter = wired.component_of("meter")
    assert "ElectricalPowerConsumptionFromoccupancy" in {port.field_name for port in meter.inputs}
    assert wired.resolved_feeds[0][0] == "meter"
    assert wired.resolved_feeds[0][1][0].weight == FeedRequest.MONITORED_ONLY_WEIGHT


@pytest.mark.base
def test_a_dispatch_block_adopts_a_signal_the_aggregator_already_publishes(tmp_path: Path) -> None:
    """Catches a second control output being grown beside the one the aggregator already has.

    An aggregator finds the output it steers a participant through by tags and weight, never by
    name, so two outputs carrying the same tags at the same weight are not a name collision that
    anything would refuse — they are one extra entry in the list the aggregator zips against its
    participants, and every participant after it is steered through somebody else's port. The
    energy management system publishes a residents' target from its own constructor, so a feed
    describing that participant has to adopt it rather than ask for a second one; if it did not,
    the household would run with the battery ranked last and never charged.

    The dispatch block is still required — the channel dispatches to every participant on it — so
    what the test pins is that the block is *served* by the existing port and that the resolved
    tags are the ones the runtime lookup searches for.
    """
    entries = (
        Systems.OCCUPANCY
        + """  ems:
    class: hisim.components.controller_l2_energy_management_system.L2GenericEnergyManagementSystem
    preset: optimize_own_consumption
    inputs:
      - from: occupancy.ElectricalPowerConsumption
        component_type: RESIDENTS
        tags: [ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        weight: 1
        dispatch: {}
"""
    )

    wired = Systems.build(entries, tmp_path)

    ems = wired.component_of("ems")
    assert "ElectricalPowerConsumptionFromoccupancy" in {port.field_name for port in ems.inputs}
    assert "DispatchForoccupancy_ElectricalPowerConsumption" not in {
        port.field_name for port in ems.outputs
    }
    resolved = wired.resolved_feeds[0][1][0]
    assert resolved.dispatch is not None
    assert lt.InandOutputType.ELECTRICITY_TARGET in resolved.dispatch.tags
    assert lt.ComponentType.RESIDENTS in resolved.dispatch.tags
    assert resolved.adopted_dispatch_output == "ElectricityToOrFromGridOfUtspLpgConnector_Output8"
    assert resolved.dispatch_output_name == resolved.adopted_dispatch_output
    assert resolved.created_dispatch_output_name is None
    served = [
        entry
        for entry in cast(DynamicComponent, ems).my_component_outputs
        if entry.source_weight == 1
        and lt.InandOutputType.ELECTRICITY_TARGET in entry.source_tags
        and lt.ComponentType.RESIDENTS in entry.source_tags
    ]
    assert len(served) == 1, "the aggregator must publish exactly one signal per participant"


@pytest.mark.base
def test_a_bare_item_whose_consumer_declares_no_defaults_is_rejected(tmp_path: Path) -> None:
    """Catches a bare item silently wiring nothing when the consumer has no defaults to expand.

    Without the rejection the item would read as a connection and make none, which is the
    quietest possible way for a household to run on an unconnected input.
    """
    entries = (
        Systems.WEATHER
        + """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - weather
      - meter
"""
        + Systems.meter()
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-23"
    assert "building" in str(failure.value) and "meter" in str(failure.value)
    assert "ElectricityMeter" in str(failure.value)


@pytest.mark.base
def test_a_wire_naming_an_output_the_source_does_not_have_is_rejected(tmp_path: Path) -> None:
    """Catches a renamed or misspelled output being wired to nothing.

    HiSim would otherwise raise deep inside the connection machinery without saying which item
    of which file named the port, which is exactly the debugging experience this format removes.
    """
    entries = (
        Systems.WEATHER
        + """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - input: TemperatureOutside
        from: weather.TemperatureOutsid
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-21"
    assert "TemperatureOutsid" in str(failure.value)
    assert "Did you mean: TemperatureOutside" in str(failure.value)


@pytest.mark.base
def test_a_wire_naming_an_input_the_consumer_does_not_have_is_rejected(tmp_path: Path) -> None:
    """Catches a wire into a port that a configuration switched off, or that never existed.

    The ports a component has depend on its configuration, so this is not decidable from the
    class alone and has to be caught here, listing the ports the built component does declare.
    """
    entries = (
        Systems.WEATHER
        + """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - input: TemperatureOutsideAir
        from: weather.TemperatureOutside
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-22"
    assert "TemperatureOutsideAir" in str(failure.value)
    assert "Valid inputs:" in str(failure.value)


@pytest.mark.base
def test_a_wire_whose_ends_carry_different_quantities_is_rejected(tmp_path: Path) -> None:
    """Catches a physically meaningless connection that would otherwise simulate happily.

    Feeding a temperature into a port expecting an angle produces numbers rather than a crash,
    so nothing downstream would ever notice; the type declarations exist precisely so that this
    is decidable before the first timestep.
    """
    entries = (
        Systems.WEATHER
        + """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - input: Altitude
        from: weather.TemperatureOutside
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-30"
    assert "weather.TemperatureOutside" in str(failure.value)
    assert "building.Altitude" in str(failure.value)


@pytest.mark.base
def test_an_input_fed_by_two_sources_is_rejected(tmp_path: Path) -> None:
    """Catches two items feeding one input, where the file cannot say which one wins.

    The duplicate is invisible in the file itself — one wire is written, the other comes out of
    a bare item's expansion — so only the assembled plan can see it, and the older executor
    silently let the last wire win.
    """
    entries = (
        Systems.WEATHER
        + Systems.OTHER_WEATHER
        + """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - weather
      - input: TemperatureOutside
        from: other_weather.TemperatureOutside
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-26"
    assert "building.TemperatureOutside" in str(failure.value)
    assert "other_weather" in str(failure.value) and "weather.TemperatureOutside" in str(failure.value)


@pytest.mark.base
def test_a_mandatory_input_nothing_feeds_is_rejected(tmp_path: Path) -> None:
    """Catches a half-wired system starting and producing numbers from an unconnected port.

    This is the failure mode the whole format exists to remove: an unfed mandatory input reads
    as zero, and a zero outside temperature is a perfectly plausible number.
    """
    entries = (
        Systems.WEATHER
        + Systems.BUILDING
        + """  hds_controller:
    class: hisim.components.heat_distribution_system.HeatDistributionController
    preset: standard
    inputs:
      - building
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-31"
    assert "hds_controller.DailyAverageOutsideTemperature" in str(failure.value)


@pytest.mark.base
def test_a_feed_addressed_at_a_component_that_aggregates_nothing_is_rejected(tmp_path: Path) -> None:
    """Catches a feed at an ordinary component, which has no channel to classify it with.

    An ordinary component has one declared port per source; handing it a tagged flow cannot mean
    anything, and reporting it as "no matching channel" would send the author looking for a tag
    problem that does not exist.
    """
    entries = (
        Systems.WEATHER
        + Systems.OCCUPANCY
        + """  building:
    class: hisim.components.building.Building
    preset: standard
    inputs:
      - weather
      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-27"
    assert "building" in str(failure.value) and "not an aggregator" in str(failure.value)


@pytest.mark.base
def test_a_feed_whose_tags_match_no_channel_is_rejected(tmp_path: Path) -> None:
    """Catches a participant that wires cleanly and is then never summed.

    A tag set no query matches produces a port the aggregator never reads, which shows up as a
    grid balance that is wrong by exactly one participant and looks entirely plausible.
    """
    entries = Systems.OCCUPANCY + Systems.meter(
        """      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_TARGET]
        weight: 999
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-28"
    assert "ELECTRICITY_TARGET" in str(failure.value)
    assert "Valid channels:" in str(failure.value)


@pytest.mark.base
def test_a_measured_participant_carrying_a_dispatch_rank_is_rejected(tmp_path: Path) -> None:
    """Catches a participant entering a ranking on a channel the aggregator never serves.

    The meter measures and never controls, so a participant on one of its channels must carry
    the reserved monitored-only weight; a real rank would promise a signal back that nothing
    can ever send.
    """
    entries = Systems.OCCUPANCY + Systems.meter(
        """      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 3
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-29"
    assert "999" in str(failure.value)
    assert "consumption_uncontrolled" in str(failure.value)


@pytest.mark.base
def test_a_feed_naming_a_tag_no_vocabulary_knows_is_rejected(tmp_path: Path) -> None:
    """Catches a misspelled tag being carried into matching, where it would look like a mismatch.

    Reporting the typo where it happens, with the vocabulary listed, is the difference between a
    one-line fix and a hunt through an aggregator's channel declaration.
    """
    entries = Systems.OCCUPANCY + Systems.meter(
        """      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLD]
        weight: 999
"""
    )

    with pytest.raises(EnergySystemBindingError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-2A"
    assert "ELECTRICITY_CONSUMPTION_UNCONTROLLD" in str(failure.value)
    assert "Valid flow tags:" in str(failure.value)


@pytest.mark.base
def test_one_participant_port_measured_twice_by_one_aggregator_is_rejected(tmp_path: Path) -> None:
    """Catches a flow counted twice, which doubles a household's consumption silently.

    The duplicate only appears after expansion — one feed is written, the other comes out of the
    meter's own declared default for that class — so the file itself looks fine.
    """
    entries = Systems.OCCUPANCY + Systems.meter(
        """      - occupancy
      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
"""
    )

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-25"
    assert "occupancy.ElectricalPowerConsumption" in str(failure.value)
    assert "twice" in str(failure.value)


@pytest.mark.base
def test_mixing_wires_and_feeds_between_one_pair_is_rejected(tmp_path: Path) -> None:
    """Catches one relationship being described twice, in two spellings that need not agree.

    A pair of components is connected in one spelling; an explicit wire and a feed between the
    same two would each claim to be the whole relationship. The rule is decidable from the file
    alone, so it is caught before anything is built — which is asserted here, because the pair
    is a wiring concern and a reader would otherwise look for it in this stage.
    """
    entries = Systems.OCCUPANCY + Systems.meter(
        """      - from: occupancy.ElectricalPowerConsumption
        tags: [ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        weight: 999
      - input: ElectricalPowerConsumptionFromoccupancy
        from: occupancy.ElectricalPowerConsumption
"""
    )

    with pytest.raises(EnergySystemFormatError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-24"
    assert "meter" in str(failure.value) and "occupancy" in str(failure.value)


@pytest.mark.base
def test_a_component_whose_constructor_refuses_its_configuration_is_rejected(tmp_path: Path) -> None:
    """Catches a construction failure surfacing as a bare traceback out of a component module.

    A configuration can be complete, correctly typed and still impossible — a load profile that
    does not exist, a device the catalogue has never heard of — and the author needs to be told
    which entry of which file caused it.
    """
    entries = """  occupancy:
    class: hisim.components.loadprofilegenerator_utsp_connector.UtspLpgConnector
    preset: standard
    config:
      name_of_predefined_loadprofile: NoSuchHousehold
"""

    with pytest.raises(EnergySystemWiringError) as failure:
        Systems.build(entries, tmp_path)

    assert failure.value.error_id.value == "EF-33"
    assert "occupancy" in str(failure.value)
    assert "NoSuchHousehold" in str(failure.value)


@pytest.mark.base
def test_a_derived_port_name_colliding_with_an_existing_one_is_rejected() -> None:
    """Catches a derived port silently overwriting or shadowing a declared one.

    Derived names carry no counter — that is what makes them stable across runs and quotable in
    a stored analysis — so a collision has nowhere to go and must abort the build rather than be
    disambiguated. The collision is provoked directly on the resolver, because no realistic file
    produces one.
    """
    parameters = SimulationParameters.one_day_only(2021, 900)
    occupancy = UtspLpgConnector(
        my_simulation_parameters=parameters,
        config=UtspLpgConnectorConfig.preset_standard("occupancy"),
    )
    meter = ElectricityMeter(
        my_simulation_parameters=parameters,
        config=ElectricityMeterConfig.preset_standard("meter"),
    )
    meter.add_output(
        object_name=meter.component_name,
        field_name="ElectricalPowerConsumptionFromoccupancy",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        output_description="A port that occupies the name the derived one would take.",
    )
    feed = FeedRequest(
        consumer="meter",
        source="occupancy",
        output=UtspLpgConnector.ElectricalPowerConsumption,
        component_type=None,
        flow_tags=(lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,),
        weight=FeedRequest.MONITORED_ONLY_WEIGHT,
    )

    resolver = DynamicConnectionResolver({"occupancy": occupancy, "meter": meter})
    with pytest.raises(EnergySystemWiringError) as failure:
        resolver.resolve_target("meter", [feed])

    assert failure.value.error_id.value == "EF-32"
    assert "ElectricalPowerConsumptionFromoccupancy" in str(failure.value)
