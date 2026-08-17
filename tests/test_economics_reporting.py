"""Tests for the human-readable lifecycle reports (LIFECYCLE_COST_REPORT).

**Surface.** The presentation side of seam 4 (roadmap/cost-spec-v2.md §2.4): the plausibility
panel, `cost_summary.md`, the self-contained `lifecycle_report.html` with its inline-SVG charts,
the matplotlib PNG companions and the `report` CLI. Also, at the boundary, the two things
presentation is allowed to consume but not to compute — the typed plausibility findings
(`plausibility.py`, W4.2) and the typed input-audit rows (`input_audit.py`, W4.6).

**How it covers it.** Structural, not pixel-exact: this file asserts that a section renders,
that a chart carries the marks it should (a `<polygon>` band, a `class="credit"` segment, one
`<circle>` per subject), and that a figure appears where a reader expects it. The one exception
is `PANEL_BEFORE_W42`, a literal capture of the eleven rendered plausibility rows from *before*
the checks moved engine-side — a parity oracle for that move. Byte-exact whole-document goldens
deliberately live elsewhere, in `tests/test_economics_report_goldens.py`; splitting them keeps
this file readable when a section legitimately changes shape. Fixtures are module-scoped and
evaluated once through the default perspective bundle at the 2026 price basis, so the bands the
charts need actually exist.

**Error class.** A failure here is a *presentation* failure — a report can mislead a reader but
cannot corrupt a stored number (§2.4). Three sub-classes are worth telling apart when reading a
red run: a missing section marker means a rendering path broke; a `PANEL_BEFORE_W42` mismatch
means the plausibility *semantics* moved, not just their layout; and a divergence between the CSV
and the HTML rendering of the input audit (`TestInputAudit`) means the two renderings of one
resolution have drifted apart again — the exact defect that made the audit table a typed object.
"""

# clean

import os

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import load_default_bundle, select_applicable
from hisim.economics.plausibility import PlausibilityConfig, run_plausibility_checks
from hisim.economics.report_plots import write_report_plots
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


#: The panel exactly as `reporting.run_plausibility_checks` rendered it before the checks moved
#: engine-side (W4.2), captured on this module's fixture matrix: (name, status, value, expected,
#: detail). Pinning the *rendered* rows is what makes the move verifiable — the engine may now
#: produce typed findings, but the report a reader sees must be character-identical.
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
    ("system cost per unit of heat", "PASS", "0.240 EUR/kWh", "0.05 - 0.5 EUR/kWh", ""),
    ("maintenance / investment NPV ratio (greenfield_gross)", "PASS", "0.145 ", "0.02 - 0.8 ",
     "a huge ratio usually means an absolute fee stored as a rate"),
    ("uncertainty band width max/min (greenfield_gross)", "PASS", "1.994 x", "1 - 3.5 x",
     "over 20 years; very wide bands can be a band typo in the data, or genuinely uncertain inputs "
     "(broad price bands, partial anyway shares) — check the uncertainty-drivers section for which "
     "subjects carry it"),
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
            "## Plausibility",
            "## Perspectives",
            "## Cost structure",
            "## Component breakdown",
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
        assert "## Comparison" in text
        assert "Discounted payback" in text
        assert "NPV delta" in text


class TestHeatCostKpiIsNamedForWhatItMeasures:
    """Q27 R1: the heat KPI reads "system cost per unit of heat" everywhere a reader sees it.

    The figure divides the *whole* perspective NPV by the heat delivered, which is not the
    literature's levelized cost of heat, and the F6 disclosure made that impossible to keep
    calling one. These pin the rename at each published surface — the KPI export, the two
    perspectives tables, the plausibility row and the derivation caption — and, jointly, that the
    old name is gone from rendered output. The internal field name is deliberately untouched and
    is therefore not asserted on here; see `results.HeatCostNaming`.
    """

    def test_the_kpi_export_publishes_the_new_name(self, matrix):
        """The name in `lifecycle_kpis.json` is the name in the report's KPI table: one list."""
        from hisim.economics.exports import build_lifecycle_kpi_entries

        names = [entry.name for entry in build_lifecycle_kpi_entries(matrix)]
        heat = [name for name in names if "heat" in name.lower()]
        assert heat, "the fixture declares an annual heat demand, so the KPI must exist"
        assert all(name.startswith("System cost per unit of heat [EUR/kWh]") for name in heat)
        assert not [name for name in names if "levelized" in name.lower()]

    def test_the_summary_and_the_panel_carry_the_new_wording(self, matrix):
        """The markdown perspectives column and the plausibility row both renamed."""
        text = build_cost_summary_markdown(matrix, run_plausibility_checks(matrix))
        assert "| System cost/kWh heat |" in text
        assert "| system cost per unit of heat |" in text
        assert "levelized" not in text.lower()

    def test_the_derivation_caption_leads_with_the_annual_division(self, database, matrix):
        """Q27 R4: EAC / annual heat first, the discounted-sum form as the stated equivalent."""
        import re

        text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        caption = re.search(
            r"<b>System cost per unit of heat, in full\.</b>(.*?)</p>", text, re.S
        )
        assert caption, "the KPI section must carry the F6 derivation caption"
        body = caption.group(1)
        assert body.index("EUR/a &divide;") < body.index("equivalently NPV")
        assert "&divide; discounted heat sum" in body
        assert "against the undiscounted NPV" not in body
        assert "No heating-only attribution is applied" in body

    def test_no_rendered_string_names_an_internal_tracker_item(self, database, matrix):
        """Q27 R5: report-visible prose may cite the data, never the issue list."""
        checks = run_plausibility_checks(matrix)
        html = build_lifecycle_report_html(matrix, checks, _audit(database, make_inputs(), matrix))
        markdown = build_cost_summary_markdown(matrix, checks)
        for text in (html, markdown):
            assert "(issues #" not in text
            assert "issues #" not in text

    def test_the_band_width_note_names_the_innocent_causes_too(self, matrix):
        """Q27 R3: a wide band may be a typo or an honestly uncertain input; the note says both."""
        from hisim.economics.plausibility import CheckIds
        from hisim.economics.reporting import _finding_detail

        finding = next(
            check for check in run_plausibility_checks(matrix).findings
            if check.check_id == CheckIds.CHECK_BAND_WIDTH
        )
        note = _finding_detail(finding)
        assert "can be a band typo" in note
        assert "genuinely uncertain inputs" in note
        assert "uncertainty-drivers" in note


class TestHtmlReport:
    """The self-contained HTML report (A)."""

    def test_report_contains_chain_sections_and_charts(self, database, matrix):
        """All chain sections render, with inline SVGs and no external resources."""
        checks = run_plausibility_checks(matrix)
        text = build_lifecycle_report_html(matrix, checks, _audit(database, make_inputs(), matrix))
        for marker in (
            ">Plausibility",
            ">Input audit",
            "sources used",  # §3.10 registry table
            ">Investment build-up",
            "investment table",
            ">Cash-flow timeline",
            "NPV by cost category",  # §3.7 result table
            ">Energy bill",
            ">CO2",  # §3.8
            ">Perspectives",
            ">Component breakdown",
            "subject table",
            ">KPIs",  # §7.3
        ):
            assert marker in text, marker
        assert text.count("<svg") >= 5
        assert "https://" not in text.split("sources used")[0]  # charts stay self-contained
        assert "prefers-color-scheme: dark" in text  # theme-aware

    def test_report_with_comparison_section(self, database, matrix):
        """The comparison section renders the delta waterfall and the payback curve."""
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        perspective = select_applicable(load_default_bundle(), has_register=False)[0]
        reference = evaluator.evaluate(make_inputs(energy_kwh=15000.0, investment=2000.0), perspective)
        comparison = compare(reference, matrix.results[perspective.id], "base", "measures")
        checks = run_plausibility_checks(matrix)
        text = build_lifecycle_report_html(matrix, checks, _audit(database, make_inputs(), matrix), comparison)
        assert ">Comparison" in text
        assert "Discounted payback" in text


class TestSectionExplanations:
    """Every section explains itself the same way, in the same four parts (rule 2.6).

    The report is read by people who did not build it and who arrive in the middle of it by
    following a contents link, so "explained somewhere" is not good enough: each section carries
    what it *shows*, what it *adds*, the terms it uses and how its numbers are calculated, in
    that order, directly under its heading. These tests pin the structure rather than the wording
    — the wording is owner-authored prose held in `ReportProse` and byte-compared by the golden
    oracle — because a section that renders three of the four parts is the failure mode a
    reviewer reading one section at a time would never notice.
    """

    #: `<p>` `<p>` `<details>` `<details>` in that order, immediately after a section's heading.
    #: Since the Q24 chapter restructure the heading is an `<h3>` under the chapter's `<h2>` and
    #: carries the chapter as a trailing `<span>`.
    FOUR_PART_OPENING = (
        r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
        r"<p class='sub'>.+?</p>"
        r"<p class='sub'>.+?</p>"
        r"<details><summary>Terms used here</summary><dl><dt>.+?</dl></details>"
        r"<details><summary>How this is calculated</summary><p class='sub'>.+?</details>"
    )

    #: The single paragraph a *repeated* section renders instead: the same section name in a
    #: second chapter links back to the chapter that carries the full explanation (Q24), so the
    #: report does not print the same page of prose three times.
    CROSS_REFERENCE_OPENING = (
        r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
        r"<p class='sub'>The same chart, read the same way: see the explanation under "
        r"<a href=\"#[^\"]+\">[^<]+</a>\.</p>"
    )

    def _rendered_sections(self, text):
        """Every anchored section of a rendered report as `(anchor, html)`, in page order."""
        import re

        return [
            (match.group(1), match.group(0))
            for match in re.finditer(r"<section id=\"([^\"]+)\">.*?</section>", text, flags=re.S)
        ]

    def _report(self, database, matrix):
        """The richest report this module's fixtures reach: comparison, bridge and benchmark."""
        from hisim.economics.results import compare

        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        perspective = select_applicable(load_default_bundle(), has_register=False)[0]
        reference = evaluator.evaluate(make_inputs(energy_kwh=15000.0, investment=2000.0), perspective)
        comparison = compare(reference, matrix.results[perspective.id], "base", "measures")
        return build_lifecycle_report_html(
            matrix,
            run_plausibility_checks(matrix),
            _audit(database, make_inputs(), matrix),
            comparison,
            reference_result=reference,
        )

    def test_every_rendered_section_opens_with_the_four_parts(self, database, matrix):
        """No section may render its chart without the block that explains it — or a link to it.

        With the chapters (Q24) a section name can appear more than once, and only the first
        occurrence carries the four parts; the later ones open with the one-line cross-reference
        instead. Both are checked here, and every section must match one of the two: a section
        that opened with neither would be a chart with no explanation anywhere.
        """
        import re

        report = self._report(database, matrix)
        sections = self._rendered_sections(report)
        assert len(sections) > 20, "the fixture stopped reaching most sections"
        explained = set()
        for anchor, html in sections:
            if re.match(self.FOUR_PART_OPENING, html, flags=re.S):
                explained.add(anchor)
                continue
            assert re.match(self.CROSS_REFERENCE_OPENING, html, flags=re.S), anchor
        assert len(explained) > 20, "the four-part blocks disappeared entirely"

    def test_a_repeated_section_links_back_instead_of_repeating_the_prose(self, database, matrix):
        """Q24: the second occurrence of a section name points at the first, by anchor.

        The failure mode the back-link exists to prevent is a report three times as long as it
        needs to be — the explanation is the bulk of most sections — so this pins that a repeated
        section carries a link to an anchor that actually exists in the document.
        """
        import re

        report = self._report(database, matrix)
        for anchor, html in self._rendered_sections(report):
            match = re.match(self.CROSS_REFERENCE_OPENING, html, flags=re.S)
            if match is None:
                continue
            target = re.search(r"see the explanation under <a href=\"#([^\"]+)\"", html).group(1)
            assert f'id="{target}"' in report, (anchor, target)
            assert target != anchor

    def test_the_comparison_sections_are_explained_too(self, database, matrix):
        """The three sections that exist only in a comparison run take the same path.

        They live in the fourth block since Q24, so their anchors carry its chapter prefix.
        """
        report = self._report(database, matrix)
        anchors = [anchor for anchor, _html in self._rendered_sections(report)]
        for anchor in ("vs-reference-comparison", "vs-reference-npv-bridge",
                       "vs-reference-bank-benchmark"):
            assert anchor in anchors, anchor

    def test_the_primer_renders_once_at_the_top(self, database, matrix):
        """The conventions every other section leans on are stated first, and only once."""
        report = self._report(database, matrix)
        anchors = [anchor for anchor, _html in self._rendered_sections(report)]
        assert anchors[0] == "building-how-to-read"
        assert anchors.count("building-how-to-read") == 1
        assert report.index(">How to read this report") > report.index("<nav")

    def test_the_chapters_carry_their_authored_intros_in_order(self, database, matrix):
        """Q24: every rendered chapter opens with its own lead-in, verbatim from `ReportProse`."""
        from hisim.economics.report_prose import ReportProse
        from hisim.economics.reporting import ReportChapters

        report = self._report(database, matrix)
        positions = []
        for anchor, name in ReportChapters.ORDER:
            heading = f"<h2 class='chapter' id=\"{anchor}\">"
            if heading not in report:
                continue
            positions.append(report.index(heading))
            if (anchor, name) in ReportChapters.WITHOUT_INTRO:
                continue
            assert ReportProse.to_html(ReportProse.for_chapter(name)) in report, name
        assert positions == sorted(positions)
        assert len(positions) >= 2

    def test_section_anchors_carry_their_chapter_prefix(self, database, matrix):
        """The same section name in two chapters must not produce the same anchor twice."""
        from hisim.economics.reporting import ReportChapters

        report = self._report(database, matrix)
        anchors = [anchor for anchor, _html in self._rendered_sections(report)]
        assert len(anchors) == len(set(anchors)), "duplicate anchors"
        chapters = {anchor for anchor, _name in ReportChapters.ORDER}
        for anchor in anchors:
            assert anchor.split("-")[0] in {chapter.split("-")[0] for chapter in chapters}, anchor

    def test_the_rendered_prose_is_the_authored_prose(self, database, matrix):
        """The renderer marks the text up; it never edits it."""
        from hisim.economics.report_prose import ReportProse
        from hisim.economics.reporting import ReportSections

        report = self._report(database, matrix)
        by_anchor = dict(self._rendered_sections(report))
        for prefixed_anchor, name in [
            (f"{chapter}-{anchor}", name)
            for chapter in ("building", "owner", "rented", "society", "vs-reference")
            for anchor, name in ReportSections.ORDER
        ]:
            anchor = prefixed_anchor
            if anchor not in by_anchor or "see the explanation under" in by_anchor[anchor]:
                continue
            prose = ReportProse.for_section(name)
            assert f"<p class='sub'>{ReportProse.to_html(prose.shows)}</p>" in by_anchor[anchor]
            assert f"<p class='sub'>{ReportProse.to_html(prose.adds)}</p>" in by_anchor[anchor]
            for term, definition in prose.terms:
                assert f"<dt><em>{ReportProse.to_html(term)}</em></dt>" in by_anchor[anchor]
                assert f"<dd>{ReportProse.to_html(definition)}</dd>" in by_anchor[anchor]

    def test_every_section_of_the_order_has_authored_prose(self):
        """Including the sections this fixture cannot reach (scenarios, the audit heatmap)."""
        from hisim.economics.report_prose import ReportProse
        from hisim.economics.reporting import ReportSections

        for _anchor, name in ReportSections.ORDER:
            prose = ReportProse.for_section(name)
            assert prose.shows and prose.adds and prose.terms and prose.calculation
        assert ReportProse.for_section(ReportProse.LEDGER_HEATMAP_SECTION_NAME).shows

    def test_prose_markup_is_escaped_before_it_is_emphasized(self):
        """A definition mentioning a tag must not be able to open one."""
        from hisim.economics.report_prose import ReportProse

        assert ReportProse.to_html("a *b* and **c** and `d`") == (
            "a <em>b</em> and <strong>c</strong> and <code>d</code>"
        )
        assert ReportProse.to_html("<details> & <em>") == "&lt;details&gt; &amp; &lt;em&gt;"
        assert ReportProse.to_plain_text("a *b* and **c** and `d`") == "a b and c and d"

    def test_the_ledger_heatmap_caption_carries_its_authored_prose(self):
        """The one chart outside the report still gets its explanation, in its caption."""
        from hisim.economics.report_plots import _heatmap_caption_lines
        from hisim.economics.report_prose import ReportProse

        caption = " ".join(_heatmap_caption_lines(dropped=3))
        shows = ReportProse.to_plain_text(
            ReportProse.for_section(ReportProse.LEDGER_HEATMAP_SECTION_NAME).shows
        )
        assert shows in caption
        assert "3 categor(ies) carried no flows" in caption


class TestDegenerateBandBanner:
    """Missing whiskers must read as a data property, not a rendering bug."""

    def _exact_matrix(self, database) -> EvaluationMatrix:
        """An evaluation in which every input band is degenerate (min = avg = max).

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
                        asset_class=ComponentType.WINDOWS, size=28.0, size_unit=Units.SQUARE_METER
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
        assert ">Who pays what" in text
        assert ">Scenarios" in text
        assert "interest=high" in text
        # The cumulative NPV chart carries its uncertainty band (banded data -> polygon).
        assert "<polygon" in text

    def test_the_scenario_table_states_the_assumption_and_both_values(self, database):
        """Q26 F1: a scenario row names the changed parameter with its own and the central value."""
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube

        inputs = make_inputs()
        parameters = EconomicParameters(country="DE", price_basis_year=2026)
        evaluator = EconomicEvaluator(database, parameters)
        perspectives = [
            Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                        subsidy_mode=SubsidyMode.none()),
        ]
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(inputs, perspectives[0])
        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "ONE_AT_A_TIME",
                "axes": [
                    {"name": "interest", "field": "interest_rate", "levels": {"high": 0.05}},
                    {"name": "electricity", "field": "energy_price_escalation_rates.ELECTRICITY",
                     "levels": {"flat": 0.0}},
                ],
            }
        )
        cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database)
        text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix), scenario_cube=cube)
        assert "Assumption (scenario value, central value)" in text
        assert "interest_rate 5.00% (central 3.00%)" in text
        # The carrier rate is not configured on the parameters, so the central value is the one
        # the run resolved from the country defaults file — not a blank.
        assert "energy_price_escalation_rates.ELECTRICITY 0.00% (central 2.00%)" in text

    def test_a_zero_swing_scenario_row_states_why_the_axis_was_inert(self, database):
        """Q27 R2: a `+0` row footnotes its cause, read off the run's own timeline.

        The German examples carry a CO2-price axis, and an all-electric house books no carbon
        price at all because the electricity price entry declares no exposure to it — so the row
        moves nothing. Without the footnote that reads either as a broken cube or as the finding
        "carbon prices do not matter here", and it is neither.
        """
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.scenarios import ScenarioSet, evaluate_cube

        inputs = make_inputs()
        parameters = EconomicParameters(country="DE", price_basis_year=2026)
        evaluator = EconomicEvaluator(database, parameters)
        perspectives = [
            Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                        subsidy_mode=SubsidyMode.none()),
        ]
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(inputs, perspectives[0])
        scenario_set = ScenarioSet.from_json(
            {
                "base": "central",
                "mode": "ONE_AT_A_TIME",
                "axes": [
                    {"name": "co2", "field": "co2_price_scenario", "levels": {"high": "high"}},
                    {"name": "interest", "field": "interest_rate", "levels": {"high": 0.05}},
                ],
            }
        )
        cube = evaluate_cube(inputs, parameters, perspectives, scenario_set, database)
        text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix), scenario_cube=cube)
        assert "<b>co2=high</b> — swing is exactly zero: no CO2-price flow is booked in this run" in text
        assert "co2_price_exposure = 0" in text
        assert "electricity price entry declares" in text
        # The axis that *did* move carries no footnote; only inert rows get one.
        assert "<b>interest=high</b> — swing is exactly zero" not in text

    def test_an_undiagnosable_inert_axis_says_so_rather_than_guessing(self, database):
        """Q27 R2: the note is derived or it is honest; it is never invented."""
        from hisim.economics.views import ZeroSwingCauses, zero_swing_notes

        class _Scenario:
            def __init__(self, identifier, overrides):
                self.id = identifier
                self.parameter_overrides = overrides
                self.data_overlays = {}

        class _Cube:
            base_id = "central"
            scenarios = [
                _Scenario("central", {}),
                _Scenario("horizon=long", {"observation_period_in_years": 40}),
            ]

        inputs = make_inputs()
        evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        result = evaluator.evaluate(
            inputs,
            Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                        subsidy_mode=SubsidyMode.none()),
        )
        notes = zero_swing_notes(_Cube(), result, {"central": 0.0, "horizon=long": 0.0})
        assert notes == {"horizon=long": ZeroSwingCauses.UNKNOWN}

    def test_the_assumptions_section_publishes_the_run_s_own_tariff_and_rates(self, database):
        """Q26 F2: the section carries the working price, the feed-in rate and the escalations."""
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode

        inputs = make_inputs()
        parameters = EconomicParameters(country="DE", price_basis_year=2026)
        evaluator = EconomicEvaluator(database, parameters)
        matrix = EvaluationMatrix()
        matrix.results["gross"] = evaluator.evaluate(
            inputs,
            Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                        subsidy_mode=SubsidyMode.none()),
        )
        text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        assert 'id="building-assumptions"' in text
        assert ">Assumptions" in text
        assert "annuity factor" in text and "(computed)" in text
        assert "ELECTRICITY: working price" in text
        assert "ELECTRICITY: feed-in rate (FIXED_TARIFF)" in text
        assert "0.0750 EUR/kWh" in text
        # Sources, not blanks: the country defaults file is cited by id where it won.
        assert "src_expert_engine_defaults" in text

    def test_the_tenant_levy_mirrors_the_landlord_levy_income_in_the_report(self, database):
        """Q26 F4: the two halves of the booked transfer pair reach the page as one figure."""
        import re

        from hisim.economics.facts import ExistingAsset, ExistingAssetRegister
        from hisim.economics.perspectives import (
            ActorScope,
            InstallationContext,
            Perspective,
            SubsidyMode,
        )
        from hisim.economics.timeline import CostCategory
        from hisim.economics.views import StatementPartitions, landlord_statement, perspective_statement

        inputs = make_inputs()
        inputs.existing_assets = ExistingAssetRegister(
            assets=[
                ExistingAsset(
                    asset_class=ComponentType.GAS_HEATER,
                    size=15.0,
                    size_unit=Units.KILOWATT,
                    installation_year=2011,
                    replaced_by_asset_classes=[ComponentType.HEAT_PUMP],
                )
            ]
        )
        inputs.living_area_in_m2 = 150.0
        inputs.current_cold_rent_in_euro_per_m2_month = 8.5
        parameters = EconomicParameters(country="DE", price_basis_year=2026)
        evaluator = EconomicEvaluator(database, parameters)
        matrix = EvaluationMatrix()
        for scope in (ActorScope.LANDLORD, ActorScope.TENANT):
            perspective = Perspective(
                id=scope.value,
                installation_context=InstallationContext.BROWNFIELD,
                actor_scope=scope,
                subsidy_mode=SubsidyMode.full(),
            )
            matrix.results[perspective.id] = evaluator.evaluate(inputs, perspective)
        landlord = landlord_statement(matrix.results[ActorScope.LANDLORD.value])
        tenant = perspective_statement(
            matrix.results[ActorScope.TENANT.value], StatementPartitions.TENANT
        )
        landlord_levy = next(
            line.npv_in_euro for line in landlord.cash_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        )
        tenant_levy = next(
            line.npv_in_euro for line in tenant.cash_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        )
        assert tenant_levy == pytest.approx(-landlord_levy, abs=0.005)
        text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        assert 'id="rented-tenant-statement"' in text
        assert "the levy is the exact counterpart of the landlord statement" in text
        # The per-world verdicts of the levy (Q26 F5) are stated beside the amount.
        assert re.search(r"Binding mechanism (in all three worlds|per world)", text)

    def test_timeline_detail_table_attributes_every_flow(self, database):
        """Section 3's detail table lists (year, subject, category) incl. anyway credits."""
        from hisim.economics.facts import ExistingAsset, ExistingAssetRegister
        from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
        from hisim.economics.reporting import _timeline_detail_table

        old_windows = ExistingAsset(
            asset_class=ComponentType.WINDOWS,
            size=25.0,
            size_unit=Units.SQUARE_METER,
            installation_year=2026 - 33,  # 2 a of 35 remaining -> credit at year 2
            replaced_by_asset_classes=[ComponentType.WINDOWS],
        )
        inputs = EvaluationInputs(
            simulation_year=2026,
            simulated_period_fraction=1.0,
            cost_facts=[
                SubjectCostFacts(
                    "Envelope.Windows",
                    ComponentCostFacts(asset_class=ComponentType.WINDOWS, size=25.0, size_unit=Units.SQUARE_METER),
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
        assert row.unit_price_in_euro.average == pytest.approx(16000.0)

        csv_text = open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8").read()
        assert "config override (test)" in csv_text
        html_text = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix), audit)
        assert "override (test)" in html_text
        # Same source registry in both directions: the audit resolved it, nobody re-derived it.
        assert audit.sources
        assert f"sources used ({len(audit.sources)} registry entries" in html_text
        assert build_input_audit(inputs, database, EconomicParameters(country="DE", price_basis_year=2026)).rows

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
        assert row.unit_price_in_euro is not None and row.unit_price_in_euro.average == 4321.0
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
        lines = open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8").read().splitlines()
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

        lines = open(write_cost_audit(audit, str(tmp_path)), encoding="utf-8").read().splitlines()
        header = lines[0].split(";")
        cells = lines[1].split(";")
        assert cells[header.index("Investment origin")] == "unresolved - not priced"
        assert cells[header.index("Price basis")] == ""  # no price, so no basis to state
        assert cells[header.index("Unit price avg")] == ""


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
        from hisim.economics.results import EvaluationMatrix
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
        from hisim.economics.carriers import EnergyCarrier as Carrier
        from hisim.economics.results import EvaluationMatrix
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
                        operational_carrier=Carrier.ELECTRICITY,
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
        assert "0.00 EUR" not in html.split('id="building-subsidies"')[1].split("</section>")[0]
        assert "0.90% interest, 20 years term, 25% repayment grant" in html
        assert "0.0800 EUR/kWh on ELECTRICITY for 10 years" in html
        assert "reduced VAT rate 7.0%" in html

    def test_the_kpi_export_carries_the_scheduled_award(self, database):
        """`lifecycle_kpis.json` publishes a tax credit at its total, and no euro KPI without one.

        The KPI set filtered on a non-zero upfront amount too, so the same award was missing from
        the machine-readable side; an award that has no euro amount at all (loan terms, an
        operational rate) still gets none, because inventing one would be worse than omitting it.
        """
        from hisim.economics.exports import build_lifecycle_kpi_entries

        entries = {entry.name: entry for entry in build_lifecycle_kpi_entries(self._matrix_with_awards(database))}
        assert "Subsidy TAX_CREDIT_SCHEME [EUR] (greenfield_net)" in entries
        assert entries["Subsidy TAX_CREDIT_SCHEME [EUR] (greenfield_net)"].value == pytest.approx(2060.40)
        assert "Subsidy GRANT_SCHEME [EUR] (greenfield_net)" in entries
        assert "Subsidy LOAN_SCHEME [EUR] (greenfield_net)" not in entries
        assert "Subsidy OPERATIONAL_SCHEME [EUR] (greenfield_net)" not in entries


class TestPngsAndCli:
    """Matplotlib companions and the `report` CLI."""

    def test_pngs_are_written(self, matrix, tmp_path):
        """The PNG set exists and is non-empty.

        The expected file names are listed rather than counted: the set grew with the
        visualization extension, and a bare count would have been satisfied by any eight files.
        This fixture is a single-perspective, unfinanced, own-capital run without a reference, so
        the four charts that need actors, financing or a comparison skip themselves — which is
        the behaviour the assertion below pins.
        """
        written = write_report_plots(matrix, str(tmp_path))
        assert {os.path.basename(path) for path in written} == {
            "lifecycle_annual_cash_flows.png",
            "lifecycle_investment_waterfall.png",
            "lifecycle_perspective_costs.png",
            "lifecycle_component_costs.png",
            "lifecycle_swimlane.png",
            "lifecycle_liquidity_fan.png",
            "lifecycle_cost_treemap.png",
            "lifecycle_monthly_burden.png",
        }
        for path in written:
            assert os.path.getsize(path) > 5000

    def test_comparison_pngs_are_written_when_a_reference_is_given(self, matrix, tmp_path):
        """The bridge and the fixed-interest benchmark need a baseline and appear only with one."""
        reference = next(iter(matrix.results.values()))
        written = {os.path.basename(path) for path in write_report_plots(matrix, str(tmp_path), reference)}
        assert "lifecycle_comparison_bridge.png" in written
        assert "lifecycle_wealth_benchmark.png" in written
        assert "lifecycle_payback_curve.png" in written

    @staticmethod
    def _drawn(monkeypatch, draw):
        """Runs a plot function and hands back the figure it was about to close.

        The plot functions own their figure end to end — they create it, draw it, save it and
        close it — which is right for a writer but leaves nothing to assert on beyond a file size.
        Intercepting `plt.close` keeps the figure alive for exactly one test, so a *layout* rule
        (a label that must clear a marker, a legend that must sit outside the axes) can be checked
        as geometry instead of by looking at a PNG.
        """
        from hisim.economics import report_plots

        captured = []
        monkeypatch.setattr(report_plots.plt, "close", captured.append)
        draw(report_plots)
        assert captured, "the plot function did not produce a figure"
        return captured[0]

    def test_component_cost_labels_clear_the_bars_and_the_whiskers(self, matrix, tmp_path, monkeypatch):
        """Every net-NPV label starts past everything drawn in its row (PR-9 finding).

        The label used to be positioned from the end of the cost stack alone, so on every row
        whose net band reached beyond the bars — which is most of them once residual value and
        subsidies are in — the text was printed over the marker and its whisker cap.
        """
        result = next(iter(matrix.results.values()))
        figure = self._drawn(
            monkeypatch,
            lambda plots: plots.plot_component_costs(
                result, os.path.join(str(tmp_path), "component_costs.png")
            ),
        )
        axis = figure.axes[0]
        nets = [breakdown.total_npv_in_euro for breakdown in result.component_breakdowns.values()]
        labels = sorted(axis.texts, key=lambda text: text.get_position()[1])
        assert len(labels) == len(nets)
        for label, band in zip(labels, nets):
            assert label.get_position()[0] > band.maximum, label.get_text()

    def test_the_component_cost_legend_sits_outside_the_axes(self, matrix, tmp_path, monkeypatch):
        """The legend cannot cover a data row, because it is not drawn over the data at all.

        In the heat-pump-only run the in-axes legend landed squarely on the ElectricityMeter row.
        Checked in display coordinates after a draw, which is the only way to know that the two
        boxes really do not intersect.
        """
        result = next(iter(matrix.results.values()))
        figure = self._drawn(
            monkeypatch,
            lambda plots: plots.plot_component_costs(
                result, os.path.join(str(tmp_path), "component_costs.png")
            ),
        )
        figure.canvas.draw()
        axis = figure.axes[0]
        assert axis.get_legend().get_window_extent().y1 <= axis.get_window_extent().y0

    def test_the_payback_chart_names_the_basis_it_is_drawn_on(self, matrix, tmp_path, monkeypatch):
        """Title and y-label name the perspective, so V9's gross years cannot look contradictory.

        The swimlane's payback milestone is drawn for the matrix's first perspective and this
        chart for the perspective the compared directories share; the two differ legitimately, and
        a chart that does not say which basis it uses invites the reader to call it a bug.
        """
        result = next(iter(matrix.results.values()))
        figure = self._drawn(
            monkeypatch,
            lambda plots: plots.plot_payback_curve(
                result, result, os.path.join(str(tmp_path), "payback.png")
            ),
        )
        axis = figure.axes[0]
        assert f"{result.perspective_id} basis" in axis.get_title(loc="left")
        assert f"{result.perspective_id} basis" in axis.get_ylabel()

    def test_audit_plots_are_written_separately(self, matrix, tmp_path):
        """V6 lives with the audit outputs (owner decision Q9), not with the report set."""
        from hisim.economics.report_plots import write_audit_plots

        written = write_audit_plots(next(iter(matrix.results.values())), str(tmp_path))
        assert [os.path.basename(path) for path in written] == ["cost_audit_timeline_heatmap.png"]
        assert os.path.getsize(written[0]) > 5000

    def test_report_cli_with_compare(self, tmp_path):
        """`python -m hisim.economics report <dir> --compare <ref>` writes everything.

        Both directories hold nothing but `economic_inputs.json`, so the invocation has to state
        its assumptions: since the parameter-resolution fix the CLI reads them from the run's
        stored results and refuses to substitute the engine defaults for a directory that has
        none.
        """
        import json

        from hisim.economics.__main__ import main
        from hisim.economics.serialization import write_inputs

        variant_dir = tmp_path / "variant"
        reference_dir = tmp_path / "reference"
        variant_dir.mkdir()
        reference_dir.mkdir()
        write_inputs(make_inputs(), str(variant_dir))
        write_inputs(make_inputs(energy_kwh=15000.0, investment=2000.0), str(reference_dir))
        parameters_path = tmp_path / "parameters.json"
        with open(parameters_path, "w", encoding="utf-8") as file:
            json.dump(EconomicParameters(price_basis_year=2024).to_dict(), file)
        assert main([
            "report", str(variant_dir), "--compare", str(reference_dir),
            "--parameters", str(parameters_path),
        ]) == 0
        for file_name in (
            "cost_summary.md",
            "lifecycle_report.html",
            "lifecycle_annual_cash_flows.png",
            "lifecycle_perspective_costs.png",
            "lifecycle_payback_curve.png",
        ):
            assert (variant_dir / file_name).is_file(), file_name
        summary = (variant_dir / "cost_summary.md").read_text(encoding="utf-8")
        assert "## Comparison" in summary
