"""Tests for building-envelope measures in the lifecycle cost engine (cost_spec.md Q7).

**Surface.** Everything that makes insulation, windows, doors, air sealing and ventilation
ordinary cost subjects: the eight envelope `ComponentType`s in the device data, the three
envelope-specific engine mechanics (the 5-year `anyway_threshold_years_override`, like-for-like
replacement recognition through `replaced_by_asset_classes`, and the `energy_related_cost_share`
coupled-cost credit), and the BEG EM envelope subsidy schemes with their U-value conditions.
Envelope measures are *not* simulation components — the building physics only sees changed
U-values — so their facts are injected into `EvaluationInputs` by whoever defines the variant
(README §3.2b); this file is where that injected path is exercised.

**How it covers it.** Two styles, deliberately separated. `TestEnvelopeData` is data-file CI: it
asserts the shipped DE/IE entries for 2026 and 2035 exist and carry the envelope conventions
(banded price, threshold 5.0, coupled-cost share exactly 1.0), so an incomplete price PR fails
here rather than in an evaluation. `TestEnvelopeTimeline` uses the shipped database but zeroes
every rate, which turns the expected values into arithmetic a reviewer can redo in their head —
40 a life inside a 20 a horizon credits back exactly half the cost, a share of 0.55 credits back
0.45 of gross, slot-wise with the LOW world taking the largest credit. `TestEnvelopeSubsidies`
prices a fixed 25 000 EUR measure so the 15 % + 5 % rates are readable in the assertion.

**Error class.** A failure in `TestEnvelopeData` is a *data* problem (a country/year/class gap or
a convention drift in the JSON files). A failure in `TestEnvelopeTimeline` is a *formula* problem
in the investment/residual/anyway-credit calculators — the zero rates rule out escalation and
discounting as causes. A failure in `TestEnvelopeSubsidies` is a *catalog* problem: either the
DE scheme definitions moved, or condition evaluation stopped reading `technical_attributes`.
Note that envelope economics are dominated by the residual value, by design and not by accident
(README §3.2b): half a 40-year wall insulation is credited back at the horizon.
"""

# clean

import pytest

from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import ComponentCostFacts, ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    MeasureForSubsidy,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
    required_questions,
    solve_cumulation,
)
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base

#: The eight asset classes that make up the envelope (README §3.2b). All are sized in m2 of the
#: respective element except EXTERIOR_DOOR and VENTILATION_SYSTEM, which are priced per unit.
ENVELOPE_CLASSES = [
    ComponentType.WALL_INSULATION,
    ComponentType.ROOF_INSULATION,
    ComponentType.TOP_CEILING_INSULATION,
    ComponentType.FLOOR_INSULATION,
    ComponentType.WINDOWS,
    ComponentType.EXTERIOR_DOOR,
    ComponentType.AIR_SEALING,
    ComponentType.VENTILATION_SYSTEM,
]

#: The two contexts an envelope measure can be evaluated in: BROWNFIELD sees the existing-asset
#: register (kept / replaced / anyway credits), GREENFIELD ignores it and charges everything as a
#: new investment. Both run with subsidies off so the timeline tests read gross costs.
BROWNFIELD = Perspective(
    id="brownfield", installation_context=InstallationContext.BROWNFIELD, subsidy_mode=SubsidyMode.none()
)
GREENFIELD = Perspective(
    id="greenfield", installation_context=InstallationContext.GREENFIELD, subsidy_mode=SubsidyMode.none()
)


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database.

    Envelope prices and service lives are deliberately taken from the real data files rather than
    from a synthetic fixture, because half of what this module checks *is* the shipped data.
    Module-scoped: loading and validating the whole database is the expensive part of these tests.
    """
    return CostDatabase()


def zero_rate_parameters(horizon: int = 20) -> EconomicParameters:
    """All rates zero, price basis 2026 (the first year with envelope entries).

    Interest, both escalation rates and carbon pricing are switched off so that every expected
    value below is a plain ratio of the purchase price — a residual value of exactly half, a
    credit of exactly `(1 - share)` — instead of a discounted figure only the implementation can
    reproduce. The horizon is a parameter because the residual-value test needs it to sit
    mid-life (20 a of a 40 a measure) while the rest can use the default.
    """
    return EconomicParameters(
        observation_period_in_years=horizon,
        interest_rate=0.0,
        general_price_escalation_rate=0.0,
        investment_price_escalation_rate=0.0,
        co2_price_scenario="none",
        country="DE",
        price_basis_year=2026,
    )


def wall_facts(area_m2: float = 100.0) -> ComponentCostFacts:
    """A wall insulation measure sized in m2 of wall.

    Nothing is overridden, so the price, service life and coupled-cost share all come from the
    shipped `devices_DE.json` entry — that is what makes the residual-value and credit ratios
    below statements about the real data. The `u_value` in `technical_attributes` is the field the
    BEG envelope conditions test; 0.18 W/m2K passes the 0.20 requirement comfortably.
    """
    return ComponentCostFacts(
        asset_class=ComponentType.WALL_INSULATION,
        size=area_m2,
        size_unit=Units.SQUARE_METER,
        technical_attributes={"u_value": 0.18, "thickness_cm": 16},
    )


class TestEnvelopeData:
    """Coverage of the new asset classes."""

    @pytest.mark.parametrize("asset_class", ENVELOPE_CLASSES, ids=lambda item: item.name)
    def test_entries_exist_for_both_countries_and_years(self, database, asset_class):
        """Every envelope class has DE and IE entries for 2026 and 2035."""
        for country, year in (("DE", 2026), ("DE", 2035), ("IE", 2026), ("IE", 2035)):
            entry = database.get_device_entry(asset_class, year, country)
            assert entry.specific_investment.minimum < entry.specific_investment.maximum
            assert entry.anyway_threshold_years_override == pytest.approx(5.0)
            assert entry.energy_related_cost_share.is_exact()
            assert entry.energy_related_cost_share.average == pytest.approx(1.0)


class TestEnvelopeTimeline:
    """Residual value dominates 40-50 a measures inside a 20 a horizon."""

    def test_residual_value_of_long_lived_measure(self, database):
        """Wall insulation, 40 a life, 20 a horizon, zero rates: residual = half the cost."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters(horizon=20))
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Envelope.WallInsulation", wall_facts(100.0))],
        )
        result = evaluator.evaluate(inputs, GREENFIELD)
        categories = result.npv_by_category
        investment = categories[CostCategory.INVESTMENT]
        residual = categories[CostCategory.RESIDUAL_VALUE]
        assert CostCategory.REPLACEMENT not in categories  # no replacement within 20 of 40 years
        for attribute in ("minimum", "average", "maximum"):
            # Residual is revenue-type: its slot band mirrors the investment band.
            assert abs(getattr(residual, attribute)) > 0
        assert residual.average == pytest.approx(-investment.average / 2.0)

    def test_like_for_like_measure_is_charged_not_kept(self, database):
        """New windows replacing old windows (same asset class) are an investment (Q7 fix)."""
        old_windows = ExistingAsset(
            asset_class=ComponentType.WINDOWS,
            size=25.0,
            size_unit=Units.SQUARE_METER,
            installation_year=2026 - 34,  # 1 a of 35 remaining
            replaced_by_asset_classes=[ComponentType.WINDOWS],
        )
        facts = ComponentCostFacts(
            asset_class=ComponentType.WINDOWS,
            size=25.0,
            size_unit=Units.SQUARE_METER,
            technical_attributes={"u_value": 0.9},
        )
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Envelope.Windows", facts)],
            existing_assets=ExistingAssetRegister(assets=[old_windows]),
        )
        result = evaluator.evaluate(inputs, BROWNFIELD)
        categories = result.npv_by_category
        assert CostCategory.INVESTMENT in categories
        # Anyway credit: the dead windows' like-for-like replacement is avoided (1 a <= 5 a).
        assert CostCategory.ANYWAY_COST_CREDIT in categories
        assert categories[CostCategory.ANYWAY_COST_CREDIT].average < 0

    def test_kept_windows_without_measure_stay_kept(self, database):
        """Without a replaced_by declaration the same class still means 'kept', as before."""
        old_windows = ExistingAsset(
            asset_class=ComponentType.WINDOWS,
            size=25.0,
            size_unit=Units.SQUARE_METER,
            installation_year=2026 - 10,
        )
        facts = ComponentCostFacts(asset_class=ComponentType.WINDOWS, size=25.0, size_unit=Units.SQUARE_METER)
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Envelope.Windows", facts)],
            existing_assets=ExistingAssetRegister(assets=[old_windows]),
        )
        result = evaluator.evaluate(inputs, BROWNFIELD)
        assert CostCategory.INVESTMENT not in result.npv_by_category

    def test_envelope_threshold_is_five_years_devices_stay_at_two(self, database):
        """Remaining life 4 a: envelope measures earn the anyway credit, devices do not."""
        evaluator = EconomicEvaluator(database, zero_rate_parameters())
        # Envelope: windows with 4 a of 35 remaining -> credit (threshold 5 from the entry).
        windows_register = ExistingAssetRegister(
            assets=[
                ExistingAsset(
                    asset_class=ComponentType.WINDOWS,
                    size=25.0,
                    size_unit=Units.SQUARE_METER,
                    installation_year=2026 - 31,
                    replaced_by_asset_classes=[ComponentType.WINDOWS],
                )
            ]
        )
        windows_inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "Envelope.Windows",
                    ComponentCostFacts(asset_class=ComponentType.WINDOWS, size=25.0, size_unit=Units.SQUARE_METER),
                )
            ],
            existing_assets=windows_register,
        )
        windows_result = evaluator.evaluate(windows_inputs, BROWNFIELD)
        assert CostCategory.ANYWAY_COST_CREDIT in windows_result.npv_by_category

        # Device: heat pump replacing a gas boiler with 4 a of 18 remaining -> no credit
        # (default threshold 2 a).
        boiler_register = ExistingAssetRegister(
            assets=[
                ExistingAsset(
                    asset_class=ComponentType.GAS_HEATER,
                    size=15.0,
                    size_unit=Units.KILOWATT,
                    installation_year=2026 - 14,
                    replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
                )
            ]
        )
        heat_pump_inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    ComponentCostFacts(asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT),
                )
            ],
            existing_assets=boiler_register,
        )
        heat_pump_result = evaluator.evaluate(heat_pump_inputs, BROWNFIELD)
        assert CostCategory.ANYWAY_COST_CREDIT not in heat_pump_result.npv_by_category

    def test_coupled_cost_credit_via_energy_related_share(self, database):
        """A share < 1 (scenario overlay) credits the non-energy share instead of like-for-like."""
        overlaid = database.with_overlays(
            {"devices_DE.WALL_INSULATION.energy_related_cost_share": {"min": 0.4, "avg": 0.55, "max": 0.7}},
            "coupled_cost_test",
        )
        evaluator = EconomicEvaluator(overlaid, zero_rate_parameters())
        register = ExistingAssetRegister(
            assets=[
                ExistingAsset(
                    asset_class=ComponentType.WALL_INSULATION,  # the old facade "element"
                    size=100.0,
                    size_unit=Units.SQUARE_METER,
                    installation_year=2026 - 38,  # 2 a of 40 remaining <= 5 a threshold
                    replaced_by_asset_classes=[ComponentType.WALL_INSULATION],
                )
            ]
        )
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Envelope.WallInsulation", wall_facts(100.0))],
            existing_assets=register,
        )
        result = evaluator.evaluate(inputs, BROWNFIELD)
        gross = result.npv_by_category[CostCategory.INVESTMENT]
        credit = result.npv_by_category[CostCategory.ANYWAY_COST_CREDIT]
        # Zero rates: credit = -(1 - share) x gross, slot-wise (LOW world takes the max credit).
        assert credit.average == pytest.approx(-gross.average * (1.0 - 0.55))
        assert credit.minimum == pytest.approx(-gross.maximum * (1.0 - 0.4))
        assert credit.maximum == pytest.approx(-gross.minimum * (1.0 - 0.7))


class TestEnvelopeSubsidies:
    """BEG EM envelope schemes (15 % + 5 % iSFP, U-value conditions)."""

    def _measure(self, u_value: float = 0.18, cost: float = 25000.0) -> MeasureForSubsidy:
        """A wall-insulation measure handed straight to the subsidy solver.

        The cost is given explicitly rather than resolved from the database, so the awards below
        are readable percentages of a round 25 000 EUR. The `u_value` is the only technical
        attribute the BEG envelope schemes condition on, which is why it is the parameter: 0.18
        passes the technical minimum, 0.35 fails it.
        """
        facts = ComponentCostFacts(
            asset_class=ComponentType.WALL_INSULATION,
            size=100.0,
            size_unit=Units.SQUARE_METER,
            technical_attributes={"u_value": u_value},
        )
        return MeasureForSubsidy(
            subject="Envelope.WallInsulation",
            facts=facts,
            measure_kind="REPLACE",
            cost_by_category={CostCategory.INVESTMENT: UncertainValue.exact(cost)},
        )

    def _context(self, has_isfp) -> SubsidyContext:
        """An owner-occupied single-family applicant with everything but the iSFP flag answered.

        The building fields are the ones BEG eligibility and the residential-share proration read;
        a single dwelling unit and zero commercial area keep proration at 1.0 so the awards are
        undiluted. `has_isfp` is passed through as the tri-state it is: True, False, or None for
        "not asked yet", which is what turns the bonus UNDETERMINED and generates a question.
        """
        return SubsidyContext(
            applicant=ApplicantProfile(actor=ApplicantActor.OWNER_OCCUPIER, main_residence=True),
            building=SubsidyBuildingContext(
                construction_year=1975,
                dwelling_units=1,
                residential_floor_area_in_m2=140.0,
                has_isfp=has_isfp,
            ),
        )

    #: Undiscounted, the 3-year §35c tax credit (20 %) exactly ties the 15+5 % upfront grant;
    #: any positive discount rate makes the upfront grant win, as in reality.
    DISCOUNT = staticmethod(lambda year: 1.0 / (1.03**year))

    def test_base_rate_plus_isfp_bonus(self):
        """15 % + 5 % iSFP on the eligible cost."""
        catalog = SubsidyCatalog.load("DE")
        decision = solve_cumulation(
            catalog, self._measure(), self._context(has_isfp=True), 2026, self.DISCOUNT
        )
        applied = {award.scheme_id: award for award in decision.applied}
        assert "DE_BEG_EM_ENVELOPE_2024" in applied
        assert "DE_BEG_EM_ENVELOPE_ISFP_2024" in applied
        total = sum(award.upfront_amount.average for award in decision.applied)
        assert total == pytest.approx(0.20 * 25000.0)

    def test_u_value_condition_rejects_poor_insulation(self):
        """Wall U-value above 0.20 W/m2K fails the technical minimum requirement."""
        catalog = SubsidyCatalog.load("DE")
        decision = solve_cumulation(
            catalog, self._measure(u_value=0.35), self._context(has_isfp=False), 2026, self.DISCOUNT
        )
        assert "DE_BEG_EM_ENVELOPE_2024" in {reject["scheme_id"] for reject in decision.rejected}

    def test_unknown_isfp_is_undetermined_and_asked(self):
        """Tri-state: unknown iSFP status leaves the bonus undetermined; the question is asked."""
        catalog = SubsidyCatalog.load("DE")
        context = self._context(has_isfp=None)
        decision = solve_cumulation(catalog, self._measure(), context, 2026, self.DISCOUNT)
        assert "DE_BEG_EM_ENVELOPE_ISFP_2024" in {item["scheme_id"] for item in decision.undetermined}
        assert decision.undetermined_upper_bound_in_euro > 0
        questions = required_questions(catalog, [self._measure()], context, 2026)
        assert "building.has_isfp" in [question.entry.fieldname for question in questions]
