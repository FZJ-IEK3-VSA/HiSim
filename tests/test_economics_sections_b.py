"""Renderer tests for the second half of the visualization set (visualization spec §5, rule 2.7).

The companion of `tests/test_economics_sections_a.py`, in the same two halves. The first half
parses the two SVG builders this slice adds — the squarified treemap and the monthly-burden stack
— and checks the claims that are only true of the emitted file: that the tiles fill the box they
were given, that a tile too small for a label keeps its tooltip, that costs and credits stack on
separate baselines instead of netting inside a bar, and that the replacement reserve is a line
above the bars rather than a bar of its own.

The second half renders the sections built on those builders — the lifecycle overview, funding,
the energy balance, the cost structure and the cost shapes, the equity build-up, the monthly
burden and the bank benchmark — and checks that each opens the way every section of this report
opens, states the disclosure it owes (what a treemap panel could not draw, what the balance could
not place) and is reachable from the table of contents. Wording is not asserted here: it is
owner-authored prose held in `report_prose.py` and byte-compared by
`tests/test_economics_report_goldens.py`.

**What a failure means.** A *presentation* failure: something a reader sees changed. It says
nothing about whether the numbers are right — `tests/test_economics_views_charts_b.py` owns that —
and a bug caught here can mislead a reader but can never corrupt a stored result.
"""

# clean

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
from hisim.economics.presentation_style import PresentationStyle
from hisim.economics.report_prose import ReportProse
from hisim.economics.reporting import ReportSections, build_lifecycle_report_html
from hisim.economics.reporting.charts import _monthly_burden_svg, _treemap_svg
from hisim.economics.reporting.scaffold import ReportChapters, _ChapterContext
from hisim.economics.reporting.sections_charts import _energy_balance_section_html
from hisim.economics.results import EvaluationMatrix, LifecycleCostResult, compare
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units

pytestmark = pytest.mark.base


# ------------------------------------------------------------------ parsing what was emitted


def _rects(svg: str):
    """Every rectangle of an inline-SVG chart as `(x, y, width, height)`."""
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
    ]


def _dashed_lines(svg: str):
    """The `y` of every dashed horizontal rule — the monthly chart's reserve overlay."""
    return [
        float(y)
        for y in re.findall(r'<line x1="[\d.]+" y1="([\d.]+)" x2="[\d.]+" y2="\1"[^>]*'
                            r'stroke-dasharray="5 3"', svg)
    ]


def _rendered_sections(text: str):
    """Every anchored section of a rendered report as `(anchor, html)`, in page order."""
    return [
        (match.group(1), match.group(0))
        for match in re.finditer(r"<section id=\"([^\"]+)\">.*?</section>", text, flags=re.S)
    ]


class TestTreemapSvg:
    """The area encoding of the cost-structure panels, asserted on the emitted rectangles."""

    #: Four tiles whose areas are 4:2:1:1, so the layout has something to be squarified about.
    TILES = [
        ("Investment - HeatPump", 40000.0, "var(--g0)"),
        ("Energy - ELECTRICITY", 20000.0, "var(--g1)"),
        ("Maintenance - HeatPump", 10000.0, "var(--g2)"),
        ("Replacements - HeatPump", 10000.0, "var(--g3)"),
    ]

    def test_the_tiles_fill_the_box_they_were_given(self):
        """A treemap that does not tile its box is not an area encoding of anything."""
        svg = _treemap_svg(self.TILES, height=240)
        rectangles = _rects(svg)
        assert len(rectangles) == len(self.TILES)
        # Every tile is inset by one unit on each side, so the drawn area is short by the insets.
        drawn = sum(width * height for _x, _y, width, height in rectangles)
        box = (860 // 2 - 20) * 240
        assert 0.85 * box < drawn < box

    def test_tile_areas_are_proportional_to_the_amounts(self):
        """The whole claim of the chart: twice the money is twice the area."""
        svg = _treemap_svg(self.TILES, height=240)
        areas = [width * height for _x, _y, width, height in _rects(svg)]
        assert areas[0] == pytest.approx(2 * areas[1], rel=0.05)
        assert areas[1] == pytest.approx(2 * areas[2], rel=0.05)

    def test_a_tile_too_small_for_a_label_keeps_its_tooltip(self):
        """The inline-SVG panel's advantage over the PNG: a sliver still names itself on hover."""
        svg = _treemap_svg(
            [("Investment - HeatPump", 100000.0, "var(--g0)"), ("Energy - sliver", 1.0, "var(--g1)")]
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

    def test_costs_and_credits_stack_on_separate_baselines(self, financed_result):
        """A feed-in credit must not shorten the energy bar it is drawn beside."""
        svg = _monthly_burden_svg(financed_result)
        rectangles = _rects(svg)
        assert rectangles, "the fixture books no recurring flow at all"
        baseline = re.search(
            r'<line x1="[\d.]+" y1="([\d.]+)" x2="[\d.]+" y2="\1" stroke="var\(--baseline\)"', svg
        )
        assert baseline is not None, "the chart drew no zero baseline to stack against"
        zero_y = float(baseline.group(1))
        above = [rect for rect in rectangles if rect[1] + rect[3] <= zero_y + 0.001]
        below = [rect for rect in rectangles if rect[1] >= zero_y - 0.001]
        assert above and below, "the fixture books no credit, so nothing tests the second baseline"
        assert len(above) + len(below) == len(rectangles), "a bar crossed the zero line"

    def test_the_reserve_line_sits_above_the_bars_it_supplements(self, financed_result):
        """The reserve is what the month costs *once the sinking fund is paid*, so it is on top."""
        burden = views.monthly_burden_series(financed_result)
        svg = _monthly_burden_svg(financed_result)
        if not burden.replacement_reserve_per_month:
            assert 'stroke-dasharray="5 3"' not in svg
            return
        assert "with replacement reserve" in svg
        reserve_tops = _dashed_lines(svg)
        bar_tops = [y for _x, y, _w, _h in _rects(svg)]
        assert reserve_tops and min(reserve_tops) <= min(bar_tops) + 0.5

    def test_the_banded_total_gets_a_whisker_and_the_segments_do_not(self, financed_result):
        """Banding every segment of a stack produces a picture nobody can read."""
        svg = _monthly_burden_svg(financed_result)
        whiskers = re.findall(r'<line x1="([\d.]+)" y1="[\d.]+" x2="\1"', svg)
        assert whiskers, "the fixture's monthly totals are degenerate, so nothing is banded"
        assert svg.count("total:") == len(whiskers)


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


#: The seven sections this slice adds to the document, by anchor.
NEW_ANCHORS = (
    "building-at-a-glance",
    "building-funding",
    "building-energy-balance",
    "building-cost-structure",
    "building-cost-shapes",
    "building-equity-build-up",
    "building-monthly-burden",
    "building-bank-benchmark",
)


class TestTheNewSectionsRender:
    """Each of them is present, names its perspective and opens the way the others open."""

    def test_every_new_section_is_in_the_document(self, report):
        """A section that skipped itself would leave the fixture verifying nothing."""
        for anchor in NEW_ANCHORS:
            assert f'id="{anchor}"' in report, anchor

    def test_every_new_section_opens_with_the_four_authored_parts(self, report):
        """No chart without the block that explains it — the rule 2.6 opening, verbatim."""
        by_anchor = dict(_rendered_sections(report))
        names = dict((f"building-{anchor}", name) for anchor, name in ReportSections.ORDER)
        for anchor in NEW_ANCHORS:
            html = by_anchor[anchor]
            prose = ReportProse.for_section(names[anchor])
            assert f"<p class='sub'>{ReportProse.to_html(prose.shows)}</p>" in html, anchor
            assert f"<p class='sub'>{ReportProse.to_html(prose.adds)}</p>" in html, anchor
            assert "<summary>Terms used here</summary>" in html, anchor
            assert "<summary>How this is calculated</summary>" in html, anchor

    def test_the_contents_reach_every_new_section(self, report):
        """Navigation replaced the numbering, so a new section that is not in it is unreachable."""
        contents = report.split("</nav>")[0]
        for anchor in NEW_ANCHORS:
            assert f'href="#{anchor}"' in contents, anchor

    def test_the_new_sections_sit_where_the_order_declares(self, report):
        """`ReportSections.ORDER` is the document's own claim about its shape; the page follows it."""
        anchors = [anchor for anchor, _html in _rendered_sections(report)]
        declared = [
            f"building-{anchor}" for anchor, _name in ReportSections.ORDER
            if f"building-{anchor}" in anchors
        ]
        assert anchors == declared

    def test_a_perspective_scoped_section_names_its_perspective(self, report):
        """The equity build-up is drawn for the financed view, not for the matrix's first row."""
        by_anchor = dict(_rendered_sections(report))
        assert "<h3>Equity build-up (financed)" in by_anchor["building-equity-build-up"]
        assert "<h3>Cost structure (gross)" in by_anchor["building-cost-structure"]
        assert "<h3>Funding (financed)" in by_anchor["building-funding"]


class TestTheDisclosuresEachSectionOwes:
    """The run-specific half of a section: what its chart could not draw, in its own caption."""

    def test_both_treemap_bases_are_drawn_and_both_state_what_they_hide(self, report):
        """Neither basis alone is the whole truth (Q11), so each panel says what it left out."""
        structure = dict(_rendered_sections(report))["building-cost-structure"]
        assert structure.count("<svg") == 2
        assert "gross cost:" in structure and "net of credits:" in structure
        assert "The gross panel leaves out" in structure
        assert "The net panel applies each subject&#x27;s credits" in structure

    def test_the_funding_section_states_the_double_entry_it_was_given(self, report):
        """Sources equal uses equal the gross year-0 investment, or the Sankey is a picture."""
        funding = dict(_rendered_sections(report))["building-funding"]
        assert "Checked here: sources" in funding
        amounts = re.findall(r"([\d,]+) EUR", funding.split("Checked here:")[1])
        assert len(amounts) >= 3
        assert amounts[0] == amounts[1] == amounts[2]

    def test_the_cost_shapes_caption_reconciles_the_widest_block(self, report):
        """The complaint it answers ("the chart does not add up") is only answered by numbers."""
        shapes = dict(_rendered_sections(report))["building-cost-shapes"]
        assert "How a block reconciles." in shapes
        assert "EUR of cost" in shapes and "EUR of credit" in shapes
        assert "stacked into a block of" in shapes

    def test_the_equity_section_ties_the_horizon_to_the_residual_credit(self, report):
        """The book-value line is the residual calculator's own basis; that is its audit weight."""
        equity = dict(_rendered_sections(report))["building-equity-build-up"]
        assert "At the horizon the book value equals the residual value credited" in equity
        assert "Equity stays positive" in equity or "Equity is negative in year" in equity

    def test_the_monthly_burden_names_year_one_and_its_reserve_line(self, report):
        """A monthly figure whose scope is unstated is the easiest number in the report to misread."""
        burden = dict(_rendered_sections(report))["building-monthly-burden"]
        assert "Year 1 is" in burden and "EUR/month" in burden
        assert "reserve line" in burden

    def test_the_benchmark_states_its_break_even_rate_or_says_there_is_none(self, report):
        """An absent break-even reads as "did not compute" unless the window is named."""
        benchmark = dict(_rendered_sections(report))["building-bank-benchmark"]
        assert "Break-even rate in this run:" in benchmark
        assert benchmark.count("<svg") == 2  # the rate fan and the terminal advantage
        assert "interest rate [%] (nominal, pre-tax)" in benchmark

    def test_the_overview_says_why_it_has_no_payback_milestone_without_a_reference(
        self, financed_result
    ):
        """Payback is a statement about a difference, so its absence has to be explained."""
        from hisim.economics.reporting.sections_charts import _lifecycle_overview_section_html

        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        section = _lifecycle_overview_section_html(financed_result, None, context)
        assert "there is no payback milestone" in section
        assert "<svg" in section


class TestEnergyBalanceSection:
    """V12's caption is where the diagram states what it could not draw."""

    def test_the_caption_carries_the_shares_and_the_residual_terminal(self, report):
        """Self-consumption, autarky and the unattributed remainder, from the view's own figures."""
        balance = dict(_rendered_sections(report))["building-energy-balance"]
        assert "self-consumption" in balance and "self-sufficiency" in balance
        assert "shown as their own node rather than quietly balanced away" in balance
        assert "losses / unattributed" in balance

    def test_a_role_the_balance_cannot_place_is_named_in_the_caption(self, database):
        """It has no side of the bus, so the only honest place for it is the words beside it.

        A diagram quietly missing 2,500 kWh/a looks exactly like a diagram that never had them,
        which is why `EnergyBalanceFlows.unattributed_roles_in_kwh` exists at all — and a view
        that carries the remainder out to a renderer that drops it again would be no better.
        """
        result = _evaluate(database, REPORT_PERSPECTIVES[0])
        result.annual_energy_attribution_by_subject_in_kwh = dict(
            DEVICE_ENERGY_FLOWS, WindTurbine={"WIND_GENERATION": 2500.0}
        )
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        section = _energy_balance_section_html(result, context)
        assert "WIND_GENERATION" in section
        assert "2,500 kWh/a carry role name(s) this balance has no side for" in section
        assert "outside the diagram altogether" in section

    def test_a_result_without_device_flows_skips_the_section(self, database):
        """A meter talking to itself is not a balance (Q16); the section vanishes instead."""
        result = _evaluate(database, REPORT_PERSPECTIVES[0])
        result.annual_energy_attribution_by_subject_in_kwh = {
            "ElectricityMeter": {"GRID_IMPORT": 5000.0, "GRID_EXPORT": 2500.0}
        }
        context = _ChapterContext(chapter=ReportChapters.THE_BUILDING)
        assert _energy_balance_section_html(result, context) == ""


class TestTheDocumentStaysSelfContained:
    """The new sections draw the same way the old ones do."""

    def test_no_new_section_reaches_outside_the_file(self, report):
        """Inline SVG, no script, no external request — the whole point of hand-drawn charts."""
        assert "<script" not in report
        assert "https://" not in report

    def test_the_new_charts_use_the_shared_group_palette(self, report):
        """A hue that disagrees with the legend three sections up is a bug nobody reads as one."""
        structure = dict(_rendered_sections(report))["building-cost-structure"]
        used = set(re.findall(r"var\(--g(\d)\)", structure))
        assert used
        assert all(int(index) < len(PresentationStyle.GROUP_COLORS_LIGHT) for index in used)
