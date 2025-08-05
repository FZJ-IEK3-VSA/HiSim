""" Generic Heat Source. """

# clean

# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum
import pandas as pd
from dataclasses_json import dataclass_json

from pygfunction.media import Fluid

# Import modules from HiSim
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.loadtypes import Units
from hisim.simulationparameters import SimulationParameters
from hisim.component import ComponentInput, ComponentConnection, OpexCostDataClass, CapexCostDataClass
from hisim.components import weather
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry

__authors__ = "Jonas Hoppe"
__copyright__ = ""
__credits__ = [""]
__license__ = ""
__version__ = ""
__maintainer__ = ""
__email__ = ""
__status__ = ""


class SimpleHeatSourceType(Enum):
    """Set Heat Source Types."""

    CONSTANT_THERMAL_POWER = "CONSTANT_THERMAL_POWER"
    CONSTANT_TEMPERATURE = "CONSTANT_TEMPERATURE"
    NEAR_SURFACE_BRINE_TEMPERATURE = "NEAR_SURFACE_BRINE_TEMPERATURE"


class FluidMediaType(Enum):
    """ Sort of Media."""

    WATER = "Water"
    ETHYLEN_GLYCOL = "EthyleneGlycol"
    PROPYLEN_GLYCOL = "PropyleneGlycol"
    ETHANOL = "EthylAlcohol"
    METHANOL = "MethylAlcohol"


@dataclass_json
@dataclass
class SimpleHeatSourceConfig(cp.ConfigBase):
    """Configuration of a generic HeatSource."""

    building_name: str
    name: str
    power_th_in_watt: Optional[float]
    temperature_out_in_celsius: Optional[float]
    const_source: Optional[SimpleHeatSourceType]
    fluid_type: FluidMediaType
    mass_fraction_of_fluid_mixed_in_water: float
    massflow_nominal_in_kg_per_s: Optional[float]
    use_external_massflow_as_signal_input_for_nominal_massflow: bool
    #: CO2 footprint of investment in kg
    device_co2_footprint_in_kg: float
    #: cost for investment in Euro
    investment_costs_in_euro: float
    #: lifetime in years
    lifetime_in_years: float
    # maintenance cost in euro per year
    maintenance_costs_in_euro_per_year: float

    @classmethod
    def get_main_classname(cls):
        """Returns the full class name of the base class."""
        return SimpleHeatSource.get_full_classname()

    @classmethod
    def get_default_config_const_power(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatSourceConstPower",
            const_source=SimpleHeatSourceType.CONSTANT_THERMAL_POWER,  # type: ignore
            power_th_in_watt=5000.0,
            temperature_out_in_celsius=None,
            fluid_type=FluidMediaType.PROPYLEN_GLYCOL,
            mass_fraction_of_fluid_mixed_in_water=0.20,
            massflow_nominal_in_kg_per_s=0.5,
            use_external_massflow_as_signal_input_for_nominal_massflow=False,
            device_co2_footprint_in_kg=100,  # Todo: check value
            investment_costs_in_euro=2000,  # value from https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick for earth collector
            lifetime_in_years=25,
            maintenance_costs_in_euro_per_year=10,  # from https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick for earth collector
        )
        return config

    @classmethod
    def get_default_config_const_temperature(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatSourceConstTemperature",
            const_source=SimpleHeatSourceType.CONSTANT_TEMPERATURE,  # type: ignore
            power_th_in_watt=None,
            temperature_out_in_celsius=5,
            fluid_type=FluidMediaType.PROPYLEN_GLYCOL,
            mass_fraction_of_fluid_mixed_in_water=0.20,
            massflow_nominal_in_kg_per_s=0.5,
            use_external_massflow_as_signal_input_for_nominal_massflow=False,
            device_co2_footprint_in_kg=100,  # Todo: check value
            investment_costs_in_euro=2000,
            # value from https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick for earth collector
            lifetime_in_years=25,  # value from emission_factors_and_costs_devices.csv
            maintenance_costs_in_euro_per_year=10,
            # from https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick for earth collector
        )
        return config

    @classmethod
    def get_default_config_var_brinetemperature(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Returns default configuration of a Heat Source used for heating."""
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatSourceVarBrineTemperature",
            const_source=SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE,  # type: ignore
            power_th_in_watt=None,
            temperature_out_in_celsius=None,
            fluid_type=FluidMediaType.PROPYLEN_GLYCOL,
            mass_fraction_of_fluid_mixed_in_water=0.20,
            massflow_nominal_in_kg_per_s=0.5,
            use_external_massflow_as_signal_input_for_nominal_massflow=False,
            device_co2_footprint_in_kg=100,  # Todo: check value
            investment_costs_in_euro=2000,
            # value from https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick for earth collector
            lifetime_in_years=25,  # value from emission_factors_and_costs_devices.csv
            maintenance_costs_in_euro_per_year=10,
            # from https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick for earth collector
        )
        return config


class SimpleHeatSourceState:
    """Heat source state class saves the state of the heat source."""

    def __init__(self, state: int = 0):
        """Initializes state."""
        self.state = state

    def clone(self) -> "SimpleHeatSourceState":
        """Creates copy of a state."""
        return SimpleHeatSourceState(state=self.state)


class SimpleHeatSource(cp.Component):
    """Heat Source implementation."""

    # Inputs
    DailyAverageOutsideTemperature = "DailyAverageOutsideTemperature"
    MassFlow = "MassFlow"
    TemperatureInput = "TemperatureInput"

    # Outputs
    ThermalPowerDelivered = "ThermalPowerDelivered"
    TemperatureOutput = "TemperatureOutput"
    MassFlowOutput = "MassFlowOutput"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleHeatSourceConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the class."""

        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        if self.config.const_source is None:  # type: ignore
            raise ValueError("const_source is not set.")

        if (self.config.use_external_massflow_as_signal_input_for_nominal_massflow and
                self.config.massflow_nominal_in_kg_per_s is None):
            raise ValueError(
                "use_external_massflow_as_signal_input is True, so massflow_nominal_in_kg_per_s can't be None."
            )

        if self.config.const_source == SimpleHeatSourceType.CONSTANT_THERMAL_POWER:  # type: ignore
            self.power_th_in_watt = self.config.power_th_in_watt
            if self.power_th_in_watt is None or str(self.power_th_in_watt) == "nan":
                raise ValueError("Undefined value for constant power")
        elif self.config.const_source == SimpleHeatSourceType.CONSTANT_TEMPERATURE:  # type: ignore
            self.temperature_out_in_celsius = self.config.temperature_out_in_celsius
            if self.temperature_out_in_celsius is None or str(self.temperature_out_in_celsius) == "nan":
                raise ValueError("Undefined value for constant temperature")
        elif self.config.const_source == SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE:  # type: ignore
            pass
        else:
            raise ValueError("Invalid const_source value.")

        self.fluid_type = config.fluid_type
        self.mass_fraction_of_fluid_mixed_in_water = config.mass_fraction_of_fluid_mixed_in_water

        self.fluid_props()

        self.state = SimpleHeatSourceState()
        self.previous_state = SimpleHeatSourceState()

        # Inputs
        self.daily_avg_outside_temperature_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.DailyAverageOutsideTemperature,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=True,
        )

        self.massflow_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.MassFlow,
            load_type=lt.LoadTypes.VOLUME,
            unit=Units.KG_PER_SEC,
            mandatory=False,
        )

        self.temperature_input_channel: ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.TemperatureInput,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=Units.CELSIUS,
            mandatory=False,
        )

        # Outputs
        self.thermal_power_delivered_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ThermalPowerDelivered,
            load_type=lt.LoadTypes.HEATING,
            unit=lt.Units.WATT,
            output_description="Thermal Power Delivered",
        )

        self.temperature_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TemperatureOutput,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            output_description="Temperature Output",
        )
        self.massflow_output_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.MassFlowOutput,
            load_type=lt.LoadTypes.VOLUME,
            unit=lt.Units.KG_PER_SEC,
            output_description="Massflow Output",
        )

        self.add_default_connections(self.get_default_connections_from_weather())

    def get_default_connections_from_weather(
        self,
    ):
        """Get default connections."""
        connections = []
        weather_classname = weather.Weather.get_classname()
        connections.append(
            ComponentConnection(
                SimpleHeatSource.DailyAverageOutsideTemperature,
                weather_classname,
                weather.Weather.DailyAverageOutsideTemperatures,
            )
        )
        return connections

    def fluid_props(self):
        """Calculation of fluid properties."""
        fluid = Fluid(self.fluid_type.value, self.mass_fraction_of_fluid_mixed_in_water * 100)
        self.cp_f = fluid.cp  # Fluid specific isobaric heat capacity (J/kg.K)

    def write_to_report(self) -> List[str]:
        """Writes relevant data to report."""
        lines = []
        lines.append(f"Name: {self.config.name}")
        lines.append(f"Source: {self.config.const_source}")
        if self.config.const_source == SimpleHeatSourceType.CONSTANT_THERMAL_POWER:
            lines.append(f"Power: {self.config.power_th_in_watt * 1e-3:4.0f} kW")
        if self.config.const_source == SimpleHeatSourceType.CONSTANT_TEMPERATURE:
            lines.append(f"Temperature : {self.config.temperature_out_in_celsius} °C")
        if self.config.const_source == SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE:
            lines.append("Temperature : .... °C")
        lines.append("--------------------")
        lines.append(f"Fluidtype: {self.fluid_type}")
        lines.append(f"Massfraction: {self.mass_fraction_of_fluid_mixed_in_water}")

        return lines

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_save_state(self) -> None:
        """Saves the state."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Performs the simulation of the heat source model."""

        daily_avg_outside_temperature_in_celsius = stsv.get_input_value(
            self.daily_avg_outside_temperature_input_channel
        )

        massflow_in_kg_per_sec = stsv.get_input_value(
            self.massflow_input_channel
        )

        if self.config.use_external_massflow_as_signal_input_for_nominal_massflow and massflow_in_kg_per_sec != 0:
            massflow_in_kg_per_sec = self.config.massflow_nominal_in_kg_per_s

        temperature_input_in_celsius = stsv.get_input_value(
            self.temperature_input_channel
        )

        if self.config.const_source == SimpleHeatSourceType.CONSTANT_THERMAL_POWER:
            thermal_power_in_watt = self.power_th_in_watt

            temperature_output = (thermal_power_in_watt / (massflow_in_kg_per_sec * self.cp_f)) + temperature_input_in_celsius

        elif self.config.const_source == SimpleHeatSourceType.CONSTANT_TEMPERATURE:
            temperature_output = self.temperature_out_in_celsius

            thermal_power_in_watt = (massflow_in_kg_per_sec * self.cp_f *
                                     (temperature_output - temperature_input_in_celsius))  # type: ignore

        elif self.config.const_source == SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE:
            """From hplib: Calculate the soil temperature by the average Temperature of the day.
            Source: „WP Monitor“ Feldmessung von Wärmepumpenanlagen S. 115, Frauenhofer ISE, 2014
            added 9 points at -15°C average day at 3°C soil temperature in order to prevent higher
            temperature of soil below -10°C."""

            temperature_output = (
                -0.0003 * daily_avg_outside_temperature_in_celsius**3
                + 0.0086 * daily_avg_outside_temperature_in_celsius**2
                + 0.3047 * daily_avg_outside_temperature_in_celsius
                + 5.0647
            )

            thermal_power_in_watt = (massflow_in_kg_per_sec * self.cp_f *
                                     (temperature_output - temperature_input_in_celsius))

        else:
            raise KeyError("Unknown heat source type")

        stsv.set_output_value(self.massflow_output_channel, massflow_in_kg_per_sec)  # type: ignore
        stsv.set_output_value(self.thermal_power_delivered_channel, thermal_power_in_watt)  # type: ignore
        stsv.set_output_value(self.temperature_output_channel, temperature_output)

    @staticmethod
    def get_cost_capex(
        config: SimpleHeatSourceConfig,
        simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year = 365 * 24 * 60 * 60
        capex_per_simulated_period = (config.investment_costs_in_euro / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        device_co2_footprint_per_simulated_period = (config.co2_footprint / config.lifetime) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )
        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.device_co2_footprint_in_kg,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period,
            kpi_tag=KpiTagEnumClass.GENERIC_HEAT_SOURCE
        )
        return capex_cost_data_class

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> OpexCostDataClass:
        # pylint: disable=unused-argument
        """Calculate OPEX costs, consisting of maintenance costs for Heat Distribution System."""
        opex_cost_data_class = OpexCostDataClass(
            opex_energy_cost_in_euro=0,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=0,
            total_consumption_in_kwh=0,
            loadtype=lt.LoadTypes.ANY,
            kpi_tag=KpiTagEnumClass.GENERIC_HEAT_SOURCE
        )

        return opex_cost_data_class

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""
        return []
