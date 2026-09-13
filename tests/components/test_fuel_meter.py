"""Pure-helper unit tests for :mod:`hisim.components.fuel_meter`.

These tests pin down small, deterministic, side-effect-free callables in the
``fuel_meter`` module that previously had no dedicated coverage:

* :func:`FuelMeterConfig.get_main_classname`
* :func:`FuelMeterConfig.preset_standard`
* :meth:`FuelMeter.get_cost_capex`
* :meth:`FuelMeterState.self_copy`

They deliberately avoid constructing a :class:`FuelMeter` instance or running a
simulation, so they are fast and free of external dependencies.
"""

# clean

import dataclasses

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim.components.fuel_meter import FuelMeter, FuelMeterConfig, FuelMeterState
from hisim.config import ComponentID, DisplayConfig, SizingContext, auto_fields

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


def test_the_preset_leaves_every_fuel_value_open() -> None:
    """The preset names the instance and pins nothing else, so the three laws can do the work.

    The deleted ``get_fuel_meter_default_config`` factory shipped a rounded oil heating value
    and an oil density whatever carrier it was asked for; the preset ships neither, which is
    what makes a meter for pellets or wood chips impossible to get wrong.
    """
    config = FuelMeterConfig.preset_standard("FuelMeter")

    assert config.component_id.name == "FuelMeter"
    assert config.component_id.building is None
    assert sorted(auto_fields(config)) == [
        "fuel_density_in_kg_per_m3",
        "fuel_loadtype",
        "heating_value_of_fuel_in_kwh_per_liter",
    ]


def test_the_preset_resolves_against_the_generators_fuel_facts() -> None:
    """Resolving the preset against one context fills all three fields and nothing else.

    The carrier and the two constants arrive as facts, which is how a converted generator hands
    them over; a hand-built context is the same thing written out.
    """
    config = FuelMeterConfig.preset_standard("FuelMeter").resolve(
        SizingContext(
            energy_carrier=lt.LoadTypes.PELLETS,
            heating_value_of_fuel_in_kwh_per_liter=3.25,
            fuel_density_in_kg_per_m3=650.0,
        )
    )

    assert config.fuel_loadtype == lt.LoadTypes.PELLETS
    assert config.heating_value_of_fuel_in_kwh_per_liter == 3.25
    assert config.fuel_density_in_kg_per_m3 == 650.0
    assert auto_fields(config) == ()


def test_a_meter_built_for_one_carrier_can_be_named_per_building() -> None:
    """The instance name carries the building label, which the factory took as a whole identity.

    ``preset_standard`` takes only a name, so a second building's meter is built by replacing
    the identity -- the one thing the deleted factory's ``component_id`` argument was used for.
    """
    config = dataclasses.replace(
        FuelMeterConfig.preset_standard("FuelMeter"),
        component_id=ComponentID(name="FuelMeter", building="BUI2"),
    )

    assert config.component_id.building == "BUI2"
    assert config.component_id.name == "FuelMeter"


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


def test_fuel_meter_display_config_not_shared() -> None:
    """Two FuelMeter instances must not share a mutable DisplayConfig default."""
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )
    config = FuelMeterConfig.preset_standard("FuelMeter").resolve(
        SizingContext(
            energy_carrier=lt.LoadTypes.OIL,
            heating_value_of_fuel_in_kwh_per_liter=9.821666666666667,
            fuel_density_in_kg_per_m3=OIL_DENSITY_IN_KG_PER_LITER * 1e3,
        )
    )

    meter_a = FuelMeter(
        my_simulation_parameters=my_simulation_parameters, config=config
    )
    meter_b = FuelMeter(
        my_simulation_parameters=my_simulation_parameters, config=config
    )

    assert meter_a.my_display_config is not meter_b.my_display_config
    assert isinstance(meter_a.my_display_config, DisplayConfig)
    assert isinstance(meter_b.my_display_config, DisplayConfig)

    # mutating one must not affect the other
    meter_a.my_display_config.pretty_name = "meter_a"
    assert meter_b.my_display_config.pretty_name is None
