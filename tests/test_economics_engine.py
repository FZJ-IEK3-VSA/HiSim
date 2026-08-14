"""Tests for the core lifecycle cost engine (cost_spec.md §3, §4, §9.4).

Engine math is tested against hand-computed VDI 2067 / EN 15459-style examples plus the
property tests listed in §9.4.

**Surface.** The evaluator and the calculators beneath it (cost-spec-v2 §2.3): the investment
schedule with replacements and residual value, brownfield context resolution, the operating-view
replacement reserve, maintenance vs. fixed operation, loan flows, energy billing over the
horizon, the macroeconomic accounting view, the actor allocation, the timeline's sign convention
and its discounting. Plus the `UncertainValue` slot semantics (§3.9) the whole engine is built on.

**How it covers it.** Two complementary regimes, and it matters which one a failing test is in.

*Hand-computed examples.* Most tests run with `zero_rate_parameters()` — interest, both escalation
rates and carbon pricing all zero — and fully overridden facts (`make_facts`), so the expected
value is arithmetic a reviewer can redo on paper: 1000 EUR + 10 x 10 EUR maintenance = 1100; a
10-year device over a 15-year horizon costs 1000 + 1000 and credits back 5/10 x 1000; a 1000 EUR
replacement prefunded over 15 years is a 1000 EUR reserve NPV. Where rates *are* switched on, the
expected value is written as the closed form itself (`1000 * 1.02**10 / 1.03**10`), never as a
literal. Consequently these tests do not move when price files change — the numbers come from the
overrides, not from the database. The few figures that *are* database-derived (the electricity and
feed-in working prices, the natural-gas emission factor, the gas boiler's like-for-like
replacement price behind the sunk-cost check) are read back out of the database by the test itself
and multiplied there, so those assertions also survive a price update.

*Property tests.* The §9.4 invariants that must hold for *all* inputs and cannot be checked by
any single example: degenerate bands in mean degenerate bands out, LOW <= AVERAGE <= HIGH on every
published figure, widening an input band never narrows the result, subject NPVs sum to the total
per slot, payer NPVs sum to the system NPV, and the zero-sum envelope semantics of B11 (AVERAGE
exact, min/max only contained, and only in the widening direction).

**Error class.** A failure here is a *formula* failure — the layer between "the right quantities
came in" (`test_economics_extraction.py`) and "the number was displayed correctly"
(`test_economics_views.py`, the report goldens). It is also, by construction, not a data failure:
the overrides mean a wrong price in a JSON file cannot turn these tests red. Several tests name
the defect they exist to prevent, and those names are worth following into
roadmap/cost-spec-v2.md §7: B2 (maintenance and fixed operation lumped into one entry, which
moved real money between landlord and tenant), B4 (`explain` listing entries the KPI never
contained), B11 (zero-sum under band mirroring) and W3.7 (entry-level sign validation).
"""

# clean

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.financing import FinancingPlan, LoanType, loan_flows
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    Accounting,
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyMode,
)
from hisim.economics.timeline import (
    Actor,
    CategoryRules,
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    expected_sign,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database.

    The evaluator needs a database even when nothing is looked up in it, because energy prices,
    emission factors and the legacy subsidy shim are resolved from it. Most tests here override
    every device figure, so the database only supplies what they deliberately want from it —
    energy prices and the gas-boiler replacement price behind the sunk-cost check. Module-scoped:
    loading and validating the data files is the expensive part.
    """
    return CostDatabase()


def make_facts(
    investment: float = 1000.0,
    lifetime: float = 10.0,
    maintenance_rate: float = 0.0,
    investment_band: UncertainValue = None,
    fixed_operation_cost: float = 0.0,
) -> ComponentCostFacts:
    """Fully overridden facts so tests do not depend on database values.

    Every cost-bearing field a device can have — investment, installation, service life,
    maintenance rate, fixed operation cost, embodied CO2 — is set explicitly, most of them to zero
    by default, so a test only sees the mechanism it switched on and every expected value is a
    function of round numbers stated at the call site. This is what makes the file immune to price
    updates: a data PR changes nothing here.

    Args:
        investment: Gross purchase price in euro, as an exact band.
        lifetime: Service life in years — decides replacement timing and the residual share.
        maintenance_rate: Annual maintenance as a *share of the gross investment*, not an amount.
        investment_band: A real (min, avg, max) band, used instead of `investment` when the test
            is about slot behavior rather than a point value.
        fixed_operation_cost: Absolute euro per year (metering fee, chimney sweep), the cost kind
            that must stay separate from maintenance (§7 B2).
    """
    return ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=investment_band or UncertainValue.exact(investment),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        lifetime_override_in_years=lifetime,
        maintenance_rate_override=UncertainValue.exact(maintenance_rate),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(fixed_operation_cost),
        embodied_co2_override_in_kg=0.0,
        override_source="unit test",
    )


def zero_rate_parameters(horizon: int = 10) -> EconomicParameters:
    """All rates zero: NPV must equal the plain sum (§9.4).

    Interest, general and investment escalation, every carrier's energy escalation, the feed-in
    escalation and carbon pricing are switched off, which collapses discounting and escalation to
    the identity and turns each expected value below into plain arithmetic on the inputs. That is
    the point: a failure then names the mechanism under test rather than the discounting, and the
    §9.4 property "NPV at 0 % interest = plain sum of the timeline" holds by construction.
    Country and price basis year are fixed at DE/2024 so the tests that *do* read a shipped price
    get a stable vintage.
    """
    return EconomicParameters(
        observation_period_in_years=horizon,
        interest_rate=0.0,
        general_price_escalation_rate=0.0,
        investment_price_escalation_rate=0.0,
        co2_price_scenario="none",
        energy_price_escalation_rates={carrier: 0.0 for carrier in EnergyCarrier},
        feed_in_escalation_rate=0.0,
        country="DE",
        price_basis_year=2024,
    )


#: The plainest perspective there is: everything is a new investment, no subsidies, no financing,
#: no actor split, financial accounting. Anything a result shows under it comes from the
#: investment/energy calculators alone, which is what makes it the baseline for hand computation.
GREENFIELD_GROSS = Perspective(
    id="test_greenfield_gross",
    installation_context=InstallationContext.GREENFIELD,
    subsidy_mode=SubsidyMode.none(),
)


class TestUncertainValue:
    """§3.9 semantics."""

    def test_band_order_enforced(self):
        """min <= avg <= max is an invariant."""
        with pytest.raises(ValueError):
            UncertainValue(average=1.0, minimum=2.0, maximum=3.0)

    def test_bare_number_means_exact(self):
        """A bare JSON number is a degenerate band."""
        band = UncertainValue.from_json(5.0)
        assert band.is_exact() and band.average == 5.0

    def test_revenue_mirroring_keeps_order(self):
        """Optimistic world takes the revenue maximum, sign flips, order holds."""
        revenue = UncertainValue(average=10.0, minimum=8.0, maximum=13.0).as_revenue()
        assert (revenue.minimum, revenue.average, revenue.maximum) == (-13.0, -10.0, -8.0)

    def test_slotwise_sum(self):
        """Aggregation is slot-wise."""
        total = UncertainValue.sum([UncertainValue(2, 1, 3), UncertainValue(20, 10, 30)])
        assert (total.minimum, total.average, total.maximum) == (11, 22, 33)


class TestHandComputedExamples:
    """VDI 2067 style hand examples (§9.4)."""

    def test_zero_rates_npv_equals_plain_sum(self, database):
        """Investment 1000, 10 a horizon = lifetime, 1 % maintenance, all rates 0."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0, 0.01))],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        # 1000 investment + 10 x 10 maintenance; no replacement (10 a not < 10 a horizon),
        # no residual (exactly written off at horizon end).
        assert result.total_npv_in_euro.average == pytest.approx(1100.0)
        assert result.equivalent_annual_cost_in_euro.average == pytest.approx(110.0)

    def test_replacement_and_residual_value(self, database):
        """Lifetime 10, horizon 15: one replacement at year 10, residual 5/10 at year 15."""
        params = zero_rate_parameters(horizon=15)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0))],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.INVESTMENT] == pytest.approx(1000.0)
        assert categories[CostCategory.REPLACEMENT] == pytest.approx(1000.0)
        assert categories[CostCategory.RESIDUAL_VALUE] == pytest.approx(-500.0)
        assert result.total_npv_in_euro.average == pytest.approx(1500.0)

    def test_replacement_escalation_and_discounting(self, database):
        """Escalated replacement, discounted; residual from the escalated last purchase."""
        params = zero_rate_parameters(horizon=15)
        params.interest_rate = 0.03
        params.investment_price_escalation_rate = 0.02
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0))],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        replacement_nominal = 1000.0 * 1.02**10
        expected_replacement_npv = replacement_nominal / 1.03**10
        residual_nominal = replacement_nominal * 5.0 / 10.0
        expected_residual_npv = -residual_nominal / 1.03**15
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.REPLACEMENT] == pytest.approx(expected_replacement_npv)
        assert categories[CostCategory.RESIDUAL_VALUE] == pytest.approx(expected_residual_npv)
        annuity = params.annuity_factor()
        assert result.equivalent_annual_cost_in_euro.average == pytest.approx(
            result.total_npv_in_euro.average * annuity
        )

    def test_energy_costs_flat_price(self, database):
        """5000 kWh at the 2024 DE electricity price, zero rates, 10 years."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=5000.0)],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        price = database.get_energy_price(EnergyCarrier.ELECTRICITY, 2024, "DE").working_price_in_euro_per_kwh.average
        assert result.total_npv_in_euro.average == pytest.approx(5000.0 * price * 10)

    def test_feed_in_is_negative_and_fixed_nominal(self, database):
        """EEG-style feed-in stays nominally fixed and reduces cost."""
        params = zero_rate_parameters(horizon=5)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=0.0, energy_sold_in_kwh=2000.0
                )
            ],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        feed_in_price = database.get_energy_price(
            EnergyCarrier.ELECTRICITY_FEED_IN, 2024, "DE"
        ).working_price_in_euro_per_kwh.average
        assert result.total_npv_in_euro.average == pytest.approx(-2000.0 * feed_in_price * 5)


class TestSlotProperties:
    """§3.9 / §9.4 property tests."""

    def test_degenerate_bands_make_all_slots_identical(self, database):
        """min = avg = max on every input -> LOW == AVERAGE == HIGH everywhere."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0, 0.02))],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0)],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        band = result.total_npv_in_euro
        assert band.minimum == pytest.approx(band.average) == pytest.approx(band.maximum)

    def test_every_total_satisfies_low_avg_high(self, database):
        """LOW <= AVERAGE <= HIGH on every result figure."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        band_facts = make_facts(investment_band=UncertainValue(average=1000, minimum=800, maximum=1400), lifetime=10.0)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", band_facts)],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        for band in [result.total_npv_in_euro, result.equivalent_annual_cost_in_euro] + list(
            result.npv_by_category.values()
        ):
            assert band.minimum <= band.average <= band.maximum

    def test_widening_a_band_never_narrows_the_result(self, database):
        """Monotonicity of the envelope (§9.4)."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters())

        def evaluate(band):
            inputs = EvaluationInputs(
                simulation_year=2024,
                simulated_period_fraction=1.0,
                cost_facts=[SubjectCostFacts("Device", make_facts(investment_band=band, lifetime=10.0))],
            )
            return evaluator.evaluate(inputs, GREENFIELD_GROSS).total_npv_in_euro

        narrow = evaluate(UncertainValue(1000, 900, 1100))
        wide = evaluate(UncertainValue(1000, 800, 1300))
        assert wide.maximum - wide.minimum >= narrow.maximum - narrow.minimum

    def test_subject_npvs_sum_to_total_per_slot(self, database):
        """The §7.4 reconciliation invariant, per slot."""
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2024))
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts("HP", make_facts(investment_band=UncertainValue(16000, 12500, 21000), lifetime=18.0)),
                SubjectCostFacts("Battery", make_facts(5000.0, 10.0)),
            ],
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=5000.0, energy_sold_in_kwh=1000.0
                )
            ],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        total = UncertainValue.sum(result.npv_by_component.values())
        for attribute in ("minimum", "average", "maximum"):
            assert getattr(total, attribute) == pytest.approx(getattr(result.total_npv_in_euro, attribute))


class TestBrownfieldAndStatusQuo:
    """§4.1 installation contexts."""

    def _register(self, age_years: int = 12, replaced: bool = False) -> ExistingAssetRegister:
        """A brownfield register holding one gas boiler of the given age.

        Age is what drives every §4.1 mechanic under test: the remaining life decides when the
        first replacement falls due, and whether it is inside the anyway-cost threshold. The
        `replaced` flag is the declaration that turns "kept" into "replaced by a heat pump" —
        without it a register entry means the asset stays, even when a new subject of another
        class is present.
        """
        asset = ExistingAsset(
            asset_class=ComponentType.GAS_HEATER,
            size=15.0,
            size_unit=Units.KILOWATT,
            installation_year=2024 - age_years,
            energy_carrier=EnergyCarrier.NATURAL_GAS,
            replaced_by_asset_classes=[ComponentType.HEAT_PUMP] if replaced else [],
        )
        return ExistingAssetRegister(assets=[asset])

    def test_kept_asset_costs_no_investment(self, database):
        """A component matched to a kept register asset only pays maintenance + replacement."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        facts = ComponentCostFacts(
            asset_class=ComponentType.GAS_HEATER,
            size=15.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue.exact(6000.0),
            lifetime_override_in_years=18.0,
            maintenance_rate_override=UncertainValue.exact(0.0),
            override_source="unit test",
        )
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("GasBoiler", facts)],
            existing_assets=self._register(age_years=12),
        )
        perspective = Perspective(
            id="brownfield", installation_context=InstallationContext.BROWNFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = evaluator.evaluate(inputs, perspective)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert CostCategory.INVESTMENT not in categories
        # age 12, lifetime 18 -> replacement at year 6, residual 14/18 of it at year 10.
        assert categories[CostCategory.REPLACEMENT] == pytest.approx(6000.0)
        assert categories[CostCategory.RESIDUAL_VALUE] == pytest.approx(-6000.0 * 14.0 / 18.0)

    def test_replaced_asset_yields_removal_sunk_cost_and_anyway_credit(self, database):
        """An almost-dead boiler replaced by a heat pump earns the anyway-cost credit."""
        params = zero_rate_parameters(horizon=20)
        evaluator = EconomicEvaluator(database, params)
        register = self._register(age_years=17, replaced=True)  # 1 a remaining of 18
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", make_facts(16000.0, 18.0))],
            existing_assets=register,
        )
        perspective = Perspective(
            id="brownfield", installation_context=InstallationContext.BROWNFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = evaluator.evaluate(inputs, perspective)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.INVESTMENT] == pytest.approx(16000.0)
        assert CostCategory.ANYWAY_COST_CREDIT in categories
        assert categories[CostCategory.ANYWAY_COST_CREDIT] < 0
        # Sunk book value: 1/18 of the like-for-like price, reported but not in the timeline.
        like_for_like = database.get_device_entry(ComponentType.GAS_HEATER, 2024, "DE").investment_for_size(15.0)
        assert result.sunk_cost_written_off_in_euro.average == pytest.approx(like_for_like.average / 18.0)

    def test_kept_asset_outliving_the_horizon_earns_no_residual(self, database):
        """A kept asset whose install was never charged gets no year-T residual (§3.6 rule 3).

        EN 15459 credits a residual value only for investments *made within* the calculation
        period. A two-year-old boiler with an 18-year life over a ten-year horizon is neither
        bought at year 0 (it is kept) nor replaced before year T, so the timeline never charges
        the installation the residual would be derived from — crediting one would be unmatched
        revenue in the BROWNFIELD/STATUS_QUO variants. The sibling
        `test_kept_asset_costs_no_investment` pins the other half of the rule: as soon as an
        in-horizon replacement *is* charged, the residual comes back.
        """
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        facts = ComponentCostFacts(
            asset_class=ComponentType.GAS_HEATER,
            size=15.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue.exact(6000.0),
            lifetime_override_in_years=18.0,
            maintenance_rate_override=UncertainValue.exact(0.0),
            override_source="unit test",
        )
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("GasBoiler", facts)],
            existing_assets=self._register(age_years=2),
        )
        perspective = Perspective(
            id="brownfield", installation_context=InstallationContext.BROWNFIELD, subsidy_mode=SubsidyMode.none()
        )
        result = evaluator.evaluate(inputs, perspective)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        # age 2, lifetime 18 -> first replacement at year 16, beyond the ten-year horizon.
        assert CostCategory.INVESTMENT not in categories
        assert CostCategory.REPLACEMENT not in categories
        assert CostCategory.RESIDUAL_VALUE not in categories

    def test_new_investment_still_earns_its_residual(self, database):
        """The gating of the residual on a charged install leaves a greenfield purchase alone.

        The counter-test to `test_kept_asset_outliving_the_horizon_earns_no_residual`: a device
        bought at year 0 *is* charged in the timeline, so the unused share of its service life is
        credited at year T exactly as before — 5 of 15 years here. Without this assertion the
        residual gating could be tightened into a rule that silently drops legitimate credits.
        """
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 15.0))],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.INVESTMENT] == pytest.approx(1000.0)
        assert categories[CostCategory.RESIDUAL_VALUE] == pytest.approx(-1000.0 * 5.0 / 15.0)

    def test_status_quo_charges_no_year0_investment(self, database):
        """The do-nothing reference still costs money later (replacements), not at year 0."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("GasBoiler", make_facts(6000.0, 18.0))],
            existing_assets=self._register(age_years=12),
        )
        perspective = Perspective(
            id="status_quo", installation_context=InstallationContext.STATUS_QUO, subsidy_mode=SubsidyMode.none()
        )
        result = evaluator.evaluate(inputs, perspective)
        assert CostCategory.INVESTMENT not in result.npv_by_category


class TestRegisteredAssetWithoutADatabaseEntry:
    """Issue #25c: a replaced asset the database cannot price must be declared, not guessed."""

    @staticmethod
    def _inputs(override=None) -> EvaluationInputs:
        """A heat pump replacing a registered WINDTURBINE — a class no shipped device file has.

        The asset class is chosen precisely because `CostDatabase` has no entry for it, which is
        the situation a register hits when a technology leaves the catalog. `override` is the
        escape hatch the register is supposed to use in that case: the like-for-like replacement
        price the engine can no longer look up.
        """
        asset = ExistingAsset(
            asset_class=ComponentType.WINDTURBINE,
            size=5.0,
            size_unit=Units.KILOWATT,
            installation_year=2023,  # 1 year old at the 2024 basis year: well inside any threshold
            replacement_cost_override_in_euro=override,
            replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
        )
        return EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", make_facts(16000.0, 18.0))],
            existing_assets=ExistingAssetRegister(assets=[asset]),
        )

    @staticmethod
    def _perspective() -> Perspective:
        """Brownfield: the only context in which a replaced asset is resolved at all."""
        return Perspective(
            id="brownfield", installation_context=InstallationContext.BROWNFIELD, subsidy_mode=SubsidyMode.none()
        )

    def test_missing_entry_without_an_override_is_a_hard_error(self, database):
        """The 0 EUR sunk cost and the 20-year guess were invented figures; now it raises."""
        from hisim.economics.database import CostDataError

        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=20))
        assert not database.has_device_entry(ComponentType.WINDTURBINE, "DE")  # premise
        with pytest.raises(CostDataError) as raised:
            evaluator.evaluate(self._inputs(), self._perspective())
        message = str(raised.value)
        assert "windturbine" in message.lower()
        assert "replacement_cost_override_in_euro" in message
        assert "DE" in message

    def test_a_declared_replacement_cost_still_works(self, database):
        """The designed escape hatch keeps working, fallback service life and all."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=20))
        result = evaluator.evaluate(
            self._inputs(override=UncertainValue.exact(9000.0)), self._perspective()
        )
        # Age 1 of the 20-year fallback life -> 19/20 of the declared price is still on the books.
        assert result.sunk_cost_written_off_in_euro.average == pytest.approx(9000.0 * 19.0 / 20.0)


class TestNegativeFlexibilityValue:
    """Issue #25b: the §8.5 clamp stays, but the condition stops being invisible."""

    @staticmethod
    def _inputs() -> EvaluationInputs:
        """A dynamic-tariff year whose load was timed *worse* than the flat mean price.

        1000 kWh integrated to 100 EUR while the unweighted mean spot price was 0.08 EUR/kWh: a
        flat profile would have paid 80 EUR, so the flexibility value is -20 EUR. That is what a
        controller optimizing the wrong signal produces, and the bill it yields looks entirely
        ordinary — which is why nothing but an explicit check finds it.
        """
        from hisim.economics.tariffs import SupplyKind, TariffContract, TariffSupply

        contract = TariffContract(
            id="TEST_DYNAMIC",
            carrier=EnergyCarrier.ELECTRICITY,
            country="DE",
            region=None,
            valid_from_year=2024,
            supply=TariffSupply(kind=SupplyKind.DYNAMIC, spot_series="test"),
            standing_charge_in_euro_per_year=UncertainValue.exact(0.0),
            source_ids=("src_test",),
        )
        return EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 15.0))],
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY,
                    energy_bought_in_kwh=1000.0,
                    cost_integrated_in_euro=100.0,
                    mean_spot_price_in_euro_per_kwh=0.08,
                )
            ],
            tariff_contracts={EnergyCarrier.ELECTRICITY: contract},
        )

    def test_the_raw_value_reaches_the_result_and_the_clamp_holds(self, database):
        """The headline bill is the unclamped integral; the negative figure travels alongside."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        result = evaluator.evaluate(self._inputs(), GREENFIELD_GROSS)
        assert result.raw_flexibility_value_by_carrier[EnergyCarrier.ELECTRICITY.value] == pytest.approx(-20.0)
        # Clamped to zero for the arithmetic: ten years of the 100 EUR integral, unescalated,
        # with no flexibility correction added or subtracted.
        working = result.npv_by_category[CostCategory.ENERGY_WORKING].average
        assert working == pytest.approx(10 * 100.0)

    def test_the_plausibility_panel_warns_about_it(self, database):
        """The condition surfaces as a WARN finding, engine-side (issue #25b)."""
        from hisim.economics.plausibility import CheckIds, CheckStatus, run_plausibility_checks
        from hisim.economics.results import EvaluationMatrix

        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        matrix = EvaluationMatrix()
        matrix.results[GREENFIELD_GROSS.id] = evaluator.evaluate(self._inputs(), GREENFIELD_GROSS)
        findings = [
            finding
            for finding in run_plausibility_checks(matrix)
            if finding.check_id == CheckIds.CHECK_FLEXIBILITY_VALUE
        ]
        assert len(findings) == 1
        assert findings[0].status == CheckStatus.WARN
        assert findings[0].value == pytest.approx(-20.0)
        assert EnergyCarrier.ELECTRICITY.value in findings[0].name

    def test_a_well_timed_load_produces_no_finding(self, database):
        """A positive flexibility value leaves the panel exactly as it was."""
        from hisim.economics.plausibility import CheckIds, run_plausibility_checks
        from hisim.economics.results import EvaluationMatrix

        inputs = self._inputs()
        inputs.billing[0].cost_integrated_in_euro = 70.0  # better than the 80 EUR flat profile
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=10))
        matrix = EvaluationMatrix()
        matrix.results[GREENFIELD_GROSS.id] = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        assert not [
            finding
            for finding in run_plausibility_checks(matrix)
            if finding.check_id == CheckIds.CHECK_FLEXIBILITY_VALUE
        ]


class TestOperatingView:
    """§4.2 operating-only with replacement reserve."""

    def test_replacement_reserve_prefunds_replacements(self, database):
        """The reserve annuity equals the discounted replacement cost annuitized."""
        params = zero_rate_parameters(horizon=15)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0))],
        )
        perspective = Perspective(
            id="operating", installation_context=InstallationContext.OPERATING_ONLY, subsidy_mode=SubsidyMode.none()
        )
        result = evaluator.evaluate(inputs, perspective)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert CostCategory.INVESTMENT not in categories
        assert CostCategory.REPLACEMENT not in categories
        # One 1000 EUR replacement, zero rates: reserve = 1000/15 per year, NPV = 1000.
        assert categories[CostCategory.REPLACEMENT_RESERVE] == pytest.approx(1000.0)


class TestFinancing:
    """§4.4 loan flows."""

    def test_annuity_loan_zero_rate_is_linear(self):
        """Zero interest: principal / term per year, no interest."""
        plan = FinancingPlan(nominal_interest_rate=0.0, term_in_years=4)
        _, schedule = loan_flows(plan, UncertainValue.exact(1000.0))
        assert len(schedule) == 4
        for _year, interest, repayment in schedule:
            assert interest.average == 0.0
            assert repayment.average == pytest.approx(250.0)

    def test_annuity_loan_amortizes_fully(self):
        """Sum of repayments equals the principal; annuity is constant."""
        plan = FinancingPlan(nominal_interest_rate=0.04, term_in_years=20)
        _, schedule = loan_flows(plan, UncertainValue.exact(10000.0))
        total_repaid = sum(repayment.average for _, _, repayment in schedule)
        assert total_repaid == pytest.approx(10000.0, rel=1e-6)
        annuities = [interest.average + repayment.average for _, interest, repayment in schedule]
        assert max(annuities) - min(annuities) == pytest.approx(0.0, abs=1e-6)

    def test_interest_only_bullet(self):
        """Bullet loan: constant interest, full repayment in the last year."""
        plan = FinancingPlan(nominal_interest_rate=0.05, term_in_years=3, type=LoanType.INTEREST_ONLY_WITH_BULLET)
        _, schedule = loan_flows(plan, UncertainValue.exact(1000.0))
        assert [interest.average for _, interest, _ in schedule] == pytest.approx([50.0, 50.0, 50.0])
        assert schedule[-1][2].average == pytest.approx(1000.0)

    def test_financing_changes_liquidity_not_categories(self, database):
        """A financed purchase replaces the year-0 outflow with loan flows."""
        params = zero_rate_parameters(horizon=10)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0))],
        )
        perspective = Perspective(
            id="financed",
            installation_context=InstallationContext.GREENFIELD,
            subsidy_mode=SubsidyMode.none(),
            financing=FinancingPlan(financed_share=1.0, nominal_interest_rate=0.0, term_in_years=10),
        )
        result = evaluator.evaluate(inputs, perspective)
        # Zero loan rate and zero discount rate: NPV unchanged, year-0 liquidity zero.
        assert result.total_npv_in_euro.average == pytest.approx(1000.0)
        assert result.annual_cost_series_nominal_in_euro[0].average == pytest.approx(0.0)
        # 1000 EUR fully financed over 10 years at 0 % = 100 EUR/a of repayment, no interest.
        assert result.annual_cost_series_nominal_in_euro[1].average == pytest.approx(100.0)


class TestSoftLoanAwardSubstitution:
    """§5.3: what a LOAN_TERMS award overrides on the financing plan, and what it inherits."""

    @staticmethod
    def _plan() -> FinancingPlan:
        """A market-rate plan with a plan-level repayment grant, to be overridden or inherited.

        Every value here is deliberately non-default and non-zero so that an inherited field can
        be told apart from a field the award happened to set to the same number: 4.5 % over 15
        years with a 10 % plan-level repayment grant, subsidized by the scheme the awards below
        cite.
        """
        return FinancingPlan(
            financed_share=1.0,
            nominal_interest_rate=0.045,
            term_in_years=15,
            subsidized_by_scheme_id="XX_SOFT_LOAN",
            repayment_grant_share=0.10,
        )

    @staticmethod
    def _decisions(**award_fields):
        """One applied LOAN_TERMS award for the plan's scheme, with the given loan fields.

        Wraps the award in the `SubsidyDecision` list `resolve_loan_plan` reads, so each test
        states only the award fields it is about. The measure subject is irrelevant to the
        substitution and is fixed.
        """
        from hisim.economics.subsidies import PayoutKind, SubsidyAward, SubsidyDecision

        award = SubsidyAward(scheme_id="XX_SOFT_LOAN", payout_kind=PayoutKind.LOAN_TERMS, **award_fields)
        return [SubsidyDecision(measure_subject="Device", applied=[award])]

    def test_zero_interest_award_is_not_replaced_by_the_market_rate(self):
        """A genuine 0.0 % soft loan survives the substitution (§5.3).

        The whole benefit of a KfW-style zero-interest loan is the rate, and 0.0 is falsy: an
        `or`-based fallback silently restored the plan's market rate and priced the loan as if no
        subsidy had been awarded. Term and grant come from the same award here, so the test also
        shows the three fields are substituted together.
        """
        from hisim.economics.calculators.financing_application import resolve_loan_plan

        resolved = resolve_loan_plan(
            self._plan(), self._decisions(loan_interest_rate=0.0, loan_term_in_years=20)
        )
        assert resolved.nominal_interest_rate == 0.0
        assert resolved.term_in_years == 20

    def test_award_without_terms_inherits_the_plans_own(self):
        """Unset award fields (None) leave the plan's rate, term and grant in place (§5.3)."""
        from hisim.economics.calculators.financing_application import resolve_loan_plan

        plan = self._plan()
        resolved = resolve_loan_plan(plan, self._decisions())
        assert resolved.nominal_interest_rate == pytest.approx(plan.nominal_interest_rate)
        assert resolved.term_in_years == plan.term_in_years
        assert resolved.repayment_grant_share == pytest.approx(plan.repayment_grant_share)

    def test_award_with_an_explicit_grant_overrides_the_plans(self):
        """A repayment grant stated by the award wins over the plan's, including an explicit 0.0.

        The mirror image of the inheritance case: the award is the scheme's decision, so whenever
        it states a Tilgungszuschuss share — 25 % here, or a deliberate 0.0 — that share is what
        the loan is written down by.
        """
        from hisim.economics.calculators.financing_application import resolve_loan_plan

        resolved = resolve_loan_plan(self._plan(), self._decisions(loan_repayment_grant_share=0.25))
        assert resolved.repayment_grant_share == pytest.approx(0.25)
        zeroed = resolve_loan_plan(self._plan(), self._decisions(loan_repayment_grant_share=0.0))
        assert zeroed.repayment_grant_share == pytest.approx(0.0)


class TestMacroeconomic:
    """§4.5 macroeconomic accounting."""

    def test_macro_strips_subsidies_and_adds_co2_damage(self, database):
        """No SUBSIDY flows; CO2_DAMAGE priced from operational emissions."""
        params = zero_rate_parameters(horizon=10)
        params.co2_damage_cost_in_euro_per_ton = 250.0
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            billing=[BillingDeterminants(carrier=EnergyCarrier.NATURAL_GAS, energy_bought_in_kwh=10000.0)],
        )
        perspective = Perspective(
            id="macro",
            installation_context=InstallationContext.GREENFIELD,
            subsidy_mode=SubsidyMode.none(),
            accounting=Accounting.MACROECONOMIC,
        )
        result = evaluator.evaluate(inputs, perspective)
        categories = {category: value.average for category, value in result.npv_by_category.items()}
        assert CostCategory.SUBSIDY not in categories
        factor = database.get_energy_price(EnergyCarrier.NATURAL_GAS, 2024, "DE").emission_factor_in_kg_per_kwh
        expected_damage_per_year = 10000.0 * factor * 250.0 / 1000.0
        assert categories[CostCategory.CO2_DAMAGE] == pytest.approx(expected_damage_per_year * 10)


class TestOperationalCo2Accumulation:
    """§3.8: the two operational-CO2 views of the same emissions must agree."""

    def test_two_records_of_one_carrier_accumulate_in_both_views(self):
        """Per-carrier totals accumulate instead of overwriting (§3.8).

        A carrier can legitimately be billed by more than one meter — a house and a wallbox both
        buying electricity — and the energy calculator then emits one `CarrierEmissions` record
        per meter. The per-year series has always summed them; the per-carrier map assigned, so
        the first record's mass vanished from the carrier breakdown while still sitting in the
        yearly series. The invariant pinned here is that the two views describe one quantity:
        the carrier total equals the sum over the years.
        """
        from hisim.economics.calculators.co2 import accumulate_operational_emissions
        from hisim.economics.calculators.energy import CarrierEmissions, EnergyFlowResult
        from hisim.economics.results import LifecycleCo2Result

        horizon = 5
        energy_result = EnergyFlowResult(
            emissions=[
                CarrierEmissions(carrier_value=EnergyCarrier.ELECTRICITY.value, annual_emissions_in_kg=100.0),
                CarrierEmissions(carrier_value=EnergyCarrier.ELECTRICITY.value, annual_emissions_in_kg=250.0),
            ]
        )
        co2_result = LifecycleCo2Result(operational_co2_by_year_in_kg=[0.0] * (horizon + 1))
        accumulate_operational_emissions(energy_result, co2_result, horizon)
        by_carrier = co2_result.operational_co2_by_carrier_in_kg[EnergyCarrier.ELECTRICITY.value]
        assert by_carrier == pytest.approx((100.0 + 250.0) * horizon)
        assert by_carrier == pytest.approx(sum(co2_result.operational_co2_by_year_in_kg))


class TestSignValidation:
    """§3.9 / W3.7: cost positive, money arriving negative — enforced on `add`."""

    @staticmethod
    def _entry(category, amount, payer=Actor.SYSTEM):
        """One entry with the given category, amount band and payer.

        Year and subject are fixed because neither takes part in sign validation — the rule is a
        function of the category, and for MODERNIZATION_LEVY additionally of the payer, since the
        same category carries both legs of the transfer. Building entries by hand is the only way
        to test the guard: an entry the engine produced is sign-clean by definition.
        """
        return CashFlowEntry(
            year=1, amount_in_euro=amount, category=category, subject="device", payer=payer
        )

    def test_compliant_entries_are_accepted(self):
        """A positive cost and a mirrored revenue both pass."""
        timeline = CashFlowTimeline()
        timeline.add(self._entry(CostCategory.INVESTMENT, UncertainValue(1000.0, 900.0, 1200.0)))
        timeline.add(
            self._entry(
                CostCategory.FEED_IN_REVENUE, UncertainValue(1000.0, 900.0, 1200.0).as_revenue()
            )
        )
        assert timeline.sign_violations() == []

    @pytest.mark.parametrize(
        "category, amount",
        [
            (CostCategory.INVESTMENT, UncertainValue.exact(-1.0)),  # cost must not be negative
            (CostCategory.SUBSIDY, UncertainValue.exact(1.0)),  # support must not be positive
            # A band that straddles zero violates too: the rule is per slot, not per average.
            (CostCategory.MAINTENANCE, UncertainValue(average=5.0, minimum=-1.0, maximum=9.0)),
        ],
    )
    def test_violating_entries_are_rejected_on_add(self, category, amount):
        """`add` raises instead of silently accepting a wrong-signed entry."""
        timeline = CashFlowTimeline()
        with pytest.raises(ValueError, match="sign convention"):
            timeline.add(self._entry(category, amount))
        assert timeline.entries == []

    def test_revenue_banding_and_negative_sign_are_two_properties(self):
        """LOAN_DISBURSEMENT is negative-signed without being revenue-banded (W3.7)."""
        assert CostCategory.LOAN_DISBURSEMENT not in CategoryRules.REVENUE_CATEGORIES
        assert CostCategory.LOAN_DISBURSEMENT in CategoryRules.NEGATIVE_SIGN_CATEGORIES
        timeline = CashFlowTimeline()
        # The disbursement band tracks the investment it finances: not mirrored, but negative.
        timeline.add(self._entry(CostCategory.LOAN_DISBURSEMENT, UncertainValue(-800.0, -960.0, -720.0)))
        assert len(timeline.entries) == 1
        with pytest.raises(ValueError, match="sign convention"):
            timeline.add(self._entry(CostCategory.LOAN_DISBURSEMENT, UncertainValue.exact(800.0)))

    def test_modernization_levy_sign_depends_on_the_payer(self):
        """The tenant pays the levy, the landlord receives it — one category, two signs (§6.4)."""
        levy = UncertainValue(average=800.0, minimum=600.0, maximum=1000.0)
        assert expected_sign(self._entry(CostCategory.MODERNIZATION_LEVY, levy, Actor.TENANT)) == (
            "non-negative"
        )
        assert expected_sign(
            self._entry(CostCategory.MODERNIZATION_LEVY, levy.as_revenue(), Actor.LANDLORD)
        ) == "non-positive"
        timeline = CashFlowTimeline()
        timeline.add(self._entry(CostCategory.MODERNIZATION_LEVY, levy, Actor.TENANT))
        timeline.add(self._entry(CostCategory.MODERNIZATION_LEVY, levy.as_revenue(), Actor.LANDLORD))
        assert len(timeline.entries) == 2
        # The legs swapped: each is now wrong for its payer.
        for payer, amount in ((Actor.LANDLORD, levy), (Actor.TENANT, levy.as_revenue())):
            with pytest.raises(ValueError, match="sign convention"):
                timeline.add(self._entry(CostCategory.MODERNIZATION_LEVY, amount, payer))

    def test_validation_can_be_switched_off_for_synthetic_timelines(self):
        """Synthetic series with arbitrary signs opt out explicitly, and stay opted out."""
        timeline = CashFlowTimeline(validate=False)
        timeline.add(self._entry(CostCategory.INVESTMENT, UncertainValue.exact(-500.0)))
        assert len(timeline.sign_violations()) == 1
        assert timeline.filtered(lambda entry: True).validate is False
        with pytest.raises(ValueError, match="sign convention"):
            timeline.validate_signs()

    def test_engine_timelines_are_sign_clean(self, database):
        """An end-to-end evaluation, including allocation, produces no violation (W3.7)."""
        params = zero_rate_parameters(horizon=10)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", make_facts(16000.0, 18.0, 0.02, fixed_operation_cost=120.0))],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
            living_area_in_m2=120.0,
            current_cold_rent_in_euro_per_m2_month=8.0,
        )
        perspective = Perspective(
            id="sign_check",
            installation_context=InstallationContext.GREENFIELD,
            actor_scope=ActorScope.TENANT,
            financing=FinancingPlan(financed_share=0.8, nominal_interest_rate=0.03, term_in_years=10),
            subsidy_mode=SubsidyMode.none(),
        )
        result = evaluator.evaluate(inputs, perspective)
        assert result.timeline.sign_violations() == []
        assert any(
            entry.category == CostCategory.LOAN_DISBURSEMENT for entry in result.timeline.entries
        )
        assert any(
            entry.category == CostCategory.MODERNIZATION_LEVY for entry in result.timeline.entries
        )


class TestMaintenanceAndFixedOperation:
    """§3.6 rule 4 and §7 B2: the two recurring non-energy cost kinds stay apart."""

    def _entries(self, database, maintenance_rate: float, fixed_operation_cost: float, actor_scope):
        """Timeline entries for a 16 000 EUR heat pump carrying the two recurring cost kinds.

        The parameters are exactly the two dials the B2 split is about — a maintenance *rate* and
        an absolute fixed operation cost — plus the actor scope, because the whole point of the
        split is that the German ruleset apportions the two differently (maintenance is shared,
        fixed operation goes to the tenant in full). Living area and cold rent are set because the
        allocation ruleset needs them; zero rates keep the yearly amounts constant and readable.
        """
        params = zero_rate_parameters(horizon=10)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    make_facts(
                        16000.0, 18.0, maintenance_rate, fixed_operation_cost=fixed_operation_cost
                    ),
                )
            ],
            living_area_in_m2=120.0,
            current_cold_rent_in_euro_per_m2_month=8.0,
        )
        perspective = Perspective(
            id="maintenance_case",
            installation_context=InstallationContext.GREENFIELD,
            actor_scope=actor_scope,
            subsidy_mode=SubsidyMode.none(),
        )
        return evaluator.evaluate(inputs, perspective).timeline.entries

    def test_both_cost_kinds_get_their_own_entry(self, database):
        """A subject with both emits a MAINTENANCE *and* a FIXED_OPERATION entry per year (B2)."""
        entries = self._entries(database, 0.02, 120.0, ActorScope.SYSTEM)
        maintenance = [entry for entry in entries if entry.category == CostCategory.MAINTENANCE]
        fixed = [entry for entry in entries if entry.category == CostCategory.FIXED_OPERATION]
        assert len(maintenance) == 10 and len(fixed) == 10
        # 0.02 x 16,000 EUR gross investment = 320 EUR/a maintenance; the fixed operation cost is
        # an absolute amount and passes through unscaled.
        assert all(entry.amount_in_euro.average == pytest.approx(320.0) for entry in maintenance)
        assert all(entry.amount_in_euro.average == pytest.approx(120.0) for entry in fixed)

    @pytest.mark.parametrize(
        "maintenance_rate, fixed_operation_cost, expected_category, expected_amount",
        [
            (0.02, 0.0, CostCategory.MAINTENANCE, 320.0),
            (0.0, 120.0, CostCategory.FIXED_OPERATION, 120.0),
        ],
    )
    def test_a_single_cost_kind_emits_a_single_entry(
        self, database, maintenance_rate, fixed_operation_cost, expected_category, expected_amount
    ):
        """Subjects with only one of the two are unaffected by the B2 split."""
        entries = self._entries(database, maintenance_rate, fixed_operation_cost, ActorScope.SYSTEM)
        recurring = [
            entry
            for entry in entries
            if entry.category in (CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION)
        ]
        assert len(recurring) == 10
        assert {entry.category for entry in recurring} == {expected_category}
        assert recurring[0].amount_in_euro.average == pytest.approx(expected_amount)

    def test_the_split_moves_money_between_actors(self, database):
        """DE2024 apportions maintenance 50/50 but fixed operation fully to the tenant (§6.2).

        Under the pre-B2 heuristic the 120 EUR/a of fixed operation were labelled MAINTENANCE
        (because the maintenance share was positive) and the tenant paid only half of them:
        220 EUR/a instead of 280 EUR/a.
        """
        entries = self._entries(database, 0.02, 120.0, ActorScope.TENANT)
        tenant_recurring = [
            entry
            for entry in entries
            if entry.payer == Actor.TENANT
            and entry.category in (CostCategory.MAINTENANCE, CostCategory.FIXED_OPERATION)
        ]
        per_year = {}
        for entry in tenant_recurring:
            per_year[entry.year] = per_year.get(entry.year, 0.0) + entry.amount_in_euro.average
        assert set(per_year) == set(range(1, 11))
        # 50 % of the 320 EUR maintenance (apportionable share) + 100 % of the 120 EUR fixed
        # operation cost = 280 EUR/a on the tenant.
        assert all(value == pytest.approx(160.0 + 120.0) for value in per_year.values())


class TestActorAllocation:
    """§6 actor model."""

    def _tenant_result(self, database, emissions=None):
        """A tenant-scope evaluation of a rented building with a heat pump and an electricity bill.

        Living area and cold rent are what the DE_2024 ruleset needs to compute the modernization
        levy, so this fixture produces the full allocation: investment to the landlord, energy and
        the tenant's maintenance share to the tenant, and a mirrored levy pair between them.
        `emissions` is the building's specific emissions, which decide the CO2KostAufG tier — left
        None where a test does not care about the carbon split.
        """
        params = zero_rate_parameters(horizon=10)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", make_facts(16000.0, 18.0, 0.02))],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
            building_specific_emissions_in_kg_per_m2_a=emissions,
            living_area_in_m2=120.0,
            current_cold_rent_in_euro_per_m2_month=8.0,
        )
        perspective = Perspective(
            id="tenant",
            installation_context=InstallationContext.GREENFIELD,
            actor_scope=ActorScope.TENANT,
            subsidy_mode=SubsidyMode.none(),
        )
        return evaluator.evaluate(inputs, perspective)

    def test_zero_sum_reallocation(self, database):
        """sum(payer NPVs) == system NPV, per slot (§6.5)."""
        result = self._tenant_result(database)
        total = UncertainValue.sum(result.npv_by_payer.values())
        system_npv = result.timeline.npv(0.0)
        for attribute in ("minimum", "average", "maximum"):
            assert getattr(total, attribute) == pytest.approx(getattr(system_npv, attribute))

    def test_tenant_pays_energy_landlord_pays_investment(self, database):
        """BetrKV / HeizKV structure."""
        result = self._tenant_result(database)
        tenant_categories = {
            entry.category for entry in result.timeline.entries if entry.payer == Actor.TENANT
        }
        landlord_categories = {
            entry.category for entry in result.timeline.entries if entry.payer == Actor.LANDLORD
        }
        assert CostCategory.ENERGY_WORKING in tenant_categories
        assert CostCategory.INVESTMENT in landlord_categories
        assert CostCategory.INVESTMENT not in tenant_categories

    def test_explain_of_a_scoped_kpi_lists_only_that_scope(self, database):
        """§7 B4: `explain` filters the same timeline the KPI was computed from.

        A tenant-scope perspective's `total_npv_in_euro` is the NPV of the tenant's flows;
        before the fix `explain` walked the full allocated timeline, so it listed the
        landlord's investment among the entries "behind" a number that never contained it.
        """
        result = self._tenant_result(database)
        report = result.explain("total_npv_in_euro")
        payers = {
            entry.payer for entry in result.timeline.entries if entry.category == CostCategory.INVESTMENT
        }
        assert payers == {Actor.LANDLORD}  # the fixture really does allocate investment away
        assert CostCategory.INVESTMENT.value not in {entry.category for entry in report.entries}
        assert len(report.entries) == len(result.scoped_timeline().entries)
        # And the entries listed reconstruct the value they explain, to the cent.
        assert sum(entry.amount.average for entry in report.entries) == pytest.approx(
            result.total_npv_in_euro.average  # zero interest in this fixture
        )

    def test_explain_of_a_payer_pivot_stays_on_the_full_timeline(self, database):
        """`npv_by_payer[LANDLORD]` exists to show the *other* side; it is not scope-filtered."""
        result = self._tenant_result(database)
        report = result.explain(f"npv_by_payer[{Actor.LANDLORD.value}]")
        assert report.entries
        assert CostCategory.INVESTMENT.value in {entry.category for entry in report.entries}

    def test_modernization_levy_flows_are_mirrored(self, database):
        """Tenant pays the levy, landlord receives it (§6.4)."""
        result = self._tenant_result(database)
        levy_tenant = sum(
            entry.amount_in_euro.average
            for entry in result.timeline.entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.TENANT
        )
        levy_landlord = sum(
            entry.amount_in_euro.average
            for entry in result.timeline.entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.LANDLORD
        )
        assert levy_tenant > 0
        assert levy_landlord == pytest.approx(-levy_tenant)

    def test_zero_sum_envelope_semantics(self):
        """§6.5 / B11: AVERAGE exact, min/max only contained — and only in the widening direction."""
        from hisim.economics.actors import assert_zero_sum

        system = UncertainValue(average=100.0, minimum=90.0, maximum=120.0)
        # A minted transfer pair widens the payer band symmetrically: accepted.
        widened = [UncertainValue(average=100.0, minimum=80.0, maximum=130.0)]
        assert_zero_sum(system, widened)
        # ... but not under the strong form used for retag-only rulesets.
        with pytest.raises(AssertionError):
            assert_zero_sum(system, widened, require_per_slot_equality=True)
        # A *narrower* payer band means money vanished from a slot: rejected.
        with pytest.raises(AssertionError):
            assert_zero_sum(system, [UncertainValue(average=100.0, minimum=95.0, maximum=120.0)])
        with pytest.raises(AssertionError):
            assert_zero_sum(system, [UncertainValue(average=100.0, minimum=90.0, maximum=110.0)])
        # The AVERAGE slot is always exact.
        with pytest.raises(AssertionError):
            assert_zero_sum(system, [UncertainValue(average=101.0, minimum=80.0, maximum=130.0)])

    def test_co2_split_responds_to_building_emissions(self, database):
        """A dirtier building shifts CO2 price cost to the landlord (§6.3)."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        assert ruleset.tenant_co2_share(5.0) == pytest.approx(1.0)
        assert ruleset.tenant_co2_share(34.0) == pytest.approx(0.5)
        assert ruleset.tenant_co2_share(60.0) == pytest.approx(0.05)


class TestHeatingModernizationLevy:
    """§6.4 / D27: the §559e heating levy — 10 %, its own cap, nested inside the §559 cap.

    Every number below is hand-computable from the context declared in the test: a levy basis of
    `cost - subsidies - 0.3 x avoided maintenance`, a rate of 8 % (§559) or 10 % (§559e), and caps
    of `rate_in_euro_per_m2_per_month x 12 x living area`. The living area is always 100 m², so
    the general cap is 3,600 EUR/a (2,400 EUR/a below the low-rent threshold) and the heating cap
    600 EUR/a, which is what makes the cap interactions readable at a glance.

    The class exists because the split is the one place where two statutory percentages meet in
    one number: a wrong pool assignment, a forked deduction rule or a cap applied in the wrong
    order all produce a *plausible* rent increase that is simply not the law's.
    """

    LIVING_AREA_IN_M2 = 100.0

    def _context(self, subjects, rent=9.0, horizon=20, living_area=LIVING_AREA_IN_M2):
        """An allocation context whose aggregates are derived from the per-measure records.

        Building the aggregates by summation rather than by hand is deliberate: the context
        refuses a breakdown that does not add up (D7), so a test that got the aggregate wrong
        would fail for the wrong reason, and every test here is about the *split*, not about the
        bookkeeping that feeds it.
        """
        from hisim.economics.actors import AllocationContext

        return AllocationContext(
            horizon_years=horizon,
            living_area_in_m2=living_area,
            current_cold_rent_in_euro_per_m2_month=rent,
            modernization_cost_in_euro=UncertainValue.sum(
                part.modernization_cost_in_euro for part in subjects
            ),
            subsidies_received_in_euro=UncertainValue.sum(
                part.subsidies_received_in_euro for part in subjects
            ),
            avoided_maintenance_in_euro=UncertainValue.sum(
                part.avoided_maintenance_in_euro for part in subjects
            ),
            levy_subjects=list(subjects),
        )

    @staticmethod
    def _measure(asset_class_name, cost, subsidies=0.0, avoided=0.0):
        """One measure of a package, with exact (degenerate) bands."""
        from hisim.economics.actors import ModernizationLevySubjectBasis

        return ModernizationLevySubjectBasis(
            subject=f"{asset_class_name}.subject",
            asset_class_name=asset_class_name,
            modernization_cost_in_euro=UncertainValue.exact(cost),
            subsidies_received_in_euro=UncertainValue.exact(subsidies),
            avoided_maintenance_in_euro=UncertainValue.exact(avoided),
        )

    def test_pure_heating_package_is_levied_at_ten_percent_of_the_reduced_basis(self):
        """§559e rate on a subsidy- and maintenance-reduced basis, below its own cap."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        # 40,000 - 34,000 - 0.3 x 10,000 = 3,000 -> 10 % = 300 EUR/a, under the 600 EUR/a cap.
        context = self._context([self._measure("HEAT_PUMP", 40000.0, subsidies=34000.0, avoided=10000.0)])
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.average == pytest.approx(300.0)
        assert outcome.general_levy_in_euro.average == pytest.approx(0.0)
        assert outcome.total_in_euro.average == pytest.approx(300.0)

    def test_pure_heating_package_is_capped_at_fifty_cents_per_m2_and_month(self):
        """The §559e cap binds long before the general one: 0.50 x 12 x 100 = 600 EUR/a."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context([self._measure("HEAT_PUMP", 30000.0)])  # 10 % = 3,000 EUR/a raw
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.average == pytest.approx(600.0)
        assert outcome.total_in_euro.average == pytest.approx(600.0)

    def test_pure_envelope_package_keeps_the_eight_percent_paragraph(self):
        """Insulation is §559, not §559e — and the split path reproduces the aggregate one."""
        from hisim.economics.actors import AllocationContext, DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        measure = self._measure("WALL_INSULATION", 30000.0, subsidies=5000.0, avoided=10000.0)
        # 30,000 - 5,000 - 3,000 = 22,000 -> 8 % = 1,760 EUR/a, under the 3,600 EUR/a cap.
        outcome = ruleset.compute_modernization_levy(self._context([measure]))
        assert outcome.general_levy_in_euro.average == pytest.approx(1760.0)
        assert outcome.heating_levy_in_euro.average == pytest.approx(0.0)
        aggregate_only = AllocationContext(
            horizon_years=20,
            living_area_in_m2=self.LIVING_AREA_IN_M2,
            current_cold_rent_in_euro_per_m2_month=9.0,
            modernization_cost_in_euro=UncertainValue.exact(30000.0),
            subsidies_received_in_euro=UncertainValue.exact(5000.0),
            avoided_maintenance_in_euro=UncertainValue.exact(10000.0),
        )
        assert ruleset.compute_modernization_levy(aggregate_only).total_in_euro.average == pytest.approx(
            outcome.total_in_euro.average
        )

    def test_mixed_package_reduces_the_non_heating_leg_first(self):
        """Nested caps: heating capped at 600, then the general cap clips the §559 leg (D27).

        Heat pump 30,000 -> 3,000 raw -> 600 (§559e cap). Wall insulation 50,000 -> 4,000 raw.
        Their sum, 4,600, exceeds the 3,600 EUR/a general cap, so 1,000 EUR/a has to go: it comes
        entirely out of the §559 leg (4,000 -> 3,000), and the heating leg keeps its 600.
        """
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context(
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_INSULATION", 50000.0)]
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.average == pytest.approx(600.0)
        assert outcome.general_levy_in_euro.average == pytest.approx(3000.0)
        assert outcome.total_in_euro.average == pytest.approx(3600.0)

    def test_low_rent_tier_tightens_the_total_but_not_the_heating_cap(self):
        """Below the 7 EUR/m² threshold the total cap drops to 2,400 EUR/a; 600 stays 600."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context(
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_INSULATION", 50000.0)],
            rent=6.0,
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.average == pytest.approx(600.0)
        assert outcome.general_levy_in_euro.average == pytest.approx(1800.0)  # 2,400 - 600
        assert outcome.total_in_euro.average == pytest.approx(2400.0)

    def test_the_heating_leg_yields_only_when_it_exceeds_the_general_cap_alone(self):
        """The one case that touches the §559e leg: its own cap above the general cap."""
        from hisim.economics.actors import DE2024Ruleset, ModernizationLevyParameters

        ruleset = DE2024Ruleset(
            levy=ModernizationLevyParameters(
                heating_cap_in_euro_per_m2_per_month=5.0,  # deliberately above the 3.00 general cap
                heating_measure_component_types=frozenset({"HEAT_PUMP"}),
            )
        )
        context = self._context(
            [self._measure("HEAT_PUMP", 100000.0), self._measure("WALL_INSULATION", 10000.0)]
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.average == pytest.approx(3600.0)  # clipped to the total cap
        assert outcome.general_levy_in_euro.average == pytest.approx(0.0)  # non-heating went first
        assert outcome.total_in_euro.average == pytest.approx(3600.0)

    def test_without_a_living_area_neither_cap_can_bind(self):
        """§6.4's documented degradation: no area, no cap — for both paragraphs alike."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context(
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_INSULATION", 50000.0)],
            living_area=None,
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.average == pytest.approx(3000.0)
        assert outcome.general_levy_in_euro.average == pytest.approx(4000.0)

    def test_the_shipped_classification_list_is_read_from_the_allocation_data_file(self):
        """The list is data (D27): heat supply in, envelope and fossil boilers out."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        assert ruleset.levy.heating_measure_component_types == frozenset(
            {
                "HEAT_PUMP",
                "DISTRICT_HEATING",
                "SOLAR_THERMAL_SYSTEM",
                "PELLET_HEATER",
                "WOOD_CHIP_HEATER",
                "THERMAL_ENERGY_STORAGE",
            }
        )
        assert ruleset.levy.heating_levy_rate_per_year == pytest.approx(0.10)
        assert ruleset.levy.heating_cap_in_euro_per_m2_per_month == pytest.approx(0.50)
        assert ruleset.is_heating_measure("HEAT_PUMP")
        assert not ruleset.is_heating_measure("WALL_INSULATION")
        assert not ruleset.is_heating_measure("GAS_HEATER")  # fossil: not a §71 GEG option
        assert not ruleset.is_heating_measure(None)  # unattributed support is never §559e

    def test_an_unknown_component_type_in_the_classification_list_is_a_located_data_error(
        self, tmp_path
    ):
        """D7: a typo in the legal classification fails the load, naming file and value."""
        import json
        import os

        from hisim.economics.actors import DE2024Ruleset
        from hisim.economics.database import CostDataError

        shipped = os.path.join(DE2024Ruleset.DEFAULT_ALLOCATION_PATH, "allocation_DE_2024.json")
        with open(shipped, encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["modernization_levy"]["heating_measure_component_types"] = ["HEAT_PUMP", "HEAT_PUMPS"]
        (tmp_path / "allocation_DE_2024.json").write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(CostDataError) as error:
            DE2024Ruleset.load(str(tmp_path))
        assert "HEAT_PUMPS" in str(error.value)
        assert "allocation_DE_2024.json" in str(error.value)

    def test_a_breakdown_that_does_not_add_up_is_refused(self):
        """D7: the per-measure records must reconstruct the aggregate levy basis."""
        from hisim.economics.actors import AllocationContext

        with pytest.raises(ValueError, match="do not add up"):
            AllocationContext(
                horizon_years=20,
                modernization_cost_in_euro=UncertainValue.exact(50000.0),
                levy_subjects=[self._measure("HEAT_PUMP", 30000.0)],
            )

    def test_the_split_is_booked_as_one_transfer_pair_and_stays_zero_sum(self):
        """One aggregate pair per year (D27), mirrored, and §6.5 still holds per slot."""
        from hisim.economics.actors import DE2024Ruleset, assert_zero_sum

        ruleset = DE2024Ruleset.load()
        context = self._context(
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_INSULATION", 50000.0)],
            horizon=3,
        )
        entries = ruleset.modernization_levy_entries(context)
        assert len(entries) == 6  # three years x (tenant leg + landlord leg)
        assert {entry.subject for entry in entries} == {"modernization levy"}
        tenant = [entry for entry in entries if entry.payer == Actor.TENANT]
        assert all(entry.amount_in_euro.average == pytest.approx(3600.0) for entry in tenant)
        timeline = CashFlowTimeline()
        timeline.add(
            CashFlowEntry(
                year=0,
                amount_in_euro=UncertainValue.exact(80000.0),
                category=CostCategory.INVESTMENT,
                subject="package",
            )
        )
        allocated = ruleset.allocate(timeline, context)
        assert_zero_sum(
            timeline.npv(0.03),
            list(allocated.npv_by(0.03, lambda entry: entry.payer).values()),
            require_per_slot_equality=True,
        )


class TestResultQuantities:
    """W4.2: the physical context of an evaluation is carried on the result object."""

    def test_annualized_quantities_areas_and_fraction_land_on_the_result(self, database):
        """A half-year simulation reports full-year volumes, plus areas and the fraction."""
        params = zero_rate_parameters(horizon=10)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=0.5,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0))],
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY,
                    energy_bought_in_kwh=1500.0,
                    energy_sold_in_kwh=400.0,
                )
            ],
            heated_floor_area_in_m2=120.0,
            living_area_in_m2=100.0,
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        quantities = result.annual_energy_quantities_by_carrier["ELECTRICITY"]
        assert quantities.bought_in_kwh == pytest.approx(3000.0)  # 1500 / 0.5
        assert quantities.sold_in_kwh == pytest.approx(800.0)
        assert result.simulated_period_fraction == pytest.approx(0.5)
        assert result.reference_areas.heated_floor_area_in_m2 == pytest.approx(120.0)
        assert result.reference_areas.living_area_in_m2 == pytest.approx(100.0)
        # Living area wins for per-area figures (the precedence the reports have always used).
        assert result.reference_areas.preferred() == pytest.approx(100.0)

    def test_quantities_are_serialized_additively(self, database):
        """lifecycle_costs.json gains the new fields without losing any existing one."""
        params = zero_rate_parameters(horizon=10)
        evaluator = EconomicEvaluator(database, params)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0))],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=2000.0)],
            living_area_in_m2=90.0,
        )
        payload = evaluator.evaluate(inputs, GREENFIELD_GROSS).to_json()
        assert payload["annual_energy_quantities_by_carrier"]["ELECTRICITY"] == {
            "bought_in_kwh": 2000.0,
            "sold_in_kwh": 0.0,
        }
        assert payload["reference_areas"] == {"heated_floor_area_in_m2": None, "living_area_in_m2": 90.0}
        assert payload["simulated_period_fraction"] == 1.0
        for key in ("total_npv_in_euro", "npv_by_category", "component_breakdowns", "lifecycle_co2"):
            assert key in payload


class TestCanonicalDiscounting:
    """W4.3: one discount formula, everything routes through it."""

    def test_parameters_delegate_to_the_kernel_helper(self):
        """`EconomicParameters.discount_factor` is a wrapper, not a second implementation."""
        from hisim.economics.timeline import discount_factor

        params = EconomicParameters(interest_rate=0.037, observation_period_in_years=20)
        for year in (0, 1, 7, 20):
            assert params.discount_factor(year) == discount_factor(0.037, year)
            assert discount_factor(0.037, year) == pytest.approx(1.0 / (1.037 ** year))

    def test_timeline_npv_uses_the_same_factors(self):
        """A timeline NPV equals the hand-summed amount x discount factor."""
        timeline = CashFlowTimeline()
        for year, amount in ((0, 1000.0), (3, 250.0), (9, 75.0)):
            timeline.add(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=UncertainValue.exact(amount),
                    category=CostCategory.MAINTENANCE,
                    subject="Device",
                )
            )
        expected = sum(
            amount / (1.04 ** year) for year, amount in ((0, 1000.0), (3, 250.0), (9, 75.0))
        )
        assert timeline.npv(0.04).average == pytest.approx(expected)


class TestOneCanonicalBillDefinition:
    """Review finding 14: "what an energy bill is" is defined once, in the kernel."""

    def test_the_two_renderers_bind_the_kernel_set_itself(self):
        """Identity, not equality: a copy could be edited on one side and stay green here.

        The plausibility panel's effective-price check and report section 4 divide the same
        categories by the same quantity; while each held its own verbatim tuple, adding a fifth
        charge category to one of them would have left the panel validating a price the report
        does not show, with nothing red to say so.
        """
        from hisim.economics.plausibility import PlausibilityCategories
        from hisim.economics.views import ViewCategories

        assert PlausibilityCategories.BILL_CATEGORIES is CategoryRules.BILL_CATEGORIES
        assert ViewCategories.BILL_CATEGORIES is CategoryRules.BILL_CATEGORIES
        assert CostCategory.FEED_IN_REVENUE not in CategoryRules.BILL_CATEGORIES
