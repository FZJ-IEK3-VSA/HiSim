"""Investment schedule of one subject: year-0 capex, replacements, residual value (cost_spec.md §3.6 rules 1-3).

Follows VDI 2067-1 / DIN EN 15459-1: re-purchase at every service life within the horizon, and a straight-line residual
value for the part of the last unit's life past the horizon. Year 0 charges device and installation as INVESTMENT
(PLANNING and REMOVAL split off) only for a new investment; a kept existing asset costs nothing today, and a stated
purchase price replaces the database blocks in their proportions. Replacements are collected for the reserve and
embodied CO2 even under OPERATING_ONLY, where their entries are dropped (§4.2). The residual value is credited at year
T only for an installation the timeline charged. Amounts are nominal euro bands of their year; nothing is discounted
here. Running totals are returned as ordered addend lists, because the float sum order is observable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from hisim.economics.calculators.context_resolution import DeviceCosting
from hisim.economics.calculators.escalation import escalate
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue


class InvestmentDating:
    """The two dating rules: when a subject is re-bought, and how much of its last unit is left at the horizon (§3.6).

    Example::

        InvestmentDating.replacement_years(10, 10.0, 25)   # [10, 20]
        InvestmentDating.residual_fraction(20, 10.0, 25)   # 0.5

    Both :func:`build_investment_schedule` and the staged evaluator (which re-dates purchases to the year their stage
    starts) use these, so the two paths cannot replace a device in different years. Neither reads or escalates a price.
    """

    @staticmethod
    def replacement_years(first_replacement_year: int, service_life_years: float, horizon: int) -> List[int]:
        """Return the years one subject is re-purchased in, ascending (§3.6 rule 2).

        Replacements fall at the first replacement year and then every rounded service life, strictly before the
        horizon: one due exactly at year T is not bought. Years below 1 are skipped.

        Args:
            first_replacement_year: Relative year of the first re-purchase: the rounded service life for a new unit,
                `service_life - age` for a kept one.
            service_life_years: The service life; rounded to whole years and floored at one year.
            horizon: Observation period T in years.

        Returns:
            The replacement years, ascending; empty when nothing is due inside the horizon.
        """
        years: List[int] = []
        step = max(1, int(round(service_life_years)))
        year = first_replacement_year
        while year < horizon:
            if year >= 1:
                years.append(year)
            year += step
        return years

    @staticmethod
    def residual_fraction(last_install_year: int, service_life_years: float, horizon: int) -> float:
        """Return the share of the last installed unit's life that extends past the horizon (§3.6 rule 3).

        Straight-line write-down: a unit installed in `last_install_year` with life L has `last_install + L - horizon`
        years left at T, and that share of L is credited back.

        Args:
            last_install_year: Relative year of the last installation the timeline charged.
            service_life_years: The service life in years.
            horizon: Observation period T in years.

        Returns:
            A fraction in ``(0, 1]``, or ``0.0`` when nothing is left at the horizon or the service life is not
                positive.
        """
        if service_life_years <= 0:
            return 0.0
        remaining_at_horizon = last_install_year + service_life_years - horizon
        if remaining_at_horizon <= 0:
            return 0.0
        return remaining_at_horizon / service_life_years


@dataclass
class InvestmentSchedule:
    """One subject's dated capital expenditure and its contributions to the running totals (§3.6 rules 1-3).

    The orchestrator decides when each part reaches the timeline, because the §4.1 anyway credit (the avoided cost of a
    replacement that was due anyway) goes between the year-0 entries and the rest. Entries are cost-positive except the
    negative `residual_entry`. `reserve_flows` are nominal, escalated replacement amounts with their year, collected
    even when `replacement_entries` is empty (OPERATING_ONLY).
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
        """Append the replacement and residual entries to the timeline.

        The year-0 entries are added earlier by `evaluator.build_timeline`, before the anyway credit, because the
        timeline keeps insertion order and every NPV and export sums in that order.
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
    """Build one subject's investment schedule (§3.6 rules 1-3).

    Replacements fall every rounded service life strictly before the horizon, each the gross investment escalated to
    its year: a 20-year horizon with an 18-year life buys again at year 18. For a kept existing asset the first
    replacement comes at `service_life - age`. The residual value writes the last installed unit down straight-line and
    credits the remainder at year T as revenue, but only if this schedule charged an installation (a year-0 purchase or
    an in-horizon replacement); a kept asset that outlasts the horizon gets no residual.

    Args:
        costing: The subject's resolved costing (§3.5, §4.1): cost blocks, service life, installation context and
            provenance ids.
        gross: `costing.gross_investment` (device, installation and planning), a euro band at price-basis-year prices.
        asset_rate: Nominal annual investment escalation rate of the asset class as a fraction; may be negative.
        horizon: Observation period T in years; replacements fall at years < T, the residual at T.
        include_investment: False under OPERATING_ONLY (§4.2); suppresses the year-0, replacement and residual entries,
            but `reserve_flows` and the embodied CO2 masses are still collected.

    Returns:
        An `InvestmentSchedule`; lists are empty rather than None when nothing is due.
    """
    schedule = InvestmentSchedule()
    subject = costing.subject

    # --- year-0 investment (§3.6 rule 1)
    if include_investment and costing.is_new_investment and costing.purchase_override is not None:
        # A stated purchase price (a reader's quote) is the whole job: the
        # same three year-0 categories, carrying the quote in the database's proportions
        # (`DeviceCosting.purchase_blocks`), and that quote in the levy basis.
        investment, planning, removal = costing.purchase_blocks()
        for amount, category, always in (
            (investment, CostCategory.INVESTMENT, True),
            (planning, CostCategory.PLANNING, False),
            (removal, CostCategory.REMOVAL, False),
        ):
            if always or amount.maximum > 0:
                schedule.year_zero_entries.append(
                    CashFlowEntry(
                        year=0,
                        amount_in_euro=amount,
                        category=category,
                        subject=subject,
                        provenance_ids=costing.provenance_ids,
                    )
                )
        schedule.modernization_cost_addends.extend([costing.purchased_gross, removal])
        schedule.embodied_co2_addends.append(costing.embodied_co2_kg)
    elif include_investment and costing.is_new_investment:
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
    first_replacement_year = costing.first_replacement_year if not costing.is_new_investment else int(
        round(costing.service_life_years)
    )
    replacement_years = InvestmentDating.replacement_years(
        first_replacement_year, costing.service_life_years, horizon
    )
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
        fraction = InvestmentDating.residual_fraction(
            last_install_year, costing.service_life_years, horizon
        )
        if fraction > 0:
            # The last installation is the year-0 purchase when nothing was replaced since, and a
            # stated purchase price is what that purchase cost.
            purchased = (
                costing.purchased_gross
                if costing.purchase_override is not None and costing.is_new_investment and not replacement_years
                else gross
            )
            escalated_price = escalate(purchased, asset_rate, last_install_year)
            residual = escalated_price.scale(fraction)
            schedule.residual_entry = CashFlowEntry(
                year=horizon,
                amount_in_euro=residual.as_revenue(),
                category=CostCategory.RESIDUAL_VALUE,
                subject=subject,
                provenance_ids=costing.provenance_ids,
            )
    return schedule
