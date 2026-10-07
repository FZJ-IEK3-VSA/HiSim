"""Energy bills per carrier, billed for year 1 and projected over the horizon (cost_spec.md §3.6 rule 5, §8.5).

Per carrier the simulated billing determinants (kWh bought and sold, peaks) are annualized, billed once under the
tariff contract, and each bill component is escalated with its own rate:

- ENERGY_WORKING: the carrier rate; a controllable tariff's flexibility value escalates with the spread rate instead.
- ENERGY_STANDING: the general price escalation rate.
- ENERGY_CAPACITY_CHARGE: the grid-fee rate (the general rate when unset).
- FEED_IN_REVENUE: nominal for an EEG-style guaranteed duration, then the feed-in rate.
- ENERGY_CO2_PRICE: not escalated; read off the CO2 price path per year.

It also returns the operational CO2 mass per carrier (§3.8), which `calculators/co2.py` folds into the CO2 result, and
handles plan-stated energy prices.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import ClassVar, Dict, List, Optional, Tuple

from hisim.economics.calculators.annualization import (
    annualize_billing_determinants,
    check_simulated_period_fraction,
)
from hisim.economics.calculators.escalation import carrier_escalation_rate, escalate, escalation_factor
from hisim.economics.carriers import EnergyCarrier, revenue_subject
from hisim.economics.database import CostDatabase, EnergyPriceEntry
from hisim.economics.facts import BillingDeterminants
from hisim.economics.parameters import EconomicParameters, StatedEnergyPrice
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
    """Operational CO2 of one carrier in kg per year: emission factor times annualized energy bought (§3.8).

    `carrier_value` is `EnergyCarrier.value`, the subject string of the carrier's cash-flow entries.
    """

    carrier_value: str
    annual_emissions_in_kg: float
    #: The factor the mass above was computed with, in kg per kWh bought, so the CO2 section can
    #: show the multiplication. The price entry's `emission_factor_in_kg_per_kwh`, constant over
    #: the horizon (§3.8 asks for a per-year trend, which would make this a path).
    emission_factor_in_kg_per_kwh: float = 0.0


@dataclass
class EnergyFlowResult:
    """The cash flows, the CO2 mass and the diagnostics the energy calculator produces.

    `entries` are money for the timeline; `emissions` are kilograms for the separate CO2 accounting and are never
    summed with money (§3.8). `raw_flexibility_value_by_carrier` holds each carrier's flexibility value before the
    projection clamps it at zero; a negative value means the load was timed worse than a flat profile, which
    `plausibility.py` warns about. `tariffs_applied` lists the contract each carrier was billed under.
    """

    entries: List[CashFlowEntry] = field(default_factory=list)
    emissions: List[CarrierEmissions] = field(default_factory=list)
    #: Carrier value -> unclamped `TariffBill.flexibility_value_in_euro` of the year-1 bill.
    raw_flexibility_value_by_carrier: Dict[str, float] = field(default_factory=dict)
    #: The contract each carrier was billed under, in carrier order: the explicit one where the run
    #: supplied it, else the generated flat contract. The assumptions table publishes its prices.
    tariffs_applied: List[TariffContract] = field(default_factory=list)


class StatedPriceError(ValueError):
    """A plan-stated energy price the engine cannot bill as stated.

    Raised when an all-in working price is below the year-1 carbon price the engine books on top, or when a price is
    stated for a carrier the run bills under an explicit contract. The staged evaluator checks both first and reports
    `problems.json` rows; this is the guard for other callers.
    """


class StatedPrices:
    """Names for a plan-stated price: its contract id, provenance record and feed-in duration.

    The staged evaluator, the document echo and the tests read these same strings.
    """

    #: Infix of the id of a contract whose terms a plan stated; distinct from
    #: `TariffContract.DEFAULT_ID_INFIX`, so a stated contract is never taken for a generated one.
    STATED_ID_INFIX: ClassVar[str] = "_STATED_"

    #: What a stated price's provenance record says it is, and where it came from.
    DETAIL: ClassVar[str] = "stated in the economics plan (parameters.energy_prices)"

    #: The pseudo-source a stated price cites: a household's own bill has no registry entry, and
    #: `inline:` is the convention for that.
    SOURCE_ID: ClassVar[str] = f"inline:{DETAIL}"

    #: The dotted parameter path a stated field is recorded under in the provenance ledger.
    PARAMETER_PATH: ClassVar[str] = "parameters.energy_prices.{carrier}.{field}"

    #: How long a feed-in rate is held nominally fixed, stated or from the database (EEG
    #: convention, §8.5).
    FEED_IN_DURATION_IN_YEARS: ClassVar[int] = 20

    #: Tolerance of the "not below the year-1 carbon price" check, in EUR/kWh: a price echoed by a
    #: document and fed back in is the database's plus the carbon price, which may differ from it
    #: in the last bit.
    TOLERANCE_IN_EURO_PER_KWH: ClassVar[float] = 1e-12

    @classmethod
    def contract_id(cls, country: str, carrier: EnergyCarrier, year: int) -> str:
        """Return the id of the contract a carrier with stated terms is billed under.

        Args:
            country: The country the plan is priced for.
            carrier: The carrier the contract bills.
            year: The year of the price entry supplying the unstated terms.

        Returns:
            `"<country>_STATED_<carrier>_<year>"`.
        """
        return f"{country}{cls.STATED_ID_INFIX}{carrier.value}_{year}"

    @classmethod
    def parameter(cls, carrier: EnergyCarrier, field_name: str) -> str:
        """Return the provenance parameter path of one stated field of one carrier."""
        return cls.PARAMETER_PATH.format(carrier=carrier.value, field=field_name)

    @classmethod
    def stated_for(
        cls, carrier: EnergyCarrier, parameters: EconomicParameters
    ) -> Tuple[Optional[StatedEnergyPrice], Optional[StatedEnergyPrice]]:
        """Return the terms a plan states for one carrier's contract: its own and, for electricity, feed-in.

        Args:
            carrier: The carrier whose contract is being built.
            parameters: The run's parameters.

        Returns:
            `(the carrier's stated terms or None, the stated feed-in terms or None)`; the second is only set for
                ELECTRICITY, whose contract carries the feed-in rate. Terms that state nothing are None.
        """

        def stated(terms: Optional[StatedEnergyPrice]) -> Optional[StatedEnergyPrice]:
            if terms is None or terms == StatedEnergyPrice():
                return None
            return terms

        own = stated(parameters.energy_prices.get(carrier))
        feed_in = (
            stated(parameters.energy_prices.get(EnergyCarrier.ELECTRICITY_FEED_IN))
            if carrier == EnergyCarrier.ELECTRICITY
            else None
        )
        return own, feed_in


def contract_from_price_entry(entry: EnergyPriceEntry, country: str) -> TariffContract:
    """Build the default flat contract from a §3.5 energy price entry.

    A carrier without an explicit contract is billed flat at the database price. This lives in the engine rather than
    in `tariffs.py`, which holds only contract data, parsing and billing.
    """
    return TariffContract(
        id=TariffContract.default_contract_id(country, entry.carrier, entry.year),
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
    """Build the default flat contract of a carrier from the §3.5 price entries, feed-in included (§8.2).

    Records nothing in the ledger: `build_energy_flows` records the working price against the same entry, and the
    feed-in rate gets no record of its own.
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
            duration_in_years=StatedPrices.FEED_IN_DURATION_IN_YEARS,
        )
    return contract


def year_one_co2_price_per_kwh(
    entry: EnergyPriceEntry, parameters: EconomicParameters, database: CostDatabase, price_basis_year: int
) -> float:
    """Return the carbon price booked on top of one kWh's working price in year 1, in EUR/kWh.

    Exposure times emission factor times the CO2 price of the price basis year. Zero when the entry declares no
    exposure or the run prices no carbon (`co2_price_scenario: "none"`). A stated all-in working price is this plus the
    contract's working price.

    Args:
        entry: The carrier's price entry at the price basis year.
        parameters: The run's parameters, for the country and the CO2 price scenario.
        database: The cost database, for the CO2 price path.
        price_basis_year: The economic "today".

    Returns:
        The year-1 carbon price per kWh bought.
    """
    if entry.co2_price_exposure <= 0:
        return 0.0
    path = database.get_co2_price_path(parameters.country, parameters.co2_price_scenario)
    if path is None:
        return 0.0
    return entry.co2_price_exposure * entry.emission_factor_in_kg_per_kwh * path.price(price_basis_year) / 1000.0


def with_stated_terms(
    contract: TariffContract,
    entry: EnergyPriceEntry,
    parameters: EconomicParameters,
    co2_per_kwh: float,
) -> TariffContract:
    """Return a carrier's default contract with the terms a plan stated put in place.

    A stated working price is the all-in year-1 price, so the year-1 carbon price is subtracted and the rest is billed
    as the working price. A stated standing charge replaces the fixed annual charge, and a stated ELECTRICITY_FEED_IN
    price becomes the electricity contract's fixed feed-in rate for 20 years. Unstated terms stay the database's. A
    contract with anything stated gets its own id, so the assumptions table does not cite a database entry for it.

    Args:
        contract: The carrier's default contract, from `default_contract`.
        entry: The carrier's price entry at the price basis year.
        parameters: The run's parameters, holding the stated terms.
        co2_per_kwh: `year_one_co2_price_per_kwh` of the entry.

    Returns:
        The contract to bill under; the given one when nothing is stated for its carrier.

    Raises:
        StatedPriceError: If a stated working price is below the year-1 carbon price.
    """
    carrier = contract.carrier
    own, feed_in = StatedPrices.stated_for(carrier, parameters)
    if own is None and feed_in is None:
        return contract
    supply = contract.supply
    standing = contract.standing_charge_in_euro_per_year
    if own is not None and own.working_price_in_euro_per_kwh is not None:
        stated = own.working_price_in_euro_per_kwh
        if stated.minimum < co2_per_kwh - StatedPrices.TOLERANCE_IN_EURO_PER_KWH:
            raise StatedPriceError(
                f"the stated all-in working price of {carrier.value} ({stated.minimum:.6g} EUR/kWh at "
                f"its lowest) is below the year-1 carbon price of {co2_per_kwh:.6g} EUR/kWh the engine "
                "books on top of the working price, so no working price would be left."
            )
        working = stated if not co2_per_kwh else stated - UncertainValue.exact(co2_per_kwh)
        supply = replace(supply, working_price_in_euro_per_kwh=working)
    if own is not None and own.standing_charge_in_euro_per_year is not None:
        standing = own.standing_charge_in_euro_per_year
    remuneration = contract.feed_in
    if feed_in is not None and feed_in.working_price_in_euro_per_kwh is not None:
        remuneration = FeedIn(
            kind=FeedInKind.FIXED_TARIFF,
            rate_in_euro_per_kwh=feed_in.working_price_in_euro_per_kwh,
            duration_in_years=StatedPrices.FEED_IN_DURATION_IN_YEARS,
        )
    return replace(
        contract,
        id=StatedPrices.contract_id(parameters.country, carrier, entry.year),
        supply=supply,
        standing_charge_in_euro_per_year=standing,
        feed_in=remuneration,
        source_ids=tuple(contract.source_ids) + (StatedPrices.SOURCE_ID,),
        is_default_contract=False,
    )


def priced_contract(
    carrier: EnergyCarrier, year: int, database: CostDatabase, parameters: EconomicParameters
) -> TariffContract:
    """Return the contract for a carrier without an explicit one: the default, with stated terms in place.

    Args:
        carrier: The carrier to price.
        year: The price basis year.
        database: The cost database.
        parameters: The run's parameters, holding the country and any stated terms.

    Returns:
        `with_stated_terms` applied to `default_contract`.

    Raises:
        StatedPriceError: See `with_stated_terms`.
    """
    entry = database.get_energy_price(carrier, year, parameters.country)
    contract = default_contract(carrier, year, database, parameters.country)
    return with_stated_terms(
        contract, entry, parameters, year_one_co2_price_per_kwh(entry, parameters, database, year)
    )


def _stated_record(
    ledger: ProvenanceLedger, carrier: EnergyCarrier, field_name: str, value: UncertainValue, detail: str
) -> int:
    """Record one stated field as a REQUEST-origin provenance record and return its id.

    Args:
        ledger: The evaluation's provenance ledger.
        carrier: The carrier the field was stated for.
        field_name: The stated field.
        value: The value as the plan stated it.
        detail: What the record says about it.

    Returns:
        The interned record id.
    """
    return ledger.record(
        ParameterProvenance(
            parameter=StatedPrices.parameter(carrier, field_name),
            value=value,
            origin=ParameterOrigin.REQUEST,
            source_ids=(StatedPrices.SOURCE_ID,),
            detail=detail,
        )
    )


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
    """Return each carrier's energy cost flows over years 1..T and its operational CO2 (§3.6 rule 5, §8.5).

    Per carrier: annualize the measured determinants, bill them once under the tariff contract for a year-1 bill, then
    repeat it for each year with every component escalated at its own rate (see the module docstring). The flexibility
    value (what load shifting saved against the year's average price) is added into the working band, escalated at the
    spread rate and subtracted back out, so it escalates separately from the volume. The CO2 price component uses the
    path value of calendar year `price_basis_year + t - 1` and is emitted only when the entry's `co2_price_exposure >
    0`; entries whose working price already includes carbon declare 0 exposure (§3.5).

    Args:
        billing: One `BillingDeterminants` per carrier over the simulated period (kWh, peaks, optional integrated cost
            or revenue).
        tariff_contracts: Explicit contracts by carrier; others are billed under `priced_contract`, flat at the
            database price.
        simulated_period_fraction: Simulated share of a year; every determinant is divided by it to annualize.
        ledger: Provenance ledger; each carrier's working price and annualized purchase are recorded and cited by its
            entries.
        database: Cost database: price entries, escalation defaults and the CO2 price path.
        parameters: Economic parameters: country, escalation rates, CO2 price scenario.
        price_basis_year: The economic "today" prices and the CO2 path are anchored on, not the weather year.
        horizon: Observation period T in years.
        macro: MACROECONOMIC accounting (§4.5): strips the tax and levy share from the working price and drops feed-in
            revenue and the CO2 price component, all transfers; `calculators/co2.py` adds the CO2 damage cost instead.

    Returns:
        An `EnergyFlowResult`. Entries are nominal euros of their year, cost-positive except negative feed-in, carriers
            in input order and years ascending. `emissions` are kg per year per carrier, unaffected by `macro`.
    """
    params = parameters
    year = price_basis_year
    fraction = simulated_period_fraction
    check_simulated_period_fraction(fraction)
    result = EnergyFlowResult()
    for determinants in billing:
        carrier = determinants.carrier
        annualized = annualize_billing_determinants(determinants, fraction)
        own_terms, feed_in_terms = StatedPrices.stated_for(carrier, params)
        explicit = tariff_contracts.get(carrier)
        if explicit is not None and (own_terms is not None or feed_in_terms is not None):
            raise StatedPriceError(
                f"{carrier.value} is billed under the explicit contract {explicit.id!r}, and a "
                "price stated in parameters.energy_prices cannot replace its terms: the "
                "contract's price signal may have driven the simulation."
            )
        stated_working = own_terms.working_price_in_euro_per_kwh if own_terms is not None else None
        stated_standing = own_terms.standing_charge_in_euro_per_year if own_terms is not None else None
        stated_feed_in = feed_in_terms.working_price_in_euro_per_kwh if feed_in_terms is not None else None
        # One call resolves the price entry and records the provenance of the field this bill is
        # priced from. A working price the plan stated is recorded as the plan's instead, so the
        # database's figure, which then bills nothing, is not cited.
        resolved_price = database.resolve_energy_price(
            carrier,
            year,
            params.country,
            ledger,
            ("working_price_in_euro_per_kwh",) if stated_working is None else (),
        )
        price_entry = resolved_price.entry
        co2_per_kwh = year_one_co2_price_per_kwh(price_entry, params, database, year)
        contract = explicit or with_stated_terms(
            default_contract(carrier, year, database, params.country), price_entry, params, co2_per_kwh
        )
        if stated_working is None:
            price_provenance = resolved_price.provenance_id("working_price_in_euro_per_kwh")
        else:
            detail = StatedPrices.DETAIL + "; the all-in year-1 price"
            if co2_per_kwh:
                detail += (
                    f", less the year-1 CO2 price of {co2_per_kwh:.6g} EUR/kWh booked separately, is "
                    f"billed as a working price of "
                    f"{contract.supply.working_price_in_euro_per_kwh.best_estimate:.6g} EUR/kWh"
                )
            price_provenance = _stated_record(
                ledger, carrier, StatedEnergyPrice.WORKING_PRICE_KEY, stated_working, detail
            )
        energy_provenance = ledger.record(
            ParameterProvenance(
                parameter=f"simulation.{carrier.value}.energy_bought",
                value=annualized.energy_bought_in_kwh,
                origin=ParameterOrigin.SIMULATION_OUTPUT,
                detail=f"annualized from simulated fraction {fraction:.4f}",
            )
        )
        provenance_ids: Tuple[int, ...] = (price_provenance, energy_provenance)
        standing_provenance_ids = provenance_ids
        if stated_standing is not None:
            standing_provenance_ids = (
                _stated_record(
                    ledger,
                    carrier,
                    StatedEnergyPrice.STANDING_CHARGE_KEY,
                    stated_standing,
                    StatedPrices.DETAIL + "; replaces the fixed annual charge",
                ),
            )
        elif stated_working is not None:
            standing_provenance_ids = (
                database.provenance_for_price(price_entry, ledger, StatedEnergyPrice.STANDING_CHARGE_KEY),
            )
        feed_in_provenance_ids = provenance_ids
        if stated_feed_in is not None:
            feed_in_provenance_ids = (
                _stated_record(
                    ledger,
                    EnergyCarrier.ELECTRICITY_FEED_IN,
                    StatedEnergyPrice.WORKING_PRICE_KEY,
                    stated_feed_in,
                    StatedPrices.DETAIL + "; the fixed feed-in rate of the electricity contract",
                ),
                energy_provenance,
            )
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
        # escalate the volume effect and the correction apart. The raw figure travels on the result
        # so the plausibility panel can flag a load timed worse than the mean price.
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
                        provenance_ids=standing_provenance_ids,
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
                # EEG-style fixed tariffs stay nominal for their duration (§8.5).
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
                        subject=revenue_subject(carrier),
                        subject_kind=SubjectKind.CARRIER,
                        provenance_ids=feed_in_provenance_ids,
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
            CarrierEmissions(
                carrier_value=carrier.value,
                annual_emissions_in_kg=annual_emissions,
                emission_factor_in_kg_per_kwh=emission_factor,
            )
        )
        result.tariffs_applied.append(contract)
    return result
