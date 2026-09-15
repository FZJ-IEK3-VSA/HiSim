"""Investment schedule: capex, replacements, residual value (cost-spec-v2 §2.3).

The §2.3 "investment schedule" calculator: for one already-resolved subject it produces the
whole dated capital-expenditure picture over the observation period.

* **Year 0** (cost_spec.md §3.6 rule 1) — device + installation as one INVESTMENT entry, with
  PLANNING and REMOVAL split off when non-zero. Charged only when the perspective includes
  investment *and* the installation context says this is a new investment; a kept existing
  asset costs nothing today.
* **Replacements** (§3.6 rule 2) — one escalated re-purchase of the gross investment every
  service life, starting at the first replacement year (which is shortened for a kept asset
  by its current age). Note that replacements are counted for the replacement *reserve* and for
  embodied CO2 even under OPERATING_ONLY, where the REPLACEMENT entries themselves are dropped —
  that is what the reserve exists for (§4.2).
* **Residual value** (§3.6 rule 3) — linear write-down of the last installation to the horizon,
  emitted as revenue at year T, but only for an installation the timeline actually charged: a
  year-0 purchase or an in-horizon replacement. A kept brownfield asset that outlives the horizon
  was never bought inside the calculation period and is written down against nothing, which is
  also DIN EN 15459-1's rule (residual value of investments made within the period).

**Accumulator note (parity).** Three of the engine's running totals are fed from here:
`modernization_cost`, the embodied CO2 mass and the reserve's replacement flows. They are
returned as *ordered addend lists* rather than pre-summed, because float addition is not
associative and the orchestrator's fold order is observable in the results. The orchestrator
folds them left, in the order the former inline code did.

**Method and units.** This is the VDI 2067-1 / DIN EN 15459-1 treatment of capital cost:
re-investment at every service life within the observation period, and a *linear* (straight-line)
residual value for the fraction of the last unit's life that extends past the horizon. All
amounts are euro bands (`UncertainValue`, §3.9) in nominal euros of the year they fall in — no
discounting happens here, `calculators/aggregation.py` does that once. Years are relative to the
investment date: year 0 is the investment year, the residual value lands exactly at year T. The
module deliberately owns no *pricing* (that is `context_resolution.py`) and no *rate lookup*
(that is `escalation.py`); it decides only the dates and the amounts at those dates.

Realizes: cost_spec.md §3.6 rules 1-3, §3.8 (embodied CO2), §4.2 (operating view).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from hisim.economics.calculators.context_resolution import DeviceCosting
from hisim.economics.calculators.escalation import escalate
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue


@dataclass
class InvestmentSchedule:
    """One subject's dated capital expenditure, plus the totals it feeds (§3.6 rules 1-3).

    A return value rather than a set of side effects on the timeline: the calculator produces the
    whole schedule as data, and the orchestrator decides when each part reaches the timeline. That
    matters because one thing must be interleaved — the §4.1 anyway-cost credit is emitted between
    the year-0 entries and everything after them — and because three running totals (levy basis,
    embodied CO2, reserve flows) are folded across subjects in an order that is observable in the
    results.

    The entry lists are cost-positive euro bands except `residual_entry`, which is
    revenue-mirrored (negative). `reserve_flows` are *nominal, escalated* replacement amounts with
    their year, not discounted — `calculators/reserve.py` discounts them. They are collected even
    when `replacement_entries` is empty, which is exactly the OPERATING_ONLY case the reserve
    exists for.
    """

    #: Year-0 INVESTMENT / PLANNING / REMOVAL entries, in emit order.
    year_zero_entries: List[CashFlowEntry] = field(default_factory=list)
    #: REPLACEMENT entries, ascending by year (empty when investment is excluded).
    replacement_entries: List[CashFlowEntry] = field(default_factory=list)
    #: The RESIDUAL_VALUE entry at the horizon, when there is one.
    residual_entry: Optional[CashFlowEntry] = None
    #: (year, escalated amount) of every replacement, for the operating view's sinking fund.
    reserve_flows: List[Tuple[int, UncertainValue]] = field(default_factory=list)
    #: Contributions to the modernization-levy basis, to be folded left (see module docstring).
    modernization_cost_addends: List[UncertainValue] = field(default_factory=list)
    #: Embodied CO2 masses to be added in this order (installation first, then replacements).
    embodied_co2_addends: List[float] = field(default_factory=list)

    def add_to(self, timeline: CashFlowTimeline) -> None:
        """Appends the replacement and residual entries (year-0 entries are added earlier).

        The year-0 entries reach the timeline before the anyway-cost credit of §4.1, so the
        orchestrator adds them itself; everything after the credit is added here.

        The split exists only to preserve entry order, which is observable: `CashFlowTimeline`
        keeps insertion order and every NPV, pivot and export folds in that order. Calling this
        without having added `year_zero_entries` first would produce the same numbers up to float
        association but a differently ordered timeline, so `evaluator.build_timeline` is the one
        place that sequences the two.
        """
        timeline.extend(self.replacement_entries)
        if self.residual_entry is not None:
            timeline.add(self.residual_entry)


def build_investment_schedule(
    costing: DeviceCosting,
    gross: UncertainValue,
    asset_rate: float,
    horizon: int,
    include_investment: bool,
) -> InvestmentSchedule:
    """Builds one subject's investment schedule (§3.6 rules 1-3).

    `gross` is `costing.gross_investment` and `asset_rate` the asset class's investment
    escalation rate; both are passed in so the caller resolves them once per subject.

    The whole dated capital picture in one pass, and the single place a reviewer has to check the
    VDI 2067-1 replacement/residual convention. Replacements fall at multiples of the (rounded)
    service life, strictly *before* the horizon — a replacement due exactly at year T is not
    bought, because the observation period ends there — and each is the gross investment escalated
    to its own year, so a 20-year horizon with an 18-year life buys once more at year 18. For a
    kept brownfield asset the first replacement is pulled forward to `service_life - age`, after
    which the normal rhythm resumes. The residual value writes the *last installed* unit down
    straight-line over its service life and credits the unused remainder at year T as revenue.

    The residual is gated on an installation this schedule actually charged — `is_new_investment`
    or at least one in-horizon replacement. A kept asset whose life outlasts the horizon has no
    installation year inside the period at all: nothing was paid for it here, so writing an unused
    remainder back as revenue would credit money against a cost the timeline never carried
    (unmatched revenue in the STATUS_QUO and BROWNFIELD variants). It therefore ends the horizon
    with no residual entry, and the only installation years the write-down can see are year 0 and
    the replacement years.

    Args:
        costing: The subject's resolved costing (§3.5, §4.1) — supplies the cost blocks, the
            service life, the installation-context verdict and the provenance ids.
        gross: `costing.gross_investment` — device + installation + planning, euro band,
            cost-positive, at price-basis-year prices.
        asset_rate: Nominal annual investment price-change rate for this asset class, as a
            fraction (`escalation.investment_escalation_rate`); may be negative.
        horizon: Observation period T in years. Replacements are scheduled at years < T, the
            residual value at exactly T.
        include_investment: False under the OPERATING_ONLY perspective (§4.2). It suppresses the
            year-0, replacement and residual *entries*, but `reserve_flows` and the replacement
            embodied-CO2 masses are still collected — the sinking fund and the CO2 accounting are
            precisely what has to survive when the capital entries are dropped.

    Returns:
        An `InvestmentSchedule`; empty lists rather than `None` when nothing is due, so the caller
        can extend unconditionally.
    """
    schedule = InvestmentSchedule()
    subject = costing.subject

    # --- year-0 investment (§3.6 rule 1)
    if include_investment and costing.is_new_investment:
        schedule.year_zero_entries.append(
            CashFlowEntry(
                year=0,
                amount_in_euro=costing.device_cost + costing.installation_cost,
                category=CostCategory.INVESTMENT,
                subject=subject,
                provenance_ids=costing.provenance_ids,
            )
        )
        if costing.planning_cost.maximum > 0:
            schedule.year_zero_entries.append(
                CashFlowEntry(
                    year=0,
                    amount_in_euro=costing.planning_cost,
                    category=CostCategory.PLANNING,
                    subject=subject,
                    provenance_ids=costing.provenance_ids,
                )
            )
        if costing.removal_cost_of_replaced.maximum > 0:
            schedule.year_zero_entries.append(
                CashFlowEntry(
                    year=0,
                    amount_in_euro=costing.removal_cost_of_replaced,
                    category=CostCategory.REMOVAL,
                    subject=subject,
                    provenance_ids=costing.provenance_ids,
                )
            )
        schedule.modernization_cost_addends.extend([gross, costing.removal_cost_of_replaced])
        schedule.embodied_co2_addends.append(costing.embodied_co2_kg)

    # --- replacements (§3.6 rule 2)
    replacement_years: List[int] = []
    replacement_year = costing.first_replacement_year if not costing.is_new_investment else int(
        round(costing.service_life_years)
    )
    while replacement_year < horizon:
        if replacement_year >= 1:
            replacement_years.append(replacement_year)
        replacement_year += max(1, int(round(costing.service_life_years)))
    for repl_year in replacement_years:
        amount = escalate(gross, asset_rate, repl_year)
        schedule.reserve_flows.append((repl_year, amount))
        if include_investment:
            schedule.replacement_entries.append(
                CashFlowEntry(
                    year=repl_year,
                    amount_in_euro=amount,
                    category=CostCategory.REPLACEMENT,
                    subject=subject,
                    provenance_ids=costing.provenance_ids,
                )
            )
        schedule.embodied_co2_addends.append(costing.embodied_co2_kg)

    # --- residual value at year T (§3.6 rule 3). Only an installation this timeline actually
    # charged may be written down: EN 15459 credits a residual value for investments made within
    # the calculation period, so a kept asset whose install predates year 0 and whose replacement
    # falls beyond the horizon earns nothing here.
    charged_an_installation = costing.is_new_investment or bool(replacement_years)
    if include_investment and charged_an_installation:
        # Either the last in-horizon replacement or, when there is none, the year-0 purchase —
        # the gate above leaves no third case, so the install year is never negative.
        last_install_year = replacement_years[-1] if replacement_years else 0
        life = costing.service_life_years
        remaining_at_horizon = last_install_year + life - horizon
        if remaining_at_horizon > 0 and life > 0:
            escalated_price = escalate(gross, asset_rate, last_install_year)
            residual = escalated_price.scale(remaining_at_horizon / life)
            schedule.residual_entry = CashFlowEntry(
                year=horizon,
                amount_in_euro=residual.as_revenue(),
                category=CostCategory.RESIDUAL_VALUE,
                subject=subject,
                provenance_ids=costing.provenance_ids,
            )
    return schedule
