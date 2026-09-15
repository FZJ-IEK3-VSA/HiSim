"""Renderer tests for the second half of the visualization set (visualization spec §5, rule 2.7).

The companion of `tests/test_economics_sections_a.py`, in the same two halves. The first half
parses the two SVG builders this slice adds — the squarified treemap and the monthly-burden stack
— and checks the claims that are only true of the emitted file: that the tiles fill the box they
were given, that a tile too small for a label keeps its tooltip, that costs and credits stack on
separate baselines instead of netting inside a bar, and that the replacement reserve is a line
above the bars rather than a bar of its own.

The second half renders the sections built on those builders — the lifecycle overview, funding,
the energy balance, the cost structure and the cost shapes, the equity build-up, the monthly
burden and the bank benchmark — and checks what only a rendering of *these* fixtures can show:
that a section states the figures its own view computed, that the balance names the roles it
could not place, and that a perspective without the data skips its section instead of drawing an
empty one.

**Wording is not asserted here** — neither the authored prose of `report_prose.py` nor the
run-specific captions built beside the charts: `tests/test_economics_report_goldens.py`
byte-compares the whole document, and since the energy balance joined that fixture it covers all
eight of these sections, their presence, their four-part opening, their contents links and every
word of their captions included. The two loops that used to assert presence and contents here are
gone for that reason; what stays is the half a golden cannot express, which is what a *different*
fixture does. The single exception is marked where it stands: a note that *replaces* a chart has
no trace in the output other than the sentence it is made of.

**What a failure means.** A *presentation* failure: something a reader sees changed. It says
nothing about whether the numbers are right — `tests/test_economics_views_charts_b.py` owns that —
and a bug caught here can mislead a reader but can never corrupt a stored result.
"""

# clean

import copy
import dataclasses
import re

import pytest

from hisim.economics import views
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import EconomicEvaluator, EvaluationInputs, SubjectCostFacts
from hisim.economics.facts import BillingDeterminants, ComponentCostFacts
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext, Perspective, SubsidyMode
from hisim.economics.plausibility import run_plausibility_checks
from hisim.economics.presentation_style import PresentationStyle, SequentialRamp
from hisim.economics.reporting import ReportSections, build_lifecycle_report_html
from hisim.economics.reporting.charts import _ChartGeometry, _monthly_burden_svg, _treemap_svg
from hisim.economics.reporting.scaffold import ReportChapters, _ChapterContext
from hisim.economics.reporting.summary import _band_str, _fmt
from hisim.economics.reporting.sections_charts import (
    _energy_balance_section_html,
    _monthly_burden_section_html,
    _points,
    _treemap_section_html,
)
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, compare
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.views import CostDataError
from hisim.loadtypes import ComponentType, Units

from tests.economics_report_test_helpers import rects, rendered_sections

pytestmark = pytest.mark.base


# ------------------------------------------------------------------ parsing what was emitted


def _viewbox(svg: str):
    """The `(width, height)` an inline SVG declares — the box its marks were laid out in."""
    match = re.search(r'<svg viewBox="0 0 (\d+) (\d+)"', svg)
    assert match is not None, "the chart declared no viewBox at all"
    return float(match.group(1)), float(match.group(2))


def _dashed_segments(svg: str):
    """The monthly chart's reserve overlay as `(x1, y)` per segment, in emission order.

    Keyed by the left edge rather than collected as bare y values, because the claim the overlay
    has to satisfy is per *year* — each segment sits above the bar it supplements — and a list of
    heights with no x cannot say which bar that is.
    """
    return [
        (float(x1), float(y))
        for x1, y in re.findall(r'<line x1="([\d.]+)" y1="([\d.]+)" x2="[\d.]+" y2="\2"[^>]*'
                                r'stroke-dasharray="5 3"', svg)
    ]


def _chapter_of(anchor: str) -> str:
    """The chapter anchor a section's chapter-prefixed anchor belongs to.

    Matched against `ReportChapters.ORDER` rather than split on the first dash, because one
    chapter anchor carries a dash of its own (`vs-reference`) and splitting would file its
    sections under a chapter that does not exist.
    """
    for chapter, _name in ReportChapters.ORDER:
        if anchor.startswith(f"{chapter}-"):
            return chapter
    raise AssertionError(f"{anchor} carries no chapter prefix")


class TestTreemapSvg:
    """The area encoding of the cost-structure panels, asserted on the emitted rectangles."""

    #: Four tiles whose areas are 4:2:1:1, so the layout has something to be squarified about.
    TILES = [
        ("Investment - HeatPump", 40000.0, "var(--g0)"),
        ("Energy - ELECTRICITY", 20000.0, "var(--g1)"),
        ("Maintenance - HeatPump", 10000.0, "var(--g2)"),
        ("Replacements - HeatPump", 10000.0, "var(--g3)"),
    ]
    #: Any box that is not the default, so "the box it was given" means something.
    PANEL = (380, 240)

    def test_the_tiles_fill_the_box_they_were_given(self):
        """A treemap that does not tile its box is not an area encoding of anything."""
        width, height = self.PANEL
        svg = _treemap_svg(self.TILES, height=height, width=width)
        assert _viewbox(svg) == (width, height)
        rectangles = rects(svg)
        assert len(rectangles) == len(self.TILES)
        # Every tile is inset by one unit on each side, so the drawn area is short by the insets.
        drawn = sum(tile_w * tile_h for _x, _y, tile_w, tile_h in rectangles)
        box = width * height
        assert 0.85 * box < drawn < box

    def test_the_default_box_is_the_shared_chart_column(self):
        """A chart that sets its own width does not line up with the one above it."""
        assert _viewbox(_treemap_svg(self.TILES))[0] == _ChartGeometry.WIDTH

    def test_the_section_draws_both_panels_side_by_side(self, gross_result):
        """Two bases across one column, which is the section's layout call rather than the chart's."""
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        panels = re.findall(r"<svg viewBox=\"0 0 (\d+) \d+\"",
                            _treemap_section_html(gross_result, context))
        assert len(panels) == 2
        assert all(0 < float(width) <= _ChartGeometry.WIDTH / 2 for width in panels)

    def test_tile_areas_are_proportional_to_the_amounts(self):
        """The whole claim of the chart: twice the money is twice the area."""
        svg = _treemap_svg(self.TILES, height=240)
        areas = [tile_w * tile_h for _x, _y, tile_w, tile_h in rects(svg)]
        assert areas[0] == pytest.approx(2 * areas[1], rel=0.05)
        assert areas[1] == pytest.approx(2 * areas[2], rel=0.05)

    def test_a_tile_too_small_for_a_label_keeps_its_tooltip(self):
        """The inline-SVG panel's advantage over the PNG: a sliver still names itself on hover."""
        svg = _treemap_svg(
            [("Investment - HeatPump", 1000000.0, "var(--g0)"), ("Energy - sliver", 1.0, "var(--g1)")],
            width=self.PANEL[0],
        )
        assert "<title>Energy - sliver: 1.00 EUR</title>" in svg
        assert ">Energy - sliver<" not in svg  # no room for the printed label
        assert ">Investment - HeatPump<" in svg

    def test_a_panel_with_no_positive_area_draws_nothing(self):
        """A treemap has no negative area, so a fully credited basis has no panel at all."""
        assert _treemap_svg([]) == ""
        assert _treemap_svg([("clamped", 0.0, "var(--g0)")]) == ""


class TestMonthlyBurdenSvg:
    """The stacked monthly bars, on a hand-priced perspective rather than on the layout."""

    @staticmethod
    def _zero_line(svg: str) -> float:
        """The y of the chart's zero baseline, which both stacks are drawn from."""
        baseline = re.search(
            r'<line x1="[\d.]+" y1="([\d.]+)" x2="[\d.]+" y2="\1" stroke="var\(--baseline\)"', svg
        )
        assert baseline is not None, "the chart drew no zero baseline to stack against"
        return float(baseline.group(1))

    def test_costs_and_credits_stack_on_separate_baselines(self, financed_result, financed_burden):
        """A feed-in credit must not shorten the energy bar it is drawn beside."""
        svg = _monthly_burden_svg(financed_result, financed_burden)
        rectangles = rects(svg)
        assert rectangles, "the fixture books no recurring flow at all"
        zero_y = self._zero_line(svg)
        above = [rect for rect in rectangles if rect[1] + rect[3] <= zero_y + 0.001]
        below = [rect for rect in rectangles if rect[1] >= zero_y - 0.001]
        assert above and below, "the fixture books no credit, so nothing tests the second baseline"
        assert len(above) + len(below) == len(rectangles), "a bar crossed the zero line"

    def test_every_reserve_segment_is_priced_at_its_own_year_plus_the_reserve(
        self, financed_result, financed_burden
    ):
        """The dashed line is a number, not a decoration: it is that month plus the sinking fund.

        Priced from the emitted file alone. The zero line and the bars give the chart's scale —
        the monthly stack draws each segment at its full height, so one year's positive stack is
        exactly its amount in user units — and every dashed segment then has to land at
        `zero - (that year's total + the reserve) x scale`. A reserve drawn flat, drawn at the
        wrong year's total, or drawn on the bars' own baseline instead of above them all fail
        this, and none of them would fail a test that only looked at the highest dashed line.
        """
        svg = _monthly_burden_svg(financed_result, financed_burden)
        reserve = financed_burden.replacement_reserve_per_month
        assert reserve, "the fixture books no replacement, so there is no overlay to price"
        per_group = views.monthly_burden_by_group(financed_result, PresentationStyle.CATEGORY_TO_GROUP)
        zero_y = self._zero_line(svg)
        bar_w = (_ChartGeometry.WIDTH - 70 - 20) / len(financed_burden.series)
        scale = None
        for year, groups in enumerate(per_group):
            positive = sum(value for value in groups.values() if value > 0)
            if positive:
                x = 70 + year * bar_w
                stack = [
                    height for left, top, _w, height in rects(svg)
                    if abs(left - (x + 1)) < 0.05 and top + height <= zero_y + 0.001
                ]
                scale = sum(stack) / positive
                break
        assert scale is not None, "no year draws a bar, so the chart's scale cannot be recovered"
        segments = dict(_dashed_segments(svg))
        assert segments, "the reserve is non-zero but no segment was drawn"
        for year, band in enumerate(financed_burden.series):
            x1 = round(70 + year * bar_w + 1, 1)
            if x1 not in segments:
                continue
            expected = zero_y - (band.best_estimate + reserve) * scale
            assert segments[x1] == pytest.approx(expected, abs=0.15), year

    def test_the_reserve_line_sits_above_the_bars_it_supplements(
        self, financed_result, financed_burden
    ):
        """The reserve is what the month costs *once the sinking fund is paid*, so it is on top.

        Per year, not "somewhere above the tallest bar": the overlay follows the recurring total,
        which moves with the years, so a segment that has slipped under its own year's stack is
        the defect and the highest segment of the chart says nothing about it.
        """
        svg = _monthly_burden_svg(financed_result, financed_burden)
        assert financed_burden.replacement_reserve_per_month, "nothing to draw"
        assert "with replacement reserve" in svg
        tops: dict = {}
        for left, top, _width, _height in rects(svg):
            tops[round(left, 1)] = min(tops.get(round(left, 1), top), top)
        drawn = 0
        for x1, line_y in _dashed_segments(svg):
            if x1 not in tops:
                continue
            drawn += 1
            assert line_y <= tops[x1] + 0.001, x1
        assert drawn, "no dashed segment sits over a bar at all"

    def test_no_reserve_segment_hangs_over_a_year_that_draws_no_bar(
        self, financed_result, financed_burden
    ):
        """Year 0 books no recurring month, so an overlay there floats in the empty margin."""
        svg = _monthly_burden_svg(financed_result, financed_burden)
        per_group = views.monthly_burden_by_group(financed_result, PresentationStyle.CATEGORY_TO_GROUP)
        first_drawn = next(year for year, groups in enumerate(per_group) if any(groups.values()))
        assert first_drawn > 0, "the fixture draws a bar in year 0, so nothing tests the trim"
        bar_w = (_ChartGeometry.WIDTH - 70 - 20) / len(financed_burden.series)
        left_edges = [x1 for x1, _y in _dashed_segments(svg)]
        assert min(left_edges) == pytest.approx(70 + first_drawn * bar_w + 1, abs=0.05)
        assert len(left_edges) == len(financed_burden.series) - first_drawn

    def test_the_banded_total_gets_a_whisker_and_the_segments_do_not(
        self, financed_result, financed_burden
    ):
        """Banding every segment of a stack produces a picture nobody can read."""
        svg = _monthly_burden_svg(financed_result, financed_burden)
        whiskers = re.findall(r'<line x1="([\d.]+)" y1="[\d.]+" x2="\1"', svg)
        assert whiskers, "the fixture's monthly totals are degenerate, so nothing is banded"
        assert svg.count("total:") == len(whiskers)

    def test_a_perspective_with_no_recurring_month_skips_the_section(self, financed_result):
        """The unreachable `if not burden.series` used to let an all-capital view draw a bare axis."""
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        capital_only = copy.deepcopy(financed_result)
        capital_only.timeline.entries = [
            entry for entry in capital_only.timeline.entries
            if entry.category not in views.BurdenCategories.RECURRING
        ]
        assert views.monthly_burden_series(capital_only).series, "the series is still one per year"
        assert _monthly_burden_section_html(capital_only, context) == ""
        assert [entry.name for entry in context.skipped] == [ReportSections.MONTHLY_BURDEN[1]]


# ---------------------------------------------------------------- the sections built on them


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database; module-scoped because validating it dominates the runtime."""
    return CostDatabase()


#: The house the balance is drawn for: PV over the meter, a heat pump and the household under it.
#: Sources 14,000 kWh/a against 12,000 kWh/a of drawn sinks, so the balance also has a residual
#: terminal to disclose. The two grid roles are the metered quantities of `make_inputs`, which
#: `views.energy_balance_flows` validates them against.
DEVICE_ENERGY_FLOWS = {
    "PVSystem": {"PV_GENERATION": 9000.0},
    "ElectricityMeter": {"GRID_IMPORT": 5000.0, "GRID_EXPORT": 2500.0},
    "HeatPump": {"HEAT_PUMP_ELECTRICITY": 6000.0},
    "UTSPConnector": {"HOUSEHOLD_ELECTRICITY": 3500.0},
}


def make_inputs(energy_kwh: float = 5000.0, investment: float = 16000.0) -> EvaluationInputs:
    """A banded heat pump, a second envelope subject and a metered bill with feed-in.

    Two subjects rather than one because the cost-shapes Sankey skips a single-subject perspective
    on purpose, and sold electricity because the energy balance's export node is validated against
    the meter — a fixture without it could only exercise the import half of that check.
    """
    heat_pump = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(investment, investment * 0.8, investment * 1.3),
        lifetime_override_in_years=18.0,
        override_source="test",
    )
    windows = ComponentCostFacts(
        asset_class=ComponentType.WINDOWS_TRIPLE_GLAZED, size=28.0, size_unit=Units.SQUARE_METER
    )
    return EvaluationInputs(
        simulation_year=2026,
        simulated_period_fraction=1.0,
        cost_facts=[
            SubjectCostFacts("HeatPump", heat_pump),
            SubjectCostFacts("Envelope.Windows", windows),
        ],
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=energy_kwh,
                energy_sold_in_kwh=2500.0,
            )
        ],
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
        heated_floor_area_in_m2=150.0,
    )


#: A fixed perspective set rather than the shipped bundle: the equity build-up and the loan need a
#: *financed* view, and a bundle data PR must not be able to remove it.
REPORT_PERSPECTIVES = [
    Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.none()),
    Perspective(id="financed", installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.none(),
                financing=FinancingPlan(financed_share=0.6, nominal_interest_rate=0.035,
                                        term_in_years=12)),
]


def _evaluate(database, perspective: Perspective, **inputs) -> LifecycleCostResult:
    """One evaluated perspective carrying `DEVICE_ENERGY_FLOWS`.

    The device energy record is attached after the evaluation rather than fed through the inputs
    because it is collected by `bridge.py` from the simulation's own output columns, which no
    in-memory fixture runs. The field is an ordinary result field, so setting it on a freshly
    evaluated (and therefore unshared) result is the whole of what the bridge would have done —
    and the energy balance validates the two grid nodes against the metered quantities that *did*
    come from these inputs, so an inconsistent fixture would raise rather than render.
    """
    evaluator = EconomicEvaluator(database, EconomicParameters(country="DE", price_basis_year=2026))
    result = evaluator.evaluate(make_inputs(**inputs), perspective)
    result.annual_energy_attribution_by_subject_in_kwh = dict(DEVICE_ENERGY_FLOWS)
    return result


@pytest.fixture(name="financed_result", scope="module")
def fixture_financed_result(database) -> LifecycleCostResult:
    """The financed perspective on its own — what the two SVG builders are exercised on."""
    return _evaluate(database, REPORT_PERSPECTIVES[1])


@pytest.fixture(name="financed_burden", scope="module")
def fixture_financed_burden(financed_result) -> views.MonthlyBurden:
    """Its monthly burden, derived once — the section derives it and hands it to the chart."""
    return views.monthly_burden_series(financed_result)


@pytest.fixture(name="gross_result", scope="module")
def fixture_gross_result(database) -> LifecycleCostResult:
    """The unfinanced perspective, for the sections drawn on the matrix's first row."""
    return _evaluate(database, REPORT_PERSPECTIVES[0])


@pytest.fixture(name="report", scope="module")
def fixture_report(database) -> str:
    """The richest document these fixtures reach: both perspectives, a comparison and a reference."""
    matrix = EvaluationMatrix()
    for perspective in REPORT_PERSPECTIVES:
        matrix.results[perspective.id] = _evaluate(database, perspective)
    reference = _evaluate(database, REPORT_PERSPECTIVES[0], energy_kwh=15000.0, investment=2000.0)
    comparison = compare(reference, matrix.results["gross"], "base", "measures")
    return build_lifecycle_report_html(
        matrix, run_plausibility_checks(matrix), None, comparison, reference_result=reference
    )


#: The eight sections this slice adds to the document, by their chapter-prefixed anchor. Three of
#: them are perspective-scoped and therefore live in the owner-occupied chapter rather than in the
#: perspective-free one, and the benchmark only exists against a reference variant: which chapter
#: a section lands in is part of what the document claims about it, so the prefixes are spelled
#: out here rather than searched for.
NEW_ANCHORS = (
    "building-at-a-glance",
    "owner-funding",
    "building-energy-balance",
    "building-cost-structure",
    "building-cost-shapes",
    "owner-equity-build-up",
    "owner-monthly-burden",
    "vs-reference-bank-benchmark",
)


class TestTheNewSectionsRender:
    """Each of them is present on *this* fixture, in the declared order, named by perspective.

    The four-part opening and the contents links are not here: they are the same for every
    section of the report and are byte-compared, for all eight of these, by
    `tests/test_economics_report_goldens.py`. What this fixture adds is that a *different* set of
    perspectives — a gross and a financed one, with a device energy record — still reaches all
    eight, so the disclosure tests below are not quietly asserting on sections that never
    rendered.
    """

    def test_every_new_section_is_in_the_document(self, report):
        """A section that skipped itself would leave the fixture verifying nothing."""
        for anchor in NEW_ANCHORS:
            assert f'id="{anchor}"' in report, anchor

    def test_the_new_sections_sit_in_the_chapter_that_tells_their_story(self, report):
        """The document is chapter-major, and each of these sections is in the chapter it claims.

        The page order is the chapters of `ReportChapters.ORDER`, each rendering in one
        uninterrupted run; *within* a chapter the order is the one that chapter's story is told in
        and deliberately not `ReportSections.ORDER`, which is a name registry rather than a page
        order. What is asserted is therefore the chapter-major shape plus the chapter each of
        these eight sections landed in — an interleaved document, or a perspective-scoped section
        rendered in the perspective-free chapter, fails one or the other.

        That each of them opens with its four authored parts and is reachable from the contents is
        the goldens oracle's job, which covers all eight and their captions word for word; what is
        left here is what a golden cannot express.
        """
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        chapters = [_chapter_of(anchor) for anchor in anchors]
        rendered_chapters = [
            chapter for chapter, _name in ReportChapters.ORDER if chapter in chapters
        ]
        assert chapters == sorted(chapters, key=rendered_chapters.index)
        for anchor in NEW_ANCHORS:
            assert anchor in anchors, anchor

    def test_a_perspective_scoped_section_names_its_perspective(self, report):
        """The equity build-up is drawn for the financed view, not for the matrix's first row."""
        by_anchor = dict(rendered_sections(report))
        assert "<h3>Equity build-up (financed)" in by_anchor["owner-equity-build-up"]
        assert "<h3>Cost structure (gross)" in by_anchor["building-cost-structure"]
        assert "<h3>Funding (financed)" in by_anchor["owner-funding"]


class TestTheDisclosuresEachSectionOwes:
    """The run-specific half of a section: this run's own figures, in its own caption.

    Asserted as *numbers read back from the view*, never as the sentence around them. The
    sentences are golden-tested word for word; what a golden cannot check is that the figure the
    section printed is the one its view computed, because both sides of that comparison move
    together when the fixture changes.
    """

    def test_the_funding_section_states_the_double_entry_it_was_given(self, report):
        """Sources equal uses equal the gross year-0 investment, or the Sankey is a picture."""
        funding = dict(rendered_sections(report))["owner-funding"]
        # The section's own caption is the last paragraph before its diagram; the paragraphs
        # above it are the authored opening, which states no amount.
        caption = funding[:funding.index("<svg")].rsplit("<p class='sub'>", 1)[1]
        amounts = re.findall(r"([\d,]+) EUR", caption)
        assert len(amounts) >= 3
        assert amounts[0] == amounts[1] == amounts[2]

    def test_the_equity_section_prints_the_residual_credit_its_view_computed(
        self, report, financed_result
    ):
        """The book-value line is the residual calculator's own basis; that is its audit weight."""
        equity = dict(rendered_sections(report))["owner-equity-build-up"]
        series = views.asset_debt_series(financed_result)
        assert f"{_fmt(series.residual_credit_in_euro)} EUR" in equity

    def test_the_monthly_burden_prints_year_one_and_the_reserve_its_view_computed(
        self, report, gross_result
    ):
        """A monthly figure whose scope is unstated is the easiest number in the report to misread."""
        section = dict(rendered_sections(report))["owner-monthly-burden"]
        burden = views.monthly_burden_series(gross_result)
        assert _band_str(burden.series[1], "EUR/month") in section
        assert f"{_fmt(burden.replacement_reserve_per_month)} EUR/month" in section

    def test_the_benchmark_draws_both_panels_and_states_the_crossings_it_found(self, report):
        """An absent break-even reads as "did not compute" unless the window is named."""
        benchmark = dict(rendered_sections(report))["vs-reference-bank-benchmark"]
        assert benchmark.count("<svg") == 2  # the rate fan and the terminal advantage
        assert "interest rate [%] (nominal, pre-tax)" in benchmark

    def test_the_rate_fan_draws_ten_distinct_ramp_steps(self, report):
        """Ten ordered rates in a cycled eight-colour palette gave 9 % and 10 % away to 1 % and 2 %."""
        benchmark = dict(rendered_sections(report))["vs-reference-bank-benchmark"]
        fan = re.findall(r'stroke="var\(--ramp(\d+)\)"', benchmark)
        assert len(fan) == len(views.WealthBenchmarkGrid.RATES) == len(SequentialRamp.LIGHT)
        assert len(set(fan)) == len(fan)
        assert [int(step) for step in fan] == sorted(int(step) for step in fan)

    def test_the_overview_says_why_it_has_no_payback_milestone_without_a_reference(
        self, financed_result
    ):
        """Payback is a statement about a difference, so its absence has to be explained."""
        from hisim.economics.reporting.sections_charts import _lifecycle_overview_section_html

        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        section = _lifecycle_overview_section_html(financed_result, None, context)
        assert "there is no payback milestone" in section
        assert "<svg" in section


class TestTheCostStructurePanels:
    """Both bases are drawn, and a basis with nothing to draw is a sentence instead of a box."""

    def test_both_bases_are_drawn_for_a_perspective_that_has_both(self, report):
        """Neither basis alone is the whole truth (Q11), so both panels are there.

        The two disclosures the panels owe — what the gross basis leaves out, which subjects the
        net basis clamped — are the goldens'; here it is only that there are two panels and one
        caption paragraph under them.
        """
        structure = dict(rendered_sections(report))["building-cost-structure"]
        assert structure.count("<svg") == 2
        assert re.search(r"</div><p class='sub'>\S", structure), "the panels carry no caption"

    def test_a_basis_with_nothing_to_draw_is_a_note_rather_than_an_empty_box(
        self, gross_result, monkeypatch
    ):
        """A "0 EUR" heading over an empty box is a chart that failed, not a composition.

        The one place this module reads a sentence: here the sentence *is* the behaviour, since a
        note that replaces a panel has no other trace in the output than the words it is made of.
        """
        _empty_the_tiles(monkeypatch, views.TileBasis.NET_OF_CREDITS)
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        section = _treemap_section_html(gross_result, context)
        assert section.count("<svg") == 1
        assert len(re.findall(r"<div><p class='sub'><b>", section)) == 1
        assert "every subject&#x27;s credits reach its costs" in section

    def test_a_perspective_with_no_area_on_either_basis_skips_the_section(
        self, gross_result, monkeypatch
    ):
        """Both bases empty is no composition at all, and the reader is told so under the contents."""
        _empty_the_tiles(monkeypatch, views.TileBasis.GROSS, views.TileBasis.NET_OF_CREDITS)
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        assert _treemap_section_html(gross_result, context) == ""
        assert [entry.name for entry in context.skipped] == [ReportSections.COST_STRUCTURE[1]]


def _empty_the_tiles(monkeypatch, *bases: views.TileBasis) -> None:
    """Makes `views.cost_structure_tiles` come back with no drawable tile for those bases.

    A perspective whose credits reach its costs on a basis is a real evaluation and a rare one,
    and building one out of the shipped cost database would pin the test to that database's
    numbers. The clamping itself happens in the view and is tested there; what is under test here
    is what the *section* does when the view comes back with nothing positive, so the view is
    asked normally and its tiles are emptied on the way out.
    """
    original = views.cost_structure_tiles

    def emptied(result, mapping, basis):
        """The real disclosures, with the tile list emptied for the named bases."""
        tiles = original(result, mapping, basis)
        return dataclasses.replace(tiles, tiles=[]) if basis in bases else tiles

    monkeypatch.setattr(views, "cost_structure_tiles", emptied)


class TestEnergyBalanceSection:
    """V12's caption is where the diagram states what it could not draw."""

    def test_the_caption_carries_the_shares_and_the_residual_terminal(self, report, gross_result):
        """Self-consumption, autarky and the unattributed remainder, from the view's own figures."""
        balance = dict(rendered_sections(report))["building-energy-balance"]
        flows = views.energy_balance_flows(gross_result)
        assert f"{flows.self_consumption_share:.0%}" in balance
        assert f"{flows.self_sufficiency_share:.0%}" in balance
        residual = next(
            node for node in flows.sources + flows.sinks
            if node.label == views.EnergyBalanceLayout.RESIDUAL_LABEL
        )
        assert f"{residual.quantity_in_kwh:,.0f} kWh/a" in balance

    def test_a_role_the_balance_cannot_place_is_named_beside_the_diagram_not_in_it(self, database):
        """It has no side of the bus, so the only honest place for it is the words beside it.

        A diagram quietly missing 2,500 kWh/a looks exactly like a diagram that never had them,
        which is why `EnergyBalanceFlows.unattributed_roles_in_kwh` exists at all — and a view
        that carries the remainder out to a renderer that drops it again would be no better.
        Asserted structurally: the role's name is in a caption paragraph and in no node label or
        tooltip of the drawing, which is the difference between naming it and drawing it.
        """
        result = _evaluate(database, REPORT_PERSPECTIVES[0])
        result.annual_energy_attribution_by_subject_in_kwh = dict(
            DEVICE_ENERGY_FLOWS, WindTurbine={"WIND_GENERATION": 2500.0}
        )
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        section = _energy_balance_section_html(result, context)
        captions = re.findall(r"<p class='sub'>([^<]*)</p>", section)
        assert any("WIND_GENERATION" in caption for caption in captions)
        assert "WIND_GENERATION" not in section[section.index("<svg"):]

    def test_a_result_without_device_flows_skips_the_section(self, database):
        """A meter talking to itself is not a balance (Q16); the section vanishes instead."""
        result = _evaluate(database, REPORT_PERSPECTIVES[0])
        result.annual_energy_attribution_by_subject_in_kwh = {
            "ElectricityMeter": {"GRID_IMPORT": 5000.0, "GRID_EXPORT": 2500.0}
        }
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        assert _energy_balance_section_html(result, context) == ""
        assert [entry.name for entry in context.skipped] == [ReportSections.ENERGY_BALANCE[1]]


class TestTheSeriesTheChartsAreHandedAreWholeSeries:
    """A curve one year short of its axis is a complete-looking line that ends in the wrong year."""

    def test_a_length_mismatch_is_refused_rather_than_zipped_away(self):
        """`zip` stops at the shorter list, which is a silent redraw of the same chart."""
        with pytest.raises(CostDataError, match="value"):
            _points([0, 1, 2], [10.0, 20.0])
        with pytest.raises(CostDataError):
            _points([0, 1], [10.0, 20.0, 30.0])

    def test_matched_lengths_are_paired_in_order(self):
        """The ordinary path, so the guard cannot be satisfied by refusing everything."""
        assert _points([0, 1], [10.0, 20.0]) == [(0.0, 10.0), (1.0, 20.0)]


class TestTheDocumentStaysSelfContained:
    """The new sections draw the same way the old ones do."""

    def test_no_new_section_reaches_outside_the_file(self, report):
        """Inline SVG, no script, no external request — the whole point of hand-drawn charts."""
        assert "<script" not in report
        assert "https://" not in report

    def test_the_new_charts_use_the_shared_group_palette(self, report):
        """A hue that disagrees with the legend three sections up is a bug nobody reads as one."""
        structure = dict(rendered_sections(report))["building-cost-structure"]
        used = set(re.findall(r"var\(--g(\d)\)", structure))
        assert used
        assert all(int(index) < len(PresentationStyle.GROUP_COLORS_LIGHT) for index in used)
