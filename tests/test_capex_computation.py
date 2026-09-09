"""Tests for the CAPEX proration rules in ``capex_computation``.

These tests pin the two different proration rules that ``prorate_to_simulated_period`` applies
when it cuts lifetime figures down to the simulated period, and that
``CapexComputationHelperFunctions.compute_capex_costs_and_emissions`` inherits by calling it:
the investment cost and the embodied CO2 footprint are one-time figures annualized over the
technical lifetime, while the maintenance cost is an annual rate that is never divided by that
lifetime. The proration function's own docstring is the authoritative statement of the rule.

Most cases feed the config branch of the helper (every capex field on the config is populated),
so the expected numbers depend only on the four inputs written into the config and on the
simulated duration. The last case deliberately takes the other branch, where the helper builds
the annual maintenance figure itself out of the tabulated device factors.
"""

# clean

import datetime
from dataclasses import dataclass
from typing import Optional

import pytest
from dataclasses_json import dataclass_json

from hisim.components.configuration import EmissionFactorsAndCostsForDevicesConfig
from hisim.config import ComponentID, ConfigBase
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.cost_and_emission_computation.capex_computation import (
    CapexComputationHelperFunctions,
    prorate_to_simulated_period,
)
from hisim.simulationparameters import SimulationParameters


@dataclass_json
@dataclass
class CostedDummyConfig(ConfigBase):
    """A minimal config carrying only the five capex fields the helper reads.

    Standing in for a real component config keeps the expected numbers independent of any
    component's default sizing and of the tabulated device factors: with all five fields set,
    the helper takes its config branch and uses these values verbatim.
    """

    @classmethod
    def get_main_classname(cls):
        """Return the name used in place of a real component class name."""
        return "tests.test_capex_computation.CostedDummy"

    component_id: ComponentID
    device_co2_footprint_in_kg: Optional[float]
    investment_costs_in_euro: Optional[float]
    lifetime_in_years: Optional[float]
    maintenance_costs_in_euro_per_year: Optional[float]
    subsidy_as_percentage_of_investment_costs: Optional[float]


def make_config(lifetime_in_years: float = 10.0) -> CostedDummyConfig:
    """Build the reference config: 1000 EUR investment, 200 kg CO2, 100 EUR/a maintenance.

    Args:
        lifetime_in_years: The technical lifetime to write into the config; varied by the test
            that checks maintenance does not depend on it.

    Returns:
        A config with every capex field populated, so the helper uses these values directly.
    """
    return CostedDummyConfig(
        component_id=ComponentID(name="CostedDummy"),
        device_co2_footprint_in_kg=200.0,
        investment_costs_in_euro=1000.0,
        lifetime_in_years=lifetime_in_years,
        maintenance_costs_in_euro_per_year=100.0,
        subsidy_as_percentage_of_investment_costs=0.0,
    )


def uncosted_config() -> CostedDummyConfig:
    """Build a config with every capex field left ``None``, which selects the device branch.

    The helper only reaches for the tabulated device factors when all five capex fields are
    absent, so a config that omits every one of them is what steers the test into that branch.

    Returns:
        A config carrying no capex values at all.
    """
    return CostedDummyConfig(
        component_id=ComponentID(name="UncostedDummy"),
        device_co2_footprint_in_kg=None,
        investment_costs_in_euro=None,
        lifetime_in_years=None,
        maintenance_costs_in_euro_per_year=None,
        subsidy_as_percentage_of_investment_costs=None,
    )


def make_simulation_parameters(days: float) -> SimulationParameters:
    """Build simulation parameters spanning ``days`` days from the start of 2021.

    The helper divides the simulated duration by a fixed 365-day year, so a span of 182.5 days
    is exactly half a year and a span of 365 days is exactly one year.

    Args:
        days: Length of the simulated period in days.

    Returns:
        Simulation parameters with an hourly timestep over that period.
    """
    start_date = datetime.datetime(2021, 1, 1)
    return SimulationParameters(
        start_date=start_date,
        end_date=start_date + datetime.timedelta(days=days),
        seconds_per_timestep=3600,
    )


@pytest.mark.base
def test_half_year_prorates_investment_over_lifetime_but_not_maintenance() -> None:
    """Half a year of a 1000 EUR / 10 a / 100 EUR-per-year device costs 50 EUR of each.

    Derivation, with a simulated fraction of a year of 182.5 / 365 = 0.5:

    * investment share: (1000 EUR / 10 a) * 0.5 a = 50 EUR
    * CO2 share:        (200 kg / 10 a) * 0.5 a   = 10 kg
    * maintenance:      100 EUR/a * 0.5 a         = 50 EUR

    The two 50 EUR figures coincide only because of the numbers chosen; the maintenance figure
    is the one that pins the rule, since dividing it by the lifetime as well would give 5 EUR.
    """
    capex_data = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=make_simulation_parameters(days=182.5),
        component_type=ComponentType.HEAT_PUMP,
        unit=Units.KILOWATT,
        size_of_energy_system=1.0,
        config=make_config(),
    )

    assert capex_data.capex_investment_cost_for_simulated_period_in_euro == 50.0
    assert capex_data.device_co2_footprint_for_simulated_period_in_kg == 10.0
    assert capex_data.maintenance_cost_per_simulated_period_in_euro == 50.0
    # The lifetime figures themselves pass through untouched.
    assert capex_data.capex_investment_cost_in_euro == 1000.0
    assert capex_data.maintenance_costs_in_euro_per_year == 100.0
    assert capex_data.lifetime_in_years == 10.0


@pytest.mark.base
def test_full_year_maintenance_equals_the_annual_rate() -> None:
    """A full simulated year charges the annual maintenance rate exactly once.

    With a simulated fraction of a year of 365 / 365 = 1.0, maintenance is 100 EUR/a * 1.0 a =
    100 EUR, while the investment share is (1000 EUR / 10 a) * 1.0 a = 100 EUR -- one tenth of
    the investment, as straight-line depreciation over ten years requires.
    """
    capex_data = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=make_simulation_parameters(days=365),
        component_type=ComponentType.HEAT_PUMP,
        unit=Units.KILOWATT,
        size_of_energy_system=1.0,
        config=make_config(),
    )

    assert capex_data.maintenance_cost_per_simulated_period_in_euro == 100.0
    assert capex_data.capex_investment_cost_for_simulated_period_in_euro == 100.0
    assert capex_data.device_co2_footprint_for_simulated_period_in_kg == 20.0


@pytest.mark.base
def test_maintenance_does_not_depend_on_the_technical_lifetime() -> None:
    """Doubling the lifetime halves the investment share and leaves maintenance alone.

    Over half a year with a 20-year lifetime instead of a 10-year one:

    * investment share: (1000 EUR / 20 a) * 0.5 a = 25 EUR, half of the 50 EUR above
    * maintenance:      100 EUR/a * 0.5 a         = 50 EUR, unchanged

    This is the discriminating case: a formula that divided maintenance by the lifetime would
    report 2.50 EUR here and 5 EUR at a 10-year lifetime, i.e. it would make maintenance
    lifetime-dependent.
    """
    capex_data = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=make_simulation_parameters(days=182.5),
        component_type=ComponentType.HEAT_PUMP,
        unit=Units.KILOWATT,
        size_of_energy_system=1.0,
        config=make_config(lifetime_in_years=20.0),
    )

    assert capex_data.capex_investment_cost_for_simulated_period_in_euro == 25.0
    assert capex_data.maintenance_cost_per_simulated_period_in_euro == 50.0


@pytest.mark.base
def test_prorate_to_simulated_period_applies_both_rules_over_half_a_year() -> None:
    """Pins the shared proration function directly, without a helper or a config around it.

    Over half a year the one-time figures lose both the lifetime and the half-year factor,
    while the annual maintenance rate loses only the half-year factor:

    * investment: (1000 EUR / 10 a) * 0.5 a = 50 EUR
    * CO2:        (200 kg / 10 a) * 0.5 a   = 10 kg
    * maintenance: 100 EUR/a * 0.5 a        = 50 EUR
    """
    prorated = prorate_to_simulated_period(
        investment_in_euro=1000.0,
        co2_footprint_in_kg=200.0,
        maintenance_in_euro_per_year=100.0,
        lifetime_in_years=10.0,
        simulation_parameters=make_simulation_parameters(days=182.5),
    )

    assert prorated.investment_for_simulated_period_in_euro == 50.0
    assert prorated.co2_footprint_for_simulated_period_in_kg == 10.0
    assert prorated.maintenance_for_simulated_period_in_euro == 50.0


@pytest.mark.base
def test_prorate_to_simulated_period_charges_a_full_year_once() -> None:
    """A full simulated year charges the annual maintenance rate exactly once.

    With a simulated fraction of a year of 365 / 365 = 1.0, the maintenance figure is the
    annual rate itself, while the investment share is one tenth of a 1000 EUR device with a
    ten-year lifetime.
    """
    prorated = prorate_to_simulated_period(
        investment_in_euro=1000.0,
        co2_footprint_in_kg=200.0,
        maintenance_in_euro_per_year=100.0,
        lifetime_in_years=10.0,
        simulation_parameters=make_simulation_parameters(days=365),
    )

    assert prorated.maintenance_for_simulated_period_in_euro == 100.0
    assert prorated.investment_for_simulated_period_in_euro == 100.0
    assert prorated.co2_footprint_for_simulated_period_in_kg == 20.0


@pytest.mark.base
def test_prorate_to_simulated_period_keeps_maintenance_free_of_the_lifetime() -> None:
    """Doubling the lifetime halves the investment share and leaves maintenance untouched.

    This is the discriminating case for the rule the function exists to hold: a formula that
    divided maintenance by the lifetime as well would report a different maintenance figure
    for each of the two lifetimes, and both would be a factor of the lifetime too small.
    """
    simulation_parameters = make_simulation_parameters(days=182.5)
    ten_years = prorate_to_simulated_period(
        investment_in_euro=1000.0,
        co2_footprint_in_kg=200.0,
        maintenance_in_euro_per_year=100.0,
        lifetime_in_years=10.0,
        simulation_parameters=simulation_parameters,
    )
    twenty_years = prorate_to_simulated_period(
        investment_in_euro=1000.0,
        co2_footprint_in_kg=200.0,
        maintenance_in_euro_per_year=100.0,
        lifetime_in_years=20.0,
        simulation_parameters=simulation_parameters,
    )

    assert twenty_years.investment_for_simulated_period_in_euro == ten_years.investment_for_simulated_period_in_euro / 2
    assert twenty_years.maintenance_for_simulated_period_in_euro == ten_years.maintenance_for_simulated_period_in_euro
    assert twenty_years.maintenance_for_simulated_period_in_euro == 50.0


@pytest.mark.base
def test_device_database_branch_prorates_maintenance_without_the_lifetime() -> None:
    """The tabulated-factor branch charges the annual maintenance share, not a lifetime slice.

    The three tests above feed the helper's config branch, so none of them would notice the
    device-database branch keeping the old rule: that branch builds the annual maintenance
    figure itself, out of the tabulated investment cost and the tabulated maintenance share.
    With every capex field on the config left ``None``, the helper looks the factors up for the
    simulation year and country and scales them by the size, and the maintenance for a
    half-year period must be

        investment_per_kw * size * maintenance_share_per_year * 0.5

    with no division by the tabulated technical lifetime anywhere in it. The expected numbers
    are read from the same table rather than written down, so a revised device row moves the
    test with it instead of breaking it.
    """
    simulation_parameters = make_simulation_parameters(days=182.5)
    size_in_kw = 8.0
    device_factors = EmissionFactorsAndCostsForDevicesConfig.get_values_for_year(
        year=simulation_parameters.year,
        device=ComponentType.HEAT_PUMP,
        country=simulation_parameters.country,
    )
    assert device_factors.investment_costs_in_euro_per_kw is not None
    investment_in_euro = device_factors.investment_costs_in_euro_per_kw * size_in_kw
    expected_maintenance_in_euro = (
        investment_in_euro * device_factors.maintenance_costs_as_percentage_of_investment_per_year * 0.5
    )

    capex_data = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=simulation_parameters,
        component_type=ComponentType.HEAT_PUMP,
        unit=Units.KILOWATT,
        size_of_energy_system=size_in_kw,
        config=uncosted_config(),
    )

    assert capex_data.maintenance_cost_per_simulated_period_in_euro == pytest.approx(
        expected_maintenance_in_euro
    )
    # The discriminating half of the assertion: the old rule divided by the lifetime as well,
    # which for a lifetime of more than one year is a strictly smaller figure.
    assert device_factors.technical_lifetime_in_years > 1
    assert capex_data.maintenance_cost_per_simulated_period_in_euro != pytest.approx(
        expected_maintenance_in_euro / device_factors.technical_lifetime_in_years
    )
    # The annual rate itself is reported untouched by the simulated duration.
    assert capex_data.maintenance_costs_in_euro_per_year == pytest.approx(
        investment_in_euro * device_factors.maintenance_costs_as_percentage_of_investment_per_year
    )
