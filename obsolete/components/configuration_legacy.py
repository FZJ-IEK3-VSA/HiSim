"""The dead legacy configuration classes of `configuration.py` (D-16).

Moved out of `hisim/components/configuration.py` by component sweep decision D-16
(2026-09-10). Each was verified to have zero live references across `hisim/`, `tests/`,
`system_setups/`, `energy_systems/`, `scripts/` and `docs/` before the move:

* `WarmWaterStorageConfig` — named only inside a commented-out block of
  `hisim/components/generic_heat_pump.py`, which is left as the comment it is.
* `LoadConfig` — no reference anywhere.
* `ElectricityDemandConfig` — no reference anywhere.
* `PVConfig` — no reference anywhere; the live PV configuration is
  `generic_pv_system.PVSystemConfig`.

Three of their neighbours deliberately stayed behind in `configuration.py`, and this file is
not the place to look for them. `HouseholdWarmWaterDemandConfig` is live — its class constants
are read by `loadprofilegenerator_utsp_connector.py` and `simple_water_storage.py`.
`HydrogenStorageConfig` and `AdvElectrolyzerConfig` belong to the hydrogen chain and are
decided under D-25/D-29, not here. `GasHeaterConfig`, `PhysicsConfig` and the two emission
factor tables are untouched.

The classes below are the file's own text, unchanged. Nothing here is maintained.
"""

from typing import Any, Optional
from dataclasses import dataclass
from dataclasses_json import dataclass_json

from hisim.config import ConfigBase, ComponentID


@dataclass_json
@dataclass
class WarmWaterStorageConfig(ConfigBase):
    """Warm water storage config class."""

    component_id: ComponentID
    tank_diameter: float  # [m]
    tank_height: float  # [m]
    tank_start_temperature: float  # [°C]
    temperature_difference: float  # [°C]
    tank_u_value: float  # [W/m^2*K]
    slice_height_minimum: float  # [m]

    @classmethod
    def get_default_config(
        cls,
        component_id: Optional[ComponentID] = None,
    ) -> Any:
        """Gets a default config."""
        if component_id is None:
            component_id = ComponentID(name="WarmWaterStorage")
        return WarmWaterStorageConfig(
            component_id=component_id,
            tank_diameter=1,  # 0.9534        # [m]
            tank_height=2,  # 3.15              # [m]
            tank_start_temperature=65,  # [°C]
            temperature_difference=0.3,  # [°C]
            tank_u_value=0,  # 0.35                 # [W/m^2*K]
            slice_height_minimum=0.05,  # [m]
        )


class LoadConfig:
    """Load config."""

    # massflow_load_minute = 2.5          # [kg/min]
    # massflow_load = massflow_load_minute / 60   # [kg/s]

    possible_massflows_load = [0.1, 0.2, 0.3, 0.4]  # [kg/s]
    delta_temperature = 20

    # the returnflow shows if there was enough energy in the water
    # -> use in load! Not in storage, there the water from WW is included
    temperature_returnflow_minimum = 30  # [°C]

    kwh_per_year = 20_201
    demand_factor = kwh_per_year / 1000


class ElectricityDemandConfig:
    """Electricity demand config class."""

    kwh_per_year = 6000
    demand_factor = kwh_per_year / 1000


class PVConfig:
    """PV config class."""

    peak_power = 20_000  # [W]
