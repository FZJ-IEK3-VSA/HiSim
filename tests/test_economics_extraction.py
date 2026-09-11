"""Unit tests for the simulation-extraction side of seam 1 (roadmap/cost-spec-v2.md §2.1, W1.5).

Everything here runs against hand-built component stubs and an in-memory results frame — the
same objects the postprocessing bridge sees, without any simulator machinery. The concern is
purely "are the right physical quantities extracted", never "are they priced correctly".

**Surface.** `bridge.build_evaluation_inputs` and `adapter.py`: the layer that reads a finished
run's component objects, configs and result columns and produces `EvaluationInputs`. W1.5 called
this out as the inverted risk of the module — extraction was the *least*-tested layer, covered
only transitively by the one `extendedbase` simulation test — and this file is the answer to it.

**How it covers it.** Fixture-based, with fakes rather than real components: `FakeHeatPump`,
`ElectricityMeter` and `FuelMeter` implement exactly the attributes the bridge reads, so a test
failure points at the bridge instead of at a component's unrelated refactor. Expected values are
hand-derived from the stub inputs (24 x 1000 Wh -> 24 kWh; 20 kWh at 10 kWh/l -> 2 l; a 1..48 kW
ramp -> monthly maxima 4, 8, ... 48 kW), never read from a golden file, and no cost database is
consulted except in the one faithfulness test that deliberately needs a missing device entry.

**Error class.** A failure here means "wrong physical quantities in" (cost-spec-v2 §2.1) —
energy summed over the wrong column, a unit conversion lost, a peak window mis-derived, a subject
silently dropped. Pricing bugs live one seam downstream and cannot fail these tests; conversely,
if this file is green, an implausible cost result is a data or formula problem, not an extraction
problem. `TestFaithfulness` additionally pins the W1.1/D7 policy that `economic_inputs.json` is a
*pure* extract: written first and unconditionally, with the resolvability check strictly
downstream of it and hard-failing rather than dropping subjects.

Two classes pin the D25 tightening of that policy. `TestUnresolvedSubjects` covers the two ways a
component the adapter *recognizes* could previously vanish from the cost model without a trace —
a registered extractor returning nothing (issue #2) and a meter whose configured fuel maps to no
carrier, which used to be billed as heating oil (issue #3) — and `TestEnergyFlowHookAdoption`
pins the meter-path precedence of issue #18: the component's own `get_energy_flow_facts` first,
the adapter's class-name table second, with the capacity peaks still coming from the table because
the hook cannot express them. Quantities are kWh on both paths (D26).

The third way a component could vanish is now closed here too, and it is why every stub in this
file declares a `cost_relevance`. An *undeclared* component used to be dropped from the extract
and logged at INFO level; it becomes an `UnresolvedSubject` and fails the evaluation instead
(§9.2), so a stub that merely borrowed a real class's name would no longer reach the path it is
meant to exercise. `FakeController` keeps its missing declaration deliberately — it is the defect
under test — and `FakeFreeOfCostController` is the control that shows the declared "costs
nothing" answer still costs nothing.
"""

# clean

import ast
import datetime
import importlib
import json
import os
import re
from typing import Any

import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.economics import adapter, bridge
from hisim.economics.bridge import (
    _peaks_from_power_series,
    _sum_output_column,
    build_evaluation_inputs,
)
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.catalog_entries import CostDataError
from hisim.economics.facts import ComponentCostFacts, CostRelevance, EnergyFlowFacts
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


class _Output:
    """Stand-in for hisim.component.ComponentOutput (only these three fields are read).

    The bridge locates a meter's series by finding the position of `(component_name, field_name)`
    in the list of all outputs and taking the results frame column at that index, so a fake needs
    nothing more than those two strings. Using a stub instead of the real class keeps the tests
    independent of whatever else `ComponentOutput` grows.

    `unit` defaults to `WATT_HOUR`, the unit every billable meter column is declared in, because
    both summing paths now convert by the declared unit rather than assuming one: the meters' own
    `get_energy_flow_facts` hooks filter on it so a same-named power output in watts can never be
    summed as energy, and `bridge._sum_output_column` and `bridge._device_energy_flows` read it
    through the same conversion table. A power column has to say so explicitly.

    `load_type` is None unless a test declares one. Only the check that refuses a component class
    missing from the energy-balance table reads it, and a stub with no declared load type is
    exactly the "moves nothing the balance cares about" case that check has to let through.
    """

    def __init__(
        self,
        component_name: str,
        field_name: str,
        unit: Any = lt.Units.WATT_HOUR,
        load_type: Any = None,
    ) -> None:
        self.component_name = component_name
        self.field_name = field_name
        self.unit = unit
        self.load_type = load_type


class _Wrapper:
    """Stand-in for ComponentWrapper: the bridge only reaches for `.my_component`.

    Postprocessing hands the bridge the simulator's list of wrapped components, and the bridge
    unwraps each one before asking it anything. Faking the wrapper rather than importing it keeps
    this file free of simulator imports, which is the point of testing extraction in isolation.
    """

    def __init__(self, component) -> None:
        self.my_component = component


class _Config:
    """A component config: an attribute bag, exactly what the adapter expects.

    The compatibility adapter (§10.0 rule 4) reads named attributes off a component's config —
    `fuel_loadtype`, a heating value, a capacity — without knowing the config's class, so an
    ad-hoc namespace is a faithful stand-in. Components that have adopted `get_cost_facts()`
    ignore this entirely and read their own dataclass instead.
    """

    def __init__(self, **attributes) -> None:
        """Takes any keyword as an attribute; `fuel_loadtype` is declared because tests set it."""
        # `getattr(config, "fuel_loadtype", None)` is what the adapter does, so declaring the
        # attribute as None changes nothing for it and gives the tests something to assign to.
        self.fuel_loadtype: Any = attributes.pop("fuel_loadtype", None)
        self.__dict__.update(attributes)


class FakeHeatPump:
    """A component that adopted the new API (`get_cost_facts`, §9.1).

    Represents the target state: the class declares `cost_relevance = PRICED` and returns its own
    facts, so the bridge uses them verbatim and the adapter is never consulted. The asset class is
    a constructor argument so the same stub can play both a priceable subject and — as
    `WINDTURBINE`, which no shipped device file covers — an unresolvable one.
    """

    cost_relevance = CostRelevance.PRICED

    def __init__(
        self,
        component_name: str = "FakeHeatPump",
        asset_class=ComponentType.HEAT_PUMP,
        size: float = 9.0,
    ) -> None:
        """A priced component of the given asset class and size."""
        self.component_name = component_name
        self.config = _Config()
        self._asset_class = asset_class
        self._size = size

    def get_cost_facts(self) -> ComponentCostFacts:
        """9 kW of whatever asset class the test asked for, or whatever size the test set.

        The size is a constructor argument so the same stub can also play a device the setup
        built and then sized away (0 kW) and one whose declaration is simply wrong (a negative
        size), which are the two halves of the not-installed rule.
        """
        return ComponentCostFacts(
            asset_class=self._asset_class, size=self._size, size_unit=Units.KILOWATT
        )


class FakeController:
    """A component with no cost declaration at all (must abort the run, never be priced).

    §9.2 makes the declaration mandatory, so this stub is a *defect*, not a controller: it has no
    `cost_relevance` and no adapter entry, which is the combination that makes the bridge produce
    an unresolved subject and the D7 check refuse to price anything. A real controller with no
    cost of its own declares `FREE_OF_COST` and is skipped silently; that is a different stub and
    a different outcome, which is exactly what §9.2 exists to keep apart.
    """

    def __init__(self) -> None:
        """A component with no cost declaration of any kind."""
        self.component_name = "FakeController"
        self.config = _Config()


class FakeFreeOfCostController:
    """A controller that declares `FREE_OF_COST` — the legitimate "costs nothing" answer.

    The counterpart to `FakeController`, and the reason the abort above is not simply "controllers
    break the cost model": a component that has stated it has no cost of its own contributes
    neither facts nor a blocker, and the bridge drops it without a word.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    def __init__(self) -> None:
        """A declared cost-free component."""
        self.component_name = "FakeFreeOfCostController"
        self.config = _Config()


class ElectricityMeter:
    """Class name matters: the adapter's meter table keys on it.

    The real electricity meter has not adopted `get_energy_flow_facts()` yet, so the adapter maps
    it by class name to a `MeterSpec` naming the output fields to read (grid import/export in Wh
    and the power series for peaks). The stub therefore has to carry that exact class name, and
    reproduces the double role of a meter: a billed carrier *and* a priced device of its own.

    It declares `METER` because the real class does. Relevance is never inferred from the adapter
    tables, so a stub that only borrowed the class name would be `UNDECLARED` and the bridge would
    never reach the meter path these tests are about.
    """

    cost_relevance = CostRelevance.METER

    #: The output-name constants the real meter publishes. The adapter resolves a `MeterSpec`'s
    #: column names off the meter *class* rather than duplicating them as literals, so a stub that
    #: omitted them would be refused as a meter that cannot say which column carries its energy —
    #: which is the point of that lookup, and means the stub has to declare them like the real one.
    ElectricityFromGrid = "ElectricityFromGrid"
    ElectricityToGrid = "ElectricityToGrid"
    ElectricityFromGridInWatt = "ElectricityFromGridInWatt"

    def __init__(self) -> None:
        """A meter the adapter knows by class name, with no adopted flow hook yet."""
        self.component_name = "ElectricityMeter"
        self.config = _Config()
        # The adapter probes with `getattr(..., None)`, so an unset hook is the compatibility
        # path; a test that wants the adopted path assigns a callable here.
        self.get_energy_flow_facts: Any = None


class FuelMeter:
    """Oil meter; its billing quantity is kilowatt-hours, like every other carrier's (D26).

    The config still carries a `heating_value_of_fuel_in_kwh_per_liter`, deliberately set to a
    value (10.0) that differs from the `PhysicsConfig` one, because the point of the stub is that
    the extraction no longer reads it: the meter's kWh reach `energy_bought_in_kwh` untouched and
    it is the *price* that gets divided by a heating value, at resolution time. The field survives
    for the component's own legacy OPEX report only.
    """

    cost_relevance = CostRelevance.METER

    #: The consumption column the real fuel meter publishes, declared here for the same reason as
    #: on the electricity stub above: the adapter reads the name off the class.
    HeatConsumption = "HeatConsumption"

    def __init__(self) -> None:
        """An oil meter with the legacy heating value its config still carries."""
        self.component_name = "FuelMeter"
        self.config = _Config(
            fuel_loadtype=lt.LoadTypes.OIL, heating_value_of_fuel_in_kwh_per_liter=10.0
        )
        self.get_energy_flow_facts: Any = None


class GenericBoiler:
    """Class name matters: it is in `FactsExtractors.BY_CLASS_NAME`, keyed on exactly this name.

    The compatibility extractor picks the boiler's asset class from the fuel in its config, and
    returns None for a fuel that has no boiler asset class at all. That combination — a
    *registered* class yielding no facts — is what issue #2 was about, so the stub exists to
    produce it on demand: an OIL config resolves to the oil-boiler class, an ELECTRICITY one
    resolves to nothing.
    """

    cost_relevance = CostRelevance.PRICED

    def __init__(self, energy_carrier=lt.LoadTypes.OIL, component_name: str = "Boiler") -> None:
        """A boiler the compatibility table knows, burning the given carrier."""
        self.component_name = component_name
        self.config = _Config(energy_carrier=energy_carrier, maximal_thermal_power_in_watt=12000.0)


class _CountingFlowHook:
    """An adopted `get_energy_flow_facts` (§3.4), installed on a meter stub and counting calls.

    Attached to an instance rather than to a class on purpose: the adapter's meter table is keyed
    by class *name*, so a subclass would silently drop out of the compatibility path and the test
    could no longer tell which of the two paths produced a number. Installing the hook on an
    instance of the `ElectricityMeter` stub keeps the class exactly as the adapter knows it, and
    the declared flows are values the results frame does not contain — so whichever number comes
    out identifies the path that produced it.
    """

    def __init__(self, bought_in_kwh: float, sold_in_kwh: float = 0.0) -> None:
        self.bought_in_kwh = bought_in_kwh
        self.sold_in_kwh = sold_in_kwh
        self.calls = 0

    def __call__(self, all_outputs, postprocessing_results) -> EnergyFlowFacts:
        """The hook itself: records the call and returns the declared boundary flows."""
        self.calls += 1
        return EnergyFlowFacts(
            carrier=EnergyCarrier.ELECTRICITY,
            energy_bought_in_kwh=self.bought_in_kwh,
            energy_sold_in_kwh=self.sold_in_kwh,
        )


class FakeZeroSizedMeteredDevice:
    """A device configured at zero size that nevertheless reports boundary energy flows.

    The counter-example to `adapter._resolved_or_not_installed`'s claim that a zero-size device
    moves no energy. It is a `PRICED` component with an adopted `get_cost_facts()` returning a
    0 kWp PV system — so the extraction comes back as "not installed" — and an adopted
    `get_energy_flow_facts()` hook whose kWh the test dials, so the same component can play both
    halves of the rule: the ordinary zero-size device that also metered zero, and the contradiction
    that must stop the run.
    """

    cost_relevance = CostRelevance.PRICED

    def __init__(self, bought_in_kwh: float = 0.0, component_name: str = "ZeroSizedPv") -> None:
        """A 0 kWp PV system whose meter reports the given purchased energy."""
        self.component_name = component_name
        self.config = _Config()
        self.get_energy_flow_facts: Any = _CountingFlowHook(bought_in_kwh=bought_in_kwh)

    def get_cost_facts(self) -> ComponentCostFacts:
        """0 kWp: the size that makes the adapter report the component as not installed."""
        return ComponentCostFacts(asset_class=ComponentType.PV, size=0.0, size_unit=Units.KILOWATT)


class _SimulationParameters:
    """The handful of attributes `build_evaluation_inputs` reads off SimulationParameters.

    Year, timestep length, the date range and the result directory are the whole dependency —
    which is itself worth pinning, because anything more would mean the extraction layer had
    started to depend on simulator state it cannot serialize into `economic_inputs.json`. The
    `days` argument is what drives the simulated-period fraction the engine annualizes with.
    """

    def __init__(self, days: int = 1, seconds_per_timestep: int = 900, year: int = 2024) -> None:
        self.year = year
        self.seconds_per_timestep = seconds_per_timestep
        self.start_date = datetime.datetime(year, 1, 1)
        self.end_date = self.start_date + datetime.timedelta(days=days)
        self.result_directory = ""


def _fail_evaluation(inputs) -> str:
    """Runs the D7 resolution check over extracted inputs and returns the raised error's message.

    Shared by every test that has to show a subject *blocking* rather than merely being recorded:
    extraction only writes `unresolved_subjects` into the record, and it is this check — the one
    `bridge.compute_lifecycle_costs` calls — that turns them into the refusal to produce partial
    cost results. Asserting on its message is what distinguishes the D7 abort from a warning.

    Args:
        inputs: The `EvaluationInputs` an extraction produced.

    Returns:
        The `UnresolvableSubjectsError` message; the call fails the test if nothing is raised.
    """
    from hisim.economics.database import CostDatabase
    from hisim.economics.evaluator import (
        EconomicEvaluator,
        UnresolvableSubjectsError,
        require_resolvable_subjects,
    )
    from hisim.economics.parameters import EconomicParameters

    evaluator = EconomicEvaluator(
        CostDatabase(), EconomicParameters(country="DE", price_basis_year=2024)
    )
    with pytest.raises(UnresolvableSubjectsError) as raised:
        require_resolvable_subjects(inputs, evaluator)
    return str(raised.value)


def _results_frame(columns) -> pd.DataFrame:
    """Column order defines the index the bridge uses to find an output's series.

    Takes `(name, values)` pairs and builds the in-memory results frame postprocessing holds
    after a run. The names are irrelevant to the bridge — it locates a series *positionally*, by
    the index of the matching entry in the list of all outputs — so a test must keep this frame's
    column order aligned with the `_Output` list it passes alongside.
    """
    return pd.DataFrame(dict(columns))


class TestFactsExtraction:
    """Which subjects come out of a set of wrapped components."""

    def test_component_with_get_cost_facts_becomes_a_subject(self):
        """The adopted API is used verbatim, keyed by the component name."""
        component = FakeHeatPump()
        inputs = build_evaluation_inputs(
            [_Wrapper(component)], [], pd.DataFrame(), _SimulationParameters()
        )
        assert [subject_facts.subject for subject_facts in inputs.cost_facts] == ["FakeHeatPump"]
        facts = inputs.cost_facts[0].facts
        assert facts.asset_class is ComponentType.HEAT_PUMP
        assert facts.size == pytest.approx(9.0)
        assert facts.size_unit is Units.KILOWATT

    def test_undeclared_component_becomes_an_unresolved_subject(self):
        """A component with no declaration aborts the evaluation instead of being skipped (§9.2).

        Inverted from ``test_undeclared_component_is_skipped``, which pinned the parallel-phase
        leniency: an undeclared component used to be dropped from the extract and mentioned at
        INFO level, so a forgotten declaration cost a device its place in every cost result and
        said so only in a log nobody reads. The declaration is mandatory now, so the component
        becomes an `UnresolvedSubject` and the downstream D7 check turns it into a hard failure.
        """
        inputs = build_evaluation_inputs(
            [_Wrapper(FakeController())], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.cost_facts == []
        assert inputs.billing == []
        assert [item.subject for item in inputs.unresolved_subjects] == ["FakeController"]
        reason = inputs.unresolved_subjects[0].reason
        assert "FakeController" in reason  # the class, so the reader knows which file to open
        assert "cost_relevance" in reason  # what is missing ...
        assert "FREE_OF_COST" in reason  # ... and what may be written instead
        message = _fail_evaluation(inputs)
        assert "FakeController" in message
        assert "no partial cost results" in message  # the D7 refusal, not a warning

    def test_a_component_declared_free_of_cost_is_skipped_silently(self):
        """The declared "costs nothing" answer stays free: no facts, no billing, no blocker.

        The control for the test above — without it, the abort could just as well be "any
        component the cost model has no facts for", which would make `FREE_OF_COST` unusable and
        break every controller in the fleet.
        """
        inputs = build_evaluation_inputs(
            [_Wrapper(FakeFreeOfCostController())], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.cost_facts == []
        assert inputs.billing == []
        assert inputs.unresolved_subjects == []

    def test_zero_size_component_is_skipped_instead_of_killing_the_evaluation(self):
        """A device the setup built and then sized to zero is "not installed", not invalid data.

        `share_of_maximum_pv_potential = 0` still constructs a PV system, at 0 kWp; the whole
        lifecycle evaluation used to die on its facts' validation. It must now be excluded from
        pricing, and it must not become an unresolved subject either, because that aborts the
        evaluation just as thoroughly under D7.
        """
        component = FakeHeatPump(component_name="ZeroSizedPv", size=0.0)
        inputs = build_evaluation_inputs(
            [_Wrapper(component)], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.cost_facts == []
        assert inputs.unresolved_subjects == []

    def test_a_not_installed_component_that_metered_nothing_is_still_billed_its_zeros(self):
        """The rule as it stands: zero size, zero flows — skipped from pricing, determinants kept.

        The determinants of a device that measured nothing are themselves zero, so keeping them
        changes no bill; what they do is keep the carrier on the boundary, which is why the bridge
        appends them rather than dropping the component wholesale. This is the control for the test
        below: without it, the refusal there could just as well be "a not-installed component may
        not meter at all".
        """
        inputs = build_evaluation_inputs(
            [_Wrapper(FakeZeroSizedMeteredDevice(bought_in_kwh=0.0))],
            [],
            pd.DataFrame(),
            _SimulationParameters(),
        )
        assert inputs.cost_facts == []
        assert inputs.unresolved_subjects == []
        assert [determinants.energy_bought_in_kwh for determinants in inputs.billing] == [0.0]

    def test_a_not_installed_component_that_metered_energy_aborts_instead_of_being_billed(self):
        """The adapter's assumption is checked, not trusted: flows from a zero-size device stop the run.

        `adapter._resolved_or_not_installed` excludes a zero-size component from pricing on the
        grounds that such a device moves no energy. Where that is false the two available answers
        are both wrong — billing the flows charges energy to a device the result says is absent,
        dropping them silently loses measured energy off the boundary — so the component becomes an
        unresolved subject and the D7 check refuses to price the rest of the fleet around it. The
        reason has to name the non-zero figure, because "zero size but metered" is a defect in the
        setup and the reader has to know which of the two statements to believe.
        """
        inputs = build_evaluation_inputs(
            [_Wrapper(FakeZeroSizedMeteredDevice(bought_in_kwh=1234.0))],
            [],
            pd.DataFrame(),
            _SimulationParameters(),
        )
        assert inputs.cost_facts == []
        assert inputs.billing == []  # not billed, which is the whole point
        assert [item.subject for item in inputs.unresolved_subjects] == ["ZeroSizedPv"]
        reason = inputs.unresolved_subjects[0].reason
        assert "zero size" in reason
        assert "energy_bought_in_kwh" in reason  # the field that contradicts it, by name
        message = _fail_evaluation(inputs)
        assert "ZeroSizedPv" in message
        assert "no partial cost results" in message

    def test_zero_size_extraction_carries_a_reason(self):
        """The skip is stated, not silent: the adapter names the class and why it contributes nothing."""
        extraction = adapter.extract_cost_facts(FakeHeatPump(size=0.0))
        assert extraction.facts is None
        assert extraction.unresolved_reason is None
        assert "zero size" in (extraction.not_installed_reason or "")
        assert "not installed" in (extraction.not_installed_reason or "")

    def test_negative_size_still_fails_fast(self):
        """The other direction: zero is a statement, a negative size is corrupt and must raise."""
        with pytest.raises(ValueError, match="size"):
            adapter.extract_cost_facts(FakeHeatPump(size=-3.0))

    def test_simulated_period_fraction_follows_the_date_range(self):
        """One simulated day of a 365-day year -> 1/365; a full year -> 1.0."""
        one_day = build_evaluation_inputs([], [], pd.DataFrame(), _SimulationParameters(days=1))
        assert one_day.simulated_period_fraction == pytest.approx(1.0 / 365.0)
        assert one_day.simulation_year == 2024
        full_year = build_evaluation_inputs([], [], pd.DataFrame(), _SimulationParameters(days=365))
        assert full_year.simulated_period_fraction == pytest.approx(1.0)
        # Longer runs are clamped to the first simulated year (cost_module_issues.md #15).
        two_years = build_evaluation_inputs([], [], pd.DataFrame(), _SimulationParameters(days=730))
        assert two_years.simulated_period_fraction == pytest.approx(1.0)


class TestFactsExtractionRecordRefusesTwoAnswers:
    """The three-field union may state at most one thing, and says so at construction."""

    def test_two_fields_at_once_are_refused(self):
        """Facts beside a reason would be read as resolved and the reason would vanish.

        `bridge.py` reads the record by branch order — facts first, then `unresolved_reason`, then
        `not_installed_reason` — so a record carrying two of them is not an ambiguity the caller
        can notice: it silently drops the later one. That is the class of silent omission the
        record exists to close, so the contradiction fails where it is built.
        """
        facts = ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP, size=9.0, size_unit=Units.KILOWATT
        )
        with pytest.raises(ValueError, match="at once"):
            adapter.FactsExtraction(facts=facts, unresolved_reason="and also unpriceable")
        with pytest.raises(ValueError, match="at once"):
            adapter.FactsExtraction(
                unresolved_reason="unpriceable", not_installed_reason="also absent"
            )

    def test_the_empty_record_is_a_legitimate_answer(self):
        """All-None is what `FREE_OF_COST` and an unknown, undeclared class both return."""
        empty = adapter.FactsExtraction()
        assert empty.facts is None
        assert empty.unresolved_reason is None
        assert empty.not_installed_reason is None


class TestMeterExtraction:
    """Meter outputs -> BillingDeterminants."""

    def test_sum_output_column_converts_wh_to_kwh(self):
        """The bridge sums the Wh column and scales by 1e-3; unknown outputs give None."""
        all_outputs = [_Output("Meter", "Other"), _Output("Meter", "ElectricityFromGrid")]
        results = _results_frame([("a", [1000.0, 2000.0, 0.0]), ("b", [500.0, 250.0, 250.0])])
        assert _sum_output_column(
            "Meter", "ElectricityFromGrid", all_outputs, results, 900
        ) == pytest.approx(1.0)
        assert _sum_output_column("Meter", "Missing", all_outputs, results, 900) is None

    def test_the_billing_sum_converts_by_the_declared_unit_like_the_balance_does(self):
        """One conversion table for both paths, so a bill and a chart cannot disagree (review).

        The billing sum used to hardcode `* 1e-3`, which is right for the Wh every shipped meter
        column is declared in and wrong for anything else. Here the same column is declared in kWh
        and in W, and the result follows the declaration rather than the old assumption.
        """
        results = _results_frame([("a", [1.5, 2.5])])
        in_kwh = [_Output("Meter", "Column", unit=lt.Units.KWH)]
        assert _sum_output_column("Meter", "Column", in_kwh, results, 900) == pytest.approx(4.0)
        in_watt = [_Output("Meter", "Column", unit=lt.Units.WATT)]
        # 4 W-steps of 900 s = 4 x 0.25 Wh = 1 Wh = 1e-3 kWh.
        assert _sum_output_column("Meter", "Column", in_watt, results, 900) == pytest.approx(1e-3)

    def test_a_meter_column_in_an_unconvertible_unit_is_refused(self):
        """A degree Celsius is not energy, and billing it would be a number out of nowhere."""
        results = _results_frame([("a", [1.0, 2.0])])
        outputs = [_Output("Meter", "Column", unit=lt.Units.CELSIUS)]
        with pytest.raises(CostDataError, match="neither an energy nor a power unit"):
            _sum_output_column("Meter", "Column", outputs, results, 900)

    def test_electricity_meter_yields_bought_sold_and_peaks(self):
        """Bought/sold energy in kWh and the 15-minute peaks land on the determinants."""
        meter = ElectricityMeter()
        all_outputs = [
            _Output("ElectricityMeter", "ElectricityFromGrid"),
            _Output("ElectricityMeter", "ElectricityToGrid"),
            _Output("ElectricityMeter", "ElectricityFromGridInWatt", unit=lt.Units.WATT),
        ]
        bought = [1000.0] * 24  # Wh per 15-min step -> 24 kWh
        sold = [500.0] * 24  # -> 12 kWh
        power = [4000.0] * 23 + [8000.0]  # W, one peak interval at the end
        results = _results_frame([("bought", bought), ("sold", sold), ("power", power)])
        inputs = build_evaluation_inputs(
            [_Wrapper(meter)], all_outputs, results, _SimulationParameters(seconds_per_timestep=900)
        )
        assert len(inputs.billing) == 1
        determinants = inputs.billing[0]
        assert determinants.carrier is EnergyCarrier.ELECTRICITY
        assert determinants.energy_bought_in_kwh == pytest.approx(24.0)
        assert determinants.energy_sold_in_kwh == pytest.approx(12.0)
        assert determinants.annual_peak_in_kw == pytest.approx(8.0)
        # The meter hardware itself is a priced device as well.
        assert [subject_facts.subject for subject_facts in inputs.cost_facts] == ["ElectricityMeter"]
        assert inputs.cost_facts[0].facts.asset_class is ComponentType.ELECTRICITY_METER

    def test_fuel_meter_quantity_stays_in_kilowatt_hours(self):
        """`energy_bought_in_kwh` holds kWh for oil too — the field name is now true (D26).

        This test used to assert the opposite (2.0 liters, from a `_fuel_quantity` conversion the
        adapter no longer has); it was pinning the mislabelling of review finding 11. The meter
        config's heating value of 10 kWh/l is left in place precisely so that a re-introduced
        conversion would show up as 2.0 here instead of passing unnoticed.
        """
        all_outputs = [_Output("FuelMeter", "HeatConsumption")]
        results = _results_frame([("heat", [1000.0] * 20)])  # 1 kWh per step -> 20 kWh
        inputs = build_evaluation_inputs(
            [_Wrapper(FuelMeter())], all_outputs, results, _SimulationParameters()
        )
        assert inputs.billing[0].carrier is EnergyCarrier.HEATING_OIL
        assert inputs.billing[0].energy_bought_in_kwh == pytest.approx(20.0)
        assert not hasattr(adapter, "_fuel_quantity")

    def test_missing_meter_output_leaves_the_carrier_unbilled(self):
        """A meter whose bought-field is absent produces no billing determinants."""
        inputs = build_evaluation_inputs(
            [_Wrapper(ElectricityMeter())], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.billing == []


class TestPeakExtraction:
    """`_peaks_from_power_series` bakes the 15-minute billing grid into extraction (§8.4)."""

    def test_monthly_and_annual_peaks_of_a_synthetic_series(self):
        """48 quarter-hour values -> 12 monthly peaks over 4 intervals each."""
        # Ramp 1..48 kW: the maximum of every 4-interval block is 4, 8, ... 48 kW.
        series = pd.Series([value * 1000.0 for value in range(1, 49)])
        monthly, annual = _peaks_from_power_series(series, seconds_per_timestep=900)
        assert monthly == pytest.approx([4.0 * (month + 1) for month in range(12)])
        assert annual == pytest.approx(48.0)

    def test_peaks_are_interval_means_not_instantaneous(self):
        """Three 5-minute steps average into one 15-minute billing interval."""
        series = pd.Series([0.0, 0.0, 3000.0, 1000.0, 1000.0, 1000.0])
        monthly, annual = _peaks_from_power_series(series, seconds_per_timestep=300)
        assert annual == pytest.approx(1.0)  # both intervals average to 1 kW
        assert monthly == pytest.approx([1.0, 1.0])

    def test_trailing_intervals_are_not_dropped_when_twelve_does_not_divide_them(self):
        """The last block absorbs the remainder, so a peak in the tail is still billed (§8.4).

        A partial-year run rarely produces a multiple of twelve billing intervals. Floor division
        used to size the blocks and then truncate to twelve, which discarded every interval past
        the twelfth block — the last days of the run, capacity charge and all. With 35 intervals
        the blocks are three wide (ceil), the twelfth holds the final two, and the global maximum
        sitting on the very last interval reappears as the last monthly peak.
        """
        series = pd.Series([value * 1000.0 for value in range(1, 36)])
        monthly, annual = _peaks_from_power_series(series, seconds_per_timestep=900)
        assert annual == pytest.approx(35.0)
        assert len(monthly) == 12
        assert monthly[-1] == pytest.approx(35.0)
        assert max(monthly) == pytest.approx(annual)

    def test_timestep_not_dividing_the_billing_interval_yields_no_peaks(self):
        """Hourly timesteps cannot resolve a 15-minute peak."""
        series = pd.Series([1000.0] * 24)
        assert _peaks_from_power_series(series, seconds_per_timestep=3600) == ([], 0.0)


class TestUnresolvedSubjects:
    """A component the adapter recognizes but cannot describe fails the run (issues #2, #3, D7)."""

    def test_registered_extractor_returning_nothing_blocks_the_evaluation(self):
        """A boiler burning an unmapped fuel is an unresolved subject, not a silent drop (#2).

        `GenericBoiler` is in the adapter's extractor table, so the component counts as PRICED and
        never appears among the undeclared components; its extractor nevertheless returns None for
        a carrier that has no boiler asset class. The whole component used to vanish from the cost
        model at that point, with nothing anywhere saying so.
        """
        boiler = GenericBoiler(energy_carrier=lt.LoadTypes.ELECTRICITY, component_name="MysteryBoiler")
        inputs = build_evaluation_inputs(
            [_Wrapper(boiler)], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.cost_facts == []
        assert [item.subject for item in inputs.unresolved_subjects] == ["MysteryBoiler"]
        message = _fail_evaluation(inputs)
        assert "MysteryBoiler" in message
        assert "GenericBoiler" in message  # the reason names the registered class ...
        assert "no partial cost results" in message  # ... and it is the D7 refusal

    def test_a_boiler_with_a_mapped_fuel_still_prices(self):
        """The counterpart: a fuel the table knows yields facts and no unresolved subject."""
        inputs = build_evaluation_inputs(
            [_Wrapper(GenericBoiler(energy_carrier=lt.LoadTypes.OIL))],
            [],
            pd.DataFrame(),
            _SimulationParameters(),
        )
        assert inputs.unresolved_subjects == []
        assert inputs.cost_facts[0].facts.asset_class is ComponentType.OIL_HEATER
        assert inputs.cost_facts[0].facts.size == pytest.approx(12.0)

    def test_unmapped_fuel_meter_fails_instead_of_billing_heating_oil(self):
        """An unset `fuel_loadtype` used to be billed at oil prices; now it blocks (#3)."""
        meter = FuelMeter()
        meter.config.fuel_loadtype = None
        inputs = build_evaluation_inputs(
            [_Wrapper(meter)],
            [_Output("FuelMeter", "HeatConsumption")],
            _results_frame([("heat", [1000.0] * 20)]),
            _SimulationParameters(),
        )
        assert inputs.billing == []  # nothing was billed at a guessed carrier
        assert [item.subject for item in inputs.unresolved_subjects] == ["FuelMeter"]
        message = _fail_evaluation(inputs)
        assert "FuelMeter" in message
        assert "fuel_loadtype" in message

    def test_an_unknown_load_type_is_rejected_and_the_four_valid_fuels_are_not(self):
        """Only the four mapped load types resolve; anything else raises with the component name."""
        expected = {
            lt.LoadTypes.OIL: EnergyCarrier.HEATING_OIL,
            lt.LoadTypes.PELLETS: EnergyCarrier.PELLETS,
            lt.LoadTypes.WOOD_CHIPS: EnergyCarrier.WOOD_CHIPS,
            lt.LoadTypes.DISTRICTHEATING: EnergyCarrier.DISTRICT_HEATING,
        }
        for load_type, carrier in expected.items():
            meter = FuelMeter()
            meter.config.fuel_loadtype = load_type
            spec = adapter.get_meter_spec(meter)
            assert spec is not None
            assert spec.carrier is carrier
        meter = FuelMeter()
        meter.component_name = "WeirdMeter"
        meter.config.fuel_loadtype = lt.LoadTypes.ELECTRICITY
        with pytest.raises(CostDataError) as raised:
            adapter.get_meter_spec(meter)
        assert "WeirdMeter" in str(raised.value)
        assert "electricity" in str(raised.value).lower()

    def test_a_meter_output_missing_from_the_run_blocks_instead_of_unbilling_the_carrier(self):
        """A declared meter column the run does not contain is an unresolved subject, not a warning.

        Failure mode caught: the bill that is silently short one flow. The bridge used to log a
        warning and return no determinants for the meter, so the whole carrier went unbilled and
        the lifecycle result looked complete — the same silent-omission class as an undeclared
        component (§9.2), and now refused the same way.
        """
        inputs = build_evaluation_inputs(
            [_Wrapper(ElectricityMeter())],
            [_Output("ElectricityMeter", "ElectricityToGrid")],  # the bought column is missing
            _results_frame([("sold", [500.0] * 24)]),
            _SimulationParameters(seconds_per_timestep=900),
        )
        assert inputs.billing == []
        assert [item.subject for item in inputs.unresolved_subjects] == ["ElectricityMeter"]
        message = _fail_evaluation(inputs)
        assert "ElectricityFromGrid" in message  # the missing column is named ...
        assert "no partial cost results" in message  # ... and it is the D7 refusal

    def test_a_meter_missing_only_its_power_column_blocks_too(self):
        """The peak series counts as a declared output: missing it drops a capacity charge silently.

        Failure mode caught: the narrower version of the same hole. Energy and feed-in were read,
        so a bill came out — just without the capacity charge the tariff bills on (§8.4), which no
        reader of the result could have noticed.
        """
        inputs = build_evaluation_inputs(
            [_Wrapper(ElectricityMeter())],
            [
                _Output("ElectricityMeter", "ElectricityFromGrid"),
                _Output("ElectricityMeter", "ElectricityToGrid"),
            ],
            _results_frame([("bought", [1000.0] * 24), ("sold", [500.0] * 24)]),
            _SimulationParameters(seconds_per_timestep=900),
        )
        assert inputs.billing == []
        assert "ElectricityFromGridInWatt" in _fail_evaluation(inputs)

    def test_the_extraction_failure_survives_the_written_extract(self, tmp_path):
        """`economic_inputs.json` records the failure, so re-pricing hits the same wall (W1.1)."""
        from hisim.economics.serialization import read_inputs, write_inputs

        inputs = build_evaluation_inputs(
            [_Wrapper(GenericBoiler(energy_carrier=lt.LoadTypes.ELECTRICITY, component_name="Odd"))],
            [],
            pd.DataFrame(),
            _SimulationParameters(),
        )
        write_inputs(inputs, str(tmp_path))
        with open(tmp_path / "economic_inputs.json", encoding="utf-8") as file:
            written = json.load(file)
        assert [item["subject"] for item in written["unresolved_subjects"]] == ["Odd"]
        reloaded = read_inputs(str(tmp_path))
        assert [item.subject for item in reloaded.unresolved_subjects] == ["Odd"]
        assert "Odd" in _fail_evaluation(reloaded)


class TestEnergyFlowHookAdoption:
    """The §3.4 hook is the meter path; the adapter table is the fallback (issue #18)."""

    _ALL_OUTPUTS = [
        _Output("ElectricityMeter", "ElectricityFromGrid"),
        _Output("ElectricityMeter", "ElectricityToGrid"),
        _Output("ElectricityMeter", "ElectricityFromGridInWatt", unit=lt.Units.WATT),
    ]

    @classmethod
    def _frame(cls):
        """The results frame both paths read: 24 kWh bought, 12 kWh sold, an 8 kW peak."""
        return _results_frame(
            [
                ("bought", [1000.0] * 24),
                ("sold", [500.0] * 24),
                ("power", [4000.0] * 23 + [8000.0]),
            ]
        )

    def test_the_hook_is_consulted_and_wins_over_the_output_columns(self):
        """A declared flow is billed; the frame's columns are not summed behind its back."""
        meter = ElectricityMeter()
        meter.get_energy_flow_facts = _CountingFlowHook(bought_in_kwh=111.0, sold_in_kwh=7.0)
        inputs = build_evaluation_inputs(
            [_Wrapper(meter)],
            self._ALL_OUTPUTS,
            self._frame(),
            _SimulationParameters(seconds_per_timestep=900),
        )
        assert meter.get_energy_flow_facts.calls == 1
        determinants = inputs.billing[0]
        assert determinants.carrier is EnergyCarrier.ELECTRICITY
        assert determinants.energy_bought_in_kwh == pytest.approx(111.0)  # the hook's number
        assert determinants.energy_sold_in_kwh == pytest.approx(7.0)

    def test_the_hook_keeps_the_peaks_the_meter_spec_knows_how_to_read(self):
        """`EnergyFlowFacts` cannot carry peaks, so they still come from the MeterSpec (§8.4)."""
        meter = ElectricityMeter()
        meter.get_energy_flow_facts = _CountingFlowHook(bought_in_kwh=111.0)
        inputs = build_evaluation_inputs(
            [_Wrapper(meter)],
            self._ALL_OUTPUTS,
            self._frame(),
            _SimulationParameters(seconds_per_timestep=900),
        )
        assert inputs.billing[0].annual_peak_in_kw == pytest.approx(8.0)

    def test_a_meter_without_the_hook_is_billed_exactly_as_before(self):
        """The adapter table stays the fallback: identical determinants, no hook involved."""
        inputs = build_evaluation_inputs(
            [_Wrapper(ElectricityMeter())],
            self._ALL_OUTPUTS,
            self._frame(),
            _SimulationParameters(seconds_per_timestep=900),
        )
        determinants = inputs.billing[0]
        assert determinants.energy_bought_in_kwh == pytest.approx(24.0)
        assert determinants.energy_sold_in_kwh == pytest.approx(12.0)
        assert determinants.annual_peak_in_kw == pytest.approx(8.0)

    def test_a_declared_fuel_flow_reaches_the_billing_determinants_untouched(self):
        """A hook declares kWh and kWh is what gets billed — nothing rescales it (D26).

        Rewritten from `..._conversion_still_applies_to_a_declared_flow`, which asserted 5 liters
        and so pinned exactly the behaviour review finding 11 objected to. The MeterSpec still
        contributes capacity peaks on top of a declared flow; it no longer contributes a unit.
        """
        meter = FuelMeter()
        meter.get_energy_flow_facts = lambda all_outputs, results: EnergyFlowFacts(
            carrier=EnergyCarrier.HEATING_OIL, energy_bought_in_kwh=50.0
        )
        inputs = build_evaluation_inputs(
            [_Wrapper(meter)], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.billing[0].energy_bought_in_kwh == pytest.approx(50.0)


class TestFaithfulness:
    """The written file is a pure simulation extract (W1.1) — no cost-database filtering."""

    def test_unresolvable_subject_is_written_and_then_fails_the_evaluation(self, tmp_path):
        """The file holds every extracted subject; evaluating it is a hard error (§8, D7)."""
        from hisim.economics.database import CostDatabase
        from hisim.economics.evaluator import (
            EconomicEvaluator,
            UnresolvableSubjectsError,
            require_resolvable_subjects,
        )
        from hisim.economics.parameters import EconomicParameters
        from hisim.economics.serialization import write_inputs

        database = CostDatabase()
        assert not database.has_device_entry(ComponentType.WINDTURBINE, "DE")  # premise of the test
        components = [
            _Wrapper(FakeHeatPump("Priceable")),
            _Wrapper(FakeHeatPump("Unpriceable", asset_class=ComponentType.WINDTURBINE)),
        ]
        inputs = build_evaluation_inputs(components, [], pd.DataFrame(), _SimulationParameters())
        write_inputs(inputs, str(tmp_path))

        with open(tmp_path / "economic_inputs.json", encoding="utf-8") as file:
            written = json.load(file)
        assert [item["subject"] for item in written["cost_facts"]] == ["Priceable", "Unpriceable"]

        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2024))
        with pytest.raises(UnresolvableSubjectsError) as raised:
            require_resolvable_subjects(inputs, evaluator)
        message = str(raised.value)
        assert "Unpriceable" in message  # the blocked subject is named ...
        assert "windturbine" in message.lower()  # ... with the database's reason
        assert "  - Priceable:" not in message  # the resolvable subject is not blamed
        assert [problem.subject for problem in raised.value.problems] == ["Unpriceable"]
        # The caller's inputs (the object that was written) are untouched.
        assert len(inputs.cost_facts) == 2


class TestTheElectricityMeterHookRefusesAMissingColumn:
    """§3.4: the adopted hook and the `MeterSpec` fallback must refuse the same run."""

    def _meter(self):
        """A real `ElectricityMeter`, constructed without running `__init__`.

        The hook reads nothing but `self.component_name` and the two class-level output constants,
        so building the component properly — config, simulation parameters, display config, output
        declarations — would add construction cost without adding coverage.
        """
        from hisim.components.electricity_meter import ElectricityMeter as RealElectricityMeter

        meter = RealElectricityMeter.__new__(RealElectricityMeter)
        meter.component_name = "ElectricityMeter"
        return meter

    def _output(self, field_name: str):
        """One declared output of that meter, in watt-hours."""
        return _Output("ElectricityMeter", field_name, unit=lt.Units.WATT_HOUR)

    def test_both_columns_present_reports_both_flows(self):
        """The ordinary case still integrates both directions into kWh."""
        meter = self._meter()
        outputs = [self._output(meter.ElectricityFromGrid), self._output(meter.ElectricityToGrid)]
        frame = pd.DataFrame({0: [1000.0, 1000.0], 1: [500.0, 500.0]})

        flows = meter.get_energy_flow_facts(outputs, frame)

        assert flows.energy_bought_in_kwh == pytest.approx(2.0)
        assert flows.energy_sold_in_kwh == pytest.approx(1.0)

    @pytest.mark.parametrize("missing", ["ElectricityFromGrid", "ElectricityToGrid"])
    def test_a_missing_column_raises_instead_of_reporting_zero(self, missing):
        """Catches a grid bill or a feed-in revenue of zero being published as if measured.

        Both columns are declarations of this meter's class, so a run without one is a broken
        extraction, not an unused direction. The hook initialised both totals to 0.0 and returned
        them, which publishes a bill missing a flow — while the bridge's `MeterSpec` fallback has
        always refused the same run. Same defect, same refusal, whichever path reads the meter.
        """
        meter = self._meter()
        present = [name for name in ("ElectricityFromGrid", "ElectricityToGrid") if name != missing]
        outputs = [self._output(name) for name in present]
        frame = pd.DataFrame({0: [1000.0, 1000.0]})

        with pytest.raises(CostDataError) as raised:
            meter.get_energy_flow_facts(outputs, frame)

        assert missing in str(raised.value)
        assert "ElectricityMeter" in str(raised.value)
        assert "D7" in str(raised.value)


class TestOverrideSourceCoversEveryOverride:
    """§3.10: whatever is overridden needs provenance, not only an overridden investment."""

    def test_a_lifetime_only_override_still_carries_its_source(self):
        """Catches a config-declared lifetime or CO2 override arriving unattributed.

        `override_source` used to be set only when `investment_costs_in_euro` was present, so a
        config that overrode the lifetime alone produced facts that `has_overrides()` reports as
        overridden with no source behind them — which strict mode (§9.3) rejects and the provenance
        ledger records as an unattributed number.
        """
        from hisim.components.generic_pv_system import PVSystem

        pv_system = PVSystem.__new__(PVSystem)
        pv_system.config = _PvConfigStub(lifetime_in_years=30.0)

        facts = pv_system.get_cost_facts()

        assert facts.lifetime_override_in_years == 30.0
        assert facts.has_overrides()
        assert facts.override_source

    def test_no_override_at_all_still_carries_no_source(self):
        """The other half: a component that overrides nothing must not claim a source."""
        from hisim.components.generic_pv_system import PVSystem

        pv_system = PVSystem.__new__(PVSystem)
        pv_system.config = _PvConfigStub()

        facts = pv_system.get_cost_facts()

        assert not facts.has_overrides()
        assert facts.override_source is None


class _PvConfigStub:
    """The five `PVSystemConfig` fields `PVSystem.get_cost_facts` reads."""

    def __init__(
        self,
        investment_costs_in_euro=None,
        lifetime_in_years=None,
        device_co2_footprint_in_kg=None,
    ) -> None:
        """A 5 kW array with the given (optional) per-field overrides."""
        self.power_in_watt = 5000.0
        self.investment_costs_in_euro = investment_costs_in_euro
        self.lifetime_in_years = lifetime_in_years
        self.device_co2_footprint_in_kg = device_co2_footprint_in_kg


def _device(class_name: str, **constants: str):
    """A component stub carrying the class name and the output constants the table refers to.

    `adapter.DeviceEnergySpecs` is keyed by class name, exactly like the cost-facts table, and its
    rows name *constants* on that class rather than column names, so a stub needs both halves of
    the contract — and building it with `type()` states that instead of hiding it behind a
    hand-written class whose name happens to match. `component_name` is the only attribute the
    collector reads off the instance.

    Args:
        class_name: The component class name the table is expected to know.
        **constants: The output-name constants the row names, mapped to the column names the real
            class declares. `TestTheEnergyBalanceTableMatchesTheRealClasses` is what pins these
            against the real classes; here they only have to exist.

    Returns:
        An instance of a freshly made class of that name.
    """
    return type(class_name, (), {"component_name": class_name, **constants})()


class TestDeviceEnergyFlows:
    """The household energy balance's collector: role -> kWh, unit-aware and sign-aware.

    Three things can go wrong here and each of them produces a chart that is silently wrong rather
    than a run that fails: a power column summed as if it were energy (a factor of 3,600 over the
    timestep), a kWh column divided by a thousand a second time, and a battery's signed channel
    summed net so the round trip disappears. Every expected value below is hand-computed from the
    stub series, and the timestep is 900 s so the watt conversion is a visible 0.25 h per step.

    The collector is deliberately exercised directly rather than through
    `build_evaluation_inputs`, because the question is the conversion table and the sign split —
    everything the surrounding walk adds (cost relevance, meter contracts, the D7 check) belongs
    to the cost path and would only obscure a unit bug.
    """

    SECONDS_PER_TIMESTEP = 900

    def _flows(self, component, columns, load_type=None):
        """Runs the collector over one component and the columns it declares.

        Args:
            component: The stub component; its class name selects the declared specs.
            columns: `(field_name, unit, values)` triples, in the frame's column order.
            load_type: The load type every declared column carries. Only the completeness check
                reads it, so it stays None unless a test is about a class outside the table.

        Returns:
            The role -> kWh map the collector produced.
        """
        outputs = [
            _Output(component.component_name, field_name, unit=unit, load_type=load_type)
            for field_name, unit, _ in columns
        ]
        frame = _results_frame([(field_name, values) for field_name, _, values in columns])
        return bridge._device_energy_flows(  # pylint: disable=protected-access
            component, outputs, frame, self.SECONDS_PER_TIMESTEP
        )

    def test_watt_hours_are_divided_by_a_thousand(self):
        """A Wh channel is energy already: 1,000 + 2,000 + 3,000 Wh = 6 kWh."""
        flows = self._flows(
            _device("PVSystem", ElectricityEnergyOutput="ElectricityEnergyOutput"),
            [("ElectricityEnergyOutput", lt.Units.WATT_HOUR, [1000.0, 2000.0, 3000.0])],
        )
        assert flows == {"PV_GENERATION": pytest.approx(6.0)}

    def test_kilowatt_hours_are_taken_as_they_are(self):
        """A kWh channel needs no conversion; a second division would be a factor of 1,000."""
        flows = self._flows(
            _device(
                "ElectricityMeter",
                ElectricityFromGrid="ElectricityFromGrid",
                ElectricityToGrid="ElectricityToGrid",
            ),
            [
                ("ElectricityFromGrid", lt.Units.KWH, [1.5, 2.5]),
                ("ElectricityToGrid", lt.Units.KWH, [0.25, 0.75]),
            ],
        )
        assert flows == {"GRID_IMPORT": pytest.approx(4.0), "GRID_EXPORT": pytest.approx(1.0)}

    def test_watts_are_integrated_over_the_timestep(self):
        """A power channel is integrated: 2 x 4,000 W over 900 s each = 2 kWh, not 8 kWh.

        The column name also differs from the constant that names it, which is the case the
        constant lookup exists for: the class calls the constant `ElectricalInputPowerTotal` and
        writes the column `ElectricalInputPowerTotalHeatpump`.
        """
        flows = self._flows(
            _device(
                "MoreAdvancedHeatPumpHPLib",
                ElectricalInputPowerTotal="ElectricalInputPowerTotalHeatpump",
            ),
            [("ElectricalInputPowerTotalHeatpump", lt.Units.WATT, [4000.0, 4000.0])],
        )
        assert flows == {"HEAT_PUMP_ELECTRICITY": pytest.approx(2.0)}

    def test_a_signed_battery_series_becomes_two_positive_roles(self):
        """Charging and discharging are two roles of one column, both positive magnitudes.

        4,000 + 1,000 W of charging over 0.25 h each = 1.25 kWh in; 2,000 W of discharging over
        0.25 h = 0.5 kWh out. Summing the column net would report 0.75 kWh of "something" and lose
        the round-trip loss the balance exists to show.
        """
        flows = self._flows(
            _device("Battery", AcBatteryPowerUsed="AcBatteryPowerUsed"),
            [("AcBatteryPowerUsed", lt.Units.WATT, [4000.0, -2000.0, 1000.0])],
        )
        assert flows == {
            "BATTERY_CHARGE": pytest.approx(1.25),
            "BATTERY_DISCHARGE": pytest.approx(0.5),
        }

    def test_an_unconvertible_unit_is_refused_rather_than_guessed_or_dropped(self):
        """A column that is neither energy nor power stops the run instead of vanishing.

        It used to be logged and skipped, which is the failure this whole section is about: the
        chart then draws a house with no PV and nothing on it says a column was dropped.
        """
        with pytest.raises(CostDataError, match="neither an energy nor a power unit"):
            self._flows(
                _device("PVSystem", ElectricityEnergyOutput="ElectricityEnergyOutput"),
                [("ElectricityEnergyOutput", lt.Units.CELSIUS, [1000.0, 2000.0])],
            )

    def test_a_listed_column_this_run_did_not_produce_is_refused(self):
        """A row naming a column the run does not contain is a renamed output, not an absence."""
        with pytest.raises(CostDataError, match="which this run did not produce"):
            self._flows(
                _device("PVSystem", ElectricityEnergyOutput="ElectricityEnergyOutput"),
                [("SomethingElse", lt.Units.WATT_HOUR, [1000.0])],
            )

    def test_a_row_naming_a_constant_the_class_does_not_declare_is_refused(self):
        """The constant is the contract; a class that lost it cannot say which column to read."""
        with pytest.raises(CostDataError, match="declares no output-name constant"):
            self._flows(_device("PVSystem"), [("ElectricityEnergyOutput", lt.Units.WATT_HOUR, [1.0])])

    def test_an_explicitly_empty_row_contributes_nothing_and_complains_about_nothing(self):
        """A controller's watts are an instruction, and the empty row is that decision written down.

        The EMS publishes electricity in watts, so the completeness check would refuse it; its
        empty row is what says a person looked and decided it is not a flow across a balance node.
        """
        flows = self._flows(
            _device("L2GenericEnergyManagementSystem"),
            [("TotalElectricityToOrFromGrid", lt.Units.WATT, [4000.0, 4000.0])],
        )
        assert not flows

    def test_a_class_outside_the_table_that_publishes_electricity_is_refused(self):
        """The table is the one statement of who is in the balance, so silence is not an answer.

        A class nobody has classified might be a device whose kilowatt hours belong on the chart
        or a controller whose watts are a signal; the collector cannot tell, and used to assume
        the second.
        """
        with pytest.raises(CostDataError, match="no row in adapter.DeviceEnergySpecs"):
            self._flows(
                _device("SomeBrandNewInverter"),
                [("ElectricityOutput", lt.Units.WATT, [4000.0])],
                load_type=lt.LoadTypes.ELECTRICITY,
            )

    def test_a_class_outside_the_table_that_publishes_no_electricity_is_fine(self):
        """Most components move no electricity across a balance node; that is not a defect."""
        assert not self._flows(
            _device("SomeThermalThing"), [("ThermalPower", lt.Units.WATT, [4000.0])]
        )

    def test_a_component_the_table_does_not_know_contributes_nothing(self):
        """A component that declares no outputs at all cannot be in the balance either way."""
        assert not self._flows(FakeHeatPump(), [])

    def test_a_role_that_comes_out_negative_is_refused(self):
        """Direction is the role, so a negative magnitude means the row named the wrong column."""
        with pytest.raises(CostDataError, match="carries negative energy"):
            self._flows(
                _device("PVSystem", ElectricityEnergyOutput="ElectricityEnergyOutput"),
                [("ElectricityEnergyOutput", lt.Units.WATT_HOUR, [-1000.0, -2000.0])],
            )

    def test_the_collector_reaches_the_extract_for_every_component(self):
        """A meter contributes its two grid roles alongside its billing determinants.

        The energy question is asked before and independently of the cost-relevance branch, so
        this also pins that a component's flows are not conditional on it being priced.
        """
        meter = ElectricityMeter()
        outputs = [
            _Output("ElectricityMeter", "ElectricityFromGrid", unit=lt.Units.WATT_HOUR),
            _Output("ElectricityMeter", "ElectricityToGrid", unit=lt.Units.WATT_HOUR),
            _Output("ElectricityMeter", "ElectricityFromGridInWatt", unit=lt.Units.WATT),
        ]
        frame = _results_frame(
            [
                ("from_grid", [1000.0] * 96),
                ("to_grid", [500.0] * 96),
                ("power", [4000.0] * 96),
            ]
        )
        inputs = bridge.build_evaluation_inputs(
            [_Wrapper(meter)], outputs, frame, _SimulationParameters(days=1)
        )
        attribution = inputs.energy_attribution_by_subject_in_kwh["ElectricityMeter"]
        assert attribution["GRID_IMPORT"] == pytest.approx(96.0)
        assert attribution["GRID_EXPORT"] == pytest.approx(48.0)

    def test_a_free_of_cost_device_still_contributes_its_flows(self):
        """The balance is physics: a PV array nobody prices still generates the kilowatt hours.

        The class above claims the energy question is asked independently of cost relevance, but
        only ever ran a priced meter, so the branch that would have made the flows conditional on
        pricing was never exercised. This runs a `FREE_OF_COST` device through the same walk.
        """
        pv_system = _device("PVSystem", ElectricityEnergyOutput="ElectricityEnergyOutput")
        type(pv_system).cost_relevance = CostRelevance.FREE_OF_COST
        outputs = [_Output("PVSystem", "ElectricityEnergyOutput", unit=lt.Units.WATT_HOUR)]
        inputs = bridge.build_evaluation_inputs(
            [_Wrapper(pv_system)],
            outputs,
            _results_frame([("generation", [1000.0] * 96)]),
            _SimulationParameters(days=1),
        )
        assert inputs.cost_facts == [] and inputs.unresolved_subjects == []
        assert inputs.energy_attribution_by_subject_in_kwh == {
            "PVSystem": {"PV_GENERATION": pytest.approx(96.0)}
        }

    def test_a_missing_device_column_becomes_an_unresolved_subject(self):
        """The extract records the failure and the D7 check refuses to price around it."""
        pv_system = _device("PVSystem", ElectricityEnergyOutput="ElectricityEnergyOutput")
        type(pv_system).cost_relevance = CostRelevance.FREE_OF_COST
        inputs = bridge.build_evaluation_inputs(
            [_Wrapper(pv_system)],
            [_Output("PVSystem", "SomethingElse", unit=lt.Units.WATT_HOUR)],
            _results_frame([("other", [1000.0])]),
            _SimulationParameters(days=1),
        )
        assert [item.subject for item in inputs.unresolved_subjects] == ["PVSystem"]
        assert "ElectricityEnergyOutput" in _fail_evaluation(inputs)

    def test_an_unlisted_electricity_carrying_class_becomes_an_unresolved_subject(self):
        """A device nobody classified stops the run rather than shrinking the chart in silence."""
        inverter = _device("SomeBrandNewInverter")
        type(inverter).cost_relevance = CostRelevance.FREE_OF_COST
        inputs = bridge.build_evaluation_inputs(
            [_Wrapper(inverter)],
            [
                _Output(
                    "SomeBrandNewInverter",
                    "ElectricityOutput",
                    unit=lt.Units.WATT,
                    load_type=lt.LoadTypes.ELECTRICITY,
                )
            ],
            _results_frame([("power", [4000.0])]),
            _SimulationParameters(days=1),
        )
        assert [item.subject for item in inputs.unresolved_subjects] == ["SomeBrandNewInverter"]
        assert "DeviceEnergySpecs" in _fail_evaluation(inputs)


#: Every class named by `adapter.DeviceEnergySpecs`, mapped to the module it lives in. Written out
#: rather than derived, because the whole point of the binding test below is that a table keyed by
#: class *name* — which is what keeps `hisim.economics` free of component imports — is checked
#: against the classes those names refer to.
_ENERGY_BALANCE_MODULES = {
    "PVSystem": "generic_pv_system",
    "Battery": "advanced_battery_bslib",
    "MoreAdvancedHeatPumpHPLib": "more_advanced_heat_pump_hplib",
    "GenericHeatPump": "generic_heat_pump",
    "UtspLpgConnector": "loadprofilegenerator_utsp_connector",
    "ElectricityMeter": "electricity_meter",
    "L2GenericEnergyManagementSystem": "controller_l2_energy_management_system",
    "FuelCellController": "controller_l1_fuel_cell",
    "L1Controller": "controller_l1_generic_ev_charge",
    "L1GenericElectrolyzerController": "controller_l1_electrolyzer",
    "PTXController": "controller_l2_ptx_energy_management_system",
    "RsocBatteryController": "controller_l2_rsoc_battery_system",
    "XTPController": "controller_l2_xtp_fuel_cell_ems",
    "ExampleComponent": "example_component",
    "ComponentName": "example_template",
    "CarBattery": "advanced_ev_battery_bslib",
    "Car": "generic_car",
    "ElectricHeating": "generic_electric_heating",
    "AirConditioner": "air_conditioner",
    "SimpleAirConditioner": "simple_air_conditioner",
    "SmartDevice": "generic_smart_device",
    "SolarThermalSystem": "solar_thermal_system",
    "AdvancedElectrolyzer": "generic_electrolyzer_and_h2_storage",
    "Electrolyzer": "generic_electrolyzer_h2",
    "GenericElectrolyzer": "generic_electrolyzer",
    "HydrogenStorage": "generic_electrolyzer_and_h2_storage",
    "FuelCell": "generic_fuel_cell",
    "Rsoc": "generic_rsoc",
    "CHP": "advanced_fuel_cell",
    "SimpleCHP": "generic_chp",
}

#: Unit values the energy collector can convert, i.e. the ones that make an electricity output
#: count as a flow the balance would have to place.
_CONVERTIBLE_UNITS = frozenset(bridge.EnergyUnitConversion.BY_UNIT)


def _declared_electricity_outputs(module_name: str, class_name: str):
    """Every `LoadTypes.ELECTRICITY` output one component class declares, read from its source.

    Parsed rather than instantiated: building a real `MoreAdvancedHeatPumpHPLib` needs a weather
    file, a config and a simulation, none of which say anything about the question here, which is
    purely "does this class declare this output". The declaration is a literal `add_output(...)`
    call in the class body's `__init__`, so the syntax tree is the faithful source.

    Args:
        module_name: The `hisim.components` submodule holding the class.
        class_name: The class whose declarations to collect.

    Returns:
        `{constant_name: unit_value}` for every electricity output the class declares through a
        `self.X` / `ClassName.X` constant, where `unit_value` is the `Units` member's value.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "hisim",
        "components",
        f"{module_name}.py",
    )
    with open(path, encoding="utf-8") as file:
        source = file.read()
    tree = ast.parse(source, filename=path)
    declared = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            function = call.func
            if not isinstance(function, ast.Attribute) or function.attr != "add_output":
                continue
            text = " ".join((ast.get_source_segment(source, call) or "").split())
            if "LoadTypes.ELECTRICITY" not in text:
                continue
            unit = re.search(r"Units\.([A-Z_]+)", text)
            arguments = [argument for keyword, argument in
                         [(kw.arg, kw.value) for kw in call.keywords] if keyword == "field_name"]
            if not arguments:
                arguments = call.args[1:2]
            for argument in arguments:
                if isinstance(argument, ast.Attribute):
                    declared[argument.attr] = unit.group(1) if unit else ""
    return declared


class TestTheEnergyBalanceTableMatchesTheRealClasses:
    """`adapter.DeviceEnergySpecs` against the components it names (review, decision 2).

    The table is keyed by class name and names output *constants* by name, which is what keeps
    `hisim.economics` free of component imports — and what makes it a set of strings nothing
    checks. These tests are the check: every key is a real class, every constant a real constant
    naming a real electricity output, and — the half that matters most — every component class in
    the tree that publishes convertible electricity has a row, so "not in the table" can only ever
    mean "nobody has looked at it yet", which is what the collector refuses runs over.
    """

    @pytest.mark.parametrize("class_name", sorted(adapter.DeviceEnergySpecs.BY_CLASS_NAME))
    def test_every_key_names_a_real_component_class(self, class_name):
        """A key that no longer matches a class is a row that can never fire again."""
        module = importlib.import_module(f"hisim.components.{_ENERGY_BALANCE_MODULES[class_name]}")
        assert hasattr(module, class_name)

    @pytest.mark.parametrize(
        "class_name",
        sorted(name for name, specs in adapter.DeviceEnergySpecs.BY_CLASS_NAME.items() if specs),
    )
    def test_every_spec_names_a_declared_electricity_output(self, class_name):
        """The constant exists on the class, holds a string, and names an electricity output.

        This is the check the hardcoded column names could not have: `MoreAdvancedHeatPumpHPLib`
        calls the constant `ElectricalInputPowerTotal` and writes the column
        `ElectricalInputPowerTotalHeatpump`, and a table spelling the column would keep pointing at
        a renamed constant and stop pointing at a renamed column.
        """
        module_name = _ENERGY_BALANCE_MODULES[class_name]
        component_class = getattr(importlib.import_module(f"hisim.components.{module_name}"), class_name)
        declared = _declared_electricity_outputs(module_name, class_name)
        for spec in adapter.DeviceEnergySpecs.BY_CLASS_NAME[class_name]:
            column = getattr(component_class, spec.output_constant, None)
            assert isinstance(column, str), f"{class_name}.{spec.output_constant} is not a constant"
            assert spec.output_constant in declared, (
                f"{class_name}.{spec.output_constant} names no declared electricity output"
            )
            assert declared[spec.output_constant] in _CONVERTIBLE_UNITS or declared[
                spec.output_constant
            ] in {"WATT", "WATT_HOUR", "KWH"}

    def test_every_electricity_carrying_class_in_the_tree_has_a_row(self):
        """The table is total, so an absent class is an omission rather than a decision.

        Scans `hisim/components` the way the collector's refusal does at runtime and asserts the
        two agree: a class that would be refused mid-run is a class this test names now, with the
        file it lives in, which is the difference between a maintainer adding a row and a user
        seeing an aborted simulation.
        """
        directory = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hisim", "components"
        )
        missing = []
        for file_name in sorted(os.listdir(directory)):
            if not file_name.endswith(".py"):
                continue
            module_name = file_name[:-3]
            with open(os.path.join(directory, file_name), encoding="utf-8") as file:
                tree = ast.parse(file.read(), filename=file_name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if node.name in adapter.DeviceEnergySpecs.BY_CLASS_NAME:
                    continue
                units = set(_declared_electricity_outputs(module_name, node.name).values())
                if units & {"WATT", "WATT_HOUR", "KWH"}:
                    missing.append(f"{node.name} ({file_name})")
        assert not missing, (
            "These component classes publish electricity the energy collector can convert and "
            "have no row in adapter.DeviceEnergySpecs, so a run containing one of them aborts: "
            + ", ".join(missing)
        )
