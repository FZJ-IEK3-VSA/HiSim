"""Energy bills projected over the horizon (cost-spec-v2 §2.3).

The §2.3 "energy bills" calculator. Per carrier it annualizes the simulated billing
determinants, applies the tariff contract once to get a *year-1* bill, and then projects that
bill over years 1..T, escalating each bill component with its own rate (cost_spec.md §3.6
rule 5, §8.5):

===========================  =========================================================
component                    escalation rate
===========================  =========================================================
ENERGY_WORKING               the carrier rate; the flexibility value earned by a
                             controllable tariff is escalated with the *spread* rate
                             instead and subtracted back out
ENERGY_STANDING              the general price escalation rate
ENERGY_CAPACITY_CHARGE       the grid-fee rate (general rate when unset)
FEED_IN_REVENUE              nominal for the guaranteed duration of an EEG-style fixed
                             tariff, then the feed-in rate (§8.5, spec Q10)
ENERGY_CO2_PRICE             not escalated — read off the CO2 price path per year
===========================  =========================================================

Two things this calculator produces besides cash flows: the operational CO2 *mass* per carrier
(a parallel, undiscounted accounting that must never be summed with the CO2 price or the CO2
damage cost, §3.8) and, under the macroeconomic accounting, a working price stripped of taxes
and levies (§4.5).

**Threading note.** The operational CO2 mass is returned as a typed per-carrier figure rather
than accumulated into a shared `LifecycleCo2Result`; `calculators/co2.py` folds it in, so the
addition order into the per-year array stays exactly as it was (carrier by carrier, year
ascending).

Realizes: cost_spec.md §3.5 (price entries, emission factors, CO2 exposure), §3.6 rule 5,
§3.8 (CO2 accounting), §4.5 (macroeconomic view), §8 (tariffs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from hisim.economics.calculators.annualization import (
    annualize_billing_determinants,
    check_simulated_period_fraction,
)
from hisim.economics.calculators.escalation import carrier_escalation_rate, escalate, escalation_factor
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase, EnergyPriceEntry
from hisim.economics.facts import BillingDeterminants
from hisim.economics.parameters import EconomicParameters
from hisim.economics.provenance import ParameterOrigin, ParameterProvenance, ProvenanceLedger
from hisim.economics.tariffs import (
    FeedIn,
    FeedInKind,
    SupplyKind,
    TariffContract,
    TariffSupply,
    apply_tariff,
)
from hisim.economics.timeline import CashFlowEntry, CostCategory, SubjectKind
from hisim.economics.uncertainty import UncertainValue


@dataclass
class CarrierEmissions:
    """Operational CO2 of one carrier, in kg per year (§3.8).

    The typed hand-over that keeps this calculator free of the shared CO2 accumulator: emission
    factor times annualized purchased energy, computed here because the price entry that carries
    the factor is already open, but folded into `LifecycleCo2Result` by `calculators/co2.py`.
    `carrier_value` is `EnergyCarrier.value`, i.e. the same string the carrier's cash-flow entries
    use as their timeline subject, so mass and money join without a second mapping.
    """

    carrier_value: str
    annual_emissions_in_kg: float


@dataclass
class EnergyFlowResult:
    """The cash flows and the CO2 mass the energy calculator produces.

    Two outputs, deliberately separate: `entries` are money and go on the timeline, `emissions`
    are kilograms and go into the parallel CO2 accounting that must never be summed with them
    (§3.8). Returning both instead of mutating shared state is what makes the energy calculator
    reviewable on its own — everything it produces is in its return value.

    The third field is diagnostics rather than result: the flexibility value **before** the clamp
    that keeps the §8.5 projection well-formed. A negative one means the simulated load was timed
    *worse* than a flat profile at the mean spot price, which is a plausible-looking bill built on
    an implausible dispatch; carrying it out of the calculator is what lets `plausibility.py` warn
    about it without the calculator importing the panel (issue #25b).
    """

    entries: List[CashFlowEntry] = field(default_factory=list)
    emissions: List[CarrierEmissions] = field(default_factory=list)
    #: Carrier value -> unclamped `TariffBill.flexibility_value_in_euro` of the year-1 bill.
    raw_flexibility_value_by_carrier: Dict[str, float] = field(default_factory=dict)


def contract_from_price_entry(entry: EnergyPriceEntry, country: str) -> TariffContract:
    """The default flat contract generated from a §3.5 energy price entry (behavioral no-op).

    Moved here from `TariffContract.default_from_price_entry` (cost-spec-v2 §2.2/§2.5): turning
    a price entry into a contract is *engine* behavior — it decides that a carrier without a
    contract is billed flat at the database price — and it made the tariff data module depend on
    the device/price catalog. `tariffs.py` now keeps contract data, parsing and the billing
    engine; the whole default-contract construction lives here, in one place.
    """
    return TariffContract(
        id=f"{country}_DEFAULT_{entry.carrier.value}_{entry.year}",
        carrier=entry.carrier,
        country=country,
        region=None,
        valid_from_year=entry.year,
        supply=TariffSupply(
            kind=SupplyKind.FLAT,
            working_price_in_euro_per_kwh=entry.working_price_in_euro_per_kwh,
        ),
        standing_charge_in_euro_per_year=entry.standing_charge_in_euro_per_year,
        source_ids=entry.source_ids,
        is_default_contract=True,
    )


def default_contract(
    carrier: EnergyCarrier, year: int, database: CostDatabase, country: str
) -> TariffContract:
    """Default flat contract from the §3.5 price entries, feed-in included (§8.2).

    Contract *construction*, not pricing: it takes no ledger and records nothing (W2.1). The
    working price it copies is recorded against the same entry by `build_energy_flows`, which
    interns to one record; the feed-in rate has no record of its own — unchanged, and noted as
    the one remaining gap of the energy path.
    """
    entry = database.get_energy_price(carrier, year, country)
    contract = contract_from_price_entry(entry, country)
    if carrier == EnergyCarrier.ELECTRICITY and database.has_energy_price(
        EnergyCarrier.ELECTRICITY_FEED_IN, country
    ):
        feed_in_entry = database.get_energy_price(EnergyCarrier.ELECTRICITY_FEED_IN, year, country)
        contract.feed_in = FeedIn(
            kind=FeedInKind.FIXED_TARIFF,
            rate_in_euro_per_kwh=feed_in_entry.working_price_in_euro_per_kwh,
            duration_in_years=20,
        )
    return contract


def build_energy_flows(
    billing: List[BillingDeterminants],
    tariff_contracts: Dict[EnergyCarrier, TariffContract],
    simulated_period_fraction: float,
    ledger: ProvenanceLedger,
    database: CostDatabase,
    parameters: EconomicParameters,
    price_basis_year: int,
    horizon: int,
    macro: bool,
) -> EnergyFlowResult:
    """Per-carrier energy cost flows projected over the horizon (§3.6 rule 5, §8.5).

    The whole operating-cost half of the engine, in one loop over carriers: annualize what the
    meters measured, bill it *once* under the carrier's tariff contract to obtain a year-1 bill,
    then repeat that bill for years 1..T with each component escalated at its own rate (the table
    in the module docstring). Billing once and projecting is the §8.5 decision — re-simulating
    twenty spot-price years would be spurious precision, and escalating a single lump sum would
    hide that the volume effect, the flexibility value and the grid fees move differently.

    Three subtleties a reviewer should check here rather than elsewhere. The **flexibility value**
    (what load shifting saved against paying the year's average price) is added into the working
    band, escalated at the *spread* rate and then subtracted back out, which is how it ends up
    escalating at its own rate while the volume effect escalates at the carrier rate. **Feed-in
    revenue** stays nominally fixed while an EEG-style guaranteed duration lasts and only escalates
    afterwards (spec Q10). The **CO2 price component** is read off the trajectory for the calendar
    year `price_basis_year + t - 1` and is never escalated, and it is emitted only when the price
    entry declares `co2_price_exposure > 0` — entries whose working price already contains carbon
    costs declare 0 exposure, which is the §3.5 rule against double counting.

    Args:
        billing: One `BillingDeterminants` per carrier, as measured over the simulated period
            (kWh, peaks, optional integrated cost/revenue).
        tariff_contracts: Explicit contracts by carrier; a carrier without one is billed under
            :func:`default_contract`, i.e. flat at the database price.
        simulated_period_fraction: Simulated share of a year; validated once here, then used to
            annualize every determinant (§3.6 rule 5).
        ledger: Provenance ledger; the working price and the annualized purchase volume of every
            carrier are interned into it and cited by all of that carrier's entries.
        database: Loaded cost database — price entries, escalation defaults and the CO2 price path.
        parameters: Economic parameters — country, escalation rates, CO2 price scenario.
        price_basis_year: The economic "today". Prices are looked up for it and the CO2 trajectory
            is anchored on it, deliberately not on the simulated weather year.
        horizon: Observation period T in years; entries are emitted for years 1..T.
        macro: MACROECONOMIC accounting (§4.5). Strips the carrier's tax and levy share from the
            working price and suppresses both the feed-in revenue and the CO2 price component,
            all three being transfers rather than resource costs; the CO2 externality re-enters as
            the damage cost of `calculators/co2.py`.

    Returns:
        An `EnergyFlowResult` whose `entries` are nominal, undiscounted euro amounts of their own
        year — cost-positive for working, standing, capacity and CO2 price, revenue-mirrored
        (negative) for feed-in — with carriers in input order and years ascending within a
        carrier; and whose `emissions` carry each carrier's operational CO2 in kg per year,
        unaffected by `macro`.
    """
    params = parameters
    year = price_basis_year
    fraction = simulated_period_fraction
    check_simulated_period_fraction(fraction)
    result = EnergyFlowResult()
    for determinants in billing:
        carrier = determinants.carrier
        annualized = annualize_billing_determinants(determinants, fraction)
        contract = tariff_contracts.get(carrier) or default_contract(
            carrier, year, database, params.country
        )
        # W2.1: one call resolves the price entry and records the provenance of the field this
        # bill is priced from; the two used to be separate steps a calculator could get wrong.
        resolved_price = database.resolve_energy_price(
            carrier, year, params.country, ledger, ("working_price_in_euro_per_kwh",)
        )
        price_entry = resolved_price.entry
        price_provenance = resolved_price.provenance_id("working_price_in_euro_per_kwh")
        energy_provenance = ledger.record(
            ParameterProvenance(
                parameter=f"simulation.{carrier.value}.energy_bought",
                value=annualized.energy_bought_in_kwh,
                origin=ParameterOrigin.SIMULATION_OUTPUT,
                detail=f"annualized from simulated fraction {fraction:.4f}",
            )
        )
        provenance_ids = (price_provenance, energy_provenance)
        bill = apply_tariff(annualized, contract)

        if macro:
            # Strip taxes/levies and VAT from the working price (§4.5). Migrated AS_LEGACY
            # entries carry tax_and_levy_share=0, so this is approximate for them.
            strip = 1.0 - price_entry.tax_and_levy_share
            working_component: Optional[UncertainValue] = bill.by_category.get(CostCategory.ENERGY_WORKING)
            if working_component is not None:
                bill.by_category[CostCategory.ENERGY_WORKING] = working_component.scale(strip)

        carrier_rate = carrier_escalation_rate(carrier, params, database)
        spread_rate = params.spread_escalation_rate if params.spread_escalation_rate is not None else carrier_rate
        grid_rate = (
            params.grid_fee_escalation_rate
            if params.grid_fee_escalation_rate is not None
            else params.general_price_escalation_rate
        )
        co2_path = database.get_co2_price_path(params.country, params.co2_price_scenario)
        emission_factor = price_entry.emission_factor_in_kg_per_kwh
        annual_emissions = annualized.energy_bought_in_kwh * emission_factor

        working_band = bill.by_category.get(CostCategory.ENERGY_WORKING, UncertainValue.exact(0.0))
        # The clamp keeps the §8.5 decomposition well-formed: a negative flexibility value would
        # project a *rising* volume effect against a shrinking correction and escalate the two
        # apart. The raw figure travels on the result instead of being lost here, so the
        # plausibility panel can flag a load that was timed worse than the mean price (issue #25b)
        # — the calculator itself never judges, that is the layering plausibility.py depends on.
        result.raw_flexibility_value_by_carrier[carrier.value] = bill.flexibility_value_in_euro
        flexibility = max(0.0, bill.flexibility_value_in_euro)
        volume_band = working_band + UncertainValue.exact(flexibility)

        for projection_year in range(1, horizon + 1):
            working = escalate(volume_band, carrier_rate, projection_year - 1)
            if flexibility:
                working = working - escalate(
                    UncertainValue.exact(flexibility), spread_rate, projection_year - 1
                )
            if working.maximum != 0 or working.minimum != 0:
                result.entries.append(
                    CashFlowEntry(
                        year=projection_year,
                        amount_in_euro=working,
                        category=CostCategory.ENERGY_WORKING,
                        subject=carrier.value,
                        subject_kind=SubjectKind.CARRIER,
                        provenance_ids=provenance_ids,
                    )
                )
            standing = bill.by_category.get(CostCategory.ENERGY_STANDING)
            if standing is not None and (standing.maximum or standing.minimum):
                result.entries.append(
                    CashFlowEntry(
                        year=projection_year,
                        amount_in_euro=escalate(
                            standing, params.general_price_escalation_rate, projection_year - 1
                        ),
                        category=CostCategory.ENERGY_STANDING,
                        subject=carrier.value,
                        subject_kind=SubjectKind.CARRIER,
                        provenance_ids=provenance_ids,
                    )
                )
            capacity = bill.by_category.get(CostCategory.ENERGY_CAPACITY_CHARGE)
            if capacity is not None and capacity.maximum:
                result.entries.append(
                    CashFlowEntry(
                        year=projection_year,
                        amount_in_euro=escalate(capacity, grid_rate, projection_year - 1),
                        category=CostCategory.ENERGY_CAPACITY_CHARGE,
                        subject=carrier.value,
                        subject_kind=SubjectKind.CARRIER,
                        provenance_ids=provenance_ids,
                    )
                )
            feed_in = bill.by_category.get(CostCategory.FEED_IN_REVENUE)
            if feed_in is not None and not macro and feed_in.minimum != 0:
                # EEG-style fixed tariffs stay nominal for their duration (§8.5, spec Q10).
                within_duration = projection_year <= contract.feed_in.duration_in_years
                feed_escalation = (
                    1.0
                    if within_duration
                    else escalation_factor(params.feed_in_escalation_rate, projection_year - 1)
                )
                result.entries.append(
                    CashFlowEntry(
                        year=projection_year,
                        amount_in_euro=feed_in.scale(feed_escalation),
                        category=CostCategory.FEED_IN_REVENUE,
                        subject=EnergyCarrier.ELECTRICITY_FEED_IN.value
                        if carrier == EnergyCarrier.ELECTRICITY
                        else carrier.value,
                        subject_kind=SubjectKind.CARRIER,
                        provenance_ids=provenance_ids,
                    )
                )
            # Explicit CO2 price component (§3.5): exposure share of emissions.
            if not macro and co2_path is not None and price_entry.co2_price_exposure > 0 and annual_emissions:
                # The CO2 path is anchored on the price basis year (the economic "today").
                co2_price = co2_path.price(year + projection_year - 1)
                amount = annual_emissions * price_entry.co2_price_exposure * co2_price / 1000.0
                if amount:
                    result.entries.append(
                        CashFlowEntry(
                            year=projection_year,
                            amount_in_euro=UncertainValue.exact(amount),
                            category=CostCategory.ENERGY_CO2_PRICE,
                            subject=carrier.value,
                            subject_kind=SubjectKind.CARRIER,
                            provenance_ids=provenance_ids,
                        )
                    )
        result.emissions.append(
            CarrierEmissions(carrier_value=carrier.value, annual_emissions_in_kg=annual_emissions)
        )
    return result
