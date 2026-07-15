"""Pure-helper unit tests for :mod:`hisim.components.fuel_meter`.

These tests pin down small, deterministic, side-effect-free callables in the
``fuel_meter`` module that previously had no dedicated coverage:

* :func:`FuelMeterConfig.get_main_classname`
* :func:`FuelMeterConfig.get_fuel_meter_default_config`
* :meth:`FuelMeter.get_cost_capex`
* :meth:`FuelMeterState.self_copy`

They deliberately avoid constructing a :class:`FuelMeter` instance or running a
simulation, so they are fast and free of external dependencies.
"""

# clean

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.fuel_meter import FuelMeter, FuelMeterConfig, FuelMeterState

# Heating-oil density expressed in kg per liter. The stored field
# ``fuel_density_in_kg_per_m3`` uses kg/m^3, and since 1 L == 1e-3 m^3 the
# conversion is kg/L * 1e3 == kg/m^3. Documented explicitly here so the
# magic ``* 1e3`` factor in the assertions below is no longer unexplained.
OIL_DENSITY_IN_KG_PER_LITER: float = 0.83  # kg/L -- typical density of heating oil


# Mark every test in this module as a fast ``base`` test (see pytest.ini).
pytestmark: pytest.MarkDecorator = pytest.mark.base


def test_get_main_classname_returns_full_class_path() -> None:
    """``get_main_classname`` must return the module-qualified ``FuelMeter`` path."""
    expected = FuelMeter.get_full_classname()
    actual = FuelMeterConfig.get_main_classname()
    assert actual == expected
    # Sanity: it is the fully-qualified path, not just the bare class name.
    assert actual == "hisim.components.fuel_meter.FuelMeter"


def test_get_fuel_meter_default_config_with_no_arguments() -> None:
    """Default factory produces the documented hardcoded defaults."""
    config = FuelMeterConfig.get_fuel_meter_default_config()

    assert config.name == "FuelMeter"
    assert config.fuel_loadtype == lt.LoadTypes.OIL
    assert config.heating_value_of_fuel_in_kwh_per_liter == 9.82
    assert config.fuel_density_in_kg_per_m3 == OIL_DENSITY_IN_KG_PER_LITER * 1e3  # kg/L -> kg/m^3
    assert config.building_name == "BUI1"


def test_get_fuel_meter_default_config_with_custom_building_name() -> None:
    """Passing ``building_name`` only changes that field, keeping the rest."""
    config = FuelMeterConfig.get_fuel_meter_default_config(building_name="BUI2")

    assert config.building_name == "BUI2"
    # All other defaults are preserved.
    assert config.name == "FuelMeter"
    assert config.fuel_loadtype == lt.LoadTypes.OIL
    assert config.heating_value_of_fuel_in_kwh_per_liter == 9.82
    assert config.fuel_density_in_kg_per_m3 == OIL_DENSITY_IN_KG_PER_LITER * 1e3  # kg/L -> kg/m^3


def test_get_fuel_meter_default_config_with_custom_fuel_loadtype() -> None:
    """Passing ``fuel_loadtype`` propagates it while numeric defaults stay put."""
    config = FuelMeterConfig.get_fuel_meter_default_config(
        fuel_loadtype=lt.LoadTypes.PELLETS
    )

    assert config.fuel_loadtype == lt.LoadTypes.PELLETS
    # Numeric defaults unchanged.
    assert config.heating_value_of_fuel_in_kwh_per_liter == 9.82
    assert config.fuel_density_in_kg_per_m3 == OIL_DENSITY_IN_KG_PER_LITER * 1e3  # kg/L -> kg/m^3
    # And the other defaults are preserved too.
    assert config.name == "FuelMeter"
    assert config.building_name == "BUI1"


def test_get_cost_capex_returns_default_capex_and_ignores_none_inputs() -> None:
    """``get_cost_capex`` ignores its arguments and returns the default capex data.

    Because both arguments are unused (``config`` and ``simulation_parameters``),
    passing ``None`` must not raise and must yield the default capex data class.
    """
    # Should not raise with None inputs - pins the "unused argument" contract.
    capex = FuelMeter.get_cost_capex(config=None, simulation_parameters=None)  # type: ignore[arg-type]

    expected = cp.CapexCostDataClass.get_default_capex_cost_data_class()
    # CapexCostDataClass is a dataclass, so == compares field-by-field.
    assert capex == expected
    # And it is a fresh instance rather than the cached module-level singleton,
    # should one ever be introduced.
    assert isinstance(capex, cp.CapexCostDataClass)


@pytest.mark.parametrize(
    "consumption_in_watt_hour",
    [0.0, 1234.5, -10.0],
    ids=["zero", "positive", "negative"],
)
def test_fuel_meter_state_self_copy_preserves_value_and_returns_new_instance(
    consumption_in_watt_hour: float,
) -> None:
    """``self_copy`` returns a distinct ``FuelMeterState`` with the same value."""
    state = FuelMeterState(cumulative_consumption_in_watt_hour=consumption_in_watt_hour)
    copy = state.self_copy()

    # Distinct object, same value (covers the zero, positive, and negative cases).
    assert copy is not state
    assert isinstance(copy, FuelMeterState)
    assert copy.cumulative_consumption_in_watt_hour == consumption_in_watt_hour
