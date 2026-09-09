"""Reporting tests: input audit, cross-perspective subsidy cards, PNGs and the CLI (cost_spec.md §7.2, §9.5).

Second of the two reporting test files (split per the PR-3 review's 500-line rule): the
input-audit section (origins, price-basis column, unresolved rows — review findings 12/13),
the subsidy decision cards de-duplicated by content and annotated with perspectives (D28),
and the PNG chart / CLI smoke coverage. The panel, summary and HTML-section tests are in
`test_economics_reporting.py`.
"""

import os
import pytest
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.plausibility import run_plausibility_checks
from hisim.economics.report_plots import write_report_plots
from hisim.economics.reporting import build_cost_summary_markdown, build_lifecycle_report_html
from hisim.economics.results import EvaluationMatrix
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


class TestInputAudit:
    """One resolution, two renderings (W4.6)."""

    def test_csv_and_html_render_the_same_resolved_rows(self, database, matrix, tmp_path):
        """The CSV writer and the report's section 1 read the same typed rows."""
        from hisim.economics.audit import build_input_audit, write_cost_audit
        from hisim.economics.input_audit import OriginKind

        inputs = make_inputs()
        audit = _audit(database, inputs, matrix)
        row = next(row for row in audit.rows if row.subject == "HeatPump")
        assert row.origin_kind == OriginKind.ORIGIN_OVERRIDE  # the fixture overrides the investment
        assert row.unit_price_in_euro is not None
        assert row.unit_price_in_euro.best_estimate == pytest.approx(16000.0)

        with open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8") as audit_file:
            csv_text = audit_file.read()
        assert "config override (test)" in csv_text
        html_text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix), audit)
        assert "override (test)" in html_text
        # Same source registry in both directions: the audit resolved it, nobody re-derived it.
        assert audit.sources
        assert f"sources used ({len(audit.sources)} registry entries" in html_text
        assert build_input_audit(inputs, database, EconomicParameters(country="DE", price_basis_year=2026)).rows

    def test_the_anyway_columns_state_the_share_and_the_cost_it_applied_to(self, database, matrix, tmp_path):
        """A share with no basis cannot be audited: 30 % of what? (review, agreed small fix).

        The CSV published "Anyway share" alone, so a reader could see that a credit had been
        reduced and had no second number to multiply it by. The basis is the escalated
        like-for-like cost the share scaled, which is exactly what the row now carries beside it.
        """
        import copy

        from hisim.economics.audit import build_input_audit, write_cost_audit

        inputs = make_inputs()
        # A copy, because the matrix fixture is module-scoped and every other test in this file
        # reads the same results.
        result = copy.copy(next(iter(matrix.results.values())))
        result.anyway_share_by_subject = {"HeatPump": 0.3}
        result.anyway_basis_by_subject = {"HeatPump": 9000.0}
        audit = build_input_audit(
            inputs, database, EconomicParameters(country="DE", price_basis_year=2026), result
        )
        with open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8") as audit_file:
            lines = audit_file.read().splitlines()
        header = lines[0].split(";")
        assert header.index("Anyway basis [EUR]") == header.index("Anyway share") + 1
        cells = lines[1].split(";")
        assert cells[header.index("Anyway share")] == "0.3"
        assert cells[header.index("Anyway basis [EUR]")] == "9000.0"

    def test_override_survives_a_missing_database_entry(self, database, matrix):
        """Precedence is decided once: an override prices the row even with no entry (W4.6).

        The HTML report used to drop the price here while the CSV kept it — the divergence that
        made the audit table a typed object in the first place.
        """
        from hisim.economics.audit import _csv_origin, build_input_audit
        from hisim.economics.input_audit import OriginKind

        facts = ComponentCostFacts(
            asset_class=ComponentType.WINDTURBINE,  # deliberately absent from the DE 2026 device data
            size=1.0,
            size_unit=Units.ANY,
            investment_cost_override_in_euro=UncertainValue.exact(4321.0),
            override_source="test",
        )
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[SubjectCostFacts("Weird", facts)],
        )
        audit = build_input_audit(
            inputs, database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        row = audit.rows[0]
        assert "no database entry" in row.flags
        assert row.origin_kind == OriginKind.ORIGIN_OVERRIDE
        assert row.unit_price_in_euro is not None and row.unit_price_in_euro.best_estimate == 4321.0
        assert _csv_origin(row) == "config override (test)"
        assert "4,321" in build_lifecycle_report_html(matrix, run_plausibility_checks(matrix), audit)

    def test_csv_says_what_the_unit_price_column_is_measured_in(self, database, tmp_path):
        """The three price columns carry two different quantities; the CSV names which (finding 12).

        A database row prices euro per unit of size, an override prices the subject outright. Read
        as the same quantity a 1,500 EUR/kW heat pump and a 4,321 EUR wind turbine sit in the same
        column with no hint that only one of them still has to be multiplied by anything.
        """
        from hisim.economics.audit import build_input_audit, write_cost_audit

        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "Priced",
                    ComponentCostFacts(
                        asset_class=ComponentType.HEAT_PUMP, size=10.0, size_unit=Units.KILOWATT
                    ),
                ),
                SubjectCostFacts(
                    "Overridden",
                    ComponentCostFacts(
                        asset_class=ComponentType.HEAT_PUMP,
                        size=10.0,
                        size_unit=Units.KILOWATT,
                        investment_cost_override_in_euro=UncertainValue.exact(4321.0),
                        override_source="test",
                    ),
                ),
            ],
        )
        audit = build_input_audit(
            inputs, database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        with open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8") as audit_file:
            lines = audit_file.read().splitlines()
        header = lines[0].split(";")
        assert header.index("Price basis") == header.index("Unit price min") - 1
        basis_column = header.index("Price basis")
        assert lines[1].split(";")[basis_column] == "EUR/kW (database)"
        assert lines[2].split(";")[basis_column] == "EUR absolute (override)"

    def test_an_unpriceable_row_is_not_reported_as_priced_from_the_database(self, database, tmp_path):
        """UNRESOLVED gets its own label instead of falling through to "database entry" (13).

        The row exists precisely because nothing priced the subject; saying "database entry" about
        it points a reviewer at a data file that never mentioned the component, and hides the one
        thing the row is there to report.
        """
        from hisim.economics.audit import _csv_origin, build_input_audit, write_cost_audit
        from hisim.economics.input_audit import OriginKind

        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "Unpriceable",
                    ComponentCostFacts(
                        asset_class=ComponentType.WINDTURBINE, size=1.0, size_unit=Units.ANY
                    ),
                )
            ],
        )
        audit = build_input_audit(
            inputs, database, EconomicParameters(country="DE", price_basis_year=2026)
        )
        row = audit.rows[0]
        assert row.origin_kind == OriginKind.ORIGIN_UNRESOLVED
        assert _csv_origin(row) == "unresolved - not priced"

        with open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8") as audit_file:
            lines = audit_file.read().splitlines()
        header = lines[0].split(";")
        cells = lines[1].split(";")
        assert cells[header.index("Investment origin")] == "unresolved - not priced"
        assert cells[header.index("Price basis")] == ""  # no price, so no basis to state
        assert cells[header.index("Unit price best_estimate")] == ""


class TestSubsidyDecisionsAcrossPerspectives:
    """Review finding 16: a decision only some perspectives reached must still be reported."""

    @staticmethod
    def _matrix(database, modes):
        """Evaluates one subsidised heat pump once per named subsidy mode.

        The subsidy context is the minimum the DE catalog needs to price a heat pump for an
        owner-occupier — without it no scheme applies and there is no decision to render at all.
        Everything except the subsidy mode is held equal across the perspectives, so any difference
        in the rendered cards is a difference the mode caused.
        """
        from hisim.economics.perspectives import InstallationContext, Perspective
        from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    SubsidyBuildingContext,
    SubsidyCatalog,
    SubsidyContext,
)

        parameters = EconomicParameters(country="DE", price_basis_year=2026)
        evaluator = EconomicEvaluator(database, parameters, SubsidyCatalog.load("DE"))
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "HeatPump",
                    ComponentCostFacts(
                        asset_class=ComponentType.HEAT_PUMP,
                        size=10.0,
                        size_unit=Units.KILOWATT,
                        investment_cost_override_in_euro=UncertainValue.exact(16000.0),
                        lifetime_override_in_years=18.0,
                        override_source="test",
                    ),
                )
            ],
            subsidy_context=SubsidyContext(
                applicant=ApplicantProfile(
                    actor=ApplicantActor.OWNER_OCCUPIER,
                    taxable_household_income_in_euro=35000.0,
                    main_residence=True,
                ),
                building=SubsidyBuildingContext(
                    construction_year=1985,
                    dwelling_units=1,
                    residential_floor_area_in_m2=150.0,
                    commercial_floor_area_in_m2=0.0,
                ),
            ),
            annual_heat_demand_in_kwh=15000.0,
            living_area_in_m2=150.0,
        )
        matrix = EvaluationMatrix()
        for perspective_id, mode in modes.items():
            matrix.results[perspective_id] = evaluator.evaluate(
                inputs,
                Perspective(
                    id=perspective_id,
                    installation_context=InstallationContext.GREENFIELD,
                    subsidy_mode=mode,
                ),
            )
        return inputs, parameters, matrix

    def _rendered(self, database, modes):
        """Both documents rendered from the same matrix, plus the matrix itself."""
        from hisim.economics.audit import build_input_audit

        inputs, parameters, matrix = self._matrix(database, modes)
        audit = build_input_audit(
            inputs, database, parameters, next(iter(matrix.results.values()))
        )
        plausibility = run_plausibility_checks(matrix)
        return (
            matrix,
            build_lifecycle_report_html(matrix, plausibility, audit),
            build_cost_summary_markdown(matrix, plausibility),
        )

    def test_two_subsidy_modes_produce_two_visible_decisions(self, database):
        """Excluding one scheme changes what applies — and the report has to say so.

        De-duplicating on the measure name showed the first perspective's decision and dropped the
        other without a trace, so a reader of the awards table could not tell that the second
        perspective got a different set of schemes for the same heat pump.
        """
        from hisim.economics.perspectives import SubsidyMode

        matrix, html, markdown = self._rendered(
            database,
            {
                "net_full": SubsidyMode.full(),
                "net_without_income": SubsidyMode.exclude(("DE_BEG_EM_HP_INCOME_2024",)),
            },
        )
        applied_per_perspective = {
            perspective_id: {award.scheme_id for decision in result.subsidy_decisions
                             for award in decision.applied}
            for perspective_id, result in matrix.results.items()
        }
        # Precondition: the two perspectives really did decide differently.
        assert applied_per_perspective["net_full"] != applied_per_perspective["net_without_income"]

        assert "(net_full)" in html and "(net_without_income)" in html
        assert "**HeatPump** (net_full)" in markdown
        assert "**HeatPump** (net_without_income)" in markdown
        for scheme_ids in applied_per_perspective.values():
            for scheme_id in scheme_ids:
                assert scheme_id in html  # every award of every perspective reaches the report

    def test_perspectives_that_agree_share_one_card(self, database):
        """Agreement collapses to a single annotated card — the point of grouping by content."""
        from hisim.economics.perspectives import SubsidyMode

        _matrix, html, markdown = self._rendered(
            database, {"net_a": SubsidyMode.full(), "net_b": SubsidyMode.full()}
        )
        assert "(all perspectives)" in html
        assert "**HeatPump** (all perspectives): applied" in markdown
        assert markdown.count("**HeatPump** (") == 1


class TestAwardsOfEveryPayoutKindAreReported:
    """PR-9 finding: an applied award with no upfront amount vanished from the subsidy section.

    Observed on the German retrofit run: the §35c tax credit awarded to the heat distribution
    system (2,060 EUR average, paid over three years) was in `lifecycle_costs.json` and in the
    SUBSIDY category NPV, but `cost_summary.md` said "applied none" for that subject and the HTML
    decision card printed "0.00 EUR" — both read `SubsidyAward.upfront_amount`, which is zero for
    three of the five payout kinds. These tests pin all five.
    """

    @staticmethod
    def _matrix_with_awards(database):
        """One evaluated perspective whose decision carries an award of every payout kind.

        The awards are attached to the result rather than solved for, because no shipped catalog
        offers all five kinds for one measure and the renderers are what is under test here: they
        must describe an award by its payout kind, whatever produced it.
        """
        from hisim.economics.subsidies import PayoutKind, SubsidyAward, SubsidyDecision

        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        matrix = EvaluationMatrix()
        perspective = next(
            perspective for perspective in select_applicable(load_default_bundle(), has_register=False)
            if perspective.id == "greenfield_net"
        )
        result = evaluator.evaluate(make_inputs(), perspective)
        result.subsidy_decisions = [
            SubsidyDecision(
                measure_subject="HeatPump",
                applied=[
                    SubsidyAward(
                        scheme_id="GRANT_SCHEME",
                        payout_kind=PayoutKind.UPFRONT_GRANT,
                        upfront_amount=UncertainValue(3000.0, 2000.0, 4000.0),
                    ),
                    SubsidyAward(
                        scheme_id="TAX_CREDIT_SCHEME",
                        payout_kind=PayoutKind.TAX_CREDIT_SCHEDULE,
                        schedule_amounts=[UncertainValue.exact(721.14)] * 2 + [UncertainValue.exact(618.12)],
                    ),
                    SubsidyAward(
                        scheme_id="LOAN_SCHEME",
                        payout_kind=PayoutKind.LOAN_TERMS,
                        loan_interest_rate=0.009,
                        loan_term_in_years=20,
                        loan_repayment_grant_share=0.25,
                    ),
                    SubsidyAward(
                        scheme_id="OPERATIONAL_SCHEME",
                        payout_kind=PayoutKind.OPERATIONAL,
                        operational_rate_per_kwh=0.08,
                        operational_carrier=EnergyCarrier.ELECTRICITY,
                        operational_duration_years=10,
                    ),
                    SubsidyAward(
                        scheme_id="VAT_SCHEME",
                        payout_kind=PayoutKind.VAT_REDUCTION,
                        reduced_vat_rate=0.07,
                    ),
                ],
            )
        ]
        matrix.results[perspective.id] = result
        return matrix

    def _rendered(self, database):
        """The two documents rendered from that matrix."""
        matrix = self._matrix_with_awards(database)
        plausibility = run_plausibility_checks(matrix)
        return (
            build_lifecycle_report_html(matrix, plausibility, None),
            build_cost_summary_markdown(matrix, plausibility),
        )

    def test_the_markdown_lists_every_applied_award(self, database):
        """No applied award is dropped, and a scheduled payout shows the sum of its instalments."""
        _html, markdown = self._rendered(database)
        assert "applied none" not in markdown
        for scheme_id in ("GRANT_SCHEME", "TAX_CREDIT_SCHEME", "LOAN_SCHEME", "OPERATIONAL_SCHEME",
                          "VAT_SCHEME"):
            assert scheme_id in markdown, scheme_id
        assert "TAX_CREDIT_SCHEME (2,060 EUR, tax credit paid over 3 years)" in markdown
        assert "GRANT_SCHEME (3,000 [2,000 | 4,000] EUR)" in markdown

    def test_the_html_card_shows_totals_and_terms_instead_of_zeros(self, database):
        """The decision card of the HTML report says the same thing as the markdown summary."""
        html, _markdown = self._rendered(database)
        # Q20 wraps the scheme in a span carrying the raw id as its tooltip; these awards declare
        # no display name, so the visible text is still the id.
        assert (
            '<span title="TAX_CREDIT_SCHEME">TAX_CREDIT_SCHEME</span>: '
            "2,060 EUR, tax credit paid over 3 years"
        ) in html
        section = html.split('<section id="building-subsidies">')[1].split("</section>")[0]
        assert "0.00 EUR" not in section
        assert "0.90% interest, 20 years term, 25% repayment grant" in html
        assert "0.0800 EUR/kWh on ELECTRICITY for 10 years" in html
        assert "reduced VAT rate 7.0%" in html

    def test_the_kpi_export_carries_the_scheduled_award(self, database):
        """`lifecycle_kpis.json` publishes a tax credit at its total, and no euro KPI without one.

        The KPI set filtered on a non-zero upfront amount too, so the same award was missing from
        the machine-readable side; an award that has no euro amount at all (loan terms, an
        operational rate) still gets none, because inventing one would be worse than omitting it.

        The key carries the friendly name **and** the catalog id. The name alone is neither unique
        — two schemes may share a display name, and a run's KPI namespace is flat, so one would
        overwrite the other — nor greppable back to the catalog. These fixture awards declare no
        display name, so their label falls back to the id and the key repeats it; that is the
        honest rendering of "this scheme has no name", not a special case worth suppressing.
        """
        from hisim.economics.exports import build_lifecycle_kpi_entries

        entries = {entry.name: entry for entry in build_lifecycle_kpi_entries(self._matrix_with_awards(database))}
        credit = "Subsidy TAX_CREDIT_SCHEME (TAX_CREDIT_SCHEME) [EUR] (greenfield_net)"
        assert credit in entries
        assert entries[credit].value == pytest.approx(2060.40)
        assert "Subsidy GRANT_SCHEME (GRANT_SCHEME) [EUR] (greenfield_net)" in entries
        assert not [name for name in entries if name.startswith("Subsidy LOAN_SCHEME")]
        assert not [name for name in entries if name.startswith("Subsidy OPERATIONAL_SCHEME")]

    def test_the_kpi_key_of_a_named_scheme_carries_both_the_name_and_the_id(self, database):
        """A display name does not displace the catalog key it belongs to.

        The awards above have no name, so the key would look the same under the old naming; this
        is the case the change is actually about — the shipped catalogs all declare display names,
        and their KPI keys used to identify a scheme by a prose label alone.
        """
        from hisim.economics.exports import build_lifecycle_kpi_entries

        matrix = self._matrix_with_awards(database)
        award = matrix.results["greenfield_net"].subsidy_decisions[0].applied[0]
        award.display_name = "BEG EM heat pump — base grant (30 %)"
        entries = {entry.name: entry for entry in build_lifecycle_kpi_entries(matrix)}
        expected = (
            "Subsidy BEG EM heat pump — base grant (30 %) (GRANT_SCHEME) [EUR] (greenfield_net)"
        )
        assert expected in entries
        assert (entries[expected].description or "").startswith("HeatPump; scheme GRANT_SCHEME")


class TestPngsAndCli:
    """Matplotlib companions and the `report` CLI."""

    def test_pngs_are_written(self, matrix, tmp_path):
        """Every perspective gets its own charts, and the matrix-wide one is written once.

        The names are listed rather than counted: the set grew with the visualization extension,
        and a bare count would be satisfied by any handful of files. What the list pins is which
        charts this run can draw at all — every perspective here is single-actor and
        own-capital, so the actor Sankey and the funding statement skip themselves throughout,
        and with no reference variant the three comparison charts do too — and that the
        per-perspective files carry the perspective they are about. Drawing only the first
        perspective was the defect: the file called `lifecycle_cost_treemap.png` was the
        treemap of *one* of the six perspectives the report beside it prints, and nothing in the
        name said which.
        """
        written = write_report_plots(matrix, str(tmp_path))

        per_perspective = {
            f"lifecycle_{chart}_{perspective_id}.png"
            for perspective_id in matrix.results
            for chart in ("annual_cash_flows", "investment_waterfall", "component_costs",
                          "cost_treemap", "monthly_burden", "swimlane", "liquidity_fan")
        }
        # Two charts of the set are drawn only where they have something to say: the operating
        # perspective books no year-0 investment, so it has no build-up to split, and only the
        # subsidised perspective has a funding statement worth a Sankey.
        per_perspective -= {"lifecycle_investment_waterfall_operating.png"}
        assert {os.path.basename(path) for path in written.paths} == (
            per_perspective
            | {"lifecycle_perspective_costs.png", "lifecycle_sources_and_uses_greenfield_net.png"}
        )
        for path in written.paths:
            assert os.path.getsize(path) > 5000
        assert {skip.chart for skip in written.skipped} >= {
            "actor-flow Sankey", "sources-and-uses Sankey", "payback curve"
        }

    def test_report_cli_with_compare(self, tmp_path):
        """`python -m hisim.economics report <dir> --compare <ref>` writes everything."""
        import json

        from hisim.economics.__main__ import main
        from hisim.economics.serialization import write_inputs

        variant_dir = tmp_path / "variant"
        reference_dir = tmp_path / "reference"
        variant_dir.mkdir()
        reference_dir.mkdir()
        write_inputs(make_inputs(), str(variant_dir))
        write_inputs(make_inputs(energy_kwh=15000.0, investment=2000.0), str(reference_dir))
        # Neither directory carries a stored evaluation, so the assumptions have to be stated:
        # the engine defaults are not a fallback any more.
        parameters_path = tmp_path / "parameters.json"
        with open(parameters_path, "w", encoding="utf-8") as file:
            json.dump(EconomicParameters().to_dict(), file)
        assert main([
            "report", str(variant_dir), "--compare", str(reference_dir),
            "--parameters", str(parameters_path),
        ]) == 0
        for file_name in (
            "cost_summary.md",
            "lifecycle_report.html",
            "lifecycle_annual_cash_flows_greenfield_net.png",
            "lifecycle_perspective_costs.png",
            "lifecycle_payback_curve_greenfield_net.png",
        ):
            assert (variant_dir / file_name).is_file(), file_name
        summary = (variant_dir / "cost_summary.md").read_text(encoding="utf-8")
        assert "## Variant comparison" in summary
        # The CLI is the only caller that passes the comparison's *reference result*, which is
        # what the NPV bridge decomposes; without it the section is skipped, and nothing else
        # here would notice.
        report = (variant_dir / "lifecycle_report.html").read_text(encoding="utf-8")
        assert 'id="vs-reference-npv-bridge"' in report
