"""Tests of the sizing-fact engine (``hisim/config/engine.py``, spec §8.4).

Covers the three phases (registration, graph validation, fixed-point resolution), the
fact-scoping rules (flat pool for global facts, connection-graph lookup for sibling
facts, hard errors on every ambiguity), the null-fact and pre-seeding semantics, and the
real production chain building → HDS controller → HDS / boiler, resolved seedlessly.
"""

# clean

from dataclasses import dataclass

import pytest
from dataclasses_json import dataclass_json

from hisim.config import (
    AUTO,
    ComponentID,
    ConfigBase,
    ConfigSizingError,
    Sizable,
    Size,
    SizingContext,
    SizingError,
    sized_field,
)
from hisim.config.engine import FactContribution, FactScope, resolve_all


@dataclass_json
@dataclass
class _ProducerConfig(ConfigBase):
    """A fixture producer contributing one CONNECTED fact from its own resolved field."""

    component_id: ComponentID
    power_in_watt: Sizable[float] = sized_field(rule=1.0 * Size.HEATING_LOAD_IN_WATT)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._ProducerConfig"


_ProducerConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("maximal_thermal_power_in_watt",),
        compute=lambda config, ctx: {"maximal_thermal_power_in_watt": config.power_in_watt},
        scope=FactScope.CONNECTED,
    ),
)


@dataclass_json
@dataclass
class _ConsumerConfig(ConfigBase):
    """A fixture consumer sized from the producer's CONNECTED fact."""

    component_id: ComponentID
    band_in_watt: Sizable[float] = sized_field(rule=0.5 * Size.MAXIMAL_THERMAL_POWER_IN_WATT)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._ConsumerConfig"


def _producer(name: str) -> _ProducerConfig:
    """Builds a producer fixture with the given component name."""
    return _ProducerConfig(component_id=ComponentID(name=name))


def _consumer(name: str) -> _ConsumerConfig:
    """Builds a consumer fixture with the given component name."""
    return _ConsumerConfig(component_id=ComponentID(name=name))


@pytest.mark.base
def test_a_connected_fact_flows_from_a_single_producer_without_adjacency():
    """One producer in the pool: the flat-pool uniqueness rule suffices (setup mode)."""
    resolved = resolve_all(
        [_producer("Boiler"), _consumer("Controller")],
        seed=SizingContext(heating_load_in_watt=10_000.0),
    )
    assert resolved[1].band_in_watt == 5_000.0


@pytest.mark.base
def test_two_producers_without_adjacency_are_ambiguous():
    """Two producers of one fact and no connection graph: sizing refuses to guess."""
    with pytest.raises(ConfigSizingError, match="more than one source"):
        resolve_all(
            [_producer("Boiler1"), _producer("Boiler2"), _consumer("Controller")],
            seed=SizingContext(heating_load_in_watt=10_000.0),
        )


@pytest.mark.base
def test_the_connection_graph_disambiguates_two_producers():
    """With adjacency, the consumer reads the producer it is connected to."""
    resolved = resolve_all(
        [_producer("Boiler1"), _producer("Boiler2"), _consumer("Controller")],
        seed=SizingContext(heating_load_in_watt=10_000.0),
        adjacency={"Controller": {"Boiler1"}},
    )
    assert resolved[2].band_in_watt == 5_000.0


@pytest.mark.base
def test_a_two_hop_single_provider_resolves_through_the_flat_pool_fallback():
    """No direct neighbor declares the fact, so the flat-pool fallback finds the sole provider.

    This is the §8.4 hybrid refinement (2026-08-20): the consumer and its provider are
    two wiring hops apart (both connect to a hub, like battery and PV around the EMS),
    so a purely adjacency-scoped lookup would starve. Because no neighbor declares the
    fact, the lookup widens to the whole pool, where exactly one provider exists.
    """
    resolved = resolve_all(
        [_producer("Boiler"), _consumer("Controller")],
        seed=SizingContext(heating_load_in_watt=10_000.0),
        adjacency={"Controller": {"Hub"}, "Hub": {"Controller", "Boiler"}, "Boiler": {"Hub"}},
    )
    assert resolved[1].band_in_watt == 5_000.0


@pytest.mark.base
def test_two_two_hop_providers_are_ambiguous_and_name_both():
    """Two providers beyond the consumer's neighborhood: the fallback refuses to guess.

    The fallback is the flat-pool rule, so genuine ambiguity stays a hard error even
    with an adjacency present, and the message names both providers and the consumer.
    """
    with pytest.raises(
        ConfigSizingError, match=r"(?s)'Controller'.*more than one source.*Boiler1, Boiler2"
    ):
        resolve_all(
            [_producer("Boiler1"), _producer("Boiler2"), _consumer("Controller")],
            seed=SizingContext(heating_load_in_watt=10_000.0),
            adjacency={"Controller": {"Hub"}, "Hub": {"Controller", "Boiler1", "Boiler2"}},
        )


@pytest.mark.base
def test_ambiguity_is_detected_regardless_of_listing_order():
    """The consumer listed before its two providers still fails: providership is declared.

    The lookup decides its provider set from the *declared* contributions rather than
    from the values contributed so far, so a consumer that the fixed point visits before
    either producer has computed anything reports the identical ambiguity instead of
    silently consuming whichever value happened to arrive first.
    """
    with pytest.raises(ConfigSizingError, match="more than one source"):
        resolve_all(
            [_consumer("Controller"), _producer("Boiler1"), _producer("Boiler2")],
            seed=SizingContext(heating_load_in_watt=10_000.0),
        )


@pytest.mark.base
def test_an_unprovided_fact_is_rejected_before_any_computation():
    """Phase-2 validation names consumer and fact when nobody could provide it."""
    with pytest.raises(ConfigSizingError, match="Controller.*maximal_thermal_power_in_watt|maximal_thermal_power_in_watt.*Controller"):
        resolve_all([_consumer("Controller")])


@pytest.mark.base
def test_a_null_fact_names_its_provider():
    """A fact provided as null (feature off) is a hard error attributed to the provider."""

    @dataclass_json
    @dataclass
    class _NullProducerConfig(ConfigBase):
        """Fixture producer whose declared fact is off, hence null."""

        component_id: ComponentID

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._NullProducerConfig"

    _NullProducerConfig.SIZING_CONTRIBUTIONS = (
        FactContribution(
            facts=("maximal_thermal_power_in_watt",),
            compute=lambda config, ctx: {"maximal_thermal_power_in_watt": None},
            scope=FactScope.CONNECTED,
        ),
    )
    with pytest.raises(ConfigSizingError, match="provided as null by 'Off'"):
        resolve_all(
            [_NullProducerConfig(component_id=ComponentID(name="Off")), _consumer("Controller")]
        )


@pytest.mark.base
def test_preseeded_facts_win_over_contributions():
    """File-level sizing_facts overrides beat contributed values, per spec §8.4."""
    resolved = resolve_all(
        [_producer("Boiler"), _consumer("Controller")],
        seed=SizingContext(heating_load_in_watt=10_000.0),
        preseeded_facts={"maximal_thermal_power_in_watt": 2_000.0},
    )
    assert resolved[1].band_in_watt == 1_000.0


@pytest.mark.base
def test_a_deadlock_is_diagnosed_with_the_waiting_picture():
    """Mutual waiting reports who waits for what instead of a bare no-progress error."""

    @dataclass_json
    @dataclass
    class _CyclicConfig(ConfigBase):
        """Fixture that needs the very fact it would contribute — a one-node cycle."""

        component_id: ComponentID
        value_in_watt: Sizable[float] = sized_field(rule=1.0 * Size.MAXIMAL_THERMAL_POWER_IN_WATT)

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._CyclicConfig"

    _CyclicConfig.SIZING_CONTRIBUTIONS = (
        FactContribution(
            facts=("maximal_thermal_power_in_watt",),
            compute=lambda config, ctx: {"maximal_thermal_power_in_watt": config.value_in_watt},
            scope=FactScope.CONNECTED,
        ),
    )
    with pytest.raises(ConfigSizingError, match=r"(?s)no further progress.*waits for"):
        resolve_all([_CyclicConfig(component_id=ComponentID(name="Snake"))])


@pytest.mark.base
def test_duplicate_component_keys_are_rejected_at_registration():
    """Two configs with one key would make fact attribution ambiguous."""
    with pytest.raises(SizingError, match="share the component key"):
        resolve_all([_producer("Twin"), _producer("Twin")])


@pytest.mark.base
def test_a_double_global_contribution_is_a_hard_error():
    """The flat pool rejects a second contributor of the same global fact."""

    @dataclass_json
    @dataclass
    class _GlobalProducerConfig(ConfigBase):
        """Fixture contributing a global fact."""

        component_id: ComponentID

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._GlobalProducerConfig"

    _GlobalProducerConfig.SIZING_CONTRIBUTIONS = (
        FactContribution(
            facts=("conditioned_floor_area_in_m2",),
            compute=lambda config, ctx: {"conditioned_floor_area_in_m2": 100.0},
        ),
    )
    with pytest.raises(ConfigSizingError, match="contributed twice"):
        resolve_all(
            [
                _GlobalProducerConfig(component_id=ComponentID(name="A")),
                _GlobalProducerConfig(component_id=ComponentID(name="B")),
            ]
        )


@pytest.mark.base
def test_a_contribution_must_name_only_registry_facts():
    """Declared fact names outside the SizingContext registry fail at declaration time."""
    with pytest.raises(SizingError, match="unknown fact"):
        FactContribution(facts=("no_such_fact",), compute=lambda config, ctx: {})


@pytest.mark.base
def test_unresolved_configs_keep_auto_until_the_engine_ran():
    """Sanity: the engine, not construction order, is what turns AUTO into numbers."""
    template = _consumer("Controller")
    assert template.band_in_watt is AUTO
    resolved = resolve_all(
        [_producer("Boiler"), template], seed=SizingContext(heating_load_in_watt=4_000.0)
    )
    assert template.band_in_watt is AUTO  # input untouched
    assert resolved[1].band_in_watt == 2_000.0


@pytest.mark.base
def test_the_real_chain_resolves_seedlessly_and_order_independently():
    """The chain building → HDS controller → HDS/boiler resolves from a shuffled, seedless input."""
    from hisim.components.building import BuildingConfig
    from hisim.components.generic_boiler import GenericBoilerConfig
    from hisim.components.heat_distribution_system import (
        HeatDistributionConfig,
        HeatDistributionControllerConfig,
        HeatDistributionSystemType,
    )

    building = BuildingConfig.presets.german_single_family_home
    controller = HeatDistributionControllerConfig.get_default_heat_distribution_controller_config(
        set_heating_temperature_for_building_in_celsius=20.0,
        set_cooling_temperature_for_building_in_celsius=25.0,
        heating_load_of_building_in_watt=7780.8,
        heating_reference_temperature_in_celsius=-7.0,
    )
    hds = HeatDistributionConfig.presets.standard
    boiler = GenericBoilerConfig.presets.condensing_gas
    resolved = resolve_all([hds, boiler, building, controller])  # deliberately shuffled
    resolved_hds, resolved_boiler = resolved[0], resolved[1]
    assert resolved_hds.water_mass_flow_rate_in_kg_per_second == 0.27
    assert resolved_hds.heating_system is HeatDistributionSystemType.FLOORHEATING
    heating_load = SizingContext.for_building(building).heating_load_in_watt
    assert heating_load is not None
    assert resolved_boiler.maximal_thermal_power_in_watt == pytest.approx(heating_load * 1.1)
    assert resolved_boiler.sizing_record  # provenance for the audit trail
