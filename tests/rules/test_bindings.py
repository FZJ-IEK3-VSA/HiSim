"""Direct tests of the binding rules: each rule on a file whose entries break it and on one whose entries do not.

The files are written as YAML text around converted classes (a building, a gas boiler, a weather), bound by the real
binder and configured by the real configurator, so that each rule sees what a run of the file would hand it. Further
tests pin what the rules report together, the location a fault carries, and the helpers the declarative layer gained
when its checks moved here: the binder's recorded refusals, the refusal record, the violation's catalogue parts, the
configuring stage's outcome and the hooks that raise.
"""

from types import SimpleNamespace
from typing import Sequence, Type

import pytest

from hisim.config.introspection import describe_config
from hisim.components.building import BuildingConfig
from hisim.energy_system.assemblies.record import SourceLocation, SourceMap, SourceMapEntry
from hisim.energy_system.bindings import ClassBindings
from hisim.energy_system.classes import ClassBinder, collect_class_failures, validate_classes
from hisim.energy_system.configure import ConfigurationOutcome, EntryRefusal, configure_energy_system
from hisim.energy_system.errors import (
    CatalogueRefusal,
    CatalogueViolation,
    EnergySystemBindingError,
    EnergySystemClassBindingError,
    EnergySystemErrorId,
    EnergySystemFormatError,
)
from hisim.energy_system.path_resolver import PathResolver
from hisim.setup_rules.bindings import (
    AutoOnlyOnASizedField,
    BindingRule,
    CatalogueFaults,
    ClassImportable,
    ConfigurationRule,
    ConstructorArguments,
    DecodableValue,
    KnownConfigField,
    KnownConstructor,
    KnownPreset,
    PathVariableKnown,
    SizingFactIsRead,
)
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule
from hisim.setup_rules.subjects import BoundDocument, ConfiguredDocument
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class Entries:
    """The entries the binding-rule tests are written from: converted classes, each configured as its class allows."""

    WEATHER = """  weather:
    class: hisim.components.weather.Weather
    preset: aachen
"""
    BUILDING = """  building:
    class: hisim.components.building.Building
    preset: german_single_family_home
"""
    BOILER = """  boiler:
    class: hisim.components.generic_boiler.GenericBoiler
    preset: condensing_gas
"""
    #: Every rule of the bound file accepts these entries, and the configurator builds and sizes them.
    CLEAN = WEATHER + BUILDING + BOILER

    @staticmethod
    def boiler(extra: str) -> str:
        """The boiler entry with the given lines added under it, indented by four spaces."""
        return Entries.BOILER + extra


class Check:
    """Runs one rule on a subject and returns its faults."""

    @staticmethod
    def faults(rule: Type[SetupRule], subject: object) -> Sequence[Fault]:
        """The faults one rule finds in the subject."""
        return rule().check(subject)


def test_class_importable_refuses_a_module_that_does_not_exist_and_accepts_converted_classes() -> None:
    """Catches a missing module reported as a bare ``ModuleNotFoundError``, or a clean file refused."""
    faults = Check.faults(ClassImportable, fft.BindingRules.bound("  ghost:\n    class: hisim.components.no_such_module.Ghost\n    preset: standard\n"))

    assert [fault.component for fault in faults] == ["ghost"]
    assert faults[0].message.startswith("EF-10 at components.ghost.class: the module 'hisim.components.no_such_module' of 'ghost' cannot be imported")
    assert faults[0].key_path == ("components", "ghost", "class")
    assert not Check.faults(ClassImportable, fft.BindingRules.bound(Entries.CLEAN))


def test_class_importable_refuses_a_configuration_class_named_as_the_component() -> None:
    """Catches an entry naming the configuration class being accepted as a component."""
    entry = "  boiler:\n    class: hisim.components.generic_boiler.GenericBoilerConfig\n    preset: condensing_gas\n"
    faults = Check.faults(ClassImportable, fft.BindingRules.bound(entry))

    assert len(faults) == 1 and "is not a HiSim component class" in faults[0].message and "GenericBoiler" in faults[0].message


def test_known_preset_refuses_an_undeclared_preset_listing_the_declared_ones() -> None:
    """Catches a misspelled preset reported without the presets the class declares."""
    faults = Check.faults(KnownPreset, fft.BindingRules.bound(Entries.BUILDING + Entries.BOILER.replace("condensing_gas", "condensing_gaz")))

    assert [fault.component for fault in faults] == ["boiler"]
    assert "'boiler' names the preset 'condensing_gaz', which GenericBoilerConfig does not declare." in faults[0].message
    assert "Valid presets of GenericBoilerConfig: condensing_gas" in faults[0].message
    assert not Check.faults(KnownPreset, fft.BindingRules.bound(Entries.CLEAN))


def test_known_constructor_refuses_an_undeclared_constructor() -> None:
    """Catches a misspelled constructor reported without the constructors the class declares."""
    entry = "  building:\n    class: hisim.components.building.Building\n    constructor:\n      for_tabula:\n        building_code: DE.N.SFH.05.Gen.ReEx.001.002\n"
    faults = Check.faults(KnownConstructor, fft.BindingRules.bound(entry))

    assert len(faults) == 1 and "'building' calls the constructor 'for_tabula'" in faults[0].message and "for_tabula_code" in faults[0].message
    assert faults[0].key_path == ("components", "building", "constructor")
    assert not Check.faults(KnownConstructor, fft.BindingRules.bound(entry.replace("for_tabula:", "for_tabula_code:")))


def test_constructor_arguments_refuses_an_unknown_and_a_missing_argument_of_one_call_at_once() -> None:
    """Catches the arguments of a call reported one fault per run, or a call that fits its parameters refused."""
    entry = "  building:\n    class: hisim.components.building.Building\n    constructor:\n      for_tabula_code:\n        tabula_code: DE.N.SFH.05.Gen.ReEx.001.002\n"
    faults = Check.faults(ConstructorArguments, fft.BindingRules.bound(entry))

    assert len(faults) == 2
    assert "got the unknown argument tabula_code." in faults[0].message and "building_code: str" in faults[0].message
    assert "needs the argument building_code, which the entry does not pass." in faults[1].message
    assert not Check.faults(ConstructorArguments, fft.BindingRules.bound(entry.replace("tabula_code:", "building_code:")))


def test_constructor_arguments_leaves_the_arguments_of_an_undeclared_constructor_alone() -> None:
    """Catches a call of a constructor the class lacks reported twice, once as ``EF-15`` and again as ``EF-16``."""
    entry = "  building:\n    class: hisim.components.building.Building\n    constructor:\n      for_tabula:\n        tabula_code: x\n"

    assert not Check.faults(ConstructorArguments, fft.BindingRules.bound(entry))


def test_known_config_field_refuses_an_unknown_key_and_component_id() -> None:
    """Catches a misspelled field or a second spelling of the identity accepted in a ``config`` block."""
    faults = Check.faults(KnownConfigField, fft.BindingRules.bound(Entries.boiler("    config:\n      seasonal_eficiency: 0.9\n      component_id: other\n")))

    assert [fault.key_path for fault in faults] == [
        ("components", "boiler", "config", "seasonal_eficiency"),
        ("components", "boiler", "config", "component_id"),
    ]
    assert "Did you mean: seasonal_efficiency?" in faults[0].message
    assert "the entry's key is the component's identity" in faults[1].message
    assert not Check.faults(KnownConfigField, fft.BindingRules.bound(Entries.boiler("    config:\n      seasonal_efficiency: 0.9\n")))


def test_auto_only_on_a_sized_field_refuses_auto_on_a_field_without_a_law() -> None:
    """Catches ``AUTO`` accepted on a field nothing would ever fill in, or refused on a field a law sizes."""
    faults = Check.faults(AutoOnlyOnASizedField, fft.BindingRules.bound(Entries.boiler("    config:\n      seasonal_efficiency: AUTO\n")))

    assert len(faults) == 1 and "'boiler' sets 'seasonal_efficiency' to AUTO" in faults[0].message
    assert "maximal_thermal_power_in_watt" in faults[0].message
    sized = fft.BindingRules.bound(Entries.boiler("    config:\n      maximal_thermal_power_in_watt: AUTO\n"))
    assert not Check.faults(AutoOnlyOnASizedField, sized)


def test_auto_only_on_a_sized_field_leaves_an_unknown_key_to_the_field_rule() -> None:
    """Catches ``AUTO`` on a key that is no field reported twice, as ``EF-17`` and again as ``EF-1B``."""
    assert not Check.faults(AutoOnlyOnASizedField, fft.BindingRules.bound(Entries.boiler("    config:\n      no_such_field: AUTO\n")))


def test_sizing_fact_is_read_refuses_a_source_for_a_fact_the_class_does_not_read() -> None:
    """Catches a ``sizing_sources`` line for a fact nobody reads silently ignored, or a line for a read fact refused."""
    unread = Entries.boiler("    sizing_sources:\n      conditioned_floor_area_in_m2: building.conditioned_floor_area_in_m2\n")
    faults = Check.faults(SizingFactIsRead, fft.BindingRules.bound(Entries.BUILDING + unread))

    assert len(faults) == 1 and "which GenericBoilerConfig never reads" in faults[0].message
    assert "heating_load_in_watt" in faults[0].message
    read = Entries.boiler("    sizing_sources:\n      heating_load_in_watt: building.heating_load_in_watt\n")
    assert not Check.faults(SizingFactIsRead, fft.BindingRules.bound(Entries.BUILDING + read))


def test_decodable_value_refuses_every_entry_with_a_value_that_does_not_fit() -> None:
    """Catches an undecodable value reported one entry per run, or a value that fits refused."""
    hds = (
        "  hds:\n    class: hisim.components.heat_distribution_system.HeatDistribution\n    preset: building_derived\n"
        "    config:\n      heating_system: FLOOR_HEATING\n"
    )
    subject = fft.BindingRules.configured(Entries.boiler("    config:\n      seasonal_efficiency: very high\n") + hds)
    faults = Check.faults(DecodableValue, subject)

    assert [fault.component for fault in faults] == ["boiler", "hds"]
    assert faults[0].message.startswith("EF-1A at components.boiler.config.seasonal_efficiency: 'boiler' sets 'seasonal_efficiency'")
    assert "FLOORHEATING" in faults[1].message
    assert subject.outcome.sizing is None
    assert not Check.faults(DecodableValue, fft.BindingRules.configured(Entries.CLEAN))


def test_path_variable_known_refuses_an_unregistered_variable() -> None:
    """Catches a path through a variable nobody registered reaching a component, or a registered one refused."""
    weather = Entries.WEATHER + "    config:\n      source_path: ${nowhere}/weather\n"
    faults = Check.faults(PathVariableKnown, fft.BindingRules.configured(weather))

    assert len(faults) == 1 and "the path reference '${nowhere}' names no registered path variable." in faults[0].message
    assert faults[0].component == "weather" and faults[0].key_path == ("components", "weather")
    registered = fft.BindingRules.configured(weather, PathResolver({**PathResolver.get_default_roots(), "nowhere": "/tmp"}))
    assert not Check.faults(PathVariableKnown, registered)


def test_the_rules_of_the_bound_file_report_every_fault_of_every_entry_at_once() -> None:
    """Catches the binding rules stopping at the first entry or the first check, as the binder did before they moved."""
    entries = (
        "  ghost:\n    class: hisim.components.no_such_module.Ghost\n    preset: standard\n"
        + Entries.BOILER.replace("condensing_gas", "condensing_gaz")
        + "    config:\n      seasonal_eficiency: 0.9\n"
    )
    findings = fft.BindingRules.findings(fft.BindingRules.bound(entries))

    assert [fault.code for fault in findings.faults] == ["EF-10", "EF-13", "EF-17"]
    assert findings.violation_type is EnergySystemClassBindingError


def test_validate_classes_raises_every_fault_as_a_binding_error_from_the_import_error() -> None:
    """Catches the hook losing the type an ``except EnergySystemBindingError`` catches, the first fault's identifier or the import error."""
    model = fft.BindingRules.file("  ghost:\n    class: hisim.components.no_such_module.Ghost\n    preset: standard\n" + Entries.BOILER.replace("condensing_gas", "x"))

    with pytest.raises(EnergySystemBindingError) as raised:
        validate_classes(model, file="house.energy_system.yaml")

    assert isinstance(raised.value, EnergySystemClassBindingError)
    assert raised.value.error_id is EnergySystemErrorId.CLASS_NOT_IMPORTABLE and raised.value.location == "components.ghost.class"
    assert [fault.code for fault in raised.value.faults] == ["EF-10", "EF-13"]
    assert raised.value.faults[0].file == "house.energy_system.yaml"
    assert isinstance(raised.value.__cause__, ImportError)
    assert validate_classes(fft.BindingRules.file(Entries.CLEAN)).names() == ("weather", "building", "boiler")


def test_configure_energy_system_raises_the_undecodable_entries_from_the_first_error() -> None:
    """Catches the configuring hook losing the binding error's type or the error the configurator raised."""
    model = fft.BindingRules.file(Entries.CLEAN + "    config:\n      seasonal_efficiency: very high\n")

    with pytest.raises(EnergySystemClassBindingError) as raised:
        configure_energy_system(model)

    assert raised.value.error_id is EnergySystemErrorId.UNDECODABLE_VALUE
    assert isinstance(raised.value.__cause__, EnergySystemBindingError)
    assert [skip.code for skip in raised.value.skipped][:2] == ["EF-4A", "EF-4B"]


def test_a_fault_of_a_component_an_import_produced_names_its_source() -> None:
    """Catches the source of an imported component lost now that the rules, not the executor, build the message."""
    source_map = SourceMap()
    source_map.add("boiler", "component", SourceMapEntry("burner", None, "Boiler", (SourceLocation("house.yaml", 3),)))
    bound = fft.BindingRules.bound(Entries.BOILER.replace("condensing_gas", "x"))
    subject = BoundDocument(model=bound.model, bindings=bound.bindings, source_map=source_map)

    faults = Check.faults(KnownPreset, subject)

    assert len(faults) == 1 and "[source: boiler (import burner, house.yaml:3)]; see: hisim setup-rules show EF-13" in faults[0].message


def test_catalogue_faults_locate_a_key_path_or_the_entry_named() -> None:
    """Catches a fault outside the components block given a wrong component, or one inside it losing its key path."""
    inside = CatalogueFaults.fault(KnownPreset, "EF-13 at components.boiler.preset: x", "components.boiler.preset", "f.yaml", None)
    outside = CatalogueFaults.fault(PathVariableKnown, "EF-04 at ${x}/y: z", "${x}/y", None, None, component="weather")
    nowhere = CatalogueFaults.fault(PathVariableKnown, "EF-04 at ${x}/y: z", "${x}/y", None, None)

    assert (inside.component, inside.key_path, inside.file) == ("boiler", ("components", "boiler", "preset"), "f.yaml")
    assert (outside.component, outside.key_path) == ("weather", ("components", "weather"))
    assert (nowhere.component, nowhere.key_path) == (None, ())


def test_location_of_is_the_entrys_key_path() -> None:
    """Catches a rule's message naming another key path than the entry's."""
    binding = SimpleNamespace(name="boiler")

    assert BindingRule.location_of(binding) == "components.boiler"  # type: ignore[arg-type]


def test_render_parameters_writes_each_parameter_with_its_type_and_default() -> None:
    """Catches the parameter list of an ``EF-16`` message losing a type or a default."""
    info = next(item for item in describe_config(BuildingConfig).constructors if item.name == "for_tabula_code")

    rendered = ConstructorArguments.render_parameters(info)

    assert "building_code: str" in rendered and "building_heat_capacity_class: str = 'medium'" in rendered


def test_refusals_of_a_call_are_empty_when_the_call_fits() -> None:
    """Catches a refusal of a call whose arguments are exactly the required parameters."""
    info = next(item for item in describe_config(BuildingConfig).constructors if item.name == "for_tabula_code")

    assert not ConstructorArguments.refusals("for_tabula_code", {"building_code": "x"}, info, "components.b.constructor.for_tabula_code", "b")
    assert len(ConstructorArguments.refusals("for_tabula_code", {"nonsense": 1}, info, "components.b.constructor.for_tabula_code", "b")) == 2


def test_the_binder_records_an_unresolved_class_and_leaves_an_undeclared_preset_unresolved() -> None:
    """Catches the binder raising again, or resolving a preset the class does not declare."""
    bindings = ClassBinder(fft.BindingRules.file("  ghost:\n    class: Ghost\n" + Entries.BOILER.replace("condensing_gas", "x"))).bind()

    assert bindings.names() == ("boiler",) and bindings["boiler"].preset is None
    assert [refusal.location for refusal in bindings.refusals] == ["components.ghost.class"]
    assert "is not a dotted path" in bindings.refusals[0].message


def test_config_class_of_raises_the_binding_error_of_an_unresolved_class() -> None:
    """Catches the one-entry lookup of the assemblies and the recorder no longer refusing a bad class path."""
    model = fft.BindingRules.file("  ghost:\n    class: hisim.components.no_such_module.Ghost\n    preset: x\n" + Entries.BOILER)

    with pytest.raises(EnergySystemBindingError) as raised:
        ClassBinder.config_class_of("ghost", model.components["ghost"])

    assert not isinstance(raised.value, CatalogueViolation) and raised.value.error_id is EnergySystemErrorId.CLASS_NOT_IMPORTABLE
    assert ClassBinder.config_class_of("boiler", model.components["boiler"]).__name__ == "GenericBoilerConfig"
    assert isinstance(ClassBinder.resolve_classes("ghost", model.components["ghost"]), CatalogueRefusal)


def test_collect_class_failures_gives_each_failing_entry_its_fault_of_the_lowest_code() -> None:
    """Catches the per-entry verdict reporting a later check of an entry, or an entry twice."""
    entries = Entries.BOILER.replace("condensing_gas", "x") + "    sizing_sources:\n      conditioned_floor_area_in_m2: building.x\n" + Entries.BUILDING

    failures = collect_class_failures(fft.BindingRules.file(entries))

    assert [(failure.name, failure.error_id) for failure in failures] == [("boiler", EnergySystemErrorId.UNKNOWN_PRESET)]


def test_a_catalogue_refusal_gives_its_message_component_and_error() -> None:
    """Catches the refusal record building another message than the error it stands for."""
    refusal = CatalogueRefusal(EnergySystemErrorId.UNKNOWN_PRESET, "components.boiler.preset", "no such preset.", alternatives=("a", "b"), offending_value="a1")

    error = refusal.error(EnergySystemBindingError)

    assert str(error) == refusal.message == "EF-13 at components.boiler.preset: no such preset. Did you mean: a? Valid values: a, b."
    assert refusal.component == "boiler"
    assert CatalogueRefusal(EnergySystemErrorId.UNRESOLVABLE_PATH_VARIABLE, "${x}/y", "p").component is None


def test_catalogue_parts_split_a_fault_message_and_refuse_another_message() -> None:
    """Catches ``error_id``, ``location`` or ``problem`` of a violation read wrongly from its first fault."""
    parts = CatalogueViolation.catalogue_parts("EF-13 at components.boiler.preset: 'boiler' names x.; see: hisim setup-rules show EF-13")

    assert parts == (EnergySystemErrorId.UNKNOWN_PRESET, "components.boiler.preset", "'boiler' names x.")
    with pytest.raises(ValueError, match="not a message of the energy-system catalogue"):
        CatalogueViolation.catalogue_parts("something else")


def test_the_configuration_outcome_records_refusals_and_its_cause_and_refuses_to_configure() -> None:
    """Catches a refused outcome handed on as a configured system, or its cause lost."""
    error = EnergySystemFormatError(EnergySystemErrorId.UNRESOLVABLE_PATH_VARIABLE, "${x}/y", "p")
    outcome = ConfigurationOutcome(bindings=ClassBindings({}), names=(), origins=(), refusals=(EntryRefusal("weather", error),), sizing=None)

    assert outcome.cause() is error
    with pytest.raises(ValueError, match="refused by the setup rules"):
        outcome.configured()


def test_the_outcome_of_a_configured_system_holds_its_sizing_and_configures_it_again() -> None:
    """Catches the outcome the check command hands the rules again differing from the configured system."""
    configured = configure_energy_system(fft.BindingRules.file(Entries.CLEAN))

    outcome = ConfigurationOutcome.of_configured(configured)

    assert not outcome.refusals and outcome.cause() is None and outcome.names == ("weather", "building", "boiler")
    assert outcome.configured().configs == configured.configs and outcome.configured().warnings == configured.warnings


def test_the_configuration_rules_raise_as_binding_errors_of_the_configured_file() -> None:
    """Catches the rules of the configured file reading another subject or raising another error."""
    assert ConfigurationRule.subject_type() is ConfiguredDocument and BindingRule.subject_type() is BoundDocument
    assert ConfigurationRule.VIOLATION is EnergySystemClassBindingError is BindingRule.VIOLATION
    assert all(rule.STAGE is RuleStage.BUILD for rule in (DecodableValue, PathVariableKnown, ClassImportable))


def test_the_family_runs_alone_on_a_clean_file_without_a_fault() -> None:
    """Catches a rule of the family refusing a file every run accepts."""
    for subject in (fft.BindingRules.bound(Entries.CLEAN), fft.BindingRules.configured(Entries.CLEAN)):
        findings = fft.BindingRules.registry().findings(RuleStage.BUILD, subject, SetupKind.RESIDENTIAL)
        assert not findings.faults and not findings.skipped
