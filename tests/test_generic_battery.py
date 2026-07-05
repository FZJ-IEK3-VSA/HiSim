"""Tests for the battery-spec injection seam in :mod:`hisim.components.generic_battery`.

These tests pin down two things introduced to make :class:`GenericBattery` testable
without the on-disk smart-appliance database:

* :func:`select_battery_spec` -- the pure, side-effect-free lookup helper that
  selects a battery spec dict from a loaded database by manufacturer/model.
* The optional ``battery_database`` parameter on :meth:`GenericBattery.__init__`
  / :meth:`GenericBattery.build`, which lets a test inject a small in-memory
  list of dicts instead of reading ``utils.load_smart_appliance("Battery")``.

They deliberately avoid touching the input file system, so they are fast and
free of external dependencies.
"""

# clean

from typing import Any

import pytest

from hisim.components.generic_battery import (
    GenericBattery,
    GenericBatteryConfig,
    select_battery_spec,
)
from hisim.simulationparameters import SimulationParameters


# Mark every test in this module as a fast ``base`` test (see pytest.ini).
pytestmark: pytest.MarkDecorator = pytest.mark.base


def _make_battery_db() -> list[dict[str, Any]]:
    """A tiny in-memory battery spec database for the tests below."""
    return [
        {
            "Manufacturer": "acme",
            "Model": "PowerBox 5",
            "Capacity": 5,
            "Maximal Charging Power": 3.4,
            "Maximal Discharging Power": 3.4,
            "Efficiency": 0.98,
            "Inverter Efficiency": 0.95,
        },
        {
            # Entry *without* "Maximal Discharging Power" to exercise the fallback
            # branch in :meth:`GenericBattery.build`.
            "Manufacturer": "acme",
            "Model": "PowerBox 10",
            "Capacity": 10,
            "Maximal Charging Power": 4.6,
            "Efficiency": 0.97,
            "Inverter Efficiency": 0.94,
        },
    ]


def test_select_battery_spec_returns_matching_entry() -> None:
    """``select_battery_spec`` returns the entry matching manufacturer + model."""
    db = _make_battery_db()
    spec = select_battery_spec(db, manufacturer="acme", model="PowerBox 5")

    assert spec is db[0]
    assert spec["Manufacturer"] == "acme"
    assert spec["Model"] == "PowerBox 5"
    assert spec["Capacity"] == 5


def test_select_battery_spec_returns_second_entry() -> None:
    """The lookup iterates and returns the *correct* match, not just the first."""
    db = _make_battery_db()
    spec = select_battery_spec(db, manufacturer="acme", model="PowerBox 10")

    assert spec is db[1]
    assert spec["Model"] == "PowerBox 10"
    assert spec["Capacity"] == 10


def test_select_battery_spec_raises_for_unknown_model() -> None:
    """An unknown model raises a clear ``ValueError`` instead of returning None."""
    db = _make_battery_db()
    with pytest.raises(ValueError, match="not registered in the provided battery database"):
        select_battery_spec(db, manufacturer="acme", model="Does Not Exist")


def test_select_battery_spec_raises_for_unknown_manufacturer() -> None:
    """An unknown manufacturer raises a clear ``ValueError`` as well."""
    db = _make_battery_db()
    with pytest.raises(ValueError, match="not registered in the provided battery database"):
        select_battery_spec(db, manufacturer="other-vendor", model="PowerBox 5")


def test_select_battery_spec_does_not_mutate_database() -> None:
    """The pure helper must not mutate its input database."""
    db = _make_battery_db()
    db_before = [dict(entry) for entry in db]
    select_battery_spec(db, manufacturer="acme", model="PowerBox 5")

    assert db == db_before


def test_generic_battery_builds_from_injected_database() -> None:
    """``GenericBattery`` can be built from an injected in-memory database.

    This is the core seam: passing ``battery_database`` avoids the call to
    ``utils.load_smart_appliance("Battery")`` entirely, so no input file is read.
    """
    sp = SimulationParameters.one_day_only(2017, 60)
    config = GenericBatteryConfig(
        building_name="BUI1",
        name="Generic Battery",
        manufacturer="acme",
        model="PowerBox 5",
        soc=0.5,
        base=False,
        predictive=False,
    )
    battery = GenericBattery(
        my_simulation_parameters=sp,
        config=config,
        battery_database=_make_battery_db(),
    )

    # Capacity is stored in kWh in the database and converted to Wh here.
    assert battery.max_stored_energy == 5 * 1e3
    assert battery.min_stored_energy == 0.0
    assert battery.efficiency == 0.98
    assert battery.efficiency_inverter == 0.95

    seconds_per_timestep = sp.seconds_per_timestep
    time_correction_factor = 1 / seconds_per_timestep
    assert battery.max_var_stored_energy == 3.4 * 1e3 * time_correction_factor
    assert battery.min_var_stored_energy == -3.4 * 1e3 * time_correction_factor
    # base is propagated from the config.
    assert battery.base is False


def test_generic_battery_uses_discharging_power_when_present() -> None:
    """When ``Maximal Discharging Power`` is present it drives ``min_var``."""
    sp = SimulationParameters.one_day_only(2017, 60)
    config = GenericBatteryConfig(
        building_name="BUI1",
        name="Generic Battery",
        manufacturer="acme",
        model="PowerBox 5",
        soc=0.5,
        base=False,
        predictive=False,
    )
    battery = GenericBattery(
        my_simulation_parameters=sp,
        config=config,
        battery_database=_make_battery_db(),
    )

    time_correction_factor = 1 / sp.seconds_per_timestep
    assert battery.min_var_stored_energy == -3.4 * 1e3 * time_correction_factor


def test_generic_battery_falls_back_when_discharging_power_absent() -> None:
    """When ``Maximal Discharging Power`` is absent ``min_var`` falls back to ``-max_var``."""
    sp = SimulationParameters.one_day_only(2017, 60)
    config = GenericBatteryConfig(
        building_name="BUI1",
        name="Generic Battery",
        manufacturer="acme",
        model="PowerBox 10",
        soc=0.5,
        base=False,
        predictive=False,
    )
    battery = GenericBattery(
        my_simulation_parameters=sp,
        config=config,
        battery_database=_make_battery_db(),
    )

    # No "Maximal Discharging Power" key -> min_var_stored_energy == -max_var_stored_energy.
    assert battery.min_var_stored_energy == -battery.max_var_stored_energy
    assert battery.efficiency == 0.97
    assert battery.efficiency_inverter == 0.94


def test_generic_battery_raises_for_unknown_model_with_injected_db() -> None:
    """An unknown model surfaces as a ``ValueError`` even with an injected database."""
    sp = SimulationParameters.one_day_only(2017, 60)
    config = GenericBatteryConfig(
        building_name="BUI1",
        name="Generic Battery",
        manufacturer="acme",
        model="Does Not Exist",
        soc=0.5,
        base=False,
        predictive=False,
    )
    with pytest.raises(ValueError, match="not registered in the provided battery database"):
        GenericBattery(
            my_simulation_parameters=sp,
            config=config,
            battery_database=_make_battery_db(),
        )
