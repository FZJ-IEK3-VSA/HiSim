"""Unit tests for the shared kernel of the lifecycle cost engine (cost_spec.md §3).

The kernel is the part of ``hisim.economics`` that has no cost data and no evaluator behind
it: uncertainty bands, the cash-flow timeline, the provenance ledger and the fact types the
components declare. Everything here is verifiable by hand.
"""

# clean

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import (
    BillingDeterminants,
    ComponentCostFacts,
    EnergyFlowFacts,
    ExistingAsset,
    ExistingAssetRegister,
)
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger
from hisim.economics.timeline import (
    Actor,
    CashFlowEntry,
    CashFlowTimeline,
    CategoryRules,
    CostCategory,
    SignExpectation,
    discount_factor,
    expected_sign,
    sign_violation,
)
from hisim.economics.uncertainty import Slot, UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


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

    def test_revenue_mirroring_is_an_involution_up_to_sign(self):
        """Mirroring twice restores the original band."""
        band = UncertainValue(average=10.0, minimum=8.0, maximum=13.0)
        restored = band.as_revenue().as_revenue()
        assert (restored.minimum, restored.average, restored.maximum) == (8.0, 10.0, 13.0)

    def test_empty_sum_is_the_zero_band(self):
        """Summing nothing yields exact zero, the neutral element."""
        assert UncertainValue.sum([]).is_exact()
        assert UncertainValue.sum([]).average == 0.0

    def test_slot_accessor_matches_the_three_worlds(self):
        """slot() maps LOW/AVERAGE/HIGH onto minimum/average/maximum."""
        band = UncertainValue(average=2.0, minimum=1.0, maximum=3.0)
        assert band.slot(Slot.LOW) == 1.0
        assert band.slot(Slot.AVERAGE) == 2.0
        assert band.slot(Slot.HIGH) == 3.0

    def test_subtraction_is_the_envelope_of_the_slot_deltas(self):
        """A wider subtrahend can make the LOW-world delta the largest one (§3.9)."""
        minuend = UncertainValue(average=10.0, minimum=9.0, maximum=11.0)
        subtrahend = UncertainValue(average=5.0, minimum=0.0, maximum=20.0)
        delta = minuend - subtrahend
        # slot deltas: low 9, average 5, high -9 -> envelope [-9, 9] around the average 5
        assert delta.average == pytest.approx(5.0)
        assert delta.minimum == pytest.approx(-9.0)
        assert delta.maximum == pytest.approx(9.0)

    def test_subtraction_of_equal_bands_is_exactly_zero(self):
        """Comparing a variant with itself must not manufacture a spread."""
        band = UncertainValue(average=10.0, minimum=8.0, maximum=13.0)
        assert (band - band).is_exact()

    def test_scale_rejects_negative_factors(self):
        """A negative factor would invert the slot ordering; as_revenue() is the way to flip signs."""
        with pytest.raises(ValueError):
            UncertainValue(average=2.0, minimum=1.0, maximum=3.0).scale(-1.0)

    def test_scale_is_slotwise_and_keeps_order(self):
        """Non-negative factors scale every slot."""
        scaled = UncertainValue(average=2.0, minimum=1.0, maximum=3.0).scale(2.5)
        assert (scaled.minimum, scaled.average, scaled.maximum) == (2.5, 5.0, 7.5)

    def test_clamp_upper_caps_every_slot_separately(self):
        """Subsidy caps bind per slot (§5.4), so each slot is capped against its own limit."""
        capped = UncertainValue(average=100.0, minimum=50.0, maximum=200.0).clamp_upper(
            UncertainValue(average=80.0, minimum=80.0, maximum=80.0)
        )
        assert (capped.minimum, capped.average, capped.maximum) == (50.0, 80.0, 80.0)

    def test_multiply_band_rejects_negative_operands(self):
        """Slot ordering only survives products of non-negative bands."""
        with pytest.raises(ValueError):
            UncertainValue(average=1.0, minimum=-1.0, maximum=3.0).multiply_band(UncertainValue.exact(2.0))

    def test_json_roundtrip_keeps_degenerate_and_real_bands_apart(self):
        """Exact values serialize as bare numbers, real bands as objects."""
        assert UncertainValue.exact(3.0).to_json() == 3.0
        band = UncertainValue(average=2.0, minimum=1.0, maximum=3.0)
        assert band.to_json() == {"min": 1.0, "avg": 2.0, "max": 3.0}
        assert UncertainValue.from_json(band.to_json()) == band

    def test_non_finite_values_are_rejected(self):
        """Infinities and NaNs must never enter a timeline."""
        with pytest.raises(ValueError):
            UncertainValue(average=float("inf"), minimum=0.0, maximum=float("inf"))


def make_entry(year: int, amount: float, category: CostCategory, subject: str = "HeatPump") -> CashFlowEntry:
    """A cost-positive entry with an exact amount."""
    return CashFlowEntry(
        year=year,
        amount_in_euro=UncertainValue.exact(amount),
        category=category,
        subject=subject,
    )


class TestCashFlowTimeline:
    """§3.6 timeline mechanics."""

    def _timeline(self) -> CashFlowTimeline:
        timeline = CashFlowTimeline()
        timeline.extend(
            [
                make_entry(0, 16000.0, CostCategory.INVESTMENT),
                make_entry(1, 400.0, CostCategory.MAINTENANCE),
                make_entry(2, 400.0, CostCategory.MAINTENANCE),
                make_entry(1, 1200.0, CostCategory.ENERGY_WORKING, subject="Electricity"),
            ]
        )
        return timeline

    def test_npv_at_zero_percent_is_the_plain_sum(self):
        """Without discounting the NPV is just the sum of the nominal amounts."""
        assert self._timeline().npv(0.0).average == pytest.approx(16000.0 + 400.0 + 400.0 + 1200.0)

    def test_npv_discounts_by_year(self):
        """Each entry is discounted with (1 + r)^-year."""
        timeline = CashFlowTimeline()
        timeline.add(make_entry(2, 1000.0, CostCategory.MAINTENANCE))
        assert timeline.npv(0.05).average == pytest.approx(1000.0 / 1.05**2)

    def test_empty_timeline_has_zero_npv(self):
        """An empty timeline is worth exactly nothing."""
        assert CashFlowTimeline().npv(0.03).is_exact()
        assert CashFlowTimeline().npv(0.03).average == 0.0

    def test_npv_by_pivot_partitions_the_total(self):
        """The pivot buckets sum back to the undivided NPV."""
        timeline = self._timeline()
        by_category = timeline.npv_by(0.04, key=lambda entry: entry.category)
        assert set(by_category) == {CostCategory.INVESTMENT, CostCategory.MAINTENANCE, CostCategory.ENERGY_WORKING}
        total = UncertainValue.sum(by_category.values())
        assert total.average == pytest.approx(timeline.npv(0.04).average)

    def test_npv_by_pivots_on_any_key(self):
        """The key is arbitrary - here the subject rather than the category."""
        by_subject = self._timeline().npv_by(0.0, key=lambda entry: entry.subject)
        assert by_subject["Electricity"].average == pytest.approx(1200.0)
        assert by_subject["HeatPump"].average == pytest.approx(16800.0)

    def test_without_categories_drops_exactly_those_categories(self):
        """The dropped amounts leave the timeline, the rest is untouched."""
        timeline = self._timeline()
        reduced = timeline.without_categories(frozenset({CostCategory.ENERGY_WORKING}))
        assert all(entry.category is not CostCategory.ENERGY_WORKING for entry in reduced.entries)
        assert reduced.npv(0.0).average == pytest.approx(timeline.npv(0.0).average - 1200.0)
        assert len(timeline.entries) == 4  # the source timeline is not mutated

    def test_filtered_keeps_the_matching_entries(self):
        """filtered() is the general form of without_categories()."""
        reduced = self._timeline().filtered(lambda entry: entry.year > 0)
        assert [entry.year for entry in reduced.entries] == [1, 2, 1]

    def test_nominal_annual_series_sums_per_year(self):
        """The liquidity view aggregates nominal euros per year, index = year."""
        series = self._timeline().nominal_annual_series(horizon_years=3)
        assert [value.average for value in series] == pytest.approx([16000.0, 1600.0, 400.0, 0.0])

    def test_subjects_are_listed_in_first_appearance_order(self):
        """Report tables rely on a stable subject order."""
        assert self._timeline().subjects() == ["HeatPump", "Electricity"]

    def test_entry_copies_do_not_touch_the_original(self):
        """with_payer/scaled return copies (frozen entries)."""
        entry = make_entry(0, 1000.0, CostCategory.INVESTMENT)
        assert entry.with_payer(Actor.TENANT).payer is Actor.TENANT
        assert entry.payer is Actor.SYSTEM
        assert entry.scaled(0.25).amount_in_euro.average == pytest.approx(250.0)
        assert entry.amount_in_euro.average == pytest.approx(1000.0)

    def test_scoped_to_system_returns_the_whole_timeline(self):
        """SYSTEM is the unscoped view - every entry stays (§6, §7 B4)."""
        timeline = self._timeline()
        assert timeline.scoped_to(Actor.SYSTEM) is timeline

    def test_scoped_to_a_payer_keeps_only_that_payers_entries(self):
        """A scoped perspective reports on the flows of one actor only."""
        timeline = CashFlowTimeline()
        timeline.extend(
            [
                make_entry(0, 16000.0, CostCategory.INVESTMENT).with_payer(Actor.LANDLORD),
                make_entry(1, 400.0, CostCategory.MAINTENANCE).with_payer(Actor.TENANT),
            ]
        )
        scoped = timeline.scoped_to(Actor.TENANT)
        assert [entry.category for entry in scoped.entries] == [CostCategory.MAINTENANCE]
        assert len(timeline.entries) == 2  # the source timeline is not mutated


class TestDiscountFactor:
    """W4.3: one discount formula for the whole module."""

    def test_year_zero_is_undiscounted(self):
        """Year 0 money is worth its face value."""
        assert discount_factor(0.05, 0) == pytest.approx(1.0)

    def test_zero_rate_never_discounts(self):
        """At 0 % every year is worth face value."""
        assert discount_factor(0.0, 17) == pytest.approx(1.0)

    def test_matches_the_closed_form(self):
        """1 / (1 + i)^year, by hand."""
        assert discount_factor(0.04, 3) == pytest.approx(1.0 / 1.04**3)

    def test_timeline_npv_delegates_to_it(self):
        """`CashFlowTimeline.npv` must not hold a second copy of the formula."""
        timeline = CashFlowTimeline()
        timeline.add(make_entry(7, 500.0, CostCategory.MAINTENANCE))
        assert timeline.npv(0.03).average == pytest.approx(500.0 * discount_factor(0.03, 7))


class TestSignConvention:
    """W3.7: cost is positive, money arriving is negative - and the two category sets differ."""

    def test_negative_sign_set_is_revenues_plus_loan_disbursement(self):
        """Band mirroring (REVENUE_CATEGORIES) and sign are deliberately not the same set."""
        assert CategoryRules.NEGATIVE_SIGN_CATEGORIES == CategoryRules.REVENUE_CATEGORIES | {
            CostCategory.LOAN_DISBURSEMENT
        }
        assert CostCategory.LOAN_DISBURSEMENT not in CategoryRules.REVENUE_CATEGORIES

    def test_cost_categories_are_expected_non_negative(self):
        """The ordinary case: money leaving is positive."""
        assert expected_sign(make_entry(0, 1.0, CostCategory.INVESTMENT)) == SignExpectation.NON_NEGATIVE
        assert expected_sign(make_entry(1, 1.0, CostCategory.ENERGY_WORKING)) == SignExpectation.NON_NEGATIVE

    def test_revenue_categories_are_expected_non_positive(self):
        """Feed-in, subsidies, residual value and anyway-cost credits arrive."""
        for category in sorted(CategoryRules.NEGATIVE_SIGN_CATEGORIES, key=lambda item: item.value):
            assert expected_sign(make_entry(0, -1.0, category)) == SignExpectation.NON_POSITIVE

    def test_modernization_levy_sign_depends_on_the_payer(self):
        """One category carries a transfer pair: the tenant pays, the landlord receives (§6.4)."""
        levy = make_entry(1, 1.0, CostCategory.MODERNIZATION_LEVY)
        assert expected_sign(levy.with_payer(Actor.TENANT)) == SignExpectation.NON_NEGATIVE
        assert expected_sign(levy.with_payer(Actor.LANDLORD)) == SignExpectation.NON_POSITIVE
        assert expected_sign(levy) == SignExpectation.NON_NEGATIVE  # unallocated (SYSTEM) reads as the paying leg

    def test_compliant_entries_report_no_violation(self):
        """sign_violation() returns None for entries that follow the convention."""
        assert sign_violation(make_entry(0, 16000.0, CostCategory.INVESTMENT)) is None
        assert sign_violation(make_entry(0, -3000.0, CostCategory.SUBSIDY)) is None
        assert sign_violation(make_entry(0, 0.0, CostCategory.FEED_IN_REVENUE)) is None

    def test_violation_is_detected_in_any_slot(self):
        """A band is compliant only if all three slots are - not just the average."""
        entry = CashFlowEntry(
            year=0,
            amount_in_euro=UncertainValue(minimum=-10.0, average=5.0, maximum=20.0),
            category=CostCategory.INVESTMENT,
            subject="HeatPump",
        )
        violation = sign_violation(entry)
        assert violation is not None and violation.expected_sign == SignExpectation.NON_NEGATIVE

    def test_violation_message_names_the_entry(self):
        """The message has to be usable in an error listing ten violations."""
        text = str(sign_violation(make_entry(3, 42.0, CostCategory.SUBSIDY)))
        assert "SUBSIDY" in text and "HeatPump" in text and "year 3" in text and SignExpectation.NON_POSITIVE in text

    def test_add_rejects_a_violating_entry(self):
        """Validation is on by default, so the engine cannot book a wrong-signed entry."""
        timeline = CashFlowTimeline()
        with pytest.raises(ValueError, match="sign convention"):
            timeline.add(make_entry(0, 500.0, CostCategory.SUBSIDY))
        assert timeline.entries == []  # nothing was appended

    def test_extend_rejects_the_batch_at_the_first_violation(self):
        """extend() validates entry by entry."""
        timeline = CashFlowTimeline()
        with pytest.raises(ValueError, match="sign convention"):
            timeline.extend(
                [
                    make_entry(0, 16000.0, CostCategory.INVESTMENT),
                    make_entry(0, 500.0, CostCategory.SUBSIDY),
                ]
            )
        assert len(timeline.entries) == 1  # the compliant entry before it was kept

    def test_validation_can_be_switched_off_for_synthetic_timelines(self):
        """`validate=False` is the documented escape for hand-written net series."""
        timeline = CashFlowTimeline(validate=False)
        timeline.add(make_entry(0, -500.0, CostCategory.MAINTENANCE))
        assert len(timeline.entries) == 1

    def test_derived_views_inherit_the_validation_flag(self):
        """filtered()/without_categories()/scoped_to() keep the regime of their source."""
        timeline = CashFlowTimeline(validate=False)
        timeline.add(make_entry(0, -500.0, CostCategory.MAINTENANCE))
        assert timeline.filtered(lambda entry: True).validate is False
        assert timeline.without_categories(frozenset()).validate is False
        assert CashFlowTimeline().filtered(lambda entry: True).validate is True

    def test_constructor_entries_bypass_add_and_are_caught_by_validate_signs(self):
        """Passing `entries=` skips add(), which is exactly what validate_signs() is for."""
        timeline = CashFlowTimeline(entries=[make_entry(0, 500.0, CostCategory.SUBSIDY)])
        assert len(timeline.sign_violations()) == 1
        with pytest.raises(ValueError, match="1 timeline entries violate"):
            timeline.validate_signs()

    def test_validate_signs_passes_on_a_compliant_timeline(self):
        """The happy path raises nothing and reports no violations."""
        timeline = CashFlowTimeline()
        timeline.extend(
            [
                make_entry(0, 16000.0, CostCategory.INVESTMENT),
                make_entry(0, -3000.0, CostCategory.SUBSIDY),
                make_entry(0, -8000.0, CostCategory.LOAN_DISBURSEMENT),
            ]
        )
        assert timeline.sign_violations() == []
        timeline.validate_signs()


class TestProvenanceLedger:
    """§3.10: every number in a result is traceable to a source."""

    def _record(self, parameter: str = "devices_DE.HEAT_PUMP@2024.specific_investment") -> ParameterProvenance:
        return ParameterProvenance(
            parameter=parameter,
            value=UncertainValue(average=1600.0, minimum=1400.0, maximum=1900.0),
            origin=ParameterOrigin.DATABASE_ENTRY,
            data_file="devices_DE.json",
            source_ids=("DE_BWP_2024",),
        )

    def test_identical_records_are_interned_under_one_id(self):
        """Interning keeps cost_provenance.json small and ids stable."""
        ledger = ProvenanceLedger()
        first = ledger.record(self._record())
        second = ledger.record(self._record())
        assert first == second == 0
        assert len(ledger) == 1

    def test_different_records_get_ascending_ids(self):
        """Ids are assigned in insertion order."""
        ledger = ProvenanceLedger()
        assert ledger.record(self._record("a")) == 0
        assert ledger.record(self._record("b")) == 1
        assert [record.parameter for record in ledger.records] == ["a", "b"]
        assert ledger.get(1).parameter == "b"

    def test_sourced_origins_without_source_ids_are_rejected(self):
        """A datapoint without a source cannot enter a calculation."""
        ledger = ProvenanceLedger()
        with pytest.raises(ValueError, match="source"):
            ledger.record(
                ParameterProvenance(
                    parameter="devices_DE.HEAT_PUMP@2024.specific_investment",
                    value=1600.0,
                    origin=ParameterOrigin.DATABASE_ENTRY,
                )
            )

    def test_simulation_outputs_and_engine_defaults_need_no_sources(self):
        """Those two origins legitimately carry no source ids."""
        ledger = ProvenanceLedger()
        for origin in (ParameterOrigin.SIMULATION_OUTPUT, ParameterOrigin.ENGINE_DEFAULT):
            assert not origin.requires_sources
            ledger.record(ParameterProvenance(parameter=f"x.{origin.value}", value=1.0, origin=origin))
        assert len(ledger) == 2

    def test_json_roundtrip_restores_records_and_bands(self):
        """Archived results stay explainable offline."""
        ledger = ProvenanceLedger()
        ledger.record(self._record())
        restored = ProvenanceLedger.from_json(ledger.to_json())
        assert restored.records == ledger.records
        assert isinstance(restored.get(0).value, UncertainValue)


class TestFactValidation:
    """§9.3: components fail fast on their own declarations."""

    def _facts(self, **overrides) -> ComponentCostFacts:
        arguments = {
            "asset_class": ComponentType.HEAT_PUMP,
            "size": 10.0,
            "size_unit": Units.KILOWATT,
        }
        arguments.update(overrides)
        return ComponentCostFacts(**arguments)

    def test_scalar_overrides_are_coerced_to_exact_bands(self):
        """A plain number means a degenerate band, so downstream code sees one type."""
        facts = self._facts(investment_cost_override_in_euro=16000.0, override_source="quote")
        assert isinstance(facts.investment_cost_override_in_euro, UncertainValue)
        assert facts.investment_cost_override_in_euro.is_exact()

    def test_negative_and_non_finite_sizes_are_rejected(self):
        """A size that cannot mean anything is still a hard error, at declaration time."""
        with pytest.raises(ValueError, match="size"):
            self._facts(size=-1.0)
        with pytest.raises(ValueError, match="size"):
            self._facts(size=float("nan"))
        with pytest.raises(ValueError, match="size"):
            self._facts(size=float("inf"))

    def test_zero_size_is_accepted_and_flagged_as_not_installed(self):
        """Zero means "the setup built it but sized it away", which is data, not corruption."""
        facts = self._facts(size=0.0)
        assert facts.is_not_installed()
        assert not self._facts(size=10.0).is_not_installed()

    def test_unsupported_size_unit_is_rejected(self):
        """Only the costing units of the database are accepted."""
        with pytest.raises(ValueError, match="size_unit"):
            self._facts(size_unit=Units.CELSIUS)

    def test_asset_class_must_be_a_component_type(self):
        """Raw strings never enter the asset-class field."""
        with pytest.raises(ValueError, match="asset_class"):
            self._facts(asset_class="HeatPump")

    def test_count_below_one_is_rejected(self):
        """Zero devices would silently zero out the investment."""
        with pytest.raises(ValueError, match="count"):
            self._facts(count=0)

    def test_negative_maintenance_rate_is_rejected(self):
        """Maintenance can never be a revenue."""
        with pytest.raises(ValueError, match="maintenance_rate_override"):
            self._facts(maintenance_rate_override=UncertainValue(average=0.01, minimum=-0.01, maximum=0.02))

    def test_non_positive_lifetime_is_rejected(self):
        """A zero lifetime would divide by zero in the replacement schedule."""
        with pytest.raises(ValueError, match="lifetime"):
            self._facts(lifetime_override_in_years=0.0)

    def test_technical_attributes_must_be_json_serializable(self):
        """They are written to economic_inputs.json and read by subsidy conditions."""
        with pytest.raises(ValueError, match="JSON"):
            self._facts(technical_attributes={"scop": object()})

    def test_has_overrides_reports_every_override_field(self):
        """`override_source` is only required once something is overridden."""
        assert not self._facts().has_overrides()
        assert self._facts(lifetime_override_in_years=25.0).has_overrides()
        assert self._facts(embodied_co2_override_in_kg=1200.0).has_overrides()

    def test_energy_flows_must_be_finite(self):
        """A NaN meter reading must not reach the billing engine."""
        with pytest.raises(ValueError, match="finite"):
            EnergyFlowFacts(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=float("nan"))

    def test_billing_determinants_wrap_plain_annual_flows(self):
        """Flat contracts reuse the simpler EnergyFlowFacts."""
        determinants = BillingDeterminants.from_energy_flow(
            EnergyFlowFacts(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=2500.0,
                energy_sold_in_kwh=100.0,
                simulated_cost_in_euro=800.0,
            )
        )
        assert determinants.carrier is EnergyCarrier.ELECTRICITY
        assert determinants.energy_bought_in_kwh == pytest.approx(2500.0)
        assert determinants.energy_sold_in_kwh == pytest.approx(100.0)
        assert determinants.cost_integrated_in_euro == pytest.approx(800.0)

    def test_existing_asset_age_is_floored_at_zero(self):
        """A future installation year must not produce a negative age."""
        asset = ExistingAsset(
            asset_class=ComponentType.GAS_HEATER,
            size=15.0,
            size_unit=Units.KILOWATT,
            installation_year=2030,
            energy_carrier=EnergyCarrier.NATURAL_GAS,
        )
        assert asset.age_in_years(2024) == 0
        assert asset.age_in_years(2035) == 5

    def test_existing_asset_size_is_validated(self):
        """The register uses the same fail-fast rules as the facts."""
        with pytest.raises(ValueError, match="size"):
            ExistingAsset(
                asset_class=ComponentType.GAS_HEATER,
                size=0.0,
                size_unit=Units.KILOWATT,
                installation_year=2005,
            )

    def test_register_finds_assets_by_class(self):
        """Brownfield lookups are by asset class."""
        asset = ExistingAsset(
            asset_class=ComponentType.GAS_HEATER,
            size=15.0,
            size_unit=Units.KILOWATT,
            installation_year=2005,
        )
        register = ExistingAssetRegister(assets=[asset])
        assert register.find(ComponentType.GAS_HEATER) is asset
        assert register.find(ComponentType.HEAT_PUMP) is None
