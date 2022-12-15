""" For setting the configuration of the household. """
# clean
from dataclasses import dataclass, field
from typing import Optional

from dataclasses_json import dataclass_json
from utspclient.helpers.lpgdata import ChargingStationSets
from utspclient.helpers.lpgpythonbindings import JsonReference


@dataclass_json
@dataclass
class SystemConfig:

    """Defines the system config for the modular household."""

    pv_included: bool = True
    pv_peak_power: Optional[float] = 9e4
    smart_devices_included: bool = False
    buffer_included: bool = True
    buffer_volume: Optional[float] = 200  # in liter
    battery_included: bool = True
    battery_capacity: Optional[float] = 1  # in Wh
    heatpump_included: bool = False
    chp_included: bool = False
    chp_power: Optional[float] = 12
    h2_storage_included: bool = True
    h2_storage_size: Optional[float] = 100
    electrolyzer_included: bool = True
    electrolyzer_power: Optional[float] = 5e3
    ev_included: bool = True
    charging_station: JsonReference = field(
        default_factory=lambda: ChargingStationSets.Charging_At_Home_with_03_7_kW  # type: ignore
    )
    utsp_connect: bool = False
    url: str = "http://134.94.131.167:443/api/v1/profilerequest"
    api_key: str = ""
