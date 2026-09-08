"""Contract tests over the class-name keyed cost adapter (``hisim/economics/adapter.py``).

The adapter maps component classes to ``ComponentCostFacts`` through a table keyed by class
*name*, which is what keeps ``hisim.economics`` importable without pulling in ``hisim.components``
— at the price of no static checking whatsoever. A renamed component class, a renamed config field
or a reshaped enum therefore used to drop a component out of every cost result without any
complaint, which is exactly the silent omission §9.2 forbids. These tests are the missing static
check: they resolve every table key against the classes actually defined in ``hisim.components``
and run every extractor against the real default configs, so a rename fails here instead of
quietly shrinking a lifecycle cost.

The second half checks the adapter's failure semantics rather than its table: nothing may report
"no facts" without a reason except a class that is both unknown to the adapter and undeclared, and
relevance must be read off the class declaration only. Both are the "fail loudly" rule seen from
the extraction side.

A single module of ``hisim.components`` failing to import for lack of an optional third-party
package must not shrink the scan, so those failures are tolerated — but a table key that cannot be
resolved because its module refused to import fails, since an unresolvable key is precisely the
defect these tests exist to find.
"""

# clean

import dataclasses
import importlib
import inspect
import pkgutil
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from hisim.components.heat_distribution_system import HeatDistributionConfig, HeatDistributionSystemType
from hisim.config import auto_fields, presets_of
from hisim.economics.adapter import (
    FactsExtractors,
    _hds_facts,
    effective_cost_relevance,
    extract_cost_facts,
)
from hisim.economics.facts import ComponentCostFacts, CostRelevance
from hisim.loadtypes import ComponentType


class AdapterContractScan:
    """The one scan of ``hisim.components`` these tests share, plus the config plumbing.

    Importing every component module takes a moment and has import-time side effects, so it
    happens once here and the resulting class-name index is handed to the individual tests. The
    class also holds the two conventions the sweep needs: how a config class is found from its
    component class (through ``get_main_classname``, which every ``ConfigBase`` subclass
    implements) and how a config's still-unresolved ``AUTO`` fields are made concrete so that an
    extractor's arithmetic can run.
    """

    #: Third-party packages HiSim does not require: a component module that imports one of them
    #: may legitimately be missing from the scan on a machine without it. Anything else failing to
    #: import is a defect, not an environment.
    OPTIONAL_DEPENDENCIES: Tuple[str, ...] = ("wetterdienst",)

    #: Instance name handed to a preset builder; deliberately unmistakable so a leak is obvious.
    PROBE_NAME: str = "AdapterContractProbe"

    #: A pre-preset default factory is a classmethod whose name contains this word. The older
    #: config classes spell it ``get_default_<something>``, a few spell it
    #: ``get_<something>_default_config``, so the infix is matched rather than a prefix.
    FACTORY_INFIX: str = "default"

    #: ``get_default_connections*`` is the component wiring API, not a config factory, and lives
    #: on config classes too; it is excluded by this infix.
    CONNECTION_INFIX: str = "connection"

    #: Concrete stand-ins for the sizable fields that a preset leaves as ``AUTO``, keyed
    #: ``"<ConfigClass>.<field>"``. A component can never be constructed from an unresolved
    #: config, so ``AUTO`` is a pre-simulation state the adapter never sees in production; the
    #: sweep substitutes real values instead of skipping, because reading the *right field* is
    #: what is under test. An ``AUTO`` field with no entry here fails the sweep rather than being
    #: ignored, so a newly sizable field has to be considered rather than silently dropped.
    AUTO_SUBSTITUTES: Dict[str, Any] = {
        "GenericBoilerConfig.minimal_thermal_power_in_watt": 3000.0,
        "GenericBoilerConfig.maximal_thermal_power_in_watt": 12000.0,
        "HeatDistributionConfig.heating_system": HeatDistributionSystemType.RADIATOR,
        "HeatDistributionConfig.water_mass_flow_rate_in_kg_per_second": 0.5,
        "HeatDistributionConfig.absolute_conditioned_floor_area_in_m2": 120.0,
    }

    @staticmethod
    def collect() -> Tuple[Dict[str, List[type]], List[Tuple[str, str]]]:
        """Indexes every class defined in an importable ``hisim.components`` module by its name.

        Only classes whose ``__module__`` is the module being scanned are indexed, so a class
        merely imported into a second module does not look like a duplicate definition.

        Returns:
            The class-name index (a name maps to every class defining it, so an ambiguous name is
            visible) and ``(module, error)`` pairs for the modules that could not be imported.
        """
        import hisim.components as components_package  # pylint: disable=import-outside-toplevel

        failures: List[Tuple[str, str]] = []
        found: Dict[str, List[type]] = {}
        for info in pkgutil.walk_packages(components_package.__path__, components_package.__name__ + "."):
            try:
                module = importlib.import_module(info.name)
            except Exception as error:  # pylint: disable=broad-except
                failures.append((info.name, f"{type(error).__name__}: {error}"))
                continue
            for _name, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate.__module__ == module.__name__:
                    found.setdefault(candidate.__name__, []).append(candidate)
        return found, failures

    @staticmethod
    def configs_of(component_class: type) -> List[type]:
        """The config dataclasses in the component's own module that name it as their main class.

        ``ConfigBase.get_main_classname`` returns the fully qualified name of the component the
        config configures, which is the only machine-readable link from a component back to its
        config; matching on its last segment finds the config without a naming convention.
        """
        module = importlib.import_module(component_class.__module__)
        configs = []
        for _name, candidate in inspect.getmembers(module, inspect.isclass):
            if not dataclasses.is_dataclass(candidate) or candidate.__module__ != module.__name__:
                continue
            getter = getattr(candidate, "get_main_classname", None)
            if getter is None:
                continue
            try:
                main_class = str(getter())
            except Exception:  # pylint: disable=broad-except
                continue
            if main_class.rsplit(".", 1)[-1] == component_class.__name__:
                configs.append(candidate)
        return configs

    @classmethod
    def factories_of(cls, config_class: type) -> Dict[str, Callable[[], Any]]:
        """Every way of building a default instance of the config class, as zero-argument calls.

        Two mechanisms coexist in the repository: the declared ``@preset`` builders, which take
        only the instance name, and the older ``get_..default..`` classmethods. Both are collected
        so that a class with several defaults — the two solar-thermal variants, the seven boiler
        fuels — has each of them run through the extractor. A legacy factory that needs arguments
        beyond its defaults is *not* silently dropped: it is returned as a factory too, and the
        sweep reports the ``TypeError`` it raises.
        """
        factories: Dict[str, Callable[[], Any]] = {}
        for name, builder in presets_of(config_class).items():
            factories[f"preset {name}"] = lambda builder=builder: builder.build(cls.PROBE_NAME)
        for name, member in inspect.getmembers(config_class, callable):
            if not name.startswith("get_") or cls.FACTORY_INFIX not in name:
                continue
            if cls.CONNECTION_INFIX in name:
                continue
            factories[name] = lambda member=member: member()
        return factories

    @classmethod
    def concrete(cls, config: Any) -> Any:
        """Returns a copy of the config with its ``AUTO`` fields replaced by real values.

        Args:
            config: A freshly built config, possibly still carrying ``AUTO`` or a per-preset
                sizing law in its sizable fields.

        Returns:
            The config itself when nothing needs sizing, otherwise a copy with substitutes from
            :attr:`AUTO_SUBSTITUTES`.

        Raises:
            AssertionError: If a field needs sizing and has no substitute declared — a new
                sizable field must be considered here rather than quietly leaving the sweep with
                an unresolved config the extractor cannot read.
        """
        unresolved = auto_fields(config)
        if not unresolved:
            return config
        substitutes = {}
        for field_name in unresolved:
            key = f"{type(config).__name__}.{field_name}"
            assert key in cls.AUTO_SUBSTITUTES, (
                f"{key} needs sizing but AdapterContractScan.AUTO_SUBSTITUTES has no concrete "
                "value for it, so the cost extractor cannot be exercised against this config"
            )
            substitutes[field_name] = cls.AUTO_SUBSTITUTES[key]
        return dataclasses.replace(config, **substitutes)


class FakeComponents:
    """Throwaway component stand-ins, one per adapter failure branch under test.

    The adapter reads nothing but a component's class name, its ``config`` attribute, its optional
    ``cost_relevance`` declaration and its optional hooks, so a two-attribute object is a faithful
    stand-in and lets a test name a class after a real table key without importing (or breaking)
    the real component.
    """

    @staticmethod
    def named(class_name: str, config: Any, relevance: Optional[CostRelevance] = None) -> Any:
        """Builds one stand-in instance with the given class name, config and declaration.

        Args:
            class_name: The name the adapter will look up in its table.
            config: Whatever the extractor should be handed as ``component.config``.
            relevance: The ``cost_relevance`` to declare on the class, or None to declare none at
                all — which is what an unmigrated component looks like.
        """
        namespace: Dict[str, Any] = {
            "__doc__": f"Test stand-in for a component class named {class_name!r}.",
            "config": config,
        }
        if relevance is not None:
            namespace["cost_relevance"] = relevance
        return type(class_name, (), namespace)()


@pytest.fixture(name="scan", scope="module")
def fixture_scan() -> Tuple[Dict[str, List[type]], List[Tuple[str, str]]]:
    """Runs the component scan once for the whole module."""
    return AdapterContractScan.collect()


@pytest.mark.base
def test_every_component_module_imports(scan):
    """No component module fails to import except for a missing optional dependency.

    Failure mode caught: a module whose import breaks drops its classes out of the scan, so the
    key-resolution sweep below would pass while covering less. A module needing a third-party
    package HiSim does not require is tolerated, because its absence is an environment and not a
    defect — but it is reported here rather than anywhere else.
    """
    _found, failures = scan
    unexpected = [
        f"{name}: {error}"
        for name, error in failures
        if not any(dependency in error for dependency in AdapterContractScan.OPTIONAL_DEPENDENCIES)
    ]
    assert unexpected == [], "component modules failed to import: " + "; ".join(unexpected)


@pytest.mark.base
def test_every_table_key_names_exactly_one_real_component_class(scan):
    """Every key of ``FactsExtractors.BY_CLASS_NAME`` resolves to one class in hisim.components.

    Failure mode caught: the whole point of the file. A key that matches no class — because the
    component was renamed, moved out of ``hisim.components``, or was never spelled that way —
    makes that component fall through to ``UNDECLARED`` and vanish from every cost result. A key
    matching *two* classes is just as bad: whichever of them the simulation holds gets the other
    one's asset class and size.

    A key whose module failed to import fails here too, deliberately: the tolerance for optional
    dependencies above cannot extend to a table entry nobody can check.
    """
    found, _failures = scan
    problems = []
    for key in FactsExtractors.BY_CLASS_NAME:
        classes = found.get(key, [])
        if len(classes) != 1:
            where = ", ".join(f"{cls.__module__}.{cls.__name__}" for cls in classes) or "nothing"
            problems.append(f"{key} -> {where}")
    assert not problems, (
        "cost adapter table keys that do not name exactly one class defined in hisim.components: "
        + "; ".join(problems)
    )


@pytest.mark.base
def test_the_scan_finds_the_component_classes_it_is_meant_to_check(scan):
    """The scan really sees the classes involved, so the sweeps above and below are not vacuous.

    Failure mode caught: a scan that quietly matches nothing — the key-resolution test would then
    prove nothing, and the extractor sweep would run zero extractors.
    """
    found, _failures = scan
    assert {"Battery", "GenericBoiler", "HeatDistribution", "DistrictHeating", "ElectricHeating"} <= set(found)


@pytest.mark.base
def test_every_extractor_reads_its_real_config_without_raising(scan):
    """Each table extractor, run against every default config of its component, yields facts or None.

    Failure mode caught: a config field that has been renamed. The extractors read fields by name
    off an ``Any``-typed config, so ``config.connected_load_w`` against a config that now spells it
    ``connected_load_in_w`` is invisible to mypy and to every other test; it surfaces only as a
    component missing from a cost report. Running the real default configs through the real
    extractors makes that a test failure at the moment of the rename.

    ``None`` is an accepted answer, since it legitimately means "registered but not priceable in
    this configuration" (an unmapped boiler fuel, a low-temperature radiator); the adapter turns
    it into an unresolved reason rather than a silent drop, which is checked separately.
    """
    found, _failures = scan
    problems = []
    for key, extractor in FactsExtractors.BY_CLASS_NAME.items():
        classes = found.get(key, [])
        if len(classes) != 1:
            continue  # already reported by the key-resolution test; nothing to build a config from
        configs = AdapterContractScan.configs_of(classes[0])
        if not configs:
            problems.append(f"{key}: no config class in {classes[0].__module__} names it as its main class")
            continue
        for config_class in configs:
            factories = AdapterContractScan.factories_of(config_class)
            if not factories:
                problems.append(f"{key}: {config_class.__name__} offers no default config to test the extractor on")
                continue
            for factory_name, factory in factories.items():
                try:
                    config = AdapterContractScan.concrete(factory())
                except AssertionError:
                    raise
                except Exception as error:  # pylint: disable=broad-except
                    problems.append(
                        f"{key}: {config_class.__name__}.{factory_name} did not build: "
                        f"{type(error).__name__}: {error}"
                    )
                    continue
                try:
                    facts = extractor(config)
                except Exception as error:  # pylint: disable=broad-except
                    problems.append(
                        f"{key}: extractor raised on {config_class.__name__}.{factory_name}: "
                        f"{type(error).__name__}: {error}"
                    )
                    continue
                assert facts is None or isinstance(facts, ComponentCostFacts), (
                    f"{key}: extractor returned a {type(facts).__name__} for "
                    f"{config_class.__name__}.{factory_name}"
                )
    assert not problems, "cost adapter extractors disagree with their configs: " + "; ".join(problems)


@pytest.mark.base
def test_the_heat_distribution_extractor_matches_the_real_emitter_types():
    """``_hds_facts`` prices floor heating and radiators and declines low-temperature radiators.

    Failure mode caught: the emitter match drifting away from ``HeatDistributionSystemType``. The
    enum has been a string enum whose values equal its member names since the string-valued-enum
    cutover, so a match written against numeric values or title-cased spellings can never succeed
    and every real heat distribution system becomes unpriceable. Asserting against the real enum
    members ties the extractor to the type it reads.
    """
    base = AdapterContractScan.concrete(HeatDistributionConfig.preset_standard(AdapterContractScan.PROBE_NAME))

    floor_heating = _hds_facts(dataclasses.replace(base, heating_system=HeatDistributionSystemType.FLOORHEATING))
    assert floor_heating is not None
    assert floor_heating.asset_class == ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    assert floor_heating.size == base.absolute_conditioned_floor_area_in_m2

    radiator = _hds_facts(dataclasses.replace(base, heating_system=HeatDistributionSystemType.RADIATOR))
    assert radiator is not None
    assert radiator.asset_class == ComponentType.HEAT_DISTRIBUTION_SYSTEM_RADIATOR
    assert radiator.size == base.absolute_conditioned_floor_area_in_m2

    # No cost database entry for this one yet; None is the honest answer and becomes an
    # unresolved reason one level up rather than a dropped component.
    low_temperature = dataclasses.replace(
        base, heating_system=HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR
    )
    assert _hds_facts(low_temperature) is None


@pytest.mark.base
def test_a_broken_extractor_reports_a_reason_instead_of_no_facts():
    """An extractor that trips over a missing config field comes back unresolved, not empty.

    Failure mode caught: the extraction accident being swallowed. Returning an empty
    ``FactsExtraction`` made a renamed config field indistinguishable from an unknown component
    class, so the bridge had no way to tell "nothing to price here" from "this device just lost
    its price", and a log warning was the only trace.
    """
    component = FakeComponents.named("Battery", SimpleNamespace())
    extraction = extract_cost_facts(component)
    assert extraction.facts is None
    assert extraction.unresolved_reason is not None
    assert "Battery" in extraction.unresolved_reason
    assert "AttributeError" in extraction.unresolved_reason


@pytest.mark.base
def test_a_declared_priced_class_without_hook_or_table_entry_reports_a_reason():
    """A class declaring PRICED that nothing can describe is unresolved, not free of cost.

    Failure mode caught: a component author declaring ``cost_relevance = PRICED``, forgetting
    ``get_cost_facts()`` and getting no complaint — the exact omission §9.2 exists to prevent,
    reintroduced by the empty extraction the adapter used to return for an unknown class.
    """
    component = FakeComponents.named(
        "ComponentTheAdapterHasNeverHeardOf", SimpleNamespace(), CostRelevance.PRICED
    )
    extraction = extract_cost_facts(component)
    assert extraction.facts is None
    assert extraction.unresolved_reason is not None
    assert "ComponentTheAdapterHasNeverHeardOf" in extraction.unresolved_reason
    assert "PRICED" in extraction.unresolved_reason


@pytest.mark.base
def test_an_unknown_undeclared_class_still_extracts_to_nothing_at_all():
    """A class the adapter does not know and that declares nothing yields neither facts nor reason.

    Failure mode caught: over-correcting the two tests above into a blanket failure. The bridge
    rejects an ``UNDECLARED`` component on its declaration *before* asking for facts, so this path
    must stay quiet — otherwise every controller and every idealized device would produce a reason
    the bridge would then have to filter out again.
    """
    extraction = extract_cost_facts(FakeComponents.named("SomeUnmigratedController", SimpleNamespace()))
    assert extraction.facts is None
    assert extraction.unresolved_reason is None


@pytest.mark.base
@pytest.mark.parametrize("class_name", ["Battery", "ElectricityMeter", "FuelMeter"])
def test_relevance_is_never_inferred_from_the_adapter_tables(class_name):
    """An undeclared class stays UNDECLARED even when its name is in the adapter's own tables.

    Failure mode caught: the migration-era inference coming back. Reading ``PRICED`` out of
    ``BY_CLASS_NAME`` and ``METER`` out of ``get_meter_spec`` produced a plausible answer for a
    component nobody had classified, which hid both the missing declaration and — because the
    tables are keyed by class name — an entry whose key no longer matches any class. Declaration
    is now the only source.
    """
    component = FakeComponents.named(class_name, SimpleNamespace())
    assert effective_cost_relevance(component) == CostRelevance.UNDECLARED


@pytest.mark.base
def test_a_declared_relevance_is_reported_verbatim():
    """``effective_cost_relevance`` hands back exactly what the class declared.

    Failure mode caught: the reporting side of the same function regressing while the inference
    was removed — a declaration that is read but then overridden would be worse than no
    declaration at all.
    """
    for relevance in CostRelevance:
        component = FakeComponents.named("SomeDeclaredComponent", SimpleNamespace(), relevance)
        assert effective_cost_relevance(component) == relevance
