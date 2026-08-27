"""Price escalation: one audited helper plus the two rate-fallback chains (cost-spec-v2 W3.1).

Escalation used to be spelled out at ~9 sites in `evaluator.py` with five different rates
(replacements, residual value, anyway-cost credit, maintenance, energy working/standing,
spread, grid fee, feed-in). They all compute the same compound factor; only the rate and the
exponent convention differ, and the exponent convention is *not* uniform:

* **Year-anchored** sites use the flow's own year as exponent — a replacement in year `t`
  costs `gross * (1 + rate)**t`, a residual value is escalated to its last installation year,
  the anyway-cost credit to the year it falls due.
* **Recurring-payment** sites use `year - 1`: maintenance, energy working/standing/capacity and
  feed-in revenue are quoted as *year-1* figures, so year 1 is unescalated (`(1+rate)**0`) and
  escalation only starts in year 2 (cost_spec.md §3.6 rule 4/5).

Both conventions stay exactly as they were; this module only removes the duplication of the
`(1.0 + rate) ** n` expression itself. Callers pass the exponent they always passed.

**Units and conventions.** All rates are *nominal* annual price-change rates as fractions
(0.02 = 2 %/a), never real rates and never discount rates — escalation moves a price *forward*
in nominal terms, discounting moves money *back*, and the two never appear in the same function
here (discounting lives in `timeline.py` / `calculators/aggregation.py`). Amounts are euro and
keep their sign, so escalating a revenue-mirrored band leaves it a revenue. A negative rate is
meaningful and used: it is how a learning curve (PV, batteries) is expressed.

The module owns the escalation *arithmetic* and the *lookup order* for a rate; it owns no rate
values — those live in `EconomicParameters` and in `escalation_defaults_<COUNTRY>.json` (§3.5) —
and it decides nothing about which flows get escalated, which is each calculator's own business.

Realizes: cost_spec.md §3.2 (escalation rates and their fallback chains), §3.6 rules 2-5.
"""

from __future__ import annotations

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.parameters import EconomicParameters
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType


def escalation_factor(rate: float, years: int) -> float:
    """The compound escalation factor ``(1 + rate)**years``.

    `years` is the exponent the call site needs, not necessarily the flow's year: recurring
    payments quoted at year-1 prices pass ``year - 1`` (see the module docstring).

    The scalar form, for the two sites that escalate a plain float rather than a band (the
    flexibility value of §8.5 and the feed-in tariff once its guaranteed duration has run out).
    Everything else goes through :func:`escalate`.

    Args:
        rate: Nominal annual price-change rate as a fraction; may be negative (learning curve).
        years: Compounding exponent in years; 0 yields exactly 1.0.

    Returns:
        A dimensionless multiplier applied to a price or an amount.
    """
    return (1.0 + rate) ** years


def escalate(amount: UncertainValue, rate: float, years: int) -> UncertainValue:
    """Escalates a banded amount by ``(1 + rate)**years``, slot-wise (§3.2).

    The one function every escalated euro in the engine passes through — replacements, residual
    value, the anyway-cost credit, maintenance and fixed O&M, and each energy bill component
    (W3.1). Because the factor is a non-negative scalar it is applied with `UncertainValue.scale`,
    so the three slots stay a coherent cheap/best-estimate/expensive triple and the sign of a
    revenue-mirrored band is preserved (§3.9).

    Args:
        amount: A banded euro amount at its quoting year's price level, cost-positive.
        rate: Nominal annual price-change rate as a fraction.
        years: Compounding exponent — the flow's own year for year-anchored sites, ``year - 1``
            for recurring payments quoted at year-1 prices (see the module docstring).

    Returns:
        The same band in nominal euros of the escalated year.
    """
    return amount.scale(escalation_factor(rate, years))


def carrier_escalation_rate(
    carrier: EnergyCarrier, parameters: EconomicParameters, database: CostDatabase
) -> float:
    """Fallback chain: explicit parameter -> country defaults file -> general rate (§3.2).

    Answers "how fast does this carrier's energy price rise?" for `calculators/energy.py`, which
    applies it to the working-price component of every projected year. The three-step chain is the
    §3.2 contract: a run-specific assumption on `EconomicParameters` beats the country's reviewed
    default table, which beats the catch-all general escalation rate — so a scenario axis can move
    one carrier without touching data files, and a country without a defaults entry still gets a
    defined answer instead of an error.

    Args:
        carrier: The energy carrier being priced.
        parameters: The run's economic parameters (also supplies the country and the general rate).
        database: Loaded cost database, consulted for `escalation_defaults_<COUNTRY>.json`.

    Returns:
        A nominal annual rate as a fraction.
    """
    if carrier in parameters.energy_price_escalation_rates:
        return parameters.energy_price_escalation_rates[carrier]
    defaults = database.get_escalation_defaults(parameters.country)
    if carrier in defaults.carrier_rates:
        return defaults.carrier_rates[carrier]
    return parameters.general_price_escalation_rate


def investment_escalation_rate(
    asset_class: ComponentType, parameters: EconomicParameters, database: CostDatabase
) -> float:
    """Fallback chain for per-asset-class investment escalation (learning curves, §3.2).

    The capital-cost twin of :func:`carrier_escalation_rate`, and the same three-step chain. It
    exists per asset class because technology price trajectories genuinely diverge — PV and battery
    prices fall while labor-heavy trades rise — and a single investment escalation rate would price
    a replacement heat pump and a replacement inverter identically. `calculators/investment.py`
    uses it for replacements and the residual value, `calculators/context_resolution.py` for the
    anyway-cost credit; the shipped per-asset-class tables are empty on purpose (spec Q2) until
    reviewed sources exist, so today the chain normally ends at the general investment rate.

    Args:
        asset_class: The `ComponentType` of the subject being escalated.
        parameters: The run's economic parameters (also supplies the country and the fallback rate).
        database: Loaded cost database, consulted for `escalation_defaults_<COUNTRY>.json`.

    Returns:
        A nominal annual rate as a fraction; negative for a falling technology cost.
    """
    if asset_class in parameters.investment_price_escalation_rates:
        return parameters.investment_price_escalation_rates[asset_class]
    defaults = database.get_escalation_defaults(parameters.country)
    if asset_class in defaults.asset_class_rates:
        return defaults.asset_class_rates[asset_class]
    return parameters.investment_price_escalation_rate
