"""This module contains a simplified buildings component that follows the 1R1C approach.

This means simplest possible modeling, one thermal zone with a thermal capacity and
only one conducting connection to the outside.
"""

# ! author Felix

# Imports

# Generic/Built-in
import importlib
from typing import List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import numpy as np

# Owned
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector
from hisim.components.weather import Weather
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


HULL_PARTS: list[str] = ["floor", "wall", "roof", "windows", "door"]


@dataclass_json
@dataclass
class Building1R1CConfig(cp.ConfigBase):
    """Configuration of the 1R1C building component.

    Attributes:
        building_name (str): If simulating multiple buildings, differentiate them with this. Default: BUI1.
        name (str): The name of the component that shows up in the json reports. Default: Building1R1C.
        target_temperature (float): The temperature that the building is supposed to be kept at (and the
            temperature that it has in the beginning) in °C. Default: 20.0
        solar_gain_reduction_factor (float): A factor with which the solar irradiation that theoretically reaches the
            windows is reduced to calculate the actual solar gains. Includes, f.ex., the window transmission
            coefficient, glare factor (for non-orthogonal incidence), ratio of the window that is the frame,
            and external shading. Default: 0.65 * 0.9 * 0.7 * 0.7 = 0.28665.

        thermal_capacity (float): The thermal capacity of the building in J/K.
        u_values (dict[str, float]): Contains the u-values of the five hull parts: "floor", "wall", "roof", "windows",
            "door", with those strings as keys and the u-values in W/(m² K) as values.
        areas (dict[str, float]): Contains the areas of the five hull parts in m², similar to the u-values.

        air_volume (float): The volume of air in m³ in the building. Gets set automatically.
        air_exchange_rate (float): The rate of air exchange with the outside in h^-1, which fraction of the air is
            exchanged with the outside each hour. Default: 0.5.
        air_heat_cap (float): The heat capacity of air. Unless you are simulating on a different planet, you likely
            do not have a reason to change this. Default: 0.34 Wh/(m³ K)
        
        
    """
    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return Building1R1C.get_full_classname()

    thermal_capacity: float
    u_values: dict[str, float]
    areas: dict[str, float]

    building_name: str
    name: str
    target_temperature: float
    solar_gain_reduction_factor: float

    air_volume: float
    air_exchange_rate: float
    air_heat_cap: float

    def __init__(
        self,
        thermal_capacity: float | str,
        u_values: dict[str, float|None] | list[float|None] | None = None,
        areas: dict[str, float|None] | list[float|None] | None = None,
        building_name: str = "BUI1",
        name: str = "Building1R1C",
        target_temperature: float = 20.0,
        solar_gain_reduction_factor: float = 0.65 * 0.9 * 0.7 * 0.7,
        air_volume: float | None = None,
        air_exchange_rate: float = 0.5,
        # parameters that may or may not be used depending on the ones above
        window_to_wall_ratio: float = 0.2,
        roof_slope: float = 42.0,
        **kwargs):
        r"""The init allows for setting every attribute individually.

        Furthermore, if offers a lot of flexibility to set values differently, as is explained in
        the Args section. If you want more info on the attributes themselves, look into the
        documentation of the class itself (remove the brackets after Building1R1CConfig).

        Args:
            building_name (str): Sets the "building_name" attribute. Default: "BUI1"
            name (str): Sets the "name" attribute. Default: "Building1R1C"
            target_temperature (float): Sets the "target_temperature" attribute. Default: 20.0
            solar_gain_reduction_factor (float): The factor by which the window solar irradiation
                gets reduced to calculate the actual thermal solar gains.
                Default: 0.65 * 0.9 * 0.7 * 0.7 = 0.28665.
            air_volume (float): The total volume of air in the building in m³. If this is not
                given, the volume gets estimated with the kwarg "living_area"*2.8, if provided, 
                or using the wall and floor areas that are guaranteed to exist. Default: None.
            air_exchange_rate (float): The rate at which air is exchanged through ventilation in
                per hour (h^(-1)). Default: 0.5. 
            thermal_capacity (float or str): This sets the "thermal_capacity" attribute. Uni: J/K.
                There are two ways to use this:
                - Provide a float value directly. The attribute then gets set to this.
                - Provide the string key of a heat capacity class. If you do this, you also need
                    to provide a float value to the keyword argument "living_area".<br>
                    The attribute then gets set using Building1R1C.get_heat_cap_by_living_area().
            u_values (dict(str, float) or list(float)): This sets the "u_values" attribute. There
                are a bunch of options to use this. The values are interpreted as W/(m² K).
                - Provide a full dict: For each of the hull components "floor", "wall", "roof,
                    "windows", "door" as keys, provide a u_value as a float.
                - Provide a partially filled dict: If you leave "door" empty, it will be set to 1.0.
                - Provide a full list: You can provide a list and the values will be interpreted
                    in this order: "floor", "wall", "roof, "windows", "door". Once again, 
                    you can leave out the door (the last one) and it will be set to 1.0.
                - Leave empty or set to None: If you do this, you can alternatively set each
                    u_value individually by providing the kwargs: "floor_u_value" etc.
            areas (dict(str, float) or list(float)): This sets the "areas" attribute in m². There
                are almost the same options to use this as for the u_values parameter, but:
                - If you leave "door" empty, it will be set to 2.0.
                - You can additionally leave "window" empty, and it will be calculated using the
                    "window_to_wall_ratio" parameter and the wall area.
                - You can also leave "roof" empty. It will then be calculated based on the floor
                    area and a roof slope. You can provide the slope in degrees via the parameter.
                - If you choose to provide a list, be aware that you can't leave out an earlier
                    value and provide a later one (f.ex. leave out roof but provide door). This
                    would mess up the interpretation. In this case, you have to actively provide
                    "None" for the value you're leaving out.
            window_to_wall_ratio (float): If you don't provide a value for the window area, this
                parameter is used to calculate it based on the wall area. Otherwise, it is
                ignored. Default: 0.2 = 20%.
            roof_slope (float): If you don't provide a value for the roof area, it gets calculated
                using this parameter. Otherwise, this parameter is ignored. Default: 42.0.
        """
        # set the easy values
        self.building_name = building_name
        self.name = name
        self.target_temperature = target_temperature
        self.solar_gain_reduction_factor = solar_gain_reduction_factor
        self.set_thermal_capacity(thermal_capacity, kwargs)
        self.air_exchange_rate = air_exchange_rate  # per hour
        self.air_heat_cap = 0.34  # heat capacity of air in Wh/(m³ K)
        # ----------------------------------
        # setting u_values and areas:
        self.u_values = {}
        self.areas = {}
        if not isinstance(u_values, (dict, list)) and u_values is not None:
            raise TypeError(f"Type of parameter 'u_values' does not match any legal option: {type(u_values)}")
        if not isinstance(areas, (dict, list)) and areas is not None:
            raise TypeError(f"Type of parameter 'areas' does not match any legal option: {type(areas)}")
        # if lists were provided, fill missing values and turn them into dicts
        if isinstance(u_values, list):
            if len(u_values) == 4: u_values.append(None)
            u_values = {hp: u_values[i] for i, hp in enumerate(HULL_PARTS)}
        if isinstance(areas, list):
            while len(areas) < 5: areas.append(None)
            areas = {hp: areas[i] for i, hp in enumerate(HULL_PARTS)}
        # if None were provided, try to make dicts out of the kwargs (that should exist)
        if u_values is None:
            u_values = self.fill_dict_from_kwargs("u_value", kwargs)
        if areas is None:
            areas = self.fill_dict_from_kwargs("area", kwargs)
        # fill the dicts with standard values
        if "door" not in u_values or u_values["door"] is None:
            u_values["door"] = 1.0
        if "door" not in areas or areas["door"] is None:
            areas["door"] = 2.0
        if "windows" not in areas or areas["windows"] is None:
            areas["windows"] = areas["wall"] * window_to_wall_ratio  # type: ignore 
        if "roof"  not in areas or areas["roof"] is None:
            areas["roof"] = areas["floor"] / np.cos(np.radians(roof_slope))
        # finally: set u_values and areas. At this point, we have good dicts, so this is easy
        for i, hp in enumerate(HULL_PARTS):
            self.u_values[hp] = (float)(u_values[hp])  # type: ignore # the value is fine, see above
            self.areas[hp] = (float)(areas[hp])  # type: ignore # the value is fine, see above
        # ----------------------------------------------------------------------------------------
        # set air volume after areas cause it's easier if the areas have been dealt with
        if air_volume is not None: self.air_volume = air_volume
        else: self.set_air_volume(areas, kwargs)

    def set_thermal_capacity(self, thermal_capacity, kwargs):
        """Helper function of __init__ to set the thermal capacity in J/K based on the different input options."""
        if isinstance(thermal_capacity, float) or isinstance(thermal_capacity, int):
            self.thermal_capacity = (float)(thermal_capacity)
        elif isinstance(thermal_capacity, str):
            if thermal_capacity not in Building1R1C.get_heat_capacity_classes():
                raise KeyError(f"The provided heat capacity class does not match any existing classes: {thermal_capacity}")
            if "living_area" not in kwargs:
                raise TypeError("Heat capacity class was provided but no living_area! Please provide a value for the living_area kwarg!")
            self.thermal_capacity = Building1R1C.get_heat_cap_by_living_area((float)(kwargs["living_area"]), thermal_capacity)
        else:
            raise TypeError(f"Value of parameter 'thermal_capacity' is neither float (nor int) nor str: {thermal_capacity}, {type(thermal_capacity)}")

    def set_air_volume(self, areas, kwargs):
        """Helper function of __init__ to set the volume of air in the building."""
        if "living_area" in kwargs:
            self.air_volume = kwargs["living_area"] * 2.8
        else: # assume square homes
            circumference = 4 * np.sqrt(areas["floor"])
            height = areas["wall"] / circumference
            self.air_volume = areas["floor"] * height * 0.8  # subtract walls etc.

    def fill_dict_from_kwargs(self, mode: str, kwargs: dict):
        """Helper function to interpret kwargs for u_value and area in __init__.

        Args:
            mode (str): Which type of value to fill: "u_value" or "area".
            kwargs (dict): The kwargs originally passed to __init__.

        Returns:
            dict: The completed dictionary.
        """
        result: dict[str, float|None] = {}
        for hp in HULL_PARTS:
            if f"{hp}_{mode}" in kwargs:
                result[hp] = (float)(kwargs[f"{hp}_{mode}"])
            elif f"{hp}_{mode}" in ["door_u_value", "door_area", "roof_area", "window_area"]:
                result[hp] = None
            else: # there is no kwarg and no standard value can be set
                raise TypeError(f"Building1R1CConfig __init__: '{mode}s' is None, but no value "
                                "was provided for necessary kwarg '{hp}_{mode}'")
        return result

# ================================================================================================
# ===== The component class itself ===============================================================
# ================================================================================================

class Building1R1C(cp.Component):
    r"""This component models a building with one thermal zone and one thermal connection to the outside (1R1C approach).

    It has a single thermal capacity and a single heat transfer coefficient to the outside.\\
    It can receive thermal power from a number of different sources and uses the ambient
    temperature as a boundary condition for the heat loss to the outside.\\
    The model further includes solar gain through windows.

    The outputs are the theoretical thermal demand, the internal temperature and the solar gain
    through windows (if solar radiation inputs are used).

    The model was built to be similar to the Building component. However, it lacks the inputs
    ThermalPowerCHP and BuildingTemperatureModifier (from an EMS). It also lacks a lot of outputs
    f.ex. due to those outputs being related to multiple zones (which this model doesn't have).\\
    Also relevant: Due to the models' simplicity, there exists an analytical solution for the heat
    loss calculation, so there are no numerical/truncation errors in this component.

    ### Inputs:
        Only one input is necessary, strictly speaking: TemperatureOutside, which denotes ambient
        temperature. Everything else is optional.<br>

        There are three inputs for thermal power that is supplied internally: ThermalPowerInput,
        ThermalPowerInput, and HeatingByDevices. These three exist for compatibility purposes with
        the other HiSim components.
        - ThermalPowerInput is supposed to be connected to
            heat_distribution_system.HeatDistribution.ThermalPowerDelivered
        - HeatingByResidents is supposed to be connected to
            loadprofilegenerator_utsp_connector.UtspLpgConnector.HeatingByResidents
        - HeatingByDevices is supposed to be connected to
            loadprofilegenerator_utsp_connector.UtspLpgConnector.HeatingByDevices
        But really, it all goes into the same sum anyways, so you can use these however you please.
        <br>

        There are seven additional inputs for the calculation of solar gains through windows. All
        of these are supposed to be connected to the weather.Weather components' outputs of the
        same name. These are: Altitude, Azimuth, ApparentZenith, DirectNormalIrradiance, 
        DirectNormalIrradianceExtra, DiffuseHorizontalIrradiance, and GlobalHorizontalIrradiance.
        If you ignore these, solar gains are simply not included in the simulation.
    
    ### Outputs:
        This component outputs
        - TheoreticalThermalBuildingDemand, a theoretical demand that is supposed to connect to
            both the heat_distribution_system.HeatDistribution and 
            heat_distribution_system.HeatDistributionController inputs of the same name.
        - InternalTemperature, the current temperature of the one internal zone. It is supposed to
            connect to heat_distribution_system.HeatDistribution -> ResidenceTemperatureIndoorAir.
        - SolarGainThroughWindows, the calculated heat gain through windows.

    ### Creating an instance of Building1R1C:
        - documentation to be added ...

    Attributes:
        total_heat_transfer_coefficient (float): The lumped heat transfer coefficient from the mass
            to the outside before correction factors. Gets calculated automatically based on values
            from the config. Unit: W/K.
        tau (float): A time constant that is calculated from thermal capacity / lumped heat transfer
            coefficient. The higher this is, the slower the building cools off. Gets calculated
            automatically from the config. Unit: (J/K) / (W/K) = W s / W = seconds.
        more_to_follow (Any): ...
    """

    # --------------------------------------------------------------------------------------------
    # ----- member variables ---------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    # Inputs: Ambient temperature
    TemperatureOutside = "TemperatureOutside"

    # Inputs -> Heating
    ThermalPowerDelivered = "ThermalPowerDelivered"
    HeatingByResidents = "HeatingByResidents"
    HeatingByDevices = "HeatingByDevices"

    # Inputs -> Solar influences
    Altitude = "Altitude" # also called elevation, optional
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    DirectNormalIrradiance = "DirectNormalIrradiance"
    DirectNormalIrradianceExtra = "DirectNormalIrradianceExtra"
    DiffuseHorizontalIrradiance = "DiffuseHorizontalIrradiance"
    GlobalHorizontalIrradiance = "GlobalHorizontalIrradiance"

    # Outputs
    TheoreticalThermalBuildingDemand = "TheoreticalThermalBuildingDemand"
    InternalTemperature = "InternalTemperature" # TemperatureIndoorAir
    SolarGainThroughWindows = "SolarGainThroughWindows"

    # other member variables
    config: Building1R1CConfig
    my_simulation_parameters: SimulationParameters
    state: float  # the state of the building, namely: current internal temperature
    previous_state: float # whatever the state was before the current time step
    input_channels: dict[str, cp.ComponentInput] = {}
    output_channels: dict[str, cp.ComponentOutput] = {}
    total_heat_transfer_coefficient: float
    tau: float
    # ... other stuff from the superclass ...

    # --------------------------------------------------------------------------------------------
    # ----- init and init supporters -------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def __init__(
        self, 
        my_simulation_parameters: SimulationParameters,
        config: Building1R1CConfig,
        my_display_config=cp.DisplayConfig()
    ) -> None:
        """I'm very sorry, but you're gonna have to provide a config. Luckily, the config class
        offers quite a bunch of standard configs, so that should be easy enough. Check there!"""
        # set basic attributes
        self.config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.component_name = self.get_component_name()
        # call super init
        super().__init__(name=self.component_name, 
                         my_simulation_parameters=my_simulation_parameters,
                         my_config=config,
                         my_display_config=my_display_config)
        # set a few static values
        self.total_heat_transfer_coefficient = self.get_total_heat_transfer_coefficient()
        self.tau = self.config.thermal_capacity / self.total_heat_transfer_coefficient
        # set state
        self.state = config.target_temperature
        self.previous_state = self.state
        # add inputs and outputs and connect default connections
        self.add_inputs_and_outputs()
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_occupancy())
        self.add_default_connections(self.get_default_connections_from_hds())
        # Temporary cache. Not a reliable attribute! Only for short-term caching!
        self.stsv: cp.SingleTimeStepValues

    def add_inputs_and_outputs(self):
        """Function to encapsulate the adding of input and output channels."""
        # List of all the inputs, loadtypes, units, and whether they are mandatory
        # These are exactly the parameters that self.add_input() needs.
        inputs = [
            ["TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, True],
            ["ThermalPowerDelivered", lt.LoadTypes.HEATING, lt.Units.WATT, False],
            ["HeatingByResidents", lt.LoadTypes.HEATING, lt.Units.WATT, False],
            ["HeatingByDevices", lt.LoadTypes.HEATING, lt.Units.WATT, False],
            ["Altitude", lt.LoadTypes.ANY, lt.Units.DEGREES, False],
            ["Azimuth", lt.LoadTypes.ANY, lt.Units.DEGREES, False],
            ["ApparentZenith", lt.LoadTypes.ANY, lt.Units.DEGREES, False],
            ["DirectNormalIrradiance", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
            ["DirectNormalIrradianceExtra", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
            ["DiffuseHorizontalIrradiance", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
            ["GlobalHorizontalIrradiance", lt.LoadTypes.IRRADIANCE, lt.Units.WATT_PER_SQUARE_METER, False],
        ]
        # The same for the outputs except we don't need the mandatory flag
        outputs = [
            ["TheoreticalThermalBuildingDemand", lt.LoadTypes.HEATING, lt.Units.WATT,
             "The theoretical heat demand to keep the building at the target temperature."],
            ["InternalTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS,
             "The current internal temperature of the building."],
            ["SolarGainThroughWindows", lt.LoadTypes.HEATING, lt.Units.WATT,
             "The total thermal power that is currently gained through solar radiation through windows."],
        ]
        # add the inputs and outputs
        for i in inputs:
            self.input_channels[i[0]] = self.add_input(self.component_name, i[0], i[1], i[2], i[3])
        for o in outputs:
            self.output_channels[o[0]] = self.add_output(self.component_name, o[0], o[1], o[2], o[3])

    def get_default_connections_from_weather(self):
        """Get weather default connnections."""
        weather_classname = Weather.get_classname()
        # define all connections target input name, source classname, source output name
        connections: list[list[str]] = [
            [Building1R1C.Altitude, weather_classname, Weather.Altitude],
            [Building1R1C.Azimuth, weather_classname, Weather.Azimuth],
            [Building1R1C.ApparentZenith, weather_classname, Weather.ApparentZenith],
            [Building1R1C.DirectNormalIrradiance, weather_classname, Weather.DirectNormalIrradiance],
            [Building1R1C.DirectNormalIrradianceExtra, weather_classname, Weather.DirectNormalIrradianceExtra],
            [Building1R1C.DiffuseHorizontalIrradiance, weather_classname, Weather.DiffuseHorizontalIrradiance],
            [Building1R1C.GlobalHorizontalIrradiance, weather_classname, Weather.GlobalHorizontalIrradiance],
            [Building1R1C.TemperatureOutside, weather_classname, Weather.TemperatureOutside]
        ]
        return [cp.ComponentConnection(c[0], c[1], c[2]) for c in connections]
    
    def get_default_connections_from_hds(self):
        """Get heat distribution system default connnections."""
        # define all connections target input name, source classname, source output name
        utsp_classname = UtspLpgConnector.get_classname()
        connections: list[list[str]] = [
            [Building1R1C.HeatingByResidents, utsp_classname, UtspLpgConnector.HeatingByResidents],
            [Building1R1C.HeatingByDevices, utsp_classname, UtspLpgConnector.HeatingByDevices],
        ]
        return [cp.ComponentConnection(c[0], c[1], c[2]) for c in connections]
    
    def get_default_connections_from_occupancy(self):
        # use importlib to avoid circular import error
        hds_module = importlib.import_module("hisim.components.heat_distribution_system")
        hds_class = getattr(hds_module, "HeatDistribution")
        return [
            cp.ComponentConnection(Building1R1C.ThermalPowerDelivered, 
                                   hds_class.get_classname(), 
                                   hds_class.ThermalPowerDelivered)
        ]

    def get_total_heat_transfer_coefficient(self,
        areas: dict[str, float] | None = None, 
        u_values: dict[str, float] | None = None
    ) -> float:
        r"""Returns the total heat transfer coefficient in W / K.
        
        You need to pass two dicts that each contain a value for each of the hull parts "floor",
        "wall", "roof", "windows", and "door". See Building1R1CCofig.__init__ for details.<br>
        If nothing is passed explicitly, the function uses the values stored in the config.
        """
        # preparation
        if areas is None:
            areas = self.config.areas
        if u_values is None:
            u_values = self.config.u_values
        result = 0
        # summation
        for hp in HULL_PARTS:
            if hp == "floor":
                result += areas[hp] * u_values[hp] / 2
            else:
                result += areas[hp] * u_values[hp]
        # add ventilation term and return
        heat_capacity = self.config.air_heat_cap * self.config.air_volume
        result += heat_capacity * self.config.air_exchange_rate  # Unit here: (Wh/K) * (1/h) = W/K
        return result

    # --------------------------------------------------------------------------------------------
    # ----- static methods -----------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    @staticmethod
    def get_heat_cap_by_living_area(living_area: float, cap_class: str = "medium") -> float:
        """Calculate the heat capacity in J/K based on the living area and a capacity class.
        
        The heat capacity classes are taken from ISO 13790, table 12, p.69/70. For more info on
        the classes, see Building1R1C.get_heat_capacity_classes().

        Args:
            living_area (float): The living area of the building in m².
            cap_class (str): One of five heat capacity classes.
        """
        return Building1R1C.get_heat_capacity_classes()[cap_class] * living_area

    @staticmethod
    def get_heat_capacity_classes() -> dict[str, float]:
        """Gets the heat capacity classes from ISO 13790, table 12, p.69/70.

        The five classes are:
            - "very light": 8e4 J / (m² K)
            - "light": 1.1e5 J / (m² K)
            - "medium": 1.65e5 J / (m² K)
            - "heavy": 2.6e5 J / (m² K)
            - "very heavy": 3.7e5 J / (m² K)
        
        These values have to be multiplied by the living area of the building to get the final 
        heat capacity in J / K.
        
        Returns:
            dict(str, float): A dict containing the five heat capacity classes, with a string key
            and a float value with a unit of J / (m² K).
        """
        return {
            "very light": 8e4,
            "light": 1.1e5,
            "medium": 1.65e5,
            "heavy": 2.6e5,
            "very heavy": 3.7e5,
        }

    @staticmethod
    def calc_k_c(tau: float, tdiff: float) -> float:
        """Calculates k_c: An empirical correction factor for the heat losses.

        # Todo: This function still needs to be written. For this, I first need to find a working
        empirical formula. I will do this by comparing my results to the 5R1C model, tabula, and
        the real values I get in my google poll.

        Args:
            tau (float): The building cooldown time constant in seconds. 
                Calculated by dividing: heat capacity / heat transfer coefficient.
            tdiff (float): The temperature difference between inside and outside in Kelvin.
        """
        return 1.0

    # --------------------------------------------------------------------------------------------
    # ----- "i-functions" ------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def i_save_state(self) -> None:
        """Save the current state."""
        self.previous_state = self.state

    def i_restore_state(self) -> None:
        """Restore the previous state."""
        self.state = self.previous_state

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks. Does nothing for this component."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation. Does nothing for this component."""
        pass

    def write_to_report(self) -> List[str]:
        """Write to report."""
        lines = []
        lines.append(f"Building 1R1C model: {self.config.name}")
        lines.append(f"Building name: {self.config.building_name}")
        lines.append(f"U-values: {self.config.u_values}")
        lines.append(f"Areas: {self.config.areas}")
        lines.append(f"Thermal Capacity: {self.config.thermal_capacity}")
        lines.append(f"Air volume: {self.config.air_volume}")
        lines.append(f"Air exchange rate: {self.config.air_exchange_rate}")
        lines.append(f"Air heat capacity: {self.config.air_heat_cap}")
        lines.append(f"Target temperature: {self.config.target_temperature}")
        return lines

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates the component."""
        # preparation
        thermal_sources = [self.ThermalPowerDelivered, self.HeatingByResidents, self.HeatingByDevices]
        solar_sources = [self.Altitude, self.Azimuth, self.ApparentZenith, 
                         self.DirectNormalIrradiance, self.DirectNormalIrradianceExtra,
                         self.DiffuseHorizontalIrradiance, self.GlobalHorizontalIrradiance]
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        # get inputs and cache state. WARNING: the shorthand get_input_value() function relies on
        # stsv being cached to self.stsv - but this is otherwise highly obscure, therefore it gets
        # deleted immediately after use. DO NOT use this at ANY other point in this class!
        self.stsv: cp.SingleTimeStepValues = stsv
        temperature_outside = self.get_input_value(self.TemperatureOutside)
        thermal_power_inputs = {s: self.get_input_value(s) for s in thermal_sources}
        solar_inputs = {s: self.get_input_value(s) for s in solar_sources}
        self.stsv = None  # type: ignore # immediately delete the stsv cache again
        t_internal_previous = self.state

        # do the calculations
        solar_power_input = self.calc_solar_power_input(**solar_inputs)
        thermal_power_input = sum(thermal_power_inputs.values()) + solar_power_input
        k_c = Building1R1C.calc_k_c(self.tau, t_internal_previous - temperature_outside)
        new_internal_temperature = self.calc_new_internal_temperature(
            thermal_power_input, t_internal_previous, temperature_outside, seconds_per_timestep, k_c)
        # this may be weird, but the heat distribution system is lagging one time step,
        # so we use the final temperature here to calc the demand for the next time step,
        # but using the outdoor temperature of the current one... whatever, we make do with what we have
        theoretical_heat_demand = self.calc_theoretical_heat_demand(
            new_internal_temperature, temperature_outside, seconds_per_timestep, k_c)

        # set outputs and new state
        self.state = new_internal_temperature
        stsv.set_output_value(self.output_channels[self.TheoreticalThermalBuildingDemand], 
                              theoretical_heat_demand)
        stsv.set_output_value(self.output_channels[self.InternalTemperature], 
                              new_internal_temperature)
        stsv.set_output_value(self.output_channels[self.SolarGainThroughWindows], 
                              solar_power_input)

    # --------------------------------------------------------------------------------------------
    # ----- helpers ------------------------------------------------------------------------------
    # --------------------------------------------------------------------------------------------

    def get_input_value(self, channel_name: str) -> float:
        """Shorthand method to get an input value from the stsv based on the name.

        WARNING: This relies on stsv being cached! ONLY use this in the very beginning of the
        i_simulate() function, where this is the case!
        """
        return self.stsv.get_input_value(self.input_channels[channel_name])

    def calc_theoretical_heat_demand(
        self,
        t_internal: float,
        t_outside: float,
        seconds_per_timestep: int,
        k_c: float,
        max_heating_rate: float = 1.0,
    ) -> float:
        """Calculates the theoretical heat demand of the building.
        
        For this component, it is the power that would be needed to keep the building at
        the target temperature or heat it up at a rate of up to [max_heating_rate] °C per hour
        if the building is colder than it is supposed to be. This ignores internal and solar gains.

        Args:
            t_internal (float): The internal temperature with which the losses are calculated (in °C).
            t_outside (float): The ambient temperature in °C.
            seconds_per_timestep (int): How many seconds one time step has.
            k_c (float): The empirical heat loss correction coefficient.
            max_heating_rate (float): The maximum heating rate in °C per hour. Default: 1.0.
        """
        # preparation
        cap_th = self.config.thermal_capacity
        h_tr_coeff = self.total_heat_transfer_coefficient * k_c
        # calculation
        p_loss_ambient = h_tr_coeff * (t_internal - t_outside)
        t_loss_ambient = p_loss_ambient / cap_th * seconds_per_timestep
        t_heat_up_max = max_heating_rate / 3600 * seconds_per_timestep
        # temperature is expected to fall below target: Heating
        if (t_internal - t_loss_ambient) < self.config.target_temperature:
            t_diff = self.config.target_temperature - t_internal  # needs to be positive
            p_heating = cap_th * min(t_diff, t_heat_up_max) / seconds_per_timestep  # capacity * needed heating / second
            return p_loss_ambient + p_heating
        # temperature is expected to be above target: No heating. Cooling is not implemented.
        elif (t_internal - t_loss_ambient) >= self.config.target_temperature:
            return 0
        else:
            raise ValueError(f"Parameter 't_internal' is neither smaller nor greater or equal to attribute " \
                             f"'self.config.target_temperature': {t_internal} ?? {self.config.target_temperature}")

    def calc_solar_power_input(self, **kwargs) -> float:
        """Calculates the solar heat gain through windows."""
        solar_power = SolarGainsCalculator.calc_total_solar_gains(
            dhi = kwargs["DiffuseHorizontalIrradiance"],
            dni = kwargs["DirectNormalIrradiance"],
            dni_e = kwargs["DirectNormalIrradianceExtra"],
            z = kwargs["ApparentZenith"],
            a = self.config.areas["windows"],
        )
        import pvlib
        solar_power_pvlib = 0
        for tilt, azimuth in [[0, 0], [0, 90], [0, 180], [0, 270]]:
            solar_power_pvlib += pvlib.irradiance.get_total_irradiance(
                surface_tilt = tilt,
                surface_azimuth = azimuth,
                solar_zenith = kwargs["ApparentZenith"],
                solar_azimuth = kwargs["Azimuth"],
                dhi = kwargs["DiffuseHorizontalIrradiance"],
                dni = kwargs["DirectNormalIrradiance"],
                ghi = kwargs["GlobalHorizontalIrradiance"]
            )["poa_global"]
        return solar_power * self.config.solar_gain_reduction_factor

    def calc_new_internal_temperature(
        self,
        thermal_power_input: float,
        t_internal_previous: float,
        t_outside: float,
        seconds_per_timestep: int,
        k_c: float,
        method: str = "analytical"
    ) -> float:
        """Calculates the new internal temperature.

        Args:
            thermal_power_input (float): The total thermal power that is put into the building in W.
            internal_temperature (float): The internal temperature with which the losses are
                calculated (in °C). If you provide the temperature at the start of the timestep here,
                it's the explicit Euler method. If you provide the final one, it's implicit Euler.
            t_internal_previous (float): The previous internal temperature in °C. This is
                the temperature at the beginning of the time step.
            t_outside (float): The ambient temperature in °C.
            seconds_per_timestep (int): How many seconds one time step has.
            k_c (float): The empirical heat loss correction coefficient.
            method (str): Which calculation method to use. Currently implemented:
                - "analytical": Solves the ODE analytically. Most accurate (no numerical error at
                    all), but also slowest. However, that barely matters, it's still very fast,
                    the test took 2.4 seconds for 1 million iterations.
                - "trapezoidal_rule": Trapezoidal rule. Medium accuracy. ~2x as fast as analytical.
                - "explicit": Explicit Euler method. Low accuracy. Not unconditionally stable (may
                    break for very large time steps). About 15% faster than trapezoidal rule and
                    2.4 times as fast as the analytical solution. 1 million iterations in 1 second.

        Returns:
            float: The new internal temperature at the end of the time step.
        """
        # preparation
        cap_th = self.config.thermal_capacity
        h_tr_coeff = self.total_heat_transfer_coefficient * k_c
        # calculation
        if method == "analytical": # most accurate but slowest
            # Analytical solution of T' = P_heat/c - (T - T_amb) * H/c
            t_equilibrium = thermal_power_input/h_tr_coeff + t_outside
            exp_term = np.exp(-(h_tr_coeff/cap_th) * seconds_per_timestep) 
            return (t_internal_previous - t_equilibrium) * exp_term + t_equilibrium
        elif method == "trapezoidal_rule": # about twice as fast as analytical method
            # Solve this on a piece of paper if you need to understand these calculations:
            # T1 = T0 + (P_heating/c + T_diff * H/c) * dt, with
            # T_diff = T_ambient - (T0 + T1) / 2
            t_gain = thermal_power_input / cap_th * seconds_per_timestep    
            t_from_amb = t_outside * h_tr_coeff / cap_th * seconds_per_timestep
            t0_term = h_tr_coeff * seconds_per_timestep / (cap_th * 2) * t_internal_previous
            factor = 1 + h_tr_coeff * seconds_per_timestep / (cap_th * 2)
            return (t_internal_previous + t_gain + t_from_amb - t0_term) / factor
        elif method == "explicit": # should be slightly faster, only 8 ops
            t_gain = thermal_power_input / cap_th * seconds_per_timestep
            dt_loss_ambient = h_tr_coeff * (t_internal_previous - t_outside) / cap_th
            return t_internal_previous + t_gain - dt_loss_ambient * seconds_per_timestep
        else:
            raise KeyError(f"Calculation method not recognized: {method}")


class SolarGainsCalculator:
    """Class to calculate solar gain. Only offers a few static methods. Main method: calc_total_solar_gains().

    This class calculates the solar irradiation for vertical surfaces, averaged in each direction.
    It uses the simplified Perez Diffuse Irradiance Model with additional manual simplifications.

    Source: "A new simplified Version of the Perez Diffuse Irradiance Model for Tilted Surfaces",
    Perez, Seals, Ineichen, Steward and Menicucci (1987). Solar Energy Vol. 39, No. 3.

    Possible further simplifications here:
    https://github.com/architecture-building-systems/RC_BuildingSimulator/blob/master/rc_simulator/radiation.py
    """

    @staticmethod 
    def calc_total_solar_gains(dhi: float, dni: float, dni_e: float, z: float, a: float) -> float:
        """Calcs the total gains in Watt.
        
        Args:
            dhi (float): Diffuse horizontal irradiation in W/m².
            dni (float): Direct normal irradiation in W/m².
            dni_e (float): Extraterrestrial direct normal irradiance in W/m².
            z (float): Solar zenith angle, i.e. angle between the sun and the zenith, 
                i.e. 90° - solar elevation. Unit: degrees.
            a (float): The total window area in m².
        """
        z = np.radians(z)
        return (SolarGainsCalculator.calc_direct_irrad(dni, z)
                + SolarGainsCalculator.calc_diffuse_irrad(dhi, dni, dni_e, z)
                ) * a

    @staticmethod
    def calc_direct_irrad(dni: float, z: float) -> float:
        # dni * altitude angle factor * factor due to integrating over the azimuth angle
        return max(dni * np.sin(z) / np.pi, 0)

    @staticmethod
    def calc_diffuse_irrad(dhi: float, dni: float, dni_e: float, z: float) -> float:
        r"""Mostly implements eq. 9 from the paper, with additional simplifications.
        
        The simplifications are:
            - We assume all windows are vertical.
            - We average over all azimuth angles (orientations). This justifies using equation 9
                because it reduces the error.
        
        Args:
            dhi (float): Diffuse horizontal irradiation in W/m².
            dni (float): Direct normal irradiation in W/m².
            dni_e (float): Extraterrestrial direct normal irradiance in W/m².
            z (float): Solar zenith angle, i.e. angle between the sun and the zenith, 
                i.e. 90° - solar elevation. Unit: radians.
        """
        if dhi == 0:  # if there is not diffuse radiation, we can skip
            return 0  # this also saves us a headache in the calculation of epsilon down the line
        # Explanation of simplifications:
        # Because of the verticality: s = 90°, cos(s) = 0 and sin(s) = 1.
        # For this reason, the factors (1 + cos(s)) and sin(s) are 1 and can be ignored.
        # The second term:
        # - theta_c is the incidence angle on a tilted plane.
        # - We have cos(theta_c) = cos(horizontal angle) * cos(vertical angle).
        # - Due to the first simplification, we have vertical angle = altitude.
        # - Due to the second, our average area facing any angle is 1/pi.
        # - therefore: cos(theta_c) = cos(altitude) / pi
        # - altitude = 90 - zenith -> cos(altitude) = cos(90 - zenith) = -sin(-zenith) = sin(zenith)
        # - with tan = sin/cos, the entire term simplifies to tan(zenith)/pi
        f1, f2 = SolarGainsCalculator.get_f1_and_f2(dhi, dni, dni_e, z)
        # to avoid explosion, z is clipped to 85° max - that is roughly 1.4835 in radiants
        return max(dhi * (0.5*(1-f1) + (np.tan(min(1.4835, z)))/np.pi*f1 + f2), 0)

    @staticmethod
    def get_f1_and_f2(dhi: float, dni: float, dni_e: float, z: float):
        """Get the F_1 and F_2 parameters for calc_diffuse_irrad().

        Args:
            dhi (float): Diffuse horizontal irradiation in W/m².
            dni (float): Direct normal irradiation in W/m².
            dni_e (float): The extraterrestrial direct normal irradiation in W/m².
            z (float): Solar zenith angle in radians.
        """
        # calc epsilon and delta
        epsilon = SolarGainsCalculator.calc_epsilon(dhi, dni, z)
        delta = SolarGainsCalculator.calc_delta(dhi, dni_e, z)
        # get the f_ij parameters
        f11, f12, f13, f21, f22, f23 = SolarGainsCalculator.get_fij_params(epsilon)
        # final part
        f1 = f11 + f12 * delta + f13 * z
        f2 = f21 + f22 * delta + f23 * z
        return max(0, f1), f2

    @staticmethod
    def calc_epsilon(dhi: float, dni: float, z: float) -> float:
        """Calc epsilon (sky clearness) for Perez irradiance model.

        Args:
            dhi (float): Diffuse horizontal irradiation in W/m².
            dni (float): Direct normal irradiation in W/m².
            z (float): Solar zenith angle in radians.
        """
        a = (dhi + dni) / dhi  # dhi = 0 is caught in the top-level function
        b = 1.041 * z**3
        return (a + b) / (1 + b)
    
    @staticmethod
    def calc_delta(dhi: float, dni_e: float, z: float) -> float:
        """Calc delta (sky brightness) for Perez irradiance model.

        Args:
            dhi (float): Diffuse horizontal irradiation in W/m².
            dni_e (float): The extraterrestrial direct normal irradiation in W/m².
            z (float): Solar zenith angle in radians.
        """
        # to avoid div by zero, z is clipped to 85° max - that is roughly 1.4835 in radiants
        m_a = 1 / np.cos(min(1.4835, z))  # very simplified formula for the air mass
        return dhi * m_a / dni_e

    @staticmethod
    def get_fij_params(epsilon) -> list[float]:
        """Gets the f_ij parameters for get_f1_and_f2. Returns them as [f11, f12, f13, f21, f22, f23]."""
        table: dict[float|None, list[float]]= {
            1.056: [0.041, 0.621, -0.105, -0.040, 0.074, -0.031],
            1.253: [0.054, 0.966, -0.166, -0.016, 0.114, -0.045],
            1.586: [0.227, 0.866, -0.250, 0.069, -0.002, -0.062],
            2.134: [0.486, 0.670, -0.373, 0.148, -0.137, -0.056],
            3.230: [0.819, 0.106, -0.465, 0.268, -0.497, -0.029],
            5.980: [1.020, -0.260, -0.514, 0.306, -0.804, 0.046],
            10.08: [1.009, -0.708, -0.433, 0.287, -1.286, 0.166],
            None:  [0.936, -1.121, -0.352, 0.226, -2.449, 0.383],
        }
        for val in table:
            if val is None or val <= epsilon:
                return table[val]
        raise IndexError(f"Something went really wrong, this should not have been reachable.")
