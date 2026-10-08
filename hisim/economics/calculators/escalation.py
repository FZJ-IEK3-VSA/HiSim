"""Price escalation: the compound factor `(1 + rate)**n` and the rate fallback chains (cost_spec.md §3.2, §3.6).

Rates are nominal annual price-change rates as fractions (0.02 = 2 %/a); a negative rate expresses a learning curve.
Callers choose the exponent: year-anchored flows (a replacement in year t, the residual value, the anyway credit) use
the year itself, while recurring payments quoted at year-1 prices (maintenance, energy, feed-in) use `year - 1`, so
year 1 is unescalated. Discounting is not done here. Rate values live in `EconomicParameters` and
`escalation_defaults_<COUNTRY>.json`.
"""

from __future__ import annotations

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.parameters import EconomicParameters
from hisim.economics.results import RateOrigin, ResolvedRate
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


def escalation_factor(rate: float, years: int) -> float:
    """Return the compound escalation factor ``(1 + rate)**years`` for a plain float.

    Example: `escalation_factor(0.02, 2)` is 1.0404. Used where a scalar, not a band, is escalated; everything else
    goes through :func:`escalate`.

    Args:
        rate: Nominal annual price-change rate as a fraction; may be negative.
        years: Compounding exponent in years; 0 yields exactly 1.0.

    Returns:
        The dimensionless multiplier.
    """
    return (1.0 + rate) ** years


def escalate(amount: UncertainValue, rate: float, years: int) -> UncertainValue:
    """Escalate a banded amount by ``(1 + rate)**years`` in every slot (§3.2).

    A band is an `UncertainValue`: a minimum, best estimate and maximum. The factor is a non-negative scalar, so the
    slots stay a coherent triple and a revenue band keeps its sign.

    Args:
        amount: A euro band at its quoting year's price level.
        rate: Nominal annual price-change rate as a fraction.
        years: Compounding exponent: the flow's own year, or ``year - 1`` for payments quoted at year-1 prices.

    Returns:
        The band in nominal euros of the escalated year.
    """
    return amount.scale(escalation_factor(rate, years))


def carrier_escalation_rate(
    carrier: EnergyCarrier, parameters: EconomicParameters, database: CostDatabase
) -> float:
    """Return a carrier's energy price escalation rate (§3.2).

    The chain is: the explicit parameter, else the country defaults file, else the general rate.
    `calculators/energy.py` applies it to the working price of every projected year. The chain lets a scenario change
    one carrier without touching data files, and a country without a defaults entry still gets a rate.

    Args:
        carrier: The energy carrier being priced.
        parameters: The run's economic parameters; also supply the country and the general rate.
        database: Loaded cost database, consulted for `escalation_defaults_<COUNTRY>.json`.

    Returns:
        A nominal annual rate as a fraction.
    """
    return resolve_carrier_escalation_rate(carrier, parameters, database).rate


def resolve_carrier_escalation_rate(
    carrier: EnergyCarrier, parameters: EconomicParameters, database: CostDatabase
) -> ResolvedRate:
    """Return the carrier escalation rate together with which step of the fallback chain produced it.

    The report's assumptions section cites the origin of every rate; :func:`carrier_escalation_rate` delegates here.

    Args:
        carrier: The energy carrier being priced.
        parameters: The run's economic parameters.
        database: Loaded cost database, consulted for `escalation_defaults_<COUNTRY>.json`.

    Returns:
        The rate, its origin and, for a defaults-file hit, that file's source ids.
    """
    if carrier in parameters.energy_price_escalation_rates:
        return ResolvedRate(
            rate=parameters.energy_price_escalation_rates[carrier], origin=RateOrigin.CONFIGURATION
        )
    defaults = database.get_escalation_defaults(parameters.country)
    if carrier in defaults.carrier_rates:
        return ResolvedRate(
            rate=defaults.carrier_rates[carrier],
            origin=RateOrigin.COUNTRY_DEFAULTS,
            source_ids=list(defaults.source_ids),
        )
    return ResolvedRate(
        rate=parameters.general_price_escalation_rate, origin=RateOrigin.GENERAL_FALLBACK
    )


def investment_escalation_rate(
    asset_class: ComponentType, parameters: EconomicParameters, database: CostDatabase
) -> float:
    """Return an asset class's investment escalation rate through the same three-step chain (§3.2).

    Technology prices diverge (PV and batteries fall, labour-heavy trades rise), so the rate is per asset class. Used
    for replacements, the residual value and the anyway credit. The shipped per-class tables are empty until reviewed
    sources exist, so the chain normally ends at the general investment rate.

    Args:
        asset_class: The `ComponentType` of the subject being escalated.
        parameters: The run's economic parameters; also supply the country and the fallback rate.
        database: Loaded cost database, consulted for `escalation_defaults_<COUNTRY>.json`.

    Returns:
        A nominal annual rate as a fraction; negative for a falling technology cost.
    """
    return resolve_investment_escalation_rate(asset_class, parameters, database).rate


def resolve_investment_escalation_rate(
    asset_class: ComponentType, parameters: EconomicParameters, database: CostDatabase
) -> ResolvedRate:
    """Return the investment escalation rate together with which step of the fallback chain produced it.

    :func:`investment_escalation_rate` delegates here; the assumptions section cites the origin.

    Args:
        asset_class: The `ComponentType` of the subject being escalated.
        parameters: The run's economic parameters.
        database: Loaded cost database, consulted for `escalation_defaults_<COUNTRY>.json`.

    Returns:
        The rate, its origin and, for a defaults-file hit, that file's source ids.
    """
    if asset_class in parameters.investment_price_escalation_rates:
        return ResolvedRate(
            rate=parameters.investment_price_escalation_rates[asset_class],
            origin=RateOrigin.CONFIGURATION,
        )
    defaults = database.get_escalation_defaults(parameters.country)
    if asset_class in defaults.asset_class_rates:
        return ResolvedRate(
            rate=defaults.asset_class_rates[asset_class],
            origin=RateOrigin.COUNTRY_DEFAULTS,
            source_ids=list(defaults.source_ids),
        )
    return ResolvedRate(
        rate=parameters.investment_price_escalation_rate, origin=RateOrigin.GENERAL_FALLBACK
    )
