"""Engine tests: views, financing, CO2 accumulation, sign validation, maintenance (cost_spec.md §4-§6).

Second of the three engine test files (split per the PR-3 review's 500-line rule): the
OPERATING and MACROECONOMIC views, loan financing and the soft-loan award substitution
(review finding 6), operational CO2 accumulation across meters (finding 4), the W3.7 sign
validation, and maintenance/fixed-operation charging. The evaluation core is in
`test_economics_engine.py`; actor allocation and the levy are in
`test_economics_engine_actors.py`.
"""

from typing import Dict, Optional

import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
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
    investment_band: Optional[UncertainValue] = None,
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
        investment_band: A real (min, best_estimate, max) band, used instead of `investment` when the test
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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
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
            assert interest.best_estimate == 0.0
            assert repayment.best_estimate == pytest.approx(250.0)

    def test_annuity_loan_amortizes_fully(self):
        """Sum of repayments equals the principal; annuity is constant."""
        plan = FinancingPlan(nominal_interest_rate=0.04, term_in_years=20)
        _, schedule = loan_flows(plan, UncertainValue.exact(10000.0))
        total_repaid = sum(repayment.best_estimate for _, _, repayment in schedule)
        assert total_repaid == pytest.approx(10000.0, rel=1e-6)
        annuities = [interest.best_estimate + repayment.best_estimate for _, interest, repayment in schedule]
        assert max(annuities) - min(annuities) == pytest.approx(0.0, abs=1e-6)

    def test_interest_only_bullet(self):
        """Bullet loan: constant interest, full repayment in the last year."""
        plan = FinancingPlan(nominal_interest_rate=0.05, term_in_years=3, type=LoanType.INTEREST_ONLY_WITH_BULLET)
        _, schedule = loan_flows(plan, UncertainValue.exact(1000.0))
        assert [interest.best_estimate for _, interest, _ in schedule] == pytest.approx([50.0, 50.0, 50.0])
        assert schedule[-1][2].best_estimate == pytest.approx(1000.0)

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
        assert result.total_npv_in_euro.best_estimate == pytest.approx(1000.0)
        assert result.annual_cost_series_nominal_in_euro[0].best_estimate == pytest.approx(0.0)
        # 1000 EUR fully financed over 10 years at 0 % = 100 EUR/a of repayment, no interest.
        assert result.annual_cost_series_nominal_in_euro[1].best_estimate == pytest.approx(100.0)


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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
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
            # A band that straddles zero violates too: the rule is per slot, not only for the best estimate.
            (CostCategory.MAINTENANCE, UncertainValue(best_estimate=5.0, minimum=-1.0, maximum=9.0)),
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
        levy = UncertainValue(best_estimate=800.0, minimum=600.0, maximum=1000.0)
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
        assert all(entry.amount_in_euro.best_estimate == pytest.approx(320.0) for entry in maintenance)
        assert all(entry.amount_in_euro.best_estimate == pytest.approx(120.0) for entry in fixed)

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
        assert recurring[0].amount_in_euro.best_estimate == pytest.approx(expected_amount)

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
        per_year: Dict[int, float] = {}
        for entry in tenant_recurring:
            per_year[entry.year] = per_year.get(entry.year, 0.0) + entry.amount_in_euro.best_estimate
        assert set(per_year) == set(range(1, 11))
        # 50 % of the 320 EUR maintenance (apportionable share) + 100 % of the 120 EUR fixed
        # operation cost = 280 EUR/a on the tenant.
        assert all(value == pytest.approx(160.0 + 120.0) for value in per_year.values())
