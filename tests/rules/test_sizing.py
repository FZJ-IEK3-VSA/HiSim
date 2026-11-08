"""Direct tests of the sizing rules: each rule on a configured file whose sizing fails in its condition and on one that sizes.

Four conditions are provoked through the real kernel on files written as YAML text (a fact nobody provides, two
providers without a source line, a source naming a component that does not provide the fact, a list for a fact read
once). The others are rare in a file — a null provider, a cycle between sibling fields, a duplicate instance name, a
group sized together stated in part, a deadlock — and their rules read nothing but the kernel's message, so they are
handed a recorded failure with the kernel's phrasing; that each phrasing is the real kernel's is pinned by the
classification tests of ``tests/test_energy_system_configure.py``. Further tests pin the location a fault carries,
the requirement of the binding rules of the configured file, the warning of a fact nobody reads and the bridge's
outcome.
"""

from typing import Dict, Type

import pytest

from hisim.config.laws import ConfigSizingError
from hisim.config.report import ResolutionReport
from hisim.energy_system.bindings import ClassBindings
from hisim.energy_system.configure import ConfigurationOutcome, configure_energy_system
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemSizingError
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.sizing_bridge import SizingOutcome
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleStage
from hisim.setup_rules.sizing import (
    ConsumerPhrasing,
    FactNotNull,
    FactProvided,
    KernelFailure,
    NoFieldCycle,
    OneReadShape,
    ProviderUnambiguous,
    Resolved,
    SizedTogetherWhole,
    SizingRule,
    SourceIsAProvider,
    SourceShape,
    UnconsumedFact,
    UniqueInstance,
)
from hisim.setup_rules.subjects import ConfiguredDocument
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class Entries:
    """The entries of the files the sizing rules are provoked on."""

    WEATHER = "  weather:\n    class: hisim.components.weather.Weather\n    preset: aachen\n"
    BOILER = "  boiler:\n    class: hisim.components.generic_boiler.GenericBoiler\n    preset: condensing_gas\n"

    @staticmethod
    def building(name: str) -> str:
        """A building entry under the given name."""
        return f"  {name}:\n    class: hisim.components.building.Building\n    preset: german_single_family_home\n"

    #: A file every sizing rule accepts: the boiler is sized from the one building.
    CLEAN = WEATHER + "  building:\n    class: hisim.components.building.Building\n    preset: german_single_family_home\n" + BOILER


class Failures:
    """Configured files whose sizing failed with a message of the kernel's phrasing, for the rules that read only the message."""

    #: The kernel's message of each condition that a small file does not provoke, by the rule that refuses it.
    MESSAGES: Dict[Type[SizingRule], str] = {
        FactNotNull: "'maximal_thermal_power_in_watt' provided as null by 'provider' (feature off); 'consumer' cannot size from it.",
        NoFieldCycle: "_CyclicConfig: fields first and second read each other via Self",
        OneReadShape: (
            "'maximal_thermal_power_in_watt' is read many-fold by 'consumer', and its providers are none; a many read sums every provider "
            "once, and at least one"
        ),
        UniqueInstance: "two configs named 'consumer' (_PowerReaderConfig, _PowerReaderConfig); sizing addresses every provider and consumer by its instance name.",
        SizedTogetherWhole: (
            "BatteryConfig 'consumer': custom_battery_capacity_generic_in_kilowatt_hour and custom_pv_inverter_power_generic_in_watt are "
            "sized together, but custom_battery_capacity_generic_in_kilowatt_hour = 15.0 is stated and "
            "custom_pv_inverter_power_generic_in_watt is left AUTO; state both or neither."
        ),
        Resolved: "sizing made no further progress; the remaining configs wait on each other or on facts nobody computed:\n  consumer",
    }

    @staticmethod
    def subject(message: str) -> ConfiguredDocument:
        """A configured file of the components ``provider`` and ``consumer`` whose sizing failed with this message."""
        sizing = SizingOutcome(configs=(), report=ResolutionReport(), names=("provider", "consumer"), failure=ConfigSizingError(message))
        outcome = ConfigurationOutcome(bindings=ClassBindings({}), names=("provider", "consumer"), origins=(), refusals=(), sizing=sizing)
        model = EnergySystemFile.model_construct(name="sizing under test", components={}, setup_kind=SetupKind.DEMO)
        return ConfiguredDocument(model=model, outcome=outcome, file="house.energy_system.yaml")


class Provoked:
    """The files whose sizing the real kernel refuses, by the rule that refuses it."""

    FILES: Dict[Type[SizingRule], str] = {
        FactProvided: Entries.BOILER,
        ProviderUnambiguous: Entries.WEATHER + Entries.building("house_a") + Entries.building("house_b") + Entries.BOILER,
        SourceIsAProvider: (
            Entries.WEATHER
            + Entries.building("building")
            + "  hds:\n    class: hisim.components.heat_distribution_system.HeatDistribution\n    preset: building_derived\n"
            + "    config:\n      heating_system: FLOORHEATING\n      water_mass_flow_rate_in_kg_per_second: 0.5\n"
            + "      absolute_conditioned_floor_area_in_m2: 100.0\n      cooling_enabled: false\n"
            + Entries.BOILER
            + "    sizing_sources:\n      heating_load_in_watt: hds.heating_load_in_watt\n"
        ),
        SourceShape: Entries.WEATHER + Entries.building("building") + Entries.BOILER + "    sizing_sources:\n      heating_load_in_watt: []\n",
    }


@pytest.mark.parametrize("rule", list(Provoked.FILES), ids=lambda rule: rule.CODE)
def test_a_rule_refuses_the_failure_the_kernel_gives_a_file_of_its_condition(rule: Type[SizingRule]) -> None:
    """Catches a condition of the kernel classified as another, located at another entry, or a clean file refused."""
    faults = rule().check(fft.SizingRules.configured(Provoked.FILES[rule]))

    assert [fault.component for fault in faults] == ["boiler"]
    assert faults[0].message.startswith(f"{rule.CODE} at components.boiler: ")
    assert faults[0].key_path == ("components", "boiler")
    assert not rule().check(fft.SizingRules.configured(Entries.CLEAN))


@pytest.mark.parametrize("rule", list(Failures.MESSAGES), ids=lambda rule: rule.CODE)
def test_a_rule_refuses_a_recorded_failure_of_its_condition_and_no_other(rule: Type[SizingRule]) -> None:
    """Catches a rule refusing another condition's failure, or none of its own."""
    faults = rule().check(Failures.subject(Failures.MESSAGES[rule]))

    assert len(faults) == 1 and faults[0].code == rule.CODE and faults[0].file == "house.energy_system.yaml"
    assert Failures.MESSAGES[rule] in faults[0].message
    others = [message for other, message in Failures.MESSAGES.items() if other is not rule]
    assert not any(rule().check(Failures.subject(message)) for message in others)
    assert not rule().check(fft.SizingRules.configured(Entries.CLEAN))


def test_every_sizing_rule_has_a_case() -> None:
    """Catches a rule of the kernel's failure added without a test of its condition."""
    rules = {rule for rule in fft.SizingRules.registry().rules if issubclass(rule, SizingRule)}

    assert rules == set(Provoked.FILES) | set(Failures.MESSAGES)


def test_the_kernels_failure_is_one_fault_raised_as_the_sizing_error_with_the_location_of_its_consumer() -> None:
    """Catches the sizing error losing its identifier, its location or the kernel's message when the rules raise it."""
    with pytest.raises(EnergySystemSizingError) as raised:
        configure_energy_system(fft.SizingRules.file(Entries.BOILER))

    assert raised.value.error_id is EnergySystemErrorId.SIZING_UNPROVIDED and raised.value.location == "components.boiler"
    assert "'heating_load_in_watt' needed by 'boiler' is provided by nobody" in raised.value.problem
    assert [fault.code for fault in raised.value.faults] == ["EF-4A"]
    assert [skip.code for skip in raised.value.skipped] == ["EF-4K"]
    assert isinstance(raised.value.__cause__, ConfigSizingError)


def test_the_sizing_rules_are_skipped_when_an_entry_could_not_be_configured() -> None:
    """Catches the sizing rules running on a system the kernel never sized, or their skip left unsaid."""
    subject = fft.SizingRules.configured(Entries.BOILER + "    config:\n      seasonal_efficiency: very high\n")
    families = ("hisim.setup_rules.bindings", "hisim.setup_rules.sizing")
    registry = SetupRuleRegistry([rule for rule in SetupRuleRegistry.discover() if rule.__module__ in families])

    findings = registry.findings(RuleStage.BUILD, subject, SetupKind.DEMO)

    assert [fault.code for fault in findings.faults] == ["EF-1A"]
    assert {skip.code for skip in findings.skipped} >= {"EF-4A", "EF-4X", "EF-4K"}
    assert all(not rule().check(subject) for rule in fft.SizingRules.registry().rules if rule.CODE.startswith("EF-4"))


def test_unconsumed_fact_logs_a_fact_nobody_reads_and_nothing_for_a_failed_sizing() -> None:
    """Catches an unread fact refused, passed over in silence, or reported for a sizing that failed."""
    findings = fft.SizingRules.findings(fft.SizingRules.configured(Entries.CLEAN))

    assert not findings.faults and findings.logged
    assert all(fault.code == "EF-4K" and fault.component in ("weather", "building", "boiler") for fault in findings.logged)
    assert any("'building' provides 'conditioned_floor_area_in_m2'" in fault.message for fault in findings.logged)
    assert not UnconsumedFact().check(fft.SizingRules.configured(Entries.BOILER))


def test_unconsumed_fact_lines_are_the_warnings_the_run_record_keeps() -> None:
    """Catches the run record's warnings drifting from what the rule logs."""
    report = ResolutionReport(unconsumed=[("building", "conditioned_floor_area_in_m2")])

    assert UnconsumedFact.lines(report) == ("'building' provides 'conditioned_floor_area_in_m2', which no component of this energy system reads.",)
    assert not UnconsumedFact.lines(None)
    assert UnconsumedFact.message_for("a", "b") == "'a' provides 'b', which no component of this energy system reads."


def test_classify_falls_back_to_the_catch_all_for_an_unknown_message() -> None:
    """Catches a kernel message of no known condition classified as one."""
    assert KernelFailure.classify("something the kernel never said") is EnergySystemErrorId.SIZING_FAILED
    assert KernelFailure.classify("'x' needed by 'boiler' is provided by nobody") is EnergySystemErrorId.SIZING_UNPROVIDED


def test_the_consumer_phrasing_locates_the_consumer_before_any_other_name() -> None:
    """Catches a failure located at the provider it was pointed at, or at a name the system does not hold."""
    message = "sources['boiler']['heating_load_in_watt'] = 'hds.heating_load_in_watt' but 'hds' does not declare it"

    assert ConsumerPhrasing.location(message, ["hds", "boiler"]) == "components.boiler"
    assert ConsumerPhrasing.location("'hds' and 'boiler' disagree", ["hds", "boiler"]) == "components.hds"
    assert ConsumerPhrasing.location("sizing made no further progress", ["hds"]) == "components"
    assert ConsumerPhrasing.NEEDED_BY.expression.search("x needed by 'boiler'") is not None


def test_the_sizing_rules_require_the_rules_of_the_configured_file() -> None:
    """Catches a sizing rule running when an entry it would size could not be built."""
    assert all(rule.REQUIRES == frozenset({"EF-1A", "EF-04"}) for rule in fft.SizingRules.registry().rules if issubclass(rule, SizingRule))
    assert UnconsumedFact.REQUIRES == frozenset(rule.CODE for rule in fft.SizingRules.registry().rules if issubclass(rule, SizingRule))
