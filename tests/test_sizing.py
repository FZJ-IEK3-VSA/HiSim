"""Tests of the sizing machinery (``hisim/config/laws|context|sizing.py``, design B of the config-defaults spec).

Covers the pieces the spike introduces: the copy-stable AUTO sentinel and its wire codec,
expression and function laws, the resolve semantics (no-op vs. NothingToSizeError vs.
missing-fact errors), the per-preset law escape hatch, the sizing_record provenance, the
central ``Component.__init__`` check, and the single-registry invariant between the
``Size`` terms and the ``SizingContext`` fields. The preset machinery itself is covered
by ``tests/test_presets.py``.
"""

# clean

import copy
import dataclasses
import json
from dataclasses import dataclass

import pytest
from dataclasses_json import dataclass_json

from hisim.config import (
    AUTO,
    Cardinality,
    ComponentID,
    ConfigBase,
    ConfigSizingError,
    NothingToSizeError,
    Sizable,
    Size,
    SizingContext,
    law,
    sized_field,
    sizing,
)


@dataclass_json
@dataclass
class _SizableFixtureConfig(ConfigBase):
    """A minimal config with one expression-law field and one constant-law field.

    Deliberately tiny: the machinery under test is generic over dataclass fields, so two
    sizable fields plus one ordinary field exercise every code path without dragging any
    component physics into the test.
    """

    component_id: ComponentID
    plain_value: float = 1.0
    power_in_watt: Sizable[float] = sized_field(rule=1.1 * Size.HEATING_LOAD_IN_WATT)
    floor_value_in_watt: Sizable[float] = sized_field(rule=0.0)

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing._SizableFixtureConfig"


@dataclass_json
@dataclass
class _UnsizableFixtureConfig(ConfigBase):
    """A config declaring no sizable field at all — the NothingToSizeError case."""

    component_id: ComponentID
    plain_value: float = 1.0

    @classmethod
    def get_main_classname(cls) -> str:
        """Returns a dummy classname, as the ConfigBase contract requires."""
        return "tests.test_sizing._UnsizableFixtureConfig"


def _fixture_config() -> _SizableFixtureConfig:
    """Builds the sizable fixture with both sized fields at AUTO."""
    return _SizableFixtureConfig(component_id=ComponentID(name="Fixture"))


@pytest.mark.base
def test_auto_is_a_copy_stable_singleton():
    """'is AUTO' must survive deepcopy, copy and dataclasses.replace."""
    assert copy.deepcopy(AUTO) is AUTO
    assert copy.copy(AUTO) is AUTO
    config = _fixture_config()
    replaced = dataclasses.replace(config, plain_value=2.0)
    assert replaced.power_in_watt is AUTO
    assert copy.deepcopy(config).power_in_watt is AUTO


@pytest.mark.base
def test_expression_laws_read_like_the_formula_and_name_their_facts():
    """Expression trees evaluate, describe themselves, and know the facts they read."""
    ctx = SizingContext(heating_load_in_watt=10_000.0)
    rule = (1.1 * Size.HEATING_LOAD_IN_WATT).at_least(5_000).rounded(1)
    assert rule.evaluate(ctx) == 11_000.0
    assert rule.facts_read() == (("heating_load_in_watt", Cardinality.ONE),)
    assert "Size.HEATING_LOAD_IN_WATT" in rule.describe()
    clamped = (0.1 * Size.HEATING_LOAD_IN_WATT).at_least(5_000)
    assert clamped.evaluate(ctx) == 5_000


@pytest.mark.base
def test_resolve_computes_auto_fields_and_records_provenance():
    """AUTO fields resolve by their laws; the copy carries a per-field sizing record."""
    resolved = _fixture_config().resolve(SizingContext(heating_load_in_watt=10_000.0))
    assert resolved.power_in_watt == 11_000.0
    assert resolved.floor_value_in_watt == 0.0
    fields = {entry.field: entry for entry in resolved.sizing_record}
    assert fields["power_in_watt"].value == 11_000.0
    assert fields["power_in_watt"].facts_read == ("heating_load_in_watt",)
    # provenance is an attribute, not a field: serialization and equality ignore it
    assert "sizing_record" not in resolved.to_dict()
    assert resolved == dataclasses.replace(resolved)


@pytest.mark.base
def test_the_sizing_record_captures_the_law_input_values():
    """Each record entry keeps the (fact, value) pairs its law actually read.

    Failure mode caught: a mis-sized field that cannot be diagnosed from the record
    alone — knowing that the law read ``heating_load_in_watt`` is useless for checking
    the arithmetic unless the record also says what that fact's value *was* at
    resolution time (a later context would show different numbers).
    """
    resolved = _fixture_config().resolve(SizingContext(heating_load_in_watt=10_000.0))
    fields = {entry.field: entry for entry in resolved.sizing_record}
    assert fields["power_in_watt"].inputs == (("heating_load_in_watt", 10_000.0),)
    assert fields["floor_value_in_watt"].inputs == ()  # a constant law reads nothing


@pytest.mark.base
def test_resolve_is_an_idempotent_noop_on_a_concrete_config():
    """Sizable fields exist but none is AUTO: resolve returns an equal fresh copy."""
    concrete = _SizableFixtureConfig(
        component_id=ComponentID(name="Fixture"), power_in_watt=500.0, floor_value_in_watt=1.0
    )
    resolved = concrete.resolve(SizingContext(heating_load_in_watt=10_000.0))
    assert resolved == concrete and resolved is not concrete
    assert resolved.sizing_record == ()


@pytest.mark.base
def test_resolve_on_a_class_without_sizable_fields_is_an_error():
    """Sizing a component that can never use it is a setup bug and fails loudly."""
    config = _UnsizableFixtureConfig(component_id=ComponentID(name="Fixture"))
    with pytest.raises(NothingToSizeError, match="declares no sizable field"):
        config.resolve(SizingContext(heating_load_in_watt=10_000.0))


@pytest.mark.base
def test_a_missing_context_fact_errors_naming_field_and_fact():
    """Expression laws name the absent fact, and the field it was needed for."""
    with pytest.raises(ConfigSizingError, match="power_in_watt.*heating_load_in_watt"):
        _fixture_config().resolve(SizingContext())


@pytest.mark.base
def test_a_failing_function_law_errors_naming_the_field():
    """Function laws cannot name facts; their failure is wrapped per field."""

    @dataclass_json
    @dataclass
    class _FunctionLawConfig(ConfigBase):
        """Fixture with a function law that reads an absent optional fact."""

        component_id: ComponentID
        value_in_watt: Sizable[float] = sized_field(
            rule=lambda ctx: ctx.heating_load_in_watt * 2, reads=(Size.HEATING_LOAD_IN_WATT,)
        )

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing._FunctionLawConfig"

    with pytest.raises(ConfigSizingError, match="value_in_watt"):
        _FunctionLawConfig(component_id=ComponentID(name="F")).resolve(SizingContext())


@pytest.mark.base
def test_a_preset_may_override_the_class_law_per_field():
    """A SizingLaw as field value resolves with that law instead of the declared one."""
    config = _fixture_config()
    config.floor_value_in_watt = 1 / 12 * law(lambda ctx: ctx.heating_load_in_watt, reads=(Size.HEATING_LOAD_IN_WATT,))
    resolved = config.resolve(SizingContext(heating_load_in_watt=12_000.0))
    assert resolved.floor_value_in_watt == 1_000.0


@pytest.mark.base
def test_a_sized_field_keeps_its_author_note():
    """An optional ``note=`` is stored on the field and readable without touching metadata.

    Failure mode caught: the provenance of a hard-coded constant (a standard, a
    datasheet) living only in a comment, so the audit trail can state the number but
    never where it came from.
    """

    @dataclass_json
    @dataclass
    class _NotedConfig(ConfigBase):
        """Fixture with one annotated and one unannotated sized field."""

        component_id: ComponentID
        volume_in_liter: Sizable[float] = sized_field(rule=50.0, note="VDI 4645 rule of thumb")
        power_in_watt: Sizable[float] = sized_field(rule=0.0)

        @classmethod
        def get_main_classname(cls) -> str:
            """Returns a dummy classname, as the ConfigBase contract requires."""
            return "tests.test_sizing._NotedConfig"

    assert sizing.field_notes(_NotedConfig) == {"volume_in_liter": "VDI 4645 rule of thumb"}


@pytest.mark.base
def test_auto_round_trips_through_the_wire_spelling():
    """to_dict writes "AUTO"; from_dict restores the sentinel; concrete values pass."""
    dumped = _fixture_config().to_dict()
    assert dumped["power_in_watt"] == "AUTO"
    json.dumps(dumped)  # json-serializable as-is
    restored = _SizableFixtureConfig.from_dict(dumped)
    assert restored.power_in_watt is AUTO
    concrete = _SizableFixtureConfig(component_id=ComponentID(name="F"), power_in_watt=750.0)
    assert _SizableFixtureConfig.from_dict(concrete.to_dict()).power_in_watt == 750.0


@pytest.mark.base
def test_size_terms_and_sizing_context_fields_are_one_registry():
    """Every context fact has exactly one Size term and vice versa.

    Failure mode caught: someone adds a SizingContext field without its Size term (laws
    cannot read the new fact) or a Size term without its field (the term reads garbage) —
    the two vocabularies silently drifting apart.
    """
    term_names = {name for name in vars(Size) if name.isupper()}
    field_names = {field.name.upper() for field in dataclasses.fields(SizingContext)}
    assert term_names == field_names
    for field in dataclasses.fields(SizingContext):
        term = getattr(Size, field.name.upper())
        assert term.facts_read() == ((field.name, Cardinality.ONE),)


@pytest.mark.base
def test_for_building_snapshots_the_derived_building_facts():
    """for_building runs the TABULA lookup once and fills the building-scope facts."""
    from hisim.components.building import BuildingConfig

    ctx = SizingContext.for_building(BuildingConfig.preset_standard("Building"))
    assert ctx.heating_load_in_watt is not None and ctx.heating_load_in_watt > 0
    assert ctx.number_of_apartments == 1
    assert ctx.conditioned_floor_area_in_m2 == pytest.approx(121.2)
    enriched = ctx.with_facts(water_mass_flow_rate_in_kg_per_second=0.27)
    assert enriched.water_mass_flow_rate_in_kg_per_second == 0.27
    assert ctx.water_mass_flow_rate_in_kg_per_second is None


@pytest.mark.base
def test_the_component_init_check_rejects_unresolved_configs():
    """A config still carrying AUTO must never reach a component, with a lawful error."""
    from hisim.components.generic_boiler import GenericBoiler, GenericBoilerConfig
    from hisim.simulationparameters import SimulationParameters

    parameters = SimulationParameters.one_day_only(2021, 3600)
    unresolved = GenericBoilerConfig.preset_condensing_gas("CondensingGasBoiler")
    with pytest.raises(ConfigSizingError, match=r"requires sizing in 2 field\(s\)"):
        GenericBoiler(config=unresolved, my_simulation_parameters=parameters)


@pytest.mark.base
def test_boiler_presets_reproduce_the_former_factory_values():
    """The pilot conversion is value-neutral: presets resolve to the old factory numbers."""
    from hisim.components.generic_boiler import GenericBoilerConfig

    ctx = SizingContext(heating_load_in_watt=8_000.0, number_of_apartments=1)
    expected_max = max(8_000.0, 2_500.0 * 1) * 1.1  # the old scale_thermal_power
    scaled = GenericBoilerConfig.preset_condensing_gas("CondensingGasBoiler").resolve(ctx)
    assert scaled.maximal_thermal_power_in_watt == expected_max
    assert scaled.minimal_thermal_power_in_watt == 0.0
    pellet = GenericBoilerConfig.preset_pellets("ConventionalPelletBoiler").resolve(ctx)
    assert pellet.minimal_thermal_power_in_watt == 1 / 12 * expected_max
    nominal = GenericBoilerConfig.preset_condensing_gas_12kw("CondensingGasBoiler")
    assert nominal.maximal_thermal_power_in_watt == 12_000.0
    assert nominal.minimal_thermal_power_in_watt == 1_000.0
    assert not sizing.auto_fields(nominal)


@pytest.mark.base
def test_hds_preset_is_sizing_mandatory_and_enum_typed():
    """The heat distribution preset resolves its enum fact and rounds the mass flow."""
    from hisim.components.heat_distribution_system import (
        HeatDistributionConfig,
        HeatDistributionSystemType,
    )

    ctx = SizingContext(
        water_mass_flow_rate_in_kg_per_second=0.2712,
        conditioned_floor_area_in_m2=121.2,
        heat_distribution_system_type=HeatDistributionSystemType.FLOORHEATING,
    )
    resolved = HeatDistributionConfig.preset_standard("HeatDistributionSystem").resolve(ctx)
    assert resolved.water_mass_flow_rate_in_kg_per_second == 0.27  # the old factory's round(.., 2)
    assert resolved.heating_system is HeatDistributionSystemType.FLOORHEATING
    # enum-typed sizable field round-trips as a member, thanks to value_type
    restored = HeatDistributionConfig.from_dict(resolved.to_dict())
    assert restored.heating_system is HeatDistributionSystemType.FLOORHEATING


@pytest.mark.base
def test_ems_preset_has_nothing_to_size():
    """The EMS is the IGNORED case: resolve raises instead of silently no-opping."""
    from hisim.components.controller_l2_energy_management_system import EMSConfig

    config = EMSConfig.preset_optimize_own_consumption("L2EMSElectricityController")
    assert config.strategy == "optimize_own_consumption"
    with pytest.raises(NothingToSizeError):
        config.resolve(SizingContext(heating_load_in_watt=10_000.0))
