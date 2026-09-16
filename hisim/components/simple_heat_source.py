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
from hisim.config import ConfigBase, ComponentID, DisplayConfig, preset
from hisim.components import weather
from hisim.postprocessing.kpi_computation.kpi_structure import KpiTagEnumClass, KpiEntry
from hisim.postprocessing.cost_and_emission_computation.capex_computation import prorate_to_simulated_period
from hisim.economics.facts import CostRelevance


class SimpleHeatSourceType(str, Enum):
    """Set Heat Source Types.

    A ``str`` mixin so members survive ``json.dumps(config.to_dict())`` — the exact
    path the scenario generator uses — without a custom encoder. The values already
    equal the member names and stay unchanged.
    """

    CONSTANT_THERMAL_POWER = "CONSTANT_THERMAL_POWER"
    CONSTANT_TEMPERATURE = "CONSTANT_TEMPERATURE"
    NEAR_SURFACE_BRINE_TEMPERATURE = "NEAR_SURFACE_BRINE_TEMPERATURE"


class FluidMediaType(str, Enum):
    """ Sort of Media.

    A ``str`` mixin so members survive ``json.dumps(config.to_dict())``. The values
    are pygfunction fluid identifiers consumed via ``.value`` in ``Fluid(...)`` and
    must not be renamed to the member names.
    """

    WATER = "Water"
    ETHYLENE_GLYCOL = "EthyleneGlycol"
    PROPYLEN_GLYCOL = "PropyleneGlycol"
    ETHANOL = "EthylAlcohol"
    METHANOL = "MethylAlcohol"


@dataclass_json
@dataclass
class SimpleHeatSourceConfig(ConfigBase):
    """Configuration of the Simple Heat Source class.

    The cold side of a heat pump: a brine circuit that hands the machine a massflow at some
    temperature. What it is depends on :attr:`heat_source_type`, and the presets are one per
    kind -- :meth:`preset_constant_thermal_power` puts a fixed thermal power into the circuit,
    :meth:`preset_constant_temperature` holds a fixed output temperature, and
    :meth:`preset_near_surface_brine` derives the output temperature from the daily average
    outside temperature, the way a shallow ground collector behaves::

        SimpleHeatSourceConfig.preset_near_surface_brine("HeatSource")

    Everything the three share -- the fluid, its mixing ratio, the nominal massflow and the
    four investment figures -- is a field default, so a preset states only the kind of source
    it is and the one number that kind needs.

    JSON field-name migrations (issue #1603):
        ``const_source``              -> ``heat_source_type``
        ``temperature_out_in_celsius`` -> ``temperature_output_in_celsius``

    :meth:`from_dict` still accepts the legacy names (with a
    :class:`DeprecationWarning`); :meth:`to_dict` / :meth:`to_json`
    always emit the current names.
    """

    MAIN_CLASS = "hisim.components.simple_heat_source.SimpleHeatSource"

    component_id: ComponentID
    #: Which kind of source this is, and therefore which of the two numbers below the
    #: component reads. The component refuses to start on ``None``.
    heat_source_type: Optional[SimpleHeatSourceType]
    #: Thermal power put into the circuit, read only by a ``CONSTANT_THERMAL_POWER`` source.
    power_th_in_watt: Optional[float] = None
    #: Temperature the circuit is held at, read only by a ``CONSTANT_TEMPERATURE`` source.
    temperature_output_in_celsius: Optional[float] = None
    #: Fluid circulating on the source side; its heat capacity is looked up from pygfunction.
    fluid_type: FluidMediaType = FluidMediaType.PROPYLEN_GLYCOL
    #: Share of that fluid in the water it is mixed with, as a fraction.
    mass_fraction_of_fluid_mixed_in_water: float = 0.20
    #: Massflow the circuit runs at when the external signal below is used instead of the
    #: measured massflow input.
    massflow_nominal_in_kg_per_s: Optional[float] = 0.5
    #: Whether a non-zero massflow input is a mere on signal, the circuit then running at
    #: :attr:`massflow_nominal_in_kg_per_s` rather than at the value handed in.
    use_external_massflow_as_signal_input_for_nominal_massflow: bool = False
    #: CO2 footprint of investment in kg. Todo: check value
    device_co2_footprint_in_kg: float = 100
    #: cost for investment in Euro. Value from
    #: https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick
    #: for an earth collector.
    investment_costs_in_euro: float = 2000
    #: lifetime in years, value from emission_factors_and_costs_devices.csv
    lifetime_in_years: float = 25
    #: maintenance cost in euro per year, from
    #: https://www.buderus.de/de/waermepumpe/kosten-einer-erdwaermeanlage-im-ueberblick
    #: for an earth collector.
    maintenance_costs_in_euro_per_year: float = 10

    @preset
    @classmethod
    def preset_constant_thermal_power(cls, name: str) -> "SimpleHeatSourceConfig":
        """A source that puts a constant 5 kW of heat into the brine circuit.

        The output temperature follows from that power and the massflow the circuit carries,
        so :attr:`temperature_output_in_celsius` stays unset.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            SimpleHeatSourceConfig: The preset configuration.
        """
        return cls(
            component_id=ComponentID(name=name),
            heat_source_type=SimpleHeatSourceType.CONSTANT_THERMAL_POWER,
            power_th_in_watt=5000.0,
        )

    @preset
    @classmethod
    def preset_constant_temperature(cls, name: str) -> "SimpleHeatSourceConfig":
        """A source that holds the brine circuit at a constant 5 °C.

        The thermal power follows from that temperature and the massflow the circuit carries,
        so :attr:`power_th_in_watt` stays unset.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            SimpleHeatSourceConfig: The preset configuration.
        """
        return cls(
            component_id=ComponentID(name=name),
            heat_source_type=SimpleHeatSourceType.CONSTANT_TEMPERATURE,
            temperature_output_in_celsius=5,
        )

    @preset
    @classmethod
    def preset_near_surface_brine(cls, name: str) -> "SimpleHeatSourceConfig":
        """A shallow ground collector, its brine temperature following the weather.

        Neither a power nor a temperature is pinned: the component derives the output
        temperature from the daily average outside temperature with the cubic fit hplib
        uses for soil temperature, and the thermal power follows from it and the massflow.

        Args:
            name: The instance name, which becomes the configuration's component identity.

        Returns:
            SimpleHeatSourceConfig: The preset configuration.
        """
        return cls(
            component_id=ComponentID(name=name),
            heat_source_type=SimpleHeatSourceType.NEAR_SURFACE_BRINE_TEMPERATURE,
        )


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

# The @dataclass_json-provided decoder, captured before it is replaced below. The type
# checker only sees ConfigBase's from_dict stub (a plain classmethod signature without
# __func__), so the runtime unwrapping needs an ignore.
_dataclass_json_from_dict: "Callable[..., SimpleHeatSourceConfig]" = (
    SimpleHeatSourceConfig.from_dict.__func__  # type: ignore[attr-defined]
)


@classmethod
def _from_dict_with_legacy_aliases(
    cls: "type[SimpleHeatSourceConfig]",
    config_dict: Any,
    *,
    infer_missing: bool = False,
) -> "SimpleHeatSourceConfig":
    """Decode a config dict, accepting pre-rename field names as aliases.

    Maps the legacy keys ``const_source`` and ``temperature_out_in_celsius``
    (see issue #1603) to their current names and emits a :class:`DeprecationWarning`
    so callers can migrate saved configs. When both the old and the new name are
    present, the new name takes precedence and the legacy key is dropped.
    """
    if isinstance(config_dict, dict):
        legacy_keys = [old for old in _LEGACY_CONFIG_FIELD_ALIASES if old in config_dict]
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
            config_dict = dict(config_dict)
            for old_name in legacy_keys:
                new_name = _LEGACY_CONFIG_FIELD_ALIASES[old_name]
                if new_name not in config_dict:
                    config_dict[new_name] = config_dict.pop(old_name)
                else:
                    config_dict.pop(old_name)
    return _dataclass_json_from_dict(cls, config_dict, infer_missing=infer_missing)


SimpleHeatSourceConfig.from_dict = _from_dict_with_legacy_aliases  # type: ignore[method-assign,assignment]


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

    cost_relevance = CostRelevance.PRICED

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
        my_display_config: DisplayConfig = DisplayConfig(),
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
            load_type=lt.LoadTypes.WARM_WATER,
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
            load_type=lt.LoadTypes.WARM_WATER,
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
        lines.append(f"Name: {self.config.component_id.name}")
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
        prorated = prorate_to_simulated_period(
            investment_in_euro=config.investment_costs_in_euro,
            co2_footprint_in_kg=config.device_co2_footprint_in_kg,
            maintenance_in_euro_per_year=config.maintenance_costs_in_euro_per_year,
            lifetime_in_years=config.lifetime_in_years,
            simulation_parameters=simulation_parameters,
        )
        capex_cost_data_class = CapexCostDataClass(
            capex_investment_cost_in_euro=config.investment_costs_in_euro,
            device_co2_footprint_in_kg=config.device_co2_footprint_in_kg,
            lifetime_in_years=config.lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=prorated.investment_for_simulated_period_in_euro,
            device_co2_footprint_for_simulated_period_in_kg=prorated.co2_footprint_for_simulated_period_in_kg,
            maintenance_costs_in_euro_per_year=config.maintenance_costs_in_euro_per_year,
            maintenance_cost_per_simulated_period_in_euro=prorated.maintenance_for_simulated_period_in_euro,
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
