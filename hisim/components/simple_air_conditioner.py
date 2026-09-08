"""Simple air-conditioning component with a Carnot-efficiency cooling model and hysteresis controller.

This module provides a physically-grounded, minimal air-conditioning component for HiSim.
The :class:`SimpleAirConditioner` computes a Carnot-limited coefficient of performance (COP)
for cooling and delivers a negative thermal power (heat removed from the space) when commanded
by the :class:`SimpleAirConditionerController`.  The controller uses a hysteresis band around a
24 °C setpoint to avoid rapid on/off cycling.

Sign convention (matching the existing ``AirConditioner``):
    * ``ThermalPowerDelivered`` is **negative** when cooling.
    * ``ModulatingPowerSignal`` is **-1.0** for cooling at full capacity and **0.0** when off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim.component import (
    CapexCostDataClass,
    OpexCostDataClass,
)
from hisim.config import ConfigBase, ComponentID, DisplayConfig
from hisim.components.configuration import EmissionFactorsAndCostsForFuelsConfig
from hisim.postprocessing.kpi_computation.kpi_structure import (
    KpiEntry,
    KpiTagEnumClass,
)
from hisim.simulationparameters import SimulationParameters
from hisim.loadtypes import LoadTypes, Units
from hisim.components.weather import Weather
from hisim.components.building import Building
from hisim import utils

__authors__ = "HiSim Project"
__copyright__ = "Copyright 2025, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "HiSim Project"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


# ==============================================================================
# Configuration
# ==============================================================================


@dataclass_json
@dataclass
class SimpleAirConditionerConfig(ConfigBase):
    """Configuration for the :class:`SimpleAirConditioner` component."""

    component_id: ComponentID
    nominal_cooling_power_w: float = 2000.0
    eta_carnot: float = 0.3
    temperature_epsilon_k: float = 0.01

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the main component class."""
        return SimpleAirConditioner.get_full_classname()

    @classmethod
    def get_default_simple_air_conditioner_config(
        cls, component_id: Optional[ComponentID] = None
    ) -> SimpleAirConditionerConfig:
        """Return a default configuration for the simple air conditioner."""
        if component_id is None:
            component_id = ComponentID(name="SimpleAirConditioner")
        return cls(
            component_id=component_id,
            nominal_cooling_power_w=2000.0,
            eta_carnot=0.3,
            temperature_epsilon_k=0.01,
        )


# ==============================================================================
# Component
# ==============================================================================


class SimpleAirConditioner(cp.Component):
    """Simple air-conditioning component with a Carnot-efficiency cooling model.

    The component reads outside and inside temperatures plus a modulating power
    signal from its controller.  When the signal is negative (cooling commanded)
    and the outside temperature exceeds the inside temperature by more than
    ``temperature_epsilon_k``, it computes a Carnot-limited COP and delivers
    negative thermal power (heat removed from the space).  Otherwise all outputs
    are zero.
    """

    # Input channel names
    TemperatureOutside: str = "TemperatureOutside"
    TemperatureIndoorAir: str = "TemperatureIndoorAir"
    ModulatingPowerSignal: str = "ModulatingPowerSignal"

    # Output channel names
    ThermalPowerDelivered: str = "ThermalPowerDelivered"
    ThermalEnergyDelivered: str = "ThermalEnergyDelivered"
    ElectricalPowerConsumption: str = "ElectricalPowerConsumption"
    ElectricalEnergyConsumption: str = "ElectricalEnergyConsumption"
    CoefficientOfPerformance: str = "CoefficientOfPerformance"
    RunningState: str = "RunningState"

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleAirConditionerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Initialize the simple air conditioner component."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        self.config: SimpleAirConditionerConfig = config
        self.simple_air_conditioner_config: SimpleAirConditionerConfig = config
        super().__init__(
            name=self.get_component_name(),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # --- Inputs ---
        self.t_out_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureOutside,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.t_indoor_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureIndoorAir,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )
        self.modulating_power_signal_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.ModulatingPowerSignal,
            LoadTypes.ANY,
            Units.PERCENT,
            True,
        )

        # --- Outputs ---
        self.thermal_power_generation_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalPowerDelivered,
            LoadTypes.HEATING,
            Units.WATT,
            output_description="Thermal power delivered (negative = cooling).",
        )
        self.thermal_energy_generation_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ThermalEnergyDelivered,
            LoadTypes.HEATING,
            Units.WATT_HOUR,
            output_description="Thermal energy delivered (negative = cooling).",
        )
        self.electrical_power_consumption_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricalPowerConsumption,
            LoadTypes.ELECTRICITY,
            Units.WATT,
            output_description="Electrical power consumption.",
        )
        self.electrical_energy_consumption_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ElectricalEnergyConsumption,
            LoadTypes.ELECTRICITY,
            Units.WATT_HOUR,
            output_description="Electrical energy consumption.",
        )
        self.cop_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.CoefficientOfPerformance,
            LoadTypes.ANY,
            Units.ANY,
            output_description="Coefficient of performance (Carnot-scaled).",
        )
        self.running_state_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.RunningState,
            LoadTypes.ON_OFF,
            Units.ANY,
            output_description="Running state (1 = cooling, 0 = off).",
        )

        # --- Default connections ---
        self.add_default_connections(self.get_default_connections_from_weather())
        self.add_default_connections(self.get_default_connections_from_building())
        self.add_default_connections(self.get_default_connections_from_controller())

    def get_default_connections_from_weather(self) -> list[cp.ComponentConnection]:
        """Connect to default weather component for outside temperature."""
        return [
            cp.ComponentConnection(
                self.TemperatureOutside,
                Weather.get_classname(),
                Weather.TemperatureOutside,
            )
        ]

    def get_default_connections_from_building(self) -> list[cp.ComponentConnection]:
        """Connect to default building component for indoor air temperature."""
        return [
            cp.ComponentConnection(
                self.TemperatureIndoorAir,
                Building.get_classname(),
                Building.TemperatureIndoorAir,
            )
        ]

    def get_default_connections_from_controller(self) -> list[cp.ComponentConnection]:
        """Connect to default controller for the modulating power signal."""
        return [
            cp.ComponentConnection(
                self.ModulatingPowerSignal,
                SimpleAirConditionerController.get_classname(),
                SimpleAirConditionerController.ModulatingPowerSignal,
            )
        ]

    # --- Lifecycle ---

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation (no preparation needed for this stateless component)."""
        pass

    def i_save_state(self) -> None:
        """Save state (component is stateless; nothing to save)."""
        pass

    def i_restore_state(self) -> None:
        """Restore state (component is stateless; nothing to restore)."""
        pass

    def i_doublecheck(
        self, timestep: int, stsv: cp.SingleTimeStepValues
    ) -> None:
        """Double-check (no checks needed for this component)."""
        pass

    def write_to_report(self) -> list[str]:
        """Write configuration to the simulation report."""
        return self.config.get_string_dict()

    # --- Simulation ---

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate one timestep.

        Computes the Carnot-limited COP for cooling and the corresponding
        electrical and thermal power outputs based on the modulating power
        signal from the controller.
        """
        if force_convergence:
            return

        t_out_c = stsv.get_input_value(self.t_out_channel)
        t_in_c = stsv.get_input_value(self.t_indoor_channel)
        modulation_signal = stsv.get_input_value(
            self.modulating_power_signal_channel
        )

        t_in_k = t_in_c + 273.15
        t_out_k = t_out_c + 273.15
        delta_t_in_k = t_out_k - t_in_k

        # modulation_signal < 0 means cooling (signed convention from existing AC)
        is_cooling = modulation_signal < 0

        if is_cooling and delta_t_in_k > self.config.temperature_epsilon_k:
            cop_carnot = t_in_k / delta_t_in_k
            cop_real = self.config.eta_carnot * cop_carnot
            # modulation_signal is negative; abs() gives the fraction, clamped to [0, 1]
            modulation_fraction = min(abs(modulation_signal), 1.0)
            thermal_cooling_w = (
                self.config.nominal_cooling_power_w * modulation_fraction
            )
            electric_w = thermal_cooling_w / cop_real
            thermal_power_delivered_w = -thermal_cooling_w
            running_state = 1
        else:
            cop_real = 0.0
            thermal_cooling_w = 0.0
            electric_w = 0.0
            thermal_power_delivered_w = 0.0
            running_state = 0

        seconds_per_timestep_in_s = self.my_simulation_parameters.seconds_per_timestep
        # Convert watts to watt-hours: W * seconds / 3600 = Wh (3.6e3 = 3600 s/h)
        thermal_energy_wh = thermal_power_delivered_w * seconds_per_timestep_in_s / 3.6e3
        electric_energy_wh = electric_w * seconds_per_timestep_in_s / 3.6e3

        stsv.set_output_value(
            self.thermal_power_generation_channel, thermal_power_delivered_w
        )
        stsv.set_output_value(
            self.thermal_energy_generation_channel, thermal_energy_wh
        )
        stsv.set_output_value(
            self.electrical_power_consumption_channel, electric_w
        )
        stsv.set_output_value(
            self.electrical_energy_consumption_channel, electric_energy_wh
        )
        stsv.set_output_value(self.cop_channel, cop_real)
        stsv.set_output_value(self.running_state_channel, running_state)

    # --- Cost / KPI ---

    @staticmethod
    def get_cost_capex(
        config: SimpleAirConditionerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:
        """Return capital expenditure (CAPEX) and CO2 footprint for the simulation duration."""
        seconds_per_year = 365 * 24 * 60 * 60
        duration_ratio = (
            simulation_parameters.duration.total_seconds() / seconds_per_year
        )

        investment_cost = 1500.0
        co2_footprint_in_kg = 100.0
        lifetime_in_years = 15

        capex_per_period = (investment_cost / lifetime_in_years) * duration_ratio
        co2_footprint_for_simulated_period_in_kg = (co2_footprint_in_kg / lifetime_in_years) * duration_ratio

        return CapexCostDataClass(
            capex_investment_cost_in_euro=investment_cost,
            device_co2_footprint_in_kg=co2_footprint_in_kg,
            lifetime_in_years=lifetime_in_years,
            capex_investment_cost_for_simulated_period_in_euro=capex_per_period,
            device_co2_footprint_for_simulated_period_in_kg=co2_footprint_for_simulated_period_in_kg,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER,
        )

    def get_cost_opex(
        self, all_outputs: list[cp.ComponentOutput], postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:
        """Return operational expenditure (OPEX) based on electricity consumption."""
        electricity_consumption_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if (
                output.component_name == self.component_name
                and output.field_name == self.ElectricalEnergyConsumption
                and output.unit == Units.WATT_HOUR
            ):
                electricity_consumption_kwh = round(
                    sum(postprocessing_results.iloc[:, index]) * 1e-3, 1
                )
                break

        emissions_and_cost_factors = (
            EmissionFactorsAndCostsForFuelsConfig.get_values_for_year(
                self.my_simulation_parameters.year,
                self.my_simulation_parameters.country,
            )
        )

        return OpexCostDataClass(
            opex_energy_cost_in_euro=electricity_consumption_kwh
            * emissions_and_cost_factors.electricity_costs_in_euro_per_kwh,
            opex_maintenance_cost_in_euro=self.calc_maintenance_cost(),
            co2_footprint_in_kg=electricity_consumption_kwh
            * emissions_and_cost_factors.electricity_footprint_in_kg_per_kwh,
            total_consumption_in_kwh=electricity_consumption_kwh,
            loadtype=LoadTypes.ELECTRICITY,
            kpi_tag=KpiTagEnumClass.AIR_CONDITIONER,
        )

    def get_component_kpi_entries(
        self,
        all_outputs: list[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> list[KpiEntry]:
        """Calculate KPIs for the simple air conditioner and return them as a list."""
        list_of_kpi_entries: list[KpiEntry] = []
        opex_dataclass = self.get_cost_opex(
            all_outputs=all_outputs,
            postprocessing_results=postprocessing_results,
        )
        capex_dataclass = self.get_cost_capex(
            self.config, self.my_simulation_parameters
        )

        # Energy-related KPIs
        electricity_consumption_kwh = KpiEntry(
            name="Electrical energy consumption",
            unit="kWh",
            value=opex_dataclass.total_consumption_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(electricity_consumption_kwh)

        thermal_energy_delivered_cooling_in_kwh: float = 0.0
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if (
                    output.field_name == self.ThermalEnergyDelivered
                    and output.unit == Units.WATT_HOUR
                ):
                    thermal_energy_delivered_cooling_in_kwh = round(
                        postprocessing_results.iloc[:, index][
                            postprocessing_results.iloc[:, index] < 0
                        ].sum()
                        * 1e-3,
                        1,
                    )
                    break

        thermal_energy_delivered_cooling_entry = KpiEntry(
            name="Thermal energy delivered - cooling",
            unit="kWh",
            value=thermal_energy_delivered_cooling_in_kwh,
            tag=opex_dataclass.kpi_tag,
            description=self.component_name,
        )
        list_of_kpi_entries.append(thermal_energy_delivered_cooling_entry)

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
            name="OPEX - Electricity costs",
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
            name="Total Costs (CAPEX for simulated period + OPEX energy and maintenance)",
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


# ==============================================================================
# Controller configuration
# ==============================================================================


@dataclass_json
@dataclass
class SimpleAirConditionerControllerConfig(ConfigBase):
    """Configuration for the :class:`SimpleAirConditionerController`."""

    component_id: ComponentID
    setpoint_temperature_c: float = 24.0
    deadband_k: float = 0.5

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the full class name of the associated controller class."""
        return SimpleAirConditionerController.get_full_classname()  # type: ignore[no-any-return]

    @classmethod
    def get_default_simple_air_conditioner_controller_config(
        cls, component_id: Optional[ComponentID] = None
    ) -> SimpleAirConditionerControllerConfig:
        """Return a default configuration for the simple air conditioner controller."""
        if component_id is None:
            component_id = ComponentID(name="SimpleAirConditionerController")
        return cls(
            component_id=component_id,
            setpoint_temperature_c=24.0,
            deadband_k=0.5,
        )


# ==============================================================================
# Controller state
# ==============================================================================


class SimpleAirConditionerControllerState:
    """Internal state of the :class:`SimpleAirConditionerController`."""

    def __init__(self, state: int = 0) -> None:
        """Initialize the controller state.

        Args:
            state: 1 = cooling, 0 = off.
        """
        self.state: int = state

    def clone(self) -> SimpleAirConditionerControllerState:
        """Return a deep copy of the current state."""
        return SimpleAirConditionerControllerState(self.state)


# ==============================================================================
# Controller
# ==============================================================================


class SimpleAirConditionerController(cp.Component):
    """Hysteresis controller for the :class:`SimpleAirConditioner`.

    Turns cooling on when the indoor temperature rises above
    ``setpoint_temperature_c + deadband_k`` and off when it falls below
    ``setpoint_temperature_c - deadband_k``.  Within the deadband the previous
    state is maintained (hysteresis) to prevent rapid cycling.

    The controller outputs a signed modulating power signal:
    ``-1.0`` for cooling at full capacity, ``0.0`` when off.
    """

    # Input channel names
    TemperatureIndoorAir: str = "TemperatureIndoorAir"

    # Output channel names
    ModulatingPowerSignal: str = "ModulatingPowerSignal"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: SimpleAirConditionerControllerConfig,
        my_display_config: DisplayConfig | None = None,
    ) -> None:
        """Initialize the simple air conditioner controller component."""
        if my_display_config is None:
            my_display_config = DisplayConfig()
        self.config: SimpleAirConditionerControllerConfig = config
        self.my_simulation_parameters: SimulationParameters = my_simulation_parameters
        super().__init__(
            name=self.get_component_name(),
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        # State initialization
        self.state: SimpleAirConditionerControllerState = SimpleAirConditionerControllerState(0)
        self.previous_state: SimpleAirConditionerControllerState = self.state.clone()

        # --- Inputs ---
        self.indoor_air_temperature_channel: cp.ComponentInput = self.add_input(
            self.component_name,
            self.TemperatureIndoorAir,
            LoadTypes.TEMPERATURE,
            Units.CELSIUS,
            True,
        )

        # --- Outputs ---
        self.operation_modulating_signal_channel: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.ModulatingPowerSignal,
            LoadTypes.ANY,
            Units.PERCENT,
            output_description="Modulating power signal for the simple air conditioner (-1.0 = cooling, 0.0 = off).",
        )

        # --- Default connections ---
        self.add_default_connections(self.get_default_connections_from_building())

    def get_default_connections_from_building(self) -> list[cp.ComponentConnection]:
        """Connect the controller input to the building indoor air temperature."""
        return [
            cp.ComponentConnection(
                self.TemperatureIndoorAir,
                Building.get_classname(),
                Building.TemperatureIndoorAir,
            )
        ]

    # --- Lifecycle ---

    def i_prepare_simulation(self) -> None:
        """Prepare the simulation (no preparation needed)."""
        pass

    def i_save_state(self) -> None:
        """Save the current state before a timestep iteration."""
        self.previous_state = self.state.clone()

    def i_restore_state(self) -> None:
        """Restore the state from the beginning of the timestep iteration."""
        self.state = self.previous_state.clone()

    def i_doublecheck(
        self, timestep: int, stsv: cp.SingleTimeStepValues
    ) -> None:
        """Double-check (no checks needed)."""
        pass

    def write_to_report(self) -> list[str]:
        """Write configuration to the simulation report."""
        return self.config.get_string_dict() + [
            "Simple Air Conditioner Controller",
            f"Setpoint temperature: {self.config.setpoint_temperature_c} °C",
            f"Deadband: {self.config.deadband_k} K",
        ]

    # --- Simulation ---

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulate one controller timestep using hysteresis control."""
        if force_convergence:
            return

        t_in_in_celsius = stsv.get_input_value(self.indoor_air_temperature_channel)
        upper_threshold_in_celsius = self.config.setpoint_temperature_c + self.config.deadband_k
        lower_threshold_in_celsius = self.config.setpoint_temperature_c - self.config.deadband_k

        if t_in_in_celsius > upper_threshold_in_celsius:
            self.state.state = 1  # turn on cooling
        elif t_in_in_celsius < lower_threshold_in_celsius:
            self.state.state = 0  # turn off cooling
        # else: within deadband -> maintain previous state (hysteresis)

        # Encode state as signed modulating signal: 0.0 = off, -1.0 = cooling
        if self.state.state == 1:
            modulation = -1.0
        else:
            modulation = 0.0
        stsv.set_output_value(self.operation_modulating_signal_channel, modulation)

    # --- Cost / KPI (controller has no direct costs) ---

    @staticmethod
    def get_cost_capex(
        config: SimpleAirConditionerControllerConfig,
        simulation_parameters: SimulationParameters,
    ) -> CapexCostDataClass:  # pylint: disable=unused-argument
        """Return default CAPEX (controller has no direct investment cost)."""
        return CapexCostDataClass.get_default_capex_cost_data_class()

    def get_cost_opex(
        self, all_outputs: list[cp.ComponentOutput], postprocessing_results: pd.DataFrame
    ) -> OpexCostDataClass:  # pylint: disable=unused-argument
        """Return default OPEX (controller has no direct operational cost)."""
        return OpexCostDataClass.get_default_opex_cost_data_class()

    def get_component_kpi_entries(
        self,
        all_outputs: list[cp.ComponentOutput],
        postprocessing_results: pd.DataFrame,
    ) -> list[KpiEntry]:  # pylint: disable=unused-argument
        """Return an empty KPI list (controller has no direct KPIs)."""
        return []
