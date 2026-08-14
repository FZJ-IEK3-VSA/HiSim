"""Engine tests: actor allocation, the modernization levy, and the canonical-definition pins (cost_spec.md §6).

Third of the three engine test files (split per the PR-3 review's 500-line rule): the
landlord/tenant/owner-occupier allocation (zero-sum invariant included), the §559/§559e
modernization levy with its nested caps (D27), result quantities, and the two
one-definition pins — canonical discounting (W4.3) and the single bill definition. The
evaluation core is in `test_economics_engine.py`; views and financing are in
`test_economics_engine_financing.py`.
"""

import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
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

GREENFIELD_GROSS = Perspective(
    id="test_greenfield_gross",
    installation_context=InstallationContext.GREENFIELD,
    subsidy_mode=SubsidyMode.none(),
)


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
        for attribute in ("minimum", "best_estimate", "maximum"):
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
        assert sum(entry.amount.best_estimate for entry in report.entries) == pytest.approx(
            result.total_npv_in_euro.best_estimate  # zero interest in this fixture
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
            entry.amount_in_euro.best_estimate
            for entry in result.timeline.entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.TENANT
        )
        levy_landlord = sum(
            entry.amount_in_euro.best_estimate
            for entry in result.timeline.entries
            if entry.category == CostCategory.MODERNIZATION_LEVY and entry.payer == Actor.LANDLORD
        )
        assert levy_tenant > 0
        assert levy_landlord == pytest.approx(-levy_tenant)

    def test_zero_sum_envelope_semantics(self):
        """§6.5 / B11: BEST_ESTIMATE exact, min/max only contained — and only in the widening direction."""
        from hisim.economics.actors import assert_zero_sum

        system = UncertainValue(best_estimate=100.0, minimum=90.0, maximum=120.0)
        # A minted transfer pair widens the payer band symmetrically: accepted.
        widened = [UncertainValue(best_estimate=100.0, minimum=80.0, maximum=130.0)]
        assert_zero_sum(system, widened)
        # ... but not under the strong form used for retag-only rulesets.
        with pytest.raises(AssertionError):
            assert_zero_sum(system, widened, require_per_slot_equality=True)
        # A *narrower* payer band means money vanished from a slot: rejected.
        with pytest.raises(AssertionError):
            assert_zero_sum(system, [UncertainValue(best_estimate=100.0, minimum=95.0, maximum=120.0)])
        with pytest.raises(AssertionError):
            assert_zero_sum(system, [UncertainValue(best_estimate=100.0, minimum=90.0, maximum=110.0)])
        # The BEST_ESTIMATE slot is always exact.
        with pytest.raises(AssertionError):
            assert_zero_sum(system, [UncertainValue(best_estimate=101.0, minimum=80.0, maximum=130.0)])

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
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(300.0)
        assert outcome.general_levy_in_euro.best_estimate == pytest.approx(0.0)
        assert outcome.total_in_euro.best_estimate == pytest.approx(300.0)

    def test_pure_heating_package_is_capped_at_fifty_cents_per_m2_and_month(self):
        """The §559e cap binds long before the general one: 0.50 x 12 x 100 = 600 EUR/a."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context([self._measure("HEAT_PUMP", 30000.0)])  # 10 % = 3,000 EUR/a raw
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(600.0)
        assert outcome.total_in_euro.best_estimate == pytest.approx(600.0)

    def test_pure_envelope_package_keeps_the_eight_percent_paragraph(self):
        """Insulation is §559, not §559e — and the split path reproduces the aggregate one."""
        from hisim.economics.actors import AllocationContext, DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        measure = self._measure("WALL_EXTERNAL_INSULATION", 30000.0, subsidies=5000.0, avoided=10000.0)
        # 30,000 - 5,000 - 3,000 = 22,000 -> 8 % = 1,760 EUR/a, under the 3,600 EUR/a cap.
        outcome = ruleset.compute_modernization_levy(self._context([measure]))
        assert outcome.general_levy_in_euro.best_estimate == pytest.approx(1760.0)
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(0.0)
        aggregate_only = AllocationContext(
            horizon_years=20,
            living_area_in_m2=self.LIVING_AREA_IN_M2,
            current_cold_rent_in_euro_per_m2_month=9.0,
            modernization_cost_in_euro=UncertainValue.exact(30000.0),
            subsidies_received_in_euro=UncertainValue.exact(5000.0),
            avoided_maintenance_in_euro=UncertainValue.exact(10000.0),
        )
        assert ruleset.compute_modernization_levy(aggregate_only).total_in_euro.best_estimate == pytest.approx(
            outcome.total_in_euro.best_estimate
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
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_EXTERNAL_INSULATION", 50000.0)]
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(600.0)
        assert outcome.general_levy_in_euro.best_estimate == pytest.approx(3000.0)
        assert outcome.total_in_euro.best_estimate == pytest.approx(3600.0)

    def test_low_rent_tier_tightens_the_total_but_not_the_heating_cap(self):
        """Below the 7 EUR/m² threshold the total cap drops to 2,400 EUR/a; 600 stays 600."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context(
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_EXTERNAL_INSULATION", 50000.0)],
            rent=6.0,
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(600.0)
        assert outcome.general_levy_in_euro.best_estimate == pytest.approx(1800.0)  # 2,400 - 600
        assert outcome.total_in_euro.best_estimate == pytest.approx(2400.0)

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
            [self._measure("HEAT_PUMP", 100000.0), self._measure("WALL_EXTERNAL_INSULATION", 10000.0)]
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(3600.0)  # clipped to the total cap
        assert outcome.general_levy_in_euro.best_estimate == pytest.approx(0.0)  # non-heating went first
        assert outcome.total_in_euro.best_estimate == pytest.approx(3600.0)

    def test_without_a_living_area_neither_cap_can_bind(self):
        """§6.4's documented degradation: no area, no cap — for both paragraphs alike."""
        from hisim.economics.actors import DE2024Ruleset

        ruleset = DE2024Ruleset.load()
        context = self._context(
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_EXTERNAL_INSULATION", 50000.0)],
            living_area=None,
        )
        outcome = ruleset.compute_modernization_levy(context)
        assert outcome.heating_levy_in_euro.best_estimate == pytest.approx(3000.0)
        assert outcome.general_levy_in_euro.best_estimate == pytest.approx(4000.0)

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
        assert not ruleset.is_heating_measure("WALL_EXTERNAL_INSULATION")
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
            [self._measure("HEAT_PUMP", 30000.0), self._measure("WALL_EXTERNAL_INSULATION", 50000.0)],
            horizon=3,
        )
        entries = ruleset.modernization_levy_entries(context)
        assert len(entries) == 6  # three years x (tenant leg + landlord leg)
        assert {entry.subject for entry in entries} == {"modernization levy"}
        tenant = [entry for entry in entries if entry.payer == Actor.TENANT]
        assert all(entry.amount_in_euro.best_estimate == pytest.approx(3600.0) for entry in tenant)
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
        assert timeline.npv(0.04).best_estimate == pytest.approx(expected)


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


