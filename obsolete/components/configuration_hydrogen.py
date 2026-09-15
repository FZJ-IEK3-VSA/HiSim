"""The dead hydrogen configuration classes of `configuration.py` (D-29).

Moved out of `hisim/components/configuration.py` by component sweep decision D-29
(2026-09-11), the way D-16 moved its legacy neighbours into `configuration_legacy.py`.
`configuration.py` keeps everything else. Both classes were verified to have zero
references across `hisim/`, `tests/`, `system_setups/`, `energy_systems/` and `scripts/`
before the move:

* `AdvElectrolyzerConfig` — the dead third copy of the electrolyzer configuration, and the
  reason it is dated here rather than under D-16. It duplicates
  `generic_electrolyzer_and_h2_storage.ElectrolyzerWithStorageConfig` field for field and
  disagrees with it: `max_power = 2_4000` (24 000 W against the live class's 2 400 W, a
  missing underscore) and `min_power = 1_400` against 1 200. Its only reader in the
  repository is `obsolete/components/advanced_fuel_cell_controller.py`, retired under D-8,
  which is archived with its import paths as they stood and is not importable in place.
* `HydrogenStorageConfig` — a plain class of class attributes duplicating the storage
  configuration. No reference of any kind; the one mention left in the tree is a bare
  comment in `tests/test_generic_electrolyzer_and_h2_storage.py`.

D-29 kept the `generic_electrolyzer_and_h2_storage.py` pair — `ElectrolyzerWithStorageConfig`
/ `AdvancedElectrolyzer` and `ElectrolyzerWithHydrogenStorageConfig` / `HydrogenStorage` —
because it models waste energy and part-load percentages the retired `generic_*` pair did
not. The retired pair itself is in `obsolete/components/generic_electrolyzer.py` and
`obsolete/components/generic_hydrogen_storage.py`.

The classes below are the file's own text, unchanged. Nothing here is maintained.
"""


class HydrogenStorageConfig:
    """Hydrogen storage config class."""

    # combination of
    min_capacity = 0  # [kg_H2]
    max_capacity = 500  # [kg_H2]

    starting_fill = 400  # [kg_H2]

    max_charging_rate_hour = 2  # [kg/h]
    max_discharging_rate_hour = 2  # [kg/h]
    max_charging_rate = max_charging_rate_hour / 3600
    max_discharging_rate = max_discharging_rate_hour / 3600

    # ToDo: How does the necessary Heat/Energy come to the Storage?
    energy_for_charge = 0  # [kWh/kg]
    energy_for_discharge = 0  # [kWh/kg]

    loss_factor_per_day = 0  # [lost_%/day]


class AdvElectrolyzerConfig:
    """Adv electrolyzer config class."""

    waste_energy = 400  # [W]   # 400
    min_power = 1_400  # [W]   # 1400
    max_power = 2_4000  # [W]   # 2400
    min_power_percent = 60  # [%]
    max_power_percent = 100  # [%]
    min_hydrogen_production_rate_hour = 300  # [Nl/h]
    max_hydrogen_production_rate_hour = 5000  # [Nl/h]   #500
    min_hydrogen_production_rate = min_hydrogen_production_rate_hour / 3600  # [Nl/s]
    max_hydrogen_production_rate = max_hydrogen_production_rate_hour / 3600  # [Nl/s]
    pressure_hydrogen_output = 30  # [bar]     --> max pressure mode at 35 bar

    """
    The production rate can be converted to an efficiency.
    eff_electrolyzer = (production_rate_hour * hydrogen_specific_heat_capacity_per_kg[kWh/kg]) / (Power_this_timestep[kWh] * hydrogen_specific_volume [m³kg])

    in the component electrolyzer:
    hydrogen_output = Power_this_timestep[kWh] * eff_electrolyzer / hydrogen_specific_heat_capacity_per_kg[kWh/kg]

    I think its overengineering because the providers give the needed information and we try to calculate it back and forth

    --> Solution: efficiency of the electrolyzer is calculated and is an Output
    """
