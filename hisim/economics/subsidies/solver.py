"""Award computation and the cumulation solver (cost_spec.md §5.4-§5.5).

Cumulation is combining several subsidy schemes for one measure under their stacking rules. This module computes each
scheme's eligible cost and award, applies the caps per band slot (minimum, best estimate, maximum), and picks the
admissible combination worth most to the applicant (`solve_cumulation`). It also values each scheme alone
(`scheme_maximum`).
"""

from __future__ import annotations

from dataclasses import replace
from typing import (
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Optional,
    Tuple,
)

from hisim.economics.uncertainty import UncertainValue

from hisim.economics.subsidies.assessment import (
    EligibilityStatus,
    MeasureForSubsidy,
    SchemeAssessment,
    SchemeMaximum,
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
    TieredPerUnitBenefit,
)
from hisim.economics.subsidies.context import SubsidyDataError


class CumulationLimits:
    """Size limit of the cumulation solver's subset enumeration (§5.4).

    The solver enumerates every subset of the schemes that apply to one measure, so its cost is 2^n. A catalog that
    exceeds the limit is treated as a data defect (:func:`_check_cumulation_size`) rather than truncated, since
    truncation would understate the support.
    """

    #: Upper bound on the number of schemes handed to a subset enumeration in the cumulation
    #: solver (§5.4). The solver is exponential in that number, so a catalog that made many
    #: schemes apply to one measure would hang instead of returning a wrong answer. Real
    #: catalogs stay far below the limit (the shipped DE catalog holds nine schemes in total).
    MAX_CUMULATION_SCHEMES = 16


def _eligible_cost_basis(
    scheme: SubsidyScheme, measure: MeasureForSubsidy, context: SubsidyContext
) -> Tuple[UncertainValue, Dict[str, bool]]:
    """Return the eligible cost of a scheme for a measure per slot, with per-slot cap-binding flags (§3.9, §5.4).

    The eligible cost is the part of the measure's year-0 gross cost a scheme pays on. The steps run in this order: sum
    the counted categories, strip VAT if the scheme's basis is NET, prorate to the residential share for a
    residential-only programme in a mixed-use building, then clamp to the per-dwelling-unit cap. An unanswered
    residential share leaves the basis unprorated, and a scheme with no cap is uncapped. The cap applies per slot, so
    it can bind in the HIGH slot and not in LOW.

    Args:
        scheme: The scheme whose eligible-cost rules apply.
        measure: The measure, supplying the year-0 gross cost per category and the VAT rate.
        context: The building context, read for the residential share and the dwelling-unit count.

    Returns:
        The eligible-cost band in euro, and a ``{"low"/"best_estimate"/"high": bool}`` map saying in which slots the
            cap was binding (computed before clamping).
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
    """Return the share payload of a SHARE_OF_ELIGIBLE_COST or BONUS_SHARE scheme.

    `SubsidyScheme.__post_init__` already enforces the pairing of kind and payload, so this only narrows the type for
    the type checker.
    """
    assert isinstance(scheme.benefit, ShareBenefit)
    return scheme.benefit


#: The three cap ratios of :func:`_overall_cap_ratios`, in slot order LOW, BEST_ESTIMATE, HIGH.
CapRatios = Tuple[float, float, float]


def _overall_cap_ratios(total_upfront: UncertainValue, cap: UncertainValue) -> CapRatios:
    """Return how much of the upfront support survives the overall state-aid cap, per slot (§5.4).

    Each slot is a coherent world: in the LOW world both the support and the cap (a share of that world's gross cost)
    are smaller, so the ratio differs per slot. Using one ratio for all slots would let LOW or HIGH support exceed that
    world's cap.
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
    """Apply the per-slot cap ratios to one award while keeping its band ordered (§3.9).

    The ratios need not be monotone across slots: the cap grows with the gross cost, while a statutory lump sum does
    not grow at all. A plain slot-wise product could then order the band the wrong way round. The award is therefore
    capped from the HIGH slot downwards, each slot taking the smaller of its own capped value and the slot above. This
    keeps ``minimum <= best_estimate <= maximum``, never overruns a slot's cap, and never exceeds the award's eligible
    basis. A slot may give up support it would have received alone, which is the conservative direction. With monotone
    ratios, as in every shipped catalog, the result equals the plain per-slot product.
    """
    low_ratio, best_estimate_ratio, high_ratio = ratios
    maximum = amount.maximum * high_ratio
    best_estimate = min(amount.best_estimate * best_estimate_ratio, maximum)
    minimum = min(amount.minimum * low_ratio, best_estimate)
    return UncertainValue(best_estimate=best_estimate, minimum=minimum, maximum=maximum)


def _apply_overall_cap(award: SubsidyAward, ratios: CapRatios) -> None:
    """Scale one award to the state-aid cap and keep its stated rate consistent with the capped amount.

    An award's recorded factors must multiply to the euros it pays, so a report can print `rate x basis = amount`. The
    new rate is read off the best-estimate slot (`amount / basis`), and the pre-cap rate is kept in
    `benefit_rate_before_overall_cap`. A tax-credit schedule keeps its rate, because it pays through
    `schedule_amounts`, which this cap does not touch. An award with no basis, or a zero basis, has no rate to
    re-derive.

    Args:
        award: The award to cap; modified in place.
        ratios: The per-slot cap ratios from :func:`_overall_cap_ratios`.
    """
    scaled = _scaled_to_cap(award.upfront_amount, ratios)
    basis = award.eligible_basis_in_euro
    if (
        award.benefit_rate is not None
        and basis is not None
        and basis.best_estimate > 0.0
        and award.upfront_amount.best_estimate > 0.0
        and scaled.best_estimate < award.upfront_amount.best_estimate
    ):
        award.benefit_rate_before_overall_cap = award.benefit_rate
        award.benefit_rate = scaled.best_estimate / basis.best_estimate
    award.upfront_amount = scaled


def _combination_awards(
    schemes: List[SubsidyScheme],
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    overall_cap_share: Optional[float],
) -> List[SubsidyAward]:
    """Value one admissible combination of schemes in all three slots (§5.4).

    Called once per enumerated subset by the solver, without side effects on its inputs. Three stages, in order:

    - Share kinds (base rates and bonuses) are grouped by ``cumulation_group``. Their rates are summed, limited by the
      smallest ``combined_rate_cap`` in the group (the BEG's 70 %), and the limit is distributed back over the members
      proportionally. Schemes with no group share the ``None`` group and stack together.
    - Other kinds are valued against their own eligible cost: lump sums and per-unit amounts are clamped to it, tax
      credits are spread over their schedule, and loan terms and reduced VAT carry parameters rather than amounts.
    - The overall cap limits the total upfront support (grants and lump sums, not tax-credit schedules or loans) to the
      catalog's state-aid share of the gross cost, per slot, and adjusts the stated rate of every award it cuts
      (`_apply_overall_cap`).

    Args:
        schemes: One combination, already checked against ``excludes``.
        measure: The measure being funded, supplying the cost and its size.
        context: The building context the eligible-cost rules read.
        overall_cap_share: The catalog's state-aid ceiling as a share of gross cost, or ``None`` for no ceiling.

    Returns:
        One award per scheme, in nominal year-0 euro per slot. A loan or VAT award may be zero-valued, since it carries
            terms only.

    Raises:
        SubsidyDataError: If a per-unit scheme's ``size_unit`` differs from the unit of the measure's size.
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
                    # Both factors of the multiplication, and the pre-cap rate when the
                    # group's combined-rate cap scaled this scheme down.
                    benefit_rate=rate,
                    benefit_rate_before_group_cap=scheme_rate if scale_down < 1.0 else None,
                    eligible_basis_in_euro=basis,
                    eligible_basis_cap_in_euro=scheme.eligible_cost.cap_for_units(
                        context.building.dwelling_units
                    ),
                )
            )
    for scheme in schemes:
        if scheme.benefit_kind in (BenefitKind.SHARE_OF_ELIGIBLE_COST, BenefitKind.BONUS_SHARE):
            continue
        basis, binding = _eligible_cost_basis(scheme, measure, context)
        basis_cap = scheme.eligible_cost.cap_for_units(context.building.dwelling_units)
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
                    eligible_basis_in_euro=basis if scheme.eligible_cost.categories else None,
                    eligible_basis_cap_in_euro=basis_cap,
                )
            )
        elif isinstance(benefit, (PerUnitBenefit, TieredPerUnitBenefit)):
            # One path for both per-unit kinds: the benefit prices the measure's size (one rate
            # times the size, or the band sum under the benefit's own cap), and the amount is
            # clamped to the eligible basis like a lump sum — with the same exception for a scheme
            # that declares no eligible-cost categories.
            if measure.facts.size_unit != benefit.size_unit:
                raise SubsidyDataError(
                    f"Scheme {scheme.id}: its {scheme.benefit_kind.value} benefit is an amount per "
                    f"{benefit.size_unit.value!r}, but the measure ({measure.facts.asset_class.value}) is "
                    f"sized in {measure.facts.size_unit.value!r}; the amount cannot be priced on that size."
                )
            amount = UncertainValue.exact(benefit.amount_for(measure.facts.size))
            awards.append(
                SubsidyAward(
                    scheme_id=scheme.id,
                    payout_kind=scheme.payout_kind,
                    upfront_amount=amount.clamp_upper(basis) if scheme.eligible_cost.categories else amount,
                    caps_binding_per_slot=binding,
                    eligible_basis_in_euro=basis if scheme.eligible_cost.categories else None,
                    eligible_basis_cap_in_euro=basis_cap,
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
                    # A tax credit is a percentage form like a share award, so it states
                    # the same multiplication; the instalment split is the payout note's job.
                    benefit_rate=benefit.rate,
                    eligible_basis_in_euro=basis,
                    eligible_basis_cap_in_euro=basis_cap,
                )
            )
        elif isinstance(benefit, ReducedVatBenefit):
            # No consumer reads this award yet; it is typed but not booked.
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
                _apply_overall_cap(award, ratios)
    # The friendly name travels with the award, because the report that shows it is often built
    # from a serialized result in a process that never loaded a catalog. Attached in one pass so a
    # new benefit kind cannot forget it.
    names = {scheme.id: scheme.display_name for scheme in schemes}
    for award in awards:
        award.display_name = names.get(award.scheme_id) or ""
    return awards


class SchemeMaximumNotes:
    """The notes that say why a scheme's maximum is ``None``, one per kind of scheme that states no amount."""

    #: A reduced VAT rate is a rate on the price, not an amount, and no consumer books it.
    REDUCED_VAT = "no maximum: the scheme reduces the VAT rate and states no amount it pays"

    #: A soft loan with no repayment grant pays no grant; its benefit is the cheaper interest. The
    #: sentence an awarded loan-terms row already states.
    SOFT_LOAN = "loan terms: the benefit is in the financing costs, not a grant"

    #: A fixed amount is capped at the eligible cost, and a share -- a soft loan's repayment grant
    #: among them -- is a share of it, and an unpriced measure's cost is unknown, not zero, so
    #: neither can be stated.
    UNPRICED = (
        "no maximum: the measure is unpriced, and what the scheme pays is capped at or a share of the "
        "measure's cost, which is unknown, not zero"
    )


class UnpricedMeasures:
    """Rules for a cost-bounded scheme on a measure whose price is unknown.

    An unpriced measure (``ComponentCostFacts.is_unpriced``) books its price as a placeholder zero. A scheme whose
    amount is capped at or a share of the eligible cost (lump sum, per-unit, share, bonus share, tax credit, or a soft
    loan with a non-zero repayment grant) would then read "up to EUR 0" where the truth is "unknown". Such a scheme
    gets no maximum (:attr:`SchemeMaximumNotes.UNPRICED`), and a scheme the answers would award is left undetermined on
    the price (:attr:`PRICE_QUESTION`), so nothing is booked for it. A scheme that counts no cost category, a soft loan
    without a repayment grant and an operational per-kWh payment are decided as usual.
    """

    #: The benefit kinds whose amount is always capped at the eligible cost or a share of it. A
    #: SOFT_LOAN is bounded by the cost only through a non-zero repayment grant (:meth:`applies`).
    COST_BOUND_KINDS: ClassVar[FrozenSet[BenefitKind]] = frozenset(
        {
            BenefitKind.LUMP_SUM,
            BenefitKind.PER_UNIT,
            BenefitKind.TIERED_PER_UNIT,
            BenefitKind.SHARE_OF_ELIGIBLE_COST,
            BenefitKind.BONUS_SHARE,
            BenefitKind.TAX_CREDIT,
        }
    )

    #: The open question an otherwise decided scheme is left undetermined on.
    PRICE_QUESTION: ClassVar[str] = (
        "the measure's price: the scheme pays an amount capped at or a share of the measure's "
        "eligible cost, and the plan states no price for the measure"
    )

    @classmethod
    def applies(cls, scheme: SubsidyScheme, measure: MeasureForSubsidy) -> bool:
        """Whether the measure's price is unknown and the scheme's amount is bounded by the eligible cost.

        Bounded means a kind in :attr:`COST_BOUND_KINDS`, or a soft loan with a non-zero repayment grant, in a scheme
        that counts cost categories.
        """
        benefit = scheme.benefit
        grants_a_share = scheme.benefit_kind in cls.COST_BOUND_KINDS or (
            isinstance(benefit, LoanTermsBenefit) and bool(benefit.repayment_grant_rate)
        )
        return grants_a_share and bool(scheme.eligible_cost.categories) and measure.facts.is_unpriced()

    @classmethod
    def assessed(cls, assessment: SchemeAssessment, measure: MeasureForSubsidy) -> SchemeAssessment:
        """Return the assessment with the price question added where the scheme cannot be valued.

        On an unpriced measure where :meth:`applies` holds, an eligible scheme becomes undetermined on the price and an
        undetermined one also asks for the price. An ineligible scheme stays ineligible, since no price could change
        that.
        """
        if assessment.status == EligibilityStatus.INELIGIBLE or not cls.applies(assessment.scheme, measure):
            return assessment
        return replace(
            assessment,
            status=EligibilityStatus.UNDETERMINED,
            missing_fields=[*assessment.missing_fields, cls.PRICE_QUESTION],
        )


def _unstated_maximum_note(scheme: SubsidyScheme, measure: MeasureForSubsidy) -> Optional[str]:
    """Return why a scheme states no maximum for a measure before anything is valued, or None.

    A VAT reduction states a rate on the price, not an amount; a cost-bounded amount on an unpriced measure is bounded
    by a cost nobody stated (:class:`UnpricedMeasures`).
    """
    if isinstance(scheme.benefit, ReducedVatBenefit):
        return SchemeMaximumNotes.REDUCED_VAT
    if UnpricedMeasures.applies(scheme, measure):
        return SchemeMaximumNotes.UNPRICED
    return None


def scheme_maximum(
    scheme: SubsidyScheme,
    measure: MeasureForSubsidy,
    context: SubsidyContext,
    overall_cap_share: Optional[float],
) -> SchemeMaximum:
    """Return the most one scheme can pay for one measure on its own, whatever its eligibility verdict.

    The scheme is valued alone by :func:`_combination_awards`, the same kernel as every award, so the maximum and an
    award differ only by what the combination did. Per benefit kind:

    - LUMP_SUM: its amount, clamped to the eligible cost when the scheme counts cost categories.
    - PER_UNIT and TIERED_PER_UNIT: the amount for the measure's size, clamped the same way.
    - SHARE_OF_ELIGIBLE_COST and BONUS_SHARE: the rate times the eligible cost, capped by the scheme's basis cap
      (:meth:`EligibleCostSpec.cap_for_units`). A rate that depends on an open question takes the larger value.
    - TAX_CREDIT: the whole credit, every instalment.
    - SOFT_LOAN: the repayment grant on the capped eligible cost; ``None`` when there is no grant.
    - OPERATIONAL: the rate times the measure's annual energy times the duration.
    - REDUCED_VAT: ``None``, since the catalog states a rate, not an amount.

    A cost-bounded amount on an unpriced measure is ``None`` too (:class:`UnpricedMeasures`). The catalog's overall
    state-aid share, where declared, bounds the upfront amount as for an award.

    Args:
        scheme: The scheme, whatever its eligibility verdict.
        measure: The measure, with its year-0 cost and its size.
        context: The building context the eligible-cost rules read.
        overall_cap_share: The catalog's state-aid ceiling, or None.

    Returns:
        The maximum as a positive band in nominal year-0 euro, or None with the reason.
    """
    benefit = scheme.benefit
    unstated = _unstated_maximum_note(scheme, measure)
    if unstated is not None:
        return SchemeMaximum(amount_in_euro=None, note=unstated)
    award = _combination_awards([scheme], measure, context, overall_cap_share)[0]
    if isinstance(benefit, LoanTermsBenefit):
        if not benefit.repayment_grant_rate:
            return SchemeMaximum(amount_in_euro=None, note=SchemeMaximumNotes.SOFT_LOAN)
        basis, _binding = _eligible_cost_basis(scheme, measure, context)
        return SchemeMaximum(amount_in_euro=basis.scale(benefit.repayment_grant_rate))
    if isinstance(benefit, TaxCreditBenefit):
        return SchemeMaximum(amount_in_euro=UncertainValue.sum(award.schedule_amounts))
    if isinstance(benefit, OperationalBenefit):
        energy = measure.annual_energy_sold_in_kwh.get(
            benefit.carrier, 0.0
        ) or measure.annual_energy_bought_in_kwh.get(benefit.carrier, 0.0)
        return SchemeMaximum(
            amount_in_euro=UncertainValue.exact(benefit.rate_per_kwh * energy * benefit.duration_years)
        )
    return SchemeMaximum(amount_in_euro=award.upfront_amount)


def _support_value(
    awards: List[SubsidyAward],
    measure: MeasureForSubsidy,
    discount: Callable[[int], float],
    slot_getter: Callable[[UncertainValue], float],
) -> float:
    """Return the discounted value of a combination's support in one slot; the solver maximizes it.

    Grants count at year 0, tax-credit instalments are discounted by their year, operational per-kWh payments are
    discounted over their duration, and a soft loan's repayment grant is added. Discounting matters because a 20 %
    credit over ten years is worth less than a 20 % grant today. Operational support falls back from sold to bought
    energy for its carrier, so a premium on consumed heat is valued too. The repayment grant is valued on the measure's
    gross cost here, while ``calculators/financing_application.py`` applies the share to the loan principal; the two
    differ whenever only part of the cost is financed (the shipped KfW rate is 0.0, so no shipped run is affected).

    Args:
        awards: The valued awards of one combination.
        measure: The measure, read for the annual energy of operational awards and the gross cost of a repayment grant.
        discount: Year to discount factor (from ``EconomicParameters``).
        slot_getter: Picks the slot to value; see :func:`solve_cumulation` for why LOW reads the band's maximum.

    Returns:
        The discounted support in euro, positive; larger is better for the applicant.
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
    """Refuse a subset enumeration that would be too large for the cumulation solver.

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
    """Pick the best admissible combination of ELIGIBLE schemes for one measure and report everything (§5.4).

    The objective is :func:`_support_value` on the best-estimate slot; the chosen combination is then valued in all
    three slots. All 2^n subsets of the eligible schemes are enumerated in bitmask order (including the empty one, "no
    subsidy"), subsets violating an ``excludes`` relation are skipped, and each is fully valued, since caps and
    exclusions make the objective non-additive. A candidate replaces the incumbent only if it is better by more than
    1e-9 euro, so ties go to the first in catalog order and the result is reproducible.

    `admits` restricts the candidates before the optimization (§5.5), so ONLY and EXCLUDE perspectives get the best
    combination among the admitted schemes. UNDETERMINED schemes are excluded but reported with the extra support they
    could unlock (§5.7). The best combinations in the LOW and HIGH slots are named where they differ from the chosen
    one; LOW reads the band's maximum and HIGH its minimum, because support is booked as revenue (`as_revenue`) and the
    optimistic world gets the most.

    Args:
        catalog: The country catalog.
        measure: The single measure being funded.
        context: The applicant and building answers.
        year: The year scheme validity is tested against (the price basis year in production).
        discount: Year to discount factor, for comparing payout timings.
        admits: The perspective's subsidy-mode filter on scheme ids; ``None`` admits everything.

    Returns:
        The :class:`SubsidyDecision` with the chosen awards, rejected and undetermined schemes, the optimistic bound,
            the per-slot alternatives and the objective value (``discounted_support_in_euro``).

    Raises:
        SubsidyDataError: If the eligible set, or eligible plus undetermined, exceeds MAX_CUMULATION_SCHEMES.
    """
    # An amount bounded by an unknown price is not decided.
    assessments = [
        UnpricedMeasures.assessed(assessment, measure)
        for assessment in assess_schemes(catalog, measure, context, year, admits)
    ]
    eligible = [assessment.scheme for assessment in assessments if assessment.status == EligibilityStatus.ELIGIBLE]
    decision = SubsidyDecision(measure_subject=measure.subject)
    for assessment in assessments:
        if assessment.status == EligibilityStatus.INELIGIBLE:
            decision.rejected.append({
                "scheme_id": assessment.scheme.id,
                "display_name": assessment.scheme.label,
                "reason": assessment.rejected_reason,
            })
        elif assessment.status == EligibilityStatus.UNDETERMINED:
            decision.undetermined.append({
                "scheme_id": assessment.scheme.id,
                "display_name": assessment.scheme.label,
                "missing_fields": assessment.missing_fields,
            })

    for assessment in assessments:
        decision.maximum_by_scheme[assessment.scheme.id] = scheme_maximum(
            assessment.scheme, measure, context, catalog.overall_cap_share
        )

    def admissible(combination: List[SubsidyScheme]) -> bool:
        """Whether the combination violates no `excludes` relation.

        Every member is checked against every other member's exclusion list, so an exclusion stated on one side applies
        both ways.
        """
        ids = {scheme.id for scheme in combination}
        for scheme in combination:
            if ids & set(scheme.excludes):
                return False
        return True

    # Enumerate subsets (scheme sets are small, typically < 10 per measure).
    _check_cumulation_size(len(eligible), "eligible schemes")
    best_value = float("-inf")
    best_combination: List[SubsidyScheme] = []
    best_awards: List[SubsidyAward] = []
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
        if best_estimate_value > best_value + 1e-9:
            best_value, best_combination, best_awards = best_estimate_value, combination, awards
    if best_value == float("-inf"):
        best_value = 0.0
    chosen_key = "|".join(sorted(scheme.id for scheme in best_combination)) or "<none>"
    for slot_name in ("low", "high"):
        slot_best = best_by_slot.get(slot_name)
        best_per_slot[slot_name] = slot_best[1] if slot_best and slot_best[1] != chosen_key else None
    decision.applied = best_awards
    decision.other_slot_optimal_combination = best_per_slot
    # The objective value the choice was made on, published rather than recomputed downstream.
    decision.discounted_support_in_euro = best_value
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
        decision.undetermined_upper_bound_in_euro = max(0.0, best_optimistic - best_value)
    return decision
