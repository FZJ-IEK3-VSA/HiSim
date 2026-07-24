"""Tests for the core lifecycle cost engine (cost_spec.md §3, §4, §9.4).

Engine math is tested against hand-computed VDI 2067 / EN 15459-style examples plus the
property tests listed in §9.4.
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
from hisim.economics.timeline import Actor, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database."""
    return CostDatabase()


def make_facts(
    investment: float = 1000.0,
    lifetime: float = 10.0,
    maintenance_rate: float = 0.0,
    investment_band: UncertainValue = None,
) -> ComponentCostFacts:
    """Fully overridden facts so tests do not depend on database values."""
    return ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=investment_band or UncertainValue.exact(investment),
        installation_cost_override_in_euro=UncertainValue.exact(0.0),
        lifetime_override_in_years=lifetime,
        maintenance_rate_override=UncertainValue.exact(maintenance_rate),
        fixed_operation_cost_override_in_euro_per_year=UncertainValue.exact(0.0),
        embodied_co2_override_in_kg=0.0,
        override_source="unit test",
    )


def zero_rate_parameters(horizon: int = 10) -> EconomicParameters:
    """All rates zero: NPV must equal the plain sum (§9.4)."""
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
        assert result.annual_cost_series_nominal_in_euro[1].average == pytest.approx(100.0)


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


class TestActorAllocation:
    """§6 actor model."""

    def _tenant_result(self, database, emissions=None):
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

    def test_co2_split_responds_to_building_emissions(self, database):
        """A dirtier building shifts CO2 price cost to the landlord (§6.3)."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        assert ruleset.tenant_co2_share(5.0) == pytest.approx(1.0)
        assert ruleset.tenant_co2_share(34.0) == pytest.approx(0.5)
        assert ruleset.tenant_co2_share(60.0) == pytest.approx(0.05)
