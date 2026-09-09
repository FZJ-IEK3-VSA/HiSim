"""Renderer tests for the chapter split and the sections that make it possible (rule 2.8, Q24).

The third of the section-renderer files, and the one about the *shape* of the document rather
than about any one chart. `tests/test_economics_sections_a.py` and `_b.py` cover the charts of
the visualization set; this covers what the report is made of once those charts have to be told
as four stories: the chapters, the anchors that keep the same section name in two of them apart,
the explain-once-then-link rule that stops the prose tripling, and the four sections the split
exists for — the owner's, the tenant's and society's statements (the landlord's lands with the
Sankey it is drawn as, in `_a`) and the assumptions table the whole report leans on.

Two fixtures rather than one, because the chapters are a function of what a run *books*: a
brownfield tenancy with a macroeconomic view reaches all four chapters, and a plain owner-occupied
greenfield run reaches two — and it is the second that pins the skip, which is the behaviour a
chapter restructure most easily gets wrong (an empty chapter heading with nothing under it reads
as a broken report, not as an honest absence).

**What a failure means.** A *presentation* failure: something a reader sees changed. It says
nothing about whether the numbers are right — `tests/test_economics_views_charts_a.py` owns the
statements' own reconciliation invariant — and a bug caught here can mislead a reader but can
never corrupt a stored result.
"""

# clean

import re

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
from hisim.economics.financing import FinancingPlan
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import (
    Accounting,
    ActorScope,
    InstallationContext,
    Perspective,
    SubsidyMode,
)
from hisim.economics.plausibility import run_plausibility_checks
from hisim.economics.report_prose import ReportProse
from hisim.economics.reporting import (
    ReportChapters,
    ReportSections,
    build_lifecycle_report_html,
)
from hisim.economics.reporting.summary import _fmt
from hisim.economics.results import EvaluationMatrix
from hisim.economics.timeline import CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.views import StatementPartitions, landlord_statement, perspective_statement
from hisim.loadtypes import ComponentType, Units
from tests.economics_report_test_helpers import rendered_sections

pytestmark = pytest.mark.base


PARAMETERS = EconomicParameters(country="DE", price_basis_year=2026)


def make_inputs(energy_kwh: float = 5000.0, investment: float = 16000.0) -> EvaluationInputs:
    """A banded heat pump replacing a registered gas boiler in a rented flat.

    The register and the tenancy fields are what make this a *rented* run at all: without a cold
    rent and a living area the DE_2024 ruleset books no modernization levy, and the two statements
    the rented chapter exists for would then have nothing to state. The heat demand and the areas
    are the quantities the assumptions table divides by.
    """
    facts = ComponentCostFacts(
        asset_class=ComponentType.HEAT_PUMP,
        size=10.0,
        size_unit=Units.KILOWATT,
        investment_cost_override_in_euro=UncertainValue(investment, investment * 0.8, investment * 1.3),
        lifetime_override_in_years=18.0,
        override_source="test",
    )
    register = ExistingAssetRegister(
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
    return EvaluationInputs(
        simulation_year=2026,
        simulated_period_fraction=1.0,
        cost_facts=[SubjectCostFacts("HeatPump", facts)],
        billing=[
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY,
                energy_bought_in_kwh=energy_kwh,
                energy_sold_in_kwh=2000.0,
            )
        ],
        existing_assets=register,
        annual_heat_demand_in_kwh=15000.0,
        living_area_in_m2=150.0,
        heated_floor_area_in_m2=150.0,
        current_cold_rent_in_euro_per_m2_month=8.5,
    )


#: A fixed perspective set rather than the shipped bundle: one perspective per story plus a
#: financed one, so all four chapters render and a bundle data PR cannot remove any of them. The
#: net view is what puts a perspective in the owner story (it books support), the two scoped views
#: are the rented story, and the macroeconomic one is the only view that books CO2 damage.
ALL_STORIES_PERSPECTIVES = [
    Perspective(id="gross", installation_context=InstallationContext.BROWNFIELD,
                subsidy_mode=SubsidyMode.none()),
    Perspective(id="net", installation_context=InstallationContext.BROWNFIELD,
                subsidy_mode=SubsidyMode.full()),
    Perspective(id="financed", installation_context=InstallationContext.BROWNFIELD,
                subsidy_mode=SubsidyMode.full(),
                financing=FinancingPlan(financed_share=0.6, nominal_interest_rate=0.035,
                                        term_in_years=12)),
    Perspective(id="landlord", installation_context=InstallationContext.BROWNFIELD,
                actor_scope=ActorScope.LANDLORD, subsidy_mode=SubsidyMode.full()),
    Perspective(id="tenant", installation_context=InstallationContext.BROWNFIELD,
                actor_scope=ActorScope.TENANT, subsidy_mode=SubsidyMode.full()),
    Perspective(id="macroeconomic", installation_context=InstallationContext.BROWNFIELD,
                accounting=Accounting.MACROECONOMIC, subsidy_mode=SubsidyMode.none()),
]

#: The other end of the range: a greenfield cash purchase with no support and no tenancy, which is
#: an owner's story and nothing else.
OWNER_ONLY_PERSPECTIVES = [
    Perspective(id="gross", installation_context=InstallationContext.GREENFIELD,
                subsidy_mode=SubsidyMode.none()),
]


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database; module-scoped because validating it dominates the runtime."""
    return CostDatabase()


def _matrix(database, perspectives) -> EvaluationMatrix:
    """One evaluated matrix over the given perspective set, on `make_inputs`."""
    evaluator = EconomicEvaluator(database, PARAMETERS)
    matrix = EvaluationMatrix()
    for perspective in perspectives:
        matrix.results[perspective.id] = evaluator.evaluate(make_inputs(), perspective)
    return matrix


@pytest.fixture(name="all_stories", scope="module")
def fixture_all_stories(database) -> EvaluationMatrix:
    """The matrix that reaches every chapter: owner, rented and society."""
    return _matrix(database, ALL_STORIES_PERSPECTIVES)


@pytest.fixture(name="report", scope="module")
def fixture_report(all_stories) -> str:
    """The four-chapter document; no comparison, so the fourth block is absent by design."""
    return build_lifecycle_report_html(all_stories, run_plausibility_checks(all_stories))


class TestTheChaptersTheDocumentIsToldAs:
    """Q24: four stories, each on its own perspectives, each announced to the reader."""

    def test_the_chapters_carry_their_authored_intros_in_order(self, report):
        """Every rendered chapter opens with its own lead-in, verbatim from `ReportProse`."""
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
        assert len(positions) == 4  # the three stories plus the building; no comparison here

    def test_every_section_lands_in_a_chapter_and_no_two_share_an_anchor(self, report):
        """The same section name in two chapters must not produce the same anchor twice."""
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        assert len(anchors) == len(set(anchors))
        prefixes = tuple(f"{chapter}-" for chapter, _name in ReportChapters.ORDER)
        for anchor in anchors:
            assert anchor.startswith(prefixes), anchor

    def test_a_perspective_scoped_section_is_told_once_per_story(self, report):
        """The cash curve is a different chart under each party; all of them have to be there."""
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        curve = ReportSections.CASH_CURVE[0]
        told_in = [chapter for chapter, _name in ReportChapters.ORDER if f"{chapter}-{curve}" in anchors]
        assert told_in == ["owner", "rented", "society"]

    def test_a_repeated_section_links_back_instead_of_repeating_the_prose(self, report):
        """The explanation is the bulk of a section, so the later occurrences point at the first.

        The failure mode the back-link exists to prevent is a report three times as long as it
        needs to be, so what is pinned is that a repeat carries a link to an anchor that really
        is in the document and really is a different one.
        """
        cross_reference = re.compile(
            r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
            r"<p class='sub'>The same chart, read the same way: see the explanation under "
            r"<a href=\"#([^\"]+)\">",
            flags=re.S,
        )
        repeats = 0
        for anchor, html in rendered_sections(report):
            match = cross_reference.match(html)
            if match is None:
                continue
            repeats += 1
            assert f'id="{match.group(1)}"' in report, (anchor, match.group(1))
            assert match.group(1) != anchor
        assert repeats >= 2, "no section repeated, so the back-link rule was never exercised"

    def test_each_section_names_the_chapter_it_is_being_read_in(self, report):
        """A bare "Cash curve" does not say whose liquidity a contents link just landed on."""
        by_anchor = dict(rendered_sections(report))
        for chapter, name in (ReportChapters.OWNER_OCCUPIED, ReportChapters.RENTED_OUT):
            html = by_anchor[f"{chapter}-{ReportSections.CASH_CURVE[0]}"]
            assert f"<span class='chapter-tag'>{name}</span>" in html

    def test_the_contents_list_the_chapters_and_their_sections(self, report):
        """Two levels, because a flat list would show three "Cash curve" entries as one word."""
        contents = report.split("</nav>")[0]
        for chapter, name in ReportChapters.ORDER:
            if f"<h2 class='chapter' id=\"{chapter}\">" not in report:
                assert f"<a href=\"#{chapter}\">" not in contents, chapter
                continue
            assert contents.count(f"<a href=\"#{chapter}\"><b>{name}</b></a>") == 1, chapter
        for anchor, _html in rendered_sections(report):
            assert f'href="#{anchor}"' in contents, anchor

    def test_a_story_this_run_does_not_tell_is_skipped_rather_than_drawn_empty(self, database):
        """An owner-occupied cash purchase has no landlord and no macroeconomic view.

        The chapter has to disappear entirely — heading, intro and contents entry — because an
        empty chapter reads as a broken report rather than as an honest absence.
        """
        matrix = _matrix(database, OWNER_ONLY_PERSPECTIVES)
        report = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        for chapter, name in (ReportChapters.RENTED_OUT, ReportChapters.SOCIETY):
            assert f"<h2 class='chapter' id=\"{chapter}\">" not in report, name
            assert ReportProse.to_html(ReportProse.for_chapter(name)) not in report, name
            assert f"<a href=\"#{chapter}\">" not in report.split("</nav>")[0], name
        assert f"<h2 class='chapter' id=\"{ReportChapters.THE_BUILDING[0]}\">" in report
        assert f"<h2 class='chapter' id=\"{ReportChapters.OWNER_OCCUPIED[0]}\">" in report


class TestThePartyStatements:
    """Q26 F4: the three statements that open a story chapter, plus the pair that must agree."""

    def test_the_owner_statement_splits_cash_from_book_value(self, report):
        """A negative owner NPV carried by book value is a different proposition from cash."""
        statement = dict(rendered_sections(report))["owner-owner-statement"]
        assert "<h3>Owner statement (financed)" in statement  # the loan flows belong on the cash side
        assert "cash flows, subtotal" in statement
        assert "accounting credits, subtotal" in statement
        assert "net position" in statement
        assert "in present value; together they" in statement

    def test_the_society_statement_states_that_its_transfers_cancel(self, report):
        """The proof of what "macroeconomic" means, rather than the word."""
        statement = dict(rendered_sections(report))["society-society-statement"]
        assert "<h3>Society statement (macroeconomic)" in statement
        assert "real resource costs, subtotal" in statement
        assert "transfers, subtotal" in statement
        assert "removes every transfer at source" in statement
        assert "at a damage cost of" in statement

    def test_the_tenant_statement_has_an_empty_credit_side_and_prints_the_zero(self, report):
        """A tenant receives nothing back, and a stated zero is the answer a missing row is not."""
        statement = dict(rendered_sections(report))["rented-tenant-statement"]
        assert "<h3>Tenant statement (tenant)" in statement
        assert "what the tenant pays, subtotal" in statement
        assert "credits (none in this ledger), subtotal" in statement
        assert "<td><b>0.00</b></td><td><b>credits (none in this ledger)</b></td>" in statement

    def test_the_tenant_levy_mirrors_the_landlord_levy_income(self, all_stories, report):
        """The two halves of the booked transfer pair reach the page as one figure (F4)."""
        landlord = landlord_statement(all_stories.results["landlord"])
        tenant = perspective_statement(all_stories.results["tenant"], StatementPartitions.TENANT)
        landlord_levy = next(
            line.npv_in_euro for line in landlord.cash_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        )
        tenant_levy = next(
            line.npv_in_euro for line in tenant.cash_lines
            if line.category == CostCategory.MODERNIZATION_LEVY
        )
        assert tenant_levy == pytest.approx(-landlord_levy, abs=0.005)
        statement = dict(rendered_sections(report))["rented-tenant-statement"]
        assert "the levy is the exact counterpart of the landlord statement" in statement
        assert f"<b>{_fmt(tenant_levy)} EUR</b>" in statement
        # The per-world verdicts of the levy (Q26 F5) are stated beside the landlord's amount.
        assert re.search(r"Binding mechanism (in all three worlds|per world)", report)

    def test_every_statement_is_the_same_three_column_shape(self, report):
        """Four partitions, one shape: item, present value, and which side it sits on.

        Three of them come through one shared table builder and the landlord's predates it, so
        this is the check that the older one has not drifted into a different table.
        """
        by_anchor = dict(rendered_sections(report))
        for anchor in ("owner-owner-statement", "rented-landlord-statement",
                       "rented-tenant-statement", "society-society-statement"):
            html = by_anchor[anchor]
            assert "<th>Item</th><th>NPV [EUR]</th><th>Side</th>" in html, anchor
            assert "<b>net position</b>" in html, anchor
            assert html.count(", subtotal</b>") == 2, anchor


class TestTheAssumptionsSection:
    """Q26 F2: the causes, stated with a source, directly after the audit of what was priced."""

    def test_the_section_publishes_the_run_s_own_tariff_and_rates(self, report):
        """The working price, the feed-in rate and the escalations the run actually resolved."""
        assumptions = dict(rendered_sections(report))["building-assumptions"]
        assert "annuity factor" in assumptions and "(computed)" in assumptions
        assert "ELECTRICITY: working price" in assumptions
        assert "ELECTRICITY: standing charge" in assumptions
        assert "ELECTRICITY: feed-in rate (FIXED_TARIFF)" in assumptions
        assert "annual heat demand" in assumptions and "15,000 kWh/a" in assumptions
        # Sources, not blanks: the country defaults file is cited by id where it won.
        assert "src_expert_engine_defaults" in assumptions

    def test_every_group_the_view_fills_is_banded_in_the_table(self, report):
        """The table's bands are the order a reader reconstructs a number in, not a sort key."""
        from hisim.economics import views

        assumptions = dict(rendered_sections(report))["building-assumptions"]
        positions = [
            assumptions.index(f"<b>{group}</b>")
            for group in views.AssumptionGroups.ORDER
            if f"<b>{group}</b>" in assumptions
        ]
        assert len(positions) == len(views.AssumptionGroups.ORDER)
        assert positions == sorted(positions)

    def test_the_damage_cost_is_stated_because_some_perspective_priced_one(self, report):
        """It belongs in the table when the *run* books it, not when the reference view does."""
        assumptions = dict(rendered_sections(report))["building-assumptions"]
        assert "CO2 damage cost (flat over the horizon)" in assumptions
        assert "CO2 price scenario (path on the energy bill)" in assumptions

    def test_a_run_without_a_macroeconomic_view_omits_the_damage_cost(self, database):
        """The shadow price is not an assumption of a run that never applies it."""
        matrix = _matrix(database, OWNER_ONLY_PERSPECTIVES)
        report = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        assumptions = dict(rendered_sections(report))["building-assumptions"]
        assert "CO2 damage cost (flat over the horizon)" not in assumptions
        assert "CO2 price scenario (path on the energy bill)" in assumptions

    def test_the_co2_factors_table_states_every_mass_as_a_multiplication(self, report):
        """F3: a bar the reader cannot reproduce from its factor is a number to be trusted."""
        co2 = dict(rendered_sections(report))["building-co2"]
        assert "CO2 factors — every mass as its own multiplication" in co2
        assert "<th>Factor x quantity</th><th>Over the horizon</th>" in co2
        assert re.search(r"[\d.]+ kg/kWh x [\d,]+ kWh/a = [\d,]+ kg/a", co2)
        assert re.search(r"x \d+ a = [\d,]+ kg", co2)
        assert re.search(r"x \d+ installation\(s\) = [\d,]+ kg", co2)


class TestTheChapteredDocumentStaysSelfContained:
    """The restructure must not have introduced a request, a script or an unexplained section."""

    def test_no_chapter_reaches_outside_the_file(self, report):
        """Inline SVG, no script, no external request — unchanged by the split."""
        assert "<script" not in report
        assert "https://" not in report

    def test_every_rendered_section_opens_with_the_four_parts_or_the_back_link(self, report):
        """No chart without the block that explains it, or a link to where it is explained."""
        four_parts = re.compile(
            r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
            r"<p class='sub'>.+?</p>"
            r"<p class='sub'>.+?</p>"
            r"<details><summary>Terms used here</summary><dl><dt>.+?</dl></details>"
            r"<details><summary>How this is calculated</summary><p class='sub'>.+?</details>",
            flags=re.S,
        )
        back_link = re.compile(
            r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
            r"<p class='sub'>The same chart, read the same way: ",
            flags=re.S,
        )
        sections = rendered_sections(report)
        assert len(sections) > 20, "the fixture stopped reaching most sections"
        for anchor, html in sections:
            assert four_parts.match(html) or back_link.match(html), anchor
