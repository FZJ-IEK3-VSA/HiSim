"""Subsidy application: turning awards into cash flows (cost-spec-v2 §2.3).

The §2.3 "subsidy application" calculator — the *orchestration half* of the subsidy machinery.
The eligibility, amount and cumulation logic lives in `subsidies.py` and is untouched here;
this module builds the `MeasureForSubsidy` the solver needs, hands it the perspective's
subsidy-mode filter (§5.5) so the solver only ever optimizes over admitted schemes, and lays the
resulting support out on the timeline according to its payout kind (§5.3):

* ``UPFRONT_GRANT`` — one negative SUBSIDY entry at year 0;
* ``TAX_CREDIT_SCHEDULE`` — one entry per scheduled year within the observation period;
* ``OPERATIONAL`` — a per-kWh payment on the energy *sold*, for the award's duration.

Without a catalog the phase-1 shim applies instead: the legacy flat percentage carried on the
device entry (§10.1).

**W3.4 — how the support total is obtained (fixed 2026-08-12).** This calculator no longer
returns a running "total upfront support": that figure was incomplete (OPERATIONAL payouts were
emitted but never accumulated), mixed units (tax-credit schedules entered discounted, upfront
grants nominal) and was closed before financing emitted its repayment-grant SUBSIDY entry. The
support figure the §6.4 modernization-levy basis deducts is now derived from the finished
timeline by :func:`nominal_support_from_entries` — the **nominal sum of every SUBSIDY entry**,
which is what §559 BGB deducts (subsidies *received*) and is complete by construction.

**B5 — the subsidy mode filters before the solve (fixed 2026-08-12).** The awards used to be
filtered *after* `solve_cumulation` had optimized over the unrestricted candidate set, so an
ONLY/EXCLUDE perspective could end up with the leftovers of a combination chosen for schemes it
does not admit — a non-optimal, and with `excludes` in play even an empty, remainder. The filter
is now a predicate handed to the solver, so eligibility, cumulation, the undetermined bound and
the question set all see the same admitted candidate set.

Realizes: cost_spec.md §5 (subsidies), §5.5 (subsidy modes), §6.4 (levy basis),
§10.1 (legacy flat shim).
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
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger
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
    """Cash flows and the cumulation record.

    No support *total* is returned on purpose (W3.4): a per-subject running total cannot see the
    financing repayment grant, which is emitted after every subject has been costed. Callers
    derive the figure from the finished timeline with :func:`nominal_support_from_entries`.
    """

    entries: List[CashFlowEntry] = field(default_factory=list)
    #: The solver's record, present only when a catalog was used (never for the legacy shim).
    decision: Optional[SubsidyDecision] = None


def nominal_support_from_entries(entries: Iterable[CashFlowEntry]) -> UncertainValue:
    """The nominal support carried by the SUBSIDY entries of a timeline, as a positive band.

    **Unit: nominal euros received, undiscounted, summed across years** (W3.4). §559 BGB deducts
    the subsidies the landlord *receives*, which are nominal amounts, so the §6.4 levy basis
    deducts exactly this figure. Timeline SUBSIDY entries are negative (revenue-mirrored bands);
    the returned band is mirrored back, so `minimum` reads "least support" again.

    Deriving the figure here rather than accumulating it while entries are emitted makes it
    complete by construction — every SUBSIDY entry counts, whichever calculator emitted it —
    and recoverable from the timeline, which is the property §5.1 asserts.
    """
    signed = UncertainValue.sum(
        entry.amount_in_euro for entry in entries if entry.category == CostCategory.SUBSIDY
    )
    return signed.as_revenue()


def _legacy_flat_shim(costing: DeviceCosting, ledger: ProvenanceLedger) -> SubsidyApplicationResult:
    """Phase-1 shim: the legacy flat percentage from the database entry (§10.1).

    Kept until §10.1 Phase 4 has a catalog for every shipped country (Ireland has none yet), but
    no longer anonymous: the support it emits carries its own ledger record with
    :attr:`ParameterOrigin.LEGACY_MIGRATION_SHIM`, so a reader of `cost_provenance.json` sees
    that this euro came from a migration leftover in the *device* catalog and not from a scheme
    anyone can point at (W2.6). The device entry's `source_ids` are deliberately not cited —
    they source the device price — unless the data file declares a `field_sources` entry for the
    share itself.
    """
    result = SubsidyApplicationResult()
    share = costing.legacy_flat_subsidy_share
    if share > 0:
        amount = (costing.device_cost + costing.installation_cost).scale(share)
        entry = costing.entry
        shim_provenance = ledger.record(
            ParameterProvenance(
                parameter=(
                    f"{entry.entry_key}.legacy_flat_subsidy_share"
                    if entry is not None
                    else f"{costing.subject}.legacy_flat_subsidy_share"
                ),
                value=share,
                origin=ParameterOrigin.LEGACY_MIGRATION_SHIM,
                data_file=f"{entry.data_file}#{entry.entry_key}" if entry is not None else None,
                source_ids=(
                    entry.field_sources.get("legacy_flat_subsidy_share", ()) if entry is not None else ()
                ),
                detail=(
                    "§10.1 Phase-1 flat subsidy shim: subsidy data carried in the device catalog "
                    "for countries without a subsidy catalog; superseded by any active catalog."
                ),
            )
        )
        result.entries.append(
            CashFlowEntry(
                year=0,
                amount_in_euro=amount.as_revenue(),
                category=CostCategory.SUBSIDY,
                subject=costing.subject,
                subsidy_scheme_id="LEGACY_FLAT",
                provenance_ids=costing.provenance_ids + (shim_provenance,),
            )
        )
    return result


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
) -> SubsidyApplicationResult:
    """Subsidy flows for one measure (§5); the support total is derived from the timeline.

    The bridge between the subsidy *engine* and the timeline. It assembles the measure record the
    solver needs — the measure's costs split by category (the eligible-cost basis), whether it is
    an INSTALL or a REPLACE, its VAT rate and the annualized energy it sells — runs the cumulation
    solver over the schemes the perspective admits, and then lays each resulting award out
    according to its payout kind. No eligibility rule, cap or cumulation constraint is evaluated
    here; all of that is `subsidies.py`, which knows nothing about timelines.

    Two things a reviewer should note about *when* things happen. The measure's costs are those of
    `DeviceCosting`, i.e. gross and before any other support, so eligible-cost caps bind on the
    figure the legal texts mean. And scheme validity is tested against `price_basis_year`, the
    economic "today" — a 2015 weather year simulated with 2026 prices is offered 2026 schemes,
    which is the only reading under which a subsidy result means anything.

    Args:
        costing: The measure's resolved costing (§3.5, §4.1). Supplies the cost blocks, the facts
            the eligibility conditions are evaluated against, the VAT rate, whether an asset is
            being replaced, and — without a catalog — the legacy flat share.
        subsidy_catalog: The country catalog, or `None` to fall back to the phase-1 flat shim.
        subsidy_context: The applicant/building questionnaire answers the conditions read (§5.7).
        subsidy_mode: The perspective's mode (NONE / FULL / ONLY / EXCLUDE). Its `admits`
            predicate is handed to the solver, so filtering happens *before* the optimization
            (B5), not after.
        billing: All carriers' billing determinants, read only for `energy_sold_in_kwh`, which
            OPERATIONAL payouts (per-kWh feed-in-style premiums) are paid on.
        simulated_period_fraction: Simulated share of a year, used to annualize that sold energy.
        ledger: Provenance ledger; each applied scheme's own record is interned into it (W2.4).
        parameters: Economic parameters — supplies the horizon that truncates schedules and the
            discount factor the solver uses to compare payout timings.
        price_basis_year: The economic "today" scheme validity is tested against.

    Returns:
        A `SubsidyApplicationResult` whose `entries` are revenue-mirrored (negative) SUBSIDY
        entries in nominal euros of their year, at year 0 for upfront grants, at years 1..N for
        tax-credit schedules, and for the award's duration for operational payouts — and whose
        `decision` carries the solver's full audit trail (applied, rejected, undetermined) unless
        the legacy shim ran. Deliberately no support total: see the module docstring (W3.4).
    """
    params = parameters
    if subsidy_catalog is None:
        return _legacy_flat_shim(costing, ledger)
    result = SubsidyApplicationResult()
    measure = MeasureForSubsidy(
        subject=costing.subject,
        facts=costing.facts,
        measure_kind="REPLACE" if costing.replaced_asset is not None else "INSTALL",
        cost_by_category={
            CostCategory.INVESTMENT: costing.device_cost + costing.installation_cost,
            CostCategory.PLANNING: costing.planning_cost,
            CostCategory.REMOVAL: costing.removal_cost_of_replaced,
        },
        vat_rate=costing.vat_rate,
    )
    energy_sold: Dict[EnergyCarrier, float] = measure.annual_energy_sold_in_kwh
    for determinants in billing:
        if determinants.energy_sold_in_kwh:
            # W3.5: this site guards the divisor, the energy calculator does not — see
            # calculators/annualization.py for the (preserved) discrepancy.
            energy_sold[determinants.carrier] = annualize(
                determinants.energy_sold_in_kwh, simulated_period_fraction, guard_zero=True
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
        # W2.4: the scheme's own provenance, minted by the catalog that loaded (and resolved) it —
        # this used to fabricate an `inline:subsidy scheme <id>` pseudo-source here.
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
