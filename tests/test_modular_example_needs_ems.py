"""Unit tests for the ``needs_ems`` decision function in ``system_setups.modular_example``.

``needs_ems`` is a pure, deterministic boolean predicate over its arguments: it
only reads its parameters and the stable ``hisim.loadtypes.HeatingSystems``
enum, performs no I/O and mutates no module state. The tests below cover every
triggering branch plus the two edge cases (all-false final ``return False``
path and multiple triggers short-circuiting to ``True``).
"""

# clean

from __future__ import annotations

import pytest

import hisim.loadtypes as lt
from system_setups.modular_example import needs_ems


def _needs_ems(
    battery_included: bool = False,
    chp_included: bool = False,
    hydrogen_setup_included: bool = False,
    ev_included: bool = False,
    heating_system_installed: lt.HeatingSystems = lt.HeatingSystems.GAS_HEATING,
    smart_devices_included: bool = False,
    water_heating_system_installed: lt.HeatingSystems = lt.HeatingSystems.GAS_HEATING,
) -> bool:
    """Thin keyword-only wrapper around ``needs_ems`` with sensible defaults.

    Every boolean flag defaults to ``False`` and both heating-system arguments
    default to a non-electric system (``GAS_HEATING``) so that only the single
    trigger under test is exercised in each parametrized case.
    """
    return bool(
        needs_ems(
            battery_included=battery_included,
            chp_included=chp_included,
            hydrogen_setup_included=hydrogen_setup_included,
            ev_included=ev_included,
            heating_system_installed=heating_system_installed,
            smart_devices_included=smart_devices_included,
            water_heating_system_installed=water_heating_system_installed,
        )
    )


@pytest.mark.base
@pytest.mark.parametrize(
    ("expected", "kwargs"),
    [
        # 1. All flags False, non-electric heating -> False.
        (
            False,
            {},
        ),
        # 2. battery_included=True alone -> True.
        (True, {"battery_included": True}),
        # 3. chp_included=True alone -> True.
        (True, {"chp_included": True}),
        # 4. hydrogen_setup_included=True alone -> True.
        (True, {"hydrogen_setup_included": True}),
        # 5. smart_devices_included=True alone -> True.
        (True, {"smart_devices_included": True}),
        # 6. ev_included=True alone -> True.
        (True, {"ev_included": True}),
        # 7. heating_system_installed=HEAT_PUMP alone -> True.
        (
            True,
            {
                "heating_system_installed": lt.HeatingSystems.HEAT_PUMP,
                "water_heating_system_installed": lt.HeatingSystems.GAS_HEATING,
            },
        ),
        # 8. heating_system_installed=ELECTRIC_HEATING alone -> True.
        (
            True,
            {
                "heating_system_installed": lt.HeatingSystems.ELECTRIC_HEATING,
                "water_heating_system_installed": lt.HeatingSystems.GAS_HEATING,
            },
        ),
        # 9. water_heating_system_installed=HEAT_PUMP alone -> True.
        (
            True,
            {
                "heating_system_installed": lt.HeatingSystems.GAS_HEATING,
                "water_heating_system_installed": lt.HeatingSystems.HEAT_PUMP,
            },
        ),
        # 10. water_heating_system_installed=ELECTRIC_HEATING alone -> True.
        (
            True,
            {
                "heating_system_installed": lt.HeatingSystems.GAS_HEATING,
                "water_heating_system_installed": lt.HeatingSystems.ELECTRIC_HEATING,
            },
        ),
        # 11. Edge: all flags False, both heating systems non-matching -> False.
        (
            False,
            {
                "heating_system_installed": lt.HeatingSystems.OIL_HEATING,
                "water_heating_system_installed": lt.HeatingSystems.DISTRICT_HEATING,
            },
        ),
        # 12. Edge: multiple triggers simultaneously -> True (short-circuit).
        (
            True,
            {"battery_included": True, "ev_included": True},
        ),
    ],
)
def test_needs_ems(expected: bool, kwargs: dict[str, bool | lt.HeatingSystems]) -> None:
    """Assert ``needs_ems`` returns ``expected`` for the given keyword triggers."""
    assert _needs_ems(**kwargs) is expected
