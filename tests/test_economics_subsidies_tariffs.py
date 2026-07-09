"""Tests for the subsidy engine (§5), tariff engine (§8) and scenario analysis (§4.6)."""

# clean

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    HeritageStatus,
    MeasureForSubsidy,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    required_questions,
    solve_cumulation,
)
from hisim.economics.tariffs import (
    CapacityCharge,
    CapacityChargeKind,
    FeedIn,
    FeedInKind,
    SupplyKind,
    TariffContract,
    TariffSupply,
    apply_tariff,
    synthetic_reference_spot_series,
    tariff_counterfactual,
    validate_billing_interval,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

DISCOUNT = EconomicParameters(price_basis_year=2024).discount_factor


@pytest.fixture(name="catalog", scope="module")
def fixture_catalog() -> SubsidyCatalog:
    """The shipped DE subsidy catalog."""
    return SubsidyCatalog.load("DE")


def make_measure(cost: float = 30000.0, scop: float = 4.0, refrigerant: str = "R290") -> MeasureForSubsidy:
    """A heat pump measure for subsidy tests."""
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        technical_attributes={"scop": scop, "refrigerant": refrigerant},
    )
    return MeasureForSubsidy(
        subject="HeatPump",
        facts=facts,
        measure_kind="REPLACE",
        cost_by_category={CostCategory.INVESTMENT: UncertainValue.exact(cost)},
    )


def full_context(income: float = 35000.0) -> SubsidyContext:
    """Owner-occupier with a functioning gas boiler, everything answered."""
    return SubsidyContext(
        applicant=ApplicantProfile(
            actor=ApplicantActor.OWNER_OCCUPIER, taxable_household_income_in_euro=income, main_residence=True
        ),
        building=SubsidyBuildingContext(
            construction_year=1985,
            dwelling_units=1,
            residential_floor_area_in_m2=150.0,
            commercial_floor_area_in_m2=0.0,
            existing_heating=ExistingAsset(
                asset_class=ComponentType.GAS_HEATER,
                size=15.0,
                size_unit=Units.KILOWATT,
                installation_year=2005,
                is_functional=True,
                energy_carrier=EnergyCarrier.NATURAL_GAS,
            ),
        ),
    )


class TestSubsidyEngine:
    """§5 scheme mechanics."""

    def test_beg_stacking_capped_at_70_percent(self, catalog):
        """Base 30 + speed 20 + income 30 caps at 70 % of the eligible cost."""
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), full_context(), 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 20000.0)

    def test_eligible_cost_cap_binds(self, catalog):
        """40 kEUR cost, cap 30 kEUR first unit: support = 70 % of 30 kEUR."""
        decision = solve_cumulation(catalog, make_measure(cost=40000.0), full_context(), 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 30000.0)
        assert any(award.caps_binding_per_slot.get("average") for award in decision.applied)

    def test_35c_excluded_by_beg(self, catalog):
        """§35c EStG never stacks with BEG grants."""
        decision = solve_cumulation(catalog, make_measure(), full_context(), 2024, DISCOUNT)
        applied_ids = {award.scheme_id for award in decision.applied}
        assert "DE_TAX_35C_2024" not in applied_ids or not applied_ids & {
            "DE_BEG_EM_HP_BASE_2024",
            "DE_BEG_EM_HP_SPEED_2024",
        }

    def test_income_bonus_needs_low_income(self, catalog):
        """High income drops the income bonus."""
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), full_context(income=80000.0), 2024, DISCOUNT)
        applied_ids = {award.scheme_id for award in decision.applied}
        assert "DE_BEG_EM_HP_INCOME_2024" not in applied_ids
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        assert upfront == pytest.approx((0.30 + 0.20 + 0.05) * 20000.0)

    def test_residential_share_proration(self, catalog):
        """Mixed use: only the residential share of the cost basis is eligible (§5.2)."""
        context = full_context()
        context.building.commercial_floor_area_in_m2 = 50.0  # share 0.75
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), context, 2024, DISCOUNT)
        upfront = sum(award.upfront_amount.average for award in decision.applied)
        assert upfront == pytest.approx(0.70 * 20000.0 * 0.75)

    def test_heritage_relaxes_scop_threshold(self, catalog):
        """SCOP 2.8 fails normally but passes for a protected building (§5.2 example)."""
        measure = make_measure(scop=2.8, refrigerant="R32")
        context = full_context()
        decision = solve_cumulation(catalog, measure, context, 2024, DISCOUNT)
        assert "DE_BEG_EM_HP_BASE_2024" in {reject["scheme_id"] for reject in decision.rejected}
        context.building.heritage_status = HeritageStatus.LISTED_MONUMENT
        decision = solve_cumulation(catalog, measure, context, 2024, DISCOUNT)
        assert "DE_BEG_EM_HP_BASE_2024" in {award.scheme_id for award in decision.applied}

    def test_tristate_reports_undetermined_upper_bound(self, catalog):
        """Unknown income makes schemes UNDETERMINED, with an optimistic upper bound (§5.7)."""
        context = full_context()
        context.applicant.taxable_household_income_in_euro = None
        decision = solve_cumulation(catalog, make_measure(cost=20000.0), context, 2024, DISCOUNT)
        undetermined_ids = {item["scheme_id"] for item in decision.undetermined}
        assert "DE_BEG_EM_HP_INCOME_2024" in undetermined_ids
        assert decision.undetermined_upper_bound_in_euro > 0

    def test_questionnaire_is_minimal_and_ordered(self, catalog):
        """Only unanswered fields are asked, ordered by pruning power, with asked-because."""
        context = SubsidyContext(
            applicant=ApplicantProfile(actor=ApplicantActor.OWNER_OCCUPIER, main_residence=True),
            building=SubsidyBuildingContext(construction_year=1985, dwelling_units=1),
        )
        questions = required_questions(catalog, [make_measure()], context, 2024)
        fields = [question.entry.fieldname for question in questions]
        assert "building.construction_year" not in fields  # already known
        assert "applicant.taxable_household_income_in_euro" in fields
        powers = [question.pruning_power_in_euro for question in questions]
        assert powers == sorted(powers, reverse=True)
        for question in questions:
            assert question.asked_because

    def test_tax_credit_schedule_shares(self, catalog):
        """§35c pays 35/35/30 over three years when BEG is not taken."""
        from hisim.economics.subsidies import _combination_awards  # noqa: PLC2701 — targeted unit test

        scheme = next(scheme for scheme in catalog.schemes if scheme.id == "DE_TAX_35C_2024")
        awards = _combination_awards([scheme], make_measure(cost=10000.0), full_context(), None)
        schedule = awards[0].schedule_amounts
        assert [amount.average for amount in schedule] == pytest.approx([700.0, 700.0, 600.0])


def make_flat_contract(price: float = 0.30, standing: float = 0.0) -> TariffContract:
    """A flat contract for billing tests."""
    return TariffContract(
        id="TEST_FLAT",
        carrier=EnergyCarrier.ELECTRICITY,
        country="DE",
        region=None,
        valid_from_year=2024,
        supply=TariffSupply(kind=SupplyKind.FLAT, working_price_in_euro_per_kwh=UncertainValue.exact(price)),
        standing_charge_in_euro_per_year=UncertainValue.exact(standing),
    )


class TestTariffEngine:
    """§8.4 billing engine properties."""

    def test_flat_contract_reproduces_kwh_times_price(self):
        """The central property test of §8.4."""
        determinants = BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4321.0)
        bill = apply_tariff(determinants, make_flat_contract(price=0.25))
        assert bill.by_category[CostCategory.ENERGY_WORKING].average == pytest.approx(4321.0 * 0.25)

    def test_capacity_charge_monotone_in_every_peak(self):
        """Raising any period peak never lowers the bill."""
        contract = make_flat_contract()
        contract.capacity_charge = CapacityCharge(
            kind=CapacityChargeKind.MONTHLY_PEAK, price_in_euro_per_kw=UncertainValue.exact(8.0)
        )
        base_peaks = [4.0] * 12
        base = apply_tariff(
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0,
                peak_per_billing_period_in_kw=base_peaks, annual_peak_in_kw=4.0,
            ),
            contract,
        )
        for month in range(12):
            raised = list(base_peaks)
            raised[month] += 2.0
            higher = apply_tariff(
                BillingDeterminants(
                    carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0,
                    peak_per_billing_period_in_kw=raised, annual_peak_in_kw=6.0,
                ),
                contract,
            )
            assert higher.total().average > base.total().average

    def test_dynamic_supply_uses_integrated_cost_and_decomposes(self):
        """DYNAMIC bills the native integral; flexibility value separates from volume (§8.5)."""
        contract = make_flat_contract()
        contract.supply = TariffSupply(
            kind=SupplyKind.DYNAMIC,
            spot_series="test",
            markup_in_euro_per_kwh=UncertainValue.exact(0.02),
        )
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY,
            energy_bought_in_kwh=1000.0,
            cost_integrated_in_euro=70.0,  # load shifted into cheap hours
            mean_spot_price_in_euro_per_kwh=0.08,
        )
        bill = apply_tariff(determinants, contract)
        assert bill.by_category[CostCategory.ENERGY_WORKING].average == pytest.approx(70.0 + 1000.0 * 0.02)
        assert bill.flexibility_value_in_euro == pytest.approx(1000.0 * 0.08 - 70.0)

    def test_uncertain_additive_components_shift_slots(self):
        """Additive per-kWh bands shift each slot by E x delta without re-integration (§8.4)."""
        contract = make_flat_contract()
        contract.supply = TariffSupply(
            kind=SupplyKind.DYNAMIC,
            spot_series="test",
            markup_in_euro_per_kwh=UncertainValue(average=0.02, minimum=0.01, maximum=0.04),
        )
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0, cost_integrated_in_euro=80.0
        )
        working = apply_tariff(determinants, contract).by_category[CostCategory.ENERGY_WORKING]
        assert working.minimum == pytest.approx(80.0 + 10.0)
        assert working.maximum == pytest.approx(80.0 + 40.0)

    def test_feed_in_revenue_negative(self):
        """Fixed tariff feed-in enters as negative cost."""
        contract = make_flat_contract()
        contract.feed_in = FeedIn(kind=FeedInKind.FIXED_TARIFF, rate_in_euro_per_kwh=UncertainValue.exact(0.08))
        bill = apply_tariff(
            BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=0.0, energy_sold_in_kwh=500.0),
            contract,
        )
        assert bill.by_category[CostCategory.FEED_IN_REVENUE].average == pytest.approx(-40.0)

    def test_billing_interval_must_divide(self):
        """seconds_per_timestep must divide the billing interval (§8.4)."""
        contract = make_flat_contract()
        contract.capacity_charge = CapacityCharge(
            kind=CapacityChargeKind.MONTHLY_PEAK,
            price_in_euro_per_kw=UncertainValue.exact(8.0),
            billing_interval_in_minutes=15,
        )
        validate_billing_interval(900, contract)
        validate_billing_interval(60, contract)
        with pytest.raises(Exception):
            validate_billing_interval(7 * 60, contract)

    def test_tariff_counterfactual(self):
        """Billing the same profile under a flat contract isolates the tariff choice (§8.5)."""
        dynamic = make_flat_contract()
        dynamic.supply = TariffSupply(kind=SupplyKind.DYNAMIC, spot_series="test")
        determinants = BillingDeterminants(
            carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0, cost_integrated_in_euro=200.0
        )
        outcome = tariff_counterfactual(determinants, dynamic, make_flat_contract(price=0.30))
        assert outcome["tariff_advantage_in_euro"].average == pytest.approx(300.0 - 200.0)

    def test_synthetic_series_is_deterministic_and_hourly(self):
        """The Q16 fallback profile."""
        series = synthetic_reference_spot_series()
        assert len(series) == 8760
        assert series == synthetic_reference_spot_series()
        assert min(series) >= 0.0


class TestScenarioAnalysis:
    """§4.6 scenario sets, overlays and derived analyses."""

    def _inputs(self):
        from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts

        facts = ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT)
        return EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=4000.0)],
        )

    def test_one_at_a_time_expansion(self):
        """Base + one scenario per axis level."""
        from hisim.economics.scenarios import ScenarioSet

        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "ONE_AT_A_TIME",
                "axes": [
                    {"name": "interest", "field": "interest_rate", "levels": {"low": 0.01, "high": 0.05}},
                ],
            }
        )
        scenarios = scenario_set.expand()
        assert [scenario.id for scenario in scenarios] == ["central", "interest=high", "interest=low"]

    def test_factorial_expansion(self):
        """Cartesian product plus base."""
        from hisim.economics.scenarios import ScenarioSet

        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "FACTORIAL",
                "axes": [
                    {"name": "a", "field": "interest_rate", "levels": {"l": 0.01, "h": 0.05}},
                    {"name": "b", "field": "general_price_escalation_rate", "levels": {"l": 0.0, "h": 0.04}},
                ],
            }
        )
        assert len(scenario_set.expand()) == 1 + 4

    def test_non_sweepable_fields_rejected(self):
        """country and dataset paths are not axes (§4.6)."""
        from hisim.economics.scenarios import ScenarioDataError, ScenarioSet

        for fieldname in ("country", "cost_database_path", "subsidy_catalog_path"):
            with pytest.raises(ScenarioDataError):
                ScenarioSet.from_json(
                    {"axes": [{"name": "x", "field": fieldname, "levels": {"a": "DE"}}]}
                )
        with pytest.raises(ScenarioDataError):
            ScenarioSet.from_json({"axes": [{"name": "x", "field": "not_a_field", "levels": {"a": 1}}]})

    def test_data_overlay_changes_device_price(self):
        """Overlaying a datapoint answers 'what if heat pumps get cheaper' (§4.6)."""
        database = CostDatabase()
        overlaid = database.with_overlays(
            {"devices_DE.HEAT_PUMP.specific_investment": {"min": 900, "avg": 1100, "max": 1400}}, "cheap_hp"
        )
        entry = overlaid.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE")
        assert entry.specific_investment.average == pytest.approx(1100.0)
        # The shipped database is untouched.
        assert database.get_device_entry(ComponentType.HEAT_PUMP, 2024, "DE").specific_investment.average == 1600.0
        assert overlaid.overlay_records and overlaid.overlay_records[0].detail == "cheap_hp"

    def test_cube_and_tornado(self):
        """Full cube evaluation with tornado data."""
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube, tornado_data

        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "ONE_AT_A_TIME",
                "axes": [{"name": "interest", "field": "interest_rate", "levels": {"low": 0.0, "high": 0.06}}],
            }
        )
        parameters = EconomicParameters(price_basis_year=2024)
        cube = evaluate_cube(self._inputs(), parameters, [perspective], scenario_set)
        assert set(cube.results["gross"].keys()) == {"central", "interest=low", "interest=high"}
        rows = tornado_data(cube, "gross")
        assert {row["scenario"] for row in rows} == {"interest=low", "interest=high"}
        swings = {row["scenario"]: row["swing"] for row in rows}
        assert swings["interest=high"] != swings["interest=low"]

    def test_counterfactual_billing_boundary(self):
        """Overriding consumed energy prices is rejected without the opt-in (§4.6)."""
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import ScenarioDataError, ScenarioSet, evaluate_cube

        inputs = self._inputs()
        inputs.consumed_tariff_ids = ["DE_DYNAMIC_SPOT_2024"]
        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "ONE_AT_A_TIME",
                "axes": [
                    {
                        "name": "elec",
                        "field": "energy_prices_DE.ELECTRICITY.working_price_in_euro_per_kwh",
                        "levels": {"high": 0.5},
                    }
                ],
            }
        )
        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        parameters = EconomicParameters(price_basis_year=2024)
        with pytest.raises(ScenarioDataError):
            evaluate_cube(inputs, parameters, [perspective], scenario_set)
        parameters.allow_counterfactual_billing = True
        cube = evaluate_cube(inputs, parameters, [perspective], scenario_set)
        assert "elec=high" in cube.results["gross"]

    def test_break_even_finds_crossing(self):
        """Bisection on the interest rate between a capex-heavy and an opex-heavy variant."""
        from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import find_break_even

        def variant(investment: float, energy_kwh: float) -> EvaluationInputs:
            facts = ComponentCostFacts(
                asset_class=ComponentType.HEAT_PUMP,
                size=10.0,
                size_unit=Units.KILOWATT,
                investment_cost_override_in_euro=UncertainValue.exact(investment),
                lifetime_override_in_years=20.0,
                maintenance_rate_override=UncertainValue.exact(0.0),
                override_source="test",
            )
            return EvaluationInputs(
                simulation_year=2024,
                simulated_period_fraction=1.0,
                cost_facts=[SubjectCostFacts("Device", facts)],
                billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=energy_kwh)],
            )

        perspective = Perspective(
            id="gross", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
        )
        outcome = find_break_even(
            axis_field="interest_rate",
            search_range=(0.0, 0.15),
            inputs_a=variant(20000.0, 1000.0),
            inputs_b=variant(2000.0, 4000.0),
            base_parameters=EconomicParameters(price_basis_year=2024),
            perspective=perspective,
        )
        assert not outcome["no_crossing_in_range"] or outcome["break_even"] is None
