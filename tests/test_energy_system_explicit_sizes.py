"""Tests that a size written into an energy-system file reaches the component unchanged.

A battery and a heat pump are sized by the system around them when their preset leaves the size
``AUTO``: the battery from the photovoltaic peak power beside it, the heat pump from the heating
load of the building it heats. A caller who knows the device writes the size into the entry's
sparse ``config:`` block instead, for example::

    MoreAdvancedHeatPumpHPLib:
      preset: air_water
      config:
        set_thermal_output_power_in_watt: 8000

The tests below take the recorded heat-pump twin, pin both sizes on it and configure it exactly as
a run does (group expansion, class binding, sizing kernel). They check that the pinned numbers come
out unchanged, that the component sized from the heat pump follows the pinned power, and that the
same file without the pins still sizes both devices from their laws.
"""

from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

import pytest
import yaml

from hisim.energy_system import expand_groups
from hisim.energy_system.configure import ConfiguredSystem, configure_energy_system
from hisim.energy_system.loader import load_energy_system


class PinnedTwin:
    """Writes the recorded heat-pump twin, optionally with pinned sizes, and configures it.

    The twin is edited as plain YAML data rather than as text, so the test does not depend on
    the file's layout: the heat pump is a top-level component, the battery sits in the
    ``ems_with_battery`` option of the ``electricity_management`` variant.
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
        directory: Path,
        heat_pump: Optional[Dict[str, Any]] = None,
        battery: Optional[Dict[str, Any]] = None,
    ) -> ConfiguredSystem:
        """Writes the twin with the given overrides into a directory and configures it.

        Example: ``PinnedTwin.configure(tmp_path, heat_pump={"set_thermal_output_power_in_watt": 8000})``
        configures the twin with an 8 kW heat pump and everything else as recorded.

        Args:
            directory: The test's temporary directory; the edited file is written there.
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
        path = directory / cls.TWIN.name
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        expanded, _ = expand_groups(load_energy_system(path))
        return configure_energy_system(expanded)


@pytest.mark.base
class TestExplicitSizes:
    """hisim-4g9.2: a stated battery capacity and heat-pump power are the sizes the run builds."""

    def test_pinned_sizes_reach_the_components_unchanged(self, tmp_path: Path) -> None:
        """Each pinned field comes out of the sizing kernel exactly as written."""
        configured = PinnedTwin.configure(
            tmp_path,
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

    def test_the_buffer_follows_a_pinned_heat_pump(self, tmp_path: Path) -> None:
        """The buffer reads the heat pump's resolved power, so a pinned machine gets its own vessel."""
        auto = PinnedTwin.configure(tmp_path)
        pinned = PinnedTwin.configure(tmp_path, heat_pump={"set_thermal_output_power_in_watt": 8000.0})

        auto_power_in_kilowatt = auto.config_of(PinnedTwin.HEAT_PUMP).set_thermal_output_power_in_watt / 1e3
        auto_volume_in_liter = auto.config_of(PinnedTwin.BUFFER).volume_heating_water_storage_in_liter
        pinned_volume_in_liter = pinned.config_of(PinnedTwin.BUFFER).volume_heating_water_storage_in_liter
        assert auto_power_in_kilowatt != pytest.approx(8.0)
        assert pinned_volume_in_liter / 8.0 == pytest.approx(auto_volume_in_liter / auto_power_in_kilowatt, rel=1e-3)

    def test_without_pins_both_devices_are_sized_by_their_laws(self, tmp_path: Path) -> None:
        """Regression: the recorded twin still sizes the heat pump from the building and the battery from the PV."""
        configured = PinnedTwin.configure(tmp_path)

        heating_loads = [
            lookup.value
            for lookup in configured.report.lookups
            if lookup.consumer == PinnedTwin.HEAT_PUMP and lookup.fact == "heating_load_in_watt"
        ]
        assert len(heating_loads) == 1
        assert configured.config_of(PinnedTwin.HEAT_PUMP).set_thermal_output_power_in_watt == heating_loads[0]
        pv_power_in_watt = configured.config_of(PinnedTwin.PV).power_in_watt
        battery = configured.config_of(PinnedTwin.BATTERY)
        assert battery.custom_battery_capacity_generic_in_kilowatt_hour == round(pv_power_in_watt * 1e-3, 2)
        assert battery.custom_pv_inverter_power_generic_in_watt == round(pv_power_in_watt * 0.5, 2)
