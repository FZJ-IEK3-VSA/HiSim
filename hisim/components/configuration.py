from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim.component import ConfigBase


@dataclass_json
@dataclass
class WarmWaterStorageConfig(ConfigBase):
    name: str
    tank_diameter: float  # [m]
    tank_height: float  # [m]
    tank_start_temperature: float  # [°C]
    temperature_difference: float  # [°C]
    tank_u_value: float  # [W/m^2*K]
    slice_height_minimum: float  # [m]

    @classmethod
    def get_default_config(cls):
        """Gets a default config."""
        return WarmWaterStorageConfig(
            name="WarmWaterStorage",
            tank_diameter=1,  # 0.9534        # [m]
            tank_height=2,  # 3.15              # [m]
            tank_start_temperature=65,  # [°C]
            temperature_difference=0.3,  # [°C]
            tank_u_value=0,  # 0.35                 # [W/m^2*K]
            slice_height_minimum=0.05,  # [m]
        )


class CHPControllerConfig:
    """
    The CHP controller is used to implement an on and off hysteresis
    Decide if its heat- or electricity-led

    Two temperature sensors in the tank are giving the needed information. They can be set at a height percentage in the tank. =% is the top, 100 % s the bottom of the tank.
    If the T at the upper sensor is below temperature_switch_on the chp will run until the lower sensor is above temperature_switch_off.
    A minimum runtime in minutes can be defined for the chp.

    If the chp is electric-led, ths is not needed and the electricity demand is provided directly to the chp
    """

    method_of_operation = "heat"
    temperature_switch_on = 60  # [°C]
    temperature_switch_off = 65  # [°C]

    # in steps of 20 % [0, 20, 40 ,60, 80, 100]
    heights_in_tank = [0, 20, 40, 60, 80, 100]
    height_upper_sensor = 20  # [%]
    height_lower_sensor = 60  # [%]

    minimum_runtime_minutes = 4000  # [min]


class GasHeaterConfig:
    is_modulating = True
    P_th_min = 1_000  # [W]
    P_th_max = 12_000  # [W]
    eff_th_min = 0.60  # [-]
    eff_th_max = 0.90  # [-]
    delta_T = 25
    mass_flow_max = P_th_max / (4180 * delta_T)  # kg/s ## -> ~0.07
    temperature_max = 80  # [°C]


class GasControllerConfig:
    """
    This controller works like the CHP controller, but switches on later so the CHP is used more often.
    Gas heater is used as a backup if the CHP power is not high enough.
    If the minimum_runtime is smaller than the timestep, the minimum_runtime is 1 timestep --> generic_gas_heater.py
    """

    temperature_switch_on = 55  # [°C]
    temperature_switch_off = 70  # [°C]

    # in steps of 20 % [0, 20, 40 ,60, 80, 100]
    height_upper_sensor = 20  # [%]
    height_lower_sensor = 80  # [%]

    # minimal timestep is minute
    minimum_runtime_minutes = 7000  # [min]


class LoadConfig:
    # massflow_load_minute = 2.5          # [kg/min]
    # massflow_load = massflow_load_minute / 60   # [kg/s]

    possible_massflows_load = [0.1, 0.2, 0.3, 0.4]  # [kg/s]
    delta_T = 20

    # the returnflow shows if there was enough energy in the water
    # -> use in load! Not in storage, there the water from WW is included
    temperature_returnflow_minimum = 30  # [°C]

    kWh_per_year = 20_201
    demand_factor = kWh_per_year / 1000


class ElectricityDemandConfig:
    kWh_per_year = 6000
    demand_factor = kWh_per_year / 1000


class HouseholdWarmWaterDemandConfig:
    freshwater_temperature = 10  # [°C]
    ww_temperature_demand = 45  # [°C]
    # german --> Grädigkeit
    # difference between T1_in and T2_out
    temperature_difference_hot = 5  # [°C]
    temperature_difference_cold = 6  # [°C]

    heat_exchanger_losses = 0

    kWh_per_year = 2000
    demand_factor = kWh_per_year / 1000


class HydrogenStorageConfig:
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


class PVConfig:
    peak_power = 20_000  # [W]


@dataclass_json
@dataclass
class ExtendedControllerConfig(ConfigBase):

    name: str
    # Active Components
    chp: bool
    gas_heater: bool
    electrolyzer: bool
    # electrolyzer: bool

    # power mode chp
    # chp_mode: str
    chp_mode: str
    chp_power_states_possible: int
    maximum_autarky: bool

    @classmethod
    def get_default_config(cls):
        """Gets a default ExtendedControllerConfig."""
        return ExtendedControllerConfig(
            name="Example Component",
            chp=True,
            gas_heater=True,
            electrolyzer=True,
            # electrolyzer = False,
            # power mode chp,
            # chp_mode = "heat",
            chp_mode="power",
            chp_power_states_possible=10,
            maximum_autarky=False,
        )


class PhysicsConfig:
    water_density = 1000  # [kg/m^3]
    water_specific_heat_capacity_in_joule_per_kilogram_per_kelvin = 4_180  # J/kgK

    # Schmidt 2020: Wasserstofftechnik  S.170ff
    # fuel value H2:    10.782 MJ/m³    (S.172)
    # density H2:       0.08989 kg/m³   (S. 23) -> standard conditions
    hydrogen_density = 0.08989  # [kg/m³]
    hydrogen_specific_volume = 1 / hydrogen_density  # [m^3/kg]
    hydrogen_specific_fuel_value_per_m_3 = 10.782 * 10**6  # [J/m³]
    hydrogen_specific_fuel_value_per_kg = (
        hydrogen_specific_fuel_value_per_m_3 / hydrogen_density
    )  # [J/kg]

    # Schmidt 2020: Wasserstofftechnik  S.170ff
    # fuel value Methan:    35.894 MJ/m³    (S.172)
    # density Methan:       0.71750 kg/m³   (S. 23) -> standard conditions
    natural_gas_density = 0.71750  # [kg/m³]
    natural_gas_specific_volume = 1 / hydrogen_density  # [m^3/kg]
    natural_gas_specific_fuel_value_per_m_3 = 35.894 * 10**6  # [J/m³]
    natural_gas_specific_fuel_value_per_kg = (
        natural_gas_specific_fuel_value_per_m_3 / natural_gas_density
    )  # [J/kg]
