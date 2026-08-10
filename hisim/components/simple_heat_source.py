""" Generic Heat Source. """


# Import packages from standard library or the environment e.g. pandas, numpy etc.
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from enum import Enum
import math
import warnings
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
    ETHYLENE_GLYCOL = "EthyleneGlycol"
    PROPYLEN_GLYCOL = "PropyleneGlycol"
    ETHANOL = "EthylAlcohol"
    METHANOL = "MethylAlcohol"


@dataclass_json
@dataclass
class SimpleHeatSourceConfig(cp.ConfigBase):
    """Configuration of a generic HeatSource.

    JSON field-name migrations (issue #1603):
        ``const_source``              -> ``heat_source_type``
        ``temperature_out_in_celsius`` -> ``temperature_output_in_celsius``

    :meth:`from_dict` still accepts the legacy names (with a
    :class:`DeprecationWarning`); :meth:`to_dict` / :meth:`to_json`
    always emit the current names.
    """

    building_name: str
    name: str
    power_th_in_watt: Optional[float]
    temperature_output_in_celsius: Optional[float]
    heat_source_type: Optional[SimpleHeatSourceType]
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
            heat_source_type=SimpleHeatSourceType.CONSTANT_THERMAL_POWER,  # type: ignore
            power_th_in_watt=5000.0,
            temperature_output_in_celsius=None,
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
            heat_source_type=SimpleHeatSourceType.CONSTANT_TEMPERATURE,  # type: ignore
            power_th_in_watt=None,
            temperature_output_in_celsius=5,
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
    def get_default_config_near_surface_brine_temperature(
        cls,
        building_name: str = "BUI1",
    ) -> "SimpleHeatSourceConfig":
        """Return the default config for a near-surface brine heat source.

        Sets ``heat_source_type`` to
        :attr:`SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE`, which
        models a variable brine temperature derived from the daily average
        outside temperature.
        """
        config = SimpleHeatSourceConfig(
            building_name=building_name,
            name="HeatSourceVarBrineTemperature",
            heat_source_type=SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE,  # type: ignore
            power_th_in_watt=None,
            temperature_output_in_celsius=None,
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
        """Deprecated alias for :meth:`get_default_config_near_surface_brine_temperature`.

        The original abbreviated name ``get_default_config_var_brinetemperature``
        was renamed for clarity (issue #1603). This shim keeps older callers
        working and emits a :class:`DeprecationWarning`.
        """
        warnings.warn(
            "SimpleHeatSourceConfig.get_default_config_var_brinetemperature is "
            "deprecated; use get_default_config_near_surface_brine_temperature "
            "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.get_default_config_near_surface_brine_temperature(building_name)


# Backward-compatible deserialization for config fields renamed in issue #1603.
#
# SimpleHeatSourceConfig is serialized to JSON through @dataclass_json, which uses
# the dataclass field names as JSON keys. Two fields were renamed for clarity:
#   const_source               -> heat_source_type
#   temperature_out_in_celsius -> temperature_output_in_celsius
# Config JSON saved before the rename therefore uses the old keys and would raise
# KeyError on load. The @dataclass_json decorator installs its own ``from_dict``
# classmethod and would overwrite any ``from_dict`` defined in the class body, so
# the legacy-aware decoder is installed here, after the class is decorated.
# to_dict / to_json keep emitting the current (new) field names; only
# deserialization accepts the legacy aliases and warns about them.
_LEGACY_CONFIG_FIELD_ALIASES: Dict[str, str] = {
    "const_source": "heat_source_type",
    "temperature_out_in_celsius": "temperature_output_in_celsius",
}

# The @dataclass_json-provided decoder, captured before it is replaced below.
_dataclass_json_from_dict: "Callable[..., SimpleHeatSourceConfig]" = (
    SimpleHeatSourceConfig.from_dict.__func__
)


@classmethod
def _from_dict_with_legacy_aliases(
    cls: "type[SimpleHeatSourceConfig]",
    kvs: Any,
    *,
    infer_missing: bool = False,
) -> "SimpleHeatSourceConfig":
    """Decode a config dict, accepting pre-rename field names as aliases.

    Maps the legacy keys ``const_source`` and ``temperature_out_in_celsius``
    (see issue #1603) to their current names and emits a :class:`DeprecationWarning`
    so callers can migrate saved configs. When both the old and the new name are
    present, the new name takes precedence and the legacy key is dropped.
    """
    if isinstance(kvs, dict):
        legacy_keys = [old for old in _LEGACY_CONFIG_FIELD_ALIASES if old in kvs]
        if legacy_keys:
            warnings.warn(
                "SimpleHeatSourceConfig: the JSON field name(s) "
                + ", ".join(repr(name) for name in legacy_keys)
                + " are deprecated; use "
                + ", ".join(repr(_LEGACY_CONFIG_FIELD_ALIASES[name]) for name in legacy_keys)
                + " instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            kvs = dict(kvs)
            for old_name in legacy_keys:
                new_name = _LEGACY_CONFIG_FIELD_ALIASES[old_name]
                if new_name not in kvs:
                    kvs[new_name] = kvs.pop(old_name)
                else:
                    kvs.pop(old_name)
    return _dataclass_json_from_dict(cls, kvs, infer_missing=infer_missing)


SimpleHeatSourceConfig.from_dict = _from_dict_with_legacy_aliases


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
        self.config: SimpleHeatSourceConfig = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        if self.config.heat_source_type is None:  # type: ignore
            raise ValueError("heat_source_type is not set.")

        if (self.config.use_external_massflow_as_signal_input_for_nominal_massflow and
                self.config.massflow_nominal_in_kg_per_s is None):
            raise ValueError(
                "use_external_massflow_as_signal_input_for_nominal_massflow is True, "
                "so massflow_nominal_in_kg_per_s can't be None."
            )

        if self.config.heat_source_type == SimpleHeatSourceType.CONSTANT_THERMAL_POWER:  # type: ignore
            self.power_th_in_watt: Optional[float] = self.config.power_th_in_watt
            if self.power_th_in_watt is None or math.isnan(self.power_th_in_watt):
                raise ValueError("Undefined value for constant power")
        elif self.config.heat_source_type == SimpleHeatSourceType.CONSTANT_TEMPERATURE:  # type: ignore
            self.temperature_output_in_celsius: Optional[float] = self.config.temperature_output_in_celsius
            if self.temperature_output_in_celsius is None or math.isnan(self.temperature_output_in_celsius):
                raise ValueError("Undefined value for constant temperature")
        elif self.config.heat_source_type == SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE:  # type: ignore
            pass
        else:
            raise ValueError("Invalid heat_source_type value.")

        self.fluid_type: FluidMediaType = config.fluid_type
        self.mass_fraction_of_fluid_mixed_in_water: float = config.mass_fraction_of_fluid_mixed_in_water

        self.specific_heat_capacity_of_fluid_in_joule_per_kg_per_kelvin: float = 0.0
        self.calculate_fluid_properties()

        self.state: SimpleHeatSourceState = SimpleHeatSourceState()
        self.previous_state: SimpleHeatSourceState = SimpleHeatSourceState()

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

    def calculate_fluid_properties(self):
        """Calculation of fluid properties."""
        fluid = Fluid(self.fluid_type.value, self.mass_fraction_of_fluid_mixed_in_water * 100)
        self.specific_heat_capacity_of_fluid_in_joule_per_kg_per_kelvin = fluid.cp  # Fluid specific isobaric heat capacity (J/kg.K)

    def write_to_report(self) -> List[str]:
        """Writes relevant data to report."""
        lines = []
        lines.append(f"Name: {self.config.name}")
        lines.append(f"Source: {self.config.heat_source_type}")
        if self.config.heat_source_type == SimpleHeatSourceType.CONSTANT_THERMAL_POWER:
            assert self.config.power_th_in_watt is not None
            lines.append(f"Power: {self.config.power_th_in_watt * 1e-3:4.0f} kW")
        if self.config.heat_source_type == SimpleHeatSourceType.CONSTANT_TEMPERATURE:
            lines.append(f"Temperature : {self.config.temperature_output_in_celsius} °C")
        if self.config.heat_source_type == SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE:
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
            assert self.config.massflow_nominal_in_kg_per_s is not None
            massflow_in_kg_per_sec = self.config.massflow_nominal_in_kg_per_s

        temperature_input_in_celsius = stsv.get_input_value(
            self.temperature_input_channel
        )

        if self.config.heat_source_type == SimpleHeatSourceType.CONSTANT_THERMAL_POWER:
            assert self.power_th_in_watt is not None
            thermal_power_in_watt = self.power_th_in_watt

            temperature_output_in_celsius = (
                thermal_power_in_watt
                / (massflow_in_kg_per_sec * self.specific_heat_capacity_of_fluid_in_joule_per_kg_per_kelvin)
            ) + temperature_input_in_celsius

        elif self.config.heat_source_type == SimpleHeatSourceType.CONSTANT_TEMPERATURE:
            assert self.temperature_output_in_celsius is not None
            temperature_output_in_celsius = self.temperature_output_in_celsius

            thermal_power_in_watt = (massflow_in_kg_per_sec * self.specific_heat_capacity_of_fluid_in_joule_per_kg_per_kelvin *
                                     (temperature_output_in_celsius - temperature_input_in_celsius))  # type: ignore

        elif self.config.heat_source_type == SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE:
            """From hplib: Calculate the soil temperature by the average Temperature of the day.
            Source: „WP Monitor“ Feldmessung von Wärmepumpenanlagen S. 115, Frauenhofer ISE, 2014
            added 9 points at -15°C average day at 3°C soil temperature in order to prevent higher
            temperature of soil below -10°C."""

            temperature_output_in_celsius = (
                -0.0003 * daily_avg_outside_temperature_in_celsius**3
                + 0.0086 * daily_avg_outside_temperature_in_celsius**2
                + 0.3047 * daily_avg_outside_temperature_in_celsius
                + 5.0647
            )

            thermal_power_in_watt = (massflow_in_kg_per_sec * self.specific_heat_capacity_of_fluid_in_joule_per_kg_per_kelvin *
                                     (temperature_output_in_celsius - temperature_input_in_celsius))

        else:
            raise KeyError("Unknown heat source type")

        stsv.set_output_value(self.massflow_output_channel, massflow_in_kg_per_sec)  # type: ignore
        stsv.set_output_value(self.thermal_power_delivered_channel, thermal_power_in_watt)  # type: ignore
        stsv.set_output_value(self.temperature_output_channel, temperature_output_in_celsius)

    @staticmethod
    def get_cost_capex(
        config: SimpleHeatSourceConfig,
        simulation_parameters: SimulationParameters
    ) -> CapexCostDataClass:
        """Returns investment cost, CO2 emissions and lifetime."""
        seconds_per_year_in_s = 365 * 24 * 60 * 60
        capex_per_simulated_period_in_euro = (config.investment_costs_in_euro / config.lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year_in_s
        )
        device_co2_footprint_per_simulated_period_in_kg = (config.device_co2_footprint_in_kg / config.lifetime_in_years) * (
            simulation_parameters.duration.total_seconds() / seconds_per_year_in_s
        )
        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.device_co2_footprint_in_kg,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_simulated_period_in_euro,
            device_co2_footprint_for_simulated_period_in_kg=device_co2_footprint_per_simulated_period_in_kg,
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
