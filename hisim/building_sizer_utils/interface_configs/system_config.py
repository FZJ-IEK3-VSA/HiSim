"""Energy system config module for building sizer (without UTSP but with cluster)."""

from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.loadtypes import HeatingSystems, ComponentType


@dataclass_json
@dataclass
class EnergySystemConfig:

    """Defines the configuration and sizing of all energy system components considered in a household."""

    heating_system: HeatingSystems = HeatingSystems.DISTRICT_HEATING
    heat_distribution_system: ComponentType = ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING
    share_of_maximum_pv_potential: float = 1.0
    use_battery_and_ems: bool = True

    @classmethod
    def get_default_config_for_energy_system_gas(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.GAS_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_oil(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.OIL_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_heatpump(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.HEAT_PUMP, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_district_heating(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.DISTRICT_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_pellet_heating(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.PELLET_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_wood_chip_heating(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.WOOD_CHIP_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_hydrogen(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.HYDROGEN_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_electric(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.ELECTRIC_HEATING, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_gas_solar_thermal(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.GAS_SOLAR_THERMAL, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config

    @classmethod
    def get_default_config_for_energy_system_heatpump_solar_thermal(cls):
        """Get default energy system config."""
        energy_system_config = EnergySystemConfig(heating_system=HeatingSystems.HEAT_PUMP_SOLAR_THERMAL, heat_distribution_system=ComponentType.HEAT_DISTRIBUTION_SYSTEM_FLOORHEATING, share_of_maximum_pv_potential=1.0, use_battery_and_ems=True)
        return energy_system_config
