"""Tests for the per-field capex default fallback.

A component config may leave any subset of its capex fields unset. Every field it does
specify must survive untouched, every field it leaves as ``None`` must be filled from
``EmissionFactorsAndCostsForDevicesConfig``, and each fallback must show up in
``CapexDefaultsRegistry`` so the post processor can report them in one block at the end.
"""

# clean

from typing import Generator

import pytest

from hisim.components.generic_pv_system import PVSystemConfig
from hisim.loadtypes import ComponentType, Units
from hisim.postprocessing.cost_and_emission_computation.capex_computation import (
    CapexComputationHelperFunctions,
    CapexDefaultsRegistry,
)
from hisim.simulationparameters import SimulationParameters
from hisim.units import Euro, Quantity

# Device database values for PV in DE (the only year present is used for any requested year).
INVESTMENT_COSTS_IN_EURO_PER_KW = 794.41
CO2_FOOTPRINT_IN_KG_PER_KW = 330.51
MAINTENANCE_SHARE_PER_YEAR = 0.01
TECHNICAL_LIFETIME_IN_YEARS = 25
POWER_IN_WATT = 25000.0
SIZE_IN_KW = POWER_IN_WATT * 1e-3


@pytest.fixture(autouse=True)
def clear_registry() -> Generator[None, None, None]:
    """Keep recorded fallbacks from leaking between tests."""
    CapexDefaultsRegistry.reset()
    yield
    CapexDefaultsRegistry.reset()


def make_pv_config(**capex_values) -> PVSystemConfig:
    """Build a PV config whose capex fields are all unset except the ones passed in."""
    config = PVSystemConfig.get_default_pv_system(power_in_watt=POWER_IN_WATT)
    config.name = "PVSystem"
    for field_name, value in capex_values.items():
        setattr(config, field_name, value)
    return config


def compute_for(config: PVSystemConfig, unit: Units = Units.KILOWATT):
    """Run the capex computation for a PV config with a one-year simulation."""
    return CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=SimulationParameters.full_year(year=2021, seconds_per_timestep=60),
        component_type=ComponentType.PV,
        unit=unit,
        size_of_energy_system=SIZE_IN_KW,
        config=config,
    )


@pytest.mark.base
def test_mixed_config_fills_only_the_unset_fields() -> None:
    """A config that sets four of five fields keeps them and gets only the fifth defaulted."""
    config = make_pv_config(
        investment_costs_in_euro=20000.0,
        device_co2_footprint_in_kg=None,
        lifetime_in_years=25.0,
        maintenance_costs_in_euro_per_year=100.0,
        subsidy_as_percentage_of_investment_costs=0.4,
    )

    capex = compute_for(config)

    assert capex.capex_investment_cost_in_euro == 20000.0
    assert capex.lifetime_in_years == 25.0
    assert capex.maintenance_costs_in_euro == 100.0
    assert capex.subsidy_as_percentage_of_investment_costs == 0.4
    # The one unset field comes from the database, scaled by the system size.
    assert capex.device_co2_footprint_in_kg == pytest.approx(CO2_FOOTPRINT_IN_KG_PER_KW * SIZE_IN_KW, abs=0.01)
    assert CapexDefaultsRegistry.get_defaulted_fields_per_component() == {
        "PVSystem": ["device_co2_footprint_in_kg"]
    }


@pytest.mark.base
def test_all_fields_unset_uses_database_for_everything() -> None:
    """A config with no capex values at all still gets a complete set of defaults."""
    config = make_pv_config()

    capex = compute_for(config)

    expected_investment = INVESTMENT_COSTS_IN_EURO_PER_KW * SIZE_IN_KW
    assert capex.capex_investment_cost_in_euro == pytest.approx(expected_investment, abs=0.01)
    assert capex.device_co2_footprint_in_kg == pytest.approx(CO2_FOOTPRINT_IN_KG_PER_KW * SIZE_IN_KW, abs=0.01)
    assert capex.lifetime_in_years == TECHNICAL_LIFETIME_IN_YEARS
    assert capex.maintenance_costs_in_euro == pytest.approx(
        expected_investment * MAINTENANCE_SHARE_PER_YEAR, abs=0.01
    )
    assert CapexDefaultsRegistry.get_defaulted_fields_per_component()["PVSystem"] == [
        "investment_costs_in_euro",
        "device_co2_footprint_in_kg",
        "lifetime_in_years",
        "subsidy_as_percentage_of_investment_costs",
        "maintenance_costs_in_euro_per_year",
    ]


@pytest.mark.base
def test_defaulted_maintenance_follows_the_investment_cost_from_the_config() -> None:
    """The maintenance share applies to the investment cost actually in use, not the database one."""
    config = make_pv_config(investment_costs_in_euro=20000.0)

    capex = compute_for(config)

    assert capex.maintenance_costs_in_euro == pytest.approx(20000.0 * MAINTENANCE_SHARE_PER_YEAR, abs=0.01)


@pytest.mark.base
def test_fully_specified_config_never_consults_the_database() -> None:
    """A component type absent from the database is fine as long as the config is complete."""
    config = make_pv_config(
        investment_costs_in_euro=1234.0,
        device_co2_footprint_in_kg=567.0,
        lifetime_in_years=20.0,
        maintenance_costs_in_euro_per_year=89.0,
        subsidy_as_percentage_of_investment_costs=0.25,
    )

    capex = CapexComputationHelperFunctions.compute_capex_costs_and_emissions(
        simulation_parameters=SimulationParameters.full_year(year=2021, seconds_per_timestep=60),
        component_type=ComponentType.CAR,  # no capex entry exists for this one
        unit=Units.KILOWATT,
        size_of_energy_system=SIZE_IN_KW,
        config=config,
    )

    assert capex.capex_investment_cost_in_euro == 1234.0
    assert capex.device_co2_footprint_in_kg == 567.0
    assert capex.lifetime_in_years == 20.0
    assert capex.maintenance_costs_in_euro == 89.0
    assert capex.subsidy_as_percentage_of_investment_costs == 0.25
    assert CapexDefaultsRegistry.get_defaulted_fields_per_component() == {}


@pytest.mark.base
def test_quantity_values_are_accepted_alongside_plain_numbers() -> None:
    """Configs using the typed Quantity system may also mix set and unset fields."""
    config = make_pv_config(
        investment_costs_in_euro=Quantity(20000.0, Euro),
        maintenance_costs_in_euro_per_year=Quantity(100.0, Euro),
    )

    capex = compute_for(config)

    assert capex.capex_investment_cost_in_euro == 20000.0
    assert capex.maintenance_costs_in_euro == 100.0
    assert capex.lifetime_in_years == TECHNICAL_LIFETIME_IN_YEARS
    assert "lifetime_in_years" in CapexDefaultsRegistry.get_defaulted_fields_per_component()["PVSystem"]


@pytest.mark.base
def test_unset_field_without_database_value_raises_a_named_error() -> None:
    """When neither config nor database has a value, the error says which field is missing."""
    config = make_pv_config()

    # PV has no per-liter investment factor in the database.
    with pytest.raises(ValueError, match="investment_costs_in_euro of PVSystem"):
        compute_for(config, unit=Units.LITER)


@pytest.mark.base
def test_registry_deduplicates_repeated_computations() -> None:
    """get_cost_capex runs several times per component; each field is listed only once."""
    compute_for(make_pv_config(investment_costs_in_euro=20000.0))
    compute_for(make_pv_config(investment_costs_in_euro=20000.0))

    assert CapexDefaultsRegistry.get_defaulted_fields_per_component() == {
        "PVSystem": [
            "device_co2_footprint_in_kg",
            "lifetime_in_years",
            "subsidy_as_percentage_of_investment_costs",
            "maintenance_costs_in_euro_per_year",
        ]
    }


@pytest.mark.base
def test_log_summary_names_every_defaulted_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """The end-of-run warning lists each component with the fields it had defaulted."""
    logged: list = []
    monkeypatch.setattr(
        "hisim.postprocessing.cost_and_emission_computation.capex_computation.log.warning",
        logged.append,
    )
    CapexDefaultsRegistry.record("Battery", "device_co2_footprint_in_kg", "country DE, year 2021")
    CapexDefaultsRegistry.record("PVSystem", "investment_costs_in_euro", "country DE, year 2021")

    CapexDefaultsRegistry.log_summary()

    assert len(logged) == 1
    message = logged[0]
    assert "The following default values had to be used" in message
    assert "Battery: device_co2_footprint_in_kg" in message
    assert "PVSystem: investment_costs_in_euro" in message


@pytest.mark.base
def test_log_summary_stays_quiet_when_nothing_was_defaulted(monkeypatch: pytest.MonkeyPatch) -> None:
    """No fallbacks means no warning."""
    logged: list = []
    monkeypatch.setattr(
        "hisim.postprocessing.cost_and_emission_computation.capex_computation.log.warning",
        logged.append,
    )

    CapexDefaultsRegistry.log_summary()

    assert not logged
