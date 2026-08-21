"""Engine tests: slot arithmetic, brownfield resolution and diagnostics (cost_spec.md §3-§4).

First of the three engine test files (split per the PR-3 review's 500-line rule; the
fixtures each file needs travel with it). This one covers the evaluation core: band
arithmetic on real evaluations, the hand-computed examples, per-slot coherence,
BROWNFIELD/STATUS_QUO context resolution (kept/replaced assets, anyway credits, the
missing-asset-class error), and the negative-flexibility clamp. Financing, views and CO2 are
in `test_economics_engine_financing.py`; actor allocation, the modernization levy and the
canonical-definition pins are in `test_economics_engine_actors.py`.
"""

from typing import Optional

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
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.timeline import CostCategory
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


GREENFIELD_GROSS = Perspective(
    id="test_greenfield_gross",
    installation_context=InstallationContext.GREENFIELD,
    subsidy_mode=SubsidyMode.none(),
)


class TestUncertainValue:
    """§3.9 semantics."""

    def test_band_order_enforced(self):
        """The ordering min <= best_estimate <= max is an invariant."""
        with pytest.raises(ValueError):
            UncertainValue(best_estimate=1.0, minimum=2.0, maximum=3.0)

    def test_bare_number_means_exact(self):
        """A bare JSON number is a degenerate band."""
        band = UncertainValue.from_json(5.0)
        assert band.is_exact() and band.best_estimate == 5.0

    def test_revenue_mirroring_keeps_order(self):
        """Optimistic world takes the revenue maximum, sign flips, order holds."""
        revenue = UncertainValue(best_estimate=10.0, minimum=8.0, maximum=13.0).as_revenue()
        assert (revenue.minimum, revenue.best_estimate, revenue.maximum) == (-13.0, -10.0, -8.0)

    def test_slotwise_sum(self):
        """Aggregation is slot-wise."""
        total = UncertainValue.sum([UncertainValue(2, 1, 3), UncertainValue(20, 10, 30)])
        assert (total.minimum, total.best_estimate, total.maximum) == (11, 22, 33)


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
        assert result.total_npv_in_euro.best_estimate == pytest.approx(1100.0)
        assert result.equivalent_annual_cost_in_euro.best_estimate == pytest.approx(110.0)

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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.INVESTMENT] == pytest.approx(1000.0)
        assert categories[CostCategory.REPLACEMENT] == pytest.approx(1000.0)
        assert categories[CostCategory.RESIDUAL_VALUE] == pytest.approx(-500.0)
        assert result.total_npv_in_euro.best_estimate == pytest.approx(1500.0)

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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.REPLACEMENT] == pytest.approx(expected_replacement_npv)
        assert categories[CostCategory.RESIDUAL_VALUE] == pytest.approx(expected_residual_npv)
        annuity = params.annuity_factor()
        assert result.equivalent_annual_cost_in_euro.best_estimate == pytest.approx(
            result.total_npv_in_euro.best_estimate * annuity
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
        price = database.get_energy_price(EnergyCarrier.ELECTRICITY, 2024, "DE").working_price_in_euro_per_kwh.best_estimate
        assert result.total_npv_in_euro.best_estimate == pytest.approx(5000.0 * price * 10)

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
        ).working_price_in_euro_per_kwh.best_estimate
        assert result.total_npv_in_euro.best_estimate == pytest.approx(-2000.0 * feed_in_price * 5)


class TestSlotProperties:
    """§3.9 / §9.4 property tests."""

    def test_degenerate_bands_make_all_slots_identical(self, database):
        """Degenerate bands (min = best_estimate = max) give LOW == BEST_ESTIMATE == HIGH everywhere."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", make_facts(1000.0, 10.0, 0.02))],
            billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=1000.0)],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        band = result.total_npv_in_euro
        assert band.minimum == pytest.approx(band.best_estimate) == pytest.approx(band.maximum)

    def test_every_total_satisfies_low_avg_high(self, database):
        """LOW <= BEST_ESTIMATE <= HIGH on every result figure."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        band_facts = make_facts(investment_band=UncertainValue(best_estimate=1000, minimum=800, maximum=1400), lifetime=10.0)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Device", band_facts)],
        )
        result = evaluator.evaluate(inputs, GREENFIELD_GROSS)
        for band in [result.total_npv_in_euro, result.equivalent_annual_cost_in_euro] + list(
            result.npv_by_category.values()
        ):
            assert band.minimum <= band.best_estimate <= band.maximum

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
        for attribute in ("minimum", "best_estimate", "maximum"):
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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
        assert categories[CostCategory.INVESTMENT] == pytest.approx(16000.0)
        assert CostCategory.ANYWAY_COST_CREDIT in categories
        assert categories[CostCategory.ANYWAY_COST_CREDIT] < 0
        # Sunk book value: 1/18 of the like-for-like price, reported but not in the timeline.
        like_for_like = database.get_device_entry(ComponentType.GAS_HEATER, 2024, "DE").investment_for_size(15.0)
        assert result.sunk_cost_written_off_in_euro.best_estimate == pytest.approx(like_for_like.best_estimate / 18.0)

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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
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
        categories = {category: value.best_estimate for category, value in result.npv_by_category.items()}
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
        assert result.sunk_cost_written_off_in_euro.best_estimate == pytest.approx(9000.0 * 19.0 / 20.0)


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
        working = result.npv_by_category[CostCategory.ENERGY_WORKING].best_estimate
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
