"""Property tests: money conservation and the numpy-financial cross-check (cost-spec-v2 §5.3).

Part of the invariant test files (split per the PR-3 review's 500-line rule): whole-engine
money conservation across random inputs, and the independent numpy-financial re-computation
of annuities and NPVs. Allocation properties are in
`test_economics_invariants_allocation.py`.
"""

import random
from typing import List, Optional, Tuple
import numpy_financial as npf
import pytest
from hisim.economics.actors import DE2024Ruleset
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.financing import FinancingPlan, loan_flows
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import ActorScope, InstallationContext, Perspective, SubsidyMode
from hisim.economics.subsidies import (
    Benefit,
    BenefitKind,
    Condition,
    EligibleCostSpec,
    OperationalBenefit,
    PayoutKind,
    ShareBenefit,
    SubsidyCatalog,
    SubsidyScheme,
    TaxCreditBenefit,
)
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CashFlowTimeline,
    CostCategory,
    SubjectKind,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

CASES = 100

FEW_CASES = 25

SEED_CONSERVATION = 20260817

SEED_NUMPY_FINANCIAL = 20260818

RANDOM_CATEGORIES = [
    CostCategory.INVESTMENT,
    CostCategory.PLANNING,
    CostCategory.REMOVAL,
    CostCategory.REPLACEMENT,
    CostCategory.RESIDUAL_VALUE,
    CostCategory.MAINTENANCE,
    CostCategory.FIXED_OPERATION,
    CostCategory.ENERGY_WORKING,
    CostCategory.ENERGY_STANDING,
    CostCategory.ENERGY_CO2_PRICE,
    CostCategory.ENERGY_CAPACITY_CHARGE,
    CostCategory.FEED_IN_REVENUE,
    CostCategory.SUBSIDY,
    CostCategory.LOAN_INTEREST,
    CostCategory.LOAN_PRINCIPAL,
    CostCategory.LOAN_DISBURSEMENT,
    CostCategory.CO2_DAMAGE,
    CostCategory.ANYWAY_COST_CREDIT,
    CostCategory.REPLACEMENT_RESERVE,
]

NEGATIVE_CATEGORIES = frozenset(
    {
        CostCategory.RESIDUAL_VALUE,
        CostCategory.FEED_IN_REVENUE,
        CostCategory.SUBSIDY,
        CostCategory.ANYWAY_COST_CREDIT,
        CostCategory.LOAN_DISBURSEMENT,
    }
)

SLOTS = ("minimum", "best_estimate", "maximum")


def cost_band(rng: random.Random, high: float = 20000.0) -> UncertainValue:
    """A random non-negative band (cost-type parameter)."""
    values = sorted(rng.uniform(0.0, high) for _ in range(3))
    return UncertainValue(best_estimate=values[1], minimum=values[0], maximum=values[2])


def maybe_exact(rng: random.Random, band: UncertainValue) -> UncertainValue:
    """Half the cases collapse to a degenerate band, so both regimes get exercised."""
    return UncertainValue.exact(band.best_estimate) if rng.random() < 0.5 else band


def random_timeline(rng: random.Random, horizon: int, count: int = 12) -> CashFlowTimeline:
    """A random timeline: random categories, years (some beyond the horizon) and amounts.

    Sign validation stays **on** here (W3.7): `NEGATIVE_CATEGORIES` above is this file's
    independent statement of which categories are negative-signed, so a validated timeline also
    checks that it still agrees with the engine's `timeline.CategoryRules.NEGATIVE_SIGN_CATEGORIES`.
    """
    timeline = CashFlowTimeline()
    subjects = ["HeatPump", "Battery", EnergyCarrier.ELECTRICITY.value, "financing"]
    for _ in range(count):
        category = rng.choice(RANDOM_CATEGORIES)
        amount = maybe_exact(rng, cost_band(rng, high=5000.0))
        if category in NEGATIVE_CATEGORIES:
            amount = amount.as_revenue()
        subject = rng.choice(subjects)
        timeline.add(
            CashFlowEntry(
                year=rng.randint(0, horizon + 3),
                amount_in_euro=amount,
                category=category,
                subject=subject,
                subject_kind=SubjectKind.CARRIER if subject.isupper() else SubjectKind.COMPONENT,
            )
        )
    return timeline


def slot_values(band: UncertainValue) -> Tuple[float, float, float]:
    """(minimum, best_estimate, maximum) as a plain tuple, for compact failure messages."""
    return (band.minimum, band.best_estimate, band.maximum)


def assert_bands_equal(actual: UncertainValue, expected: UncertainValue, context: str) -> None:
    """Slot-wise equality with a relative tolerance, reporting the generated case on failure."""
    for slot in SLOTS:
        assert getattr(actual, slot) == pytest.approx(getattr(expected, slot), rel=1e-9, abs=1e-9), (
            f"slot {slot}: {slot_values(actual)} != {slot_values(expected)} — {context}"
        )


def case_context(seed: int, case: int, **operands) -> str:
    """A reproduction hint: seed, case index and the generated operands."""
    rendered = ", ".join(f"{name}={value!r}" for name, value in operands.items())
    return f"case={case}, rng=random.Random({seed}), {rendered}"


def property_scheme(
    scheme_id: str, kind: BenefitKind, benefit: Benefit, payout: PayoutKind
) -> SubsidyScheme:
    """An always-eligible synthetic scheme for the conservation cases."""
    return SubsidyScheme(
        id=scheme_id,
        country="DE",
        region=None,
        valid_from="1900-01-01",
        valid_to=None,
        legal_basis="synthetic conservation-test scheme",
        url="https://example.invalid/conservation",
        asset_classes=[ComponentType.PV],
        measure_kinds=["INSTALL", "REPLACE"],
        eligibility=Condition(kind="all"),
        benefit_kind=kind,
        benefit=benefit,
        eligible_cost=EligibleCostSpec(),
        cumulation_group=None,
        combined_rate_cap=None,
        excludes=[],
        payout_kind=payout,
    )


def conservation_parameters(rng: Optional[random.Random] = None) -> EconomicParameters:
    """Deterministic (or randomized) parameters with a non-zero discount rate."""
    if rng is None:
        return EconomicParameters(
            price_basis_year=2024,
            observation_period_in_years=10,
            interest_rate=0.05,
            general_price_escalation_rate=0.0,
            investment_price_escalation_rate=0.0,
            energy_price_escalation_rates={carrier: 0.0 for carrier in EnergyCarrier},
        )
    return EconomicParameters(
        price_basis_year=2024,
        observation_period_in_years=rng.randint(5, 25),
        interest_rate=rng.choice([0.0, 0.02, 0.05]),
        general_price_escalation_rate=rng.choice([0.0, 0.02]),
        investment_price_escalation_rate=rng.choice([0.0, 0.02]),
    )


def pv_facts(investment: float, lifetime: float = 20.0) -> ComponentCostFacts:
    """Fully overridden PV facts so the case never depends on database values."""
    return ComponentCostFacts(
        asset_class=ComponentType.PV,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue.exact(investment),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        lifetime_override_in_years=lifetime,
        maintenance_rate_override=UncertainValue.exact(0.0),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(0.0),
        embodied_co2_override_in_kg=0.0,
        override_source="cost-spec-v2 §5.1 conservation test",
    )


OPERATIONAL_INVESTMENT_IN_EURO = 20000.0

OPERATIONAL_GRANT_RATE = 0.2

OPERATIONAL_RATE_PER_KWH = 0.05

OPERATIONAL_DURATION_YEARS = 5

OPERATIONAL_ENERGY_SOLD_IN_KWH = 3000.0


def operational_subsidy_case() -> Tuple[EconomicEvaluator, EvaluationInputs, Perspective]:
    """Landlord-scope PV evaluation with an upfront grant and an operational per-kWh support."""
    catalog = SubsidyCatalog(
        schemes=[
            property_scheme(
                "PROPERTY_GRANT",
                BenefitKind.SHARE_OF_ELIGIBLE_COST,
                ShareBenefit(rate=OPERATIONAL_GRANT_RATE),
                PayoutKind.UPFRONT_GRANT,
            ),
            property_scheme(
                "PROPERTY_OPERATIONAL",
                BenefitKind.OPERATIONAL,
                OperationalBenefit(
                    rate_per_kwh=OPERATIONAL_RATE_PER_KWH,
                    carrier=EnergyCarrier.ELECTRICITY,
                    duration_years=OPERATIONAL_DURATION_YEARS,
                ),
                PayoutKind.OPERATIONAL,
            ),
        ],
        questions={},
        snapshot_date=None,
        overall_cap_share=None,
        base_path="",
        country="DE",
    )
    evaluator = EconomicEvaluator(CostDatabase(), conservation_parameters(), catalog)
    inputs = EvaluationInputs(
        simulation_year=2024,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("PV", pv_facts(OPERATIONAL_INVESTMENT_IN_EURO))],
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=0.0,
                energy_sold_in_kwh=OPERATIONAL_ENERGY_SOLD_IN_KWH,
            )
        ],
    )
    perspective = Perspective(
        id="conservation_landlord",
        installation_context=InstallationContext.GREENFIELD,
        actor_scope=ActorScope.LANDLORD,
        subsidy_mode=SubsidyMode.full(),
    )
    return evaluator, inputs, perspective


def tax_credit_case() -> Tuple[EconomicEvaluator, EvaluationInputs, Perspective]:
    """Owner-occupier PV evaluation with an upfront grant and a three-year tax credit."""
    catalog = SubsidyCatalog(
        schemes=[
            property_scheme(
                "PROPERTY_GRANT",
                BenefitKind.SHARE_OF_ELIGIBLE_COST,
                ShareBenefit(rate=0.2),
                PayoutKind.UPFRONT_GRANT,
            ),
            property_scheme(
                "PROPERTY_TAX_CREDIT",
                BenefitKind.TAX_CREDIT,
                TaxCreditBenefit(rate=0.2, years=3),
                PayoutKind.TAX_CREDIT_SCHEDULE,
            ),
        ],
        questions={},
        snapshot_date=None,
        overall_cap_share=None,
        base_path="",
        country="DE",
    )
    evaluator = EconomicEvaluator(CostDatabase(), conservation_parameters(), catalog)
    inputs = EvaluationInputs(
        simulation_year=2024,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("PV", pv_facts(OPERATIONAL_INVESTMENT_IN_EURO))],
    )
    perspective = Perspective(
        id="conservation_owner",
        installation_context=InstallationContext.GREENFIELD,
        subsidy_mode=SubsidyMode.full(),
    )
    return evaluator, inputs, perspective


def discounted_sum(entries: List[CashFlowEntry], rate: float) -> UncertainValue:
    """The discounted total of the given entries, computed independently of CashFlowTimeline."""
    total = UncertainValue.exact(0.0)
    for entry in entries:
        total = total + entry.amount_in_euro.scale(1.0 / ((1.0 + rate) ** entry.year))
    return total


class TestMoneyConservation:
    """§5.1: every euro of an aggregate traces back to timeline entries."""

    def _random_evaluation(self, rng: random.Random):
        """A random but fully overridden evaluation (no database price dependence)."""
        parameters = conservation_parameters(rng)
        evaluator = EconomicEvaluator(CostDatabase(), parameters)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts("PV", pv_facts(rng.uniform(5000.0, 30000.0), rng.uniform(8.0, 25.0)))
            ],
            billing=[
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY,
                    energy_bought_in_kwh=rng.uniform(0.0, 8000.0),
                    energy_sold_in_kwh=rng.uniform(0.0, 4000.0),
                )
            ],
            living_area_in_m2=rng.choice([None, 120.0]),
            current_cold_rent_in_euro_per_m2_month=rng.choice([None, 8.0]),
            building_specific_emissions_in_kg_per_m2_a=rng.choice([None, 20.0, 45.0]),
        )
        perspective = Perspective(
            id="conservation",
            installation_context=rng.choice(
                [
                    InstallationContext.GREENFIELD,
                    InstallationContext.OPERATING_ONLY,
                ]
            ),
            actor_scope=rng.choice([ActorScope.SYSTEM, ActorScope.LANDLORD, ActorScope.TENANT]),
            subsidy_mode=SubsidyMode.none(),
        )
        return evaluator, inputs, perspective, parameters

    def test_total_npv_equals_the_discounted_scoped_timeline(self):
        """The headline NPV is exactly the discounted sum of the entries it is scoped to."""
        for case in range(FEW_CASES):
            seed = SEED_CONSERVATION + case
            rng = random.Random(seed)
            evaluator, inputs, perspective, parameters = self._random_evaluation(rng)
            result = evaluator.evaluate(inputs, perspective)
            scoped = [
                entry
                for entry in result.timeline.entries
                if perspective.actor_scope == ActorScope.SYSTEM or entry.payer == result.scope_payer
            ]
            report = case_context(
                seed, case, perspective=perspective.installation_context.value,
                actor=perspective.actor_scope.value, rate=parameters.interest_rate,
                entries=len(result.timeline.entries),
            )
            assert_bands_equal(
                result.total_npv_in_euro, discounted_sum(scoped, parameters.interest_rate), report
            )

    def test_category_and_component_pivots_sum_to_the_total(self):
        """Both reported pivots partition the same money (§7.4 reconciliation)."""
        for case in range(FEW_CASES):
            seed = SEED_CONSERVATION + case
            rng = random.Random(seed)
            evaluator, inputs, perspective, parameters = self._random_evaluation(rng)
            result = evaluator.evaluate(inputs, perspective)
            report = case_context(
                seed, case, actor=perspective.actor_scope.value, rate=parameters.interest_rate
            )
            assert_bands_equal(
                UncertainValue.sum(result.npv_by_category.values()), result.total_npv_in_euro, report
            )
            assert_bands_equal(
                UncertainValue.sum(result.npv_by_component.values()), result.total_npv_in_euro, report
            )
            assert_bands_equal(
                UncertainValue.sum(result.npv_by_payer.values()),
                result.timeline.npv(parameters.interest_rate),
                report,
            )

    def test_component_breakdowns_sum_to_the_total(self):
        """Per-subject breakdowns are a pivot of the same timeline, not a second computation."""
        for case in range(FEW_CASES):
            seed = SEED_CONSERVATION + case
            rng = random.Random(seed)
            evaluator, inputs, perspective, parameters = self._random_evaluation(rng)
            result = evaluator.evaluate(inputs, perspective)
            report = case_context(seed, case, actor=perspective.actor_scope.value)
            assert_bands_equal(
                UncertainValue.sum(
                    breakdown.total_npv_in_euro for breakdown in result.component_breakdowns.values()
                ),
                result.total_npv_in_euro,
                report,
            )
            assert result.parameters.interest_rate == parameters.interest_rate

    def test_subsidy_total_is_recoverable_from_the_timeline(self):
        """The levy basis must deduct exactly what the SUBSIDY entries say, in one unit.

        W3.4 (decided 2026-08-12): that unit is the **nominal** sum of every SUBSIDY entry —
        §559 BGB deducts the support *received*. The case pays an upfront grant plus five years
        of operational support, so nominal and discounted differ: the assertion has teeth.
        """
        evaluator, inputs, perspective = operational_subsidy_case()
        result = evaluator.evaluate(inputs, perspective)
        rate = result.parameters.interest_rate
        subsidy_entries = [
            entry for entry in result.timeline.entries if entry.category == CostCategory.SUBSIDY
        ]
        assert subsidy_entries, "the case must produce subsidy entries"
        nominal = UncertainValue.sum(entry.amount_in_euro for entry in subsidy_entries).as_revenue()
        discounted = discounted_sum(subsidy_entries, rate).as_revenue()
        assert nominal.best_estimate != pytest.approx(discounted.best_estimate, abs=1e-6), (
            "the case must distinguish the two units (payouts after year 0 at a non-zero rate)"
        )
        levy = next(
            entry
            for entry in result.timeline.entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.TENANT
        )
        ruleset = DE2024Ruleset.load()
        # basis = modernization cost - subsidies received (no avoided maintenance in greenfield).
        implied_basis = levy.amount_in_euro.best_estimate / ruleset.levy.levy_rate_per_year
        deducted = OPERATIONAL_INVESTMENT_IN_EURO - implied_basis
        assert deducted == pytest.approx(nominal.best_estimate, abs=1e-6), (
            f"the levy basis deducted {deducted:.2f} EUR of subsidies, but the timeline holds "
            f"{nominal.best_estimate:.2f} EUR nominal / {discounted.best_estimate:.2f} EUR discounted "
            f"(entries: {[(entry.year, entry.amount_in_euro.best_estimate) for entry in subsidy_entries]})"
        )

    def test_reported_subsidy_figures_share_one_unit(self):
        """Every reported subsidy figure names its unit and agrees with the timeline in it.

        W3.4: the breakdown used to carry a single `subsidies_in_euro` that summed nominal euros
        while `npv_by_category[SUBSIDY]` discounted them. The result object now reports both,
        each named after its unit, and each must reproduce the timeline exactly.
        """
        evaluator, inputs, perspective = tax_credit_case()
        result = evaluator.evaluate(inputs, perspective)
        breakdown = result.component_breakdowns["PV"]
        rate = result.parameters.interest_rate
        subsidy_entries = [
            entry for entry in result.timeline.entries if entry.category == CostCategory.SUBSIDY
        ]
        nominal = UncertainValue.sum(entry.amount_in_euro for entry in subsidy_entries).as_revenue()
        subsidy_npv = result.npv_by_category[CostCategory.SUBSIDY].as_revenue()
        assert breakdown.subsidies_nominal_in_euro.best_estimate == pytest.approx(nominal.best_estimate, rel=1e-9), (
            f"breakdown subsidies_nominal_in_euro={breakdown.subsidies_nominal_in_euro.best_estimate:.2f} EUR "
            f"vs the timeline's nominal SUBSIDY sum {nominal.best_estimate:.2f} EUR"
        )
        assert breakdown.subsidies_npv_in_euro.best_estimate == pytest.approx(subsidy_npv.best_estimate, rel=1e-9), (
            f"breakdown subsidies_npv_in_euro={breakdown.subsidies_npv_in_euro.best_estimate:.2f} EUR "
            f"vs npv_by_category[SUBSIDY]={subsidy_npv.best_estimate:.2f} EUR"
        )
        assert breakdown.subsidies_npv_in_euro.best_estimate == pytest.approx(
            discounted_sum(subsidy_entries, rate).as_revenue().best_estimate, rel=1e-9
        )
        # The tax credit pays out in years 1..3, so the two units must actually differ here —
        # otherwise this test would pass for a mixed-unit implementation too.
        assert breakdown.subsidies_nominal_in_euro.best_estimate > breakdown.subsidies_npv_in_euro.best_estimate


class TestNumpyFinancialCrossCheck:
    """§5.3: the closed-form parts checked against an independent implementation.

    `numpy_financial` is a third implementation of the same textbook formulas, so agreement
    on randomized inputs is evidence beyond "the code agrees with itself".

    Sign convention: `npf.pmt` returns the payment as a cash *outflow* (negative) for a
    positive principal, while `financing.loan_flows` reports costs positive — hence the
    leading minus in every comparison below.
    """

    def test_annuity_matches_numpy_financial_pmt(self):
        """Every year's annuity (interest + repayment) equals -npf.pmt(rate, term, principal)."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.uniform(0.001, 0.15)
            term = rng.randint(1, 40)
            principal = rng.uniform(1000.0, 500000.0)
            context = case_context(seed, case, rate=rate, term=term, principal=principal)
            plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
            _disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
            expected_annuity = -float(npf.pmt(rate, term, principal))
            assert len(schedule) == term, f"schedule has {len(schedule)} years — {context}"
            for year, interest, repayment in schedule:
                annuity = interest.best_estimate + repayment.best_estimate
                assert annuity == pytest.approx(expected_annuity, rel=1e-6), (
                    f"year {year} annuity {annuity} != npf.pmt {expected_annuity} — {context}"
                )

    def test_annuity_loan_amortizes_exactly(self):
        """Repayments total the principal and the remaining debt after the term is zero."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.choice([0.0, rng.uniform(0.001, 0.15)])
            term = rng.randint(1, 40)
            principal = rng.uniform(1000.0, 500000.0)
            context = case_context(seed, case, rate=rate, term=term, principal=principal)
            plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
            _disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
            remaining = principal
            for _year, _interest, repayment in schedule:
                remaining -= repayment.best_estimate
            total_repaid = sum(repayment.best_estimate for _year, _interest, repayment in schedule)
            assert total_repaid == pytest.approx(principal, rel=1e-9), (
                f"repayments total {total_repaid}, principal {principal} — {context}"
            )
            assert remaining == pytest.approx(0.0, abs=1e-6 * principal), (
                f"remaining debt after the term is {remaining} — {context}"
            )

    def test_interest_per_year_follows_the_outstanding_debt(self):
        """Interest is rate x remaining debt — the amortization identity npf.pmt is built on."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.uniform(0.001, 0.15)
            term = rng.randint(2, 40)
            principal = rng.uniform(1000.0, 500000.0)
            context = case_context(seed, case, rate=rate, term=term, principal=principal)
            plan = FinancingPlan(nominal_interest_rate=rate, term_in_years=term)
            _disbursement, schedule = loan_flows(plan, UncertainValue.exact(principal))
            remaining = principal
            for year, interest, repayment in schedule:
                assert interest.best_estimate == pytest.approx(rate * remaining, rel=1e-9), (
                    f"year {year} interest {interest.best_estimate} != {rate * remaining} — {context}"
                )
                remaining -= repayment.best_estimate

    def test_annuity_factor_matches_numpy_financial_pmt_on_a_unit_principal(self):
        """EconomicParameters.annuity_factor() is PMT on a principal of one euro (§4.4)."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.choice([0.0, rng.uniform(0.001, 0.15)])
            years = rng.randint(1, 50)
            context = case_context(seed, case, rate=rate, years=years)
            parameters = EconomicParameters(interest_rate=rate, observation_period_in_years=years)
            expected = -float(npf.pmt(rate, years, 1.0))
            assert parameters.annuity_factor() == pytest.approx(expected, rel=1e-9), (
                f"annuity factor {parameters.annuity_factor()} != npf.pmt {expected} — {context}"
            )

    def test_timeline_npv_matches_numpy_financial_npv(self):
        """CashFlowTimeline.npv equals npf.npv on the year-indexed series.

        Convention: both discount by ``(1 + rate) ** year``, so a year-0 entry is *not*
        discounted and `npf.npv(rate, series)` can be fed the series directly. (Excel's NPV
        differs — it places the first value at the end of period 1 — which is why the
        convention is asserted here rather than assumed.)
        """
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            horizon = rng.randint(1, 30)
            rate = rng.choice([0.0, 0.01, 0.03, 0.07, 0.11])
            timeline = random_timeline(rng, horizon=horizon, count=rng.randint(1, 25))
            # npf.npv works on a dense year-indexed series; entries beyond the last index would
            # be silently dropped, so the series spans every year the timeline uses.
            last_year = max(entry.year for entry in timeline.entries)
            series = [0.0] * (last_year + 1)
            for entry in timeline.entries:
                series[entry.year] += entry.amount_in_euro.best_estimate
            context = case_context(
                seed, case, rate=rate, horizon=horizon, entries=len(timeline.entries)
            )
            expected = float(npf.npv(rate, series))
            actual = timeline.npv(rate).best_estimate
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), (
                f"timeline NPV {actual} != npf.npv {expected} — {context}"
            )

    def test_year_zero_convention_is_undiscounted(self):
        """A single year-0 entry is worth its face value at any discount rate (§3.6)."""
        for case in range(CASES):
            seed = SEED_NUMPY_FINANCIAL + case
            rng = random.Random(seed)
            rate = rng.uniform(0.0, 0.2)
            amount = rng.uniform(-50000.0, 50000.0)
            context = case_context(seed, case, rate=rate, amount=amount)
            # The amount is drawn with an arbitrary sign to exercise the discounting algebra, so
            # this synthetic timeline opts out of the §3.9 sign convention (W3.7).
            timeline = CashFlowTimeline(validate=False)
            timeline.add(
                CashFlowEntry(
                    year=0,
                    amount_in_euro=UncertainValue.exact(amount),
                    category=CostCategory.INVESTMENT,
                    subject="device",
                )
            )
            assert timeline.npv(rate).best_estimate == pytest.approx(amount, rel=1e-12), context
            assert float(npf.npv(rate, [amount])) == pytest.approx(amount, rel=1e-12), context
