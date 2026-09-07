"""Tests for the CAPEX proration rules in ``capex_computation``.

These tests pin the two different proration rules that
``CapexComputationHelperFunctions.compute_capex_costs_and_emissions`` applies when it cuts
lifetime figures down to the simulated period. The investment cost and
the embodied CO2 footprint are one-time figures, so they are annualized over the technical
lifetime and then scaled by the simulated fraction of a year. The maintenance cost is already
an annual rate that falls due in every year of the lifetime, so it is scaled by that fraction
alone and is never divided by the lifetime.

All cases feed the config branch of the helper (every capex field on the config is populated),
so the expected numbers depend only on the four inputs written into the config and on the
simulated duration -- never on the tabulated device database.
"""

# clean

import datetime
from dataclasses import dataclass
from typing import Optional

import pytest
from dataclasses_json import dataclass_json

from hisim.config import ComponentID, ConfigBase
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.cost_and_emission_computation.capex_computation import CapexComputationHelperFunctions
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
    assert capex_data.maintenance_costs_in_euro == 100.0
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
