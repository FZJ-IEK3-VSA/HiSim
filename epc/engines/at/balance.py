"""The monthly heating balance of the Austrian engine, under the reference and under the site climate.

Per month, B 8110-6-1:2024-03 balances the losses Q_l = Q_T + Q_V (Eq. (28)–(30)) against the gains
Q_g = Q_i + Q_s, utilised by η (Eq. (57a), (57b) with γ = Q_g / Q_l, Eq. (58), and a = a_0 + τ / τ_0,
Eq. (59), τ = C / (L_T + L_V), Eq. (49), C = f_BW · V, Eq. (50)), and weights the month with its share
of the heating period:

- **reference climate** (Eq. (52)): f_h of H 5050-1:2024-03 Eq. (9a)–(9j), from γ of the month and the
  means with the month before and after, December next to January; a month whose losses are not
  positive has no rule there and is refused (``E-14``, D-R1), which Annex A never reaches;
- **site climate** (Eq. (53)): d_Heiz / MT_j with d_Heiz = f_H · d of H 5056-1:2024-03 7.1, Eq. (49)–(52b),
  which replaces the γ of a month without positive losses by that of the next month with positive
  losses; such a month is outside the heating period (H 5050-1:2024-03 6.1, read as f = 0 where
  θ_e ≥ θ_i,h). Recoverable hot-water losses Q_TW,beh are 0 in stage 1.

Nothing is rounded between the steps (IEEE double).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from epc.engines.at.climate import Climate
from epc.engines.at.envelope import Envelope
from epc.engines.at.table_reading import Monthly
from epc.engines.at.usage import MONTH_DURATION_IN_H
from epc.model.errors import EpcError, EpcErrorKind

#: B 8110-6-1:2024-03 Eq. (59): a_0 and τ_0 in h, heating case.
UTILISATION_A0 = 1.0
UTILISATION_TAU0_IN_H = 16.0

#: The days of each month, January to December.
MONTH_DAYS: Tuple[float, ...] = tuple(hours / 24 for hours in MONTH_DURATION_IN_H)


@dataclass(frozen=True)
class Dynamics:
    """The building's thermal inertia, shared by both climates (I-3).

    Attributes:
        thermal_capacity_in_wh_per_k: C = f_BW · V, Eq. (50).
        time_constant_in_h: τ = C / (L_T + L_V), Eq. (49).
        utilisation_parameter: a = a_0 + τ / τ_0, Eq. (59).
        gain_loss_ratio_limit: γ_lim = (a + 1) / a, H 5050-1 Eq. (9a), H 5056-1 Eq. (50).
    """

    thermal_capacity_in_wh_per_k: float
    time_constant_in_h: float
    utilisation_parameter: float
    gain_loss_ratio_limit: float


@dataclass(frozen=True)
class Balance:
    """The monthly balance under one climate, January to December, in kWh per month.

    Attributes:
        transmission_losses: Q_T, Eq. (29a).
        ventilation_losses: Q_V, Eq. (30).
        losses: Q_l = Q_T + Q_V, Eq. (28) (I-1).
        gains: Q_g = Q_i + Q_s.
        utilisation: η of the month, ``None`` for a month without positive losses.
        heating_share: f_h (reference climate) or d_Heiz / MT (site climate), 0 … 1 (I-2).
        heating_days: The month's days times its heating share.
        heating_demand: Q_h of the month, Eq. (52) or (53), never negative (I-2).
        annual_heating_demand: Q_h,a, Eq. (51).
    """

    transmission_losses: Monthly
    ventilation_losses: Monthly
    losses: Monthly
    gains: Monthly
    utilisation: Tuple[Optional[float], ...]
    heating_share: Monthly
    heating_days: Monthly
    heating_demand: Monthly
    annual_heating_demand: float


def dynamics(envelope: Envelope, construction_factor: float, gross_volume_in_m3: float) -> Dynamics:
    """C, τ, a and γ_lim of a building (Eq. (49), (50), (59)).

    Args:
        envelope: The building's conductances.
        construction_factor: f_BW in Wh/(m³ · K).
        gross_volume_in_m3: V.

    Returns:
        The building's thermal inertia.
    """
    capacity = construction_factor * gross_volume_in_m3
    conductance = envelope.transmission_conductance_in_w_per_k + envelope.ventilation_conductance_in_w_per_k
    time_constant = capacity / conductance
    a = UTILISATION_A0 + time_constant / UTILISATION_TAU0_IN_H
    return Dynamics(capacity, time_constant, a, (a + 1) / a)


def balance(
    envelope: Envelope,
    inertia: Dynamics,
    set_temperature_in_c: float,
    climate: Climate,
    gains: Sequence[float],
    reference: bool,
) -> Balance:
    """The monthly heating balance under one climate.

    Args:
        envelope: The building's conductances.
        inertia: The building's thermal inertia.
        set_temperature_in_c: θ_i,h of the usage profile.
        climate: The climate's monthly outdoor temperatures.
        gains: Q_g = Q_i + Q_s per month, in kWh, under this climate.
        reference: ``True`` for the reference climate (Eq. (52)), ``False`` for the site climate (Eq. (53)).

    Returns:
        The balance.

    Raises:
        EpcError: ``E-14`` for a reference-climate month whose losses are not positive.
    """
    differences = [set_temperature_in_c - outdoor for outdoor in climate.outdoor_temperatures_in_c]
    transmission = tuple(
        envelope.transmission_conductance_in_w_per_k * difference * hours / 1000
        for difference, hours in zip(differences, MONTH_DURATION_IN_H)
    )
    ventilation = tuple(
        envelope.ventilation_conductance_in_w_per_k * difference * hours / 1000
        for difference, hours in zip(differences, MONTH_DURATION_IN_H)
    )
    losses = tuple(q_t + q_v for q_t, q_v in zip(transmission, ventilation))
    ratios: List[Optional[float]] = [gain / loss if loss > 0 else None for gain, loss in zip(gains, losses)]
    if reference:
        for month, (gain, loss) in enumerate(zip(gains, losses), start=1):
            if loss <= 0:
                raise EpcError(EpcErrorKind.NON_POSITIVE_LOSSES, m=month, value=gain / loss if loss else "infinite")
        shares = heating_shares([ratio for ratio in ratios if ratio is not None], inertia.gain_loss_ratio_limit)
        days = tuple(share * month_days for share, month_days in zip(shares, MONTH_DAYS))
    else:
        days = tuple(
            share * month_days for share, month_days in zip(site_heating_shares(ratios, inertia), MONTH_DAYS)
        )
        shares = tuple(heating_days / month_days for heating_days, month_days in zip(days, MONTH_DAYS))
    utilisation = tuple(None if ratio is None else utilisation_factor(ratio, inertia.utilisation_parameter)
                        for ratio in ratios)
    demand = tuple(
        0.0 if eta is None else (loss - eta * gain) * share
        for loss, gain, eta, share in zip(losses, gains, utilisation, shares)
    )
    result = Balance(transmission, ventilation, losses, tuple(gains), utilisation, shares, days, demand, sum(demand))
    _check(result)
    return result


def utilisation_factor(ratio: float, a: float) -> float:
    """η of B 8110-6-1:2024-03 Eq. (57a), (57b) for a positive gain-loss ratio γ."""
    if ratio == 1:
        return a / (1 + a)
    return float((1 - ratio**a) / (1 - ratio ** (a + 1)))


def heating_shares(ratios: Sequence[float], limit: float) -> Monthly:
    """The share f of every month from its γ and its neighbours' means, H 5050-1 Eq. (9c)–(9j), H 5056-1 7.1.

    Args:
        ratios: The positive γ of the twelve months, January to December.
        limit: γ_lim.

    Returns:
        The share of each month in the heating period.
    """
    shares = []
    for month, ratio in enumerate(ratios):
        start = (ratios[month - 1] + ratio) / 2
        end = (ratio + ratios[(month + 1) % len(ratios)]) / 2
        lower, upper = min(start, end), max(start, end)
        if upper < limit:
            shares.append(1.0)
        elif lower > limit:
            shares.append(0.0)
        elif ratio > limit:
            shares.append(0.5 * (limit - lower) / (ratio - lower))
        else:
            shares.append(0.5 + 0.5 * (limit - ratio) / (upper - ratio))
    return tuple(shares)


def site_heating_shares(ratios: Sequence[Optional[float]], inertia: Dynamics) -> Monthly:
    """f_H of every month by H 5056-1:2024-03 7.1; a month without positive losses has none (``None``).

    The γ of such a month is replaced by that of the next month with positive losses for its neighbours'
    means, and the month itself is outside the heating period. Without any such month there is none.
    """
    if all(ratio is None for ratio in ratios):
        return (0.0,) * len(ratios)
    count = len(ratios)
    replaced = [
        next(ratios[(month + step) % count] for step in range(count) if ratios[(month + step) % count] is not None)
        for month in range(count)
    ]
    shares = heating_shares([ratio for ratio in replaced if ratio is not None], inertia.gain_loss_ratio_limit)
    return tuple(0.0 if ratio is None else share for ratio, share in zip(ratios, shares))


def _check(result: Balance) -> None:
    """I-1 and I-2: losses are transmission plus ventilation, shares within 0 … 1, η within (0, 1], Q_h ≥ 0."""
    assert result.losses == tuple(q_t + q_v for q_t, q_v in zip(result.transmission_losses, result.ventilation_losses))
    assert all(0 <= share <= 1 for share in result.heating_share), result.heating_share
    assert all(eta is None or 0 < eta <= 1 for eta in result.utilisation), result.utilisation
    assert all(demand >= 0 for demand in result.heating_demand), result.heating_demand
