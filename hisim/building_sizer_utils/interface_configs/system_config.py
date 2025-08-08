"""Energy system config module for building sizer (without UTSP but with cluster)."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.loadtypes import HeatingSystems


@dataclass_json
@dataclass
class EnergySystemConfig:

    """Defines the configuration and sizing of all energy system components considered in a household."""

    # #: decision on the consideration of smart control for EV charging and heat pump
    # surplus_control_considered: bool = False
    # #: decision on the consideration of Photovoltaic Panel
    # pv_included: bool = False
    # #: peak power of the considered Photovoltaic Panel in Wp
    # pv_peak_power: Optional[float] = 8e3
    # #: decision on the consideration of Smart Control of Washing Machines, Dish Washers and Dryers
    # smart_devices_included: bool = False
    # #: decision on the consideration of a buffer storage for heating
    # buffer_included: bool = False
    # #: volume of the considered buffer storage in multiples of the default size
    # buffer_volume: Optional[float] = 1.0  # in multiples of default
    # #: decision on the consideration of battery
    # battery_included: bool = False
    # #: capacity of the considered battery in kWh
    # battery_capacity: Optional[float] = 10.0  # in kWh
    # #: decision on the consideration of heat pump
    # heatpump_included: bool = False
    # #: maximal power of the considered heat pump in multiples of the default
    # heatpump_power: Optional[float] = 1.0  # in multiples of default
    # #: decision on the consideration of combined heat and power - in this case a fuel cell
    # chp_included: bool = False
    # #: maximal power of the considered CHP in multiples of the default
    # chp_power: Optional[float] = 0.5
    # #: decision on the consideration of fuel cell + hydrogen storage + electrolyzer
    # hydrogen_setup_included: bool = False
    # #: maximal power of the considered fuel cell in kW (heat and electricity combined)
    # fuel_cell_power: Optional[float] = 0.5
    # #: size of the hydrogen storage in kg hydrogen
    # h2_storage_size: Optional[float] = 100
    # #: maximal power of the electroylzer in Watt
    # electrolyzer_power: Optional[float] = 0.5
    # #: decision on the consideration of an electriv vehicle
    # ev_included: bool = False
    # #: choice of charging station related to the options available in LoadProfileGenerator
    # charging_station: JsonReference = field(
    #     default_factory=lambda: ChargingStationSets.Charging_At_Home_with_03_7_kW  # type: ignore
    # )
    heating_system: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    share_of_maximum_pv_potential: float = 1.0
    use_battery_and_ems: bool = True

    @classmethod
    def get_default_config_for_energy_system_gas(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.GAS_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_oil(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.OIL_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_heatpump(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.HEAT_PUMP, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_district_heating(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.DISTRICT_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_pellet_heating(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.PELLET_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_wood_chip_heating(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.WOOD_CHIP_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_hydrogen(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.HYDROGEN_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_electric(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.ELECTRIC_HEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_gas_solar_thermal(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.GAS_SOLAR_THERMAL, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_heatpump_solar_thermal(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.HEAT_PUMP_SOLAR_THERMAL, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config
