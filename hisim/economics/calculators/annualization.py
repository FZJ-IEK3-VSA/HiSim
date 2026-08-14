"""Annualization of a partially simulated year (cost-spec-v2 W3.5).

A simulation may cover less than a full year. Everything the engine projects over the horizon
is a *per-year* figure, so simulated quantities are scaled up by the simulated period fraction
(simulated seconds / seconds of a full year) before they enter the timeline — linear
extrapolation, cost_spec.md §3.6 rule 5.

W3.5 note (the discrepancy this module makes visible rather than fixes): the two former call
sites guarded the division differently.

* `_add_energy_flows` validated the fraction up front (``<= 0`` is a hard error) and then
  divided by it unguarded.
* `_add_subsidies` divided by ``max(fraction, 1e-9)`` — an unreachable guard in practice, since
  the energy calculator has already rejected non-positive fractions by the time it runs, but a
  *different* number for fractions in ``(0, 1e-9)``.

Both semantics are preserved here behind the ``guard_zero`` flag so that this package stays a
pure refactor; unifying them is package 2b's decision.

The module owns *only* the extrapolation from a simulated period to a full year. It does not
price anything, does not escalate anything and never touches years 1..T: it runs once per
carrier at the very front of `calculators/energy.py` (and once per subsidized measure in
`calculators/subsidy_application.py`), so every figure the rest of the pipeline sees is already
a per-year figure. `fraction` is a dimensionless share in (0, 1] — simulated seconds divided by
the seconds of a full year, computed by `EvaluationInputs.simulated_period_fraction` — and the
result carries the input's unit per year (kWh -> kWh/a, EUR -> EUR/a).

Realizes: cost_spec.md §3.6 rule 5 (annualized energy flows).
"""

from __future__ import annotations

from typing import Optional

from hisim import log
from hisim.economics.facts import BillingDeterminants


class AnnualizationConstants:
    """Numeric bounds of the annualization rules (§3.6 rule 5).

    A single named constant rather than a literal at the division site, so the one number that
    distinguishes the two historical guard styles (see the module docstring) is greppable and can
    be deleted in one place once package 2b unifies them. It is a floor on the *divisor*, not a
    physically meaningful fraction — no simulation produces a period shorter than a nanosecond of
    a year.
    """

    #: Lower bound the subsidy call site clamps the fraction to before dividing (see module docstring).
    ZERO_GUARD = 1e-9


def annualization_divisor(fraction: float, guard_zero: bool = False) -> float:
    """The divisor that turns a simulated-period quantity into a per-year one (W3.5).

    Factored out of `annualize` so that the *only* difference between the two historical call
    styles is visible as one function with one flag, instead of two divisions in two files that a
    reviewer has to diff by eye. Callers never need it directly; it exists for the record.

    Args:
        fraction: Simulated share of a year, dimensionless, normally in (0, 1].
        guard_zero: Reproduce the subsidy site's ``max(fraction, 1e-9)`` clamp. Only changes the
            result for fractions in ``(0, ZERO_GUARD)``, which the energy calculator has already
            rejected in practice.

    Returns:
        The divisor to use, dimensionless.
    """
    return max(fraction, AnnualizationConstants.ZERO_GUARD) if guard_zero else fraction


def annualize(value: float, fraction: float, guard_zero: bool = False) -> float:
    """Scales a simulated-period quantity up to a full year (§3.6 rule 5).

    Linear extrapolation, deliberately: the spec rules out re-simulating or seasonally reweighting
    a partial year, and states the assumption openly instead (a warning is raised by
    :func:`check_simulated_period_fraction`). Used on *extensive* quantities only — energy volumes
    and integrated cost/revenue — never on prices, peaks or rates, which are intensive and already
    per-year figures.

    Args:
        value: An extensive simulated-period quantity (kWh over the simulated period, or euro).
        fraction: Simulated share of a year, dimensionless.
        guard_zero: See :func:`annualization_divisor` (W3.5).

    Returns:
        The same quantity per year (kWh/a, EUR/a).
    """
    return value / annualization_divisor(fraction, guard_zero)


def annualize_optional(value: Optional[float], fraction: float, guard_zero: bool = False) -> Optional[float]:
    """`annualize` for figures that may be absent (integrated cost/revenue).

    `BillingDeterminants.cost_integrated_in_euro` and `revenue_integrated_in_euro` are `None`
    unless a meter actually integrated a price signal over the simulation, and "absent" must stay
    absent rather than become 0.0 — a zero would read as "the meter measured no cost", which the
    dynamic-tariff decomposition of §8.5 would then trust.

    Returns:
        `None` when `value` is `None`, otherwise the annualized figure in the input's unit per year.
    """
    return None if value is None else annualize(value, fraction, guard_zero)


def check_simulated_period_fraction(fraction: float) -> None:
    """Rejects a non-positive fraction and warns about extrapolating a partial year (§3.6).

    The engine's one gate on the annualization assumption, called once by
    `calculators/energy.build_energy_flows` before any bill is annualized. A non-positive fraction
    is a broken input record rather than an edge case (it would divide by zero or flip signs), so
    it aborts; a fraction below a full year is legitimate but changes what the results *mean*, so
    it is surfaced as a warning that names the extrapolation explicitly.

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
    """A copy of the billing determinants with every *quantity* scaled to a full year.

    Prices, peaks and mean spot prices are intensive figures and stay as measured; only the
    energy volumes and the integrated cost/revenue are extensive (§3.6 rule 5, §8).

    This is the single point at which a simulated period becomes a *billing year*: everything
    downstream — `apply_tariff`, the §8.5 decomposition, the emission factors — reads the returned
    record and may assume annual quantities. A new copy is returned rather than the input mutated,
    because the same `BillingDeterminants` are evaluated once per perspective and must not
    accumulate scalings.

    Args:
        determinants: One carrier's measured billing determinants over the simulated period.
        fraction: Simulated share of a year, dimensionless. Unguarded division (W3.5) — the caller
            has validated it with :func:`check_simulated_period_fraction`.

    Returns:
        A new `BillingDeterminants` for the same carrier with kWh volumes and integrated euro
        figures per year, and every intensive field copied unchanged.
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
