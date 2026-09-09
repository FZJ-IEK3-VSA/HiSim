"""Section identity, chapter frame and the explanation block of the HTML report (rule 2.8).

The scaffolding every section builder opens with: the mnemonic `(anchor, name)` pairs of
`ReportSections`, the `ReportChapters` a section is rendered into, the `_ChapterContext` that
makes an anchor unique and remembers what has already been explained, and the three renderers
built on them — `_section_open`, `_chapter_open` and `_explanation_html`, the last of which is
the only place `report_prose.ReportProse` reaches the page. `_how_to_read_section_html` and
`_table_of_contents_html` sit here for the same reason: both are made of nothing but this
vocabulary.

Its own module rather than a block of `assembly.py` because both `sections.py` and
`sections_charts.py` open their sections through it while `assembly.py` imports *them* — putting
it in `assembly` would close that loop into a circular import. It sits one layer above
`charts.py`, whose `_esc` and `_details` it borrows, and imports nothing else from the package.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from hisim.economics.report_prose import ReportProse

from hisim.economics.reporting.charts import _details, _esc


class ReportSections:
    """The mnemonic identity of every report section (rule 2.8, owner decision Q18).

    One `(anchor, name)` pair per section, and the order they appear on the page. The report used
    to interleave two numbering schemes — legacy sections 0 to 10 with 4b and 6b wedged in, plus
    the V-numbers of the chart set — neither of which ran monotonically down the document, so a
    reader could not use either to navigate. Names replace both; the V-numbers survive only as
    spec-internal identifiers, the way the decision log's D-numbers do.

    `ORDER` is what the table of contents iterates. It is a superset: a section that has nothing
    to show returns an empty string and then appears in neither the page nor the contents, which
    is why the contents are built from the rendered sections rather than from this list alone. It
    is also ahead of the renderers — the charts of the second half of the visualization set land
    in a later slice — so a name here without a builder yet is expected, and `ReportProse` already
    carries the authored text for all of them.

    `HOW_TO_READ` is the one section that carries no chart and no number: the primer that states
    the three conventions — discounting, the three worlds of the min/max band, and the sign rule —
    every other section then leans on. It is a section like the others rather than a preamble
    glued to the header so that it has an anchor, a contents entry and an explanation block built
    the same way as everywhere else.
    """

    HOW_TO_READ = ("how-to-read", ReportProse.PRIMER_SECTION_NAME)
    AT_A_GLANCE = ("at-a-glance", "At a glance")
    PLAUSIBILITY = ("plausibility", "Plausibility")
    INPUT_AUDIT = ("input-audit", "Input audit")
    ASSUMPTIONS = ("assumptions", "Assumptions")
    INVESTMENT_BUILD_UP = ("investment-build-up", "Investment build-up")
    FUNDING = ("funding", "Funding")
    LIFETIMES = ("lifetimes", "Lifetimes")
    CASH_FLOW_TIMELINE = ("cash-flow-timeline", "Cash-flow timeline")
    CASH_CURVE = ("cash-curve", "Cash curve")
    LOAN = ("loan", "Loan")
    COST_OF_CREDIT = ("cost-of-credit", "Cost of credit")
    ENERGY_BILL = ("energy-bill", "Energy bill")
    ENERGY_BALANCE = ("energy-balance", "Energy balance")
    CO2 = ("co2", "CO2")
    SUBSIDIES = ("subsidies", "Subsidies")
    PERSPECTIVES = ("perspectives", "Perspectives")
    LANDLORD_STATEMENT = ("landlord-statement", "Landlord statement")
    OWNER_STATEMENT = ("owner-statement", "Owner statement")
    TENANT_STATEMENT = ("tenant-statement", "Tenant statement")
    SOCIETY_STATEMENT = ("society-statement", "Society statement")
    WHO_PAYS_WHAT = ("who-pays-what", "Who pays what")
    WHO_PAYS_WHOM = ("who-pays-whom", "Who pays whom")
    UNCERTAINTY_DRIVERS = ("uncertainty-drivers", "Uncertainty drivers")
    COMPONENT_BREAKDOWN = ("component-breakdown", "Component breakdown")
    COST_STRUCTURE = ("cost-structure", "Cost structure")
    COST_SHAPES = ("cost-shapes", "Cost shapes")
    EQUITY_BUILD_UP = ("equity-build-up", "Equity build-up")
    SCENARIOS = ("scenarios", "Scenarios")
    MONTHLY_BURDEN = ("monthly-burden", "Monthly burden")
    KPIS = ("kpis", "KPIs")
    COMPARISON = ("comparison", "Comparison")
    NPV_BRIDGE = ("npv-bridge", "NPV bridge")
    BANK_BENCHMARK = ("bank-benchmark", "Bank benchmark")
    #: The one chart that lives with the audit outputs rather than in the report (Q9).
    LEDGER_HEATMAP = ("ledger-heatmap", "Ledger heatmap")

    ORDER = [
        HOW_TO_READ, AT_A_GLANCE, PLAUSIBILITY, INPUT_AUDIT, ASSUMPTIONS, INVESTMENT_BUILD_UP,
        FUNDING, LIFETIMES,
        CASH_FLOW_TIMELINE, CASH_CURVE, LOAN, COST_OF_CREDIT, ENERGY_BILL, ENERGY_BALANCE, CO2,
        SUBSIDIES, PERSPECTIVES, OWNER_STATEMENT, LANDLORD_STATEMENT, TENANT_STATEMENT,
        SOCIETY_STATEMENT, WHO_PAYS_WHAT, WHO_PAYS_WHOM,
        UNCERTAINTY_DRIVERS,
        COMPONENT_BREAKDOWN, COST_STRUCTURE, COST_SHAPES, EQUITY_BUILD_UP, SCENARIOS,
        MONTHLY_BURDEN, KPIS, COMPARISON, NPV_BRIDGE, BANK_BENCHMARK,
    ]


class ReportChapters:
    """The chapters the report is organized into (owner decision Q24, rule 2.8).

    The report used to be one flat sequence of sections that told three stories at once: a
    perspective-free part about what the technology costs, an owner-occupier part, a
    landlord/tenant part and a macroeconomic part, interleaved, with each section picking a
    perspective of its own. A reader following it end to end therefore switched stories several
    times per page without being told. The chapters make the switch explicit: a common part on the
    gross basis, then one chapter per question a reader actually has.

    A perspective-scoped section consequently renders **once per chapter**, on that chapter's own
    perspectives, so the same section name legitimately appears more than once in a document — and
    that is why anchors are chapter-prefixed (`owner-cash-curve`) and the contents are two-level.
    `COMPARISON` is deliberately not one of the three stories: it is the fourth block, present only
    when a reference variant exists, and it carries no authored intro because it answers a
    question about two runs rather than about one party.

    `assembly.py` currently renders the whole document as `THE_BUILDING`: splitting the story
    chapters apart needs the per-party statement sections, which land with the second half of the
    chart set. The machinery is here now because the anchors, the contents and the
    explain-once-then-link rule are what the section builders are written against, and retrofitting
    them later would touch every one of them a second time.
    """

    THE_BUILDING = ("building", "The building")
    OWNER_OCCUPIED = ("owner", "Owner-occupied")
    RENTED_OUT = ("rented", "Rented out")
    SOCIETY = ("society", "Society")
    COMPARISON = ("vs-reference", "Comparison with the reference")

    #: The common part plus the three story chapters, in page order; each has an authored intro.
    STORY_ORDER = [THE_BUILDING, OWNER_OCCUPIED, RENTED_OUT, SOCIETY]
    ORDER = STORY_ORDER + [COMPARISON]
    #: Chapters that carry no authored lead-in — see the class docstring.
    WITHOUT_INTRO = (COMPARISON,)


@dataclass
class _ChapterContext:
    """Which chapter a section is being rendered into, and what has already been explained.

    Two jobs, both of which exist only because a section can appear more than once. It makes the
    section's anchor unique by prefixing it with the chapter's own anchor, and it remembers where
    each section name's four-part explanation was rendered *first*, so a second and third
    occurrence link back to it instead of repeating a page of prose. Tripling the explanation
    weight of the report is the obvious failure mode of the chapter restructure: the prose is the
    longest part of most sections, and a reader who has just read it does not want it again three
    screens further down.

    `first_explained` is shared between the contexts of all chapters — it is the document's
    memory, not the chapter's — which is why this is a mutable object handed around rather than a
    value each chapter builds for itself.
    """

    chapter: Tuple[str, str]
    #: section name -> (anchor, chapter name) of the occurrence that carries the full explanation.
    first_explained: Dict[str, Tuple[str, str]] = field(default_factory=dict)

    def for_chapter(self, chapter: Tuple[str, str]) -> "_ChapterContext":
        """A context for another chapter, sharing this one's explanation memory."""
        return _ChapterContext(chapter=chapter, first_explained=self.first_explained)

    def anchor_of(self, section: Tuple[str, str]) -> str:
        """The chapter-prefixed anchor of a section in this chapter (`owner-cash-curve`)."""
        return f"{self.chapter[0]}-{section[0]}"


def _section_open(section: Tuple[str, str], context: _ChapterContext, subtitle: str = "") -> str:
    """Opening tag and heading of a report section, with its chapter-prefixed anchor.

    The single place a section's name reaches the page, so the heading, the anchor a table-of-
    contents link points at and the name a cross-reference in some other section's prose can
    never drift apart. `subtitle` is the perspective id (or any qualifier) the section applies
    to, shown in brackets after the name and escaped here so callers do not have to.

    The heading carries its chapter (Q24) because the same section name can appear in more than
    one of them: "Cash curve" alone would not tell a reader arriving by a contents link whether
    they are looking at the owner's liquidity or the landlord's. Sections are `<h3>` under the
    chapter's `<h2>`, so the document outline is the chapter structure.
    """
    _anchor, name = section
    tail = f" ({_esc(subtitle)})" if subtitle else ""
    chapter = f" <span class='chapter-tag'>{_esc(context.chapter[1])}</span>"
    return f"<section id=\"{context.anchor_of(section)}\"><h3>{_esc(name)}{tail}{chapter}</h3>"


def _chapter_open(chapter: Tuple[str, str]) -> str:
    """A chapter heading with its authored lead-in (Q24), or without one for the fourth block.

    Chapters are headings between the section cards rather than cards themselves: a chapter is not
    a thing to read, it is the answer to "which of the questions do the next few sections answer".
    The lead-in comes from `ReportProse.CHAPTER_INTROS` verbatim, like every other word of
    explanation in the report.
    """
    anchor, name = chapter
    intro = (
        "" if chapter in ReportChapters.WITHOUT_INTRO
        else f"<p class='sub chapter-intro'>{ReportProse.to_html(ReportProse.for_chapter(name))}</p>"
    )
    return f"<h2 class='chapter' id=\"{anchor}\">{_esc(name)}</h2>{intro}"


def _explanation_html(section: Tuple[str, str], context: _ChapterContext) -> str:
    """The authored four-part explanation of one section — once per section name (Q24).

    Every section of the report opens the same way, so a reader who arrives at any of them by
    following a contents link is never missing context: two visible paragraphs — what the chart
    *shows* and what it *adds* that nothing else covers — then two collapsed disclosures, the
    definition list of every term of art the section uses and the account of how its numbers are
    calculated. The disclosures are collapsed because the report is read twice: once by someone
    who knows the vocabulary and wants the charts, once by someone who does not and needs the
    definitions to be one click away rather than in a glossary at the other end of the document.

    A section name that appears in a second chapter is explained at its **first** occurrence only
    and every later one links back to it: the prose is the longest part of most sections, and
    printing "Cash curve" three times in full would triple the weight of the report for a reader
    who has just read it. The back-link names the chapter it points at, because "see above" is
    useless in a document this long.

    The text itself lives in `ReportProse` and is never assembled here — this function only makes
    HTML of it, so an editorial change is a change to one file of prose and the rendering cannot
    quietly reword anything. The two summary strings come from the same place for the same
    reason: `tests/test_economics_sections_a.py` asserts on them section by section.

    Args:
        section: The `(anchor, name)` pair of `ReportSections`; the name is the prose key.
        context: The chapter being rendered; **mutated** — it records this section name as
            explained, so a later occurrence links here instead of repeating the text.

    Returns:
        The two paragraphs and the two `<details>` blocks at the first occurrence, a one-line
        cross-reference paragraph at every later one.
    """
    name = section[1]
    already = context.first_explained.get(name)
    if already is not None:
        anchor, chapter_name = already
        return (
            "<p class='sub'>The same chart, read the same way: see the explanation under "
            f"<a href=\"#{anchor}\">{_esc(name)} &mdash; {_esc(chapter_name)}</a>.</p>"
        )
    context.first_explained[name] = (context.anchor_of(section), context.chapter[1])
    prose = ReportProse.for_section(name)
    terms = "".join(
        f"<dt><em>{ReportProse.to_html(term)}</em></dt>"
        f"<dd>{ReportProse.to_html(definition)}</dd>"
        for term, definition in prose.terms
    )
    calculation = "".join(
        f"<p class='sub'>{ReportProse.to_html(paragraph)}</p>" for paragraph in prose.calculation
    )
    return (
        f"<p class='sub'>{ReportProse.to_html(prose.shows)}</p>"
        f"<p class='sub'>{ReportProse.to_html(prose.adds)}</p>"
        + _details(ReportProse.TERMS_SUMMARY, f"<dl>{terms}</dl>")
        + _details(ReportProse.CALCULATION_SUMMARY, calculation)
    )


def _how_to_read_section_html(context: _ChapterContext) -> str:
    """The primer: the three conventions every other section assumes the reader knows.

    Discounting, the three complete worlds behind every `best_estimate [min | max]` band, and the
    sign rule that keeps costs and credits apart are stated once, at the top of the page, so no
    section has to re-derive them next to its own chart. It carries no number and no chart, which
    is why it is the one section built from nothing but its heading and its explanation block —
    and why it always renders: there is no input that could make it empty.
    """
    return _section_open(ReportSections.HOW_TO_READ, context) + _explanation_html(
        ReportSections.HOW_TO_READ, context
    ) + "</section>"


def _table_of_contents_html(document: str) -> str:
    """Two-level contents: every chapter that rendered, with the sections inside it (Q24).

    Navigation is the whole reason the numbering existed, so removing the numbers means providing
    it properly. Both levels are built by asking the rendered document whether an anchor is in it,
    which means a section that skipped itself (no loans, no comparison, a degenerate band) and a
    chapter whose perspectives were absent are missing from the contents for free — an empty entry
    pointing at nothing would be worse than no numbering at all.

    The nesting is what makes the same section name appearing more than once readable: "Cash
    curve" under Owner-occupied, under Rented out and under Society are three different charts of
    three different parties, and a flat list would show them as three identical words.
    """
    parts = ["<nav class='sub' style='margin:0 0 18px 0;line-height:1.9'>"]
    for chapter_anchor, chapter_name in ReportChapters.ORDER:
        # Sorted by where the section actually is on the page, not by `ReportSections.ORDER`: a
        # chapter arranges its sections in the order its story is told, and a contents list that
        # disagreed with the page would send a reader looking in the wrong place.
        present = [
            (document.index(f'id="{chapter_anchor}-{anchor}"'), anchor, name)
            for anchor, name in ReportSections.ORDER
            if f'id="{chapter_anchor}-{anchor}"' in document
        ]
        links = [
            f"<a href=\"#{chapter_anchor}-{anchor}\">{_esc(name)}</a>"
            for _position, anchor, name in sorted(present)
        ]
        if not links:
            continue
        parts.append(
            f"<div><a href=\"#{chapter_anchor}\"><b>{_esc(chapter_name)}</b></a>: "
            + " &middot; ".join(links) + "</div>"
        )
    parts.append("</nav>")
    return "".join(parts)
