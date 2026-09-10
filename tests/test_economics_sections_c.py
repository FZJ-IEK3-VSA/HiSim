"""Renderer tests for the chapter split and the sections that make it possible (rule 2.8, Q24).

The third of the section-renderer files, and the one about the *shape* of the document rather
than about any one chart. `tests/test_economics_sections_a.py` and `_b.py` cover the charts of
the visualization set; this covers what the report is made of once those charts have to be told
as four stories: the chapters, the explain-once-then-link rule that stops the prose tripling, the
four sections the split exists for — the owner's, the tenant's and society's statements (the
landlord's lands with the Sankey it is drawn as, in `_a`) and the assumptions table the whole
report leans on — and the ways a story can be half-present: one party of a tenancy, a
macroeconomic view scoped to a party, a perspective that buys nothing in year 0.

The anchors themselves are pinned once, in `_a`; the contents links once, by the goldens oracle.
This file asserts what those cannot: that the same section name in two chapters is two charts of
two parties, and that the second occurrence carries a link *instead of* the prose.

Several fixtures rather than one, because the chapters are a function of what a run *books*: a
brownfield tenancy with a macroeconomic view reaches all four chapters, a plain owner-occupied
greenfield run reaches two — and it is the second that pins the skip, which is the behaviour a
chapter restructure most easily gets wrong (an empty chapter heading with nothing under it reads
as a broken report, not as an honest absence) — while the half-tenancy and the party-scoped
macroeconomic bundles pin the two ways a chapter used to tell the wrong party's story or refuse
to render at all.

**What a failure means.** A *presentation* failure: something a reader sees changed. It says
nothing about whether the numbers are right — `tests/test_economics_views_charts_a.py` owns the
statements' own reconciliation invariant — and a bug caught here can mislead a reader but can
never corrupt a stored result.
"""

# clean

import re
from dataclasses import replace

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
from hisim.economics.reporting.assembly import _actor_section_html
from hisim.economics.reporting.scaffold import _ChapterContext
from hisim.economics.reporting.summary import _fmt
from hisim.economics.results import EvaluationMatrix
from hisim.economics.timeline import Actor, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.economics.views import (
    CostDataError,
    StatementPartitions,
    landlord_statement,
    levy_transfer_reconciles,
    perspective_statement,
)
from hisim.loadtypes import ComponentType, Units
from tests.economics_report_test_helpers import back_link_target, rendered_sections

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

#: Half a tenancy: the bundle a request can legitimately ask for when only the tenant's side is
#: wanted. The rented chapter has to tell that half rather than draw the tenant twice.
TENANT_ONLY_PERSPECTIVES = [
    Perspective(id="gross", installation_context=InstallationContext.BROWNFIELD,
                subsidy_mode=SubsidyMode.none()),
    Perspective(id="tenant", installation_context=InstallationContext.BROWNFIELD,
                actor_scope=ActorScope.TENANT, subsidy_mode=SubsidyMode.full()),
]

#: A macroeconomic view scoped to a party — a legal combination of the five orthogonal dimensions
#: (`perspectives.Perspective`), and the one that used to take the whole report down: it books CO2
#: damage, so it was classified into the society chapter, whose statement is defined only for a
#: SYSTEM-scoped result and refused it.
LANDLORD_MACRO_PERSPECTIVES = [
    Perspective(id="gross", installation_context=InstallationContext.BROWNFIELD,
                subsidy_mode=SubsidyMode.none()),
    Perspective(id="landlord_macro", installation_context=InstallationContext.BROWNFIELD,
                actor_scope=ActorScope.LANDLORD, accounting=Accounting.MACROECONOMIC,
                subsidy_mode=SubsidyMode.none()),
]


@pytest.fixture(name="database", scope="module")
def fixture_database() -> CostDatabase:
    """The shipped cost database; module-scoped because validating it dominates the runtime."""
    return CostDatabase()


def _section_name_of(anchor: str) -> str:
    """The section name behind a chapter-prefixed anchor (`owner-cash-curve` -> "Cash curve").

    The document's anchors are the only handle a rendered section offers on its identity, and the
    prose is keyed by name, so a test that wants to compare the two has to cross that bridge once
    rather than in every assertion.
    """
    for section_anchor, name in ReportSections.ORDER:
        if anchor.endswith(f"-{section_anchor}"):
            return name
    raise AssertionError(f"{anchor!r} is not a chapter-prefixed anchor of ReportSections.ORDER")


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

    def test_a_perspective_scoped_section_is_told_once_per_story(self, report):
        """The cash curve is a different chart under each party; all of them have to be there."""
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        curve = ReportSections.CASH_CURVE[0]
        told_in = [chapter for chapter, _name in ReportChapters.ORDER if f"{chapter}-{curve}" in anchors]
        assert told_in == ["owner", "rented", "society"]

    def test_a_repeated_section_links_back_instead_of_repeating_the_prose(self, report):
        """The explanation is the bulk of a section, so the later occurrences point at the first.

        The failure mode the back-link exists to prevent is a report three times as long as it
        needs to be, so both halves are pinned: the repeat carries a link to an anchor that really
        is in the document and really is a different one, **and** the prose it points at is not
        also printed under it. A back-link above a full copy of the explanation would satisfy
        every other test in this file while tripling the weight of the report, which is the exact
        regression the rule exists to prevent.
        """
        by_anchor = dict(rendered_sections(report))
        repeats = 0
        for anchor, html in rendered_sections(report):
            target = back_link_target(html)
            if target is None:
                continue
            repeats += 1
            assert target in by_anchor, (anchor, target)
            assert target != anchor
            shows = ReportProse.to_html(ReportProse.for_section(_section_name_of(anchor)).shows)
            assert shows not in html, anchor  # the prose lives once, where the link points
            assert shows in by_anchor[target], target
            assert "<summary>Terms used here</summary>" not in html, anchor
        assert repeats >= 2, "no section repeated, so the back-link rule was never exercised"

    def test_the_society_chapter_draws_its_own_cash_curve(self, report):
        """The macroeconomic story is three sections, and the middle one is easy to lose.

        The society chapter renders on one perspective and its cash curve is the only section of
        it that carries a chart; a builder that dropped it would leave a chapter of two tables
        that still passes every "the chapter is there" assertion in this file.
        """
        curve = dict(rendered_sections(report))[f"society-{ReportSections.CASH_CURVE[0]}"]
        assert "<h3>Cash curve (macroeconomic)" in curve
        assert "<polyline" in curve  # the cumulative cost really is drawn, not just headed

    def test_the_society_chapter_neither_draws_nor_reports_the_financing_sections(self, report):
        """Who borrowed is not a macroeconomic question, so the chapter does not ask it.

        The loan, the cost of credit and the equity build-up are offered by the two chapters whose
        story can borrow. Offering them here as well would put the same three "Not drawn" lines
        under Society in every report that has this chapter — structure rather than information —
        and a reader would learn from them only that a macroeconomic view books no debt service,
        which is true by construction.
        """
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        block = report.split("Not drawn for this run", maxsplit=1)[1].split("</div>")[0]
        society_entries = re.findall(
            r"<b>([^<]+)</b> <span class='chapter-tag'>Society</span>", block
        )
        for section in (ReportSections.LOAN, ReportSections.COST_OF_CREDIT,
                        ReportSections.EQUITY_BUILD_UP):
            assert f"society-{section[0]}" not in anchors, section[1]
            assert section[1] not in society_entries, section[1]
        # The rented chapter, whose story could have borrowed and did not, does say so.
        assert "<b>Loan</b> <span class='chapter-tag'>Rented out</span>" in block

    def test_each_section_names_the_chapter_it_is_being_read_in(self, report):
        """A bare "Cash curve" does not say whose liquidity a contents link just landed on."""
        by_anchor = dict(rendered_sections(report))
        for chapter, name in (ReportChapters.OWNER_OCCUPIED, ReportChapters.RENTED_OUT):
            html = by_anchor[f"{chapter}-{ReportSections.CASH_CURVE[0]}"]
            assert f"<span class='chapter-tag'>{name}</span>" in html

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
        """The two halves of the booked transfer pair reach the page as one figure (F4).

        Pinned against a hand-derived amount rather than against each other. Both figures come
        from the same booked pair, so comparing them to one another passes for any levy the
        ruleset produces, including none at all: what the assertion is worth is the number itself.
        Here the §559e heating cap binds in the best-estimate world at 0.50 EUR/m²·month, so on
        this fixture's 150 m² the rent rises by 0.50 x 150 x 12 = 900 EUR a year, and its present
        value over the 20-year horizon at 3 % is 900 x (1 - 1.03^-20) / 0.03 = 13,389.73 EUR.
        """
        landlord = landlord_statement(all_stories.results["landlord"])
        tenant = perspective_statement(all_stories.results["tenant"], StatementPartitions.TENANT)
        levy = all_stories.results["landlord"].modernization_levy
        assert levy is not None
        assert levy.annual_amount_in_euro.best_estimate == pytest.approx(0.50 * 150.0 * 12.0)
        discounted_years = sum(1.03 ** -year for year in range(1, 21))
        expected = 900.0 * discounted_years
        assert expected == pytest.approx(13_389.73, abs=0.01)  # the arithmetic, written out
        tenant_levy = levy_transfer_reconciles(landlord, tenant)
        assert tenant_levy is not None  # both parties book it, so the check has a figure to return
        assert tenant_levy == pytest.approx(expected, abs=0.01)
        statement = dict(rendered_sections(report))["rented-tenant-statement"]
        assert "the levy is the exact counterpart of the landlord statement" in statement
        assert f"<b>{_fmt(tenant_levy)} EUR</b>" in statement
        # The per-world verdicts of the levy (Q26 F5) are stated beside the landlord's amount.
        assert re.search(r"Binding mechanism (in all three worlds|per world)", report)

    def test_a_leaked_levy_is_refused_rather_than_printed_under_the_claim(self, all_stories):
        """The caption says the two halves are one transfer, so a pair that is not is refused."""
        landlord = landlord_statement(all_stories.results["landlord"])
        tenant = perspective_statement(all_stories.results["tenant"], StatementPartitions.TENANT)
        assert levy_transfer_reconciles(landlord, tenant) is not None  # the honest pair passes
        leaked = replace(
            tenant,
            cash_lines=tuple(
                replace(line, npv_in_euro=line.npv_in_euro + 100.0)
                if line.category == CostCategory.MODERNIZATION_LEVY else line
                for line in tenant.cash_lines
            ),
        )
        with pytest.raises(CostDataError) as refusal:
            levy_transfer_reconciles(landlord, leaked)
        assert "modernization levy does not reconcile" in str(refusal.value)
        assert "13,489.73" in str(refusal.value)  # names the tenant's figure
        assert "13,389.73" in str(refusal.value)  # and the landlord's

    def test_a_rented_chapter_with_one_party_states_the_side_it_has(self, database):
        """A tenant with no landlord used to be drawn as the landlord, statement and all.

        Both party statements fell back to the chapter's first perspective, so a bundle with only
        one of the two parties printed that party's flows twice — once under its own heading and
        once under the other's. The missing side is now named under the contents instead, and the
        levy cross-check simply has nothing to compare.
        """
        matrix = _matrix(database, TENANT_ONLY_PERSPECTIVES)
        report = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        anchors = [anchor for anchor, _html in rendered_sections(report)]
        assert "rented-tenant-statement" in anchors
        assert "rented-landlord-statement" not in anchors
        block = report.split("Not drawn for this run", maxsplit=1)[1].split("</div>")[0]
        assert "<b>Landlord statement</b>" in block
        # The apostrophe reaches the page escaped, so the assertion sits on the plain half.
        assert "side of the tenancy alone" in block
        tenant_statement = dict(rendered_sections(report))["rented-tenant-statement"]
        assert "<h3>Tenant statement (tenant)" in tenant_statement

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

    def test_the_damage_cost_is_stated_because_some_perspective_priced_one(self, report, all_stories):
        """It belongs in the table when the *run* books it, not when the reference view does.

        No flag says so: the section hands the view the whole matrix and the view applies the one
        predicate the story chapters classify by. The reference perspective the table is stated on
        books no damage of its own, which is what makes the row a statement about the run.
        """
        from hisim.economics import views

        reference = next(iter(all_stories.results.values()))
        assert not views.has_macroeconomic_accounting(reference)
        assumptions = dict(rendered_sections(report))["building-assumptions"]
        assert "<h3>Assumptions (gross)" in assumptions  # stated on the reference perspective
        assert "CO2 damage cost (flat over the horizon)" in assumptions
        assert "CO2 price scenario (path on the energy bill)" in assumptions

    def test_the_values_are_spelled_the_way_each_quantity_is_read(self, report):
        """The view hands over numbers; these are the digits the section decides to print them at.

        One row per kind that the fixture reaches, because the conventions differ and a single
        formatter would get at least one of them wrong: a rate to two decimals, a working price to
        four, a factor to six, an energy quantity with separators and no decimal at all.
        """
        assumptions = dict(rendered_sections(report))["building-assumptions"]
        assert "<td>3.00%</td>" in assumptions  # the interest rate, per cent
        assert "<td>20 a</td>" in assumptions  # the horizon, with its unit
        assert "<td>2026</td>" in assumptions  # a year, with no thousands separator
        assert re.search(r"<td>\d+\.\d{6}</td>", assumptions)  # the annuity factor
        assert re.search(r"<td>\d+\.\d{4} EUR/kWh</td>", assumptions)  # a working price
        assert re.search(r"<td>[\d,]+ kWh/a</td>", assumptions)  # an energy quantity
        assert re.search(r"<td>-?[\d,]+\.\d{2}% per year</td>|<td>-?\d+\.\d{2}% per year</td>",
                         assumptions)  # an escalation rate keeps its "per year"

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


class TestTheCo2FactorsAreOneQuantityWithTheMasses:
    """F3: a stated multiplication that does not come out is worse than no disclosure at all.

    The table's whole claim is that each mass in the chart above it is `factor x quantity`, over
    the horizon or over the installations. The two halves of that claim come from two places — the
    CO2 accumulator's mass and the aggregation's annualized quantity — and they used to be allowed
    to disagree: the accumulator summed a carrier's meters while the aggregation kept the last one.
    """

    def test_a_row_whose_multiplication_does_not_come_out_is_refused(self):
        """The row is the disclosure, so it validates itself rather than trusting its builder."""
        from hisim.economics import views

        with pytest.raises(CostDataError) as refusal:
            views.Co2FactorRow(
                subject="ELECTRICITY",
                kind=views.Co2FactorKinds.OPERATIONAL,
                factor_in_kg_per_unit=0.4,
                quantity=5000.0,
                quantity_unit="kWh/a",
                annual_mass_in_kg=2000.0,
                total_in_kg=1500.0,  # the accumulator says something else entirely
                installations=20,
            )
        assert "does not multiply out" in str(refusal.value)

    def test_a_row_carrying_the_other_kinds_product_is_refused(self):
        """An operational row states an annual mass; a per-installation figure on it is a mix-up."""
        from hisim.economics import views

        with pytest.raises(CostDataError) as refusal:
            views.Co2FactorRow(
                subject="ELECTRICITY",
                kind=views.Co2FactorKinds.OPERATIONAL,
                factor_in_kg_per_unit=0.4,
                quantity=5000.0,
                quantity_unit="kWh/a",
                annual_mass_in_kg=2000.0,
                per_installation_in_kg=2000.0,
                total_in_kg=40000.0,
                installations=20,
            )
        assert "Exactly one of the two" in str(refusal.value)

    def test_a_carrier_billed_by_two_meters_states_the_summed_kwh(self, database):
        """Two meters, one carrier: the quantity has to be the sum the mass was computed from."""
        from hisim.economics import views

        inputs = make_inputs()
        inputs.billing = [
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=3000.0,
                energy_sold_in_kwh=1000.0,
            ),
            BillingDeterminants(
                carrier=EnergyCarrier.ELECTRICITY, energy_bought_in_kwh=2000.0,
                energy_sold_in_kwh=1000.0,
            ),
        ]
        evaluator = EconomicEvaluator(database, PARAMETERS)
        result = evaluator.evaluate(inputs, OWNER_ONLY_PERSPECTIVES[0])
        quantities = result.annual_energy_quantities_by_carrier[EnergyCarrier.ELECTRICITY.value]
        assert quantities.bought_in_kwh == pytest.approx(5000.0)
        assert quantities.sold_in_kwh == pytest.approx(2000.0)
        # The row builds at all only because the two sources now describe one quantity.
        row = next(
            row for row in views.co2_factor_rows(result)
            if row.kind == views.Co2FactorKinds.OPERATIONAL
        )
        assert row.quantity == pytest.approx(5000.0)
        assert row.annual_mass_in_kg == pytest.approx(row.factor_in_kg_per_unit * 5000.0)


class TestTheSectionsThatPromiseToAlwaysRender:
    """A section whose docstring says "always" has to say something when it has no chart."""

    def test_a_perspective_that_buys_nothing_in_year_zero_says_so(self, database):
        """An operating-only view books no investment at all, and used to drop the section.

        A missing section reads as a broken renderer; "nothing was bought in year 0" is a finding
        about the perspective, and the one a reader of an operating-only view came for.
        """
        operating = Perspective(
            id="operating",
            installation_context=InstallationContext.OPERATING_ONLY,
            subsidy_mode=SubsidyMode.none(),
        )
        matrix = _matrix(database, [operating])
        report = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        investment = dict(rendered_sections(report))["building-investment-build-up"]
        assert "Nothing was bought in year 0" in investment
        assert "<svg" not in investment  # the waterfalls are what is absent, not the section


class TestAScopedMacroeconomicViewIsToldAsThatPartysStory:
    """A perspective that books CO2 damage without reporting on the system is not society's story.

    The five perspective dimensions are orthogonal, so a landlord-scoped macroeconomic view is a
    legal object. Classified by its accounting alone it landed in the society chapter, whose
    statement reads its transfer side off the full timeline and its resource side off the scoped
    one and is therefore defined only for a SYSTEM-scoped result — so the chapter refused it and
    took the whole report down with it.
    """

    def test_the_report_renders_and_the_view_lands_in_the_rented_chapter(self, database):
        """No crash, no society chapter, and the landlord's own story tells it."""
        from hisim.economics import views

        matrix = _matrix(database, LANDLORD_MACRO_PERSPECTIVES)
        macro = matrix.results["landlord_macro"]
        assert views.has_macroeconomic_accounting(macro)  # it really does book the damage
        stories = views.story_perspectives(list(matrix.results.values()))
        assert stories.rented == (macro,)
        assert not stories.society
        report = build_lifecycle_report_html(matrix, run_plausibility_checks(matrix))
        assert f"<h2 class='chapter' id=\"{ReportChapters.SOCIETY[0]}\">" not in report
        assert 'id="rented-landlord-statement"' in report


class TestWhoPaysWhatNeedsTwoPayers:
    """§6.5 is a statement about a split, and one payer is not one.

    The section draws payer whiskers under a header saying that they sum to the system NPV. With
    a single payer that header is arithmetic about one number and the chart is one bar — a
    perspective that was never allocated, presented as an allocation. The guard used to require a
    SYSTEM residue beside the single payer before skipping, so a result with exactly one real
    payer and no residue was drawn.
    """

    def test_a_single_payer_is_skipped_whether_or_not_a_system_residue_sits_beside_it(
        self, database
    ):
        """One payer, no residue: the case the old guard let through."""
        matrix = _matrix(database, [ALL_STORIES_PERSPECTIVES[3]])  # the landlord view, freshly evaluated
        result = next(iter(matrix.results.values()))
        assert len({payer for payer in result.npv_by_payer if payer != Actor.SYSTEM}) > 1
        context = _ChapterContext(chapter=ReportChapters.RENTED_OUT)
        assert _actor_section_html(matrix, context) != ""  # two parties: a real split
        result.npv_by_payer = {
            Actor.LANDLORD: result.npv_by_payer[Actor.LANDLORD]
        }  # the tenant leg gone, and no SYSTEM entry either
        assert _actor_section_html(matrix, _ChapterContext(chapter=ReportChapters.RENTED_OUT)) == ""
