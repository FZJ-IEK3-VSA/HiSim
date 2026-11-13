"""Every boiler reports its fuel on the fuel's own calorific basis (bead hisim-boiler-fuel-one-basis-b7e2).

A condensing boiler's curve is stated on the gross calorific value (GCV), a conventional one's on the net (NCV). Until
this bead the boiler reported its fuel on its curve's basis, so a condensing gas boiler that burnt 7.8 % less gas
reported 2.1 % more energy, CO2 and fuel cost than a conventional one (renovisorissues #104). Now every fuel has one
basis, the one its price and emission factor are stated on, and every boiler converts its fuel to it.
"""

from typing import Callable

import pytest

from hisim import loadtypes as lt
from hisim.components import generic_boiler
from hisim.components.configuration import PhysicsConfig
from hisim.components.generic_boiler import (
    BoilerEfficiencySources,
    BoilerType,
    BurnerModulation,
    CombustionEfficiencyCurve,
    GenericBoilerConfig,
)
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class GrossOverNet:
    """The GCV/NCV ratios of ``PhysicsConfig``, written out: natural gas, heating oil and hydrogen."""

    GAS = 39.819 / 35.895
    OIL = 37.682 / 35.358
    HYDROGEN = 12.745 / 10.783


@pytest.mark.parametrize(
    ("fuel", "basis"),
    [
        (lt.LoadTypes.GAS, lt.CalorificBasis.GROSS),
        (lt.LoadTypes.GREEN_HYDROGEN, lt.CalorificBasis.GROSS),
        (lt.LoadTypes.OIL, lt.CalorificBasis.NET),
        (lt.LoadTypes.PELLETS, lt.CalorificBasis.NET),
        (lt.LoadTypes.WOOD_CHIPS, lt.CalorificBasis.NET),
        (lt.LoadTypes.DIESEL, lt.CalorificBasis.NET),
    ],
)
def test_each_fuel_is_reported_on_the_basis_its_price_is_stated_on(fuel: lt.LoadTypes, basis: lt.CalorificBasis) -> None:
    """Owner decision of 2026-11-13: GCV for gas and hydrogen, the cost database's NCV for the others."""
    assert lt.CalorificBasis.reported_for(fuel) is basis


@pytest.mark.parametrize("carrier", [lt.LoadTypes.ELECTRICITY, lt.LoadTypes.DISTRICTHEATING, lt.LoadTypes.WATER])
def test_a_carrier_that_burns_nothing_has_no_calorific_basis(carrier: lt.LoadTypes) -> None:
    """No basis is assumed for what is no fuel."""
    with pytest.raises(ValueError, match="is no fuel and has no calorific basis"):
        lt.CalorificBasis.reported_for(carrier)


@pytest.mark.parametrize(
    ("fuel", "boiler_type", "basis"),
    [
        (lt.LoadTypes.GAS, BoilerType.CONDENSING, lt.CalorificBasis.GROSS),
        (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, lt.CalorificBasis.NET),
        (lt.LoadTypes.OIL, BoilerType.CONDENSING, lt.CalorificBasis.GROSS),
        (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, lt.CalorificBasis.NET),
        (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONDENSING, lt.CalorificBasis.GROSS),
        (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONVENTIONAL, lt.CalorificBasis.NET),
        (lt.LoadTypes.PELLETS, BoilerType.CONDENSING, lt.CalorificBasis.NET),
        (lt.LoadTypes.PELLETS, BoilerType.CONVENTIONAL, lt.CalorificBasis.NET),
        (lt.LoadTypes.WOOD_CHIPS, BoilerType.CONVENTIONAL, lt.CalorificBasis.NET),
    ],
)
def test_the_curve_basis_follows_whether_the_boiler_condenses(
    fuel: lt.LoadTypes, boiler_type: BoilerType, basis: lt.CalorificBasis
) -> None:
    """A condensing boiler's heat is bounded by the GCV, a conventional one's by the NCV; solid fuels have only an NCV."""
    assert BoilerEfficiencySources.curve_basis(fuel, boiler_type) is basis


@pytest.mark.parametrize(
    ("fuel", "boiler_type", "reported_over_curve_basis"),
    [
        (lt.LoadTypes.GAS, BoilerType.CONDENSING, 1.0),
        (lt.LoadTypes.GAS, BoilerType.CONVENTIONAL, GrossOverNet.GAS),
        (lt.LoadTypes.OIL, BoilerType.CONDENSING, 1.0 / GrossOverNet.OIL),
        (lt.LoadTypes.OIL, BoilerType.CONVENTIONAL, 1.0),
        (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONDENSING, 1.0),
        (lt.LoadTypes.GREEN_HYDROGEN, BoilerType.CONVENTIONAL, GrossOverNet.HYDROGEN),
        (lt.LoadTypes.PELLETS, BoilerType.CONDENSING, 1.0),
        (lt.LoadTypes.PELLETS, BoilerType.CONVENTIONAL, 1.0),
        (lt.LoadTypes.WOOD_CHIPS, BoilerType.CONVENTIONAL, 1.0),
    ],
)
def test_the_factor_from_the_curve_basis_to_the_reported_basis_is_the_fuels_own_ratio(
    fuel: lt.LoadTypes, boiler_type: BoilerType, reported_over_curve_basis: float
) -> None:
    """A conventional gas boiler's NCV fuel is 1.109 times as many kWh on the GCV; a condensing oil boiler's 0.938."""
    assert BoilerEfficiencySources.reported_over_curve_basis(fuel, boiler_type) == pytest.approx(
        reported_over_curve_basis, rel=1e-12
    )


def test_the_burner_on_the_reported_basis_makes_the_same_heat_from_k_times_the_fuel() -> None:
    """The docstring's example: a 20 kW current conventional gas boiler becomes a 22.19 kW burner on the GCV.

    The heat at every control signal and return stays what it is on the curve basis; only the fuel's kWh change.
    """
    curve_on_net_basis = CombustionEfficiencyCurve(efficiency_at_full_load=0.937, efficiency_at_part_load=0.960)
    on_net_basis = BurnerModulation(
        minimal_power_in_watt=2000.0, maximal_power_in_watt=20000.0, efficiency_curve=curve_on_net_basis
    )

    burner = BurnerModulation.on_reported_basis(
        minimal_power_in_watt=2000.0,
        maximal_power_in_watt=20000.0,
        curve_on_curve_basis=curve_on_net_basis,
        reported_over_curve_basis=GrossOverNet.GAS,
    )

    assert burner.maximal_power_in_watt == pytest.approx(22186.4, rel=1e-5)
    assert burner.minimal_power_in_watt == pytest.approx(2000.0 * GrossOverNet.GAS, rel=1e-12)
    assert burner.efficiency_curve.efficiency_at_full_load == pytest.approx(0.8447, abs=1e-4)
    for control_signal in (0.0, 0.3, 0.5, 1.0):
        for return_temperature_in_celsius in (30.0, 45.0, 60.0):
            power_in_watt = burner.commanded_power_in_watt(control_signal)
            power_on_net_basis_in_watt = on_net_basis.commanded_power_in_watt(control_signal)
            assert power_in_watt == pytest.approx(power_on_net_basis_in_watt * GrossOverNet.GAS, rel=1e-12)
            assert burner.heat_power_in_watt(power_in_watt, return_temperature_in_celsius) == pytest.approx(
                on_net_basis.heat_power_in_watt(power_on_net_basis_in_watt, return_temperature_in_celsius), rel=1e-12
            )
    assert burner.heat_power_in_watt(burner.maximal_power_in_watt, 60.0) == pytest.approx(18740.0, rel=1e-12)


@pytest.mark.parametrize(
    ("fuel", "heating_value_in_joule_per_m3"),
    [
        (lt.LoadTypes.GAS, 39.819e6),
        (lt.LoadTypes.GREEN_HYDROGEN, 12.745e6),
        (lt.LoadTypes.OIL, 35.358e9),
        (lt.LoadTypes.PELLETS, PhysicsConfig.PELLETS_LOWER_HEATING_VALUE_IN_JOULE_PER_M3),
        (lt.LoadTypes.WOOD_CHIPS, PhysicsConfig.WOOD_CHIPS_LOWER_HEATING_VALUE_IN_JOULE_PER_M3),
    ],
)
def test_the_heating_value_is_the_one_on_the_reported_basis(fuel: lt.LoadTypes, heating_value_in_joule_per_m3: float) -> None:
    """One heating value per fuel turns any boiler's reported kWh back into the volume it burnt."""
    assert GenericBoilerConfig.heating_value_in_joule_per_m3(fuel) == pytest.approx(heating_value_in_joule_per_m3, rel=1e-12)


def test_district_heating_has_no_heating_value() -> None:
    """District heating burns nothing, so it has no basis and no heating value to divide by."""
    with pytest.raises(ValueError):
        GenericBoilerConfig.heating_value_in_joule_per_m3(lt.LoadTypes.DISTRICTHEATING)


class SameHouse:
    """Two boilers of one fuel, both firing the same heat into the same return: what each burns and reports."""

    MAXIMAL_POWER_IN_WATT = 12000.0
    MINIMAL_POWER_IN_WATT = 1000.0
    CONTROL_SIGNAL = 0.5
    RETURN_TEMPERATURE_IN_CELSIUS = 40.0
    PARAMETERS = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)

    @classmethod
    def boiler(
        cls, preset: Callable[[str], GenericBoilerConfig], boiler_type: BoilerType
    ) -> generic_boiler.GenericBoiler:
        """Build a 1-12 kW boiler of the preset's fuel and the given type."""
        config = preset("Boiler")
        config.boiler_type = boiler_type
        config.maximal_thermal_power_in_watt = cls.MAXIMAL_POWER_IN_WATT
        config.minimal_thermal_power_in_watt = cls.MINIMAL_POWER_IN_WATT
        return generic_boiler.GenericBoiler(cls.PARAMETERS, config)

    @classmethod
    def reported_fuel_in_watt_per_watt_of_heat(cls, boiler: generic_boiler.GenericBoiler) -> float:
        """The fuel power the boiler reports per watt of heat at the half-load signal and the 40 °C return."""
        power_in_watt = boiler.burner.commanded_power_in_watt(cls.CONTROL_SIGNAL)
        return power_in_watt / boiler.burner.heat_power_in_watt(power_in_watt, cls.RETURN_TEMPERATURE_IN_CELSIUS)

    @classmethod
    def burnt_volume_in_m3_per_joule_of_heat(cls, boiler: generic_boiler.GenericBoiler) -> float:
        """The fuel volume the boiler burns per joule of heat, from its curve basis: the physics, not the report."""
        config_power_in_watt = cls.CONTROL_SIGNAL * cls.MAXIMAL_POWER_IN_WATT
        heat_in_watt = config_power_in_watt * boiler.efficiency_curve.efficiency_at(
            cls.CONTROL_SIGNAL, cls.RETURN_TEMPERATURE_IN_CELSIUS
        )
        properties = PhysicsConfig.get_properties_for_energy_carrier(boiler.energy_carrier)
        is_gross = BoilerEfficiencySources.curve_basis(boiler.energy_carrier, boiler.config.boiler_type) == lt.CalorificBasis.GROSS
        heating_value_in_joule_per_m3 = (
            properties.higher_heating_value_in_joule_per_m3 if is_gross else properties.lower_heating_value_in_joule_per_m3
        )
        return config_power_in_watt / heating_value_in_joule_per_m3 / heat_in_watt


@pytest.mark.parametrize(
    "preset",
    [
        GenericBoilerConfig.preset_condensing_gas,
        GenericBoilerConfig.preset_oil,
        GenericBoilerConfig.preset_hydrogen,
        GenericBoilerConfig.preset_pellets,
        GenericBoilerConfig.preset_wood_chips,
    ],
)
def test_both_boiler_types_report_fuel_energy_in_proportion_to_the_fuel_they_burn(
    preset: Callable[[str], GenericBoilerConfig],
) -> None:
    """The bead's pin, per fuel: reported kWh over burnt volume is the same heating value for both boiler types.

    So the energy, the CO2 (one factor per kWh) and the fuel cost (one price per kWh) of the two types stand in the
    ratio of the fuel they burn, and a condensing gas or oil boiler, which burns less, reports less.
    """
    reported_in_kwh_per_m3 = {}
    burnt_in_m3_per_joule = {}
    for boiler_type in BoilerType:
        boiler = SameHouse.boiler(preset, boiler_type)
        reported = SameHouse.reported_fuel_in_watt_per_watt_of_heat(boiler)
        burnt = SameHouse.burnt_volume_in_m3_per_joule_of_heat(boiler)
        reported_in_kwh_per_m3[boiler_type] = reported / burnt / 3.6e6
        burnt_in_m3_per_joule[boiler_type] = burnt
        assert reported_in_kwh_per_m3[boiler_type] == pytest.approx(
            boiler.heating_value_of_fuel_in_kwh_per_liter * 1000.0, rel=1e-12
        )
    assert reported_in_kwh_per_m3[BoilerType.CONDENSING] == pytest.approx(
        reported_in_kwh_per_m3[BoilerType.CONVENTIONAL], rel=1e-12
    )
    fuel = SameHouse.boiler(preset, BoilerType.CONDENSING).energy_carrier
    if fuel in (lt.LoadTypes.GAS, lt.LoadTypes.OIL, lt.LoadTypes.GREEN_HYDROGEN):
        assert burnt_in_m3_per_joule[BoilerType.CONDENSING] < burnt_in_m3_per_joule[BoilerType.CONVENTIONAL]
