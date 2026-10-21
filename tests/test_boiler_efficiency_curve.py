"""Tests of the boiler's sourced part-load efficiency curve (hisim-component-physics-4g9.66).

The curve per boiler type and vintage comes from IWU 2002 (Tab. 2, Tab. 3, eq. 2-1) for gas and oil boilers installed
before 2000, and from current product data otherwise; a stated Ecodesign seasonal efficiency scales it. The spec is
``roadmap/boiler_part_load_efficiency_spec.md``. GCV and NCV are the gross and the net calorific value.
"""

import math
from typing import Callable, Optional, Tuple

import pytest

from hisim import loadtypes as lt
from hisim.components.generic_boiler import (
    BoilerEfficiencySources,
    BoilerType,
    BoilerVintage,
    CalorificBasis,
    CombustionEfficiencyCurve,
)


class Fuel:
    """The fuels' GCV/NCV ratios as the tests write them out, from the sources the spec cites."""

    #: Natural gas, PhysicsConfig: 39.819 / 35.895 MJ/m3.
    GAS = 39.819 / 35.895
    #: Heating oil, PhysicsConfig: 37.682 / 35.358 GJ/m3.
    OIL = 37.682 / 35.358
    #: Wood pellets, TUeV SUeD report 2212100-1: 18.89 / 17.52 MJ/kg.
    PELLETS = 18.89 / 17.52
    #: Wood chips at 15 % water, the spec's stated estimate (EN ISO 18125 Annex E): 17 000 / 15 522 J/g.
    WOOD_CHIPS = 17000.0 / 15522.0


def iwu(efficiency_at_20_kw: float, maximal_power_in_kilowatt: float) -> float:
    """Return IWU 2002 eq. 2-1: a 20 kW efficiency moved to another size, ``eta - 0.0195 + 0.015 lg P_n``."""
    return efficiency_at_20_kw - 0.0195 + 0.015 * math.log10(maximal_power_in_kilowatt)


def curve_of(
    energy_carrier: lt.LoadTypes,
    boiler_type: BoilerType,
    installation_year: Optional[int],
    maximal_power_in_kilowatt: float = 20.0,
    seasonal_efficiency: Optional[float] = None,
) -> CombustionEfficiencyCurve:
    """Return the curve a boiler of these properties burns by."""
    return BoilerEfficiencySources.curve(
        energy_carrier=energy_carrier,
        boiler_type=boiler_type,
        installation_year=installation_year,
        maximal_power_in_watt=maximal_power_in_kilowatt * 1000.0,
        seasonal_efficiency=seasonal_efficiency,
    )


#: Every boiler of the five fuels, as (fuel, type, installation year, expected eta_100 and eta_30 on the boiler's
#: booking basis at 20 kW), the expected values written out from the cited source rows.
SOURCE_POINTS: Tuple[Tuple[lt.LoadTypes, BoilerType, Optional[int], float, float], ...] = (
    # Viessmann Vitodens 200-W B2HF-11 fiche, GCV
    (lt.LoadTypes.GAS, BoilerType.CONDENSING, None, 0.877, 0.978),
    (lt.LoadTypes.GAS, BoilerType.CONDENSING, 2000, 0.877, 0.978),
    # IWU 2002 Tab. 2 / Tab. 3, gas low-temperature/condensing, 1990s and 1980s (1970s: the 1980s row), NCV to GCV
    (lt.LoadTypes.GAS, BoilerType.CONDENSING, 1999, iwu(0.92, 20) / Fuel.GAS, iwu(1.01, 20) / Fuel.GAS),
    (lt.LoadTypes.GAS, BoilerType.CONDENSING, 1980, iwu(0.89, 20) / Fuel.GAS, iwu(0.97, 20) / Fuel.GAS),
    (lt.LoadTypes.GAS, BoilerType.CONDENSING, 1975, iwu(0.89, 20) / Fuel.GAS, iwu(0.97, 20) / Fuel.GAS),
    # Buderus Logano plus GB125-22 fiche, GCV
    (lt.LoadTypes.OIL, BoilerType.CONDENSING, None, 0.910, 0.975),
    # IWU 2002, oil low-temperature/condensing, NCV to GCV
    (lt.LoadTypes.OIL, BoilerType.CONDENSING, 1995, iwu(0.92, 20) / Fuel.OIL, iwu(0.97, 20) / Fuel.OIL),
    (lt.LoadTypes.OIL, BoilerType.CONDENSING, 1985, iwu(0.89, 20) / Fuel.OIL, iwu(0.94, 20) / Fuel.OIL),
    # Buderus Logano G125-21 planning document (08/2004), NCV
    (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, None, 0.937, 0.960),
    (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, None, 0.937, 0.960),
    # IWU 2002, low-temperature boilers 1990s and 1980s, standard boilers ("Spezialkessel") 1970s, NCV
    (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, 1995, iwu(0.92, 20), iwu(0.92, 20)),
    (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, 1985, iwu(0.89, 20), iwu(0.88, 20)),
    (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, 1979, iwu(0.84, 20), iwu(0.79, 20)),
    (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, 1970, iwu(0.85, 20), iwu(0.80, 20)),
    # Hydrogen: the gas row on the same basis (owner decision of 2026-10-20, a stated estimate)
    (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONDENSING, None, 0.877, 0.978),
    (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONDENSING, 1995, iwu(0.92, 20) / Fuel.GAS, iwu(1.01, 20) / Fuel.GAS),
    # Froeling PE1 Pellet 15 (GCV, times 18.89 / 17.52) and Hargassner Eco-HK 20 (NCV), whatever the year
    (lt.LoadTypes.PELLETS, BoilerType.CONVENTIONAL, None, 0.878 * Fuel.PELLETS, 0.845 * Fuel.PELLETS),
    (lt.LoadTypes.PELLETS, BoilerType.CONVENTIONAL, 1985, 0.878 * Fuel.PELLETS, 0.845 * Fuel.PELLETS),
    (lt.LoadTypes.WOOD_CHIPS, BoilerType.CONVENTIONAL, None, 0.939, 0.914),
    (lt.LoadTypes.WOOD_CHIPS, BoilerType.CONVENTIONAL, 1975, 0.939, 0.914),
)


@pytest.mark.base
@pytest.mark.parametrize(
    ("energy_carrier", "boiler_type", "installation_year", "full_load", "part_load"), SOURCE_POINTS
)
def test_the_curve_passes_through_its_sources_two_test_points(
    energy_carrier: lt.LoadTypes,
    boiler_type: BoilerType,
    installation_year: Optional[int],
    full_load: float,
    part_load: float,
) -> None:
    """A curve off its source would bill a fuel no datasheet or study supports.

    The full-load point is taken at the 60 °C test return, the 30 % point at the 30 °C one; a non-condensing boiler
    does not depend on its return.
    """
    curve = curve_of(energy_carrier, boiler_type, installation_year)

    assert curve.efficiency_at(1.0, 60.0) == pytest.approx(full_load, rel=1e-12)
    assert curve.efficiency_at(0.3, 30.0) == pytest.approx(part_load, rel=1e-12)


@pytest.mark.base
def test_a_condensing_boiler_gains_with_a_colder_return_and_a_conventional_one_does_not() -> None:
    """BRE S10TP-12 §4.5: 0.30 points per kelvin on the GCV for a condensing boiler; the gain stops at both returns."""
    condensing = curve_of(lt.LoadTypes.GAS, BoilerType.CONDENSING, None)
    conventional = curve_of(lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, None)

    assert condensing.efficiency_at(1.0, 40.0) - condensing.efficiency_at(1.0, 60.0) == pytest.approx(0.06)
    assert condensing.efficiency_at(1.0, 75.0) == condensing.efficiency_at(1.0, 60.0)
    assert condensing.efficiency_at(0.3, 20.0) == condensing.efficiency_at(0.3, 30.0)
    assert conventional.efficiency_at(0.5, 30.0) == conventional.efficiency_at(0.5, 60.0)


@pytest.mark.base
@pytest.mark.parametrize("installation_year", [None, 1995, 1985])
def test_a_condensing_oil_boiler_gains_0_20_points_per_kelvin_on_the_gross_value(
    installation_year: Optional[int],
) -> None:
    """BRE S10TP-12 Fig. 2, oil curve: 96.74 % at a 30 °C and 90.83 % at a 60 °C return (GCV), 0.0020 per K.

    Owner decision C of 2026-10-21: oil's flue gas carries less water vapour than natural gas's, so it gains less
    than gas's 0.0030. The current GB125 curve therefore reaches 0.970 at full load on a 30 °C return, not 1.000.
    """
    curve = curve_of(lt.LoadTypes.OIL, BoilerType.CONDENSING, installation_year)

    assert curve.efficiency_at(1.0, 30.0) - curve.efficiency_at(1.0, 60.0) == pytest.approx(30 * 0.0020, rel=1e-12)
    if installation_year is None:
        assert curve.efficiency_at(1.0, 30.0) == pytest.approx(0.970, rel=1e-12)


@pytest.mark.base
def test_below_30_percent_load_the_burner_keeps_its_30_percent_value() -> None:
    """The standards measure no lower point; cycling losses below it are not modelled (hisim-3y10)."""
    curve = curve_of(lt.LoadTypes.PELLETS, BoilerType.CONVENTIONAL, None)

    assert curve.efficiency_at(0.1, 40.0) == curve.efficiency_at(0.3, 40.0)
    assert curve.efficiency_at(0.0, 40.0) == curve.efficiency_at(0.3, 40.0)


@pytest.mark.base
def test_iwu_rows_follow_the_size_law_and_product_data_does_not() -> None:
    """IWU eq. 2-1 moves its 20 kW rows by 0.015 per decade of kW; a product's fiche is that product's."""
    for maximal_power_in_kilowatt in (10.0, 50.0):
        shift = 0.015 * math.log10(maximal_power_in_kilowatt / 20.0)
        old_small = curve_of(lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, 1995, maximal_power_in_kilowatt)
        old_reference = curve_of(lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, 1995, 20.0)
        assert old_small.efficiency_at_full_load - old_reference.efficiency_at_full_load == pytest.approx(shift)
        assert old_small.efficiency_at_part_load - old_reference.efficiency_at_part_load == pytest.approx(shift)
        current = curve_of(lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, None, maximal_power_in_kilowatt)
        assert current.efficiency_at_full_load == 0.937


@pytest.mark.base
@pytest.mark.parametrize(
    ("installation_year", "vintage"),
    [
        (1950, BoilerVintage.SEVENTIES),
        (1979, BoilerVintage.SEVENTIES),
        (1980, BoilerVintage.EIGHTIES),
        (1989, BoilerVintage.EIGHTIES),
        (1990, BoilerVintage.NINETIES),
        (1999, BoilerVintage.NINETIES),
        (2000, BoilerVintage.CURRENT),
        (2026, BoilerVintage.CURRENT),
        (None, BoilerVintage.CURRENT),
    ],
)
def test_the_installation_year_picks_the_decade_row(installation_year: Optional[int], vintage: BoilerVintage) -> None:
    """The rows of IWU 2002 are decades; no year or one from 2000 on is a current boiler (owner decision 2026-10-20)."""
    assert BoilerVintage.of_installation_year(installation_year) is vintage


@pytest.mark.base
@pytest.mark.parametrize(("energy_carrier", "boiler_type", "installation_year"), [row[:3] for row in SOURCE_POINTS])
@pytest.mark.parametrize("seasonal_efficiency", [0.75, 0.82])
def test_a_stated_seasonal_efficiency_is_reproduced_by_the_ecodesign_weighting_less_the_correction(
    energy_carrier: lt.LoadTypes,
    boiler_type: BoilerType,
    installation_year: Optional[int],
    seasonal_efficiency: float,
) -> None:
    """The label's eta_s (GCV) is ``0.85 eta_30 + 0.15 eta_100 - F(1)``, F(1) = 3 % (2014/C 207/02, 2015/1189).

    The scaled curve keeps its type's shape: both test points scale by the same factor. A boiler booked on the NCV is
    put on the GCV with its fuel's ratio before the comparison. 0.82 lies below every row's ceiling (the lowest, the
    1970s conventional gas row at 20 kW, is 0.826); a row at its own ceiling is
    :func:`test_at_its_ceiling_the_curve_reaches_1_on_its_booking_basis_and_just_above_it_is_refused`.
    """
    unscaled = curve_of(energy_carrier, boiler_type, installation_year)
    scaled = curve_of(energy_carrier, boiler_type, installation_year, seasonal_efficiency=seasonal_efficiency)
    booking = CalorificBasis.of_booking(
        lt.LoadTypes.GAS if energy_carrier == lt.LoadTypes.GREEN_HYDROGEN else energy_carrier, boiler_type
    )
    gross_over_net = 1.0 if booking == CalorificBasis.GROSS else BoilerEfficiencySources.gross_over_net(energy_carrier)

    active_mode_on_gross_basis = (
        0.85 * scaled.efficiency_at(0.3, 30.0) + 0.15 * scaled.efficiency_at(1.0, 60.0)
    ) / gross_over_net
    assert active_mode_on_gross_basis - 0.03 == pytest.approx(seasonal_efficiency, rel=1e-12)
    assert scaled.efficiency_at_part_load / scaled.efficiency_at_full_load == pytest.approx(
        unscaled.efficiency_at_part_load / unscaled.efficiency_at_full_load, rel=1e-12
    )


@pytest.mark.base
def test_a_seasonal_efficiency_that_lifts_the_curve_above_1_on_the_gross_value_is_refused() -> None:
    """eta_s = 0.97 makes the active-mode efficiency 1, so the condensing curve's 30 % point lies above 1 (GCV)."""
    with pytest.raises(ValueError, match="above 1"):
        curve_of(lt.LoadTypes.GAS, BoilerType.CONDENSING, None, seasonal_efficiency=0.97)


#: The ceilings of :meth:`BoilerEfficiencySources.highest_seasonal_efficiency` of the current rows, as (fuel, type,
#: ceiling), written out from the source rows: ``eta_son / (H k) - 0.03`` with ``eta_son = 0.85 eta_30 + 0.15
#: eta_100``, ``H`` the curve's highest point and ``k`` the fuel's GCV/NCV for a boiler booked on the NCV.
CURRENT_CEILINGS: Tuple[Tuple[lt.LoadTypes, BoilerType, float], ...] = (
    # Vitodens, GCV: the 30 % point at the 30 °C return is the highest
    (lt.LoadTypes.GAS, BoilerType.CONDENSING, (0.85 * 0.978 + 0.15 * 0.877) / 0.978 - 0.03),
    # Logano plus GB125, GCV, oil's gain 0.0020 (BRE S10TP-12 Fig. 2): full load at the 30 °C return is
    # 0.910 + 0.0020 * 30 = 0.970, so the 30 % point, 0.975, is the highest; 0.9600
    (lt.LoadTypes.OIL, BoilerType.CONDENSING, (0.85 * 0.975 + 0.15 * 0.910) / 0.975 - 0.03),
    # Logano G125, NCV: 0.8682 for gas, 0.9050 for oil
    (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, (0.85 * 0.960 + 0.15 * 0.937) / (0.960 * Fuel.GAS) - 0.03),
    (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, (0.85 * 0.960 + 0.15 * 0.937) / (0.960 * Fuel.OIL) - 0.03),
    # Froeling PE1 (GCV, booked on the NCV): the full-load point is the highest; 0.8678
    (lt.LoadTypes.PELLETS, BoilerType.CONVENTIONAL, (0.85 * 0.845 + 0.15 * 0.878) / (0.878 * Fuel.PELLETS) - 0.03),
    # Hargassner Eco-HK 20, NCV: 0.8624
    (
        lt.LoadTypes.WOOD_CHIPS,
        BoilerType.CONVENTIONAL,
        (0.85 * 0.914 + 0.15 * 0.939) / (0.939 * Fuel.WOOD_CHIPS) - 0.03,
    ),
)


@pytest.mark.base
@pytest.mark.parametrize(("energy_carrier", "boiler_type", "ceiling"), CURRENT_CEILINGS)
def test_the_highest_seasonal_efficiency_of_a_current_boiler_is_its_rows_closed_form(
    energy_carrier: lt.LoadTypes, boiler_type: BoilerType, ceiling: float
) -> None:
    """Owner decision of 2026-10-21: a non-condensing boiler's ceiling keeps its curve at or below 1 on the NCV.

    A product row does not follow IWU's size law, so its ceiling is the same at every size.
    """
    for maximal_power_in_kilowatt in (1.0, 12.0, 400.0):
        highest = BoilerEfficiencySources.highest_seasonal_efficiency(
            energy_carrier=energy_carrier,
            boiler_type=boiler_type,
            installation_year=None,
            maximal_power_in_watt=maximal_power_in_kilowatt * 1000.0,
        )
        assert highest == pytest.approx(ceiling, rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize(("energy_carrier", "boiler_type", "installation_year"), [row[:3] for row in SOURCE_POINTS])
@pytest.mark.parametrize("maximal_power_in_kilowatt", [1.0, 20.0])
def test_at_its_ceiling_the_curve_reaches_1_on_its_booking_basis_and_just_above_it_is_refused(
    energy_carrier: lt.LoadTypes,
    boiler_type: BoilerType,
    installation_year: Optional[int],
    maximal_power_in_kilowatt: float,
) -> None:
    """The build refuses exactly what lies above the ceiling the RenoVisor request check names.

    At the ceiling the scaled curve's highest point is 1 on the basis the boiler's fuel is booked on: the GCV for a
    condensing gas, oil or hydrogen boiler, the NCV for every other one, where the combustion loss would otherwise
    turn negative.
    """
    ceiling = BoilerEfficiencySources.highest_seasonal_efficiency(
        energy_carrier=energy_carrier,
        boiler_type=boiler_type,
        installation_year=installation_year,
        maximal_power_in_watt=maximal_power_in_kilowatt * 1000.0,
    )
    at_ceiling = curve_of(energy_carrier, boiler_type, installation_year, maximal_power_in_kilowatt, ceiling)
    just_below = curve_of(energy_carrier, boiler_type, installation_year, maximal_power_in_kilowatt, ceiling - 1e-9)

    assert at_ceiling.highest_efficiency() == pytest.approx(1.0, rel=1e-12)
    assert just_below.highest_efficiency() < 1.0
    basis = "net" if BoilerEfficiencySources.gross_to_booking_factor(energy_carrier, boiler_type) > 1.0 else "gross"
    with pytest.raises(ValueError, match=f"above {ceiling:.4f}.*above 1 on the {basis} calorific value"):
        curve_of(energy_carrier, boiler_type, installation_year, maximal_power_in_kilowatt, ceiling + 1e-9)


@pytest.mark.base
def test_the_ceiling_of_a_row_that_follows_the_size_law_rises_with_the_power() -> None:
    """IWU eq. 2-1 adds the same correction to both points, so a smaller boiler's ceiling is lower.

    The RenoVisor request check therefore checks at 1 kW: a figure that passes there passes at every larger size.
    """
    ceilings = [
        BoilerEfficiencySources.highest_seasonal_efficiency(
            energy_carrier=lt.LoadTypes.GAS,
            boiler_type=BoilerType.CONVENTIONAL,
            installation_year=1975,
            maximal_power_in_watt=maximal_power_in_kilowatt * 1000.0,
        )
        for maximal_power_in_kilowatt in (1.0, 4.0, 20.0, 400.0)
    ]
    assert ceilings == sorted(ceilings) and ceilings[0] < ceilings[-1]
    full_load, part_load = iwu(0.85, 1.0), iwu(0.80, 1.0)  # IWU 1970s "Gas-Spezialkessel" at 1 kW, NCV
    assert ceilings[0] == pytest.approx((0.85 * part_load + 0.15 * full_load) / (full_load * Fuel.GAS) - 0.03)


@pytest.mark.base
@pytest.mark.parametrize("energy_carrier", [lt.LoadTypes.DISTRICTHEATING, lt.LoadTypes.ELECTRICITY])
def test_a_fuel_without_a_source_is_refused(energy_carrier: lt.LoadTypes) -> None:
    """A boiler of a fuel no source covers must not borrow another fuel's curve silently."""
    with pytest.raises(ValueError):
        curve_of(energy_carrier, BoilerType.CONDENSING, None)


class Directive9242:
    """Council Directive 92/42/EEC, Article 5(1): the useful efficiency a boiler of 4 to 400 kW must reach.

    On the NCV, ``P_n`` in kW, in percent; at full load and at 30 % load (owner decision of 2026-10-20 for the test,
    https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:31992L0042).
    """

    #: The tolerance the floor test allows, dimensionless: IWU 2002 prints its efficiencies to two decimals, so 0.92
    #: stands for 0.915 to 0.925, and Art. 5(2) itself asks for "appropriate tolerances" (owner decision of
    #: 2026-10-20).
    TOLERANCE = 0.005

    @staticmethod
    def standard(maximal_power_in_kilowatt: float) -> Tuple[float, float]:
        """Return a standard boiler's floor at full and at 30 % load: 84 + 2 log P_n and 80 + 3 log P_n."""
        lg = math.log10(maximal_power_in_kilowatt)
        return (84.0 + 2.0 * lg) / 100.0, (80.0 + 3.0 * lg) / 100.0

    @staticmethod
    def low_temperature(maximal_power_in_kilowatt: float) -> Tuple[float, float]:
        """Return a low-temperature boiler's floor, also an oil condensing boiler's: 87.5 + 1.5 log P_n at both."""
        floor = (87.5 + 1.5 * math.log10(maximal_power_in_kilowatt)) / 100.0
        return floor, floor

    @staticmethod
    def gas_condensing(maximal_power_in_kilowatt: float) -> Tuple[float, float]:
        """Return a gas condensing boiler's floor: 91 + log P_n at full and 97 + log P_n at 30 % load."""
        lg = math.log10(maximal_power_in_kilowatt)
        return (91.0 + lg) / 100.0, (97.0 + lg) / 100.0


@pytest.mark.base
@pytest.mark.parametrize("maximal_power_in_kilowatt", [10.0, 20.0, 50.0, 100.0, 400.0])
@pytest.mark.parametrize(
    ("energy_carrier", "boiler_type", "installation_year", "floor", "tolerance"),
    [
        # the 1990s rows, held to the directive within the tolerance of IWU's two decimals
        (lt.LoadTypes.GAS, BoilerType.CONDENSING, 1995, Directive9242.gas_condensing, Directive9242.TOLERANCE),
        (lt.LoadTypes.OIL, BoilerType.CONDENSING, 1995, Directive9242.low_temperature, Directive9242.TOLERANCE),
        (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, 1995, Directive9242.low_temperature, Directive9242.TOLERANCE),
        (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, 1995, Directive9242.low_temperature, Directive9242.TOLERANCE),
        # current product data, held to the directive exactly
        (lt.LoadTypes.GAS, BoilerType.CONDENSING, None, Directive9242.gas_condensing, 0.0),
        (lt.LoadTypes.OIL, BoilerType.CONDENSING, None, Directive9242.low_temperature, 0.0),
        (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, None, Directive9242.low_temperature, 0.0),
        (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, None, Directive9242.low_temperature, 0.0),
        (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONDENSING, None, Directive9242.gas_condensing, 0.0),
    ],
)
def test_every_curve_under_the_directive_meets_its_legal_floor(
    energy_carrier: lt.LoadTypes,
    boiler_type: BoilerType,
    installation_year: Optional[int],
    floor: Callable[[float], Tuple[float, float]],
    tolerance: float,
    maximal_power_in_kilowatt: float,
) -> None:
    """A curve below 92/42/EEC Art. 5 describes no boiler that could be sold since the 1990s.

    The full-load point is checked at the directive's 70 °C mean (the 60 °C return), the 30 % point at the 30 °C
    return, both on the NCV. Older rows (1970s, 1980s) predate the directive and are not checked; pellet and wood-chip
    boilers are outside it (Art. 3). Hydrogen is checked as the gas boiler it is estimated as.
    """
    curve = curve_of(energy_carrier, boiler_type, installation_year, maximal_power_in_kilowatt)
    source_carrier = lt.LoadTypes.GAS if energy_carrier == lt.LoadTypes.GREEN_HYDROGEN else energy_carrier
    is_booked_gross = CalorificBasis.of_booking(source_carrier, boiler_type) == CalorificBasis.GROSS
    to_net_basis = BoilerEfficiencySources.gross_over_net(source_carrier) if is_booked_gross else 1.0
    full_load_floor, part_load_floor = floor(maximal_power_in_kilowatt)

    assert curve.efficiency_at(1.0, 60.0) * to_net_basis >= full_load_floor - tolerance
    assert curve.efficiency_at(0.3, 30.0) * to_net_basis >= part_load_floor - tolerance
