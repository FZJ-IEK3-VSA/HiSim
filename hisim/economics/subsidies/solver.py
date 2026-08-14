"""Award computation and the cumulation solver (cost_spec.md §5.4-§5.5).

Turns assessed schemes into concrete `SubsidyAward`s and picks the best admissible
combination: eligible-cost bases, per-slot cap arithmetic (see D20/D28 — the per-slot
choice is envelope semantics), `_combination_awards`, the support-value objective and
`solve_cumulation`. Split out of the former single-module `subsidies.py` (PR-3 review);
the package `__init__` re-exports everything.
"""

from __future__ import annotations

from typing import (
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

from hisim.economics.uncertainty import UncertainValue

from hisim.economics.subsidies.assessment import (
    EligibilityStatus,
    MeasureForSubsidy,
    SubsidyAward,
    SubsidyContext,
    SubsidyDecision,
    assess_schemes,
)
from hisim.economics.subsidies.catalog import (
    BenefitKind,
    LoanTermsBenefit,
    LumpSumBenefit,
    OperationalBenefit,
    PayoutKind,
    PerUnitBenefit,
    ReducedVatBenefit,
    ShareBenefit,
    SubsidyCatalog,
    SubsidyScheme,
    TaxCreditBenefit,
)
from hisim.economics.subsidies.context import SubsidyDataError

class CumulationLimits:
    """Bounds the exponential cumulation solver is guarded by (§5.4).

    The solver enumerates *every* subset of the schemes that apply to one measure, so its cost is
    2^n in that number; this class is the one place that bound is stated. It exists as a named
    constant rather than a magic number because the guard's failure mode is a policy decision — a
    catalog that trips it is treated as a data defect (see :func:`_check_cumulation_size`) rather
    than silently truncated, since truncation would understate the support a user is entitled to.
    """

    #: Upper bound on the number of schemes handed to a subset enumeration in the cumulation
    #: solver (§5.4). The solver is exponential in that number, so a catalog that made many
    #: schemes apply to one measure would hang instead of returning a wrong answer. Real
    #: catalogs stay far below the limit (the shipped DE catalog holds nine schemes in total).
    MAX_CUMULATION_SCHEMES = 16



def _eligible_cost_basis(
    scheme: SubsidyScheme, measure: MeasureForSubsidy, context: SubsidyContext
) -> Tuple[UncertainValue, Dict[str, bool]]:
    """Eligible cost per slot, with per-slot cap-binding flags (§3.9, §5.4).

    Turns the measure's gross year-0 cost into the basis *this* scheme computes on, applying the
    four dials of :class:`EligibleCostSpec` in a fixed order: sum the counted categories, strip VAT
    if the scheme's basis is NET, prorate to the residential share for a residential-only programme
    in a mixed-use building, then clamp to the per-dwelling-unit cap. The order matters — capping
    last means the ceiling is compared against the already prorated, already net figure, which is
    how the directives read.

    Both proration and capping degrade gracefully in the direction of *not* inventing support: an
    unanswered residential share leaves the basis unprorated, and a scheme with no cap list is
    uncapped. The cap is applied per slot (`clamp_upper`), so it can bind in the HIGH-cost world
    and not in LOW; the returned flags record exactly that and end up in the audit trail and in the
    input-audit table, because "the cap bound" is usually the single most important thing to know
    about a subsidy result.

    Args:
        scheme: The scheme whose eligible-cost rules apply.
        measure: The measure, supplying the year-0 gross cost per category and the VAT rate.
        context: The building context, read for the residential share and the dwelling-unit count.

    Returns:
        The eligible-cost band in euro, and a ``{"low"/"best_estimate"/"high": bool}`` map saying in which
        slots the cap was binding (computed *before* clamping).
    """
    basis = UncertainValue.sum(
        measure.cost_by_category.get(category, UncertainValue.exact(0.0))
        for category in scheme.eligible_cost.categories
    )
    if scheme.eligible_cost.basis == "NET" and measure.vat_rate > 0:
        basis = basis.scale(1.0 / (1.0 + measure.vat_rate))
    if scheme.eligible_cost.proration == "RESIDENTIAL_SHARE":
        share = context.building.residential_share
        if share is not None:
            basis = basis.scale(share)
    cap = scheme.eligible_cost.cap_for_units(context.building.dwelling_units)
    binding = {"low": False, "best_estimate": False, "high": False}
    if cap is not None:
        binding = {
            "low": basis.minimum > cap,
            "best_estimate": basis.best_estimate > cap,
            "high": basis.maximum > cap,
        }
        basis = basis.clamp_upper(UncertainValue.exact(cap))
    return basis, binding



def _share_benefit(scheme: SubsidyScheme) -> ShareBenefit:
    """The share payload of a SHARE_OF_ELIGIBLE_COST / BONUS_SHARE scheme.

    The pairing of kind and payload type is enforced by `SubsidyScheme.__post_init__`, so this
    is a narrowing helper for the type checker, not a runtime check.
    """
    assert isinstance(scheme.benefit, ShareBenefit)
    return scheme.benefit


#: The three cap ratios of :func:`_overall_cap_ratios`, in slot order LOW, BEST_ESTIMATE, HIGH.
CapRatios = Tuple[float, float, float]


def _overall_cap_ratios(total_upfront: UncertainValue, cap: UncertainValue) -> CapRatios:
    """How much of the upfront support survives the overall cap, **per slot** (§5.4, §7 B12).

    Each slot is a coherent world: in the LOW-cost world both the support and the state-aid cap
    (a share of that world's gross cost) are smaller, so the binding ratio is a per-slot figure.
    Scaling every slot by the BEST_ESTIMATE-slot ratio — what this did before — let the LOW/HIGH-world
    support exceed the same world's cap, and with wide cost bands even its gross cost.
    """

    def ratio(total_slot: float, cap_slot: float) -> float:
        if total_slot <= 0.0:
            return 1.0
        return min(1.0, max(0.0, cap_slot) / total_slot)

    return (
        ratio(total_upfront.minimum, cap.minimum),
        ratio(total_upfront.best_estimate, cap.best_estimate),
        ratio(total_upfront.maximum, cap.maximum),
    )


def _scaled_to_cap(amount: UncertainValue, ratios: CapRatios) -> UncertainValue:
    """Applies the per-slot cap ratios to one award, keeping the band ordered (§3.9, §7 B12).

    Per-slot ratios are *not* monotone across the slots: the cap grows with the gross cost while
    the support grows with the eligible-cost bases, and a scheme paying a statutory lump sum does
    not grow at all. Where the ratio falls from one slot to the next, a plain slot-wise product
    would order an award's band the wrong way round (an exact lump sum keeping its full amount in
    LOW but being cut in BEST_ESTIMATE), which is not representable as an `UncertainValue`.

    The award is therefore capped from the HIGH slot downwards: each slot takes the smaller of
    its own capped value and the slot above it. That keeps three properties at once —

    * ``minimum <= best_estimate <= maximum`` by construction (no reliance on snapping tolerance);
    * per-slot support never exceeds the per-slot cap, because every slot is at most its own
      ``amount_slot * ratio_slot`` and those sum to at most the slot's cap;
    * no award exceeds its own eligible basis, because the ratios are at most 1 and the
      downward clamp only ever lowers a slot.

    The price is a slot that gives up support it could have received in isolation (the LOW world
    in the example above), which is the conservative direction: the cap is never overrun. Where
    the ratios *are* monotone — always, when the cost bands are degenerate, as in every shipped
    catalog and worked example — the result is exactly the plain per-slot product.
    """
    low_ratio, best_estimate_ratio, high_ratio = ratios
    maximum = amount.maximum * high_ratio
    best_estimate = min(amount.best_estimate * best_estimate_ratio, maximum)
    minimum = min(amount.minimum * low_ratio, best_estimate)
    return UncertainValue(best_estimate=best_estimate, minimum=minimum, maximum=maximum)


def _combination_awards(
    schemes: List[SubsidyScheme],
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    overall_cap_share: Optional[float],
) -> List[SubsidyAward]:
    """Values one admissible combination in all three slots (§5.4).

    The valuation kernel: given a set of schemes that may legally be combined, it produces one
    :class:`SubsidyAward` per scheme with the amounts each would actually pay. It is called once
    per enumerated subset by the solver, so it must be free of side effects on its inputs and is
    the only place the cumulation *rules* — group stacking, the combined rate cap, the state-aid
    ceiling — turn into euros.

    Three stages, in this order. **Share kinds** (base rates and bonuses) are grouped by
    ``cumulation_group``: their rates are summed, the sum is limited by the smallest
    ``combined_rate_cap`` declared in the group (the BEG's 70 %), and the limit is distributed back
    over the members *proportionally* — so a capped stack reports each scheme's pro-rata share
    rather than dropping the last bonus, which is what makes the composition chart add up. Note
    that grouping is by group label only: schemes with no group share the ``None`` group and stack
    together. **Non-share kinds** are valued individually against their own eligible-cost basis —
    lump sums and per-unit amounts are clamped to it, tax credits are spread over their schedule,
    loan terms and reduced VAT carry parameters rather than amounts. **The overall cap** finally
    bounds the total *upfront* support (grants and clamped lump sums; not tax-credit schedules, not
    loans) by the country-level state-aid share of the gross cost, per slot.

    Args:
        schemes: One admissible combination — assumed already checked against ``excludes``.
        measure: The measure being funded, supplying the cost basis and its size.
        context: The building context the eligible-cost rules read.
        overall_cap_share: The catalog's state-aid ceiling as a share of gross cost, or ``None``
            for no ceiling (the shipped DE catalog declares none).

    Returns:
        One award per scheme, amounts in nominal year-0 euro per slot. Awards may legitimately be
        zero-valued (a loan or VAT award carries terms only).
    """
    awards: List[SubsidyAward] = []
    # Share-based schemes stack additively per cumulation group, capped by combined_rate_cap.
    share_groups: Dict[Optional[str], List[SubsidyScheme]] = {}
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            share_groups.setdefault(scheme.cumulation_group, []).append(scheme)
    for _group, group_schemes in share_groups.items():
        rates = [_share_benefit(scheme).rate for scheme in group_schemes]
        total_rate = sum(rates)
        rate_caps = [scheme.combined_rate_cap for scheme in group_schemes if scheme.combined_rate_cap is not None]
        capped_rate = min([total_rate] + rate_caps)
        scale_down = capped_rate / total_rate if total_rate > 0 else 0.0
        for scheme, scheme_rate in zip(group_schemes, rates):
            basis, binding = _eligible_cost_basis(scheme, measure, context)
            rate = scheme_rate * scale_down
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=basis.scale(rate),
                    caps_binding_per_slot=binding,
                )
            )
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            continue
        basis, binding = _eligible_cost_basis(scheme, measure, context)
        benefit = scheme.benefit
        if isinstance(benefit, LumpSumBenefit):
            # A grant never exceeds the cost it funds, so the lump sum is clamped to the eligible
            # basis — unless the scheme declares no eligible-cost categories at all, which is the
            # explicit way of saying "this amount is unconditional" (an empty basis would otherwise
            # clamp every lump sum to zero).
            amount = UncertainValue.exact(benefit.amount)
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis) if scheme.eligible_cost.categories else amount,
                    caps_binding_per_slot=binding,
                )
            )
        elif isinstance(benefit, PerUnitBenefit):
            amount = UncertainValue.exact(benefit.amount * measure.facts.size)
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis),
                    caps_binding_per_slot=binding,
                )
            )
        elif isinstance(benefit, TaxCreditBenefit):
            total = basis.scale(benefit.rate)
            schedule = [total.scale(share) for share in benefit.schedule_shares()]
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.TAX_CREDIT_SCHEDULE,
                    schedule_amounts=schedule,
                    caps_binding_per_slot=binding,
                )
            )
        elif isinstance(benefit, ReducedVatBenefit):
            # §7 B7: no consumer reads this award — typed, but deliberately left unwired.
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.VAT_REDUCTION,
                    reduced_vat_rate=benefit.vat_rate,
                )
            )
        elif isinstance(benefit, LoanTermsBenefit):
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.LOAN_TERMS,
                    loan_interest_rate=benefit.interest_rate,
                    loan_term_in_years=benefit.term,
                    loan_repayment_grant_share=benefit.repayment_grant_rate,
                )
            )
        elif isinstance(benefit, OperationalBenefit):
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=PayoutKind.OPERATIONAL,
                    operational_rate_per_kwh=benefit.rate_per_kwh,
                    operational_carrier=benefit.carrier,
                    operational_duration_years=benefit.duration_years,
                )
            )
    # EU state-aid overall cap: bounds total *upfront* support per measure (§5.4), per slot.
    if overall_cap_share is not None:
        gross = UncertainValue.sum(measure.cost_by_category.values())
        cap = gross.scale(overall_cap_share)
        total_upfront = UncertainValue.sum(award.upfront_amount for award in awards)
        ratios = _overall_cap_ratios(total_upfront, cap)
        if any(ratio < 1.0 for ratio in ratios):
            for award in awards:
                award.upfront_amount = _scaled_to_cap(award.upfront_amount, ratios)
    return awards


def _support_value(
    awards: List[SubsidyAward],
    measure: MeasureForSubsidy,
    discount: Callable[[int], float],
    slot_getter: Callable[[UncertainValue], float],
) -> float:
    """Discounted value of a combination's support in one slot (solver objective).

    The scalar the cumulation solver maximizes: the present value, to the applicant, of everything a
    combination pays — grants at year 0 undiscounted, tax-credit installments discounted by their
    year, operational per-kWh payments discounted over their duration, plus a soft loan's repayment
    grant. Discounting is what makes the comparison meaningful at all, since §5.1's mechanisms pay
    at very different times: a 20 % credit over ten years is not worth a 20 % grant today.

    Two details a reviewer should note. Operational support falls back from *sold* to *bought*
    energy for the carrier, so a heat-generation premium on consumed energy is valued even though
    the field is named after feed-in. And the repayment grant is valued on the measure's **gross**
    cost here while ``calculators/financing_application.py`` applies the same share to the loan
    principal — §7 B3, preserved unfixed and currently masked by the shipped KfW rate of 0.0.

    Args:
        awards: The valued awards of one combination.
        measure: The measure, read for the annual energy an OPERATIONAL award is paid on and for
            the gross cost a repayment grant is valued on.
        discount: Year → discount factor, supplied by the caller (``EconomicParameters``).
        slot_getter: Picks the slot to value in; see :func:`solve_cumulation` for why the LOW slot
            reads the band's *maximum*.

    Returns:
        The discounted support in euro — a positive number, larger is better for the applicant.
    """
    value = 0.0
    for award in awards:
        value += slot_getter(award.upfront_amount)
        for offset, amount in enumerate(award.schedule_amounts, start=1):
            value += slot_getter(amount) * discount(offset)
        if award.payout_kind == PayoutKind.OPERATIONAL and award.operational_carrier is not None:
            energy = measure.annual_energy_sold_in_kwh.get(
                award.operational_carrier, 0.0
            ) or measure.annual_energy_bought_in_kwh.get(award.operational_carrier, 0.0)
            for year in range(1, award.operational_duration_years + 1):
                value += award.operational_rate_per_kwh * energy * discount(year)
        if award.loan_repayment_grant_share:
            gross = slot_getter(UncertainValue.sum(measure.cost_by_category.values()))
            value += gross * award.loan_repayment_grant_share
    return value


def _check_cumulation_size(count: int, what: str) -> None:
    """Guards the 2^n subset enumerations of the cumulation solver.

    Args:
        count: Number of schemes about to be enumerated.
        what: Human-readable name of that set, used in the error message.

    Raises:
        SubsidyDataError: If count exceeds MAX_CUMULATION_SCHEMES.
    """
    if count > CumulationLimits.MAX_CUMULATION_SCHEMES:
        raise SubsidyDataError(
            f"{count} {what} exceeds the cumulation solver limit of "
            f"{CumulationLimits.MAX_CUMULATION_SCHEMES}. "
            "The solver enumerates every subset of them, so a set this large would not finish "
            "in reasonable time. Narrow the catalog's candidate schemes for this measure "
            "(applies_to_asset_classes, measure kinds, region, validity years) or split it."
        )


def solve_cumulation(
    catalog: SubsidyCatalog,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    year: int,
    discount: Callable[[int], float],
    admits: Optional[Callable[[str], bool]] = None,
) -> SubsidyDecision:
    """Enumerates admissible combinations of ELIGIBLE schemes and picks the best (§5.4).

    The decision is made on the BEST_ESTIMATE slot; the chosen combination is then valued in all
    three slots. UNDETERMINED schemes are excluded but reported with the optimistic upper
    bound they could unlock (§5.7).

    `admits` restricts the candidate set *before* the optimization (§5.5 subsidy modes; §7 B5).
    It is a plain predicate on scheme ids rather than a `SubsidyMode` so this module keeps no
    dependency on the perspective types. Filtering afterwards — what the evaluator used to do —
    left ONLY/EXCLUDE perspectives with the remainder of a combination that was optimal for a
    different (unrestricted) candidate set, which is not the best admissible combination.

    Both enumerations are exponential in the number of schemes and are therefore capped at
    MAX_CUMULATION_SCHEMES: more than that many eligible schemes (or, for the optimistic
    bound, eligible plus undetermined ones) is treated as a catalog defect rather than
    silently truncated, because truncating would understate the support.

    **What is optimized, over what, and how ties break.** The objective is
    :func:`_support_value` — the discounted euro value of a combination to the applicant, maximized
    (the applicant is assumed to claim what is available to them, §5.4). The candidate set is the
    power set of the ELIGIBLE schemes for this one measure: all 2^n subsets are enumerated in
    increasing bitmask order, the ones violating an ``excludes`` relation are skipped, and each
    survivor is fully valued through :func:`_combination_awards` — no greedy or incremental
    shortcut, because caps and exclusions make the objective non-additive. Note the empty subset is
    included, so "no subsidy at all" is always an admissible answer. A candidate replaces the
    incumbent only if it beats it by more than 1e-9 euro, which makes the tie-break *first one
    wins* in enumeration order: since the bits index the eligible list, which is in catalog file
    order, an exact tie is resolved toward the combination using the fewer / earlier-listed
    schemes. That is what makes the result reproducible across runs rather than dependent on set
    iteration order.

    **The per-slot report.** The same enumeration also tracks the best combination in the LOW and
    HIGH slots, and ``other_slot_optimal_combination`` names it where it differs from the chosen
    one — the honest disclosure that the plan is optimal for the best-estimate world only. The slot
    getters map LOW to the band's *maximum* and HIGH to its *minimum*, matching the mirroring that
    turns support into a revenue-type cash flow (`as_revenue`): in the optimistic slot the most
    support arrives.

    Args:
        catalog: The country catalog.
        measure: The single measure being funded; the solver has no cross-measure view.
        context: The applicant/building answers.
        year: The year scheme validity is tested against (the price basis year in production).
        discount: Year → discount factor used to compare payout timings.
        admits: Optional scheme-id predicate implementing the perspective's subsidy mode.

    Returns:
        The full :class:`SubsidyDecision`: the chosen combination's awards, plus rejected,
        undetermined, the optimistic bound and the per-slot alternatives.

    Raises:
        SubsidyDataError: If either scheme set exceeds MAX_CUMULATION_SCHEMES.
    """
    assessments = assess_schemes(catalog, measure, context, year, admits)
    eligible = [assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.ELIGIBLE]
    decision = SubsidyDecision(measure_subject=measure.subject)
    for assessment in assessments:
        if assessment.status == EligibilityStatus.INELIGIBLE:
            decision.rejected.append({"scheme_id": assessment.scheme.id, "reason": assessment.rejected_reason})
        elif assessment.status == EligibilityStatus.UNDETERMINED:
            decision.undetermined.append(
                {"scheme_id": assessment.scheme.id, "missing_fields": assessment.missing_fields}
            )

    def admissible(combination: List[SubsidyScheme]) -> bool:
        """Whether the combination violates no `excludes` relation.

        Declaring the exclusion on one side is enough: the check asks every member whether any
        other member is on its exclusion list, so `excludes` acts symmetrically even though the
        catalog states it once (the DE catalog states it on both sides anyway).
        """
        ids = {scheme.id for scheme in combination}
        for scheme in combination:
            if ids & set(scheme.excludes):
                return False
        return True

    # Enumerate subsets (scheme sets are small, typically < 10 per measure).
    _check_cumulation_size(len(eligible), "eligible schemes")
    best: Optional[Tuple[float, List[SubsidyScheme], List[SubsidyAward]]] = None
    best_per_slot: Dict[str, Optional[str]] = {}
    slot_getters = {
        "low": lambda value: value.maximum,  # optimistic world: max support
        "best_estimate": lambda value: value.best_estimate,
        "high": lambda value: value.minimum,
    }
    best_by_slot: Dict[str, Tuple[float, str]] = {}
    count = len(eligible)
    for mask in range(1 << count):
        combination = [eligible[index] for index in range(count) if mask & (1 << index)]
        if not admissible(combination):
            continue
        awards = _combination_awards(combination, measure, context, catalog.overall_cap_share)
        for slot_name, getter in slot_getters.items():
            slot_value = _support_value(awards, measure, discount, getter)
            key = "|".join(sorted(scheme.id for scheme in combination)) or "<none>"
            if slot_name not in best_by_slot or slot_value > best_by_slot[slot_name][0] + 1e-9:
                best_by_slot[slot_name] = (slot_value, key)
        best_estimate_value = _support_value(awards, measure, discount, slot_getters["best_estimate"])
        if best is None or best_estimate_value > best[0] + 1e-9:
            best = (best_estimate_value, combination, awards)
    if best is None:
        best = (0.0, [], [])
    chosen_key = "|".join(sorted(scheme.id for scheme in best[1])) or "<none>"
    for slot_name in ("low", "high"):
        slot_best = best_by_slot.get(slot_name)
        best_per_slot[slot_name] = slot_best[1] if slot_best and slot_best[1] != chosen_key else None
    decision.applied = best[2]
    decision.other_slot_optimal_combination = best_per_slot
    # Optimistic upper bound over undetermined schemes: value if they all were eligible too.
    # Re-solving over eligible + undetermined (rather than adding the undetermined schemes' values)
    # is necessary because exclusions and caps make the best combination change, not just grow. The
    # reported figure is the *increment* over the chosen combination on the BEST_ESTIMATE slot — "answering
    # these questions could unlock up to X on top of what you already get" (§5.7) — floored at 0,
    # since an undetermined scheme can never make the applicant worse off.
    if decision.undetermined:
        undetermined_schemes = [
            assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.UNDETERMINED
        ]
        optimistic = eligible + undetermined_schemes
        best_optimistic = 0.0
        opt_count = len(optimistic)
        _check_cumulation_size(opt_count, "eligible and undetermined schemes")
        for mask in range(1 << opt_count):
            combination = [optimistic[index] for index in range(opt_count) if mask & (1 << index)]
            if not admissible(combination):
                continue
            awards = _combination_awards(combination, measure, context, catalog.overall_cap_share)
            best_optimistic = max(
                best_optimistic, _support_value(awards, measure, discount, slot_getters["best_estimate"])
            )
        decision.undetermined_upper_bound_in_euro = max(0.0, best_optimistic - best[0])
    return decision


