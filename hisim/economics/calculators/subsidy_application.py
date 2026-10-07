"""Turn subsidy awards into SUBSIDY cash flows on the timeline (cost_spec.md §2.3, §5).

Eligibility, amounts and cumulation live in the subsidy package. This module builds the `MeasureForSubsidy` the solver
needs, passes it the perspective's subsidy-mode filter (§5.5) so the solver only optimizes over admitted schemes, and
lays each award out by payout kind (§5.3): ``UPFRONT_GRANT`` at year 0, ``TAX_CREDIT_SCHEDULE`` once per scheduled year
within the horizon, ``OPERATIONAL`` per kWh sold for the award's duration. Without a catalog nothing is booked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from hisim.economics.calculators.annualization import annualize
from hisim.economics.calculators.context_resolution import DeviceCosting
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import BillingDeterminants
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import SubsidyMode
from hisim.economics.provenance import ProvenanceLedger
from hisim.economics.subsidies import (
    MeasureForSubsidy,
    PayoutKind,
    SubsidyCatalog,
    SubsidyContext,
    SubsidyDecision,
    solve_cumulation,
)
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


@dataclass
class SubsidyApplicationResult:
    """The SUBSIDY cash flows of one measure and the solver's cumulation record.

    It carries no support total, because the financing repayment grant is emitted later; callers derive the total from
    the finished timeline with :func:`nominal_support_from_entries`.
    """

    entries: List[CashFlowEntry] = field(default_factory=list)
    #: The solver's record, present only when a catalog was used.
    decision: Optional[SubsidyDecision] = None


def nominal_support_from_entries(entries: Iterable[CashFlowEntry]) -> UncertainValue:
    """Return the nominal support carried by the SUBSIDY entries of a timeline, as a positive band.

    The unit is nominal euros received, undiscounted and summed across years: what §559 BGB deducts, so the §6.4
    modernization-levy basis uses this figure. SUBSIDY entries are negative and revenue-mirrored (their optimistic slot
    is the band maximum); the result is mirrored back, so `minimum` reads "least support". Deriving it from the entries
    makes it complete whichever calculator emitted them.
    """
    signed = UncertainValue.sum(
        entry.amount_in_euro for entry in entries if entry.category == CostCategory.SUBSIDY
    )
    return signed.as_revenue()


def build_subsidy_flows(
    costing: DeviceCosting,
    subsidy_catalog: Optional[SubsidyCatalog],
    subsidy_context: SubsidyContext,
    subsidy_mode: SubsidyMode,
    billing: List[BillingDeterminants],
    simulated_period_fraction: float,
    ledger: ProvenanceLedger,
    parameters: EconomicParameters,
    price_basis_year: int,
    cost_factor: float = 1.0,
) -> SubsidyApplicationResult:
    """Return the SUBSIDY flows of one measure (§5).

    It builds the measure record the solver needs (costs by category, INSTALL or REPLACE, VAT rate, annualized energy
    sold), runs the cumulation solver over the schemes the perspective admits and lays each award out by payout kind.
    The costs are gross, before any other support, so eligible-cost caps bind on the figure the legal texts mean.
    Scheme validity is tested against `price_basis_year`, the economic "today", not the simulated weather year.

    Args:
        costing: The measure's resolved costing (§3.5, §4.1): cost blocks, facts for the eligibility conditions, VAT
            rate and whether an asset is being replaced.
        subsidy_catalog: The country catalog, or None, in which case nothing is booked.
        subsidy_context: The applicant and building answers the conditions read (§5.7).
        subsidy_mode: The perspective's mode (NONE, FULL, ONLY, EXCLUDE); its `admits` predicate filters schemes before
            the solver optimizes.
        billing: All carriers' billing determinants; only `energy_sold_in_kwh` is read, for OPERATIONAL payouts, scaled
            by the subject's ``share_of_energy_sold``.
        simulated_period_fraction: Simulated share of a year, which annualizes the sold energy.
        ledger: Provenance ledger; each applied scheme's record is interned into it.
        parameters: Economic parameters; supply the horizon that truncates schedules and the discount factor the solver
            uses to compare payout timings.
        price_basis_year: The economic "today" scheme validity is tested against.
        cost_factor: Price level of year 0 relative to the price basis year for this purchase
            (`evaluator.YearZeroPriceLevel.purchase`); 1.0 for a stated price. The solver sees the cost in year-0
            money.

    Returns:
        The result: negative, revenue-mirrored SUBSIDY entries in nominal euros of their year, and the solver's
            decision
        (applied, rejected, undetermined). Both are empty without a catalog.
    """
    params = parameters
    result = SubsidyApplicationResult()
    if subsidy_catalog is None:
        return result
    # A stated purchase price (a reader's quote, renovisorissues #53) is the whole job, so a
    # scheme's eligible cost sees it, split over the categories as the purchase itself is.
    investment, planning, removal = costing.purchase_blocks()
    cost_by_category = {
        CostCategory.INVESTMENT: investment,
        CostCategory.PLANNING: planning,
        CostCategory.REMOVAL: removal,
    }
    if cost_factor != 1.0:
        cost_by_category = {category: cost.scale(cost_factor) for category, cost in cost_by_category.items()}
    measure = MeasureForSubsidy(
        subject=costing.subject,
        facts=costing.facts,
        measure_kind="REPLACE" if costing.replaced_asset is not None else "INSTALL",
        cost_by_category=cost_by_category,
        vat_rate=costing.vat_rate,
    )
    energy_sold: Dict[EnergyCarrier, float] = measure.annual_energy_sold_in_kwh
    for determinants in billing:
        if determinants.energy_sold_in_kwh:
            # This site guards a zero divisor, the energy calculator does not (see
            # calculators/annualization.py). A piece of a subject a staged plan split is paid on its
            # size share of what the installation sells (`ComponentCostFacts.share_of_energy_sold`);
            # 1.0 for every other subject.
            energy_sold[determinants.carrier] = (
                annualize(determinants.energy_sold_in_kwh, simulated_period_fraction, guard_zero=True)
                * costing.facts.share_of_energy_sold
            )
    # Scheme validity follows the price basis year — the economic "today" — not the
    # (possibly historical) weather year of the simulation.
    decision = solve_cumulation(
        subsidy_catalog,
        measure,
        subsidy_context,
        price_basis_year,
        params.discount_factor,
        admits=subsidy_mode.admits,
    )
    result.decision = decision
    for award in decision.applied:
        # The scheme's own provenance, minted by the catalog that loaded and resolved it.
        scheme = subsidy_catalog.scheme_by_id(award.scheme_id)
        assert scheme is not None, f"award for unknown scheme {award.scheme_id}"
        provenance = subsidy_catalog.provenance_for_scheme(
            scheme,
            ledger,
            award.upfront_amount if award.upfront_amount.maximum else str(award.payout_kind.value),
        )
        if award.payout_kind == PayoutKind.UPFRONT_GRANT and award.upfront_amount.maximum > 0:
            result.entries.append(
                CashFlowEntry(
                    year=0,
                    amount_in_euro=award.upfront_amount.as_revenue(),
                    category=CostCategory.SUBSIDY,
                    subject=costing.subject,
                    subsidy_scheme_id=award.scheme_id,
                    provenance_ids=(provenance,),
                )
            )
        elif award.payout_kind == PayoutKind.TAX_CREDIT_SCHEDULE:
            for offset, amount in enumerate(award.schedule_amounts, start=1):
                if offset > params.observation_period_in_years:
                    break
                result.entries.append(
                    CashFlowEntry(
                        year=offset,
                        amount_in_euro=amount.as_revenue(),
                        category=CostCategory.SUBSIDY,
                        subject=costing.subject,
                        subsidy_scheme_id=award.scheme_id,
                        provenance_ids=(provenance,),
                    )
                )
        elif award.payout_kind == PayoutKind.OPERATIONAL and award.operational_carrier is not None:
            energy = measure.annual_energy_sold_in_kwh.get(award.operational_carrier, 0.0)
            for year in range(1, min(award.operational_duration_years, params.observation_period_in_years) + 1):
                result.entries.append(
                    CashFlowEntry(
                        year=year,
                        amount_in_euro=UncertainValue.exact(award.operational_rate_per_kwh * energy).as_revenue(),
                        category=CostCategory.SUBSIDY,
                        subject=costing.subject,
                        subsidy_scheme_id=award.scheme_id,
                        provenance_ids=(provenance,),
                    )
                )
    return result
