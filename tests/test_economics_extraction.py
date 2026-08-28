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
"""

# clean

import datetime
import json
from typing import Any

import pandas as pd
import pytest

from hisim import loadtypes as lt
from hisim.economics import adapter
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
    """Stand-in for hisim.component.ComponentOutput (only these two fields are read).

    The bridge locates a meter's series by finding the position of `(component_name, field_name)`
    in the list of all outputs and taking the results frame column at that index, so a fake needs
    nothing more than those two strings. Using a stub instead of the real class keeps the tests
    independent of whatever else `ComponentOutput` grows.
    """

    def __init__(self, component_name: str, field_name: str) -> None:
        self.component_name = component_name
        self.field_name = field_name


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

    def __init__(self, component_name: str = "FakeHeatPump", asset_class=ComponentType.HEAT_PUMP) -> None:
        """A priced component of the given asset class."""
        self.component_name = component_name
        self.config = _Config()
        self._asset_class = asset_class

    def get_cost_facts(self) -> ComponentCostFacts:
        """9 kW of whatever asset class the test asked for."""
        return ComponentCostFacts(asset_class=self._asset_class, size=9.0, size_unit=Units.KILOWATT)


class FakeController:
    """A component with no cost declaration at all (must be reported, never priced).

    Most components in a HiSim setup are controllers and have no cost of their own; §9.2 requires
    that they be visibly absent from the cost model rather than silently priced at zero. It has no
    `cost_relevance` and no adapter entry, which is the combination the bridge must skip.
    """

    def __init__(self) -> None:
        """A component with no cost declaration of any kind."""
        self.component_name = "FakeController"
        self.config = _Config()


class ElectricityMeter:
    """Class name matters: the adapter's meter table keys on it.

    The real electricity meter has not adopted `get_energy_flow_facts()` yet, so the adapter maps
    it by class name to a `MeterSpec` naming the output fields to read (grid import/export in Wh
    and the power series for peaks). The stub therefore has to carry that exact class name, and
    reproduces the double role of a meter: a billed carrier *and* a priced device of its own.
    """

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

    def test_undeclared_component_is_skipped(self):
        """Components without a declaration are not part of the cost model (§9.2)."""
        inputs = build_evaluation_inputs(
            [_Wrapper(FakeController())], [], pd.DataFrame(), _SimulationParameters()
        )
        assert inputs.cost_facts == []
        assert inputs.billing == []

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


class TestMeterExtraction:
    """Meter outputs -> BillingDeterminants."""

    def test_sum_output_column_converts_wh_to_kwh(self):
        """The bridge sums the Wh column and scales by 1e-3; unknown outputs give None."""
        all_outputs = [_Output("Meter", "Other"), _Output("Meter", "ElectricityFromGrid")]
        results = _results_frame([("a", [1000.0, 2000.0, 0.0]), ("b", [500.0, 250.0, 250.0])])
        assert _sum_output_column("Meter", "ElectricityFromGrid", all_outputs, results) == pytest.approx(1.0)
        assert _sum_output_column("Meter", "Missing", all_outputs, results) is None

    def test_electricity_meter_yields_bought_sold_and_peaks(self):
        """Bought/sold energy in kWh and the 15-minute peaks land on the determinants."""
        meter = ElectricityMeter()
        all_outputs = [
            _Output("ElectricityMeter", "ElectricityFromGrid"),
            _Output("ElectricityMeter", "ElectricityToGrid"),
            _Output("ElectricityMeter", "ElectricityFromGridInWatt"),
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

    @staticmethod
    def _fail_evaluation(inputs):
        """Runs the D7 check over extracted inputs and returns the raised error's message."""
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
        message = self._fail_evaluation(inputs)
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
        message = self._fail_evaluation(inputs)
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
        assert "Odd" in self._fail_evaluation(reloaded)


class TestEnergyFlowHookAdoption:
    """The §3.4 hook is the meter path; the adapter table is the fallback (issue #18)."""

    _ALL_OUTPUTS = [
        _Output("ElectricityMeter", "ElectricityFromGrid"),
        _Output("ElectricityMeter", "ElectricityToGrid"),
        _Output("ElectricityMeter", "ElectricityFromGridInWatt"),
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
