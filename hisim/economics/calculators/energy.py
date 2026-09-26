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
    """Operational CO2 of one carrier, in kg per year (§3.8).

    The typed hand-over that keeps this calculator free of the shared CO2 accumulator: emission
    factor times annualized purchased energy, computed here because the price entry that carries
    the factor is already open, but folded into `LifecycleCo2Result` by `calculators/co2.py`.
    `carrier_value` is `EnergyCarrier.value`, i.e. the same string the carrier's cash-flow entries
    use as their timeline subject, so mass and money join without a second mapping.
    """

    carrier_value: str
    annual_emissions_in_kg: float
    #: The factor the mass above was computed with, in kg per kWh bought. Carried alongside the
    #: product so the CO2 section can state the multiplication instead of asserting its result; it
    #: is the price entry's `emission_factor_in_kg_per_kwh`, constant over the horizon in v1.
    #: Revisit when an energy price entry carries a per-year factor: cost_spec.md §3.8 asks for a
    #: per-carrier emission trend, and at that point this single float becomes a path and the
    #: report's "factor x kWh" line becomes one row per year rather than one per carrier.
    emission_factor_in_kg_per_kwh: float = 0.0


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
    #: The contract each carrier was actually billed under, in carrier order — the explicit one
    #: where the run supplied it and the generated flat contract otherwise. The assumptions table
    #: publishes its working price, standing charge and feed-in rate, and only the calculator
    #: knows which of the two contracts won.
    tariffs_applied: List[TariffContract] = field(default_factory=list)


class StatedPriceError(ValueError):
    """A stated energy price the engine cannot bill as stated (renovisorissues #52).

    Raised for the two conditions under which a price stated in `EconomicParameters.energy_prices`
    has no honest reading: an all-in working price below the carbon price the engine books on top
    of the working price in year 1, and a stated price for a carrier the run bills under an
    explicit contract, whose price signal may have driven the simulation. The staged evaluator
    checks both before it prices anything and reports them as ``problems.json`` rows; this error
    is the calculator's own guard for every other caller.
    """


class StatedPrices:
    """The vocabulary of a stated price inside the energy calculator, in one place.

    Everything the calculator writes about a price a plan stated — the contract id it bills under,
    the provenance record it cites, the feed-in duration — is named here, so the staged evaluator,
    the document echo and the tests read the same strings the calculator writes.
    """

    #: Infix of the id of a contract whose terms a plan stated; distinct from
    #: `TariffContract.DEFAULT_ID_INFIX`, so a stated contract is never taken for a generated one.
    STATED_ID_INFIX: ClassVar[str] = "_STATED_"

    #: What a stated price's provenance record says it is, and where it came from.
    DETAIL: ClassVar[str] = "stated in the economics plan (parameters.energy_prices)"

    #: The pseudo-source a stated price cites: there is no registry entry behind a household's
    #: own bill, and `inline:` is the convention for exactly that (W2.4).
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
        """The id of the contract a carrier with stated terms is billed under.

        Args:
            country: The country the plan is priced for.
            carrier: The carrier the contract bills.
            year: The price entry's year the unstated terms come from.

        Returns:
            ``"<country>_STATED_<carrier>_<year>"``.
        """
        return f"{country}{cls.STATED_ID_INFIX}{carrier.value}_{year}"

    @classmethod
    def parameter(cls, carrier: EnergyCarrier, field_name: str) -> str:
        """The provenance parameter path of one stated field of one carrier."""
        return cls.PARAMETER_PATH.format(carrier=carrier.value, field=field_name)

    @classmethod
    def stated_for(
        cls, carrier: EnergyCarrier, parameters: EconomicParameters
    ) -> Tuple[Optional[StatedEnergyPrice], Optional[StatedEnergyPrice]]:
        """The terms a plan states for one carrier's contract: its own and, for electricity, feed-in.

        Args:
            carrier: The carrier whose contract is being built.
            parameters: The run's parameters.

        Returns:
            ``(the carrier's stated terms or None, the stated feed-in terms or None)``; the second
            is only ever set for `ELECTRICITY`, the carrier whose contract carries the feed-in rate.
            Terms that state nothing are None, so they never mint a stated contract.
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
    """The default flat contract generated from a §3.5 energy price entry (behavioral no-op).

    Moved here from `TariffContract.default_from_price_entry` (cost-spec-v2 §2.2/§2.5): turning
    a price entry into a contract is *engine* behavior — it decides that a carrier without a
    contract is billed flat at the database price — and it made the tariff data module depend on
    the device/price catalog. `tariffs.py` now keeps contract data, parsing and the billing
    engine; the whole default-contract construction lives here, in one place.
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
            duration_in_years=StatedPrices.FEED_IN_DURATION_IN_YEARS,
        )
    return contract


def year_one_co2_price_per_kwh(
    entry: EnergyPriceEntry, parameters: EconomicParameters, database: CostDatabase, price_basis_year: int
) -> float:
    """The carbon price the calculator books on top of one kWh's working price in year 1, in EUR/kWh.

    The per-kWh form of the ``ENERGY_CO2_PRICE`` component of :func:`build_energy_flows`: exposure
    times emission factor times the CO2 price of the price basis year, which is the year the path
    is read at for projection year 1. Zero when the entry declares no exposure or the run prices
    no carbon (``co2_price_scenario: "none"``). A stated all-in working price is this plus the
    working price the contract bills (`StatedEnergyPrice`).

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
    """The default contract of one carrier with the terms a plan stated put in place.

    The whole reading of `EconomicParameters.energy_prices` (renovisorissues #52): a stated
    working price is the all-in year-1 price, so the year-1 carbon price the calculator books
    separately is taken off it and the rest is billed as the working price; a stated standing
    charge replaces the fixed annual charge; a stated ``ELECTRICITY_FEED_IN`` price becomes the
    electricity contract's fixed feed-in rate for the usual 20 years. Every unstated term is the
    database's, as :func:`default_contract` built it. A contract with anything stated gets its own
    id and is no longer a generated default, so the assumptions table does not cite a database
    entry for a household's own bill.

    Args:
        contract: The carrier's default contract, from :func:`default_contract`.
        entry: The carrier's price entry at the price basis year.
        parameters: The run's parameters, holding the stated terms.
        co2_per_kwh: :func:`year_one_co2_price_per_kwh` of the entry.

    Returns:
        The contract to bill under; the one given when nothing is stated for its carrier.

    Raises:
        StatedPriceError: If a stated working price is below the year-1 carbon price, which would
            leave a negative working price.
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
    """The contract a carrier without an explicit one is billed under: the default, stated terms in place.

    Args:
        carrier: The carrier to price.
        year: The price basis year.
        database: The cost database.
        parameters: The run's parameters, holding the country and any stated terms.

    Returns:
        :func:`with_stated_terms` of :func:`default_contract`.

    Raises:
        StatedPriceError: See :func:`with_stated_terms`.
    """
    entry = database.get_energy_price(carrier, year, parameters.country)
    contract = default_contract(carrier, year, database, parameters.country)
    return with_stated_terms(
        contract, entry, parameters, year_one_co2_price_per_kwh(entry, parameters, database, year)
    )


def _stated_record(
    ledger: ProvenanceLedger, carrier: EnergyCarrier, field_name: str, value: UncertainValue, detail: str
) -> int:
    """Record one stated field as a REQUEST-origin provenance record, returning its id.

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
        # W2.1: one call resolves the price entry and records the provenance of the field this
        # bill is priced from; the two used to be separate steps a calculator could get wrong. A
        # working price the plan stated is recorded as the plan's instead (#52), so the database's
        # figure, which bills nothing then, is not cited.
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
