"""Financing application: loan flows onto the timeline (cost-spec-v2 §2.3, W3.2).

The §2.3 "financing" calculator — the *application half*. The closed-form loan mathematics
stays in `financing.py` (`loan_flows`), which is already pure; this module decides what is
financed and lays the resulting flows out: a LOAN_DISBURSEMENT that offsets the year-0 outflow,
the optional repayment grant, and the interest/principal schedule truncated at the observation
horizon (cost_spec.md §4.4).

**W3.2 — what changed and what deliberately did not.** `_apply_financing` used to be a timeline
*rewriter* with two hidden ordering dependencies: it derived the principal by re-scanning
already-emitted year-0 entries, and it read the subsidy `decisions` list that another phase had
filled in. Both are now arguments:

* `compute_year0_net_investment(timeline)` produces a typed :class:`Year0NetInvestment`, which
  the orchestrator computes and hands in. The category set is the documented, shared
  `FINANCING_YEAR0_PRINCIPAL_CATEGORIES` and the sum is folded in entry order, so the figure is
  bit-identical to the former inline scan;
* `resolve_loan_plan(plan, decisions)` applies a SOFT_LOAN scheme's LOAN_TERMS override to the
  plan before any flow is emitted.

The *ordering* is unchanged, and so is every number: this is a signature change, not a
semantic one.

.. warning::

   **§7 B3 is preserved, unfixed.** `subsidies.py` values a repayment grant on the gross measure
   cost while this module applies `repayment_grant_share` to the *principal* — they disagree
   whenever `financed_share < 1`. Currently masked because the shipped KfW rate is 0.0. Also
   note the resulting SUBSIDY entry is emitted *after* the subsidy total was closed, which is
   half of §7 W3.4. Both belong to the next package.

Realizes: cost_spec.md §4.4 (financing), §5.3 (soft loans); known defects §7 B3, W3.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from hisim.economics.calculators.categories import EngineCategoryRules
from hisim.economics.financing import FinancingPlan, loan_flows
from hisim.economics.subsidies import PayoutKind, SubsidyDecision
from hisim.economics.timeline import CashFlowEntry, CashFlowTimeline, CostCategory
from hisim.economics.uncertainty import UncertainValue


class FinancingConstants:
    """Labels of the financing flows (§4.4).

    A loan is taken out against the investment as a whole, not against any one device, so its
    flows cannot be attributed to a component and are booked under a synthetic subject instead.
    The name is published — it shows up as a row in `cash_flow_timeline.csv`, in the per-subject
    breakdowns and in the report's amortization chart — which is why it is a named constant rather
    than a literal repeated at the four emit sites below.
    """

    #: Timeline subject the loan flows are booked under (they belong to no single component).
    FINANCING_SUBJECT = "financing"


@dataclass(frozen=True)
class Year0NetInvestment:
    """The year-0 investment net of upfront support — what a loan share is taken of (§4.4).

    A one-field wrapper on purpose: the figure it carries is easy to confuse with three
    neighbours (gross investment, the levy basis, the NPV of investment), and a distinct type
    makes the W3.2 signature say which one financing actually consumes. "Net" means net of
    *upfront* subsidies only, because those are the ones that reduce what the borrower has to
    raise on the day; support arriving later does not shrink the principal.

    Units: a cost-positive euro band in year-0 money, undiscounted. It is produced by
    :func:`compute_year0_net_investment` from the finished year-0 section of the timeline and
    consumed only by :func:`build_financing_flows`.
    """

    amount: UncertainValue

    @property
    def is_financeable(self) -> bool:
        """Whether there is anything left to finance after upfront support.

        Tested on the band *maximum*, i.e. "financeable in at least the expensive world". Grants
        can in principle exceed the investment in the cheap world, and taking a loan out on a
        negative principal would emit a nonsensical disbursement; testing the maximum keeps
        "is there a loan at all" a single decision covering all three slots (§3.9: one plan,
        valued in three worlds) and errs toward financing whenever any world has something left
        to finance.
        """
        return self.amount.maximum > 0


def compute_year0_net_investment(timeline: CashFlowTimeline) -> Year0NetInvestment:
    """Sums the year-0 investment categories, net of upfront subsidies (§4.4).

    SUBSIDY belongs to the category set on purpose: subsidy entries are negative, so including
    them makes this the *net* figure. See `calculators/categories.py` for how this set differs
    from the other two and why (W3.3). The fold is in entry order, as float addition is not
    associative.

    Reading the figure off the timeline rather than re-deriving it from the cost facts is what
    guarantees the principal matches what the timeline actually charges at year 0, across all
    subjects at once — including the removal costs and planning fees that a per-device
    reconstruction is easy to forget. `evaluator.build_timeline` calls it after every subject has
    been costed and hands the result to :func:`build_financing_flows`.

    Args:
        timeline: The timeline as built so far. Only year-0 entries in
            `FINANCING_YEAR0_PRINCIPAL_CATEGORIES` are read; everything else is ignored.

    Returns:
        The net year-0 outflow as a cost-positive euro band, undiscounted. It can be zero or
        negative if upfront grants cover the investment — see `Year0NetInvestment.is_financeable`.
    """
    total = UncertainValue.exact(0.0)
    for entry in timeline.entries:
        if entry.year == 0 and entry.category in EngineCategoryRules.FINANCING_YEAR0_PRINCIPAL_CATEGORIES:
            total = total + entry.amount_in_euro
    return Year0NetInvestment(amount=total)


def resolve_loan_plan(plan: FinancingPlan, decisions: List[SubsidyDecision]) -> FinancingPlan:
    """Applies a subsidized-loan scheme's LOAN_TERMS award to the plan (§5.3).

    A soft loan (KfW-style) is a subsidy whose benefit is not cash but *terms*: a lower nominal
    interest rate, a longer term, and possibly a repayment grant (Tilgungszuschuss) that writes
    off a share of the principal. The subsidy solver decides whether the scheme applies, this
    function transcribes its award onto the financing plan, and only then is a single euro of loan
    flow computed — which is the W3.2 point that the ordering dependency between the subsidy phase
    and the financing phase is now an argument instead of a shared mutable list.

    Only an award whose scheme is the plan's `subsidized_by_scheme_id` applies. When several
    match, the last one wins — as before.

    Args:
        plan: The perspective's financing plan (financed share, rate, term, loan type).
        decisions: The subsidy decisions of this perspective, in the order the subjects were
            costed; only their `applied` awards of kind LOAN_TERMS are read.

    Returns:
        The plan itself when no award matches, otherwise a new `FinancingPlan` with the award's
        rate, term and repayment-grant share substituted in. All three fields follow one rule,
        `is not None`: a field the award states overrides the plan's, a field it leaves unset
        inherits the plan's. The distinction is not cosmetic — an award's *stated* 0.0 % interest
        is the entire point of a zero-interest soft loan and must not fall back to the plan's
        market rate, and an award that says nothing about a Tilgungszuschuss must not erase a
        repayment grant the plan already carried.
    """
    loan_plan = plan
    for decision in decisions:
        for award in decision.applied:
            if award.payout_kind == PayoutKind.LOAN_TERMS and award.scheme_id == plan.subsidized_by_scheme_id:
                loan_plan = FinancingPlan(
                    financed_share=plan.financed_share,
                    nominal_interest_rate=(
                        award.loan_interest_rate
                        if award.loan_interest_rate is not None
                        else plan.nominal_interest_rate
                    ),
                    term_in_years=(
                        award.loan_term_in_years if award.loan_term_in_years is not None else plan.term_in_years
                    ),
                    type=plan.type,
                    subsidized_by_scheme_id=plan.subsidized_by_scheme_id,
                    repayment_grant_share=(
                        award.loan_repayment_grant_share
                        if award.loan_repayment_grant_share is not None
                        else plan.repayment_grant_share
                    ),
                )
    return loan_plan


def build_financing_flows(
    loan_plan: FinancingPlan,
    year0_net: Year0NetInvestment,
    horizon: int,
) -> List[CashFlowEntry]:
    """Loan flows replacing (a share of) the year-0 outflow (§4.4).

    Nothing is emitted when there is no net investment left to finance. The schedule stops at
    the observation horizon, so a term longer than the horizon leaves the remaining debt
    implicitly outstanding (unchanged behavior).

    The layout half of financing: the closed-form annuity or interest-only schedule comes from
    `financing.loan_flows`, and this function decides what it is applied to (a share of the year-0
    net investment) and where the results land. Note what financing does and does not move — it
    barely changes the NPV, only via the spread between the loan rate and the discount rate, but
    it transforms the nominal annual series completely, which is exactly the "can I afford this"
    question §4.3 keeps separate from "is this worth it".

    Args:
        loan_plan: The plan after :func:`resolve_loan_plan` — financed share, nominal rate, term,
            loan type and any repayment-grant share.
        year0_net: The year-0 investment net of upfront subsidies, from
            :func:`compute_year0_net_investment`.
        horizon: Observation period T in years; schedule rows beyond it are dropped.

    Returns:
        A LOAN_DISBURSEMENT at year 0 (revenue-mirrored, i.e. negative, offsetting the year-0
        outflow), optionally a negative SUBSIDY entry for the repayment grant (see the module's
        §7 B3 warning), and cost-positive LOAN_INTEREST / LOAN_PRINCIPAL entries per year, each
        emitted only when non-zero. All nominal euros of their own year, undiscounted.
    """
    if not year0_net.is_financeable:
        return []
    entries: List[CashFlowEntry] = []
    principal = year0_net.amount.scale(loan_plan.financed_share)
    disbursement, schedule = loan_flows(loan_plan, principal)
    entries.append(
        CashFlowEntry(
            year=0,
            amount_in_euro=disbursement.as_revenue(),
            category=CostCategory.LOAN_DISBURSEMENT,
            subject=FinancingConstants.FINANCING_SUBJECT,
        )
    )
    if loan_plan.repayment_grant_share > 0:
        # §7 B3: the solver valued this grant on the gross measure cost, not on the principal.
        grant = principal.scale(loan_plan.repayment_grant_share)
        entries.append(
            CashFlowEntry(
                year=0,
                amount_in_euro=grant.as_revenue(),
                category=CostCategory.SUBSIDY,
                subject=FinancingConstants.FINANCING_SUBJECT,
                subsidy_scheme_id=loan_plan.subsidized_by_scheme_id,
            )
        )
    for year, interest, repayment in schedule:
        if year > horizon:
            break
        if interest.maximum:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=interest,
                    category=CostCategory.LOAN_INTEREST,
                    subject=FinancingConstants.FINANCING_SUBJECT,
                )
            )
        if repayment.maximum:
            entries.append(
                CashFlowEntry(
                    year=year,
                    amount_in_euro=repayment,
                    category=CostCategory.LOAN_PRINCIPAL,
                    subject=FinancingConstants.FINANCING_SUBJECT,
                )
            )
    return entries
