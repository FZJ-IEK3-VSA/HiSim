"""Contract tests over every config class in ``hisim.components`` that declares sizing.

These are repository-wide invariants rather than tests of one module: they scan every
component module, collect the config classes that declare a named builder or contribute
sizing facts, and check the promises that only break when two *different* modules disagree
— a fact name claimed by two unrelated classes, a preset that cannot be built, a sibling
read that names a field nobody declares, a preset name that is not in wire format.

A single module of ``hisim.components`` failing to import (an optional third-party
dependency, typically) must not silently shrink the scanned set, so the import failures are
collected and reported by their own test instead of being swallowed.
"""

# clean

import dataclasses
import importlib
import inspect
import pkgutil
import re
from typing import Any, Dict, List, Set, Tuple

import pytest

from hisim.config import (
    Cardinality,
    FactContribution,
    SizingLaw,
    constructors_of,
    presets_of,
    sizable_fields,
)
from hisim.config.sizing import _resolution_order  # the kernel's intra-config ordering check


class ComponentConfigScan:
    """The one scan of ``hisim.components`` these contract tests share.

    Importing every component module takes a moment and has import-time side effects
    (config classes assign their ``SIZING_CONTRIBUTIONS`` on import), so it happens once
    here and the results — the config classes found and the modules that refused to import
    — are handed to the individual tests.
    """

    #: Instance name handed to a preset builder purely to check that it builds and that it
    #: uses the argument; deliberately unmistakable, so a leak into a result is obvious.
    PROBE_NAME: str = "ContractProbe"

    #: Third-party packages HiSim does not require: a component module that imports one of
    #: them may legitimately be missing from the scan on a machine without it. Anything else
    #: failing to import is a defect, not an environment.
    OPTIONAL_DEPENDENCIES: Tuple[str, ...] = ("wetterdienst",)

    #: Rule 1 of the preset naming convention: a wire name is snake_case, lower-case words
    #: joined by single underscores. Checked separately from :attr:`RATING_SUFFIX` so that a
    #: capitalised name and a name full of digits fail with different messages.
    SNAKE_CASE: "re.Pattern[str]" = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

    #: Rule 2: a wire name carries no digits except in one trailing rating suffix, which
    #: marks a concrete catalogue device (``oil_12kw``, ``standard_5kwh``). Anything else
    #: numeric in a name is a value that belongs in a field, not in the wire format.
    RATING_SUFFIX: "re.Pattern[str]" = re.compile(r"^[a-z]+(_[a-z]+)*(_[0-9]+[a-z]+)?$")

    #: Rule 5 (with amendment A1 of the naming supplement): ``standard`` is reserved for a
    #: class with exactly one defensible preset, and survives a second one only when that
    #: sibling is its own rating variant, ``standard_<rating>``.
    STANDARD_NAME: str = "standard"

    @staticmethod
    def collect() -> Tuple[List[type], List[Tuple[str, str]]]:
        """Imports every component module and returns its sizing-declaring config classes.

        Returns:
            The config classes declaring a preset, a named constructor or a fact
            contribution, in a stable order, together with ``(module, error)`` pairs for the
            modules that could not be imported at all.
        """
        import hisim.components as components_package

        failures: List[Tuple[str, str]] = []
        modules = []
        for info in pkgutil.walk_packages(components_package.__path__, components_package.__name__ + "."):
            try:
                modules.append(importlib.import_module(info.name))
            except Exception as error:  # pylint: disable=broad-except
                failures.append((info.name, f"{type(error).__name__}: {error}"))
        found: Dict[str, type] = {}
        for module in modules:
            for _name, candidate in inspect.getmembers(module, inspect.isclass):
                if not dataclasses.is_dataclass(candidate):
                    continue
                if candidate.__module__ != module.__name__:
                    continue
                has_builders = bool(presets_of(candidate)) or bool(constructors_of(candidate))
                has_facts = bool(getattr(candidate, FactContribution.CLASS_ATTRIBUTE, ()))
                if has_builders or has_facts:
                    found[f"{candidate.__module__}.{candidate.__qualname__}"] = candidate
        return [found[key] for key in sorted(found)], failures

    @staticmethod
    def laws_of(config_class: type) -> Dict[str, SizingLaw]:
        """All laws that can govern a field of the class: the declared ones plus preset overrides.

        A preset may replace one field's law by assigning a ``SizingLaw`` as the field value,
        so checking only the class declarations would miss exactly the laws an author is
        most likely to get wrong.
        """
        laws = dict(sizable_fields(config_class))
        for name, builder in presets_of(config_class).items():
            instance = builder.build(ComponentConfigScan.PROBE_NAME)
            for field_name in sizable_fields(config_class):
                value = getattr(instance, field_name)
                if isinstance(value, SizingLaw):
                    laws[f"{field_name}@{name}"] = value
        return laws


class InterchangeableProviders:
    """The facts that more than one config class is *allowed* to declare, and by whom.

    Two classes declaring the same fact name is normally a collision: a scenario holding
    both would force every consumer to say which one it means, for two facts that only look
    alike. The exception is a family of interchangeable devices — a heating generator is a
    heating generator, whichever fuel it burns — and that exception must be written down
    here rather than discovered when a scenario stops resolving.
    """

    #: Fact name → the config classes sanctioned to declare it. The heating-generator
    #: family shares its power band because exactly one of its members is present in a
    #: scenario and every consumer of the band is indifferent to which. Today only the
    #: boiler family is converted; the heat pump, electric and district heating generators
    #: join this entry as they are converted.
    ALLOWED: Dict[str, Set[str]] = {
        "maximal_thermal_power_in_watt": {"GenericBoilerConfig"},
        "minimal_thermal_power_in_watt": {"GenericBoilerConfig"},
    }


class PilotWireFormat:
    """The literal preset, constructor and fact names of the four converted pilot classes.

    Preset names, constructor names and fact names are wire format: a scenario file spells
    them out, so a rename is a breaking change for every file already written. Repeating them here as
    literals makes a rename fail loudly in one obvious place instead of silently changing
    what a stored scenario means.
    """

    #: Config class name → its preset names in declaration order, canonical first.
    PRESET_NAMES: Dict[str, Tuple[str, ...]] = {
        "GenericBoilerConfig": (
            "condensing_gas",
            "condensing_gas_12kw",
            "oil",
            "oil_12kw",
            "pellets",
            "wood_chips",
            "hydrogen",
        ),
        "HeatDistributionConfig": ("standard",),
        "EMSConfig": ("optimize_own_consumption",),
        "BuildingConfig": ("standard",),
    }

    #: Config class name → its named constructors, in declaration order. A constructor's
    #: wire name is its full method name, so it is as much a breaking change to rename as a
    #: preset name is.
    CONSTRUCTOR_NAMES: Dict[str, Tuple[str, ...]] = {
        "GenericBoilerConfig": (),
        "HeatDistributionConfig": (),
        "EMSConfig": (),
        "BuildingConfig": ("for_tabula_code",),
    }

    #: The scanned classes that legitimately ship no preset at all. Today only the heat
    #: distribution controller, which is in the scan because it contributes facts and whose
    #: configuration a setup always builds from the building it serves.
    CLASSES_WITHOUT_PRESETS: Tuple[str, ...] = ("HeatDistributionControllerConfig",)

    #: Config class name → the facts it contributes, in declaration order.
    FACT_NAMES: Dict[str, Tuple[str, ...]] = {
        "GenericBoilerConfig": ("maximal_thermal_power_in_watt", "minimal_thermal_power_in_watt"),
        "HeatDistributionControllerConfig": (
            "water_mass_flow_rate_in_kg_per_second",
            "heat_distribution_system_type",
        ),
        "BuildingConfig": (
            "heating_load_in_watt",
            "number_of_apartments",
            "conditioned_floor_area_in_m2",
            "heating_reference_temperature_in_celsius",
        ),
    }


@pytest.fixture(name="scan", scope="module")
def fixture_scan() -> Tuple[List[type], List[Tuple[str, str]]]:
    """Runs the component scan once for the whole module."""
    return ComponentConfigScan.collect()


@pytest.mark.base
def test_every_component_module_imports(scan):
    """No component module fails to import except for a missing optional dependency.

    Failure mode caught: a module whose import breaks (a syntax error, a renamed symbol, a
    stale import) dropping its config classes out of every contract check below, so those
    checks keep passing while covering less and less. A module that needs a third-party
    package HiSim does not require is tolerated, because its absence is an environment and
    not a defect.
    """
    _classes, failures = scan
    unexpected = [
        f"{name}: {error}"
        for name, error in failures
        if not any(dependency in error for dependency in ComponentConfigScan.OPTIONAL_DEPENDENCIES)
    ]
    assert unexpected == [], "component modules failed to import: " + "; ".join(unexpected)


@pytest.mark.base
def test_the_scan_finds_the_converted_pilot_classes(scan):
    """The scan really sees the classes it is supposed to check.

    Failure mode caught: a scan that quietly matches nothing — every other contract test in
    this module would then pass over an empty set and prove nothing at all.
    """
    classes, _failures = scan
    names = {config_class.__name__ for config_class in classes}
    assert {"GenericBoilerConfig", "HeatDistributionConfig", "EMSConfig", "BuildingConfig"} <= names


@pytest.mark.base
def test_no_two_classes_declare_the_same_fact_unless_they_are_interchangeable(scan):
    """A fact name is owned by one class, or by an explicitly sanctioned family.

    Failure mode caught: two unrelated classes computing different things under one fact
    name. A consumer would then be forced to disambiguate between two facts that were never
    the same quantity, and a scenario holding only one of them would bind to the wrong one.
    """
    classes, _failures = scan
    declarers: Dict[str, Set[str]] = {}
    for config_class in classes:
        for contribution in getattr(config_class, FactContribution.CLASS_ATTRIBUTE, ()):
            for fact in contribution.facts:
                declarers.setdefault(fact, set()).add(config_class.__name__)
    collisions = {fact: names for fact, names in declarers.items() if len(names) > 1}
    for fact, names in collisions.items():
        allowed = InterchangeableProviders.ALLOWED.get(fact)
        assert allowed is not None, (
            f"'{fact}' is declared by {sorted(names)}; either rename one of them or list the "
            "fact in InterchangeableProviders.ALLOWED with the family that shares it."
        )
        assert names <= allowed, f"'{fact}' is declared by {sorted(names - allowed)}, which is not in the family"


@pytest.mark.base
def test_no_law_reads_one_and_many_of_the_same_fact(scan):
    """A law never asks for a single provider of a fact and for all of them at once.

    Failure mode caught: a law whose two reads of one fact mean different things — the
    engine would have to bind the same name twice with different cardinalities, and the
    author almost certainly meant only one of them.
    """
    for config_class in scan[0]:
        for field_name, sizing_law in ComponentConfigScan.laws_of(config_class).items():
            cardinalities: Dict[str, Set[Cardinality]] = {}
            for fact, cardinality in sizing_law.facts_read():
                cardinalities.setdefault(fact, set()).add(cardinality)
            both = [fact for fact, kinds in cardinalities.items() if len(kinds) > 1]
            assert both == [], f"{config_class.__name__}.{field_name} reads {both} with two cardinalities"


@pytest.mark.base
def test_every_sibling_read_names_a_real_field_and_no_config_has_a_read_cycle(scan):
    """``Self("field")`` names an existing field, and sibling reads never form a cycle.

    Failure mode caught: a typo in a sibling field name, or two fields reading each other,
    both of which turn into a resolution-time error deep inside a simulation run instead of
    being found by a scan over the declarations.
    """
    for config_class in scan[0]:
        known: Set[str] = {field.name for field in dataclasses.fields(config_class)}
        laws = sizable_fields(config_class)
        variants: List[Dict[str, SizingLaw]] = [dict(laws)]
        for builder in presets_of(config_class).values():
            instance = builder.build(ComponentConfigScan.PROBE_NAME)
            variant = dict(laws)
            for field_name in laws:
                value = getattr(instance, field_name)
                if isinstance(value, SizingLaw):
                    variant[field_name] = value
            variants.append(variant)
        for variant in variants:
            # raises on an unknown sibling name and on a cycle, naming class and fields
            _resolution_order(config_class, variant, known)


@pytest.mark.base
def test_every_preset_builds_from_its_argument_and_names_the_instance(scan):
    """Each preset builds without error and takes its component_id name from its argument.

    Failure mode caught: a preset that still hard-codes a component name — two instances of
    that class in one scenario would then share a name and the engine could not tell them
    apart — or a builder that raises, which nothing else would notice until a setup used it.
    """
    checked = 0
    for config_class in scan[0]:
        for name, builder in presets_of(config_class).items():
            instance = builder.build(ComponentConfigScan.PROBE_NAME)
            assert isinstance(instance, config_class), (
                f"{config_class.__name__}.{builder.method_name} built a {type(instance)}"
            )
            assert instance.component_id.name == ComponentConfigScan.PROBE_NAME, (
                f"{config_class.__name__}.{builder.method_name} ignores its name argument"
            )
            assert builder.method_name == f"preset_{name}", (
                f"{config_class.__name__}.{builder.method_name} does not spell its wire name '{name}'"
            )
            checked += 1
    assert checked > 0


@pytest.mark.base
def test_every_named_constructor_is_prefixed_and_takes_the_instance_name_first(scan):
    """A constructor is spelled ``for_``/``from_`` and takes ``name`` before its parameters.

    Failure mode caught: a constructor whose call site does not say that it is building a
    config (``BuildingConfig.tabula(...)``), or one that cannot be called by a scenario
    loader because its instance name is somewhere in the middle of its parameters.
    """
    for config_class in scan[0]:
        for name, builder in constructors_of(config_class).items():
            assert name == builder.method_name, f"{config_class.__name__}: constructor wire name is its method name"
            assert name.startswith(("for_", "from_")), f"{config_class.__name__}.{name} lacks the for_/from_ prefix"
            parameters = list(inspect.signature(builder.function).parameters)
            assert parameters[:2] == ["cls", "name"], f"{config_class.__name__}.{name} takes {parameters[:2]} first"


@pytest.mark.base
def test_every_preset_wire_name_follows_the_naming_convention(scan):
    """Wire names are snake_case, carry digits only in a rating suffix, and guard ``standard``.

    Failure mode caught: a camelCase, hyphenated or capitalised preset name reaching a
    scenario file, where it becomes a permanent spelling nobody may change; a number baked
    into a name that belongs in a field; or ``standard`` surviving next to a real second
    variant, where it stops saying anything about the configuration it names.
    """
    offenders: List[str] = []
    for config_class in scan[0]:
        names = list(presets_of(config_class))
        for name in names:
            if not ComponentConfigScan.SNAKE_CASE.match(name):
                offenders.append(f"{config_class.__name__}.preset_{name} is not snake_case")
            elif not ComponentConfigScan.RATING_SUFFIX.match(name):
                offenders.append(f"{config_class.__name__}.preset_{name} has digits outside a rating suffix")
        standard = ComponentConfigScan.STANDARD_NAME
        siblings = [name for name in names if name != standard]
        if standard in names and any(not name.startswith(f"{standard}_") for name in siblings):
            offenders.append(
                f"{config_class.__name__} keeps '{standard}' next to {siblings}; rename it after "
                "the variant it actually describes"
            )
    assert not offenders


@pytest.mark.base
def test_no_wire_name_collides_with_a_field_or_with_another_builder(scan):
    """A class's wire names are unique among themselves and distinct from its field names.

    Failure mode caught: a preset and a field of one name — the dataclass machinery would
    take the classmethod as that field's default — or two builders answering to one wire
    name, which would make a scenario file's reference ambiguous. The kernel rejects both at
    class creation; this scan proves it holds for every converted class in the repository.
    """
    for config_class in scan[0]:
        fields = {field.name for field in dataclasses.fields(config_class)}
        presets = presets_of(config_class)
        constructors = constructors_of(config_class)
        wire_names = list(presets) + list(constructors)
        assert len(wire_names) == len(set(wire_names)), f"{config_class.__name__} has duplicate wire names"
        clashes = sorted(set(wire_names) & fields)
        assert clashes == [], f"{config_class.__name__}: wire names {clashes} are also field names"
        methods = [builder.method_name for builder in list(presets.values()) + list(constructors.values())]
        method_clashes = sorted(set(methods) & fields)
        assert method_clashes == [], f"{config_class.__name__}: builder methods {method_clashes} are also fields"


@pytest.mark.base
def test_the_classes_without_a_preset_are_the_ones_known_to_have_none(scan):
    """Only the classes listed as preset-less have no preset at all.

    Zero presets is legal — a class that is only a fact provider, or one whose every
    instance is spelled out in the scenario, has no defensible default — but it should be a
    decision rather than an oversight. Failure mode caught: a converted class silently
    losing its presets, or a new preset-less class slipping in without anyone deciding that
    it should be one.
    """
    without = tuple(sorted(config_class.__name__ for config_class in scan[0] if not presets_of(config_class)))
    assert without == PilotWireFormat.CLASSES_WITHOUT_PRESETS


@pytest.mark.base
def test_the_pilot_preset_and_fact_names_are_the_stored_wire_format():
    """The pilots' preset and fact names match the literals a stored scenario relies on.

    Failure mode caught: a rename that reads as harmless refactoring but silently
    invalidates every scenario file and every result column already written against the old
    spelling.
    """
    from hisim.components.building import BuildingConfig
    from hisim.components.controller_l2_energy_management_system import EMSConfig
    from hisim.components.generic_boiler import GenericBoilerConfig
    from hisim.components.heat_distribution_system import (
        HeatDistributionConfig,
        HeatDistributionControllerConfig,
    )

    by_name: Dict[str, Any] = {
        "GenericBoilerConfig": GenericBoilerConfig,
        "HeatDistributionConfig": HeatDistributionConfig,
        "HeatDistributionControllerConfig": HeatDistributionControllerConfig,
        "EMSConfig": EMSConfig,
        "BuildingConfig": BuildingConfig,
    }
    for class_name, expected in PilotWireFormat.PRESET_NAMES.items():
        assert tuple(presets_of(by_name[class_name])) == expected
    for class_name, expected_constructors in PilotWireFormat.CONSTRUCTOR_NAMES.items():
        assert tuple(constructors_of(by_name[class_name])) == expected_constructors
    for class_name, expected_facts in PilotWireFormat.FACT_NAMES.items():
        declared = tuple(
            fact
            for contribution in getattr(by_name[class_name], FactContribution.CLASS_ATTRIBUTE, ())
            for fact in contribution.facts
        )
        assert declared == expected_facts
