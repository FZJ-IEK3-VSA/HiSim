"""The three meters' channel declarations, and the wildcard that makes them work.

An aggregator's ``CHANNELS`` tuple is the energy-system file format's contract for that
component: it is what an author's feed is matched against, and it is the only thing standing
between a written feed and an input the aggregator will sum for a whole simulated year. The fuel
and gas meters declare theirs with a wildcard load type, because the carrier of the flow they
measure is a property of the household rather than of the meter, and that relaxation is precisely
where a wrong unit or a mis-priced carrier could slip through unnoticed.

Both halves are pinned here. The first half is the port-type predicate itself — one class now,
shared by the channel matcher and the port-to-port wire checks — asserted directly on the three
meters' declarations. The second half is the carrier cross-check the fuel and gas meters run over
the feeds a file hands them, which is the instance-level answer to a class-level channel that
cannot know which fuel the meter was configured to price.

The matcher and resolver tests drive :class:`ChannelMatcher` and
:class:`DynamicConnectionResolver` directly rather than through a document, the same route the
signal-collision tests of ``test_energy_system_dispatch`` take, because the arrangements they
provoke need a participant no shipped energy-system file wires yet.
"""

# clean

from dataclasses import dataclass
from typing import ClassVar, List, Optional

import pytest
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentOutput, SingleTimeStepValues
from hisim.components.fuel_meter import FuelMeter, FuelMeterConfig
from hisim.components.gas_meter import GasMeter, GasMeterConfig
from hisim.components.heating_meter import HeatingMeter
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.config.channels import PortTypeCompatibility
from hisim.energy_system.channel_matching import ChannelMatcher
from hisim.energy_system.channels import FeedRequest
from hisim.energy_system.errors import EnergySystemWiringError
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class HeatSourceStubConfig(ConfigBase):
    """Configuration of the one-output heat source the resolver tests feed into a meter.

    It carries nothing but the identity and the carrier its single output is typed with, because
    the carrier is the only thing these tests vary: the whole point of the arrangement is to hand
    a meter a feed whose load type does or does not match what the meter prices.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns the full class name of the base class."""
        return HeatSourceStub.get_full_classname()

    component_id: ComponentID
    carrier: lt.LoadTypes


class HeatSourceStub(Component):
    """A heat source with one energy output, typed with whatever carrier the test asks for.

    A real boiler would do here, but only at the price of a controller, a storage and a building
    to size it against, and none of that is what is under test. This stub publishes exactly the
    port shape a fuel or gas meter's consumption channel expects — an energy output in watt-hours
    — and lets the test choose its load type, which is the single variable the carrier
    cross-check reads.

    It models no device, so a setup built from it can still be asked for costs; nothing here ever
    runs a timestep, but the lifecycle methods are implemented because the base class requires
    them.
    """

    #: A generator with nothing to buy and nothing to run; see ``Component.MODELS_NO_DEVICE``.
    MODELS_NO_DEVICE: ClassVar[bool] = True

    #: The one output, named after the energy-demand outputs a real boiler publishes.
    EnergyDemand: ClassVar[str] = "EnergyDemand"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: HeatSourceStubConfig,
        my_display_config: Optional[DisplayConfig] = None,
    ) -> None:
        """Builds the stub and publishes its single output.

        Args:
            my_simulation_parameters: Simulation parameters of the run.
            config: Identity and the carrier the output is typed with.
            my_display_config: Display configuration; a default one is made when omitted.
        """
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.config: HeatSourceStubConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        super().__init__(
            name=self.get_component_name(),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )
        self.energy_demand_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.EnergyDemand,
            config.carrier,
            lt.Units.WATT_HOUR,
            output_description="The energy this source consumed, typed by its carrier.",
        )

    def i_save_state(self) -> None:
        """Saves the state; the stub has none."""

    def i_restore_state(self) -> None:
        """Restores the state; the stub has none."""

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation; nothing to prepare."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks the timestep; nothing to check."""

    def i_simulate(
        self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Writes a constant so the port has a value; no test ever runs a timestep."""
        stsv.set_output_value(self.energy_demand_channel, 0.0)

    def write_to_report(self) -> List[str]:
        """Writes the configuration to the report.

        Returns:
            One formatted line per configuration field.
        """
        return self.config.get_string_dict()


class MeterFixtures:
    """Builds the meters, the sources and the feeds the tests below vary one field at a time.

    Every arrangement here is the same shape — one heat source, one meter, one monitored-only
    feed carrying the meter's own flow tag — so that each test differs from the next in exactly
    the value it is about: the source's carrier, the source's unit, or the meter's configured
    fuel. Sharing the construction keeps the tests about the rule rather than about the fixture.
    """

    #: Weight every feed here carries. A meter's channels forbid dispatch, so its participants
    #: must use the reserved monitored-only weight.
    MONITORED_ONLY: ClassVar[int] = FeedRequest.MONITORED_ONLY_WEIGHT

    #: Name the meter is registered under in the resolver's index and named by in messages.
    METER_NAME: ClassVar[str] = "meter"

    #: Name the heat source is registered under.
    SOURCE_NAME: ClassVar[str] = "heat_source"

    @classmethod
    def parameters(cls) -> SimulationParameters:
        """Builds the shortest simulation the components will accept.

        Returns:
            One day at a quarter-hour resolution.
        """
        return SimulationParameters.one_day_only(2021, 900)

    @classmethod
    def fuel_meter(cls, fuel: lt.LoadTypes = lt.LoadTypes.OIL) -> FuelMeter:
        """Builds a fuel meter configured for one carrier.

        Args:
            fuel: The carrier the meter measures and prices.

        Returns:
            The meter.
        """
        return FuelMeter(
            my_simulation_parameters=cls.parameters(),
            config=FuelMeterConfig.get_fuel_meter_default_config(
                component_id=ComponentID(name=cls.METER_NAME), fuel_loadtype=fuel
            ),
        )

    @classmethod
    def gas_meter(cls, gas: lt.LoadTypes = lt.LoadTypes.GAS) -> GasMeter:
        """Builds a gas meter configured for one carrier.

        Args:
            gas: The gas the meter measures and prices.

        Returns:
            The meter.
        """
        return GasMeter(
            my_simulation_parameters=cls.parameters(),
            config=GasMeterConfig.get_gas_meter_default_config(
                component_id=ComponentID(name=cls.METER_NAME), gas_loadtype=gas
            ),
        )

    @classmethod
    def heat_source(cls, carrier: lt.LoadTypes) -> HeatSourceStub:
        """Builds the stub source with its output typed by one carrier.

        Args:
            carrier: Load type of the stub's single output.

        Returns:
            The source.
        """
        return HeatSourceStub(
            my_simulation_parameters=cls.parameters(),
            config=HeatSourceStubConfig(
                component_id=ComponentID(name=cls.SOURCE_NAME), carrier=carrier
            ),
        )

    @classmethod
    def feed(cls, flow_tag: lt.InandOutputType) -> FeedRequest:
        """Builds the monitored-only feed that addresses the stub's output at the meter.

        Args:
            flow_tag: The flow tag selecting the meter's channel.

        Returns:
            The feed request.
        """
        return FeedRequest(
            consumer=cls.METER_NAME,
            source=cls.SOURCE_NAME,
            output=HeatSourceStub.EnergyDemand,
            component_type=None,
            flow_tags=(flow_tag,),
            weight=cls.MONITORED_ONLY,
        )

    @classmethod
    def resolve(
        cls, meter: Component, source: HeatSourceStub, flow_tag: lt.InandOutputType
    ) -> None:
        """Drives the resolver over the one feed from the source into the meter.

        Args:
            meter: The aggregating meter the feed is addressed at.
            source: The participant the feed measures.
            flow_tag: The flow tag selecting the meter's channel.

        Raises:
            EnergySystemWiringError: Whatever the resolver decides about the arrangement.
        """
        resolver = DynamicConnectionResolver({cls.SOURCE_NAME: source, cls.METER_NAME: meter})
        resolver.resolve_target(cls.METER_NAME, [cls.feed(flow_tag)])


@pytest.mark.base
def test_the_wildcard_agrees_with_everything_on_both_sides() -> None:
    """The port-type predicate is symmetric, which is what makes one class serve two callers.

    A wire and a feed ask the same question from opposite sides — a port against a port, a port
    against a channel — so an asymmetric answer would let a connection be legal by hand and
    refused as a feed. Two differing concrete values stay the mismatch the predicate exists for.
    """
    assert PortTypeCompatibility.load_types_agree(lt.LoadTypes.ANY, lt.LoadTypes.HEATING)
    assert PortTypeCompatibility.load_types_agree(lt.LoadTypes.HEATING, lt.LoadTypes.ANY)
    assert PortTypeCompatibility.load_types_agree(lt.LoadTypes.OIL, lt.LoadTypes.OIL)
    assert not PortTypeCompatibility.load_types_agree(lt.LoadTypes.OIL, lt.LoadTypes.HEATING)

    assert PortTypeCompatibility.units_agree(lt.Units.ANY, lt.Units.WATT_HOUR)
    assert PortTypeCompatibility.units_agree(lt.Units.WATT_HOUR, lt.Units.ANY)
    assert not PortTypeCompatibility.units_agree(lt.Units.WATT, lt.Units.WATT_HOUR)


@pytest.mark.base
def test_the_fuel_meter_channel_takes_any_carrier_but_only_watt_hours() -> None:
    """The fuel meter's declaration is loose about the carrier and strict about the unit.

    Which fuel arrives depends on the household — a boiler types its energy-demand outputs by
    carrier — so the channel cannot name one. The unit is the half that never varies: the sum in
    ``i_simulate`` is in watt-hours whatever burns, and a power output landing in it would be
    added to energies without a word.
    """
    channel = FuelMeter.get_channel(FuelMeter.CONSUMPTION_UNCONTROLLED_CHANNEL)

    assert channel.accepts_load_type(lt.LoadTypes.OIL)
    assert channel.accepts_load_type(lt.LoadTypes.WOOD_CHIPS)
    assert channel.accepts_unit(lt.Units.WATT_HOUR)
    assert not channel.accepts_unit(lt.Units.WATT)


@pytest.mark.base
def test_the_heating_meter_channel_is_concrete_but_still_takes_a_generic_signal() -> None:
    """A concrete channel refuses a different concrete carrier and accepts the wildcard.

    Everything the heating meter measures is heat, so its channels name it and catch a
    temperature summed into a power balance. The wildcard on the *source* side is a separate
    matter: it is HiSim's idiom for a port with no physical type of its own, and a hand-written
    wire from such a port into a heating input is already legal, so the channel path must permit
    exactly the same thing.
    """
    channel = HeatingMeter.get_channel(HeatingMeter.CONSUMPTION_UNCONTROLLED_CHANNEL)

    assert channel.accepts_load_type(lt.LoadTypes.HEATING)
    assert not channel.accepts_load_type(lt.LoadTypes.OIL)
    assert channel.accepts_load_type(lt.LoadTypes.ANY)


@pytest.mark.base
def test_the_matcher_lets_any_carrier_reach_the_fuel_and_gas_meters() -> None:
    """A feed with the right tag and unit matches whatever carrier its port happens to name.

    This is the declaration doing its job through the matcher rather than through the predicate:
    the wood chips of one household and the green hydrogen of another reach the same channel that
    natural gas and oil do, because the tag and the unit are what identify the flow.
    """
    fuel_channel = ChannelMatcher.match_and_validate(
        feed=MeterFixtures.feed(lt.InandOutputType.HEAT_CONSUMPTION),
        channels=FuelMeter.CHANNELS,
        target="the fuel meter",
        source_load_type=lt.LoadTypes.WOOD_CHIPS,
        source_unit=lt.Units.WATT_HOUR,
    )
    assert fuel_channel.key == FuelMeter.CONSUMPTION_UNCONTROLLED_CHANNEL

    gas_channel = ChannelMatcher.match_and_validate(
        feed=MeterFixtures.feed(lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED),
        channels=GasMeter.CHANNELS,
        target="the gas meter",
        source_load_type=lt.LoadTypes.GREEN_HYDROGEN,
        source_unit=lt.Units.WATT_HOUR,
    )
    assert gas_channel.key == GasMeter.CONSUMPTION_UNCONTROLLED_CHANNEL


@pytest.mark.base
def test_the_matcher_refuses_a_feed_in_the_wrong_unit() -> None:
    """The unit the wildcard does not cover is still checked, and refused with ``EF-30``.

    Wildcarding the carrier must not be read as wildcarding the port description, which is the
    mistake this test exists to catch: a power output feeding an energy channel is the classic
    way to end up with a plausible-looking total that means nothing.
    """
    with pytest.raises(EnergySystemWiringError) as caught:
        ChannelMatcher.match_and_validate(
            feed=MeterFixtures.feed(lt.InandOutputType.HEAT_CONSUMPTION),
            channels=FuelMeter.CHANNELS,
            target="the fuel meter",
            source_load_type=lt.LoadTypes.OIL,
            source_unit=lt.Units.WATT,
        )

    assert caught.value.error_id.value == "EF-30"


@pytest.mark.base
def test_a_carrier_the_fuel_meter_does_not_price_is_refused_at_the_resolver() -> None:
    """Wood chips fed into an oil meter are refused, naming both carriers.

    The channel is a class-level declaration and therefore cannot know which fuel this instance
    was configured for, while ``get_cost_opex`` prices everything the instance measures as that
    one fuel. Without this check the wood chips would be summed, billed at the oil price, given
    the oil emission factor and reported as a perfectly ordinary result. The refusal reaches the
    author as an ordinary located wiring error, because the resolver translates it.
    """
    with pytest.raises(EnergySystemWiringError) as caught:
        MeterFixtures.resolve(
            MeterFixtures.fuel_meter(lt.LoadTypes.OIL),
            MeterFixtures.heat_source(lt.LoadTypes.WOOD_CHIPS),
            lt.InandOutputType.HEAT_CONSUMPTION,
        )

    assert caught.value.error_id.value == "EF-30"
    assert "WOOD_CHIPS" in str(caught.value)
    assert "OIL" in str(caught.value)


@pytest.mark.base
def test_the_configured_carrier_resolves_at_the_fuel_meter() -> None:
    """The arrangement the cross-check is meant to let through does go through.

    Same meter, same feed, same tag: only the source's carrier differs from the refused case, so
    a failure here would mean the check is refusing the very wiring the meter exists for.
    """
    MeterFixtures.resolve(
        MeterFixtures.fuel_meter(lt.LoadTypes.OIL),
        MeterFixtures.heat_source(lt.LoadTypes.OIL),
        lt.InandOutputType.HEAT_CONSUMPTION,
    )


@pytest.mark.base
def test_a_generically_typed_source_resolves_at_the_fuel_meter() -> None:
    """A source that names no carrier of its own is accepted, which is what the wildcard is for.

    A component may legitimately publish an energy output with no physical load type — the
    meter's configuration is then the only statement about what is flowing — and refusing that
    would make the wildcard unusable on the very path it was introduced for.
    """
    MeterFixtures.resolve(
        MeterFixtures.fuel_meter(lt.LoadTypes.OIL),
        MeterFixtures.heat_source(lt.LoadTypes.ANY),
        lt.InandOutputType.HEAT_CONSUMPTION,
    )


@pytest.mark.base
def test_a_gas_the_meter_does_not_price_is_refused_at_the_resolver() -> None:
    """Green hydrogen fed into a natural-gas meter is refused, naming both carriers.

    The gas meter has the same footgun as the fuel meter and the same reason for it: two channels
    wildcarding the load type, one ``gas_loadtype`` deciding the price and the emission factor of
    everything they sum.
    """
    with pytest.raises(EnergySystemWiringError) as caught:
        MeterFixtures.resolve(
            MeterFixtures.gas_meter(lt.LoadTypes.GAS),
            MeterFixtures.heat_source(lt.LoadTypes.GREEN_HYDROGEN),
            lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED,
        )

    assert caught.value.error_id.value == "EF-30"
    assert "GREEN_HYDROGEN" in str(caught.value)
    assert "GAS" in str(caught.value)


@pytest.mark.base
def test_the_configured_gas_resolves_at_the_gas_meter() -> None:
    """The matching gas goes through, so the check refuses mismatches and nothing else."""
    MeterFixtures.resolve(
        MeterFixtures.gas_meter(lt.LoadTypes.GAS),
        MeterFixtures.heat_source(lt.LoadTypes.GAS),
        lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED,
    )


@pytest.mark.base
def test_a_district_heating_meter_takes_the_heat_domain_carriers_its_own_defaults_declare() -> None:
    """The one sanctioned carrier widening — district heating's two heat domains — stays open.

    A district-heating source types its two energy outputs by heat domain, space heating and hot
    water, while the meter is configured for the network it is billed by; the meter's own default
    connections have always declared exactly that pairing. The carrier cross-check consults
    :attr:`FuelMeter.ADDITIONALLY_ACCEPTED_CARRIERS` to keep it working, and this test pins that
    the table really covers both domains — and nothing else, so an oil feed still cannot reach a
    district-heating bill.
    """
    for carrier in (lt.LoadTypes.HEATING, lt.LoadTypes.WARM_WATER):
        MeterFixtures.resolve(
            MeterFixtures.fuel_meter(lt.LoadTypes.DISTRICTHEATING),
            MeterFixtures.heat_source(carrier),
            lt.InandOutputType.HEAT_CONSUMPTION,
        )

    with pytest.raises(EnergySystemWiringError) as caught:
        MeterFixtures.resolve(
            MeterFixtures.fuel_meter(lt.LoadTypes.DISTRICTHEATING),
            MeterFixtures.heat_source(lt.LoadTypes.OIL),
            lt.InandOutputType.HEAT_CONSUMPTION,
        )
    assert caught.value.error_id.value == "EF-30"
    assert "DISTRICTHEATING" in str(caught.value)
