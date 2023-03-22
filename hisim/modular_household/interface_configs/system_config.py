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

    """Defines the configuration and sizing of all components considered in a modular household."""

    #: decision on the consideration of Photovoltaic Panel
    pv_included: bool = True
    #: peak power of the considered Photovoltaic Panel in Wp
    pv_peak_power: Optional[float] = 1e4
    #: decision on the consideration of Smart Control of Washing Machines, Dish Washers and Dryers
    smart_devices_included: bool = False
    #: decision on the consideration of a buffer storage for heating
    buffer_included: bool = True
    #: volume of the considered buffer storage in multiples of the default size
    buffer_volume: Optional[float] = 1.0  # in multiples of default
    #: decision on the consideration of battery
    battery_included: bool = False
    #: capacity of the considered battery in kWh
    battery_capacity: Optional[float] = 10.0  # in kWh
    #: decision on the consideration of heat pump
    heatpump_included: bool = True
    #: maximal power of the considered heat pump in multiples of the default
    heatpump_power: Optional[float] = 1.0  # in multiples of default
    #: decision on the consideration of combined heat and power - in this case a fuel cell
    chp_included: bool = False
    #: maximal power of the considered fuel cell in kW (heat and electricity combined)
    chp_power: Optional[float] = 12
    #: decision on the consideration of a hydrogen storage
    h2_storage_included: bool = True
    #: size of the hydrogen storage in kg hydrogen
    h2_storage_size: Optional[float] = 100
    #: decision on the consideration of an electrolyzer
    electrolyzer_included: bool = True
    #: maximal power of the electroylzer in Watt
    electrolyzer_power: Optional[float] = 5e3
    #: decision on the consideration of an electriv vehicle
    ev_included: bool = False
    #: choice of charging station related to the options available in LoadProfileGenerator
    charging_station: JsonReference = field(
        default_factory=lambda: ChargingStationSets.Charging_At_Home_with_03_7_kW  # type: ignore
    )
    #: url of the UTSP
    url: str = "http://134.94.131.167:443/api/v1/profilerequest"
    #: passwort to connect to the UTSP
    api_key: str = "limited_OXT60O84N9ITLO1CM9CJ1V393QFKOKCN"
