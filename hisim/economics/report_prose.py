"""The authored explanation of every report section — the report's own prose, in one place.

Every section of `lifecycle_report.html` opens with the same four parts: what the chart in front
of the reader *shows*, what it *adds* that no other section covers, the terms of art it uses
(defined at engineering-bachelor level), and how the numbers in it are calculated. This module
holds that text and nothing else, so the renderers stay geometry and the wording stays reviewable
as prose rather than as string fragments spread over three thousand lines of `reporting.py`.

Two mappings live here: `SECTIONS`, the four-part explanation of every report section, and
`CHAPTER_INTROS`, the short authored lead-in of each story chapter (owner decision Q24). A chapter
intro is deliberately not four-part — a chapter has no chart to show, nothing to add and nothing
to calculate; it is the sentence that says which of the reader's three questions the sections
below it answer.

**Verbatim.** The text is the owner-reviewed copy of `roadmap/report_explanations.md`, transcribed
without rewording; a change here is an editorial decision, not a rendering one. It is deliberately
**number-free**: every run-specific figure stays in the renderers' dynamic captions, so the prose
is identical in every report and the golden file only moves when the wording is actually edited.
Recurring terms are re-defined in every section that leans on them, because the report is read
non-linearly and a reader landing mid-page must not have to find the primer first.

**Markup.** The strings carry markdown emphasis (`*term*`, `**emphasis**`, `` `code` ``) rather
than HTML, because both renderers need them: `reporting.py` turns them into `<em>`/`<strong>`/
`<code>` with `to_html`, and `report_plots.py` strips them for the matplotlib caption of the
ledger heatmap with `to_plain_text`. The `<details>` summaries are constants here for the same
reason a section name is a constant in `ReportSections`: the test suite asserts on them, and two
spellings of "Terms used here" would make that assertion meaningless.
"""

# clean

import re
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class SectionProse:
    """The four parts of one section's explanation.

    `shows` and `adds` are the two visible paragraphs, `terms` the definition list behind the
    "Terms used here" disclosure (one `(term, definition)` pair per entry, in the order they are
    to be read, not alphabetically), and `calculation` the paragraphs behind "How this is
    calculated". All of them carry markdown emphasis; see the module docstring.
    """

    shows: str
    adds: str
    terms: Tuple[Tuple[str, str], ...]
    calculation: Tuple[str, ...]


class ReportProse:
    """Every section's explanation, keyed by the section name `ReportSections` publishes.

    Keyed by name rather than by anchor so the mapping reads as the table of contents does, and
    so a renamed section fails loudly in `for_section` instead of silently dropping its
    explanation. `SECTIONS` covers every entry of `ReportSections.ORDER` plus `Ledger heatmap`,
    which is a PNG in the audit outputs rather than an HTML section: only its `shows` paragraph
    fits a matplotlib caption, and its terms and calculation are unreachable there — a limitation
    recorded in the PR description rather than papered over.
    """

    TERMS_SUMMARY = "Terms used here"
    CALCULATION_SUMMARY = "How this is calculated"
    PRIMER_SECTION_NAME = "How to read this report"
    LEDGER_HEATMAP_SECTION_NAME = "Ledger heatmap"

    SECTIONS: Dict[str, SectionProse] = {
        "How to read this report": SectionProse(
            shows=(
                "This report prices a simulated building over an observation period of several decades: every "
                "euro that the simulated household, and the other parties around it, pays or receives because "
                "of the installed technology. Three conventions run through every chart. **First**: money in "
                "different years is not comparable, so future amounts are *discounted* — divided by (1 + "
                "interest rate)ⁿ for a payment n years away — before they are summed. A sum of discounted "
                "amounts is a *net present value* (NPV): the single figure that answers \"what does the whole "
                "timeline cost in today's money\". **Second**: every input price is a three-value band — a low, "
                "an average and a high estimate. The report evaluates three complete, internally consistent "
                "worlds (everything cheap, everything average, everything expensive) and shows them as `avg "
                "[min | max]`. The band edges are coherent scenarios, not statistical error bars: a wide band "
                "means the assumptions matter, not that the arithmetic is shaky. **Third**: costs carry a "
                "positive sign and plot upward or rightward; revenues and credits are negative and plot "
                "downward or leftward. Nothing is silently netted."
            ),
            adds=(
                "Everything below builds on these three conventions; this block exists so no section has to "
                "re-derive them."
            ),
            terms=(
                ("Interest rate (discount rate)",
                 (
                     "the annual return the money could earn elsewhere (a savings account, another investment)."
                     " It is the exchange rate between money today and money later."
                 )),
                ("Discounting",
                 (
                     "converting a future amount into its today-equivalent by dividing by (1 + interest rate) "
                     "once per year of distance. 100 EUR due in 10 years at 3 % is worth 100/1.03¹⁰ ≈ 74 EUR "
                     "today."
                 )),
                ("Present value / net present value (NPV)",
                 (
                     "the sum of all discounted cash flows of a plan. \"Net\" means costs and revenues are both "
                     "in, with their signs."
                 )),
                ("Nominal value",
                 (
                     "the amount actually paid or received in its year, price increases included, **not** "
                     "discounted. Nominal answers \"what will the bank transfer say\"; discounted answers \"what "
                     "is it worth today\"."
                 )),
                ("Band / worlds",
                 (
                     "the three-value notation `avg [min | max]`. Min and max are complete scenarios (every "
                     "price at its cheap or expensive end at once), not per-number error bars."
                 )),
                ("Cost vs credit",
                 (
                     "a cost is money leaving the household (positive sign here); a credit is money arriving or"
                     " being saved: revenue, a subsidy, a value refunded."
                 )),
                ("Horizon (observation period)",
                 (
                     "the number of years the evaluation covers. Everything after the horizon is out of scope "
                     "except through the residual credit (see *Lifetimes*)."
                 )),
            ),
            calculation=(
                (
                    "The engine walks a *timeline* of dated, categorized cash flows: year-0 purchases, annual "
                    "energy bills and maintenance, replacement purchases when a device's service life ends "
                    "inside the horizon, subsidies, loan payments, and at the end of the horizon a credit for "
                    "hardware that still has remaining life. Prices escalate at configured annual rates before "
                    "discounting, so \"nominal\" charts show the actual future bank-account amounts and "
                    "\"discounted\" charts show their today-equivalent. Every figure in every chart is an "
                    "aggregation of this one timeline — which is why the sections reconcile with each other, "
                    "and why any number can be traced to its sources with the `explain` command named at the "
                    "foot of the report."
                ),
            ),
        ),
        "At a glance": SectionProse(
            shows=(
                "The whole evaluation on one page: a shared year axis with one row per part of the story. The "
                "top row carries computed milestones — the deepest out-of-pocket point, the payback range where"
                " a comparison exists, the end of the observation period. Below it, one row per financing and "
                "support instrument, then one row per installed component with its service periods (bars) and "
                "its dated events (ticks): purchase, replacement, and the residual credit at the horizon. "
                "Labels on the ticks carry the amounts."
            ),
            adds=(
                "Orientation. Every number here is restated in full detail somewhere below — this chart adds no"
                " new figures, it adds the *timing*: what happens when, over decades, in one picture. If a "
                "replacement lands in a surprising year or a loan outlives a device, this is where it becomes "
                "visible."
            ),
            terms=(
                ("Milestone",
                 (
                     "a computed date worth remembering, derived from the results rather than configured: the "
                     "worst cash position, the payback, the horizon."
                 )),
                ("Out-of-pocket",
                 (
                     "money actually paid from the household's own account, summed up over time. \"Deepest "
                     "out-of-pocket\" is the moment the most of one's own money is tied up in the project, "
                     "before savings and credits have won it back."
                 )),
                ("Payback",
                 (
                     "the year in which accumulated savings (relative to not renovating) have recovered the "
                     "money spent. Shown as a range because the cheap and the expensive world reach that point "
                     "in different years."
                 )),
                ("Service period",
                 (
                     "the span of years a specific installed unit is in operation, from its purchase to its "
                     "replacement or the horizon."
                 )),
                ("Replacement",
                 (
                     "buying the same component again when its service life ends inside the horizon, at that "
                     "future year's (escalated) price."
                 )),
                ("Residual credit (residual value)",
                 (
                     "the worth of hardware that has remaining life when the observation ends, credited back so"
                     " an arbitrary end date does not punish recently bought equipment."
                 )),
                ("Horizon (observation period)",
                 "the last year of the evaluation; the right edge of every time axis in this report."),
            ),
            calculation=(
                (
                    "The rows are read directly from the booked timeline, not from catalog assumptions — a "
                    "component's service period spans from its actual purchase event to its actual replacement "
                    "or the horizon. The payback milestone is drawn as a *range*, from the year the renovation "
                    "has paid for itself in the optimistic world to the year it has in the pessimistic world; a"
                    " single payback year would pretend a precision the input bands cannot support. The deepest"
                    " out-of-pocket milestone is the maximum of the cumulative undiscounted cash curve (see "
                    "*Cash curve*)."
                ),
            ),
        ),
        "Plausibility": SectionProse(
            shows=(
                "A table of automated sanity checks with their measured values and the expected ranges: totals "
                "that must reconcile internally, ratios that experience says should fall inside a generous "
                "corridor (effective energy prices, cost per square meter, maintenance relative to investment),"
                " and structural invariants (a residual credit may never exceed the purchases it stems from). "
                "`OK` means inside the corridor; `WARN` means outside it and worth a human look; `FAIL` means "
                "an internal contradiction."
            ),
            adds=(
                "The report's own error detector, run before you read anything else. A `WARN` here is not "
                "necessarily a defect — an unusual configuration can legitimately sit outside a corridor (a "
                "nearly self-sufficient house has a strange-looking effective electricity price, because a "
                "fixed standing charge is divided by very few purchased kilowatt hours) — but it tells you "
                "which number deserves scrutiny before you trust the headline."
            ),
            terms=(
                ("Invariant",
                 (
                     "a relationship that must hold exactly, always: for example \"the parts sum to the total\". "
                     "An invariant violation is a defect in the computation, never a property of the scenario."
                 )),
                ("Structural invariant",
                 (
                     "an invariant that follows from how the numbers are constructed (a residual credit is a "
                     "fraction of a purchase, so it cannot exceed the purchases), as opposed to an empirical "
                     "corridor that is merely usually true."
                 )),
                ("Corridor (expected range)",
                 (
                     "a generous value range from experience. Values outside are flagged for a look, not "
                     "declared wrong."
                 )),
                ("Effective price",
                 (
                     "total annual bill divided by the energy actually bought, in EUR/kWh. It bundles "
                     "consumption charges, fixed charges and taxes into one comparable number."
                 )),
                ("Reconcile",
                 (
                     "two independently computed figures agreeing exactly. The report leans on reconciliation "
                     "everywhere: if the same money is shown twice, both views must match."
                 )),
            ),
            calculation=(
                (
                    "Each check recomputes its value from the published results, never from internal state, so "
                    "the check proves what the report shows rather than what the engine intended. The corridors"
                    " live in a data file (`plausibility_checks.json`), not in code, and are deliberately wide:"
                    " they are tripwires for wiring mistakes — a price in the wrong unit, a missing component, "
                    "a band typo — not judgments about the scenario's economics."
                ),
            ),
        ),
        "Input audit": SectionProse(
            shows=(
                "One row per priced item: what the engine believed about it (size, unit), the unit price it "
                "resolved (as a low/average/high band), the price basis year, the data source behind it, and "
                "the subsidies attached. This is the complete list of *inputs after resolution* — the numbers "
                "the rest of the report is computed from."
            ),
            adds=(
                "Traceability at the entry point. Every chart downstream aggregates; this is the only section "
                "where each number is still attached to its origin. Configuration mistakes — a component priced"
                " at the wrong size, a price resolved from an unexpected catalog year — surface here first, "
                "before they blur into totals."
            ),
            terms=(
                ("Unit price",
                 (
                     "the price per unit of size: EUR per kilowatt of heat pump, EUR per square meter of "
                     "insulation, EUR per kilowatt hour of storage."
                 )),
                ("Band",
                 "the low/average/high triple every price carries; see the primer."),
                ("Price basis year",
                 (
                     "the year whose price level the cost data represents. All inputs are quoted at this basis "
                     "and then escalated to their payment years."
                 )),
                ("Cost database (catalog)",
                 (
                     "the versioned data files of unit prices, service lives, maintenance rates and emission "
                     "factors the engine prices from."
                 )),
                ("Resolution",
                 (
                     "the lookup step: matching an installed component to its database entry for the right "
                     "country and basis year, and scaling the unit price by the size."
                 )),
                ("Source (citation)",
                 (
                     "where a number comes from: a study, a price survey, an estimate. Every resolved price "
                     "keeps its source, so any figure can be traced."
                 )),
            ),
            calculation=(
                (
                    "The simulation hands the cost engine a set of facts (what is installed, how large). The "
                    "engine resolves each fact against a versioned cost database: it selects the database entry"
                    " valid for the configured price-basis year, multiplies the unit price band by the size, "
                    "and records the source citation. Items the database cannot resolve stop the whole "
                    "evaluation with a named error rather than being silently skipped — an unpriced component "
                    "would make every total quietly wrong."
                ),
            ),
        ),
        "Assumptions": SectionProse(
            shows=(
                "Every economic assumption the evaluation ran on, in one table: the interest rate, the "
                "horizon, the price basis year, the annuity factor they imply; the escalation rate of every "
                "price category and energy carrier; the tariffs — working price, standing charge and feed-in "
                "rate per carrier; the building quantities the per-unit figures divide by (living area, "
                "heated area, annual heat demand); and the CO2 damage-cost path where the society chapter "
                "uses one. Each value carries its source."
            ),
            adds=(
                "Reconstructability. The charts show consequences; this table is the complete set of causes. "
                "Any nominal series in the report is a year-1 value compounded with one of these escalation "
                "rates; any per-square-meter or per-kilowatt-hour figure divides by one of these quantities. "
                "If a number elsewhere seems off, the first check is whether the assumption behind it is the "
                "one you expected."
            ),
            terms=(
                ("Escalation rate",
                 (
                     "the assumed annual price growth of one category (energy, devices, maintenance), "
                     "compounded year by year; the reason later years cost more nominal euros."
                 )),
                ("Working price / standing charge / feed-in rate",
                 (
                     "the three parts of an energy tariff: per-kilowatt-hour price, fixed annual charge, and "
                     "the price paid per exported kilowatt hour."
                 )),
                ("Annuity factor",
                 (
                     "the factor i(1+i)ⁿ/((1+i)ⁿ−1) that converts a present value into the constant annual "
                     "payment with the same worth; fixed by the interest rate and horizon."
                 )),
                ("Price basis year",
                 (
                     "the year the input price level represents; escalation runs from here."
                 )),
                ("CO2 damage-cost path",
                 (
                     "the assumed societal cost per tonne of CO2 over the years, used only in the society "
                     "chapter."
                 )),
            ),
            calculation=(
                (
                    "Nothing is calculated here — this is the boundary between input and result. Everything "
                    "in the table is configuration or cost-database content, resolved for this run and cited; "
                    "everything after this section is computed from it."
                ),
            ),
        ),
        "Investment build-up": SectionProse(
            shows=(
                "One horizontal bar per component: the gross purchase price in year 0 and, where support exists"
                " in this perspective, the part covered by subsidies — so the visible split is \"what it costs\" "
                "versus \"what the owner actually pays\"."
            ),
            adds=(
                "The year-0 story per component. The cost-structure and breakdown sections show lifetime money;"
                " this section isolates the single largest event, the initial purchase, and shows how far "
                "support reduces it — including whether a subsidy cap has flattened the support below its "
                "nominal percentage."
            ),
            terms=(
                ("Gross purchase price / gross investment",
                 (
                     "the full price of buying and installing a component, before any subsidy is subtracted: "
                     "device, installation labor, planning, removal of the old unit."
                 )),
                ("Net investment",
                 (
                     "the gross investment minus the subsidies granted for it: what actually leaves the owner's"
                     " account. \"Gross\" and \"net\" in this report always mean before/after support, not "
                     "before/after VAT."
                 )),
                ("Subsidy",
                 (
                     "money granted by a public program toward a measure; it reduces the owner's cost without "
                     "reducing the measure's price."
                 )),
                ("Subsidy cap",
                 (
                     "an upper limit in a support program: a maximum eligible cost, a maximum percentage, or a "
                     "maximum amount. A binding cap means the program's nominal percentage was clamped."
                 )),
                ("Perspective",
                 (
                     "one defined viewpoint on the same evaluation (see *Perspectives*). A *gross* perspective "
                     "deliberately ignores subsidies to answer \"what does the technology cost\"; a *net* "
                     "perspective answers \"what does the owner pay\"."
                 )),
            ),
            calculation=(
                (
                    "Gross investment is unit price × size from the input audit. The subsidy split comes from "
                    "the subsidy solver (see *Subsidies*): each scheme's percentage applies to its eligible "
                    "cost basis, and caps — per measure, per program, or as a share of cost — clamp the sum. "
                    "Note the perspective in the section title: a *gross* perspective deliberately shows no "
                    "subsidies, so an empty support split there is a definition, not a defect."
                ),
            ),
        ),
        "Funding": SectionProse(
            shows=(
                "A flow diagram of day one: where the money comes from (left — own capital, loans, each subsidy"
                " program as its own block) and what it is spent on (right — each component's gross purchase). "
                "Ribbon thickness is euros; both columns have the same total height by construction."
            ),
            adds=(
                "The financing question, which no cost chart answers: \"how do I pay for this on day one\". "
                "Because each subsidy program is its own block, you can see which program funds which measure —"
                " and own capital is the balancing item, i.e. what remains after all support and debt is "
                "counted."
            ),
            terms=(
                ("Own capital (equity)",
                 (
                     "the owner's own money put into the project, as opposed to borrowed money or granted "
                     "money."
                 )),
                ("Loan disbursement",
                 (
                     "the moment the bank pays out the borrowed amount; it appears as a funding source in year "
                     "0 and is repaid over the following years (see *Loan*)."
                 )),
                ("Subsidy program (scheme)",
                 (
                     "one specific public support offer with its own rules; each is drawn as its own block so "
                     "programs are distinguishable."
                 )),
                ("Balancing item",
                 (
                     "the quantity computed last so that an identity holds. Here: own capital = total spending "
                     "− loans − subsidies."
                 )),
                ("Gross year-0 investment",
                 (
                     "the sum of all components' gross purchase prices in year 0 (see *Investment build-up* for"
                     " \"gross\")."
                 )),
                ("Ribbon",
                 (
                     "a band in a flow diagram whose thickness is proportional to the amount flowing; the curve"
                     " is only routing, the thickness is the number."
                 )),
            ),
            calculation=(
                (
                    "Sources are read from the year-0 timeline: loan disbursements, subsidy payouts, and own "
                    "capital as the residual. The two column totals must equal each other and the gross year-0 "
                    "investment; the report verifies this identity and treats support exceeding the investment "
                    "(negative own capital) as a data defect rather than drawing it. The layout places "
                    "connected blocks near each other and keeps every ribbon's thickness constant along its "
                    "length."
                ),
            ),
        ),
        "Lifetimes": SectionProse(
            shows=(
                "One row per component: bars for its periods in service, ticks for its purchase and replacement"
                " years with their prices, and at the right edge the residual credit — what the hardware is "
                "still worth when the observation ends."
            ),
            adds=(
                "The replacement schedule, which drives the mid-life cost spikes in every timeline chart, and "
                "the *audit* of it: the service periods here are derived from the flows the engine actually "
                "booked, never from the catalog's nominal service life. If a booked replacement interval "
                "disagrees with the database — or a residual appears for something never purchased — it is "
                "visible in this picture (and the second case refuses to render at all)."
            ),
            terms=(
                ("Service life",
                 (
                     "how many years a component type lasts before it needs replacing; a property from the cost"
                     " database (e.g. 18 years for a heat pump, 40 for wall insulation)."
                 )),
                ("Service period",
                 "the actual span one purchased unit is in operation in this evaluation."),
                ("Replacement",
                 (
                     "the repeat purchase when a service life ends inside the horizon, priced at that future "
                     "year's escalated price."
                 )),
                ("Escalated price",
                 (
                     "a today-price projected to a future year by compounding an annual price-increase rate; "
                     "the replacement in year 12 costs more nominal euros than the same device today."
                 )),
                ("Residual value / residual credit",
                 (
                     "the remaining worth of equipment at the horizon, credited back to the evaluation. See the"
                     " depreciation rule below."
                 )),
                ("Straight-line depreciation",
                 (
                     "spreading a purchase price evenly over the service life, so after k of L years the "
                     "remaining (\"book\") value is (L−k)/L of the price."
                 )),
            ),
            calculation=(
                (
                    "A component bought in year 0 with a service life shorter than the horizon is re-purchased "
                    "at its escalated future price when the life ends; the cycle repeats to the horizon. At the"
                    " horizon, remaining value is credited back using straight-line depreciation: a device that"
                    " has served k of its L years is worth (L − k)/L of its last purchase price. This "
                    "convention prevents the arbitrary cutoff date from punishing recently replaced equipment —"
                    " without it, a replacement in the horizon's final year would count as a total loss."
                ),
            ),
        ),
        "Cash-flow timeline": SectionProse(
            shows=(
                "The nominal money of each year as a stacked bar: costs above the zero line, revenues and "
                "credits below, colored by cost group. Year 0 is dominated by the investment; ordinary years "
                "show energy, maintenance and revenues; replacement years spike; the horizon year carries the "
                "residual credit."
            ),
            adds=(
                "The raw material of the whole report, before any discounting — the actual amounts that would "
                "cross a bank account each year. The shape is the fastest plausibility read there is: do "
                "replacements land in the right years, does energy drift upward at a believable rate, does "
                "anything appear in a year where it has no business."
            ),
            terms=(
                ("Nominal",
                 (
                     "the amount actually paid in its year, price increases included, not converted to "
                     "today-money. The bank statement's view."
                 )),
                ("Escalation",
                 (
                     "the assumed annual price growth per kind of cost (energy prices, device prices, "
                     "maintenance wages), compounded year by year."
                 )),
                ("Cost group",
                 (
                     "the report's small set of color-coded categories: investment & financing, energy, "
                     "maintenance & operation, replacements, revenues, residual & anyway credits."
                 )),
                ("Anyway cost (like-for-like renovation)",
                 (
                     "cost the building would have caused even without the improvement: an old boiler at the "
                     "end of its life must be replaced by *something*, a weathered facade must at least be "
                     "repaired. The evaluation credits that unavoidable cost to the renovation in the year it "
                     "would have occurred, so the renovation is only charged for what it truly adds."
                 )),
                ("Anyway share",
                 (
                     "the fraction of the new measure's cost that the counterfactual would really have spent. "
                     "Replacing dead windows: close to all of it. Insulating a facade that was never "
                     "insulated: only the repair share — scaffolding, render, paint — because the "
                     "counterfactual would have repaired, not insulated. The applied share is stated with "
                     "each credit."
                 )),
                ("Counterfactual",
                 (
                     "the hypothetical \"what would have happened without the measure\", against which such "
                     "credits are computed."
                 )),
                ("Residual credit",
                 "see *Lifetimes*: the horizon-year refund for remaining equipment value."),
            ),
            calculation=(
                (
                    "\"Nominal\" means escalated to the year of payment but *not* discounted: a bill n years out "
                    "is this year's price times the carrier's escalation rate compounded n times. Where the "
                    "evaluation credits an avoided like-for-like renovation, that credit is booked in the year "
                    "the counterfactual replacement would have happened — the detail table below the chart "
                    "attributes every flow to its origin."
                ),
            ),
        ),
        "Cash curve": SectionProse(
            shows=(
                "Two panels of the same running total. The upper panel accumulates the nominal flows: the "
                "curve's highest point is the deepest out-of-pocket position — the most money that is ever tied"
                " up. The lower panel accumulates the same flows discounted to today; its endpoint is, by "
                "construction, the headline NPV. The shaded envelope spans the all-cheap and all-expensive "
                "worlds; where a reference variant exists, the savings panel's zero crossing is the payback, "
                "stated as a range across the worlds."
            ),
            adds=(
                "Liquidity. Totals answer \"is it worth it\"; this section answers \"can I afford the path\" — how "
                "deep the position gets, when it recovers, and how much of that timing shifts between the "
                "optimistic and pessimistic world."
            ),
            terms=(
                ("Cumulative",
                 (
                     "each year's value is the running sum of everything up to that year, not the year's own "
                     "amount."
                 )),
                ("Out-of-pocket",
                 (
                     "one's own money actually spent so far; the curve's peak is the worst point of the "
                     "household's cash position."
                 )),
                ("Liquidity",
                 (
                     "having the cash available when a payment is due, as opposed to the project being "
                     "profitable overall. A profitable plan can still have a painful cash valley."
                 )),
                ("Discounting / present value",
                 (
                     "converting future money to today-money by dividing by (1 + interest rate) per year of "
                     "distance; see the primer."
                 )),
                ("NPV (net present value)",
                 "the discounted sum of all flows; the endpoint of the lower panel."),
                ("Envelope (min/max band)",
                 (
                     "the region between the whole-horizon-cheap world and the whole-horizon-expensive world; "
                     "two coherent scenarios, not error bars."
                 )),
                ("Payback",
                 (
                     "the first year cumulative discounted savings versus the reference reach zero; reported "
                     "per world, hence a range."
                 )),
            ),
            calculation=(
                (
                    "Discounting divides a year-n amount by (1 + i)ⁿ, with i the evaluation's interest rate — "
                    "the rate at which the decision-maker could otherwise invest (see *Bank benchmark* for that"
                    " rate made visible). Cumulative curves are computed per world: the lower band edge is the "
                    "*whole horizon* in the cheap world, not a per-year minimum. The payback year in each world"
                    " is the first year its cumulative discounted savings reach zero; reporting the range "
                    "across worlds replaces false single-year precision."
                ),
            ),
        ),
        "Loan": SectionProse(
            shows=(
                "The debt over time: annual debt service split into interest and principal repayment (bars), "
                "and the outstanding balance falling as principal is repaid (line, same euro axis). An annuity "
                "loan shows its signature shape — constant total payment, interest share falling, principal "
                "share rising; a bullet loan shows interest-only years and one final repayment spike."
            ),
            adds=(
                "The financed view. Every other chart books the investment when it happens; this section shows "
                "what a financed owner actually experiences — a stream of payments — and whether the loan is "
                "amortized before the horizon."
            ),
            terms=(
                ("Principal",
                 "the borrowed amount itself."),
                ("Interest",
                 "the price of borrowing: each year, the interest rate times the debt still outstanding."),
                ("Debt service",
                 "a year's total loan payment: interest plus principal repayment."),
                ("Outstanding balance",
                 "how much of the principal is still owed at a given time."),
                ("Annuity loan",
                 (
                     "the common household loan: the same total payment every year, with the interest share "
                     "shrinking and the repayment share growing as the debt falls."
                 )),
                ("Bullet loan (interest-only)",
                 "only interest is paid during the term; the whole principal is repaid at once at the end."),
                ("Term",
                 "the agreed number of years of the loan."),
                ("Amortized",
                 "fully repaid; an amortizing loan's balance reaches zero by the end of its term."),
            ),
            calculation=(
                (
                    "The schedule is read off the booked timeline, not recomputed, so what you see is exactly "
                    "the debt service inside the NPV. For an annuity loan the constant payment is "
                    "P·i(1+i)ⁿ/((1+i)ⁿ−1) for principal P, rate i, term n; each year's interest is the rate "
                    "times the remaining balance and the rest of the payment repays principal. The balance line"
                    " is the disbursement minus cumulative repayments and must reach zero at the end of a fully"
                    " amortizing term — a checked invariant."
                ),
            ),
        ),
        "Cost of credit": SectionProse(
            shows=(
                "The consumer-credit disclosure a loan document carries, as one stacked bar: total repaid, "
                "split into borrowed principal, interest, and fees, with any repayment grant shown as money "
                "coming back. The caption states the *effective annual rate* — the single interest rate that "
                "summarizes the whole package."
            ),
            adds=(
                "Comparability. \"What does the loan cost on top of the money itself\" and \"what rate is this "
                "package really\" are the two numbers to hold against a bank's alternative offer, and neither is"
                " visible in the amortization schedule directly."
            ),
            terms=(
                ("Total cost of credit",
                 (
                     "everything paid back minus everything received: the loan's price on top of the borrowed "
                     "money itself."
                 )),
                ("Fees",
                 (
                     "one-off charges of the loan (processing, commitment) that raise its true cost without "
                     "appearing in the interest rate."
                 )),
                ("Repayment grant",
                 (
                     "support in loan form: a program forgives part of the debt, so less principal must be "
                     "repaid than was disbursed."
                 )),
                ("Nominal rate",
                 "the interest rate written in the contract."),
                ("Effective annual rate",
                 (
                     "the single rate that reproduces the whole package — fees and grants included — when "
                     "applied to the actual payment stream. This is the legally mandated comparison figure for "
                     "consumer credit."
                 )),
                ("Internal rate of return (IRR)",
                 (
                     "the discount rate at which a payment stream's present value is exactly zero; the "
                     "effective rate is the IRR of the loan's own flows."
                 )),
            ),
            calculation=(
                (
                    "The effective rate is the internal rate of return of the loan's flow sequence: the rate at"
                    " which the discounted disbursement (and any grant) exactly balances the discounted debt "
                    "service. For a fee-free, grant-free annuity with annual payments it equals the nominal "
                    "rate — a built-in self-test — and a repayment grant strictly lowers it."
                ),
            ),
        ),
        "Energy bill": SectionProse(
            shows=(
                "The first full year's energy cost per carrier: consumption charge, standing charge, taxes and "
                "levies, with feed-in revenue as a negative entry, and the implied *effective price* — the "
                "total bill divided by the kilowatt hours actually bought."
            ),
            adds=(
                "The link between the simulation's physics and the priced result, one year deep. The effective "
                "price is the fastest unit-mistake detector in the report: it should land near the tariff's "
                "working price, and it drifts upward when a fixed standing charge is spread over few purchased "
                "kilowatt hours — which is exactly what a nearly self-sufficient building looks like, and worth"
                " recognizing rather than mistaking for a pricing error."
            ),
            terms=(
                ("Carrier (energy carrier)",
                 "the form energy is delivered in: electricity, natural gas, district heat, pellets, oil."),
                ("Working price",
                 "the per-kilowatt-hour part of a tariff: what each consumed unit costs."),
                ("Standing charge",
                 (
                     "the fixed part of a tariff, due regardless of consumption (metering, grid connection), "
                     "per month or year."
                 )),
                ("Levy / energy tax",
                 "state-imposed components of an energy price (e.g. a CO2 price on fuel)."),
                ("Feed-in",
                 (
                     "electricity exported to the grid, credited at the feed-in rate; appears as a negative "
                     "(revenue) entry."
                 )),
                ("Effective price",
                 (
                     "total annual bill ÷ kilowatt hours bought. Compare it with the working price: the gap is "
                     "the fixed and tax components spread over the volume."
                 )),
            ),
            calculation=(
                (
                    "The simulation delivers the year's bought and sold energy per carrier; the tariff data "
                    "prices them as working price × quantity plus fixed charges, minus feed-in revenue at the "
                    "feed-in rate. All later years are this bill escalated at per-carrier rates — so a wrong "
                    "year-1 bill propagates through the whole horizon, which is why it gets its own section."
                ),
            ),
        ),
        "Energy balance": SectionProse(
            shows=(
                "The building's energy flows for one year, in kilowatt hours end-to-end: generation and grid "
                "import on the left, the battery as a pass-through, consumption and grid export on the right. "
                "Money appears only as annotations on the grid nodes — import kWh × effective price = bill; "
                "export kWh × feed-in rate = revenue. The caption states the self-consumption share and the "
                "self-sufficiency of the building."
            ),
            adds=(
                "The physical story the money charts price. Whether PV mostly exports or mostly substitutes "
                "purchased electricity, and how hard the battery actually works, determines the bills — and "
                "none of it is visible in euro charts."
            ),
            terms=(
                ("Grid import",
                 "electricity bought from the grid; *grid export* — electricity sold to it."),
                ("Self-consumption share",
                 (
                     "the fraction of the building's own generation that the building uses itself (directly or "
                     "via the battery) instead of exporting."
                 )),
                ("Self-sufficiency (autarky)",
                 (
                     "the fraction of the building's consumption covered by its own generation. The two shares "
                     "answer different questions and only coincide by accident."
                 )),
                ("Pass-through node",
                 (
                     "a node whose inflow equals its outflow plus stated losses; the battery stores energy but "
                     "is not a source or sink over a full year."
                 )),
                ("Battery losses",
                 (
                     "the energy lost between charging and discharging (efficiency below 100 %); stated "
                     "explicitly rather than hidden in the flows."
                 )),
                ("Feed-in rate",
                 "the price per exported kilowatt hour."),
            ),
            calculation=(
                (
                    "The flows come from the simulation's component outputs, aggregated to annual sums; flow "
                    "conservation holds at every node. The diagram keeps one scale for every ribbon — thickness"
                    " is kilowatt hours everywhere — which is why euros are annotations here rather than "
                    "ribbons: a diagram that changed units midway would have no honest widths."
                ),
            ),
        ),
        "CO2": SectionProse(
            shows=(
                "The lifecycle greenhouse-gas masses, parallel to the money but never mixed with it: embodied "
                "emissions at each purchase and replacement, and operational emissions per carrier per year, "
                "undiscounted. A factors table under the chart states every conversion used — kilograms per "
                "kilowatt hour for each carrier, embodied kilograms per device — so each mass is one visible "
                "multiplication away from its inputs."
            ),
            adds=(
                "The second bottom line. A renovation is usually argued on both euros and CO2; this section "
                "keeps the CO2 argument honest by keeping it in kilograms — the *cost* of CO2 (a carbon price "
                "on the bills, or a damage cost in the macroeconomic perspective) lives in the money charts "
                "and is deliberately not added to these masses."
            ),
            terms=(
                ("Lifecycle emissions",
                 (
                     "all greenhouse gases attributable to the installation over the horizon: those from "
                     "manufacturing it and those from operating it."
                 )),
                ("Embodied emissions",
                 (
                     "the mass emitted producing and installing the hardware itself, booked at each purchase "
                     "and replacement."
                 )),
                ("Operational emissions",
                 (
                     "the mass emitted by the energy the building consumes, year by year."
                 )),
                ("Emission factor",
                 (
                     "kilograms of CO2-equivalent per kilowatt hour of a carrier (or per unit of device); the "
                     "conversion between energy and mass."
                 )),
                ("CO2 price vs damage cost",
                 (
                     "a CO2 *price* is a real cash flow (a tax on fuel); a *damage cost* is a societal "
                     "valuation of the harm per tonne, used only in the macroeconomic perspective. Both are "
                     "money and live in the money charts; this section is mass only."
                 )),
            ),
            calculation=(
                (
                    "Embodied masses are per-device factors from the cost database times the installed size, "
                    "booked at each purchase. Operational masses are the simulated annual energy per carrier "
                    "times that carrier's emission factor. Masses are not discounted: discounting encodes "
                    "time preference of *money*, and applying it to physical emissions would claim a tonne "
                    "emitted later matters less."
                ),
            ),
        ),
        "Subsidies": SectionProse(
            shows=(
                "Per measure, the support programs that were applied, with their amounts and — for non-cash "
                "forms — their terms; and for measures where a program was checked but not applied, the failed "
                "condition by name. Where the country has no subsidy catalog, a flat legacy share from the "
                "device data is used instead, and this section says so."
            ),
            adds=(
                "The audit trail for the least transparent numbers in the evaluation. Subsidy amounts are "
                "decisions, not prices — eligibility conditions, percentages, bonuses and caps — and this is "
                "the only place the *reasoning* is visible, including which condition disqualified a program "
                "you expected to apply."
            ),
            terms=(
                ("Measure",
                 "the thing being supported: installing a heat pump, insulating a wall."),
                ("Eligible cost basis",
                 (
                     "the part of a measure's cost a program is allowed to subsidize; percentages apply to this"
                     " basis, not necessarily to the full price."
                 )),
                ("Eligibility condition",
                 (
                     "a requirement for a program to apply at all (building age, income, technology properties,"
                     " replacing a functioning fossil system)."
                 )),
                ("Cap",
                 (
                     "an upper limit on support: maximum eligible cost, maximum percentage, or maximum combined"
                     " support across programs. \"Binding\" means the limit actually clamped the amount."
                 )),
                ("Payout form",
                 (
                     "how support arrives: an *upfront grant* (cash at purchase), a *tax credit* (spread over "
                     "several tax years), *subsidized loan terms* (below-market interest or a repayment grant),"
                     " or *reduced VAT*."
                 )),
                ("Subsidy catalog",
                 (
                     "the data file describing a country's programs and rules; without one, a flat percentage "
                     "from the device data is used as a stand-in (\"legacy share\")."
                 )),
            ),
            calculation=(
                (
                    "The solver evaluates each program's conditions against the applicant and building context,"
                    " stacks eligible percentages on the measure's eligible cost basis, and clamps at every "
                    "declared cap. Each payout form is booked into the timeline in its own shape; a tax credit "
                    "spread over years is worth slightly less than its nominal sum, because later instalments "
                    "are discounted."
                ),
            ),
        ),
        "Perspectives": SectionProse(
            shows=(
                "The same evaluation through different eyes: one row per perspective, each with its *equivalent"
                " annual cost* — the NPV converted into a constant per-year amount — and its band."
            ),
            adds=(
                "The recognition that \"what does it cost\" has no single answer: the tenant's costs are partly "
                "the landlord's income, subsidies vanish from society's view, financing moves money in time. "
                "Reading differences between rows is reading who the technology is expensive *for* — and "
                "several built-in inequalities (gross ≥ net; operating ≤ total) double as sanity checks."
            ),
            terms=(
                ("Perspective",
                 (
                     "a defined viewpoint: which flows count and whose they are. The same timeline, filtered "
                     "differently."
                 )),
                ("Gross perspective",
                 "all costs, no subsidies: what the technology costs, regardless of who helps pay."),
                ("Net perspective",
                 "after subsidies: what the owner pays."),
                ("Operating perspective",
                 "running costs only, no purchase: the view of someone who already owns the system."),
                ("Financed owner",
                 (
                     "the net perspective with a loan instead of upfront payment: the purchase becomes a "
                     "payment stream."
                 )),
                ("Landlord / tenant perspectives",
                 "the rental split: who bears which cost and who receives the levy (see *Who pays what*)."),
                ("Macroeconomic perspective",
                 (
                     "society's view: transfers between parties (subsidies, taxes, levies) cancel out, and CO2 "
                     "is charged at a damage cost instead of a market price."
                 )),
                ("Equivalent annual cost (EAC)",
                 (
                     "the constant yearly payment with the same present value as the whole plan; makes "
                     "different horizons and technologies comparable per year."
                 )),
                ("Transfer",
                 (
                     "money changing hands between parties inside the evaluation without changing society's "
                     "total: a subsidy, a tax, a levy."
                 )),
            ),
            calculation=(
                (
                    "All perspectives read the same timeline through different filters. The equivalent annual "
                    "cost is the NPV multiplied by the annuity factor i(1+i)ⁿ/((1+i)ⁿ−1) — the constant annual "
                    "payment with the same present value — making horizons and technologies comparable per year"
                    " instead of per lump sum."
                ),
            ),
        ),
        "Who pays what": SectionProse(
            shows=(
                "The landlord/tenant split of every cost group under the active allocation rules: who bears "
                "energy, operation, investment, support — and the modernization levy the landlord may add to "
                "the rent, which appears as the tenant's cost and the landlord's income. Negative totals are "
                "net gains."
            ),
            adds=(
                "The distributional answer for rental property. The owner-occupier perspectives cannot say who "
                "wins or loses when landlord and tenant are different parties; this section carries exactly "
                "that, including whether regulation (levy caps) shifts the balance."
            ),
            terms=(
                ("Allocation",
                 (
                     "the rule-based assignment of each cost to a party (landlord or tenant), or its split "
                     "between them; the rules are country- and year-specific."
                 )),
                ("Apportionable costs",
                 (
                     "operating costs a landlord may legally pass on to the tenant (many operating and metering"
                     " costs); non-apportionable ones stay with the landlord."
                 )),
                ("Modernization levy",
                 (
                     "the legally capped annual rent increase a landlord may charge after improving the "
                     "building: a share of the eligible modernization cost per year."
                 )),
                ("Levy cap",
                 (
                     "the legal ceiling on that rent increase, per square meter and month; heating measures "
                     "have their own stricter ceiling."
                 )),
                ("Net gain",
                 (
                     "a party whose received money exceeds its paid money over the horizon shows a negative "
                     "total: the evaluation is profitable *for that party*."
                 )),
                ("Zero-sum",
                 (
                     "the landlord's and tenant's flows must sum to the unallocated whole; allocation moves "
                     "money between parties, it never creates or destroys any."
                 )),
            ),
            calculation=(
                (
                    "An allocation ruleset assigns each timeline entry to a party or splits it. The "
                    "modernization levy converts a share of eligible modernization cost into an annual rent "
                    "increase, clamped by the legal caps, and is booked as a matched pair: tenant pays, "
                    "landlord receives. The zero-sum property is a checked invariant."
                ),
            ),
        ),
        "Who pays whom": SectionProse(
            shows=(
                "The money as flows, left to right: external sources first, then this story's parties — each "
                "in its own column, ordered so that payments between them also flow left to right — then the "
                "external recipients: market, suppliers, state, bank, grid operator. Ribbon thickness is the "
                "nominal sum over the whole horizon; the modernization levy is the ribbon running from the "
                "tenant's column to the landlord's. A party that receives more than it pays out closes its "
                "block with a short labeled stub — its net position — so every block's height is fully "
                "accounted for: inflows on one face, outflows plus the net stub on the other."
            ),
            adds=(
                "Direction. The tables above say how much each party pays in total; this picture says where "
                "each euro comes from and where it ends up — including the state's double role as subsidy "
                "source and tax recipient, and the direct tenant-to-landlord transfer."
            ),
            terms=(
                ("Counterparty",
                 (
                     "the external party on the other side of a payment: the market (installers, vendors) for "
                     "purchases, suppliers for energy and maintenance, the state for taxes and subsidies, the "
                     "bank for the loan, the grid operator for exported electricity."
                 )),
                ("Party (actor)",
                 "a participant inside the evaluation: household, landlord, tenant."),
                ("Nominal lifetime sum",
                 (
                     "all of a flow's yearly nominal amounts added up over the horizon, without discounting; "
                     "the quantity behind each ribbon's thickness."
                 )),
                ("Transfer",
                 (
                     "a payment between two internal parties, drawn as a direct ribbon; it cancels out in any "
                     "total across all parties."
                 )),
                ("Flow conservation",
                 (
                     "at every block, inflow equals outflow; a diagram violating it would be inventing or "
                     "losing money."
                 )),
            ),
            calculation=(
                (
                    "Each timeline entry is classified by its category into a counterparty and by its payer "
                    "into a source. Flow conservation holds per node, ribbon thickness is constant along each "
                    "ribbon, and matched transfer pairs must cancel exactly across the parties — all checked "
                    "before rendering."
                ),
            ),
        ),
        "Uncertainty drivers": SectionProse(
            shows=(
                "One bar per subject: how far the total moves when the whole evaluation switches from the "
                "average to the optimistic world (one direction) or the pessimistic one (the other), measured "
                "for that subject alone. Bars are sorted by width; small contributors are folded into one row; "
                "the bars sum exactly to the total band restated under the chart."
            ),
            adds=(
                "Where the band comes from. The headline says the result is uncertain; this section says *which"
                " assumptions* carry that uncertainty — and therefore which price research or which quote would"
                " narrow the answer most. Arguing about a narrow bar is wasted effort."
            ),
            terms=(
                ("Subject",
                 (
                     "one carrier of cost in this report: a component (heat pump, wall insulation) or an energy"
                     " position (electricity, feed-in)."
                 )),
                ("Optimistic / pessimistic world",
                 (
                     "the all-cheap and all-expensive scenarios from the primer; every price at its favorable "
                     "or unfavorable band edge at once."
                 )),
                ("Attribution",
                 (
                     "splitting an already computed total into the shares its parts contributed. Nothing is "
                     "re-computed; the whole is decomposed."
                 )),
                ("Sensitivity analysis",
                 (
                     "the different thing this is **not**: varying one input at a time and re-evaluating to "
                     "measure its isolated effect."
                 )),
                ("Fold (\"other\" row)",
                 (
                     "small contributors merged into one row to keep the chart readable; the merged row keeps "
                     "their exact sum, so the total still reconciles."
                 )),
                ("Credit subject",
                 (
                     "a subject whose money flows toward the household (feed-in revenue, support). Its cheap "
                     "world is the world where the credit is *small*, which is why both its bars can lie on the"
                     " same side of the axis."
                 )),
            ),
            calculation=(
                (
                    "The engine evaluates three complete worlds anyway; this chart decomposes the low and high "
                    "totals into per-subject shares under the same rules as the totals themselves, which is why"
                    " the parts must sum to the whole (a checked invariant)."
                ),
            ),
        ),
        "Component breakdown": SectionProse(
            shows=(
                "For each component, its lifetime money as a diverging bar: costs to the right of the zero line"
                " (purchase, replacements, maintenance, energy), credits to the left (residual value, "
                "subsidies, revenue, avoided-anyway costs), never netted into each other. The dot and whisker "
                "mark the component's net NPV with its band."
            ),
            adds=(
                "The per-device verdict with its composition intact. Two components with the same net figure "
                "can have opposite structures — expensive-to-buy-cheap-to-run versus the reverse — and lumping "
                "costs and credits into one smaller bar would hide exactly that."
            ),
            terms=(
                ("Diverging bar",
                 (
                     "a bar growing in both directions from a shared zero line: costs one way, credits the "
                     "other, so both magnitudes stay visible."
                 )),
                ("Netting",
                 (
                     "collapsing costs and credits into their difference. This chart deliberately does not net;"
                     " the whisker marks the net so you get both."
                 )),
                ("Net NPV",
                 "a component's costs minus its credits, in present value: its bottom line."),
                ("Whisker",
                 "the thin line through the dot marking the min/max band of the net figure."),
                ("Anyway credit",
                 (
                     "the avoided cost of the renovation the building would have needed regardless (see "
                     "*Cash-flow timeline*), credited to the components that replaced it — at the *anyway "
                     "share*: only the fraction the counterfactual would truly have spent (full for a "
                     "like-for-like replacement, the repair share for a first-time improvement)."
                 )),
                ("Residual value",
                 "the horizon-year refund for remaining equipment life (see *Lifetimes*)."),
            ),
            calculation=(
                (
                    "All flows carry a subject tag, so this is the timeline pivoted by component in present "
                    "value. Net = costs − credits per component, and the nets sum to the headline NPV — the "
                    "reconciliation that makes the breakdown trustworthy."
                ),
            ),
        ),
        "Cost structure": SectionProse(
            shows=(
                "The lifetime present value as a mosaic: each rectangle is one cost group of one component, "
                "area proportional to its share. Two panels: the left shows the cost side only (with total "
                "credits stated in the caption); the right shrinks each component by the credits it attracted, "
                "and names every component whose credits exceeded its costs."
            ),
            adds=(
                "Proportion at a glance — the answer to \"where does the money actually go\" that bar charts give"
                " less immediately. The two panels bracket the honest range: a mosaic cannot draw negative "
                "areas, so showing gross-plus-caption *and* net-with-disclosure is the way to show composition "
                "without hiding the credits."
            ),
            terms=(
                ("Present value share",
                 "a money's fraction of the discounted total; the quantity behind each rectangle's area."),
                ("Treemap (mosaic)",
                 (
                     "a chart that divides a rectangle into tiles whose areas are the shares; good for "
                     "proportion, incapable of negative areas."
                 )),
                ("Gross panel",
                 "cost side only: what is spent, before credits."),
                ("Net-of-credits panel",
                 (
                     "each component shrunk by its own credits (subsidies, revenue, residual): what it costs "
                     "after everything it earns back."
                 )),
                ("Clamped",
                 (
                     "a component whose credits exceed its costs cannot be drawn smaller than zero; its tile is"
                     " set to zero and the overshoot is disclosed in the caption instead of vanishing."
                 )),
            ),
            calculation=(
                (
                    "Areas are the same present values as everywhere else, laid out by a squarified treemap "
                    "(rectangles kept near-square for readability). In the net panel each component's credits "
                    "are subtracted from its own cost tiles proportionally; net-panel total minus disclosed "
                    "clamping must equal the headline NPV — a checked identity."
                ),
            ),
        ),
        "Cost shapes": SectionProse(
            shows=(
                "Components on the left, kinds of cost on the right, ribbon thickness the lifetime present "
                "value connecting them. Solid ribbons are costs; dashed translucent ribbons are credits; "
                "nothing is netted, so a PV system visibly has both an investment ribbon and a revenue "
                "ribbon. Because nothing is netted, a component's block stacks its costs and the magnitude "
                "of its credits — deliberately larger than both its gross cost and its net figure — and the "
                "block's label states both sides so the block can be checked against the component "
                "breakdown: the solid side is that table's cost column, the dashed side its credits."
            ),
            adds=(
                "The *shape* of each technology's cost, which every total hides: a cheap device with expensive "
                "fuel and an expensive device with cheap fuel can have identical totals and completely "
                "different ribbons. In a technology comparison, this shape is usually the actual argument."
            ),
            terms=(
                ("Kind of cost (category)",
                 (
                     "what a payment is for: purchase, replacement, energy, maintenance, revenue, support, "
                     "residual. The right-hand blocks of this chart."
                 )),
                ("Ribbon",
                 (
                     "a band whose thickness is the amount connecting a component to a kind of cost; thickness "
                     "is the number, the curve is routing."
                 )),
                ("Present value",
                 "discounted to today; see the primer."),
                ("Netting",
                 (
                     "collapsing a component's costs and credits into one smaller number; refused here so both "
                     "directions stay visible."
                 )),
            ),
            calculation=(
                (
                    "The same subject-by-category pivot as the component breakdown, drawn as flows. Both "
                    "margins reconcile with their tables — per-component sums with the breakdown, per-category "
                    "sums with the cost-structure groups — and the geometry rules of every flow diagram in this"
                    " report apply: one scale, constant ribbon thickness, nodes tiled exactly."
                ),
            ),
        ),
        "Equity build-up": SectionProse(
            shows=(
                "Two lines and the gap between them: the book value of the installed hardware, written down in "
                "a straight line from every purchase and stepped back up at every replacement; and the "
                "outstanding loan balance, falling as principal is repaid. The filled gap is the owner's equity"
                " in the installation. An interval where the debt line is above the book-value line is marked: "
                "the installation is worth less than what is owed on it."
            ),
            adds=(
                "The lender's solvency picture. Payback and NPV say nothing about collateral; this section "
                "shows whether debt outruns asset value at any point — the condition a bank checks — and when "
                "the installation is fully owned."
            ),
            terms=(
                ("Book value",
                 (
                     "the accounting worth of equipment: purchase price reduced by straight-line depreciation "
                     "over its service life (after k of L years: (L−k)/L of the price)."
                 )),
                ("Straight-line depreciation",
                 "spreading a purchase price evenly over the service life."),
                ("Outstanding balance",
                 "the loan principal still owed (see *Loan*)."),
                ("Equity",
                 "book value minus debt: the owned share of the installation."),
                ("Negative equity (\"underwater\")",
                 "debt exceeding book value; selling the asset would not repay the loan."),
                ("Collateral",
                 "the asset a lender can claim if the loan fails; its book value is what backs the debt."),
                ("Solvency",
                 "assets covering liabilities; the condition this chart makes visible over time."),
            ),
            calculation=(
                (
                    "Book value uses the same straight-line depreciation as the residual-value convention, "
                    "which pins this chart to the rest of the report twice over: at the horizon the book value "
                    "must equal the residual credit booked in the results, and at every purchase it must step "
                    "by exactly the charged amount. The balance line is the loan section's series reused, not "
                    "recomputed."
                ),
            ),
        ),
        "Scenarios": SectionProse(
            shows=(
                "The headline result re-evaluated under alternative assumptions — different interest rates, "
                "energy-price escalations, technology-cost paths — one row per scenario, with the shift "
                "against the central case. Each row states the assumption it changed and both values, the "
                "central one and the scenario's own, so the swing is never a label without a cause."
            ),
            adds=(
                "Structured what-if beyond the three built-in worlds. The band answers \"what if everything "
                "is cheap or expensive at once\"; scenarios answer targeted questions — what if only the "
                "interest rate doubles, what if electricity escalates fast while the rest stays put."
            ),
            terms=(
                ("Scenario",
                 (
                     "one named alternative set of economic assumptions, evaluated in full."
                 )),
                ("Central case",
                 (
                     "the evaluation's main assumptions; the reference all scenario shifts are measured "
                     "against."
                 )),
                ("Axis",
                 (
                     "the one assumption a scenario varies (interest rate, an escalation rate, a technology "
                     "price), holding the rest at the central case."
                 )),
                ("Escalation path",
                 (
                     "the assumed trajectory of a price over the years (e.g. a fast-rising electricity price)."
                 )),
                ("Re-pricing",
                 (
                     "recomputing the money on the stored simulation results without re-running the building "
                     "physics; prices do not change what the building did, only what it cost."
                 )),
            ),
            calculation=(
                (
                    "Each scenario re-runs the *pricing* of the stored evaluation with one axis changed — the "
                    "simulation itself is not re-run. Scenario values are therefore exactly comparable to the "
                    "central case: same building, same flows, different money."
                ),
            ),
        ),
        "Monthly burden": SectionProse(
            shows=(
                "The recurring cost per month, year by year: energy, maintenance, taxes, levies and debt "
                "service, minus running credits such as feed-in revenue, stacked by cost group with the band as"
                " a whisker. A dashed line above the bars adds the *replacement reserve* — the constant monthly"
                " amount that would prefund all future replacements. Capital events themselves — the year-0 "
                "investment and the replacement purchases — are deliberately not in the bars."
            ),
            adds=(
                "The household's unit of account. NPVs decide, but budgets are monthly; this is the number to "
                "hold against rent, current bills, or a bank's affordability calculation. Excluding capital "
                "events keeps the definition consistent: purchases are financing events (shown in *Funding* and"
                " the timeline), not monthly burden — and the reserve line is the honest monthly representation"
                " of the replacements that would otherwise be invisible here."
            ),
            terms=(
                ("Recurring cost",
                 (
                     "cost that arrives continually (bills, maintenance, loan payments), as opposed to one-off "
                     "capital events."
                 )),
                ("Capital event",
                 (
                     "a purchase: the year-0 investment or a replacement. Large, rare, and typically financed "
                     "rather than paid out of a monthly budget."
                 )),
                ("Replacement reserve",
                 (
                     "the constant monthly saving that would exactly prefund all future replacements; the "
                     "budgeting equivalent of a building's maintenance reserve."
                 )),
                ("Prefund",
                 "saving ahead of a known future expense so the money exists when it is due."),
                ("Equivalent annual cost (EAC)",
                 (
                     "a lump sum converted into the constant yearly amount with the same present value; the "
                     "reserve is the replacements' EAC divided by twelve."
                 )),
                ("Whisker",
                 "the min/max band of the monthly total per year."),
                ("Affordability",
                 (
                     "whether a household's monthly income supports the monthly burden; the bank's question, "
                     "distinct from whether the project is profitable."
                 )),
            ),
            calculation=(
                (
                    "The bars are the recurring categories of the timeline divided by twelve, per year, "
                    "escalation included. The reserve is the equivalent annual cost of the replacement flows "
                    "divided by twelve; it reconciles with the replacement present value via the annuity "
                    "factor, and the year-one bar reconciles with the published monthly cost figure. Actual "
                    "replacement *years* are visible in the cash-flow timeline and *At a glance*."
                ),
            ),
        ),
        "KPIs": SectionProse(
            shows=(
                "The published key figures in one table — NPV, equivalent annual cost, monthly cost, the system "
                "cost per unit of heat, CO2, subsidies per program — each as average with its band. This table "
                "is what the machine-readable export contains."
            ),
            adds=(
                "The interface. Everything above explains; this section is what downstream tools, comparisons "
                "and archives consume — the numbers of record, stated once, with their bands."
            ),
            terms=(
                ("KPI (key performance indicator)",
                 (
                     "a headline figure chosen to summarize the evaluation; each has a fixed name and recipe so"
                     " results are comparable across runs."
                 )),
                ("System cost per unit of heat",
                 (
                     "this evaluation's entire cost (everything in the perspective: heating, PV, battery, "
                     "revenues) divided by the heat delivered. Deliberately **not** called a levelized cost of "
                     "heat: an LCOH in the literature counts heating costs only, and this system has no "
                     "heating-only attribution — so this figure runs high whenever the system contains more "
                     "than heating, and it is comparable between variants of the same system, not against "
                     "published LCOH values."
                 )),
                ("Machine-readable export",
                 (
                     "the same numbers as a data file (JSON) for programs rather than people; this table is its"
                     " human-readable mirror."
                 )),
                ("Band",
                 "the min/max world values around each average; see the primer."),
            ),
            calculation=(
                (
                    "Each KPI names its recipe: the system cost per unit of heat, for example, is the "
                    "equivalent annual cost divided by the annual heat demand — equivalently the NPV divided "
                    "by the discounted heat sum. Every KPI is an aggregation of the same timeline as every "
                    "chart, so table and charts cannot disagree."
                ),
            ),
        ),
        "Comparison": SectionProse(
            shows=(
                "This evaluation against its reference variant: the per-component NPV differences (what the "
                "switch adds, what it saves) and the cumulative discounted savings over time, whose zero "
                "crossing is the payback."
            ),
            adds=(
                "The decision frame. A single evaluation states a cost; only the comparison states whether the "
                "*change* pays — against the do-nothing (or keep-the-old-system) baseline it was computed for."
            ),
            terms=(
                ("Reference (baseline)",
                 (
                     "the counterfactual evaluation the change is measured against: keep the old system, do "
                     "nothing. Note that a reference may still contain investments of its own; shared "
                     "investments cancel in the comparison."
                 )),
                ("Variant",
                 "the evaluation containing the change under decision."),
                ("Delta",
                 "variant minus reference, per component or per year; negative deltas are savings."),
                ("Cumulative discounted savings",
                 (
                     "the running total of the yearly deltas, discounted; the curve whose zero crossing is the "
                     "payback."
                 )),
                ("Payback",
                 (
                     "the first year the accumulated savings have repaid the extra investment; reported per "
                     "world (see the primer's bands)."
                 )),
            ),
            calculation=(
                (
                    "Both variants are full evaluations under identical economic parameters; the comparison "
                    "subtracts them component by component and year by year. If the reference also invests "
                    "(keeping its own PV, say), those shared investments cancel and the comparison isolates the"
                    " actual difference between the paths."
                ),
            ),
        ),
        "NPV bridge": SectionProse(
            shows=(
                "A waterfall from the reference total (left anchor) to this variant's total (right anchor): "
                "each bar is one cost group's contribution to the difference — rightward bars add cost, "
                "leftward bars save — and the bars sum exactly to the gap between the anchors. The anchors "
                "carry the bands."
            ),
            adds=(
                "The *why* of the comparison in one picture: which groups drive the verdict and which are "
                "noise. Groups keep a fixed order and their usual colors — deliberately not red/green, because "
                "whether a cost increase is \"bad\" depends on who pays it — so two reports can be laid side by "
                "side."
            ),
            terms=(
                ("Waterfall (bridge)",
                 (
                     "a chart that walks from one total to another through the individual contributions in "
                     "between; the floating bars must sum exactly to the difference."
                 )),
                ("Anchor",
                 (
                     "the two solid end bars: the reference total and the variant total, the only actually "
                     "evaluated figures in the picture."
                 )),
                ("Contribution",
                 (
                     "one cost group's share of the difference: its present value in the variant minus in the "
                     "reference."
                 )),
                ("Cost group",
                 "the report's color-coded categories (see *Cash-flow timeline*)."),
                ("Band",
                 (
                     "shown on the anchors only: the middle bars are differences of scenarios, and the extremes"
                     " of a difference are not the difference of extremes, so drawing bands on them would "
                     "fabricate precision."
                 )),
            ),
            calculation=(
                (
                    "Each bar is the present-value difference of one cost group between the variants (folded "
                    "over components). The middle bars carry no bands for the reason above; the anchors, which "
                    "are real evaluated totals, show theirs."
                ),
            ),
        ),
        "Bank benchmark": SectionProse(
            shows=(
                "The renovation raced against a savings account. Upper panel: how far ahead the renovating "
                "household is over time, one thin line per interest rate from one to ten percent, the "
                "evaluation's own rate drawn heavy with its band — above zero, renovating is winning. Lower "
                "panel: the final advantage at the horizon as a function of the rate; where that curve crosses "
                "zero is the rate a bank account would need to beat the renovation."
            ),
            adds=(
                "The everyday framing of discounting. The interest rate every NPV depends on stops being an "
                "abstract parameter: this chart shows the verdict at *your* alternative rate, whatever it is — "
                "and the crossing rate is the renovation's internal rate of return, the single number that "
                "summarizes \"how good an investment is this\"."
            ),
            terms=(
                ("Compounding",
                 (
                     "interest earning interest: a balance grows by (1 + rate) each year, the mirror image of "
                     "discounting."
                 )),
                ("Opportunity cost",
                 (
                     "what the money could have earned in its best alternative use; the savings account here "
                     "stands in for that alternative."
                 )),
                ("Terminal advantage",
                 (
                     "the difference between the two households' wealth at the horizon, per interest rate; the "
                     "lower panel's y-axis."
                 )),
                ("Internal rate of return (IRR)",
                 (
                     "the interest rate at which the renovation and the savings account end exactly even; above"
                     " it the account wins, below it the renovation does."
                 )),
                ("Nominal, pre-tax interest",
                 (
                     "the plain quoted rate: no inflation adjustment and no tax on the interest income, because"
                     " capital-income taxation is country-specific and deliberately out of scope."
                 )),
            ),
            calculation=(
                (
                    "For each rate, the year-by-year cost difference between doing nothing and renovating (the "
                    "unspent investment on one side, the unspent bills on the other) is compounded forward like"
                    " a bank balance. The construction is exactly discounting run in reverse — the final "
                    "advantage equals (1+i)ⁿ times the NPV difference at rate i, an identity the report tests —"
                    " which is why the verdict at the evaluation's own rate provably agrees with every NPV "
                    "chart."
                ),
            ),
        ),
        "Owner statement": SectionProse(
            shows=(
                "The owner-occupier's position as a two-sided statement over the same categories as every "
                "chart. On the cash side: the investment net of subsidies, the energy bills, maintenance and "
                "replacements paid; the feed-in revenue received; the loan flows where the financed view "
                "applies. On the accounting side: the residual value of the hardware at the horizon and the "
                "anyway credit for renovation the building needed regardless — values, not payments. Each "
                "side is subtotaled before they combine into the perspective's net figure."
            ),
            adds=(
                "The decomposition of the owner rows in the perspectives table, which until here were single "
                "numbers. It also carries the honest caveat in the same place as the figure: how much of the "
                "owner's result is money that moved, and how much is book value and avoided hypothetical "
                "cost."
            ),
            terms=(
                ("Net investment",
                 (
                     "gross purchase prices minus the subsidies granted; what actually left the owner's "
                     "account in year 0."
                 )),
                ("Cash flow vs accounting credit",
                 (
                     "cash moves through an account (bills, revenue, subsidies); an accounting credit values "
                     "something without moving money (remaining equipment worth, an avoided hypothetical "
                     "expense)."
                 )),
                ("Residual value",
                 (
                     "the book worth of the installation at the horizon (see *Lifetimes*)."
                 )),
                ("Anyway credit",
                 (
                     "the avoided counterfactual renovation cost at its anyway share (see *Cash-flow "
                     "timeline*)."
                 )),
                ("Net position",
                 (
                     "both sides combined, in present value; the number the perspectives table reports for "
                     "this view."
                 )),
            ),
            calculation=(
                (
                    "The perspective's category totals, partitioned: cash categories on one side, the two "
                    "accounting-credit categories on the other. No new arithmetic — the two subtotals must "
                    "sum exactly to the perspective's NPV, and the report verifies that identity."
                ),
            ),
        ),
        "Landlord statement": SectionProse(
            shows=(
                "The landlord's business case, twice over the same numbers. The table is a two-sided "
                "statement: on one side the money that actually moves — the investment, maintenance and "
                "replacements paid; the levy income, the subsidies and the feed-in revenue received — and on "
                "the other side the accounting credits that are *not* cash: the residual value of the "
                "hardware at the horizon, and the anyway credit for renovation the building would have needed "
                "regardless. The flow picture below it is the same statement drawn as an income Sankey: "
                "income ribbons arrive from the left, expense ribbons leave to the right, and the ribbon left "
                "over *is* the net position. Solid ribbons are cash; hatched ribbons are the accounting "
                "credits — so the cash/accounting split is visible, not just stated."
            ),
            adds=(
                "The explanation the headline number cannot carry. A landlord perspective can show a strongly "
                "negative net position — a gain — while the landlord's bank account sees far less of it: part "
                "of that gain is book value and avoided hypothetical cost, not income. Separating cash from "
                "accounting is what makes the landlord row of the perspectives table honest, and it is where "
                "to check which kind of advantage the renovation actually produces."
            ),
            terms=(
                ("Levy income",
                 (
                     "the modernization levy from the tenant's side of the ledger: a permanent, legally capped "
                     "rent increase after modernization; the landlord's largest recurring income item here."
                 )),
                ("§559 cap",
                 (
                     "the legal ceiling on that increase, per square meter and month; when it binds, the levy "
                     "is set by the cap, not by the modernization cost."
                 )),
                ("Cash flow vs accounting credit",
                 (
                     "cash moves through an account (levy, subsidy, bill); an accounting credit values "
                     "something without moving money (remaining equipment worth, an avoided hypothetical "
                     "expense). Both belong in a lifecycle result; only one pays bills."
                 )),
                ("Residual value",
                 (
                     "the book worth of the installation at the horizon (see *Lifetimes*); real if the "
                     "property is sold or the equipment keeps serving, but not a payment."
                 )),
                ("Anyway credit",
                 (
                     "the avoided counterfactual renovation cost, at its anyway share (see *Cash-flow "
                     "timeline*); a comparison against a world that did not happen, by construction never a "
                     "payment."
                 )),
                ("Net position",
                 (
                     "all of it combined, in present value; negative means the renovation is advantageous for "
                     "the landlord on the stated basis."
                 )),
                ("Income Sankey (statement Sankey)",
                 (
                     "the standard flow rendering of an income statement: what comes in from the left, what "
                     "goes out to the right, and the remaining ribbon is the profit or loss. Here with solid "
                     "ribbons for cash and hatched ribbons for accounting credits."
                 )),
            ),
            calculation=(
                (
                    "Every item is the landlord's slice of the same timeline as everywhere else, under the "
                    "active allocation rules: the levy pair books the tenant's payment and the landlord's "
                    "receipt in equal size, the investment net of subsidies falls to the landlord, and the "
                    "apportionable operating costs pass through to the tenant. The statement's split is by "
                    "category, not new arithmetic: cash categories on one side, the two accounting-credit "
                    "categories on the other, and the sides sum exactly to the landlord perspective's NPV."
                ),
            ),
        ),
        "Tenant statement": SectionProse(
            shows=(
                "The tenant's side of the rented-out story as a statement: the modernization levy added to "
                "the rent, the energy bills, and the apportionable operating costs passed through by the "
                "landlord — everything the tenant pays because of the renovation, year by year in present "
                "value. The tenant receives nothing back in this ledger; where the renovation lowers the "
                "energy bill, that relief appears as a smaller energy item, not as income."
            ),
            adds=(
                "The tenant's number decomposed — and the fairness question made checkable: how much of the "
                "tenant's burden is the levy (set by law and caps) versus energy (set by physics and prices). "
                "A renovation that saves energy but carries a high levy can still be a net loss for the "
                "tenant; this is the section where that verdict is visible."
            ),
            terms=(
                ("Modernization levy",
                 (
                     "the legally capped permanent rent increase after modernization; the tenant's payment and "
                     "the landlord's income, always booked as an equal pair."
                 )),
                ("Apportionable operating costs",
                 (
                     "running costs a landlord may legally pass to the tenant; the non-apportionable rest "
                     "stays with the landlord."
                 )),
                ("Energy relief",
                 (
                     "the tenant's energy bill after the renovation is lower than before; in this statement it "
                     "shows as a smaller cost, not as a credit line."
                 )),
                ("Net position",
                 (
                     "everything combined; for a tenant this is virtually always a cost, and the comparison "
                     "that matters is against what the tenant would have paid without the renovation."
                 )),
            ),
            calculation=(
                (
                    "The tenant's slice of the same timeline under the allocation rules: the levy pair's "
                    "paying half, the energy bills of the occupant, and the apportioned share of operation. "
                    "The items sum exactly to the tenant perspective's NPV — verified — and the levy line "
                    "must mirror the landlord statement's levy income to the euro."
                ),
            ),
        ),
        "Society statement": SectionProse(
            shows=(
                "The macroeconomic position as a statement in two groups. Real resource costs and benefits: "
                "the hardware bought, the maintenance performed, the energy consumed, the residual and anyway "
                "values — things that use or preserve actual resources. And the transfers: subsidies, taxes, "
                "levies — which appear here with both their halves so their sum is visibly zero. CO2 enters "
                "as a cost at its damage-cost path, not at any market or legal price."
            ),
            adds=(
                "The proof of what \"macroeconomic\" means, instead of the word: the transfers that dominate "
                "the household stories cancel line by line, and what remains is what the renovation costs or "
                "saves the economy — plus the emissions valued at their harm. Reading this section against "
                "the owner and landlord statements shows exactly which private gains are society's transfers "
                "and which are real."
            ),
            terms=(
                ("Transfer",
                 (
                     "money moving between parties without using resources: a subsidy, a tax, a levy. In the "
                     "society view every transfer appears twice with opposite signs and sums to zero."
                 )),
                ("Real resource cost",
                 (
                     "cost that consumes actual goods or labor: hardware, installation, maintenance, fuel."
                 )),
                ("CO2 damage cost",
                 (
                     "the societal harm per tonne of CO2, from the damage-cost path in the assumptions; "
                     "distinct from any CO2 price a household pays, which is a transfer."
                 )),
                ("Macroeconomic NPV",
                 (
                     "the society perspective's bottom line: real resources plus valued emissions, transfers "
                     "cancelled."
                 )),
            ),
            calculation=(
                (
                    "The same timeline with every party included at once: transfer pairs cancel by "
                    "construction (verified as an explicit zero line in the table), resource categories keep "
                    "their values, and the emission masses from the CO2 section are multiplied by the "
                    "damage-cost path and discounted like money. The statement sums exactly to the macroeconomic "
                    "NPV."
                ),
            ),
        ),
        "Ledger heatmap": SectionProse(
            shows=(
                "The entire cash-flow ledger as a matrix: one row per cost category, one column per year, each "
                "cell the nominal sum, colored on a diverging scale (costs warm, credits cool) with a "
                "logarithmic-like ramp so small operational flows stay visible next to the year-0 investment."
            ),
            adds=(
                "Completeness at a glance, for the auditor rather than the decision-maker: a stray flow in an "
                "impossible year, a category that should be empty and is not, an escalation that flatlines — "
                "patterns that stacked bars compress away."
            ),
            terms=(
                ("Ledger",
                 (
                     "the complete list of dated, categorized cash flows behind the whole report; the timeline "
                     "seen as a bookkeeping record."
                 )),
                ("Category",
                 (
                     "the fine-grained kind of a flow (investment, energy working price, standing charge, "
                     "maintenance, residual value, …); finer than the color-coded cost groups."
                 )),
                ("Nominal",
                 "the year's actual amount, escalated, not discounted (see the primer)."),
                ("Diverging color scale",
                 (
                     "a scale with a neutral middle at zero and opposing hues for positive (cost) and negative "
                     "(credit), so sign errors are visible as wrong-colored cells."
                 )),
                ("Symlog ramp",
                 (
                     "\"symmetric logarithmic\": linear near zero, logarithmic further out, so a hundred-euro fee"
                     " and a hundred-thousand-euro purchase are both readable in one picture."
                 )),
            ),
            calculation=(
                (
                    "Cells are the raw timeline aggregated by category and year, before discounting. Row sums "
                    "reconcile with the per-category totals and column sums with the annual series — the same "
                    "numbers as everywhere, arranged for pattern-spotting."
                ),
            ),
        ),
    }

    #: The short authored lead-in of each story chapter (owner decision Q24). Chapter intros are
    #: deliberately *not* four-part `SectionProse`: a chapter is not a chart, it has nothing to
    #: show, add, define or calculate — it is the sentence that says which of the three questions
    #: the sections below it answer. Keyed by the chapter names `ReportChapters` publishes, for
    #: the same reason `SECTIONS` is keyed by section name: a renamed chapter fails loudly in
    #: `for_chapter` instead of silently rendering an unexplained heading.
    CHAPTER_INTROS: Dict[str, str] = {
        "The building": (
            "What the technology costs and does, before asking whose money it is: the priced inputs, the "
            "flows over the years, the energy physics, the emissions, and where the uncertainty sits. "
            "Everything here is on the gross basis — subsidies and the question of who pays come in the "
            "story chapters that follow."
        ),
        "Owner-occupied": (
            "The first story: the owner lives in the house. Support is subtracted, financing is real, and "
            "the questions are a household's questions — how is day one paid for, how deep does the cash "
            "position get, what does it cost per month, when is the installation owned outright."
        ),
        "Rented out": (
            "The second story: the owner rents the house out. German law lets a landlord convert part of "
            "the modernization cost into a permanent rent increase, capped by law — so the same "
            "renovation splits into a landlord's business case and a tenant's monthly reality, and the "
            "two do not sum to zero for either of them alone."
        ),
        "Society": (
            "The third story: no parties, just the economy. Subsidies, taxes and levies are transfers — "
            "one pocket to another — and cancel out; what remains is real resource use, and CO2 enters at "
            "its damage cost rather than at whatever a market or a law currently charges for it."
        ),
    }

    @classmethod
    def for_chapter(cls, name: str) -> str:
        """The authored lead-in of one chapter, by the name its heading carries (Q24).

        Raises `KeyError` rather than returning an empty string, for the same reason
        `for_section` does: a chapter heading with no lead-in is a silent editorial regression.
        """
        if name not in cls.CHAPTER_INTROS:
            raise KeyError(f"No authored intro for report chapter {name!r}")
        return cls.CHAPTER_INTROS[name]

    @classmethod
    def for_section(cls, name: str) -> SectionProse:
        """The prose of one section, by the name its heading carries.

        Raises `KeyError` with the offending name rather than returning an empty block: a section
        that renders without its explanation is the failure this module exists to prevent, and it
        would otherwise be invisible in a report that is thousands of lines long.
        """
        if name not in cls.SECTIONS:
            raise KeyError(f"No authored explanation for report section {name!r}")
        return cls.SECTIONS[name]

    @classmethod
    def to_html(cls, text: str) -> str:
        """Markdown emphasis to HTML, with the text escaped first.

        The only transformation the prose is allowed to undergo on its way into the report:
        `&`, `<` and `>` become entities, `**x**` becomes `<strong>`, `*x*` becomes `<em>` and
        `` `x` `` becomes `<code>`. Escaping runs first so a definition mentioning `<details>`
        cannot open a tag, and the emphasis patterns are non-greedy and marker-free inside, so
        they cannot span from one term to the next.
        """
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        with_strong = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        with_em = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", with_strong)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", with_em)

    @classmethod
    def to_plain_text(cls, text: str) -> str:
        """The same prose with the emphasis markers removed and nothing else changed.

        For the one renderer that cannot carry markup: the matplotlib caption of the ledger
        heatmap. Dropping the markers rather than substituting anything keeps the caption
        character-for-character comparable with the authored source.
        """
        return text.replace("**", "").replace("*", "").replace("`", "")
