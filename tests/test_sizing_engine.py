"""Tests of the sizing-fact engine (``hisim/config/engine.py``) and its binding rule.

Covers the three phases (registration, graph validation, fixed-point resolution) and the
one binding rule that replaced the old fact scoping: a bare fact binds only when exactly
one config in the resolved set declares it, an explicit ``sources`` mapping decides every
other case, the seed context is a provider like any other, and a provider whose value is
``None`` still counts. Also covers the sibling-read machinery (``Self`` terms, intra-config
ordering, the cycle error), the many-cardinality hook, and the provenance the resolution
leaves behind in ``sizing_record`` and the ``ResolutionReport``.
"""

# clean

import json
import random
from dataclasses import dataclass

import pytest
from dataclasses_json import dataclass_json

from hisim.config import (
    AUTO,
    ComponentID,
    ConfigBase,
    ConfigSizingError,
    Many,
    Self,
    Sizable,
    Size,
    SizingContext,
    SizingError,
    SizingFactEngine,
    law,
    sized_field,
)
from hisim.config.contributions import FactContribution
from hisim.config.engine import resolve_all
from hisim.config.report import LookupMode


@dataclass_json
@dataclass
class _ProducerConfig(ConfigBase):
    """A fixture producer contributing its own resolved power as a sizing fact."""

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
    ),
)


@dataclass_json
@dataclass
class _NullProducerConfig(ConfigBase):
    """A fixture producer whose declared fact is switched off, hence contributed as null."""

    component_id: ComponentID

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._NullProducerConfig"


_NullProducerConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("maximal_thermal_power_in_watt",),
        compute=lambda config, ctx: {"maximal_thermal_power_in_watt": None},
    ),
)


@dataclass_json
@dataclass
class _FlowProducerConfig(ConfigBase):
    """A fixture producer of a fact that only one config in the fixtures declares."""

    component_id: ComponentID

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._FlowProducerConfig"


_FlowProducerConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("water_mass_flow_rate_in_kg_per_second",),
        compute=lambda config, ctx: {"water_mass_flow_rate_in_kg_per_second": 0.27},
    ),
)


@dataclass_json
@dataclass
class _ConsumerConfig(ConfigBase):
    """A fixture consumer sized from a producer's contributed power fact."""

    component_id: ComponentID
    band_in_watt: Sizable[float] = sized_field(rule=0.5 * Size.MAXIMAL_THERMAL_POWER_IN_WATT)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._ConsumerConfig"


@dataclass_json
@dataclass
class _FlowConsumerConfig(ConfigBase):
    """A fixture consumer of the unambiguous mass-flow fact, which needs no mapping."""

    component_id: ComponentID
    flow_in_kg_per_second: Sizable[float] = sized_field(
        rule=Size.WATER_MASS_FLOW_RATE_IN_KG_PER_SECOND.rounded(2)
    )

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._FlowConsumerConfig"


def _producer(name: str) -> _ProducerConfig:
    """Builds a producer fixture with the given instance name."""
    return _ProducerConfig(component_id=ComponentID(name=name))


def _consumer(name: str) -> _ConsumerConfig:
    """Builds a consumer fixture with the given instance name."""
    return _ConsumerConfig(component_id=ComponentID(name=name))


# ---------------------------------------------------------------- T-1: the pilots


@pytest.mark.base
def test_the_pilot_chain_resolves_without_any_sources_mapping():
    """Building → HDS controller → HDS / boiler resolves seedlessly and keeps its numbers.

    Failure mode caught: the binding rule demanding a ``sources`` entry for a scenario in
    which every fact has exactly one provider — the unambiguous case must stay free of
    declarations — or the rework quietly changing a resolved pilot value.
    """
    from hisim.components.building import BuildingConfig
    from hisim.components.generic_boiler import GenericBoilerConfig
    from hisim.components.heat_distribution_system import (
        HeatDistributionConfig,
        HeatDistributionControllerConfig,
        HeatDistributionSystemType,
    )

    building = BuildingConfig.presets.german_single_family_home
    heating_load = SizingContext.for_building(building).heating_load_in_watt
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
    assert resolved_hds.absolute_conditioned_floor_area_in_m2 == pytest.approx(121.2)
    assert heating_load is not None
    assert resolved_boiler.maximal_thermal_power_in_watt == pytest.approx(heating_load * 1.1)
    assert resolved_boiler.minimal_thermal_power_in_watt == 0.0
    assert resolved_boiler.sizing_record  # provenance for the audit trail


@pytest.mark.base
def test_a_single_provider_binds_without_a_mapping_and_leaves_the_input_untouched():
    """One declared provider makes the bare fact bind; the input configs are never mutated."""
    template = _consumer("Controller")
    assert template.band_in_watt is AUTO
    resolved = resolve_all(
        [_producer("Boiler"), template], seed=SizingContext(heating_load_in_watt=4_000.0)
    )
    assert template.band_in_watt is AUTO
    assert resolved[1].band_in_watt == 2_000.0


# ------------------------------------------------- T-2: ambiguity and explicit sources


@pytest.mark.base
def test_two_providers_of_one_fact_raise_with_both_candidates_and_a_paste_ready_mapping():
    """Ambiguity names every candidate and the exact ``sources`` snippet that resolves it.

    Failure mode caught: the engine picking one of two equally declared providers — the
    resolved numbers would look plausible while silently depending on listing order — or
    an error that says "ambiguous" without telling the author what to write.
    """
    with pytest.raises(ConfigSizingError) as raised:
        resolve_all(
            [_producer("pv_east"), _producer("pv_south"), _consumer("battery")],
            seed=SizingContext(heating_load_in_watt=10_000.0),
        )
    message = str(raised.value)
    assert "'maximal_thermal_power_in_watt' needed by 'battery' is provided by pv_east, pv_south" in message
    assert (
        "sources={'battery': {'maximal_thermal_power_in_watt': "
        "'<one of pv_east.maximal_thermal_power_in_watt or "
        "pv_south.maximal_thermal_power_in_watt>'}}"
    ) in message


@pytest.mark.base
def test_an_explicit_mapping_binds_the_named_provider():
    """With the mapping written, the consumer sizes from exactly the provider it names."""
    resolved = resolve_all(
        [_producer("pv_east"), _producer("pv_south"), _consumer("battery")],
        seed=SizingContext(heating_load_in_watt=10_000.0),
        sources={"battery": {"maximal_thermal_power_in_watt": "pv_south.maximal_thermal_power_in_watt"}},
    )
    assert resolved[2].band_in_watt == 5_000.0
    engine_lookup = [entry for entry in _run_engine_lookups(
        [_producer("pv_east"), _producer("pv_south"), _consumer("battery")],
        {"battery": {"maximal_thermal_power_in_watt": "pv_south.maximal_thermal_power_in_watt"}},
    ) if entry.consumer == "battery"]
    assert engine_lookup[0].source == "pv_south"
    assert engine_lookup[0].mode == LookupMode.EXPLICIT
    assert engine_lookup[0].candidates == ("pv_east", "pv_south")


def _run_engine_lookups(configs, sources):
    """Runs an engine over the fixtures and returns its recorded fact lookups."""
    engine = SizingFactEngine(seed=SizingContext(heating_load_in_watt=10_000.0), sources=sources)
    engine.resolve_all(configs)
    return engine.report.lookups


@pytest.mark.base
def test_a_mapping_to_a_config_that_does_not_declare_the_fact_is_rejected():
    """Pointing at a non-provider is an error naming what that config actually declares."""
    with pytest.raises(ConfigSizingError, match="does not declare 'maximal_thermal_power_in_watt'"):
        resolve_all(
            [_producer("pv_east"), _producer("pv_south"), _consumer("battery")],
            seed=SizingContext(heating_load_in_watt=10_000.0),
            sources={"battery": {"maximal_thermal_power_in_watt": "battery.maximal_thermal_power_in_watt"}},
        )


@pytest.mark.base
def test_the_seed_context_is_a_provider_and_collides_with_a_declared_one():
    """A seeded fact that a present config also declares is an ambiguity, not a preference.

    Failure mode caught: the seed silently winning over (or losing to) a component that
    computes the same fact, so a setup that seeds *and* passes the provider would size
    against a value nobody can point at afterwards.
    """
    with pytest.raises(ConfigSizingError, match=r"provided by <seed>, Boiler"):
        resolve_all(
            [_producer("Boiler"), _consumer("Controller")],
            seed=SizingContext(heating_load_in_watt=10_000.0, maximal_thermal_power_in_watt=8_000.0),
        )


@pytest.mark.base
def test_a_seeded_fact_binds_as_the_seed_provider():
    """A fact only the seed carries binds to ``<seed>`` and is recorded under that mode."""
    engine = SizingFactEngine(seed=SizingContext(heating_load_in_watt=10_000.0))
    engine.resolve_all([_producer("Boiler")])
    entry = engine.report.lookups[0]
    assert entry.source == "<seed>"
    assert entry.mode == LookupMode.SEED


@pytest.mark.base
def test_an_unprovided_fact_is_rejected_before_any_computation():
    """Validation names consumer and fact when nobody in the set could provide it."""
    with pytest.raises(ConfigSizingError, match="'maximal_thermal_power_in_watt' needed by 'Controller'"):
        resolve_all([_consumer("Controller")])


# ------------------------------------------- T-3: null providers and order independence


@pytest.mark.base
def test_a_null_valued_provider_still_counts_for_the_ambiguity_rule():
    """A provider whose feature is off still forces the consumer to say which it means.

    Failure mode caught: providership being decided from computed values instead of
    declarations — flipping a feature flag on one component would then silently re-bind a
    different component's size, which is exactly the surprise the binding rule forbids.
    """
    with pytest.raises(ConfigSizingError, match=r"provided by Boiler, HeatPump"):
        resolve_all(
            [
                _producer("Boiler"),
                _NullProducerConfig(component_id=ComponentID(name="HeatPump")),
                _consumer("DhwController"),
            ],
            seed=SizingContext(heating_load_in_watt=10_000.0),
        )


@pytest.mark.base
def test_binding_to_the_null_provider_names_it_in_the_error():
    """Reading a fact whose bound provider computed ``None`` fails, attributed to that provider."""
    with pytest.raises(ConfigSizingError, match="provided as null by 'HeatPump'"):
        resolve_all(
            [
                _producer("Boiler"),
                _NullProducerConfig(component_id=ComponentID(name="HeatPump")),
                _consumer("DhwController"),
            ],
            seed=SizingContext(heating_load_in_watt=10_000.0),
            sources={"DhwController": {"maximal_thermal_power_in_watt": "HeatPump.maximal_thermal_power_in_watt"}},
        )


@pytest.mark.base
def test_only_the_reader_of_the_contested_fact_needs_a_mapping_and_order_never_matters():
    """The unambiguous consumer stays declaration-free, and 20 shuffles give one result.

    Failure mode caught: providership or resolution outcome depending on the order the
    configs happen to be listed in — a scenario that resolves in one setup and fails, or
    resolves differently, in another that lists the same components differently.
    """
    sources = {"DhwController": {"maximal_thermal_power_in_watt": "Boiler.maximal_thermal_power_in_watt"}}
    shuffler = random.Random(20250825)
    outcomes = set()
    for _ in range(20):
        configs = [
            _producer("Boiler"),
            _NullProducerConfig(component_id=ComponentID(name="HeatPump")),
            _consumer("DhwController"),
            _FlowProducerConfig(component_id=ComponentID(name="HdsController")),
            _FlowConsumerConfig(component_id=ComponentID(name="Hds")),
        ]
        shuffler.shuffle(configs)
        resolved = {config.component_id.name: config for config in resolve_all(
            configs, seed=SizingContext(heating_load_in_watt=10_000.0), sources=sources)}
        outcomes.add((resolved["DhwController"].band_in_watt, resolved["Hds"].flow_in_kg_per_second))
    assert outcomes == {(5_000.0, 0.27)}


# ------------------------------------------------------ T-4: the mapping never computes


@pytest.mark.base
@pytest.mark.parametrize(
    "bad_value",
    [4200.0, "sum(a,b)", ["Boiler.maximal_thermal_power_in_watt"], "Boiler.some_other_fact"],
)
def test_a_sources_value_that_is_not_a_qualified_reference_is_rejected(bad_value):
    """Numbers, expressions, lists for a scalar read and wrong-fact references all fail.

    Failure mode caught: the mapping growing into a place where values are computed or
    overridden — it may only redirect an input, so anything that is not a
    ``"<name>.<fact>"`` reference must be refused at the API boundary rather than
    silently accepted and then mis-used.
    """
    with pytest.raises(ConfigSizingError, match=r"expected (one|a list of) reference\(s\)"):
        resolve_all(
            [_producer("Boiler"), _consumer("Controller")],
            seed=SizingContext(heating_load_in_watt=10_000.0),
            sources={"Controller": {"maximal_thermal_power_in_watt": bad_value}},
        )


# ------------------------------------------------------------ T-5: depth and cycles


@dataclass_json
@dataclass
class _WeatherLikeConfig(ConfigBase):
    """Root of the depth-four fixture chain: contributes a fact it needs nothing for."""

    component_id: ComponentID

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._WeatherLikeConfig"


_WeatherLikeConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("heating_reference_temperature_in_celsius",),
        compute=lambda config, ctx: {"heating_reference_temperature_in_celsius": -7.0},
    ),
)


@dataclass_json
@dataclass
class _BuildingLikeConfig(ConfigBase):
    """Second link of the depth-four chain: sizes from the weather, contributes a load."""

    component_id: ComponentID
    load_in_watt: Sizable[float] = sized_field(
        rule=-1_000.0 * Size.HEATING_REFERENCE_TEMPERATURE_IN_CELSIUS
    )

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._BuildingLikeConfig"


_BuildingLikeConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("heating_load_in_watt",),
        compute=lambda config, ctx: {"heating_load_in_watt": config.load_in_watt},
    ),
)


@pytest.mark.base
def test_a_four_deep_chain_resolves_in_the_order_the_dependencies_demand():
    """Weather → building → generator → controller resolves from a shuffled input list.

    Failure mode caught: the fixed point terminating before a late link is reachable, so
    a deep but perfectly acyclic scenario would report a deadlock instead of resolving.
    """
    configs = [
        _consumer("Controller"),
        _producer("Generator"),
        _BuildingLikeConfig(component_id=ComponentID(name="Building")),
        _WeatherLikeConfig(component_id=ComponentID(name="Weather")),
    ]
    engine = SizingFactEngine()
    resolved = engine.resolve_all(configs)
    assert resolved[2].load_in_watt == 7_000.0
    assert resolved[1].power_in_watt == 7_000.0
    assert resolved[0].band_in_watt == 3_500.0
    assert engine.resolution_order == ["Weather", "Building", "Generator", "Controller"]


@dataclass_json
@dataclass
class _CycleAConfig(ConfigBase):
    """One half of a two-node cycle: needs the power fact, contributes the load fact."""

    component_id: ComponentID
    value_in_watt: Sizable[float] = sized_field(rule=1.0 * Size.MAXIMAL_THERMAL_POWER_IN_WATT)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing_engine._CycleAConfig"


_CycleAConfig.SIZING_CONTRIBUTIONS = (
    FactContribution(
        facts=("heating_load_in_watt",),
        compute=lambda config, ctx: {"heating_load_in_watt": config.value_in_watt},
    ),
)


@pytest.mark.base
def test_a_two_node_cycle_is_diagnosed_naming_both_members_and_the_history():
    """Two configs waiting on each other's fact report who waits for what, plus the history.

    Failure mode caught: a bare "no progress" error in a scenario with dozens of configs,
    which says nothing about which pair actually closed the loop.
    """
    with pytest.raises(ConfigSizingError) as raised:
        resolve_all([
            _CycleAConfig(component_id=ComponentID(name="Alpha")),
            _producer("Beta"),
        ])
    message = str(raised.value)
    assert "'Alpha' waits for ['maximal_thermal_power_in_watt']" in message
    assert "'Beta' waits for ['heating_load_in_watt']" in message
    assert "Resolution history up to the deadlock" in message


# ------------------------------------------------------------- T-6: the many hook


@pytest.mark.base
def test_a_many_term_is_declarable_and_raises_when_it_is_evaluated():
    """``Many(...)`` binds like a fact but refuses to aggregate, naming the parking lot.

    Failure mode caught: the hook quietly evaluating to the first value or to a sum — a
    law written for several providers would then produce a plausible but undecided
    number instead of failing until the aggregation is specified.
    """

    @dataclass_json
    @dataclass
    class _ManyReaderConfig(ConfigBase):
        """Fixture whose law reads every provider of one fact."""

        component_id: ComponentID
        total_in_watt: Sizable[float] = sized_field(rule=Many(Size.MAXIMAL_THERMAL_POWER_IN_WATT))

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._ManyReaderConfig"

    with pytest.raises(NotImplementedError, match="many-cardinality is declared but not implemented"):
        resolve_all(
            [_producer("Boiler"), _ManyReaderConfig(component_id=ComponentID(name="Aggregator"))],
            seed=SizingContext(heating_load_in_watt=10_000.0),
        )


@pytest.mark.base
def test_a_many_read_of_an_ambiguous_fact_asks_for_a_list():
    """The ambiguity error for a many-read tells the author to write a list, not one reference."""

    @dataclass_json
    @dataclass
    class _ManyAmbiguousConfig(ConfigBase):
        """Fixture reading a fact declared by two configs at many cardinality."""

        component_id: ComponentID
        total_in_watt: Sizable[float] = sized_field(rule=Many(Size.MAXIMAL_THERMAL_POWER_IN_WATT))

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._ManyAmbiguousConfig"

    with pytest.raises(ConfigSizingError) as raised:
        resolve_all(
            [
                _producer("Boiler1"),
                _producer("Boiler2"),
                _ManyAmbiguousConfig(component_id=ComponentID(name="Aggregator")),
            ],
            seed=SizingContext(heating_load_in_watt=10_000.0),
        )
    message = str(raised.value)
    assert "read many-fold by 'Aggregator'" in message
    assert "'Boiler1.maximal_thermal_power_in_watt', 'Boiler2.maximal_thermal_power_in_watt'" in message
    assert "an empty list for none" in message


@pytest.mark.base
def test_a_law_reading_one_and_many_of_the_same_fact_is_rejected_at_declaration():
    """Declaring a fact at both cardinalities in one law is contradictory and fails on import."""
    with pytest.raises(SizingError, match="both as one provider and as many"):
        law(
            lambda ctx: 0.0,
            reads=(Size.MAXIMAL_THERMAL_POWER_IN_WATT, Many(Size.MAXIMAL_THERMAL_POWER_IN_WATT)),
        )


# --------------------------------------------------------------- T-7: provenance


@pytest.mark.base
def test_the_sizing_record_names_the_provider_each_fact_came_from():
    """Every recorded input is qualified with the instance that provided it.

    Failure mode caught: a record that says a value came from
    ``maximal_thermal_power_in_watt`` in a scenario with two providers of that fact —
    the number would be unverifiable after the run, which is the whole point of the record.
    """
    resolved = resolve_all(
        [_producer("pv_east"), _producer("pv_south"), _consumer("battery")],
        seed=SizingContext(heating_load_in_watt=10_000.0),
        sources={"battery": {"maximal_thermal_power_in_watt": "pv_south.maximal_thermal_power_in_watt"}},
    )
    entry = resolved[2].sizing_record[0]
    assert entry.field == "band_in_watt"
    assert entry.inputs == (("pv_south.maximal_thermal_power_in_watt", 10_000.0),)
    producer_entry = resolved[0].sizing_record[0]
    assert producer_entry.inputs == (("<seed>.heating_load_in_watt", 10_000.0),)


@pytest.mark.base
def test_a_sibling_read_is_recorded_as_a_self_input():
    """A law reading its own config's field records it as ``self.<field>`` with its value."""
    from hisim.components.generic_boiler import GenericBoilerConfig

    pellet = GenericBoilerConfig.presets.pellets
    pellet.maximal_thermal_power_in_watt = 12_000.0
    resolved = pellet.resolve(SizingContext())
    entry = {item.field: item for item in resolved.sizing_record}["minimal_thermal_power_in_watt"]
    assert entry.inputs == (("self.maximal_thermal_power_in_watt", 12_000.0),)


# ------------------------------------------------- T-14: sibling reads and ordering


@pytest.mark.base
def test_a_pinned_sibling_is_what_the_dependent_field_sizes_from():
    """Pinning the boiler's maximum to 12000 makes the pellet minimum exactly a twelfth of it.

    Failure mode caught: the dependent field re-evaluating the sibling's *law* instead of
    reading its final value — the resolved config would then carry a minimum computed
    from a maximum it does not actually have, silently and only when the author pins one
    of the two.
    """
    from hisim.components.generic_boiler import GenericBoilerConfig

    pellet = GenericBoilerConfig.presets.pellets
    pellet.maximal_thermal_power_in_watt = 12_000.0
    resolved = pellet.resolve(SizingContext())
    assert resolved.minimal_thermal_power_in_watt == 1_000.0
    assert resolved.maximal_thermal_power_in_watt == 12_000.0


@pytest.mark.base
def test_a_sized_sibling_is_read_at_its_computed_value():
    """With both fields sized, the dependent one still reads the sibling's computed result."""
    from hisim.components.generic_boiler import GenericBoilerConfig

    resolved = GenericBoilerConfig.presets.pellets.resolve(
        SizingContext(heating_load_in_watt=8_000.0, number_of_apartments=1)
    )
    expected_max = max(8_000.0, 2_500.0) * 1.1
    assert resolved.maximal_thermal_power_in_watt == expected_max
    assert resolved.minimal_thermal_power_in_watt == expected_max * (1 / 12)


@pytest.mark.base
def test_a_law_reads_a_plain_sibling_field_at_its_overridden_value():
    """A sized field reading an ordinary field of the same config honours an override.

    Failure mode caught: the class law assuming the plain field's default while the author
    overrode it — the config would then be sized for a share of the potential nobody asked
    for, with nothing in the result pointing at the discrepancy.
    """

    @dataclass_json
    @dataclass
    class _ShareConfig(ConfigBase):
        """Fixture sizing a power from a plain share field of the same config."""

        component_id: ComponentID
        share_of_maximum_potential: float = 1.0
        power_in_watt: Sizable[float] = sized_field(
            rule=law(
                lambda ctx, own: ctx.heating_load_in_watt * own.value_of("share_of_maximum_potential"),
                reads=(Size.HEATING_LOAD_IN_WATT,),
                fields=("share_of_maximum_potential",),
            )
        )

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._ShareConfig"

    resolved = _ShareConfig(
        component_id=ComponentID(name="Pv"), share_of_maximum_potential=0.5
    ).resolve(SizingContext(heating_load_in_watt=10_000.0))
    assert resolved.power_in_watt == 5_000.0
    assert resolved.sizing_record[0].inputs == (
        ("heating_load_in_watt", 10_000.0), ("self.share_of_maximum_potential", 0.5)
    )


@pytest.mark.base
def test_two_sized_fields_reading_each_other_are_rejected_naming_both():
    """A cycle of sibling reads inside one config is a hard error naming the two fields.

    Failure mode caught: an intra-config cycle surfacing as a recursion error or as a
    field silently resolving against an unresolved sentinel, instead of an error the
    author can act on.
    """

    @dataclass_json
    @dataclass
    class _SelfCycleConfig(ConfigBase):
        """Fixture whose two sized fields each read the other."""

        component_id: ComponentID
        first_in_watt: Sizable[float] = sized_field(rule=Self("second_in_watt") * 2.0)
        second_in_watt: Sizable[float] = sized_field(rule=Self("first_in_watt") * 0.5)

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._SelfCycleConfig"

    with pytest.raises(ConfigSizingError, match="first_in_watt and second_in_watt read each other"):
        _SelfCycleConfig(component_id=ComponentID(name="Snake")).resolve(SizingContext())


@pytest.mark.base
def test_a_self_reference_to_a_field_the_class_does_not_have_is_rejected():
    """A typo in a ``Self`` field name fails with the known field names, not a silent None."""

    @dataclass_json
    @dataclass
    class _TypoConfig(ConfigBase):
        """Fixture whose sibling read names a field that does not exist."""

        component_id: ComponentID
        value_in_watt: Sizable[float] = sized_field(rule=Self("no_such_field") * 2.0)

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._TypoConfig"

    with pytest.raises(ConfigSizingError, match="reads the sibling field 'no_such_field'"):
        _TypoConfig(component_id=ComponentID(name="Typo")).resolve(SizingContext())


@pytest.mark.base
def test_the_result_does_not_depend_on_the_order_the_fields_are_declared_in():
    """The same two fields declared in either order resolve to the same two values.

    Failure mode caught: the resolver falling back to declaration order, which would make
    a purely cosmetic reordering of a config class change the numbers it produces.
    """

    @dataclass_json
    @dataclass
    class _MaxFirstConfig(ConfigBase):
        """Fixture declaring the sibling before the field that reads it."""

        component_id: ComponentID
        maximum_in_watt: Sizable[float] = sized_field(rule=1.0 * Size.HEATING_LOAD_IN_WATT)
        minimum_in_watt: Sizable[float] = sized_field(rule=Self("maximum_in_watt") * (1 / 12))

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._MaxFirstConfig"

    @dataclass_json
    @dataclass
    class _MinFirstConfig(ConfigBase):
        """Fixture declaring the reading field before the sibling it reads."""

        component_id: ComponentID
        minimum_in_watt: Sizable[float] = sized_field(rule=Self("maximum_in_watt") * (1 / 12))
        maximum_in_watt: Sizable[float] = sized_field(rule=1.0 * Size.HEATING_LOAD_IN_WATT)

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing_engine._MinFirstConfig"

    ctx = SizingContext(heating_load_in_watt=12_000.0)
    first = _MaxFirstConfig(component_id=ComponentID(name="A")).resolve(ctx)
    second = _MinFirstConfig(component_id=ComponentID(name="B")).resolve(ctx)
    assert (first.maximum_in_watt, first.minimum_in_watt) == (12_000.0, 1_000.0)
    assert (second.maximum_in_watt, second.minimum_in_watt) == (12_000.0, 1_000.0)


# ------------------------------------------------------- registration and reporting


@pytest.mark.base
def test_two_configs_with_one_instance_name_are_rejected_at_registration():
    """Two configs named alike could not be addressed or blamed separately."""
    with pytest.raises(SizingError, match="two configs named 'Twin'"):
        resolve_all([_producer("Twin"), _producer("Twin")])


@pytest.mark.base
def test_a_config_without_an_instance_name_is_rejected_at_registration():
    """A config the mapping cannot point at must not enter a resolution."""

    nameless = _producer("Nameless")
    nameless.component_id = None  # what a hand-built or half-deserialized config looks like
    with pytest.raises(SizingError, match="has no component_id.name"):
        resolve_all([nameless])


@pytest.mark.base
def test_a_contribution_must_name_only_registry_facts():
    """Declared fact names outside the SizingContext registry fail at declaration time."""
    with pytest.raises(SizingError, match="unknown fact"):
        FactContribution(facts=("no_such_fact",), compute=lambda config, ctx: {})


@pytest.mark.base
def test_the_report_groups_resolution_into_sweeps_with_waits():
    """The report shows per sweep what resolved and what was still waiting on which facts.

    Failure mode caught: a dependency-ordering regression (a config resolving one sweep
    earlier or later than intended) that changes nothing about the final values in the
    happy case but would surface as wrong values once laws read stale facts — without
    the sweep record such a regression is undetectable by value assertions alone.
    """
    engine = SizingFactEngine(seed=SizingContext(heating_load_in_watt=10_000.0))
    engine.resolve_all([_consumer("Controller"), _producer("Boiler")])
    first_sweep = engine.report.sweeps[0]
    assert "Boiler" in first_sweep.resolved
    assert dict(first_sweep.waiting)["Controller"] == ("maximal_thermal_power_in_watt",)
    assert any("Controller" in sweep.resolved for sweep in engine.report.sweeps[1:])


@pytest.mark.base
def test_the_report_lists_the_facts_nobody_read():
    """A contributed fact that no consumer bound to is recorded as unconsumed.

    Failure mode caught: a provider added to a scenario for nothing, or a consumer bound
    to a different provider than its author assumed — both leave a contributed fact
    unread, which is the one non-error condition worth surfacing after a run.
    """
    engine = SizingFactEngine(seed=SizingContext(heating_load_in_watt=10_000.0))
    engine.resolve_all([_producer("Boiler"), _FlowProducerConfig(component_id=ComponentID(name="Hds"))])
    assert ("Boiler", "maximal_thermal_power_in_watt") in engine.report.unconsumed
    assert ("Hds", "water_mass_flow_rate_in_kg_per_second") in engine.report.unconsumed
    assert ("<seed>", "heating_load_in_watt") not in engine.report.unconsumed


@pytest.mark.base
def test_the_report_serializes_to_plain_json():
    """The report's dict form is json-dumpable without custom encoders.

    Failure mode caught: the audit-artifact writer crashing at the end of an otherwise
    successful run because a report entry smuggled a non-serializable object (an enum
    member, a config instance) into the record.
    """
    engine = SizingFactEngine(seed=SizingContext(heating_load_in_watt=10_000.0))
    engine.resolve_all([_producer("Boiler"), _consumer("Controller")])
    dumped = json.dumps(engine.report.to_dict())
    assert "maximal_thermal_power_in_watt" in dumped
