"""Context resolution: what a cost subject is priced with and whether it is bought (cost_spec.md §2.3).

A cost subject is one costed item on the timeline (a component instance or a measure). Per subject this module picks
each cost building block from a component override or the database entry, records its provenance, and decides the
installation context (cost_spec.md §4.1): a new investment, a kept existing asset, or a replacement of one, with the
replaced asset's sunk cost and anyway credit. `evaluator.build_timeline` calls `resolve_device` first and hands the
resulting `DeviceCosting` to the investment, maintenance and subsidy calculators, which only do arithmetic on it.

Years are relative to year 0. Ages are measured at the ageing reference year, the timeline's year 0: the price basis
year (the economic "today"), or a staged plan's own year 0; never the weather year.
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
from hisim.economics.results import AnywayBasisKinds
from hisim.economics.timeline import CashFlowEntry, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


class ContextResolutionConstants:
    """The fallback service life for a replaced asset the database does not price (§4.1).

    Used only when the register supplies the price through `ExistingAsset.replacement_cost_override_in_euro`; without
    that override `resolve_replaced_asset` raises instead of inventing a sunk cost.
    """

    #: Service life assumed for a replaced asset that is priced by an override but has no
    #: database entry to read a service life from.
    FALLBACK_SERVICE_LIFE_IN_YEARS = 20.0


@dataclass
class DeviceCosting:
    """The resolved year-0 cost building blocks of one subject, per slot.

    Euro amounts are bands (`UncertainValue`, one value per uncertainty slot) at price-basis-year prices,
    cost-positive, already sized and multiplied by `facts.count`. It is what `investment.py`, `maintenance.py` and
    `subsidy_application.py` compute from, and its `provenance_ids` let `explain` trace a euro back to its data file.

    Units: `device_cost`, `installation_cost`, `planning_cost` and `removal_cost_of_replaced` are euro bands;
    `maintenance_rate` is a share of gross investment per year; `fixed_operation_cost` is euro per year;
    `service_life_years` is years; `embodied_co2_kg` is kilograms for the whole installation; `vat_rate` is a fraction;
    `energy_related_cost_share` is the coupled-cost share (1.0 = fully energy-related). `first_replacement_year` is
    years from year 0, already shortened by a kept asset's age.
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
    provenance_ids: Tuple[int, ...]
    is_new_investment: bool  # charged at year 0 in this installation context
    first_replacement_year: int  # relative year of the first replacement
    # Coupled-cost share and anyway threshold, resolved from the device entry:
    energy_related_cost_share: UncertainValue
    anyway_threshold_years: float
    replaced_asset: Optional[ExistingAsset] = None
    #: The whole year-0 purchase as the facts state it (`purchase_cost_override_in_euro`, a
    #: reader's quote), or None when the purchase is priced from its building blocks. It replaces
    #: device, installation, planning and removal *of this purchase only*: replacements, the
    #: residual of a replacement and the maintenance base stay `gross_investment`.
    purchase_override: Optional[UncertainValue] = None

    def purchase_blocks(self) -> Tuple[UncertainValue, UncertainValue, UncertainValue]:
        """Return the year-0 purchase as `(investment, planning, removal)`, the three year-0 categories.

        Without a stated purchase price these are the building blocks themselves. A stated price (a reader's quote)
        covers the whole job, so it is split in the proportions of the database's best-estimate blocks; a scheme
        funding planning alone still sees a planning share. When the database's blocks sum to zero, the whole quote is
        investment.

        Returns:
            The investment (device plus installation), planning and removal bands of the purchase.
        """
        investment = self.device_cost + self.installation_cost
        if self.purchase_override is None:
            return investment, self.planning_cost, self.removal_cost_of_replaced
        total = (
            investment.best_estimate
            + self.planning_cost.best_estimate
            + self.removal_cost_of_replaced.best_estimate
        )
        if total <= 0.0:
            zero = UncertainValue.exact(0.0)
            return self.purchase_override, zero, zero
        return (
            self.purchase_override.scale(investment.best_estimate / total),
            self.purchase_override.scale(self.planning_cost.best_estimate / total),
            self.purchase_override.scale(self.removal_cost_of_replaced.best_estimate / total),
        )

    @property
    def purchased_gross(self) -> UncertainValue:
        """The gross investment of the year-0 purchase: investment plus planning, removal excluded.

        Equals `gross_investment` without a stated purchase price, else the quote less its removal share. The year-0
        purchase is written down from it, and a coupled-cost anyway credit is a share of it.
        """
        if self.purchase_override is None:
            return self.gross_investment
        investment, planning, _removal = self.purchase_blocks()
        return investment + planning

    @property
    def gross_investment(self) -> UncertainValue:
        """I_gross = device + installation + planning (§3.6).

        Replacements re-purchase it escalated (§3.6 rule 2), the residual value writes it down straight-line (rule 3),
        and maintenance is a rate of it (rule 4). The removal cost of a replaced asset is not part of it; it is a
        one-off charge.
        """
        return self.device_cost + self.installation_cost + self.planning_cost


@dataclass
class ReplacedAssetOutcome:
    """The §4.1 consequences of replacing an existing asset, for one subject.

    `sunk_cost` is the replaced asset's written-off residual book value; it is reported but kept out of decision KPIs.
    `credit_entry` is the ANYWAY_COST_CREDIT timeline entry, present only when a credit is due; the anyway credit is
    the avoided cost of a replacement that would have been paid anyway. `credit_amount` is that credit as a positive
    figure, which feeds the modernization-levy basis (§6.4). All stored amounts are cost-positive euro bands; only the
    entry itself is negative.
    """

    sunk_cost: UncertainValue
    credit_entry: Optional[CashFlowEntry] = None
    credit_amount: UncertainValue = UncertainValue.exact(0.0)
    #: The anyway (Sowieso) share the credit was computed at, for the audit trail and captions.
    #: 1.0, a like-for-like replacement, when the register declares none.
    anyway_share: float = 1.0
    #: The cost the share was applied to, in nominal euro of the credit year, best-estimate slot:
    #: the escalated like-for-like replacement price, or on the coupled-cost branch the non-energy
    #: share of the new measure. `share x this = credit`, which the report shows. 0.0 when no
    #: credit is due.
    credit_basis_in_euro: float = 0.0
    #: Which of the two quantities that basis is, from `results.AnywayBasisKinds`, so the caption
    #: does not have to re-derive it.
    credit_basis_kind: str = AnywayBasisKinds.LIKE_FOR_LIKE


@dataclass(frozen=True)
class InstallationVerdict:
    """Whether one asset class is bought, kept or replacing something, decided before any price (§4.1).

    A replacement is a new investment naming the register entry it replaces; a kept asset is no investment and names
    the register entry it is; otherwise the subject is a new investment of nothing registered (GREENFIELD, or
    BROWNFIELD without a match) or, under STATUS_QUO, no investment.

    Attributes:
        is_new_investment: Whether the subject is charged at year 0.
        replaced_asset: The register entry the subject replaces, if any.
        kept_asset: The register entry the subject is, if it is a kept existing asset.

    Raises:
        ValueError: On a replacement that is not an investment, a kept asset that is an investment, or both a
            replacement and kept.
    """

    is_new_investment: bool
    replaced_asset: Optional[ExistingAsset] = None
    kept_asset: Optional[ExistingAsset] = None

    def __post_init__(self) -> None:
        """Refuse the combinations `installation_verdict` never produces."""
        if self.replaced_asset is not None and self.kept_asset is not None:
            raise ValueError("an installation verdict is a replacement or a kept asset, never both")
        if self.replaced_asset is not None and not self.is_new_investment:
            raise ValueError("a replacement is a new investment; a verdict that is not one replaces nothing")
        if self.kept_asset is not None and self.is_new_investment:
            raise ValueError("a kept existing asset is not a new investment")


def installation_verdict(
    asset_class: ComponentType,
    context: InstallationContext,
    register: Optional[ExistingAssetRegister],
    subject: Optional[str] = None,
    own_register_entry: bool = False,
) -> InstallationVerdict:
    """Decide the §4.1 installation context of one asset class without touching prices or the ledger.

    Under BROWNFIELD the replacement check runs first, so new windows replacing old windows count as an investment, not
    as kept. Under STATUS_QUO nothing is a new investment and nothing is replaced; a matching entry is a kept asset
    ageing toward its like-for-like replacement. The evaluator uses this before pricing to learn which asset classes an
    evaluation installs (the subsidy conditions' `package.*` fields).

    A register entry bound to a subject (`ExistingAsset.subject`) is seen only by that subject, and a subject with
    `own_register_entry` sees no other entry; a staged plan's increment on a kept subject uses its own entry.

    Args:
        asset_class: The subject's asset class.
        context: The perspective's installation context.
        register: The existing-asset register, or None for a greenfield run.
        subject: The subject asked about; only compared with bound entries.
        own_register_entry: The subject's `ComponentCostFacts.own_register_entry`.

    Returns:
        The verdict.

    Raises:
        ValueError: When more than one register entry is declared replaced by `asset_class`.
    """
    replaced_asset: Optional[ExistingAsset] = None
    kept_asset: Optional[ExistingAsset] = None
    is_new_investment = True
    if register is not None:
        visible = [
            asset
            for asset in register.assets
            if (asset.subject == subject if own_register_entry else asset.subject is None)
        ]
        register = ExistingAssetRegister(assets=visible)
    if context == InstallationContext.BROWNFIELD and register is not None:
        replaced = [asset for asset in register.assets if asset_class in asset.replaced_by_asset_classes]
        if len(replaced) > 1:
            raise ValueError(
                f"{len(replaced)} register entries are replaced by {asset_class.value!r}: "
                + " and ".join(
                    f"{asset.asset_class.value!r} ({asset.size:g} {asset.size_unit.value}, installed "
                    f"{asset.installation_year})"
                    for asset in replaced
                )
                + "; one subject replaces one asset, so the register must name one."
            )
        replaced_asset = replaced[0] if replaced else None
    if context in (InstallationContext.BROWNFIELD, InstallationContext.STATUS_QUO) and register is not None:
        if replaced_asset is None:
            kept_asset = register.find(asset_class)
            if kept_asset is not None:
                is_new_investment = False
    if context == InstallationContext.STATUS_QUO:
        is_new_investment = False
    return InstallationVerdict(
        is_new_investment=is_new_investment, replaced_asset=replaced_asset, kept_asset=kept_asset
    )


def _age(asset: ExistingAsset, ageing_reference_year: int) -> int:
    """Return the age a kept or replaced register asset is priced at (§4.1).

    The caller's stated age (`ExistingAsset.stated_age_in_years`, set by the staged evaluator for a subject an earlier
    stage bought) if there is one, else its floored age at the timeline's year 0 (`ExistingAsset.age_in_years`).
    """
    if asset.stated_age_in_years is not None:
        return asset.stated_age_in_years
    return asset.age_in_years(ageing_reference_year)


def resolve_device(
    subject: str,
    facts: ComponentCostFacts,
    context: InstallationContext,
    existing_assets: Optional[ExistingAssetRegister],
    ledger: ProvenanceLedger,
    database: CostDatabase,
    parameters: EconomicParameters,
    price_basis_year: int,
    ageing_reference_year: int,
) -> DeviceCosting:
    """Resolve one subject's cost building blocks and its installation context (§3.5, §4.1).

    For each building block (investment, installation, planning, maintenance rate, fixed operation cost, service life,
    embodied CO2) a component override wins over the country's device entry for the price basis year, and the winner is
    recorded in the provenance ledger. The installation context comes from `installation_verdict`: a new investment, a
    replacement of a registered asset, or a kept asset that costs nothing at year 0 and is first replaced at its
    remaining life. An unregistered class under STATUS_QUO gets its full service life as first replacement year.

    Args:
        subject: Timeline subject name (component instance or measure).
        facts: What the component declared: asset class, size, count, optional per-field overrides (§3.3). No prices.
        context: The perspective's installation context (§4.1).
        existing_assets: Register of what is already installed; None for a greenfield run.
        ledger: Provenance ledger; every priced field resolved here is recorded in it (§3.10).
        database: Loaded cost database (§3.5).
        parameters: Economic parameters; supply the country and the default `anyway_threshold_years`.
        price_basis_year: The economic "today" the device entries are looked up for, not the weather year.
        ageing_reference_year: Calendar year of the timeline's year 0, at which a kept asset's age is measured; the
            price basis year except for a staged plan (`StagedEvaluator.plan_year_zero`).

    Returns:
        A `DeviceCosting` with every block banded and sized, the installation verdict (`is_new_investment`,
            `first_replacement_year`, `replaced_asset`) and the provenance ids used.

    Raises:
        CostDataError: If no device entry exists for the asset class, country and year and the component does not
            override both the investment cost and the lifetime.
    """
    year = price_basis_year
    entry: Optional[DeviceEntry] = None
    provenance_ids: List[int] = []
    # The entry is resolved with the provenance of every field this subject is priced from. Fields
    # an override supersedes are not requested; the override's own record replaces them.
    priced_fields = [
        field_name
        for field_name, is_overridden in (
            ("specific_investment", facts.investment_cost_override_in_euro is not None),
            ("maintenance_rate_per_year", facts.maintenance_rate_override is not None),
            (
                "service_life_in_years",
                facts.lifetime_override_in_years is not None or facts.lifetime_of_asset_class is not None,
            ),
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
    elif facts.lifetime_of_asset_class is not None:
        # Part of another subject's system: renewed on that class's life.
        companion = database.resolve_device_entry(
            facts.lifetime_of_asset_class, year, parameters.country, ledger, ["service_life_in_years"]
        )
        service_life = companion.entry.service_life_in_years
        provenance_ids.append(companion.provenance_id("service_life_in_years"))
    else:
        assert resolved is not None
        service_life = resolved.entry.service_life_in_years
        provenance_ids.append(resolved.provenance_id("service_life_in_years"))
    purchase_override = facts.purchase_cost_override_in_euro
    if purchase_override is not None:
        provenance_ids.append(override_record("purchase_cost_override_in_euro", purchase_override))
    if facts.embodied_co2_override_in_kg is not None:
        embodied_co2 = facts.embodied_co2_override_in_kg
    elif entry is not None:
        embodied_co2 = entry.embodied_co2_for_size(facts.size) * facts.count
    else:
        embodied_co2 = 0.0

    # Installation context: matched-kept vs new measure vs replacement (§4.1), decided by
    # `installation_verdict`; only the replacement schedule of a kept asset is computed here.
    register = existing_assets
    verdict = installation_verdict(facts.asset_class, context, register, subject, facts.own_register_entry)
    is_new_investment = verdict.is_new_investment
    replaced_asset = verdict.replaced_asset
    first_replacement_year = int(round(service_life))
    if verdict.kept_asset is not None:
        # Kept asset: no investment; first replacement at service_life - current_age. Ages are
        # measured at the timeline's year 0 (the price basis year, or a staged plan's own year 0),
        # never at the weather year.
        age = _age(verdict.kept_asset, ageing_reference_year)
        first_replacement_year = max(1, int(round(service_life - age)))
    if context == InstallationContext.STATUS_QUO and register is None:
        log.warning(
            f"STATUS_QUO without an existing-asset register: treating {subject} "
            "as an existing asset of age 0."
        )
    if context == InstallationContext.STATUS_QUO and register is not None and verdict.kept_asset is None:
        first_replacement_year = int(round(service_life))

    removal_cost = UncertainValue.exact(0.0)
    if is_new_investment and replaced_asset is not None:
        # Disposal of the replaced device type (§3.5 removal_cost). A raw lookup: the removal entry
        # carries the measure's provenance ids and has no ledger record of its own.
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
        purchase_override=purchase_override,
    )


def resolve_replaced_asset(
    costing: DeviceCosting,
    gross: UncertainValue,
    database: CostDatabase,
    parameters: EconomicParameters,
    price_basis_year: int,
    ageing_reference_year: int,
    ledger: Optional[ProvenanceLedger] = None,
) -> ReplacedAssetOutcome:
    """Compute the sunk cost and the anyway credit for the asset a measure replaces (§4.1).

    Called only for measures charged at year 0 that replace a registered asset. The sunk cost is the old asset's
    straight-line residual book value, `like_for_like_price * remaining_life / service_life`; it is reported but kept
    out of decision KPIs. The anyway credit applies when the old asset had at most `anyway_threshold_years` left: the
    replacement it would have needed anyway is credited, so only the extra cost of the measure is charged.

    With `energy_related_cost_share < 1` (envelope measures, where scaffolding and render are paid anyway) the credit
    is the non-energy share of the measure's own gross cost; otherwise it is the old asset's like-for-like replacement.
    Either is escalated to `credit_year` (the remaining life, rounded) and then scaled by the anyway share, the
    fraction the counterfactual would really have paid: 1.0 for dead windows replaced by windows, only the repair share
    for a facade that was never insulated.

    Args:
        costing: The replacing measure's costing; supplies `replaced_asset` (with its anyway share), the coupled-cost
            share and the anyway threshold.
        gross: The measure's own gross investment band, used only on the coupled-cost branch.
        database: Loaded cost database, for the replaced asset's price and service life.
        parameters: Economic parameters: the country and the escalation-rate fallbacks.
        price_basis_year: The economic "today" the replaced asset's price is looked up for.
        ageing_reference_year: Calendar year of the timeline's year 0, at which the replaced asset's age is measured.
        ledger: Provenance ledger the applied anyway share is recorded in; optional for arithmetic-only tests.

    Returns:
        A `ReplacedAssetOutcome`. `sunk_cost` is always present (possibly zero); `credit_entry` is None unless a
            positive credit is due, in which case it is a negative ANYWAY_COST_CREDIT entry at `credit_year`.

    Raises:
        CostDataError: When the replaced asset's class has no database entry and the register declares no
            `replacement_cost_override_in_euro` for it.
    """
    replaced = costing.replaced_asset
    assert replaced is not None
    try:
        # Raw lookup, as for the removal cost above: the replaced asset's own entry adds no ledger record.
        old_entry = database.get_device_entry(replaced.asset_class, price_basis_year, parameters.country)
        like_for_like = (
            replaced.replacement_cost_override_in_euro
            or old_entry.investment_for_size(replaced.size)
        )
        old_life = old_entry.service_life_in_years
    except CostDataError as err:
        if replaced.replacement_cost_override_in_euro is None:
            # No entry and no declared price: the sunk cost and the anyway threshold would both be
            # invented, so refuse.
            raise CostDataError(
                f"Registered existing asset {replaced.asset_class.value} has no cost database "
                f"entry for {parameters.country} at price basis year {price_basis_year} and no "
                "replacement_cost_override_in_euro: its sunk cost and anyway-cost credit cannot "
                f"be established. Underlying lookup: {err}"
            ) from err
        like_for_like = replaced.replacement_cost_override_in_euro
        old_life = ContextResolutionConstants.FALLBACK_SERVICE_LIFE_IN_YEARS
    age = _age(replaced, ageing_reference_year)
    remaining = max(0.0, old_life - age)
    sunk_cost = like_for_like.scale(remaining / old_life if old_life else 0.0)
    if remaining > costing.anyway_threshold_years:
        return ReplacedAssetOutcome(sunk_cost=sunk_cost)

    credit_year = int(round(remaining))
    anyway_share = replaced.anyway_share
    share = costing.energy_related_cost_share
    if share.best_estimate < 1.0:
        # Coupled-cost credit: the non-energy share of the measure (scaffolding, render, standard
        # glazing) would have been spent anyway; it replaces the like-for-like credit, so the two
        # never double count.
        non_energy_share = UncertainValue(
            best_estimate=1.0 - share.best_estimate,
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
    # The anyway share is applied last, to whichever branch produced the credit, so the report can
    # show the escalated basis and the share as separate factors. The basis is captured before scaling.
    credit_basis = credit.best_estimate
    # The provenance detail names which quantity the basis is: the non-energy share of the new
    # measure on the coupled-cost branch, the escalated like-for-like cost otherwise.
    credit_basis_kind = (
        AnywayBasisKinds.NON_ENERGY_SHARE
        if share.best_estimate < 1.0
        else AnywayBasisKinds.LIKE_FOR_LIKE
    )
    credit_basis_description = (
        credit_basis_kind
        if share.best_estimate < 1.0
        else f"{credit_basis_kind} of {replaced.asset_class.value}"
    )
    credit = credit.scale(anyway_share)
    if credit.maximum <= 0:
        return ReplacedAssetOutcome(sunk_cost=sunk_cost)
    provenance_ids = costing.provenance_ids
    if ledger is not None:
        provenance_ids = provenance_ids + (
            ledger.record(
                ParameterProvenance(
                    parameter=f"{costing.subject}.anyway_share",
                    value=anyway_share,
                    origin=ParameterOrigin.CONFIG_OVERRIDE,
                    source_ids=("inline:existing-asset register (Sowieso-Kosten share, §4.1)",),
                    detail=(
                        f"anyway credit = {anyway_share:.0%} x {credit_basis_description} "
                        f"@ year {credit_year} "
                        f"({credit_basis:,.2f} EUR) = {credit.best_estimate:,.2f} EUR"
                    ),
                )
            ),
        )
    return ReplacedAssetOutcome(
        sunk_cost=sunk_cost,
        credit_entry=CashFlowEntry(
            year=credit_year,
            amount_in_euro=credit.as_revenue(),
            category=CostCategory.ANYWAY_COST_CREDIT,
            subject=costing.subject,
            provenance_ids=provenance_ids,
        ),
        credit_amount=credit,
        anyway_share=anyway_share,
        credit_basis_in_euro=credit_basis,
        credit_basis_kind=credit_basis_kind,
    )
