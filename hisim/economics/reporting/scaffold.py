"""Section identity, chapter frame and explanation blocks of the HTML report.

Holds `ReportSections` (the `(anchor, name)` pair of every section), `ReportChapters`, the `_ChapterContext` that makes
anchors unique and remembers what has been explained or skipped, and the renderers built on them: `_section_open`,
`_chapter_open`, `_explanation_html` (the only place `report_prose.ReportProse` reaches the page),
`_table_of_contents_html` and `_not_drawn_html`. A module of its own because `sections.py` and `sections_charts.py` use
it while `assembly.py` imports them; it imports only `charts.py` from the package.
"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from hisim.economics.report_prose import ReportProse

from hisim.economics.reporting.charts import _details, _esc


class ReportSections:
    """The `(anchor, name)` identity of every report section.

    `ORDER` is the set of sections and their canonical names, not the page order: each chapter builder in `assembly.py`
    sets its own order, and one section may appear in several chapters. `tests/test_economics_sections_a.py` checks
    that every anchor the document emits is a member, and the table of contents looks names up here. A section with
    nothing to show renders as an empty string and is listed under "Not drawn for this run". `HOW_TO_READ` is the
    primer stating the three conventions every other section relies on: discounting, the three worlds of the min/max
    band, and the sign rule.
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
    #: The one chart that lives with the audit outputs rather than in the report.
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
    """The chapters of the report: a common part on the gross basis, then one chapter per reader's question.

    A perspective-scoped section renders once per chapter on that chapter's perspectives, so anchors are
    chapter-prefixed (`owner-cash-curve`) and the contents have two levels. `assembly.py` has one builder per chapter
    and passes it the perspectives `views.story_perspectives` assigned to it, based on what their results book. A
    chapter without perspectives is skipped with `skip_chapter` and the reason is shown under the contents (an
    owner-occupied house has no landlord story). `COMPARISON` appears only with a reference variant and has no authored
    intro.
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


@dataclass(frozen=True)
class SkippedSection:
    """One section or chapter the run did not draw, and why; `_not_drawn_html` prints these.

    Attributes:
        name: The section's or chapter's name, as its heading would have shown it.
        reason: The full sentence saying why it was not drawn.
        chapter: The chapter it would have been drawn in; empty for a skipped chapter.
    """

    name: str
    reason: str
    chapter: str = ""


@dataclass
class _ChapterContext:
    """The chapter a section is rendered into, plus the document's memory of explanations and skips.

    The context prefixes a section's anchor with the chapter's anchor, and remembers where each section name was first
    explained so later occurrences link back instead of repeating the prose. `first_explained` and `skipped` are shared
    by all chapters' contexts, which is why this object is mutable and handed around.
    """

    chapter: Tuple[str, str]
    #: section name -> (anchor, chapter name) of the occurrence that carries the full explanation.
    first_explained: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    #: Everything this run could not draw, in the order the assembly reached it.
    skipped: List[SkippedSection] = field(default_factory=list)

    def for_chapter(self, chapter: Tuple[str, str]) -> "_ChapterContext":
        """Return a context for another chapter that shares this one's explanation and skip memory."""
        return _ChapterContext(
            chapter=chapter, first_explained=self.first_explained, skipped=self.skipped
        )

    def skip(self, section: Tuple[str, str], reason: str) -> str:
        """Record a section this run cannot draw and return the empty string it renders as.

        The reason is printed under the table of contents by `_not_drawn_html`.

        Args:
            section: The `(anchor, name)` pair of the section not drawn.
            reason: Why, as a full sentence; it is read as prose in the document.

        Returns:
            The empty string, so a builder can `return context.skip(...)`.
        """
        self.skipped.append(SkippedSection(name=section[1], reason=reason, chapter=self.chapter[1]))
        return ""

    def skip_chapter(self, chapter: Tuple[str, str], reason: str) -> List[str]:
        """Record a whole chapter this run cannot draw, e.g. no landlord story for an owner-occupied house.

        The entry carries no chapter tag, since it is the chapter.

        Args:
            chapter: The `(anchor, name)` pair of the chapter not drawn.
            reason: Why, as a full sentence.

        Returns:
            The empty list of blocks.
        """
        self.skipped.append(SkippedSection(name=chapter[1], reason=reason))
        return []

    def anchor_of(self, section: Tuple[str, str]) -> str:
        """Return the chapter-prefixed anchor of a section in this chapter (`owner-cash-curve`)."""
        return f"{self.chapter[0]}-{section[0]}"


def _section_open(section: Tuple[str, str], context: _ChapterContext, subtitle: str = "") -> str:
    """Return the opening tag and `<h3>` heading of a report section, with its chapter-prefixed anchor.

    The only place a section's name reaches the page, so heading, contents link and cross-references agree. The heading
    names its chapter because one section name can appear in several. `subtitle` (a perspective id or other qualifier)
    is escaped and shown in brackets.
    """
    _anchor, name = section
    tail = f" ({_esc(subtitle)})" if subtitle else ""
    chapter = f" <span class='chapter-tag'>{_esc(context.chapter[1])}</span>"
    return f"<section id=\"{context.anchor_of(section)}\"><h3>{_esc(name)}{tail}{chapter}</h3>"


def _chapter_open(chapter: Tuple[str, str]) -> str:
    """Return a chapter's `<h2>` heading with its lead-in from `ReportProse.CHAPTER_INTROS`, if it has one."""
    anchor, name = chapter
    intro = (
        "" if chapter in ReportChapters.WITHOUT_INTRO
        else f"<p class='sub chapter-intro'>{ReportProse.to_html(ReportProse.for_chapter(name))}</p>"
    )
    return f"<h2 class='chapter' id=\"{anchor}\">{_esc(name)}</h2>{intro}"


def _explanation_html(section: Tuple[str, str], context: _ChapterContext) -> str:
    """Return the four-part explanation of one section, in full only at its first occurrence.

    The four parts: two visible paragraphs (what the chart shows, and what it adds) and two collapsed `<details>`
    blocks (definitions of its terms, and how its numbers are calculated). A later occurrence in another chapter gets a
    one-line link back that names the chapter. The text comes verbatim from `ReportProse`;
    `tests/test_economics_sections_a.py` asserts on the summary strings.

    Args:
        section: The `(anchor, name)` pair from `ReportSections`; the name is the prose key.
        context: The chapter being rendered; mutated to record this section name as explained.

    Returns:
        The two paragraphs and two `<details>` blocks at the first occurrence, a cross-reference paragraph afterwards.
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


def _table_of_contents_html(document: str) -> str:
    """Return the two-level contents: every chapter that rendered, with its sections.

    Entries are found by checking whether each anchor is in the rendered document, so skipped sections and chapters are
    left out automatically. The nesting distinguishes a section that appears in several chapters (e.g. "Cash curve" for
    owner, landlord and society).
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


def _not_drawn_html(skipped: List[SkippedSection]) -> str:
    """Return the block listing what this run could not draw and why, shown under the contents.

    Args:
        skipped: What the builders recorded, in the order they reached it.

    Returns:
        The block, or the empty string when everything was drawn.
    """
    if not skipped:
        return ""
    items = "".join(
        f"<li><b>{_esc(entry.name)}</b>"
        + (f" <span class='chapter-tag'>{_esc(entry.chapter)}</span>" if entry.chapter else "")
        + f" &mdash; {_esc(entry.reason)}</li>"
        for entry in skipped
    )
    return (
        "<div class='sub' style='margin:0 0 18px 0'><b>Not drawn for this run</b>"
        f"<ul style='margin:4px 0 0 0'>{items}</ul></div>"
    )
