"""Solar thermal system for DHW."""

from copy import deepcopy
import datetime
from typing import Any, List
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd
from oemof.thermal.solar_thermal_collector import flat_plate_precalc

from hisim.component import (
    CapexCostDataClass,
    Component,
    ComponentConnection,
    ComponentInput,
    ComponentOutput,
    Coordinates,
    OpexCostDataClass,
    SingleTimeStepValues,
    DisplayConfig,
)
from hisim import loadtypes, log, utils
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig, PhysicsConfig
from hisim.components.simple_water_storage import SimpleDHWStorage
from hisim.components.weather import Weather
from hisim.simulationparameters import SimulationParameters
from hisim.component import ConfigBase
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass

__authors__ = "Kristina Dabrock"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Kristina Dabrock"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Kristina Dabrock"
__email__ = "k.dabrock@fz-juelich.de"
__status__ = "development"


@dataclass_json
@dataclass
class SolarThermalSystemConfig(ConfigBase):
    """Configuration of the SolarThermalSystem component."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SolarThermalSystem.get_full_classname()

    building_name: str
    name: str
    coordinates: Coordinates

    # Module configuration
    azimuth: float
    tilt: float
    area_m2: float  # m2
    eta_0: float
    a_1_w_m2_k: float  # W/(m2*K)
    a_2_w_m2_k: float  # W/(m2*K2)
    delta_temperature_n_k = 10  # K

    # Whether on old solar pump or a new one is used
    old_solar_pump: bool

    # techno-economic parameters
    co2_footprint_kg_co2eq: float
    investment_cost_eur: float
    lifetime_y: float
    maintenance_cost_eur_per_y: float

    # Weight of component, defines hierachy in control. The default is 1.
    source_weight: int

    @classmethod
    def get_default_solar_thermal_system(
        cls,
        building_name: str = "BUI1",
        coordinates: Coordinates = Coordinates(latitude=50.78, longitude=6.08),
        azimuth: float = 180.0,
        tilt: float = 30.0,
        area_m2: float = 1.5,
        eta_0: float = 0.78,
        a_1_w_m2_k: float = 3.2,  # W/(m2*K)
        a_2_w_m2_k: float = 0.015,  # W/(m2*K2)
        old_solar_pump: bool = False,
        source_weight: int = 1,
    ) -> "SolarThermalSystemConfig":
        """Gets a default SolarThermalSystem."""
        return SolarThermalSystemConfig(
            building_name=building_name,
            coordinates=coordinates,
            name="SolarThermalSystem",
            azimuth=azimuth,
            tilt=tilt,
            area_m2=area_m2,  # m2
            # These values are taken from the Excel sheet that can be downloaded from
            # http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters
            # Values were determined by changing eta_0, a_1, and a_2 so that the curve
            # fits with the typical flat plat curve
            eta_0=eta_0,
            a_1_w_m2_k=a_1_w_m2_k,  # W/(m2*K)
            a_2_w_m2_k=a_2_w_m2_k,  # W/(m2*K2)
            old_solar_pump=old_solar_pump,
            co2_footprint_kg_co2eq=(
                area_m2 * (240.1 / 2.03)  # material solar collector
                + area_m2 * (34.74 / 2.03) + 108.28  # material external support
                + area_m2 * (8.64 / 2.03)  # manufacturing solar collector
                + area_m2 * (2.53 / 2.03)  # manufacturing external support
                + area_m2 * (4.39 * 0.56 / 2.03)),  # 56% (share of mass of solar collector+support/total, i.e., including storage)
                                                    # of transport phase 1 https://www.tandfonline.com/doi/full/10.1080/19397030903362869#d1e1255
            investment_cost_eur=area_m2 * 797,  # Flachkollektoren
                                                # https://www.co2online.de/modernisieren-und-bauen/solarthermie/solarthermie-preise-kosten-amortisation/
            maintenance_cost_eur_per_y=100,  # https://www.co2online.de/modernisieren-und-bauen/solarthermie/solarthermie-preise-kosten-amortisation/
            lifetime_y=20,  # https://www.tandfonline.com/doi/full/10.1080/19397030903362869#d1e1712
            source_weight=source_weight,
        )


class SolarThermalSystem(Component):
    """Solar thermal system.

    This class represents a solar thermal system that can be used
    for warm water and space heating.
    """

    # Inputs
    TemperatureOutsideDegC = "TemperatureOutsideDegC"
    DiffuseHorizontalIrradianceWM2 = "DiffuseHorizontalIrradianceWM2"
    GlobalHorizontalIrradianceWM2 = "GlobalHorizontalIrradianceWM2"
    Azimuth = "Azimuth"
    ApparentZenith = "ApparentZenith"
    TemperatureCollectorInletDegC = "TemperatureCollectorInletDegC"
    ControlSignal = "ControlSignal"

    # Outputs
    ThermalPowerOutput = "ThermalPowerOutput"
    ThermalEnergyOutput = "ThermalEnergyOutput"
    RequiredWaterMassFlowOutput = "RequiredWaterMassFlowOutput"
    WaterMassFlowOutput = "WaterMassFlowOutput"
    WaterTemperatureOutput = "WaterTemperatureOutput"
    ElectricityConsumptionOutput = "ElectricityConsumptionOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SolarThermalSystemConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Constructs all the neccessary attributes."""
        self.componentnameconfig = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # If a component requires states, this can be implemented here.
        self.state = SolarThermalSystemState()
        self.previous_state = deepcopy(self.state)
        # Initialized variables
        self.factor = 1.0
        self.precalc_data_for_all_timesteps_data: List = []
        self.precalc_data_for_all_timesteps_output: List = []
        self.cache_filepath: str

        # Add inputs
        self.t_out_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutsideDegC,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        self.dhi_channel: ComponentInput = self.add_input(
            self.component_name,
            self.DiffuseHorizontalIrradianceWM2,
            loadtypes.LoadTypes.IRRADIANCE,
            loadtypes.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.ghi_channel: ComponentInput = self.add_input(
            self.component_name,
            self.GlobalHorizontalIrradianceWM2,
            loadtypes.LoadTypes.IRRADIANCE,
            loadtypes.Units.WATT_PER_SQUARE_METER,
            True,
        )

        self.azimuth_channel: ComponentInput = self.add_input(
            self.component_name,
            self.Azimuth,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.DEGREES,
            True,
        )

        self.apparent_zenith_channel: ComponentInput = self.add_input(
            self.component_name,
            self.ApparentZenith,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.DEGREES,
            True,
        )

        self.water_temperature_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureCollectorInletDegC,
            loadtypes.LoadTypes.TEMPERATURE,
            loadtypes.Units.CELSIUS,
            True,
        )

        self.control_signal_channel: ComponentInput = self.add_input(
            self.component_name,
            SolarThermalSystem.ControlSignal,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.BINARY,
            True,
        )

        # Add outputs
        self.thermal_power_w_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerOutput,
            load_type=loadtypes.LoadTypes.HEATING,
            unit=loadtypes.Units.WATT,
            postprocessing_flag=[loadtypes.InandOutputType.WATER_HEATING],
            output_description="Thermal power output [W]",
        )

        self.thermal_energy_wh_output_channel: ComponentOutput = (
            self.add_output(
                object_name=self.component_name,
                field_name=self.ThermalEnergyOutput,
                load_type=loadtypes.LoadTypes.HEATING,
                unit=loadtypes.Units.WATT_HOUR,
                output_description="Thermal energy output [Wh]",
                postprocessing_flag=[
                    loadtypes.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL
                ],
            )
        )

        self.water_mass_flow_kg_s_output_channel: ComponentOutput = (
            self.add_output(
                object_name=self.component_name,
                field_name=self.WaterMassFlowOutput,
                load_type=loadtypes.LoadTypes.WATER,
                unit=loadtypes.Units.KG_PER_SEC,
                output_description="Mass flow of heat transfer liquid [kg/s]",
            )
        )
        self.required_water_mass_flow_kg_s_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.RequiredWaterMassFlowOutput,
            load_type=loadtypes.LoadTypes.WATER,
            unit=loadtypes.Units.KG_PER_SEC,
            output_description="The required mass flow of heat transfer liquid [kg/s] for achieving target temperature rise",
        )

        self.water_temperature_deg_c_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.WaterTemperatureOutput,
            load_type=loadtypes.LoadTypes.WATER,
            unit=loadtypes.Units.CELSIUS,
            output_description="Output temperature of heat transfer liquid [°C]",
        )
        self.electricity_consumption_output_channel: ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityConsumptionOutput,
            load_type=loadtypes.LoadTypes.ELECTRICITY,
            unit=loadtypes.Units.WATT,
            output_description="Electricity consumption of the solar pump.",
            postprocessing_flag=[
                loadtypes.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
            ],
        )

        self.add_default_connections(
            self.get_default_connections_from_simple_hot_water_storage()
        )
        self.add_default_connections(
            self.get_default_connections_from_weather()
        )
        self.add_default_connections(
            self.get_default_connections_from_controller()
        )

    @staticmethod
    def get_cost_capex(config: SolarThermalSystemConfig, simulation_parameters: SimulationParameters) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.investment_cost_eur / config.lifetime_y) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_footprint_kg_co2eq / config.lifetime_y) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_cost_eur,
            device_co2_footprint_in_kg=config.co2_footprint_kg_co2eq,
            lifetime_in_years=config.lifetime_y,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.SOLAR_THERMAL
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX."""
        dhw_consumption_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.ElectricityConsumptionOutput
                and output.unit == loadtypes.Units.WATT
            ):
                dhw_consumption_in_kilowatt_hour = round(
                    sum(postprocessing_results.iloc[:, index])
                    * self.my_simulation_parameters.seconds_per_timestep
                    / 3.6e6,
                    1,
                )
                break

        emissions_and_cost_factors = EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
            self.my_simulation_parameters.year
        )
        assert dhw_consumption_in_kilowatt_hour is not None
        total_consumption_in_kilowatt_hour = dhw_consumption_in_kilowatt_hour

        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=total_consumption_in_kilowatt_hour * emissions_and_cost_factors.electricity_costs_in_euro_per_kwh,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=total_consumption_in_kilowatt_hour * emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh,
            total_consumption_in_kwh=total_consumption_in_kilowatt_hour,
            consumption_for_domestic_hot_water_in_kwh=dhw_consumption_in_kilowatt_hour,
            loadtype=loadtypes.LoadTypes.WARM_WATER,
            kpi_tag=KpiTagEnumClass.SOLAR_THERMAL
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        list_of_kpi_entries: List[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        capex_dataclass = self.get_cost_capex(
            self.config, self.my_simulation_parameters
        )
        dhw_thermal_energy_delivered_in_kilowatt_hour = None
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if (
                    output.field_name == self.ThermalEnergyOutput
                    and output.unit == loadtypes.Units.WATT_HOUR
                ):
                    dhw_thermal_energy_delivered_in_kilowatt_hour = round(
                        sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                    )

        assert dhw_thermal_energy_delivered_in_kilowatt_hour is not None
        total_thermal_energy_delivered_in_kilowatt_hour = dhw_thermal_energy_delivered_in_kilowatt_hour
        thermal_energy_delivered_entry = KpiEntry(
            name="Total thermal energy delivered",
            unit="kWh",
            value=total_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(thermal_energy_delivered_entry)

        dhw_thermal_energy_delivered_entry = KpiEntry(
            name="Thermal energy delivered for domestic hot water",
            unit="kWh",
            value=dhw_thermal_energy_delivered_in_kilowatt_hour,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_thermal_energy_delivered_entry)

        energy_consumption = KpiEntry(
            name="Total consumption (energy)",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(energy_consumption)

        dhw_energy_consumption = KpiEntry(
            name="Energy consumption for doemstic hot water",
            unit="kWh",
            value=opex_dataclass.consumption_for_domestic_hot_water_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(dhw_energy_consumption)

        # Economic and environmental KPIs
        capex = KpiEntry(
            name="CAPEX - Investment cost",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(capex)

        co2_footprint_capex = KpiEntry(
            name="CAPEX - CO2 Footprint",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint_capex)

        opex = KpiEntry(
            name="OPEX - Fuel costs",
            unit="EUR",
            value=opex_dataclass.opex_energy_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(opex)

        maintenance_costs = KpiEntry(
            name="OPEX - Maintenance costs",
            unit="EUR",
            value=opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(maintenance_costs)

        co2_footprint = KpiEntry(
            name="OPEX - CO2 Footprint",
            unit="kg",
            value=opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(co2_footprint)

        total_costs = KpiEntry(
            name="Total Costs (CAPEX for simulated period + OPEX fuel and maintenance)",
            unit="EUR",
            value=capex_dataclass.capex_investment_cost_for_simulated_period_in_euro
            + opex_dataclass.opex_energy_cost_in_euro
            + opex_dataclass.opex_maintenance_cost_in_euro,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_costs)

        total_co2_footprint = KpiEntry(
            name="Total CO2 Footprint (CAPEX for simulated period + OPEX)",
            unit="kg",
            value=capex_dataclass.device_co2_footprint_for_simulated_period_in_kg
            + opex_dataclass.co2_footprint_in_kg,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(total_co2_footprint)
        return list_of_kpi_entries

    def calc_maintenance_cost(self) -> float:
        """Calc maintenance_cost per simulated period as share of capex of component."""
        seconds_per_year = 365 * 24 * 60 * 60

        # add maintenance costs per simulated period
        maintenance_cost_per_simulated_period_in_euro: float = (
            self.config.maintenance_cost_eur_per_y
            * (self.my_simulation_parameters.duration.total_seconds() / seconds_per_year)
        )
        return maintenance_cost_per_simulated_period_in_euro

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureCollectorInletDegC,
                storage_classname,
                SimpleDHWStorage.WaterTemperatureToHeatGenerator,
            )
        )
        return connections

    def get_default_connections_from_weather(self):
        """Get default connections from weather."""

        connections = []
        weather_classname = Weather.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.TemperatureOutsideDegC,
                weather_classname,
                Weather.TemperatureOutside,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.GlobalHorizontalIrradianceWM2,
                weather_classname,
                Weather.GlobalHorizontalIrradiance,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.DiffuseHorizontalIrradianceWM2,
                weather_classname,
                Weather.DiffuseHorizontalIrradiance,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.Azimuth, weather_classname, Weather.Azimuth
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystem.ApparentZenith,
                weather_classname,
                Weather.ApparentZenith,
            )
        )
        return connections

    def get_default_connections_from_controller(
        self,
    ):
        """Get Controller default connections."""
        component_class = SolarThermalSystemController
        connections = []
        l1_controller_classname = component_class.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystem.ControlSignal,
                l1_controller_classname,
                component_class.ControlSignalToSolarThermalSystem,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves the current state."""
        self.previous_state = deepcopy(self.state)

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = deepcopy(self.previous_state)

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        file_exists, self.cache_filepath = utils.get_cache_file(
            self.config.name, self.config, self.my_simulation_parameters
        )

        if file_exists:
            log.information("Get solar thermal results from cache.")
            self.precalc_data_for_all_timesteps_output = pd.read_csv(
                self.cache_filepath, sep=",", decimal="."
            )["precalc_data"].tolist()

            if (
                len(self.precalc_data_for_all_timesteps_output)
                != self.my_simulation_parameters.timesteps
            ):
                raise Exception(
                    "Reading the cached solar thermal precalc values seems to have failed. "
                    + "Expected "
                    + str(self.my_simulation_parameters.timesteps)
                    + " values, but got "
                    + str(len(self.precalc_data_for_all_timesteps_output))
                )

        # create empty result lists as a preparation for caching
        # in i_simulate

        self.precalc_data_for_all_timesteps_data = [
            0
        ] * self.my_simulation_parameters.timesteps

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulates the component."""
        # check if results could be found in cache and if the list has
        # the right length
        if (
            hasattr(self, "precalc_data_for_all_timesteps_output")
            and len(self.precalc_data_for_all_timesteps_output)
            == self.my_simulation_parameters.timesteps
        ):
            pass  # use precalculated data from cache

        # calculate outputs
        else:
            control_signal = stsv.get_input_value(self.control_signal_channel)
            global_horizontal_irradiance_w_m2 = stsv.get_input_value(
                self.ghi_channel
            )
            diffuse_horizontal_irradiance_w_m2 = stsv.get_input_value(
                self.dhi_channel
            )
            ambient_air_temperature_deg_c = stsv.get_input_value(
                self.t_out_channel
            )
            temperature_collector_inlet_deg_c = stsv.get_input_value(
                self.water_temperature_input_channel
            )

            # calculate collectors heat
            # Some more info on equation:
            # http://www.estif.org/solarkeymarknew/the-solar-keymark-scheme-rules/21-certification-bodies/certified-products/58-collector-performance-parameters #noqa
            time_ind = (
                self.my_simulation_parameters.start_date
                + datetime.timedelta(
                    0,
                    self.my_simulation_parameters.seconds_per_timestep * timestep,
                )
            )

            precalc_data = flat_plate_precalc(
                lat=self.config.coordinates.latitude,
                long=self.config.coordinates.longitude,
                collector_tilt=self.config.tilt,
                collector_azimuth=self.config.azimuth,
                eta_0=self.config.eta_0,  # optical efficiency of the collector
                a_1=self.config.a_1_w_m2_k,  # thermal loss parameter 1
                a_2=self.config.a_2_w_m2_k,  # thermal loss parameter 2
                temp_collector_inlet=temperature_collector_inlet_deg_c,  # collectors inlet temperature
                delta_temp_n=self.config.delta_temperature_n_k,  # temperature difference between collector inlet and mean temperature
                irradiance_global=pd.Series(
                    global_horizontal_irradiance_w_m2, index=[time_ind]
                ),
                irradiance_diffuse=pd.Series(
                    diffuse_horizontal_irradiance_w_m2, index=[time_ind]
                ),
                temp_amb=pd.Series(
                    ambient_air_temperature_deg_c, index=[time_ind]
                ),
            )

        thermal_power_output_w = (
            precalc_data["collectors_heat"].iloc[0] * self.config.area_m2
        )

        thermal_energy_output_wh = (
            thermal_power_output_w
            * self.my_simulation_parameters.seconds_per_timestep
            / 3.6e3
        )
        required_mass_flow_output_kg_s = thermal_power_output_w / (
            PhysicsConfig.get_properties_for_energy_carrier(
                energy_carrier=loadtypes.LoadTypes.WATER
            ).specific_heat_capacity_in_joule_per_kg_per_kelvin
            * self.config.delta_temperature_n_k
        )

        if thermal_power_output_w > 0:
            # Given the right mass flow, assume that target temperature rise is achieved
            # Factor of 2 because delta_temperature_n_k is difference between inlet and mean temperature
            water_temperature_output_deg_c = (
                2 * self.config.delta_temperature_n_k
                + stsv.get_input_value(self.water_temperature_input_channel)
            )
        else:
            # Simplified assumption, neglecting heat losses: collector temperature equals input temperature
            water_temperature_output_deg_c = stsv.get_input_value(
                self.water_temperature_input_channel
            )

        if control_signal == 0:
            # If the controller signals 'off', the solar pump does not pump the solar fluid from
            # the collector to the storage
            mass_flow_output_kg_s = 0
            thermal_power_output_w = 0
            thermal_energy_output_wh = 0
            electric_power_demand_solar_pump_w = 0
        else:
            mass_flow_output_kg_s = required_mass_flow_output_kg_s
            # Calculate electricity consumption of solar pump
            electric_power_demand_solar_pump_w = (
                35 if self.config.old_solar_pump else 10
            )

        stsv.set_output_value(
            self.thermal_power_w_output_channel, thermal_power_output_w
        )
        stsv.set_output_value(
            self.thermal_energy_wh_output_channel, thermal_energy_output_wh
        )
        stsv.set_output_value(
            self.water_temperature_deg_c_output_channel,
            water_temperature_output_deg_c,
        )
        stsv.set_output_value(
            self.water_mass_flow_kg_s_output_channel, mass_flow_output_kg_s
        )
        stsv.set_output_value(
            self.required_water_mass_flow_kg_s_output_channel,
            required_mass_flow_output_kg_s,
        )
        stsv.set_output_value(
            self.electricity_consumption_output_channel,
            electric_power_demand_solar_pump_w,
        )
        # cache results at the end of the simulation
        self.precalc_data_for_all_timesteps_data[timestep] = (
            precalc_data
        )

        if timestep + 1 == self.my_simulation_parameters.timesteps:
            dict_with_results = {
                "precalc_data": self.precalc_data_for_all_timesteps_data,  # noqa: E501
            }

            database = pd.DataFrame(
                dict_with_results,
                columns=[
                    "precalc_data",
                ],
            )

            database.to_csv(
                self.cache_filepath, sep=",", decimal=".", index=False
            )


@dataclass
class SolarThermalSystemState:
    """The data class saves the state of the simulation results.

    Parameters
    ----------
    output_with_state : int
    Stores the state of the output_with_state value from
    :py:class:`~hisim.component.ComponentName`.

    """

    output_with_state: float = 0


@dataclass_json
@dataclass
class SolarThermalSystemControllerConfig(ConfigBase):
    """Config class for controller of solar thermal system."""

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SolarThermalSystemController.get_full_classname()

    building_name: str
    name: str
    set_temperature_difference_for_on: float

    @classmethod
    def get_solar_thermal_system_controller_config(
        cls,
        building_name: str = "BUI1",
        name: str = "SolarThermalSystemController",
        set_temperature_difference_for_on: float = 10,
    ) -> Any:
        """Gets a default SolarThermalSystemController for DHW."""
        return SolarThermalSystemControllerConfig(
            building_name=building_name,
            name=name,
            set_temperature_difference_for_on=set_temperature_difference_for_on,
        )


class SolarThermalSystemController(Component):
    """Solar Controller.

    It takes data from other components and sends signal to the
    solar pump (implicitly integrated in the SolarThermalSystem)
    for activation or deactivation.

    Parameters
    ----------
    Components to connect to:
    (1) SolarThermalSystem (control_signal)

    """

    # Inputs
    MeanWaterTemperatureInStorage = "MeanWaterTemperatureInStorage"
    CollectorTemperature = "CollectorTemperature"
    MassFlow = "MassFlow"

    # Outputs
    ControlSignalToSolarThermalSystem = "ControlSignalToSolarThermalSystem"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SolarThermalSystemControllerConfig,
        my_display_config: DisplayConfig = DisplayConfig(),
    ) -> None:
        """Construct all the neccessary attributes."""
        self.config = config
        self.my_simulation_parameters = my_simulation_parameters
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # warm water should aim for 55°C, should be 60°C when leaving heat generator, see source below
        # https://www.umweltbundesamt.de/umwelttipps-fuer-den-alltag/heizen-bauen/warmwasser#undefined
        self.warm_water_temperature_aim_in_celsius: float = 60.0

        # Configure Input Channels
        self.mean_water_temperature_storage_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.MeanWaterTemperatureInStorage,
                loadtypes.LoadTypes.TEMPERATURE,
                loadtypes.Units.CELSIUS,
                True,
            )
        )

        self.collector_temperature_input_channel: ComponentInput = (
            self.add_input(
                self.component_name,
                self.CollectorTemperature,
                loadtypes.LoadTypes.TEMPERATURE,
                loadtypes.Units.CELSIUS,
                True,
            )
        )

        self.required_mass_flow_input_channel: ComponentInput = self.add_input(
            self.component_name,
            self.MassFlow,
            loadtypes.LoadTypes.WATER,
            loadtypes.Units.KG_PER_SEC,
            True,
        )

        # Configure Output Channels
        self.control_signal_to_solar_thermal_system_channel: ComponentOutput = self.add_output(
            self.component_name,
            self.ControlSignalToSolarThermalSystem,
            loadtypes.LoadTypes.ANY,
            loadtypes.Units.BINARY,
            output_description="Control signal to solar pump in SolarThermalSystem",
        )

        self.state: SolarThermalSystemControllerState = (
            SolarThermalSystemControllerState(0, 0, 0)
        )
        self.previous_state: SolarThermalSystemControllerState = (
            self.state.clone()
        )
        self.processed_state: SolarThermalSystemControllerState = (
            self.state.clone()
        )

        self.add_default_connections(
            self.get_default_connections_from_simple_hot_water_storage()
        )
        self.add_default_connections(
            self.get_default_connections_from_solar_thermal_system()
        )

    def get_default_connections_from_simple_hot_water_storage(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SimpleDHWStorage.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.MeanWaterTemperatureInStorage,
                storage_classname,
                SimpleDHWStorage.WaterMeanTemperatureInStorage,
            )
        )
        return connections

    def get_default_connections_from_solar_thermal_system(
        self,
    ):
        """Get simple_water_storage default connections."""

        connections = []
        storage_classname = SolarThermalSystem.get_classname()
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.CollectorTemperature,
                storage_classname,
                SolarThermalSystem.WaterTemperatureOutput,
            )
        )
        connections.append(
            ComponentConnection(
                SolarThermalSystemController.MassFlow,
                storage_classname,
                SolarThermalSystem.RequiredWaterMassFlowOutput,
            )
        )
        return connections

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores previous state."""
        self.state = self.previous_state.clone()

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation."""
        pass

    def i_simulate(
        self,
        timestep: int,
        stsv: SingleTimeStepValues,
        force_convergence: bool,
    ) -> None:
        """Simulate the solar thermal system controller."""
        if force_convergence:
            # states are saved after each timestep, outputs after each iteration
            # outputs have to be in line with states, so if convergence is forced outputs are aligned to last known state.
            self.state = self.processed_state.clone()
        else:
            # Retrieves inputs
            mean_water_temperature_storage_deg_c = stsv.get_input_value(
                self.mean_water_temperature_storage_input_channel
            )
            collector_temperature_deg_c = stsv.get_input_value(
                self.collector_temperature_input_channel
            )
            required_mass_flow_kg_s = stsv.get_input_value(
                self.required_mass_flow_input_channel
            )

            self.get_controller_state(
                timestep,
                mean_water_temperature_storage_deg_c,
                collector_temperature_deg_c,
                required_mass_flow_kg_s,
            )
            self.processed_state = self.state.clone()

        stsv.set_output_value(
            self.control_signal_to_solar_thermal_system_channel,
            self.state.on_off,
        )

    def get_controller_state(
        self,
        timestep: int,
        mean_water_temperature_storage_deg_c: float,
        collector_temperature_deg_c: float,
        mass_flow_kg_s: float,
    ) -> None:
        """Calculate the solar pump state and activate / deactives."""
        if (
            collector_temperature_deg_c - mean_water_temperature_storage_deg_c
        ) > self.config.set_temperature_difference_for_on:
            # activate heating when difference between collector temperature and storage temperature
            # is at least 6 K
            self.state.activate(timestep)

        if (
            mean_water_temperature_storage_deg_c
            > self.warm_water_temperature_aim_in_celsius
        ):
            # deactivate heating when storage temperature is too high
            # this overrides the activation based on temperature difference
            self.state.deactivate(timestep)

        if mass_flow_kg_s < 0.01:
            # deactivate when mass flow is too low
            self.state.deactivate(timestep)

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = (
            OpexCostDataClass.get_default_opex_cost_data_class()
        )
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(
        config: SolarThermalSystemControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = (
            CapexCostDataClass.get_default_capex_cost_data_class()
        )
        return capex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []


class SolarThermalSystemControllerState:
    """Data class that saves the state of the controller."""

    def __init__(
        self,
        on_off: int,
        activation_time_step: int,
        deactivation_time_step: int,
    ) -> None:
        """Initializes the solar pump controller state."""
        self.on_off: int = on_off
        self.activation_time_step: int = activation_time_step
        self.deactivation_time_step: int = deactivation_time_step

    def clone(self) -> "SolarThermalSystemControllerState":
        """Copies the current instance."""
        return SolarThermalSystemControllerState(
            on_off=self.on_off,
            activation_time_step=self.activation_time_step,
            deactivation_time_step=self.deactivation_time_step,
        )

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def activate(self, timestep: int) -> None:
        """Activates the solar pump and remembers the time step."""
        self.on_off = 1
        self.activation_time_step = timestep

    def deactivate(self, timestep: int) -> None:
        """Deactivates the solar pump and remembers the time step."""
        self.on_off = 0
        self.deactivation_time_step = timestep
