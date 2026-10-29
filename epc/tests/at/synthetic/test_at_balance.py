"""T-5: the monthly balance of the Austrian engine, each rule against a hand computation with chosen values.

DISCLAIMER - SYNTHETIC TEST DATA. Every table value in this module is invented to test the code,
and any building in it is a placeholder or a HiSim test record: no value comes from an OENORM
table, and no number computed here is the heating demand of any real building. Never use them for
a study or a real calculation. The standards themselves (OENORM B 8110-5, B 8110-6-1, B 8110-6-2,
H 5050-1, H 5056-1, edition 2024-03) can be purchased from Austrian Standards International:
https://www.austrian-standards.at

Conductances, temperatures, gains and ratios are placeholders; no value of a licensed table appears
here (R8.5).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import List, Optional, Tuple

import pytest

from epc.engines.at import balance, defaults
from epc.engines.at.climate import Climate
from epc.engines.at.envelope import Envelope
from epc.engines.at.usage import MONTH_DURATION_IN_H
from epc.model.building import Element, Situation
from epc.model.errors import EpcError
from epc.tests.at.synthetic.at_tables import PROSE_CONSTANTS
from epc.tests.at.synthetic.test_at_terms import _building

pytestmark = pytest.mark.base

#: The synthetic constants the expectations below are computed with.
C = PROSE_CONSTANTS


def _envelope(transmission: float, ventilation: float) -> Envelope:
    """An envelope with the two conductances that matter for the balance; the rest are placeholders."""
    return Envelope((), transmission, 100.0, 0.0, transmission, 1.0, 1.0, 100.0, 0.5, ventilation, ())


def _climate(temperatures: List[float]) -> Climate:
    """A climate of chosen monthly temperatures and no radiation."""
    return Climate(tuple(temperatures), MappingProxyType({}))


def test_the_dynamics_follow_eq_49_50_59() -> None:
    """Eq. (50), (49) and (59) with the constants of the tables; γ_lim = (a + 1) / a."""
    result = balance.dynamics(C, _envelope(150.0, 50.0), 20.0, 400.0)
    assert result.thermal_capacity_in_wh_per_k == 8000.0
    assert result.time_constant_in_h == 40.0
    a = C["utilisation_a0"] + 40.0 / C["utilisation_tau0"]
    assert result.utilisation_parameter == a
    assert result.gain_loss_ratio_limit == (a + 1) / a


@pytest.mark.parametrize("ratio, a, expected", [(1.0, 3.0, 3.0 / 4.0), (0.5, 2.0, (1 - 0.5**2.0) / (1 - 0.5**3.0))])
def test_the_utilisation_factor_follows_eq_57a_and_57b(ratio: float, a: float, expected: float) -> None:
    """η = a / (1 + a) at γ = 1, else (1 − γ^a) / (1 − γ^(a+1))."""
    assert balance.utilisation_factor(ratio, a) == expected


def test_the_heating_shares_follow_h_5050_1_eq_9g_to_9j() -> None:
    """Whole months in and out, and the two partial cases, with December next to January."""
    ratios = [0.5, 0.5, 1.0, 1.8, 2.6, 5.0, 5.0, 5.0, 3.5, 2.5, 1.5, 0.5]
    shares = balance.heating_shares(ratios, 2.0)
    assert shares[0] == 1.0  # (9g): December and February keep both means below the limit
    assert shares[6] == 0.0  # (9h): both means above the limit
    assert shares[3] == 0.5 + 0.5 * (2.0 - 1.8) / ((1.8 + 2.6) / 2 - 1.8)  # (9j): γ at or below the limit
    assert shares[9] == 0.5 * (2.0 - (2.5 + 1.5) / 2) / (2.5 - (2.5 + 1.5) / 2)  # (9i): γ above the limit
    assert shares[11] == 1.0


def test_december_and_january_are_neighbours() -> None:
    """The mean at the start of January takes December, the mean at the end of December takes January."""
    ratios = [1.8] + [0.5] * 10 + [3.0]
    shares = balance.heating_shares(ratios, 2.0)
    assert shares[0] == 0.5 + 0.5 * (2.0 - 1.8) / ((3.0 + 1.8) / 2 - 1.8)


def test_the_site_climate_replaces_a_month_without_positive_losses() -> None:
    """H 5056-1 7.1: its γ becomes that of the next month with positive losses; it is outside the period."""
    ratios: List[Optional[float]] = [0.5, 0.6, 0.8, 1.2, 1.9, 2.4, None, None, 2.8, 1.6, 0.9, 0.6]
    inertia = balance.Dynamics(1.0, 12.0, 2.0, 1.5)
    shares = balance.site_heating_shares(ratios, inertia)
    replaced = [0.5, 0.6, 0.8, 1.2, 1.9, 2.4, 2.8, 2.8, 2.8, 1.6, 0.9, 0.6]
    expected = balance.heating_shares(replaced, 1.5)
    assert shares[6] == shares[7] == 0.0
    assert shares[5] == expected[5] and shares[8] == expected[8]


def test_without_any_positive_losses_there_is_no_heating_period() -> None:
    """A site where no month needs heat has no heating share at all."""
    assert balance.site_heating_shares([None] * 12, balance.Dynamics(1.0, 12.0, 2.0, 1.5)) == (0.0,) * 12


def _gains() -> Tuple[float, ...]:
    """Chosen monthly gains in kWh."""
    return tuple(200.0 + 50.0 * month for month in range(12))


def test_the_reference_balance_follows_eq_28_to_30_and_52() -> None:
    """Q_T, Q_V by the temperature difference and the month's hours, Q_l = Q_T + Q_V, Q_h = (Q_l − η Q_g) f_h."""
    envelope = _envelope(150.0, 50.0)
    inertia = balance.dynamics(C, envelope, 20.0, 400.0)
    temperatures = [-2.0, 0.0, 4.0, 9.0, 14.0, 17.0, 19.0, 18.5, 15.0, 9.5, 4.0, 0.0]
    result = balance.balance(envelope, inertia, 20.5, _climate(temperatures), _gains(), reference=True)
    for month, (outdoor, hours) in enumerate(zip(temperatures, MONTH_DURATION_IN_H)):
        assert result.transmission_losses[month] == 150.0 * (20.5 - outdoor) * hours / 1000
        assert result.ventilation_losses[month] == 50.0 * (20.5 - outdoor) * hours / 1000
        loss = result.losses[month]
        assert loss == result.transmission_losses[month] + result.ventilation_losses[month]
        eta = balance.utilisation_factor(_gains()[month] / loss, inertia.utilisation_parameter)
        assert result.utilisation[month] == eta
        assert result.heating_demand[month] == (loss - eta * _gains()[month]) * result.heating_share[month]
    ratios = [gain / loss for gain, loss in zip(_gains(), result.losses)]
    assert result.heating_share == balance.heating_shares(ratios, inertia.gain_loss_ratio_limit)
    assert result.annual_heating_demand == sum(result.heating_demand)


def test_a_reference_month_without_positive_losses_is_refused() -> None:
    """E-14: H 5050-1 Eq. (9) has no rule for it (D-R1)."""
    envelope = _envelope(150.0, 50.0)
    with pytest.raises(EpcError) as refusal:
        balance.balance(envelope, balance.dynamics(C, envelope, 20.0, 400.0), 20.5, _climate([25.0] + [0.0] * 11),
                        _gains(), reference=True)
    assert refusal.value.error_id == "E-14"
    assert refusal.value.message.startswith("Month 1:")


def test_a_site_month_without_positive_losses_contributes_nothing() -> None:
    """Under the site climate such a month is outside the heating period, without a refusal."""
    envelope = _envelope(150.0, 50.0)
    temperatures = [0.0] * 6 + [23.0] + [0.0] * 5
    result = balance.balance(envelope, balance.dynamics(C, envelope, 20.0, 400.0), 20.5, _climate(temperatures),
                             _gains(), reference=False)
    assert result.utilisation[6] is None
    assert result.heating_share[6] == 0.0 and result.heating_demand[6] == 0.0
    assert result.heating_days == tuple(share * days for share, days in zip(result.heating_share, balance.MONTH_DAYS))


@pytest.mark.parametrize(
    "heat_capacity_class, name, key",
    [("very light", "leicht", "light"), ("light", "leicht", "light"), ("medium", "mittelschwer", "medium"),
     ("heavy", "schwer", "heavy"), ("very heavy", "schwer", "heavy")],
)
def test_hisims_five_classes_map_to_the_norms_three_constructions(heat_capacity_class: str, name: str,
                                                                  key: str) -> None:
    """D3: f_BW of 9.1.2 by construction, recorded as an assumption."""
    result = defaults.construction(C, _building(heat_capacity_class=heat_capacity_class))
    assert result.value == C[f"construction_factor_{key}"]
    assert result.assumption is not None and name in result.assumption.value


def test_an_unknown_heat_capacity_class_is_refused() -> None:
    """E-18 names the classes HiSim has."""
    with pytest.raises(EpcError) as refusal:
        defaults.construction(C, _building(heat_capacity_class="massive"))
    assert refusal.value.error_id == "E-18" and "very light" in refusal.value.message


def test_a_closed_garage_is_a_stated_floor_row() -> None:
    """A hand-written floor above a closed garage takes its Tab. 4 row without an assumption; a wall cannot."""
    floor = Element("floor", 10.0, 0.5, 0.5, Situation.UNCONDITIONED_GARAGE, "hand")
    assert defaults.correction_factor_row(floor) == ("CEILING_TO_CLOSED_GARAGE", None)
    wall = Element("wall", 10.0, 0.5, 0.5, Situation.UNCONDITIONED_GARAGE, "hand")
    with pytest.raises(ValueError, match="floor only"):
        defaults.correction_factor_row(wall)


def test_a_floor_on_the_ground_takes_the_shallow_row_as_an_assumption() -> None:
    """A hand-written floor on the ground takes the shallow Tab. 5 row, recorded; a roof cannot."""
    floor = Element("floor", 10.0, 0.5, 1.0, Situation.GROUND, "hand")
    row, assumption = defaults.correction_factor_row(floor)
    assert row == "FLOOR_ON_GROUND_SHALLOW"
    assert assumption is not None and "Tab. 5" in assumption.value and "no depth" in assumption.value
    with pytest.raises(ValueError, match="floor only"):
        defaults.correction_factor_row(Element("roof", 10.0, 0.5, 1.0, Situation.GROUND, "hand"))
