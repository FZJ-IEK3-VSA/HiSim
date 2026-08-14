"""Context resolution: what does this subject actually cost *here* (cost-spec-v2 §2.3).

The §2.3 "context resolution" calculator. It answers, per cost subject, the questions that
precede every euro of the investment schedule:

* which database entry / override supplies each cost building block, and with what provenance;
* is this a **new investment**, a **kept** existing asset, or a **replacement** of one
  (installation context, cost_spec.md §4.1) — and therefore, is anything charged at year 0;
* when does the first replacement fall due (a kept asset has already aged);
* what happens to the asset being replaced: its written-off residual book value (**sunk cost**,
  reported but excluded from decision KPIs) and the **anyway-cost credit** for the like-for-like
  replacement that no longer has to be paid, or — for coupled measures — the non-energy share
  that would have been spent anyway (spec Q7).

This module is a straight extraction of `_resolve_device` and the §4.1 blocks of
`build_timeline`; the arithmetic, the ordering of the context checks (the replacement check
runs before the "kept" check on purpose) and the fallbacks on missing database entries are
unchanged.

**Its place in the pipeline.** It runs first, once per cost subject, before any cash flow
exists: `evaluator.build_timeline` calls :func:`resolve_device` and hands the resulting
:class:`DeviceCosting` to the investment, maintenance and subsidy calculators, which then only
do arithmetic on it. That is the review concern this module isolates — *which numbers are used
and why*, separated from *what is done with them*. It is also the last place database lookups
and the provenance ledger are touched for a subject, so a value that cannot be explained later
was not recorded here.

Units and conventions used throughout: euro amounts are banded (`UncertainValue`, §3.9) and
cost-positive; the anyway-cost credit is the one entry emitted here and it is revenue-mirrored
(negative). Years are relative to the investment date, so `first_replacement_year` is "years
from now", not a calendar year; ages and scheme validity are anchored on the *price basis year*
— the economic "today" — which may differ from the simulated weather year.

Realizes: cost_spec.md §3.5 (device entries, removal cost), §3.10 (provenance), §4.1 (existing
assets, sunk cost, anyway cost), spec Q7 (coupled cost).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from hisim import log
from hisim.economics.calculators.escalation import escalate, investment_escalation_rate
from hisim.economics.database import CostDatabase, CostDataError, DeviceEntry, ResolvedDeviceEntry
from hisim.economics.facts import ComponentCostFacts, ExistingAsset, ExistingAssetRegister
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import InstallationContext
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue


class ContextResolutionConstants:
    """The one fallback applied when the database cannot answer for an existing asset (§4.1).

    A brownfield register may name an asset class that the current cost database no longer
    prices — an old night-storage heater, a technology dropped from the catalog — and the
    replaced asset's *remaining life* is still needed to compute the sunk cost and to decide
    whether the anyway-cost credit applies.

    That situation is only survivable when the register itself supplies the missing price through
    `ExistingAsset.replacement_cost_override_in_euro`, the designed escape hatch for exactly this
    case: the price is then declared, and only the service life is missing, so the default below
    stands in for it. Without the override there is nothing left to assume from and
    `resolve_replaced_asset` raises instead of reporting a 0 EUR sunk cost and a guessed life as
    if they were established figures (issue #25c). The constant is therefore reachable only on the
    override path.
    """

    #: Service life assumed for a replaced asset that is priced by an override but has no
    #: database entry to read a service life from.
    FALLBACK_SERVICE_LIFE_IN_YEARS = 20.0


@dataclass
class DeviceCosting:
    """Resolved year-0 cost building blocks for one component (per slot).

    Everything the downstream calculators need about one cost subject, resolved once: the euro
    building blocks (already banded and already multiplied by `facts.count` / sized by
    `facts.size`), the technical figures that drive the schedule, and the installation-context
    verdict. It is the contract between "which number applies here" (this module) and "what is
    done with it" (`investment.py`, `maintenance.py`, `subsidy_application.py`), and it carries
    the `provenance_ids` that let `explain` walk any resulting euro back to its data file.

    Units, in the order the fields appear: `device_cost`, `installation_cost`, `planning_cost`
    and `removal_cost_of_replaced` are euro bands, cost-positive, at price-basis-year prices;
    `maintenance_rate` is a dimensionless *share of gross investment per year*;
    `fixed_operation_cost` is euro per year; `service_life_years` is years; `embodied_co2_kg` is
    kilograms for the whole installation (size and count already applied); `vat_rate` and
    `legacy_flat_subsidy_share` are fractions; `energy_related_cost_share` is the dimensionless
    Q7 coupled-cost share (1.0 = fully energy-related). `first_replacement_year` is a *relative*
    year index — years from the investment date, already shortened by a kept asset's age.
    """

    subject: str
    facts: ComponentCostFacts
    entry: Optional[DeviceEntry]
    device_cost: UncertainValue
    installation_cost: UncertainValue
    planning_cost: UncertainValue
    removal_cost_of_replaced: UncertainValue
    maintenance_rate: UncertainValue
    fixed_operation_cost: UncertainValue
    service_life_years: float
    embodied_co2_kg: float
    vat_rate: float
    legacy_flat_subsidy_share: float
    provenance_ids: Tuple[int, ...]
    is_new_investment: bool  # charged at year 0 in this installation context
    first_replacement_year: int  # relative year of the first replacement
    # Coupled-cost share and anyway threshold, resolved from the device entry (Q7):
    energy_related_cost_share: UncertainValue
    anyway_threshold_years: float
    replaced_asset: Optional[ExistingAsset] = None

    @property
    def gross_investment(self) -> UncertainValue:
        """I_gross = device + installation + planning (§3.6).

        The reference amount three separate mechanisms are computed from, which is why it is one
        property rather than three sums: replacements re-purchase it escalated (§3.6 rule 2), the
        residual value writes it down straight-line (rule 3), and maintenance is a rate *of* it
        (rule 4). The removal cost of a replaced asset is deliberately **not** part of it — it is
        a one-off disposal charge, not something that recurs at every replacement or attracts a
        maintenance rate — and enters the modernization-levy basis as its own addend instead.
        """
        return self.device_cost + self.installation_cost + self.planning_cost


@dataclass
class ReplacedAssetOutcome:
    """The §4.1 consequences of replacing an existing asset, for one subject.

    `sunk_cost` is this subject's contribution to the reported write-off; `credit_entry` is the
    ANYWAY_COST_CREDIT timeline entry, present only when a credit is actually due, and
    `credit_amount` is that credit as a positive figure (it feeds the modernization-levy basis).

    The two halves are separated because they go to different places and mean different things.
    `evaluator.build_timeline` accumulates `sunk_cost` into `sunk_cost_written_off_in_euro`, which
    is *reported but deliberately kept out of every decision KPI* — a sunk cost must not distort a
    comparison, yet researchers want to see it (§4.1). `credit_entry` is real money in the
    differential frame and goes onto the timeline; `credit_amount` is the same figure unmirrored,
    summed into the avoided-maintenance deduction of the §6.4 levy basis. Both euro bands, both
    cost-positive as stored here (only the entry itself carries the negative sign).
    """

    sunk_cost: UncertainValue
    credit_entry: Optional[CashFlowEntry] = None
    credit_amount: UncertainValue = UncertainValue.exact(0.0)


def resolve_device(
    subject: str,
    facts: ComponentCostFacts,
    context: InstallationContext,
    existing_assets: Optional[ExistingAssetRegister],
    ledger: ProvenanceLedger,
    database: CostDatabase,
    parameters: EconomicParameters,
    price_basis_year: int,
) -> DeviceCosting:
    """Resolves one subject's cost building blocks and its installation context (§3.5, §4.1).

    Two questions in one pass. First, *where does each number come from*: for every building
    block (investment, installation, planning, maintenance rate, fixed O&M, service life,
    embodied CO2) a component-declared override wins over the country's device entry for the
    price basis year, and whichever wins is recorded in the provenance ledger — overrides as
    `CONFIG_OVERRIDE` citing their `override_source`, database fields as the entry's own record
    (W2.1). Second, *is this subject bought today*: the perspective's installation context plus
    the existing-asset register decide whether the subject is a new investment, a like-for-like
    or fuel-switching **replacement** of a registered asset, or a **kept** asset that costs
    nothing at year 0 and merely ages toward its first replacement.

    The order of the context checks is load-bearing and deliberately not "obvious": the
    replacement check (`asset.replaced_by_asset_classes` names this class) runs *before* the
    same-class "kept" lookup, so that new windows replacing old windows are charged as an
    investment instead of silently counting as the existing element. STATUS_QUO always ends up
    as "not a new investment" — that is what a do-nothing reference variant means — but an
    unregistered class still gets its full service life as first replacement year.

    Args:
        subject: Timeline subject name for this cost subject (component instance or measure).
        facts: What the component/variant declared about itself — asset class, size, count,
            optional per-field overrides (§3.3). No prices.
        context: The perspective's installation context (GREENFIELD / BROWNFIELD / STATUS_QUO /
            OPERATING_ONLY, §4.1).
        existing_assets: Register of what is physically already in the building; `None` for a
            greenfield run.
        ledger: Provenance ledger; every priced field resolved here is interned into it (§3.10).
        database: Loaded cost database for the country's device entries (§3.5).
        parameters: Economic parameters — supplies the country and the default
            `anyway_threshold_years`.
        price_basis_year: The economic "today"; device entries are looked up for it and asset
            ages are measured against it, deliberately not against the simulated weather year.

    Returns:
        A `DeviceCosting` with every building block banded and sized, the installation-context
        verdict (`is_new_investment`, `first_replacement_year`, `replaced_asset`) and the
        provenance ids of everything that entered it.

    Raises:
        CostDataError: If no device entry exists for the asset class, country and year *and* the
            component did not override both the investment cost and the lifetime — the two fields
            that cannot be defaulted. A missing entry is otherwise tolerated field by field.
    """
    year = price_basis_year
    entry: Optional[DeviceEntry] = None
    provenance_ids: List[int] = []
    # W2.1: the entry is resolved *with* the provenance of every field this subject will be
    # priced from — which fields those are is decided by the overrides, so it is declared here
    # and recorded by the database layer. Fields an override supersedes are not requested: the
    # override's own record replaces them, exactly as before.
    priced_fields = [
        field_name
        for field_name, is_overridden in (
            ("specific_investment", facts.investment_cost_override_in_euro is not None),
            ("maintenance_rate_per_year", facts.maintenance_rate_override is not None),
            ("service_life_in_years", facts.lifetime_override_in_years is not None),
        )
        if not is_overridden
    ]
    resolved: Optional[ResolvedDeviceEntry] = None
    try:
        resolved = database.resolve_device_entry(
            facts.asset_class, year, parameters.country, ledger, priced_fields
        )
        entry = resolved.entry
    except CostDataError:
        if facts.investment_cost_override_in_euro is None or facts.lifetime_override_in_years is None:
            raise

    def override_record(field_name: str, value) -> int:
        return ledger.record(
            ParameterProvenance(
                parameter=f"{subject}.{field_name}",
                value=value,
                origin=ParameterOrigin.CONFIG_OVERRIDE,
                source_ids=(f"inline:{facts.override_source or 'override without source (migration mode)'}",),
                detail=facts.override_source,
            )
        )

    if facts.investment_cost_override_in_euro is not None:
        device_cost = facts.investment_cost_override_in_euro.scale(float(facts.count))
        provenance_ids.append(override_record("investment_cost_override_in_euro", device_cost))
    else:
        assert resolved is not None
        device_cost = resolved.entry.investment_for_size(facts.size).scale(float(facts.count))
        provenance_ids.append(resolved.provenance_id("specific_investment"))
    if facts.installation_cost_override_in_euro is not None:
        installation_cost = facts.installation_cost_override_in_euro
        provenance_ids.append(override_record("installation_cost_override_in_euro", installation_cost))
    elif entry is not None:
        installation_cost = entry.fixed_installation_cost_in_euro
    else:
        installation_cost = UncertainValue.exact(0.0)
    planning_cost = entry.planning_cost_in_euro if entry is not None else UncertainValue.exact(0.0)
    if facts.maintenance_rate_override is not None:
        maintenance_rate = facts.maintenance_rate_override
        provenance_ids.append(override_record("maintenance_rate_override", maintenance_rate))
    elif resolved is not None:
        maintenance_rate = resolved.entry.maintenance_rate_per_year
        provenance_ids.append(resolved.provenance_id("maintenance_rate_per_year"))
    else:
        maintenance_rate = UncertainValue.exact(0.0)
    if facts.fixed_operation_cost_override_in_euro_per_year is not None:
        fixed_operation = facts.fixed_operation_cost_override_in_euro_per_year
        provenance_ids.append(override_record("fixed_operation_cost_override_in_euro_per_year", fixed_operation))
    elif entry is not None:
        fixed_operation = entry.fixed_operation_cost_in_euro_per_year
    else:
        fixed_operation = UncertainValue.exact(0.0)
    if facts.lifetime_override_in_years is not None:
        service_life = facts.lifetime_override_in_years
        provenance_ids.append(override_record("lifetime_override_in_years", service_life))
    else:
        assert resolved is not None
        service_life = resolved.entry.service_life_in_years
        provenance_ids.append(resolved.provenance_id("service_life_in_years"))
    if facts.embodied_co2_override_in_kg is not None:
        embodied_co2 = facts.embodied_co2_override_in_kg
    elif entry is not None:
        embodied_co2 = entry.embodied_co2_for_size(facts.size) * facts.count
    else:
        embodied_co2 = 0.0

    # Installation context: matched-kept vs new measure vs replacement (§4.1). The
    # replacement check runs FIRST so like-for-like measures (new windows replacing old
    # windows, same asset class) are charged as investments instead of "kept".
    register = existing_assets
    is_new_investment = True
    first_replacement_year = int(round(service_life))
    replaced_asset: Optional[ExistingAsset] = None
    if context in (InstallationContext.BROWNFIELD, InstallationContext.STATUS_QUO) and register is not None:
        replaced_asset = next(
            (
                asset
                for asset in register.assets
                if facts.asset_class in asset.replaced_by_asset_classes
            ),
            None,
        )
        if replaced_asset is None:
            matched = register.find(facts.asset_class)
            if matched is not None:
                # Kept asset: no investment; first replacement at service_life - current_age.
                # Ages anchor on the price basis year (the economic "today", like scheme
                # validity and the CO2 path), not the possibly historical weather year.
                is_new_investment = False
                age = matched.age_in_years(price_basis_year)
                first_replacement_year = max(1, int(round(service_life - age)))
    elif context == InstallationContext.STATUS_QUO and register is None:
        is_new_investment = False
        log.warning(
            f"STATUS_QUO without an existing-asset register: treating {subject} "
            "as an existing asset of age 0."
        )

    if context == InstallationContext.STATUS_QUO:
        is_new_investment = False
        if register is not None and register.find(facts.asset_class) is None:
            first_replacement_year = int(round(service_life))

    removal_cost = UncertainValue.exact(0.0)
    if is_new_investment and replaced_asset is not None:
        # Disposal of the replaced device type (§3.5 removal_cost). Deliberately the raw lookup:
        # this field never had a provenance record of its own (the removal entry rides on the
        # measure's provenance ids), and W2.1 preserves the ledger content exactly. Recording it
        # is a separate, deliberate change — see the W2.1 note in cost-spec-v2 §2.2.
        try:
            old_entry = database.get_device_entry(replaced_asset.asset_class, year, parameters.country)
            removal_cost = old_entry.removal_cost_in_euro
        except CostDataError:
            removal_cost = UncertainValue.exact(0.0)

    return DeviceCosting(
        subject=subject,
        facts=facts,
        entry=entry,
        device_cost=device_cost,
        installation_cost=installation_cost,
        planning_cost=planning_cost,
        removal_cost_of_replaced=removal_cost,
        maintenance_rate=maintenance_rate,
        fixed_operation_cost=fixed_operation,
        service_life_years=service_life,
        embodied_co2_kg=embodied_co2,
        vat_rate=entry.vat_rate if entry is not None else 0.0,
        legacy_flat_subsidy_share=entry.legacy_flat_subsidy_share if entry is not None else 0.0,
        provenance_ids=tuple(provenance_ids),
        is_new_investment=is_new_investment,
        first_replacement_year=first_replacement_year,
        energy_related_cost_share=(
            entry.energy_related_cost_share if entry is not None else UncertainValue.exact(1.0)
        ),
        anyway_threshold_years=(
            entry.anyway_threshold_years_override
            if entry is not None and entry.anyway_threshold_years_override is not None
            else parameters.anyway_threshold_years
        ),
        replaced_asset=replaced_asset,
    )


def resolve_replaced_asset(
    costing: DeviceCosting,
    gross: UncertainValue,
    database: CostDatabase,
    parameters: EconomicParameters,
    price_basis_year: int,
) -> ReplacedAssetOutcome:
    """Sunk cost and anyway-cost credit for the asset this measure replaces (§4.1, Q7).

    Only called for measures that are charged at year 0 and do replace a registered asset.

    Two figures, both about the *old* asset. The **sunk cost** is its straight-line residual book
    value — `like_for_like_price * remaining_life / service_life`, in euro at price-basis-year
    prices — thrown away by replacing it early; the evaluator reports it and keeps it out of every
    decision KPI. The **anyway-cost credit** is the differential-comparison correction of the EU
    cost-optimal methodology: if the old asset had at most `anyway_threshold_years` of life left,
    the replacement it no longer needs would have been paid regardless, so its cost is credited
    against the measure and only the *extra* cost of choosing a heat pump over a new boiler is
    charged. Above the threshold no credit is due and the full measure price stands.

    Which credit is computed depends on the coupled-cost share (spec Q7). With
    `energy_related_cost_share < 1` — envelope measures, where scaffolding and render would have
    been paid anyway — the credit is the *non-energy* share of this measure's own gross cost;
    otherwise it is the avoided like-for-like replacement of the old asset. The two are mutually
    exclusive by construction so they can never double count, and each is escalated to
    `credit_year` (the old asset's remaining life, rounded) with its own asset class's investment
    escalation rate.

    Args:
        costing: The replacing measure's resolved costing; supplies `replaced_asset`, the
            coupled-cost share and the anyway threshold that applies to this class.
        gross: The measure's own gross investment (euro band), used only for the coupled-cost
            branch.
        database: Loaded cost database, for the replaced asset's price and service life.
        parameters: Economic parameters — country and the escalation-rate fallback chains.
        price_basis_year: The economic "today" the replaced asset's age is measured against.

    Returns:
        A `ReplacedAssetOutcome`. `sunk_cost` is always present (possibly zero); `credit_entry` is
        `None` unless a credit is actually due and positive, in which case it is a revenue-mirrored
        ANYWAY_COST_CREDIT entry at `credit_year` and `credit_amount` mirrors it cost-positive.

    Raises:
        CostDataError: When the replaced asset's class has no database entry and the register
            declared no `replacement_cost_override_in_euro` for it — see
            `ContextResolutionConstants` for why that combination cannot be assumed away.
    """
    replaced = costing.replaced_asset
    assert replaced is not None
    try:
        # Raw lookup for the same reason as the removal cost above: the replaced asset's own
        # entry contributes no ledger record today, and W2.1 keeps the ledger content unchanged.
        old_entry = database.get_device_entry(replaced.asset_class, price_basis_year, parameters.country)
        like_for_like = (
            replaced.replacement_cost_override_in_euro
            or old_entry.investment_for_size(replaced.size)
        )
        old_life = old_entry.service_life_in_years
    except CostDataError as err:
        if replaced.replacement_cost_override_in_euro is None:
            # No entry and no declared price: the sunk cost and the anyway-cost threshold would
            # both be invented (a 0 EUR like-for-like and a guessed service life), and the
            # register would silently mis-state the decision situation (issue #25c).
            raise CostDataError(
                f"Registered existing asset {replaced.asset_class.value} has no cost database "
                f"entry for {parameters.country} at price basis year {price_basis_year} and no "
                "replacement_cost_override_in_euro: its sunk cost and anyway-cost credit cannot "
                f"be established. Underlying lookup: {err}"
            ) from err
        like_for_like = replaced.replacement_cost_override_in_euro
        old_life = ContextResolutionConstants.FALLBACK_SERVICE_LIFE_IN_YEARS
    age = replaced.age_in_years(price_basis_year)
    remaining = max(0.0, old_life - age)
    sunk_cost = like_for_like.scale(remaining / old_life if old_life else 0.0)
    if remaining > costing.anyway_threshold_years:
        return ReplacedAssetOutcome(sunk_cost=sunk_cost)

    credit_year = int(round(remaining))
    share = costing.energy_related_cost_share
    if share.average < 1.0:
        # Coupled-cost credit (Q7): the non-energy share of the measure
        # (scaffolding, render, standard glazing) would have been spent
        # anyway when the old element was due — it replaces the
        # like-for-like credit so the two never double count.
        non_energy_share = UncertainValue(
            average=1.0 - share.average,
            minimum=1.0 - share.maximum,
            maximum=1.0 - share.minimum,
        )
        rate = investment_escalation_rate(costing.facts.asset_class, parameters, database)
        credit = escalate(gross.multiply_band(non_energy_share), rate, credit_year)
    elif like_for_like.maximum > 0:
        old_rate = investment_escalation_rate(replaced.asset_class, parameters, database)
        credit = escalate(like_for_like, old_rate, credit_year)
    else:
        credit = UncertainValue.exact(0.0)
    if credit.maximum <= 0:
        return ReplacedAssetOutcome(sunk_cost=sunk_cost)
    return ReplacedAssetOutcome(
        sunk_cost=sunk_cost,
        credit_entry=CashFlowEntry(
            year=credit_year,
            amount_in_euro=credit.as_revenue(),
            category=CostCategory.ANYWAY_COST_CREDIT,
            subject=costing.subject,
            provenance_ids=costing.provenance_ids,
        ),
        credit_amount=credit,
    )
