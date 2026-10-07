"""Scale quantities from a partially simulated year up to a full year (cost_spec.md §3.6 rule 5).

Everything the engine projects over the horizon is a per-year figure, so simulated quantities are extrapolated linearly
by the simulated period fraction (simulated seconds divided by the seconds of a full year, in (0, 1]). Example: 120 kWh
over a quarter year becomes 480 kWh/a. Runs once per carrier at the front of `calculators/energy.py` and once per
subsidized measure in `calculators/subsidy_application.py`. The two call sites guard the division differently, selected
by the ``guard_zero`` flag: the subsidy site clamps the fraction to a tiny floor, the energy site rejects non-positive
fractions up front.
"""

from __future__ import annotations

from typing import Optional

from hisim import log
from hisim.economics.facts import BillingDeterminants


class AnnualizationConstants:
    """Numeric bounds of the annualization rules (§3.6 rule 5).

    `ZERO_GUARD` is a floor on the divisor used by the subsidy call site, not a meaningful fraction.
    """

    #: Lower bound the subsidy call site clamps the fraction to before dividing (see module docstring).
    ZERO_GUARD = 1e-9


def annualization_divisor(fraction: float, guard_zero: bool = False) -> float:
    """Return the divisor that turns a simulated-period quantity into a per-year one.

    Args:
        fraction: Simulated share of a year, dimensionless, normally in (0, 1].
        guard_zero: Clamp the fraction to ``ZERO_GUARD`` first, as the subsidy call site does; only changes the result
            for fractions below that floor.

    Returns:
        The divisor, dimensionless.
    """
    return max(fraction, AnnualizationConstants.ZERO_GUARD) if guard_zero else fraction


def annualize(value: float, fraction: float, guard_zero: bool = False) -> float:
    """Scale a simulated-period quantity up to a full year by linear extrapolation (§3.6 rule 5).

    Use it on extensive quantities only (energy volumes, integrated cost or revenue), never on prices, peaks or rates.
    :func:`check_simulated_period_fraction` warns about the extrapolation.

    Args:
        value: The quantity over the simulated period (kWh or euro).
        fraction: Simulated share of a year, dimensionless.
        guard_zero: See :func:`annualization_divisor`.

    Returns:
        The same quantity per year (kWh/a, EUR/a).
    """
    return value / annualization_divisor(fraction, guard_zero)


def annualize_optional(value: Optional[float], fraction: float, guard_zero: bool = False) -> Optional[float]:
    """`annualize` for figures that may be absent, such as integrated cost or revenue.

    `BillingDeterminants.cost_integrated_in_euro` and `revenue_integrated_in_euro` are None unless a meter integrated a
    price signal, and None must stay None: a zero would read as "the meter measured no cost".

    Returns:
        None when `value` is None, otherwise the annualized figure per year.
    """
    return None if value is None else annualize(value, fraction, guard_zero)


def check_simulated_period_fraction(fraction: float) -> None:
    """Reject a non-positive simulated period fraction and warn when a partial year is extrapolated (§3.6).

    Called once by `calculators/energy.build_energy_flows` before any bill is annualized.

    Args:
        fraction: Simulated share of a year, dimensionless.

    Raises:
        ValueError: If `fraction` is zero or negative.
    """
    if fraction <= 0:
        raise ValueError("simulated_period_fraction must be > 0.")
    if fraction < 0.999:
        log.warning(
            f"Simulated period covers {fraction:.2%} of a year; energy flows are annualized "
            "by linear extrapolation (§3.6)."
        )


def annualize_billing_determinants(
    determinants: BillingDeterminants, fraction: float
) -> BillingDeterminants:
    """Return a copy of the billing determinants with every extensive quantity scaled to a full year.

    Billing determinants are the measured quantities a tariff is applied to (kWh bought and sold, peaks, spot prices).
    Energy volumes and integrated cost or revenue are scaled; prices, peaks and mean spot prices stay as measured (§3.6
    rule 5, §8). Everything downstream assumes annual quantities. A copy is returned because the same input is
    evaluated once per perspective.

    Args:
        determinants: One carrier's billing determinants over the simulated period.
        fraction: Simulated share of a year; divided by unguarded, so the caller must have checked it with
            :func:`check_simulated_period_fraction`.

    Returns:
        A new `BillingDeterminants` for the same carrier with per-year volumes and euro figures.
    """
    return BillingDeterminants(
        carrier=determinants.carrier,
        energy_bought_in_kwh=annualize(determinants.energy_bought_in_kwh, fraction),
        energy_sold_in_kwh=annualize(determinants.energy_sold_in_kwh, fraction),
        energy_bought_per_band_in_kwh={
            band: annualize(energy, fraction)
            for band, energy in determinants.energy_bought_per_band_in_kwh.items()
        },
        cost_integrated_in_euro=annualize_optional(determinants.cost_integrated_in_euro, fraction),
        revenue_integrated_in_euro=annualize_optional(determinants.revenue_integrated_in_euro, fraction),
        peak_per_billing_period_in_kw=determinants.peak_per_billing_period_in_kw,
        annual_peak_in_kw=determinants.annual_peak_in_kw,
        mean_spot_price_in_euro_per_kwh=determinants.mean_spot_price_in_euro_per_kwh,
    )
