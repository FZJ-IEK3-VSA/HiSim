"""The fuel cell manufacturer-table path, archived (D-25).

`hisim/inputs/fuel_cell_manufacturer_config.json` **does not exist in this repository and
never did**, as far as its history can tell. Every function below opened it, so every one of
them raised `FileNotFoundError` on the first call, from the day it was written. Component
sweep decision D-25 (2026-09-11) therefore removed this path from the three live modules
rather than converting it, and this file is where its text is kept.

What came from where, all of it unchanged:

* `FuelCellConfig.read_config` and `FuelCellConfig.config_fuel_cell` — from
  `hisim/components/generic_fuel_cell.py`. The module keeps `FuelCellConfig` and its
  hand-typed `get_default_pem_fuel_cell_config`, which builds.
* `FuelCellControllerConfig.read_config` and `FuelCellControllerConfig.control_fuel_cell` —
  from `hisim/components/controller_l1_fuel_cell.py`. The module keeps
  `FuelCellControllerConfig` and its hand-typed `get_default_fuel_cell_controller_config`,
  which builds.
* `XTPControllerConfig.read_config` and `XTPControllerConfig.control_fuel_cell` — from
  `hisim/components/controller_l2_xtp_fuel_cell_ems.py`. That class had **no other factory**,
  so it is left with its fields and its `XtpOperationMode` enum and no default builder at all
  until the conversion batch gives it a preset. Its component and its tests stay in `hisim/`
  and `tests/`; only this reader left.

The classes here are shells that exist to hold the six methods at the indentation they had.
They are **not** the configuration classes — those are still the live ones in
`hisim/components/`, and nothing imports this file. The bodies name `FuelCellConfig`,
`FuelCellControllerConfig` and `XTPControllerConfig`: read those as the live classes the
factories returned. Nothing here is maintained.
"""

from pathlib import Path
from typing import Optional, Any, cast
import json

from hisim.config import ComponentID
from hisim import utils


class FuelCellConfigTablePath:
    """From `FuelCellConfig` in `hisim/components/generic_fuel_cell.py`."""

    @staticmethod
    def read_config(fuel_cell_name):
        """Read config."""
        config_file = Path(utils.HISIMPATH["inputs"]) / "fuel_cell_manufacturer_config.json"
        with config_file.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Fuel Cell variants", {}).get(fuel_cell_name, {})

    @classmethod
    def config_fuel_cell(
        cls,
        fuel_cell_name: str,
        component_id: Optional[ComponentID] = None,
    ) -> "FuelCellConfig":
        """Get config of fuel cell."""
        if component_id is None:
            component_id = ComponentID(name="FuelCell")
        config_json = cls.read_config(fuel_cell_name)
        config = FuelCellConfig(
            component_id=component_id,  # config_json.get("name", "")
            type=config_json.get("type", ""),
            nom_output_in_kilowatt=config_json.get("nom_output", 0.0),
            max_output_in_kilowatt=config_json.get("max_output", 0.0),
            min_output_in_kilowatt=config_json.get("min_output", 0.0),
            nom_h2_flow_rate_in_m3_per_h=config_json.get("nom_h2_flow_rate", 0.0),
            faraday_eff=config_json.get("faraday_eff", 0.0),
            i_cell_nom_in_ampere_per_cm2=config_json.get("i_cell_nom", 0.0),
            ramp_up_rate_in_percent_per_s=config_json.get("ramp_up_rate", 0.0),
            ramp_down_rate_in_percent_per_s=config_json.get("ramp_down_rate", 0.0),
        )
        return config


class FuelCellControllerConfigTablePath:
    """From `FuelCellControllerConfig` in `hisim/components/controller_l1_fuel_cell.py`."""

    @staticmethod
    def read_config(fuel_cell_name):
        """Opens the according JSON-file, based on the fuel_cell_name."""

        config_file = Path(utils.HISIMPATH["inputs"]) / "fuel_cell_manufacturer_config.json"
        with open(config_file, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return data.get("Fuel Cell variants", {}).get(fuel_cell_name, {})

    @classmethod
    def control_fuel_cell(
        cls,
        fuel_cell_name: str,
        component_id: Optional[ComponentID] = None,
    ) -> Any:
        """Initializes the config variables based on the JSON-file."""

        if component_id is None:
            component_id = ComponentID(name="FuelCellController")
        config_json = cls.read_config(fuel_cell_name)

        config = FuelCellControllerConfig(
            component_id=component_id,  # config_json.get("name", "")
            nom_output=config_json.get("nom_output", 0.0),
            min_output=config_json.get("min_output", 0.0),
            max_output=config_json.get("max_output", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            warm_start_time=config_json.get("warm_start_time", 0.0),
            cold_start_time=config_json.get("cold_start_time", 0.0),
        )
        return config


class XTPControllerConfigTablePath:
    """From `XTPControllerConfig` in `hisim/components/controller_l2_xtp_fuel_cell_ems.py`.

    This was that class's only factory, which is why removing it leaves the class without a
    default builder rather than with one fewer.
    """

    @staticmethod
    def read_config(fuel_cell_name: str) -> dict[str, Any]:
        """Read config."""
        config_file = Path(utils.HISIMPATH["inputs"]) / "fuel_cell_manufacturer_config.json"
        with config_file.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return cast(dict[str, Any], data.get("Fuel Cell variants", {}).get(fuel_cell_name, {}))

    @classmethod
    def control_fuel_cell(
        cls,
        fuel_cell_name: str,
        operation_mode: "XtpOperationMode",
        component_id: Optional[ComponentID] = None,
    ) -> "XTPControllerConfig":
        """Sets the according parameters for the chosen fuel cell."""
        if component_id is None:
            component_id = ComponentID(name="L2XTPController")
        config_json = cls.read_config(fuel_cell_name)

        config = XTPControllerConfig(
            component_id=component_id,  # config_json.get("name", "")
            nom_output=config_json.get("nom_output", 0.0),
            min_output=config_json.get("min_output", 0.0),
            max_output=config_json.get("max_output", 0.0),
            standby_load=config_json.get("standby_load", 0.0),
            operation_mode=operation_mode,
        )
        return config
