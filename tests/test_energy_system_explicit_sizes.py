"""Tests that a size written into an energy-system file reaches the component unchanged.

A battery and a heat pump are sized by the system around them when their preset leaves the size
``AUTO``: the battery from the photovoltaic peak power beside it, the heat pump from the heating
load of the building it heats. A caller who knows the device writes the size into the entry's
sparse ``config:`` block instead, for example::

    MoreAdvancedHeatPumpHPLib:
      preset: air_water
      config:
        set_thermal_output_power_in_watt: 8000

The tests below take the recorded heat-pump twin, pin sizes on it and configure it exactly as a
run does (group expansion, class binding, sizing kernel). They check that the pinned numbers come
out unchanged, that the component sized from the heat pump follows the pinned power, that a battery
capacity pinned alone leaves the battery's power to its law, and that the same file without the
pins still sizes both devices from their laws.
"""

from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Sequence

import pytest
import yaml

from hisim.energy_system import expand_groups
from hisim.energy_system.configure import ConfiguredSystem, configure_energy_system
from hisim.energy_system.loader import load_energy_system


class PinnedTwin:
    """Loads the recorded heat-pump twin, optionally with pinned sizes, and configures it.

    The twin is edited as plain YAML data rather than as text, so the test does not depend on the
    file's formatting. It does depend on where the two entries sit, and names it here: the heat
    pump is a top-level component, the battery sits in the ``ems_with_battery`` option of the
    ``electricity_management`` variant. A re-recording that moves either fails the edit with a
    ``KeyError`` naming the missing key.
    """

    #: The recorded grouped twin of the heat-pump building-sizer setup.
    TWIN: ClassVar[Path] = (
        Path(__file__).resolve().parents[1]
        / "energy_systems"
        / "household_heatpump_building_sizer.grouped.energy_system.yaml"
    )

    #: Entry names in the twin.
    HEAT_PUMP: ClassVar[str] = "MoreAdvancedHeatPumpHPLib"
    BATTERY: ClassVar[str] = "Battery"
    BUFFER: ClassVar[str] = "SimpleHotWaterStorage"
    PV: ClassVar[str] = "PVSystem"

    @classmethod
    def configure(
        cls,
        heat_pump: Optional[Dict[str, Any]] = None,
        battery: Optional[Dict[str, Any]] = None,
    ) -> ConfiguredSystem:
        """Configures the twin with the given fields added to its entries.

        Example: ``PinnedTwin.configure(heat_pump={"set_thermal_output_power_in_watt": 8000})``
        configures the twin with an 8 kW heat pump and everything else as recorded. The edited
        document is handed to the loader as YAML text, so nothing is written to disk.

        Args:
            heat_pump: Fields to add to the heat pump's ``config:`` block, or ``None`` for none.
            battery: Fields to add to the battery's ``config:`` block, or ``None`` for none.

        Returns:
            The configured system: every configuration built and sized.
        """
        document = yaml.safe_load(cls.TWIN.read_text(encoding="utf-8"))
        if heat_pump:
            document["components"][cls.HEAT_PUMP].setdefault("config", {}).update(heat_pump)
        if battery:
            option = document["variants"]["electricity_management"]["options"]["ems_with_battery"]
            option["components"][cls.BATTERY].setdefault("config", {}).update(battery)
        expanded, _ = expand_groups(load_energy_system(yaml.safe_dump(document, sort_keys=False)))
        return configure_energy_system(expanded)

    @staticmethod
    def facts_read(configured: ConfiguredSystem, consumer: str, fact: str) -> Sequence[Any]:
        """Returns the values of a fact one entry read while it was sized, one per provider.

        Example: ``PinnedTwin.facts_read(configured, "Battery", "pv_peak_power_in_watt")`` gives
        the peak power of every array the battery was sized to; the twin has one array, so one value.

        Args:
            configured: The configured system whose sizing report is read.
            consumer: The entry name of the component that read the fact.
            fact: The fact's name, e.g. ``"heating_load_in_watt"``.

        Returns:
            The values read, one per providing entry, in the report's order.
        """
        return [
            lookup.value
            for lookup in configured.report.lookups
            if lookup.consumer == consumer and lookup.fact == fact
        ]


@pytest.mark.base
class TestExplicitSizes:
    """hisim-4g9.2: a stated battery capacity and heat-pump power are the sizes the run builds."""

    def test_pinned_sizes_reach_the_components_unchanged(self) -> None:
        """Each pinned field comes out of the sizing kernel exactly as written."""
        configured = PinnedTwin.configure(
            heat_pump={"set_thermal_output_power_in_watt": 8000.0},
            battery={
                "custom_battery_capacity_generic_in_kilowatt_hour": 15.0,
                "custom_pv_inverter_power_generic_in_watt": 6000.0,
            },
        )

        assert configured.config_of(PinnedTwin.HEAT_PUMP).set_thermal_output_power_in_watt == 8000.0
        battery = configured.config_of(PinnedTwin.BATTERY)
        assert battery.custom_battery_capacity_generic_in_kilowatt_hour == 15.0
        assert battery.custom_pv_inverter_power_generic_in_watt == 6000.0

    def test_the_buffer_follows_a_pinned_heat_pump(self) -> None:
        """The buffer reads the heat pump's resolved power, so a pinned machine gets its own vessel."""
        auto = PinnedTwin.configure()
        pinned = PinnedTwin.configure(heat_pump={"set_thermal_output_power_in_watt": 8000.0})

        auto_power_in_kilowatt = auto.config_of(PinnedTwin.HEAT_PUMP).set_thermal_output_power_in_watt / 1e3
        auto_volume_in_liter = auto.config_of(PinnedTwin.BUFFER).volume_heating_water_storage_in_liter
        pinned_volume_in_liter = pinned.config_of(PinnedTwin.BUFFER).volume_heating_water_storage_in_liter
        assert auto_power_in_kilowatt != pytest.approx(8.0)
        assert pinned_volume_in_liter / 8.0 == pytest.approx(auto_volume_in_liter / auto_power_in_kilowatt, rel=1e-3)

    def test_a_capacity_pinned_alone_leaves_the_power_to_its_law(self) -> None:
        """The battery's two fields have independent laws: pinning the capacity keeps the PV-derived power."""
        auto = PinnedTwin.configure()
        pinned = PinnedTwin.configure(battery={"custom_battery_capacity_generic_in_kilowatt_hour": 15.0})

        battery = pinned.config_of(PinnedTwin.BATTERY)
        assert battery.custom_battery_capacity_generic_in_kilowatt_hour == 15.0
        auto_power_in_watt = auto.config_of(PinnedTwin.BATTERY).custom_pv_inverter_power_generic_in_watt
        assert battery.custom_pv_inverter_power_generic_in_watt == auto_power_in_watt

    def test_without_pins_both_devices_are_sized_by_their_laws(self) -> None:
        """Regression: the recorded twin still sizes the heat pump from the building and the battery from the PV."""
        configured = PinnedTwin.configure()

        heating_loads = PinnedTwin.facts_read(configured, PinnedTwin.HEAT_PUMP, "heating_load_in_watt")
        assert len(heating_loads) == 1
        assert configured.config_of(PinnedTwin.HEAT_PUMP).set_thermal_output_power_in_watt == heating_loads[0]
        # The battery's laws sum the peak power of every array it is sized to.
        pv_power_in_watt = sum(PinnedTwin.facts_read(configured, PinnedTwin.BATTERY, "pv_peak_power_in_watt"))
        assert pv_power_in_watt > 0
        battery = configured.config_of(PinnedTwin.BATTERY)
        assert battery.custom_battery_capacity_generic_in_kilowatt_hour == round(pv_power_in_watt * 1e-3, 2)
        assert battery.custom_pv_inverter_power_generic_in_watt == round(pv_power_in_watt * 0.5, 2)
