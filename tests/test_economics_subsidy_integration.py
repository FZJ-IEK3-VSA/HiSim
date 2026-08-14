"""Cross-layer tests for subsidy provenance and scenario analysis (§3.10, §4.6).

**Why this file exists separately.** Every test here drives the subsidy and scenario machinery
*through* an engine module: it imports `evaluator`, `perspectives`, `scenarios` or `financing`,
which sit a layer above the rule engines they exercise. The unit tests for those same rule engines have no such
dependency and live in `test_economics_subsidies_tariffs.py`, so that each stage of the review
stack is self-testing — the subsidy and tariff engines ship with their own passing tests, and only
the cross-layer cases wait for the evaluator. Splitting on that line is the whole point of the
file; adding a test here that needs nothing above the rule engines means it belongs in the
sibling file.

**Surface.** Two subjects. `TestSubsidyProvenanceThroughTheEvaluator` follows a subsidy award from
the catalog through a real evaluation into `explain()`, checking that the source ids survive the
whole chain and that the §10.1 legacy shim labels itself as a migration leftover rather than a
scheme. `TestScenarioAnalysis` covers §4.6: scenario-set expansion, the evaluation cube with its
tornado / swing / spread helpers, the counterfactual billing boundary and the break-even search.

**Error class.** A failure here is an *integration* failure — the rules themselves are checked in
the sibling file, so if those pass and these do not, the defect is in how the evaluator wires the
rule engines together, or in a number losing its provenance on the way to the report (§3.10).
"""

# clean

import dataclasses

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts, ExistingAsset
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


@pytest.fixture(name="catalog", scope="module")
def fixture_catalog() -> SubsidyCatalog:
    """The shipped DE subsidy catalog.

    Duplicated from `test_economics_subsidies_tariffs.py` rather than shared through a conftest:
    both files need it, it is four lines of body, and a conftest would hide which file owns the
    fixture. Module-scoped because loading resolves and validates the whole catalog including its
    source registry.
    """
    return SubsidyCatalog.load("DE")


def full_context(income: float = 35000.0) -> SubsidyContext:
    """Owner-occupier with a functioning gas boiler, everything answered.

    The same helper as in `test_economics_subsidies_tariffs.py`, duplicated for the same reason as
    the `catalog` fixture. "Everything answered" is the point: no field is None, so no scheme can
    come back UNDETERMINED and every award below is a definite yes. The functioning gas boiler
    makes the BEG speed bonus eligible, and 35 000 EUR of taxable household income sits below the
    income-bonus threshold.
    """
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


class TestSubsidyProvenanceThroughTheEvaluator:
    """W2.4: a subsidy award keeps its sources all the way into the report.

    The catalog-level half of W2.4 — that a loaded scheme cites its registry entries and an
    in-memory scheme says so honestly — is `TestSubsidyProvenance` in the sibling file. These two
    tests are the end of that chain: they run a real evaluation and check what reaches `explain()`.
    """

    def test_legacy_flat_shim_records_its_own_origin(self):
        """W2.6: support from the §10.1 shim is labelled as a migration leftover, not a scheme."""
        from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.provenance import ParameterOrigin
        from hisim.economics.timeline import CostCategory

        database = CostDatabase()
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2024))
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    ComponentCostFacts(
                        asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT
                    ),
                )
            ],
        )
        result = evaluator.evaluate(
            inputs,
            Perspective(
                id="test_legacy_shim",
                installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.full(),
            ),
        )
        subsidy_entries = [
            entry for entry in result.timeline.entries if entry.category == CostCategory.SUBSIDY
        ]
        assert subsidy_entries and {entry.subsidy_scheme_id for entry in subsidy_entries} == {"LEGACY_FLAT"}
        shim_records = [
            record
            for record in result.ledger.records
            if record.origin == ParameterOrigin.LEGACY_MIGRATION_SHIM
        ]
        assert len(shim_records) == 1
        assert shim_records[0].parameter.endswith(".legacy_flat_subsidy_share")
        assert "§10.1" in (shim_records[0].detail or "")
        # The shim record is attached to the support it explains.
        assert any(
            result.ledger.records.index(shim_records[0]) in entry.provenance_ids
            for entry in subsidy_entries
        )

    def test_subsidy_sources_reach_the_report(self, catalog):
        """load -> ledger -> `explain`: a subsidy award resolves to the statute it comes from."""
        from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        database = CostDatabase()
        parameters = EconomicParameters(country="DE", price_basis_year=2024)
        evaluator = EconomicEvaluator(database, parameters, catalog)
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    ComponentCostFacts(
                        asset_class=ComponentType.HEAT_PUMP,
                        size=10.0,
                        size_unit=Units.KILOWATT,
                        technical_attributes={"scop": 4.0, "refrigerant": "R290"},
                    ),
                )
            ],
            subsidy_context=full_context(),
        )
        result = evaluator.evaluate(
            inputs,
            Perspective(
                id="test_subsidised",
                installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.full(),
            ),
        )
        subsidy_records = [
            record for record in result.ledger.records if record.parameter.startswith("subsidy.")
        ]
        assert subsidy_records
        assert not any(
            source_id.startswith("inline:") for record in subsidy_records for source_id in record.source_ids
        )
        report = result.explain("npv_by_category[SUBSIDY]")
        assert "src_beg_em_2023" in {source.source_id for source in report.sources}
        assert all(source.kind != "INLINE" for source in report.sources)


class TestScenarioAnalysis:
    """§4.6 scenario sets, overlays and derived analyses, run through the evaluator.

    Expansion, the evaluation cube and the break-even search all need a real evaluation behind
    them. The two pure data-overlay tests, which need only the cost database, stay in
    `test_economics_subsidies_tariffs.py` as `TestScenarioDataOverlays`.
    """

    def _inputs(self):
        """The variant every scenario sweep is run on: one database-priced heat pump, 4 000 kWh.

        Nothing is overridden here, unlike the fixtures of the sibling unit-test file — a scenario
        axis has to be able to *change* something, and a data overlay on `devices_DE.HEAT_PUMP`
        only bites when the price actually comes from the database. Small enough that a full cube
        is a handful of evaluations.
        """
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

    def test_cube_swings_and_spreads_match_the_report_derivation(self):
        """W4.1: the cube itself provides what the scenario section used to derive in HTML."""
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube

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
        cube = evaluate_cube(self._inputs(), EconomicParameters(price_basis_year=2024), [perspective], scenario_set)
        per_scenario = cube.results["gross"]
        # Independent recomputation, exactly as reporting._scenario_section_html did it.
        base_value = per_scenario["central"].equivalent_annual_cost_in_euro.average
        expected_swings = {
            scenario_id: result.equivalent_annual_cost_in_euro.average - base_value
            for scenario_id, result in per_scenario.items()
        }
        assert cube.equivalent_annual_cost_swings("gross") == pytest.approx(expected_swings)
        assert cube.equivalent_annual_cost_swings("gross")["central"] == 0.0
        values = [result.equivalent_annual_cost_in_euro.average for result in per_scenario.values()]
        spread = cube.equivalent_annual_cost_spreads()["gross"]
        assert (spread.minimum, spread.maximum) == pytest.approx((min(values), max(values)))
        assert spread.spread == pytest.approx(max(values) - min(values))

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

    def test_robustness_summary_rejects_an_empty_scenario_set(self):
        """An empty cube is named as such instead of surfacing as a bare `min()` error (§4.6).

        `robustness_summary` folds min/max over the per-scenario deltas, so a cube whose scenario
        list is empty used to fail deep inside the standard library with "min() arg is an empty
        sequence" — a message that names neither the function nor the input that was empty. The
        D7 fail-fast philosophy asks for the opposite: a `ScenarioDataError` that says which
        analysis refused and why.
        """
        from hisim.economics.scenarios import ScenarioCube, ScenarioDataError, robustness_summary

        empty = ScenarioCube(results={"gross": {}}, scenarios=[])
        with pytest.raises(ScenarioDataError, match="robustness_summary"):
            robustness_summary(empty, empty, "gross")

    def test_break_even_finds_crossing(self):
        """Bisection on the interest rate between a capex-heavy and an opex-heavy variant."""
        from hisim.economics.evaluator import EvaluationInputs, SubjectCostFacts
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import find_break_even

        def variant(investment: float, energy_kwh: float) -> EvaluationInputs:
            """One point on the capex/opex trade-off, with everything else neutralized.

            The service life equals the horizon and maintenance is zero, so the two variants
            differ only in their year-0 investment and their annual energy bill — which is what
            makes the interest rate the single axis the crossing can depend on.
            """
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
        # The point of the test is that a crossing is *found*: the previous assertion was
        # satisfied by "no crossing in range", i.e. by the bisection never running at all.
        assert outcome["no_crossing_in_range"] is False
        assert outcome["break_even"] is not None
        low, high = 0.0, 0.15
        assert low < outcome["break_even"] < high
        # The capex-heavy variant is the cheaper one below the crossing and the dearer one above:
        # that sign flip is what makes the returned number a break-even rather than an artefact.
        from hisim.economics.evaluator import EconomicEvaluator

        database = CostDatabase()

        def annual_cost_delta(interest_rate: float) -> float:
            """Capex-heavy minus opex-heavy equivalent annual cost at one interest rate."""
            evaluator = EconomicEvaluator(
                database, EconomicParameters(price_basis_year=2024, interest_rate=interest_rate)
            )
            capex_heavy = evaluator.evaluate(variant(20000.0, 1000.0), perspective)
            opex_heavy = evaluator.evaluate(variant(2000.0, 4000.0), perspective)
            return (
                capex_heavy.equivalent_annual_cost_in_euro.average
                - opex_heavy.equivalent_annual_cost_in_euro.average
            )

        assert annual_cost_delta(low) < 0 < annual_cost_delta(high)
        assert abs(annual_cost_delta(outcome["break_even"])) < 1.0  # EUR/a, at the root


class TestDeletedDeadSurfaceInTheEngine:
    """§8 D23, the half that needs an engine module.

    `FinancingPlan` lives in `financing`, a layer above the rule engines, so this assertion sits
    here rather than beside its sibling in `test_economics_subsidies_tariffs.py`. It pins that a
    stale perspective file naming the deleted `refinance_replacements` field now fails loudly (D7)
    instead of being accepted and quietly ignored.
    """

    def test_refinance_replacements_is_gone_and_rejected(self):
        """The field is off the dataclass, so `FinancingPlan(**raw)` rejects a stale file."""
        from hisim.economics.financing import FinancingPlan

        fields = {field.name for field in dataclasses.fields(FinancingPlan)}
        assert "refinance_replacements" not in fields
        with pytest.raises(TypeError):
            FinancingPlan(**{"refinance_replacements": True})
