"""Reporting tests: plausibility panel, markdown summary, and the HTML report's sections (cost_spec.md §7.2, §7.4).

First of the two reporting test files (split per the PR-3 review's 500-line rule): the
plausibility checks and their rendering, the diffable markdown summary, the HTML report's
structure, the degenerate-band banner, and the economic-context sections (timeline detail,
component stacks, tenant view). The input audit, the cross-perspective subsidy cards and the
PNG/CLI outputs are in `test_economics_reporting_outputs.py`.
"""

import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.plausibility import PlausibilityConfig, run_plausibility_checks
from hisim.economics.reporting import (
    all_bands_degenerate,
    build_cost_summary_markdown,
    build_lifecycle_report_html,
    render_plausibility_findings,
)
from hisim.economics.results import EvaluationMatrix, compare
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database.

    The reports are rendered from real price data on purpose: the plausibility panel's range
    checks (effective price, EAC per m2, maintenance ratio) are only meaningful against numbers
    of realistic magnitude. Module-scoped because loading and validating it dominates the runtime
    of this file.
    """
    return CostDatabase()


def make_inputs(energy_kwh: float = 5000.0, investment: float = 16000.0) -> EvaluationInputs:
    """A small but complete evaluation input set.

    One banded heat pump plus one bought-electricity carrier — the minimum that still produces
    every figure the reports need: an investment with whiskers, an energy bill, a heat demand for
    the levelized cost of heat, and a living area for the per-m2 checks. The two arguments exist
    so a *second*, deliberately worse variant (cheap device, high consumption) can be built from
    the same shape for the comparison sections.
    """
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(investment, investment * 0.8, investment * 1.3),
        lifetime_override_in_years=18.0,
        override_source="test",
    )
    return EvaluationInputs(
        simulation_year=2026,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("HeatPump", facts)],
        billing=[BillingDeterminants(carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=energy_kwh)],
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
    )


def _audit(database, inputs, matrix):
    """The typed input audit the report's section 1 renders (W4.6).

    `build_input_audit` resolves every declared fact against the database exactly once and returns
    typed rows; the CSV writer and the HTML section then render the same rows, which is what keeps
    them from disagreeing. The result passed along only supplies the resulting gross investment
    and subsidy outcome per subject, and the assertions here are about origins, prices and flags,
    so the matrix's first perspective is taken rather than a specific one.
    """
    from hisim.economics.audit import build_input_audit

    return build_input_audit(
        inputs, database, EconomicParameters(country="DE", price_basis_year=inputs.simulation_year),
        next(iter(matrix.results.values())),
    )


@pytest.fixture(name="matrix", scope="module")
def fixture_matrix(database) -> EvaluationMatrix:
    """Default-bundle evaluation of the test inputs.

    Uses the *shipped* perspective bundle (filtered by `select_applicable` for the absence of an
    existing-asset register, so the brownfield rows drop out) rather than a hand-picked set: the
    reports are what an ordinary run produces, and the perspective table is one of the things
    under test. Module-scoped so the ~half-dozen evaluations happen once for the whole file.
    """
    evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
    matrix = EvaluationMatrix()
    for perspective in select_applicable(load_default_bundle(), has_register=False):
        matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
    return matrix


PANEL_BEFORE_W42 = [
    ("subjects sum to total (greenfield_gross)", "PASS", "delta 0.00 EUR", "0", ""),
    ("subjects sum to total (greenfield_net)", "PASS", "delta 0.00 EUR", "0", ""),
    ("subjects sum to total (operating)", "PASS", "delta 0.00 EUR", "0", ""),
    ("residual value <= purchases (greenfield_gross)", "PASS", "11,247 vs 29,423 EUR",
     "residual below discounted purchases", ""),
    ("residual value <= purchases (greenfield_net)", "PASS", "11,247 vs 29,423 EUR",
     "residual below discounted purchases", ""),
    ("subsidies <= eligible basis (greenfield_net)", "PASS", "4,800 vs 16,000 EUR",
     "support below its cost basis", ""),
    ("effective ELECTRICITY price (year 1)", "PASS", "0.352 EUR/kWh", "0.1 - 0.6 EUR/kWh",
     "1,760 EUR for 5,000 kWh — catches unit mix-ups"),
    ("equivalent annual cost per m2 (greenfield_gross)", "PASS", "24.032 EUR/m2a", "5 - 80 EUR/m2a", ""),
    ("levelized cost of heat", "PASS", "0.240 EUR/kWh", "0.05 - 0.5 EUR/kWh", ""),
    ("maintenance / investment NPV ratio (greenfield_gross)", "PASS", "0.145 ", "0.02 - 0.8 ",
     "a huge ratio usually means an absolute fee stored as a rate (issues #1)"),
    ("uncertainty band width max/min (greenfield_gross)", "PASS", "1.994 x", "1 - 3.5 x",
     "over 20 years; very wide bands usually mean a band typo in the data"),
]


class TestPlausibilityChecks:
    """The automated panel (B), now produced engine-side and rendered here (W4.2)."""

    def test_clean_result_passes_all_checks(self, matrix):
        """A sane result raises no flags."""
        report = run_plausibility_checks(matrix)
        assert report.findings
        assert report.ok(), [f"{finding.name}: {finding.value}" for finding in report.flagged()]

    def test_out_of_range_effective_price_warns(self, matrix):
        """A narrow configured range flags the effective price as WARN, not FAIL."""
        config = PlausibilityConfig(effective_price_ranges={"ELECTRICITY": (0.0, 0.001)})
        report = run_plausibility_checks(matrix, config)
        price_checks = [finding for finding in report if "effective ELECTRICITY" in finding.name]
        assert price_checks and price_checks[0].status == "WARN"

    def test_invariants_reported_as_structural(self, matrix):
        """Reconciliation checks exist per perspective and pass."""
        report = run_plausibility_checks(matrix)
        reconciliation = [f for f in report if f.name.startswith("subjects sum to total")]
        assert len(reconciliation) == len(matrix.results)
        assert all(finding.status == "PASS" for finding in reconciliation)

    def test_panel_is_unchanged_by_the_move_engine_side(self, matrix):
        """Semantics parity (W4.2): same checks, same order, same rendered rows as before."""
        rendered = [
            (check.name, check.status, check.value, check.expected, check.detail)
            for check in render_plausibility_findings(run_plausibility_checks(matrix))
        ]
        assert rendered == PANEL_BEFORE_W42

    def test_findings_carry_numbers_not_strings(self, matrix):
        """The engine-side finding is data: a float value, a unit and the required bounds."""
        report = run_plausibility_checks(matrix)
        price = next(finding for finding in report if "effective ELECTRICITY" in finding.name)
        assert isinstance(price.value, float)
        assert price.unit == "EUR/kWh"
        assert price.bounds == (0.10, 0.60)
        # The bill and the quantity behind the price are data too, not text in a detail string.
        assert price.context["quantity"] == pytest.approx(5000.0)
        assert price.value == pytest.approx(price.context["year1_cost"] / price.context["quantity"])


class TestCostSummaryMarkdown:
    """The diffable text report (C)."""

    def test_summary_contains_all_sections(self, matrix):
        """Header, checks, perspectives, structure, subjects."""
        checks = run_plausibility_checks(matrix)
        text = build_cost_summary_markdown(matrix, checks)
        for marker in (
            "# Lifecycle cost summary",
            "## Plausibility checks",
            "## Perspectives",
            "## Cost structure",
            "## Per subject",
            "HeatPump",
            "ELECTRICITY",
        ):
            assert marker in text, marker

    def test_summary_carries_comparison_section(self, database, matrix):
        """The variant comparison (D) appears with payback band and subject deltas."""
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        perspective = select_applicable(load_default_bundle(), has_register=False)[0]
        reference = evaluator.evaluate(make_inputs(energy_kwh=15000.0, investment=2000.0), perspective)
        variant = matrix.results[perspective.id]
        comparison = compare(reference, variant, "base", "measures")
        checks = run_plausibility_checks(matrix)
        text = build_cost_summary_markdown(matrix, checks, comparison)
        assert "## Variant comparison" in text
        assert "Discounted payback" in text
        assert "NPV delta" in text


class TestHtmlReport:
    """The self-contained HTML report (A)."""

    def test_report_contains_chain_sections_and_charts(self, database, matrix):
        """All chain sections render, with inline SVGs and no external resources."""
        checks = run_plausibility_checks(matrix)
        text = build_lifecycle_report_html(matrix, checks, _audit(database, make_inputs(), matrix))
        for marker in (
            "0 - Plausibility panel",
            "1 - Input audit",
            "sources used",  # §3.10 registry table
            "2 - Investment build-up",
            "investment table",
            "3 - Cash-flow timeline",
            "NPV by cost category",  # §3.7 result table
            "4 - Year-1 energy bill",
            "4b - Lifecycle CO2",  # §3.8
            "6 - Perspectives at a glance",
            "7 - Per-component breakdown",
            "subject table",
            "10 - Lifecycle KPIs",  # §7.3
        ):
            assert marker in text, marker
        assert text.count("<svg") >= 5
        assert "https://" not in text.split("sources used", maxsplit=1)[0]  # charts stay self-contained
        assert "prefers-color-scheme: dark" in text  # theme-aware

    def test_report_with_comparison_section(self, database, matrix):
        """Section 8 renders the delta waterfall and the payback curve."""
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        perspective = select_applicable(load_default_bundle(), has_register=False)[0]
        reference = evaluator.evaluate(make_inputs(energy_kwh=15000.0, investment=2000.0), perspective)
        comparison = compare(reference, matrix.results[perspective.id], "base", "measures")
        checks = run_plausibility_checks(matrix)
        text = build_lifecycle_report_html(matrix, checks, _audit(database, make_inputs(), matrix), comparison)
        assert "8 - Variant comparison" in text
        assert "Discounted payback" in text


class TestDegenerateBandBanner:
    """Missing whiskers must read as a data property, not a rendering bug."""

    def _exact_matrix(self, database) -> EvaluationMatrix:
        """An evaluation in which every input band is degenerate (min = best_estimate = max).

        Every uncertain figure is overridden with an exact value and the price basis is moved to
        2024, whose migrated legacy entries are degenerate by construction (README §4.2) — so no
        band can enter through the database either. The result is a run with no whiskers at all,
        which is the situation the report has to explain rather than silently render flat.
        """
        facts = ComponentCostFacts(
            asset_class=ComponentType.HEAT_PUMP,
            size=10.0,
            size_unit=Units.KILOWATT,
            investment_cost_override_in_euro=UncertainValue.exact(16000.0),
            lifetime_override_in_years=18.0,
            maintenance_rate_override=UncertainValue.exact(0.015),
            override_source="test",
        )
        inputs = EvaluationInputs(
            simulation_year=2024,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("HeatPump", facts)],
        )
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2024))
        matrix = EvaluationMatrix()
        perspective = select_applicable(load_default_bundle(), has_register=False)[0]
        matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)
        return matrix

    def test_banner_appears_for_degenerate_bands(self, database):
        """Exact inputs everywhere -> the report explains why there are no whiskers."""
        matrix = self._exact_matrix(database)
        assert all_bands_degenerate(matrix)
        inputs = EvaluationInputs(simulation_year=2024, simulated_period_fraction=1.0)
        checks = run_plausibility_checks(matrix)
        html_text = build_lifecycle_report_html(matrix, checks, _audit(database, inputs, matrix))
        assert "No uncertainty bands in this run" in html_text
        markdown = build_cost_summary_markdown(matrix, checks)
        assert "degenerate" in markdown

    def test_banner_absent_with_banded_data(self, database, matrix):
        """The 2026 price basis carries real bands -> no banner, whiskers present."""
        assert not all_bands_degenerate(matrix)
        inputs = make_inputs()
        checks = run_plausibility_checks(matrix)
        html_text = build_lifecycle_report_html(matrix, checks, _audit(database, inputs, matrix))
        assert "No uncertainty bands in this run" not in html_text


class TestEconomicContextAndNewSections:
    """EconomicContext merge (bridge) plus the actor-split and scenario sections."""

    def test_merge_context_enriches_inputs(self):
        """Register, envelope measures, technical attributes and tenancy data are merged."""
        from hisim.economics.bridge import EconomicContext, _merge_context
        from hisim.economics.facts import ExistingAsset, ExistingAssetRegister

        inputs = make_inputs()
        context = EconomicContext(
            existing_assets=ExistingAssetRegister(
                assets=[
                    ExistingAsset(
                        asset_class=ComponentType.GAS_HEATER,
                        size=15.0,
                        size_unit=Units.KILOWATT,
                        installation_year=2011,
                        replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
                    )
                ]
            ),
            extra_cost_facts=[
                SubjectCostFacts(
                    "Envelope.Windows",
                    ComponentCostFacts(
                        asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED, size=28.0, size_unit=Units.SQUARE_METER
                    ),
                )
            ],
            technical_attributes_by_subject={"HeatPump": {"scop": 4.1}},
            living_area_in_m2=150.0,
            current_cold_rent_in_euro_per_m2_month=8.5,
            annual_heat_demand_in_kwh=15000.0,
        )
        _merge_context(inputs, context)
        assert inputs.existing_assets is not None
        assert any(sf.subject == "Envelope.Windows" for sf in inputs.cost_facts)
        heat_pump_facts = next(sf.facts for sf in inputs.cost_facts if sf.subject == "HeatPump")
        assert heat_pump_facts.technical_attributes["scop"] == 4.1
        assert inputs.living_area_in_m2 == 150.0
        assert inputs.annual_heat_demand_in_kwh == 15000.0

    def test_actor_and_scenario_sections_render(self, database):
        """A tenant-scope result yields section 6b; a scenario cube yields section 9."""
        from hisim.economics.perspectives import ActorScope, InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube

        inputs = make_inputs()
        parameters = EconomicParameters(country="DE", price_basis_year=2026)
        evaluator = EconomicEvaluator(database, parameters)
        perspectives = [
            Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                        subsidy_mode=SubsidyMode.none()),
            Perspective(id="tenant", installation_context=InstallationContext.GREENFIELD,
                        actor_scope=ActorScope.TENANT, subsidy_mode=SubsidyMode.none()),
        ]
        matrix = EvaluationMatrix()
        for perspective in perspectives:
            matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)
        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "ONE_AT_A_TIME",
                "axes": [{"name": "interest", "field": "interest_rate", "levels": {"low": 0.01, "high": 0.05}}],
            }
        )
        cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database)
        checks = run_plausibility_checks(matrix)
        text = build_lifecycle_report_html(matrix, checks, _audit(database, inputs, matrix), scenario_cube=cube)
        assert "6b - Who pays what" in text
        assert "9 - Scenario analysis" in text
        assert "interest=high" in text
        # The cumulative NPV chart carries its uncertainty band (banded data -> polygon).
        assert "<polygon" in text

    def test_timeline_detail_table_attributes_every_flow(self, database):
        """Section 3's detail table lists (year, subject, category) incl. anyway credits."""
        from hisim.economics.facts import ExistingAsset, ExistingAssetRegister
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.reporting import _timeline_detail_table

        old_windows = ExistingAsset(
            asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED,
            size=25.0,
            size_unit=Units.SQUARE_METER,
            installation_year=2026 - 33,  # 2 a of 35 remaining -> credit at year 2
            replaced_by_asset_classes=[ComponentType.WINDOWS_TRIPLE_GLAZED],
        )
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "Envelope.Windows",
                    ComponentCostFacts(asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED, size=25.0, size_unit=Units.SQUARE_METER),
                )
            ],
            existing_assets=ExistingAssetRegister(assets=[old_windows]),
        )
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        result = evaluator.evaluate(
            inputs,
            Perspective(id="brownfield", installation_context=InstallationContext.BROWNFIELD,
                        subsidy_mode=SubsidyMode.none()),
        )
        table = _timeline_detail_table(result)
        assert "ANYWAY_COST_CREDIT" in table
        assert "year total" in table
        # The credit sits in year 2 (remaining life of the replaced windows).
        credit_row = next(row for row in table.split("<tr>") if "ANYWAY_COST_CREDIT" in row)
        assert credit_row.startswith("<td>2</td>")

    def test_component_stacks_diverge_and_whisker_is_net(self, database):
        """§7.4 chart: credits stack left of zero, costs right; the whisker marks the NET band.

        Regression for the earlier layout that stacked absolute values (credits drawn as
        costs), which made the bar overstate and the net whisker end 'inside' it.
        """
        import re

        from hisim.economics.reporting import _stacked_subject_svg

        inputs = make_inputs()  # subsidised + residual value -> credit segments exist
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        perspective = select_applicable(load_default_bundle(), has_register=False)[0]
        result = evaluator.evaluate(inputs, perspective)
        svg = _stacked_subject_svg(result)
        # Credits are drawn on their own (left) side, marked as such:
        assert 'class="credit"' in svg
        # The zero baseline exists and every net dot sits at a signed position (circles present):
        assert svg.count("<circle") == len(result.component_breakdowns)
        # Residual value must NOT appear as a positive-side cost segment: its tooltip carries
        # a negative amount.
        residual_tooltips = re.findall(r"Residual value &amp; anyway credit: (-?[\d.,k]+) EUR NPV", svg)
        assert residual_tooltips and all(text.startswith("-") for text in residual_tooltips)

    def test_tenant_timeline_chart_shows_only_tenant_flows(self, database):
        """Actor-scoped perspectives plot the scoped timeline: no investment in the tenant view."""
        from hisim.economics.perspectives import ActorScope, InstallationContext, Perspective, SubsidyMode
        from hisim.economics.reporting import _annual_flow_svg
        from hisim.economics.timeline import CostCategory

        inputs = make_inputs()
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        tenant = evaluator.evaluate(
            inputs,
            Perspective(id="tenant", installation_context=InstallationContext.GREENFIELD,
                        actor_scope=ActorScope.TENANT, subsidy_mode=SubsidyMode.none()),
        )
        # The scoped timeline has no investment (allocated to the landlord)...
        assert CostCategory.INVESTMENT not in tenant.npv_by_category
        scoped_categories = {entry.category for entry in tenant.scoped_timeline().entries}
        assert CostCategory.INVESTMENT not in scoped_categories
        # ...and neither does the chart (which previously plotted the full system timeline).
        chart = _annual_flow_svg(tenant)
        assert "Investment &amp; financing" not in chart
        assert "Energy" in chart  # tenant flows still render
