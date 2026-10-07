"""Mock components for the mock assemblies: small, deterministic, and fully declared.

A mock assembly or a mock class exists only for a shape the real library cannot show
(``assemblies_spec.md`` §9.3); everything that can run on a real assembly moves to the real library
when it exists (§13 step 4). The names say so: the family ``mock/``, the classes ``Mock*``, each with
one line naming the real class or role it stands in for.

Like every HiSim component, each mock creates its inputs, its outputs and its default connections in
its constructor and nowhere else; the wiring stage of a build reads them off the constructed instance.
Its configuration declares the unit of every field an assembly parameter feeds (D16 b).
:class:`MockBareDevice` declares no default connections at all, for the wiring refusal of a port
lowered into a member without them.

The physics is a toy: a weather series, an occupancy drawing hot water, a PV array producing from the
temperature, a tank losing heat and filled by a heater a thermostat switches, and an energy manager
whose modifier raises the thermostat's set point. Every value converges in a few iterations, so a
one-day run is fast.

For the circuit, carrier and fact ports a gas boiler charges a cylinder over the ``dhw`` circuit —
the boiler owns ``MassFlowDhw`` and ``SupplyTemperatureDhw``, the cylinder ``ReturnTemperatureDhw``,
each reading the other's by its default connections — and burns natural gas a gas meter observes
through the default feed its constructor declares; a battery is sized from the arrays' peak power.
For the selectors an energy manager and an electricity meter declare their feeds as the real ones
do, the manager at the real controller's weights (``L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS``);
a hot-water heater beside the space heaters is ranked at the hot-water weight.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentConnection, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.config import ComponentID, ConfigBase, DisplayConfig, FactContribution, Many, Sizable, Size, Sum
from hisim.config import concrete, preset, sized_field
from hisim.config.channels import DispatchRule, DynamicConnectionChannel
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicComponentConnection,
    DynamicConnectionInput,
    DynamicConnectionOutput,
)
from hisim.energy_port import EnergyPort
from hisim.economics.facts import CostRelevance
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters

#: The metadata key a configuration field declares its unit under (``SizedFieldMetadata.UNIT``).
UNIT = "unit"


class KpiAggregation(enum.Enum):
    """How a summed KPI of a mock turns its output's series into one number, whatever the resolution."""

    #: An energy per step in Wh, summed and given in kWh.
    ENERGY_IN_KWH = "energy"
    #: A power in W, integrated over the steps' length and given in kWh.
    POWER_IN_KWH = "power"
    #: The mean of the series, in the output's own unit.
    MEAN = "mean"

    def of(self, series: pd.Series, seconds_per_timestep: int) -> float:
        """The KPI of one output's series."""
        if self is KpiAggregation.ENERGY_IN_KWH:
            return float(series.sum()) * 1e-3
        if self is KpiAggregation.POWER_IN_KWH:
            return float(series.sum()) * seconds_per_timestep / 3600.0 * 1e-3
        return float(series.mean())


class MockComponent(Component):
    """The shared behaviour of every mock: helpers its constructor adds ports and default connections with."""

    cost_relevance = CostRelevance.FREE_OF_COST

    #: KPI name to ``(output, aggregation)``: the KPI is the output's series over the run, aggregated.
    SUM_KPIS: Dict[str, Tuple[str, KpiAggregation]] = {}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: Any) -> None:
        """Builds the component; the subclass's constructor adds its ports and default connections."""
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.ports_in: Dict[str, ComponentInput] = {}
        self.ports_out: Dict[str, ComponentOutput] = {}

    def input_port(self, name: str, load_type: lt.LoadTypes, unit: lt.Units, *, mandatory: bool = True) -> None:
        """Adds one input (``add_input``)."""
        self.ports_in[name] = self.add_input(self.component_name, name, load_type, unit, mandatory)

    def output_port(
        self,
        name: str,
        load_type: lt.LoadTypes,
        unit: lt.Units,
        postprocessing_flag: Optional[List[Any]] = None,
        energy_port: Optional[EnergyPort] = None,
    ) -> None:
        """Adds one output (``add_output``), with its post-processing flags and energy port where it has them."""
        self.ports_out[name] = self.add_output(
            self.component_name,
            name,
            load_type,
            unit,
            postprocessing_flag=postprocessing_flag,
            output_description=name,
            energy_port=energy_port,
        )

    def defaults_from(self, source_class: str, wires: Dict[str, str]) -> None:
        """Adds the default connections from one source class, ``{input: output}`` (``add_default_connections``)."""
        self.add_default_connections(
            [ComponentConnection(target, source_class, output) for target, output in wires.items()]
        )

    def value(self, stsv: SingleTimeStepValues, name: str) -> float:
        """The current value of one input (0 when it is optional and unconnected)."""
        port = self.ports_in[name]
        if port.source_output is None:
            return 0.0
        return float(stsv.get_input_value(port))

    def set(self, stsv: SingleTimeStepValues, name: str, value: float) -> None:
        """Sets one output."""
        stsv.set_output_value(self.ports_out[name], value)

    def i_prepare_simulation(self) -> None:
        """Nothing to prepare."""

    def i_save_state(self) -> None:
        """Stateless."""

    def i_restore_state(self) -> None:
        """Stateless."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Nothing to check."""

    def get_cost_facts(self) -> None:
        """Free of cost."""
        return None

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """The aggregates :attr:`SUM_KPIS` declares, each over the run, at the run's resolution."""
        entries: List[KpiEntry] = []
        for name, (output, aggregation) in self.SUM_KPIS.items():
            column = next(
                (
                    item.get_pretty_name()
                    for item in all_outputs
                    if item.component_name == self.component_name and item.field_name == output
                ),
                None,
            )
            total = 0.0
            if column is not None and column in postprocessing_results:
                total = aggregation.of(
                    postprocessing_results[column], self.my_simulation_parameters.seconds_per_timestep
                )
            entries.append(KpiEntry(name=name, unit="kWh", value=total, tag=KpiTagEnumClass.GENERAL))
        return entries


# ------------------------------------------------------------------------------------------ weather


@dataclass_json
@dataclass
class MockWeatherConfig(ConfigBase):
    """A constant-ish outside temperature."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockWeather"

    component_id: ComponentID
    mean_temperature_in_celsius: float = field(default=5.0, metadata={UNIT: lt.Units.CELSIUS})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockWeatherConfig":
        """A winter day around 5 °C."""
        return cls(component_id=ComponentID(name=name))


class MockWeather(MockComponent):
    """Outputs the outside temperature.

    Stands in for the real ``Weather`` in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockWeatherConfig) -> None:
        """Builds the weather."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A daily wave around the mean."""
        self.set(stsv, "TemperatureOutside", self.config.mean_temperature_in_celsius + (timestep % 96) / 24.0)


# ---------------------------------------------------------------------------------------- occupancy


@dataclass_json
@dataclass
class MockOccupancyConfig(ConfigBase):
    """Residents drawing hot water and electricity."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockOccupancy"

    component_id: ComponentID
    residents: int = 2
    #: Whether the residents' electricity is an output at all; without it the occupancy stands in for a
    #: component built without an output its observers declare a feed from (a heat pump without its DHW side).
    with_electricity: bool = True

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockOccupancyConfig":
        """Two residents."""
        return cls(component_id=ComponentID(name=name))


class MockOccupancy(MockComponent):
    """Outputs a hot-water draw and an electricity consumption.

    Stands in for the residents' occupancy (``UtspLpgConnector``) in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockOccupancyConfig) -> None:
        """Builds the occupancy."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("WaterDemand", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP)
        if config.with_electricity:
            self.output_port("ElectricityConsumption", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Draws in the morning and the evening."""
        draw = 10.0 if timestep % 96 in (28, 29, 76, 77) else 0.0
        self.set(stsv, "WaterDemand", draw)
        if self.config.with_electricity:
            self.set(stsv, "ElectricityConsumption", 150.0 * self.config.residents)


# ------------------------------------------------------------------------------------------------ pv


@dataclass_json
@dataclass
class MockPVSystemConfig(ConfigBase):
    """A PV array: its peak power — or the share of the roof it covers instead — and its orientation.

    As the real ``PVSystemConfig``: the preset leaves the power open and gives a share, and a stated
    power wins over the share, so an import stating either one overrides only that field (G9: a
    ``none`` writes no line, and the preset's value of the other field stays).
    """

    MAIN_CLASS = "tests.assemblies.mock_components.MockPVSystem"

    #: The peak power of the whole roof, which a share of it is taken of.
    ROOF_PEAK_POWER_IN_WATT: ClassVar[float] = 20000.0

    component_id: ComponentID
    #: The peak power; ``None`` takes the share of the roof's.
    power_in_watt: Optional[float] = field(default=None, metadata={UNIT: lt.Units.WATT})
    #: The share of the roof the array covers, read when no power is given; dimensionless (5 kWp of the roof).
    share_of_roof: Optional[float] = field(default=0.25, metadata={UNIT: lt.Units.ANY})
    azimuth: float = field(default=180.0, metadata={UNIT: lt.Units.DEGREES})
    tilt: float = field(default=30.0, metadata={UNIT: lt.Units.DEGREES})
    #: A field without a declared unit, for the refusal of a fed field without one.
    shading_factor: float = 1.0

    #: The array's peak power, which a battery beside it is sized from.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=("pv_peak_power_in_watt",),
            compute=lambda config, ctx: {"pv_peak_power_in_watt": config.peak_power_in_watt()},
        ),
    )

    def peak_power_in_watt(self) -> float:
        """The peak power: the one given, else the share of the roof's."""
        if self.power_in_watt is not None:
            return self.power_in_watt
        if self.share_of_roof is None:
            raise ValueError(f"{self.component_id.name} gives neither power_in_watt nor share_of_roof.")
        return self.share_of_roof * self.ROOF_PEAK_POWER_IN_WATT

    @preset
    @classmethod
    def preset_rooftop(cls, name: str) -> "MockPVSystemConfig":
        """A south-facing roof array."""
        return cls(component_id=ComponentID(name=name))


class MockPVSystem(MockComponent):
    """Produces electricity from the outside temperature (a toy stand-in for irradiance).

    Stands in for the real ``PVSystem`` in the mock assemblies.
    """

    PRODUCTION_KPI = "PV production"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockPVSystemConfig) -> None:
        """Builds the array."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        # Flagged as PV production, so the derived KPI "PV production" (General) sums every array.
        self.output_port(
            "ElectricityOutput",
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            postprocessing_flag=[lt.InandOutputType.ELECTRICITY_PRODUCTION, lt.ComponentType.PV],
        )
        self.defaults_from("MockWeather", {"TemperatureOutside": "TemperatureOutside"})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production proportional to power, orientation and temperature."""
        orientation = 1.0 - abs(self.config.azimuth - 180.0) / 360.0
        temperature = self.value(stsv, "TemperatureOutside")
        peak = self.config.peak_power_in_watt()
        self.set(stsv, "ElectricityOutput", peak * orientation * max(temperature, 0.0) / 50.0)

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """The energy the array produced, in kWh."""
        column = next(
            (
                output.get_pretty_name()
                for output in all_outputs
                if output.component_name == self.component_name and output.field_name == "ElectricityOutput"
            ),
            None,
        )
        produced = 0.0
        if column is not None and column in postprocessing_results:
            seconds = self.my_simulation_parameters.seconds_per_timestep
            produced = float(postprocessing_results[column].sum()) * seconds / 3600.0 / 1000.0
        return [KpiEntry(name=self.PRODUCTION_KPI, unit="kWh", value=produced, tag=KpiTagEnumClass.ROOFTOP_PV)]


# ---------------------------------------------------------------------------------------------- tank


@dataclass_json
@dataclass
class MockTankConfig(ConfigBase):
    """A hot-water tank."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockTank"

    component_id: ComponentID
    volume_in_liter: float = field(default=150.0, metadata={UNIT: lt.Units.LITER})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockTankConfig":
        """A 150 l tank."""
        return cls(component_id=ComponentID(name=name))


class MockTank(MockComponent):
    """Mixes the heater's power and the residents' draw into a temperature.

    Stands in for a hot-water storage tank in the mock assemblies.
    """

    STANDBY_KPI = "Standby heat losses"

    #: The standby loss over the run in kWh (a power in W summed over 15-minute steps).
    SUM_KPIS = {STANDBY_KPI: ("HeatLoss", KpiAggregation.POWER_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockTankConfig) -> None:
        """Builds the tank."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("WaterDemand", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP)
        self.input_port("ThermalPower", lt.LoadTypes.HEATING, lt.Units.WATT)
        self.output_port("WaterTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        self.output_port("HeatLoss", lt.LoadTypes.HEATING, lt.Units.WATT)
        self.defaults_from("MockOccupancy", {"WaterDemand": "WaterDemand"})
        self.defaults_from("MockHeater", {"ThermalPower": "ThermalPower"})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A quasi-static balance: heat in minus draw and standby loss."""
        loss = 0.2 * self.config.volume_in_liter
        temperature = 40.0 + (self.value(stsv, "ThermalPower") - loss) / 100.0 - self.value(stsv, "WaterDemand") / 10.0
        self.set(stsv, "WaterTemperature", temperature)
        self.set(stsv, "HeatLoss", loss)


# -------------------------------------------------------------------------------------------- heater


@dataclass_json
@dataclass
class MockHeaterConfig(ConfigBase):
    """An electric heater."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockHeater"

    component_id: ComponentID
    power_in_watt: float = field(default=2000.0, metadata={UNIT: lt.Units.WATT})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockHeaterConfig":
        """A 2 kW heater."""
        return cls(component_id=ComponentID(name=name))


class MockHeater(MockComponent):
    """Heats when its controller says so.

    Stands in for an electric heater in the mock assemblies.
    """

    ENERGY_KPI = "Heater energy"

    #: The electricity the heater drew over the run in kWh (a power in W summed over 15-minute steps).
    SUM_KPIS = {ENERGY_KPI: ("ElectricityInput", KpiAggregation.POWER_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockHeaterConfig) -> None:
        """Builds the heater."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("Signal", lt.LoadTypes.ON_OFF, lt.Units.ANY, mandatory=False)
        # Electricity in, space-heating heat out, one for one: its balance closes every step.
        self.output_port(
            "ThermalPower",
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            energy_port=EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        )
        self.output_port(
            "ElectricityInput",
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            energy_port=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.ELECTRICITY),
        )
        self.defaults_from("MockController", {"Signal": "Signal"})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Full power when on."""
        power = self.config.power_in_watt * (1.0 if self.value(stsv, "Signal") > 0.5 else 0.0)
        self.set(stsv, "ThermalPower", power)
        self.set(stsv, "ElectricityInput", power)


@dataclass_json
@dataclass
class MockWaterHeaterConfig(MockHeaterConfig):
    """An electric hot-water heater."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockWaterHeater"


class MockWaterHeater(MockHeater):
    """The heater on the hot-water side; an energy manager ranks it at the hot-water weight.

    Stands in for an electric hot-water heater in the mock assemblies.
    """

    def __init__(  # pylint: disable=useless-parent-delegation  # the annotation names the config class
        self, my_simulation_parameters: SimulationParameters, config: MockWaterHeaterConfig
    ) -> None:
        """Builds the heater."""
        super().__init__(my_simulation_parameters, config)


# ---------------------------------------------------------------------------------------- controller


@dataclass_json
@dataclass
class MockControllerConfig(ConfigBase):
    """A thermostat on the tank."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockController"

    component_id: ComponentID
    set_temperature_in_celsius: float = field(default=45.0, metadata={UNIT: lt.Units.CELSIUS})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockControllerConfig":
        """45 °C."""
        return cls(component_id=ComponentID(name=name))

    @preset
    @classmethod
    def preset_eco(cls, name: str) -> "MockControllerConfig":
        """40 °C."""
        return cls(component_id=ComponentID(name=name), set_temperature_in_celsius=40.0)


class MockController(MockComponent):
    """Switches the heater on below the set point, which an energy manager may raise.

    Stands in for a heater's thermostat (an L1 controller) in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockControllerConfig) -> None:
        """Builds the controller."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("TankTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, mandatory=False)
        self.input_port("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN, mandatory=False)
        self.output_port("Signal", lt.LoadTypes.ON_OFF, lt.Units.ANY)
        self.defaults_from("MockTank", {"TankTemperature": "WaterTemperature"})
        self.defaults_from("MockEms", {"Modifier": "Modifier"})
        self.defaults_from("MockEnergyManager", {"Modifier": "Modifier"})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """On in one step of four, longer the higher the (raised) set point; off above a safety limit.

        The schedule depends on the set point rather than on the tank's temperature in the same
        step, so the toy loop of thermostat, heater and tank converges at once.
        """
        target = self.config.set_temperature_in_celsius + self.value(stsv, "Modifier")
        scheduled = timestep % 4 == 0 or (target > 46.0 and timestep % 4 == 1)
        safe = self.value(stsv, "TankTemperature") < 1000.0
        self.set(stsv, "Signal", 1.0 if scheduled and safe else 0.0)


# ------------------------------------------------------------------------------------------------ ems


@dataclass_json
@dataclass
class MockEmsConfig(ConfigBase):
    """An energy manager raising set points by a fixed offset."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockEms"

    component_id: ComponentID
    offset_in_kelvin: float = field(default=2.0, metadata={UNIT: lt.Units.KELVIN})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockEmsConfig":
        """Raises by 2 K."""
        return cls(component_id=ComponentID(name=name))


class MockEms(MockComponent):
    """Outputs a set-point modifier.

    Stands in for an energy manager in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockEmsConfig) -> None:
        """Builds the energy manager."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A constant offset."""
        self.set(stsv, "Modifier", self.config.offset_in_kelvin)


# -------------------------------------------------------------------------------------- bare device


@dataclass_json
@dataclass
class MockBareDeviceConfig(ConfigBase):
    """A device with one input and no default connections, for the refusal that needs one."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockBareDevice"

    component_id: ComponentID
    rating_in_watt: float = 100.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockBareDeviceConfig":
        """Nothing to configure."""
        return cls(component_id=ComponentID(name=name))


class MockBareDevice(MockComponent):
    """Takes the outside temperature, but declares no default connections from any class.

    Stands in for a component class whose author has not yet added the default connections a port
    lowers to; a port binding it to a weather station is refused once it is constructed.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockBareDeviceConfig) -> None:
        """Builds the device: one input, no default connections."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, mandatory=False)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Nothing."""


# --------------------------------------------------------------------------------- the dhw circuit

#: The ``dhw`` circuit's three outputs (``assemblies_spec.md`` §11.1).
MASS_FLOW_DHW = "MassFlowDhw"
SUPPLY_TEMPERATURE_DHW = "SupplyTemperatureDhw"
RETURN_TEMPERATURE_DHW = "ReturnTemperatureDhw"


@dataclass_json
@dataclass
class MockBoilerConfig(ConfigBase):
    """A gas boiler charging a cylinder over the dhw circuit."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockBoiler"

    component_id: ComponentID
    power_in_watt: float = field(default=3000.0, metadata={UNIT: lt.Units.WATT})
    efficiency: float = field(default=0.9, metadata={UNIT: lt.Units.ANY})

    @preset
    @classmethod
    def preset_condensing(cls, name: str) -> "MockBoilerConfig":
        """A 3 kW condensing boiler."""
        return cls(component_id=ComponentID(name=name))


class MockBoiler(MockComponent):
    """Owns the dhw circuit's mass flow and supply leg, reads its return leg, and burns natural gas.

    Every step in two it heats at its power; the gas it burns is that heat over its efficiency and
    the rest leaves as flue loss, so its energy ports — gas in, heat out, flue loss — balance.
    Stands in for the real ``GenericBoiler`` in the mock assemblies.
    """

    FUEL_KPI = "Boiler fuel"

    SUM_KPIS = {FUEL_KPI: ("FuelUse", KpiAggregation.ENERGY_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockBoilerConfig) -> None:
        """Builds the boiler: the circuit's mass flow and supply leg out, its return leg in, gas in."""
        super().__init__(my_simulation_parameters, config)
        self.input_port(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        self.output_port(MASS_FLOW_DHW, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
        self.output_port(SUPPLY_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        self.output_port(
            "ThermalPowerDhw",
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            energy_port=EnergyPort(
                lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT, peer_output=MASS_FLOW_DHW
            ),
        )
        self.output_port(
            "FuelUse",
            lt.LoadTypes.GAS,
            lt.Units.WATT_HOUR,
            energy_port=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.NATURAL_GAS, peer_output="FuelUse"),
        )
        self.output_port(
            "FlueLoss",
            lt.LoadTypes.GAS,
            lt.Units.WATT,
            energy_port=EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.NATURAL_GAS),
        )
        self.defaults_from("MockCylinder", {RETURN_TEMPERATURE_DHW: RETURN_TEMPERATURE_DHW})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Heats one step in two at full power, supplying ten kelvin above the return leg."""
        heat = self.config.power_in_watt if timestep % 2 == 0 else 0.0
        fuel_power = heat / self.config.efficiency
        self.set(stsv, MASS_FLOW_DHW, heat / (4180.0 * 10.0))
        self.set(stsv, SUPPLY_TEMPERATURE_DHW, self.value(stsv, RETURN_TEMPERATURE_DHW) + 10.0)
        self.set(stsv, "ThermalPowerDhw", heat)
        self.set(stsv, "FuelUse", fuel_power * self.my_simulation_parameters.seconds_per_timestep / 3600.0)
        self.set(stsv, "FlueLoss", fuel_power - heat)


@dataclass_json
@dataclass
class MockCombiBurnerConfig(ConfigBase):
    """A gas burner with three fuel outputs."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockCombiBurner"

    component_id: ComponentID
    power_in_watt: float = field(default=1000.0, metadata={UNIT: lt.Units.WATT})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockCombiBurnerConfig":
        """A 1 kW burner per fuel output."""
        return cls(component_id=ComponentID(name=name))


class MockCombiBurner(MockComponent):
    """Burns natural gas on three outputs, for space heating, hot water and a pilot flame.

    The gas meter declares a feed for each of the three, so an assembly naming two of them shows
    that only the named outputs are metered (D30). It heats nothing the tests read.
    """

    #: The three fuel outputs, each an energy port taking natural gas in.
    FUEL_OUTPUTS: Tuple[str, ...] = ("FuelSh", "FuelDhw", "FuelPilot")

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockCombiBurnerConfig) -> None:
        """Builds the burner's three fuel outputs."""
        super().__init__(my_simulation_parameters, config)
        for name in self.FUEL_OUTPUTS:
            self.output_port(
                name,
                lt.LoadTypes.GAS,
                lt.Units.WATT_HOUR,
                energy_port=EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.NATURAL_GAS, peer_output=name),
            )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Burns its power on every output."""
        for name in self.FUEL_OUTPUTS:
            energy = self.config.power_in_watt * self.my_simulation_parameters.seconds_per_timestep / 3600.0
            self.set(stsv, name, energy)


@dataclass_json
@dataclass
class MockCylinderConfig(ConfigBase):
    """A hot-water cylinder charged over the dhw circuit."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockCylinder"

    component_id: ComponentID
    volume_in_liter: float = field(default=200.0, metadata={UNIT: lt.Units.LITER})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockCylinderConfig":
        """A 200 l cylinder."""
        return cls(component_id=ComponentID(name=name))


class MockCylinder(MockComponent):
    """Owns the dhw circuit's return leg and reads the boiler's mass flow and supply leg.

    Stands in for an indirectly heated hot-water cylinder in the mock assemblies.
    """

    HEAT_KPI = "Cylinder heat received"

    SUM_KPIS = {HEAT_KPI: ("HeatReceived", KpiAggregation.POWER_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockCylinderConfig) -> None:
        """Builds the cylinder: the circuit's mass flow and supply leg in, its return leg out."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("WaterDemand", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP)
        self.input_port(MASS_FLOW_DHW, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
        self.input_port(SUPPLY_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        self.output_port(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        self.output_port("HeatReceived", lt.LoadTypes.HEATING, lt.Units.WATT)
        self.defaults_from("MockOccupancy", {"WaterDemand": "WaterDemand"})
        self.defaults_from("MockBoiler", {MASS_FLOW_DHW: MASS_FLOW_DHW, SUPPLY_TEMPERATURE_DHW: SUPPLY_TEMPERATURE_DHW})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Returns water the colder the more is drawn, and receives the circuit's heat."""
        returned = 40.0 - self.value(stsv, "WaterDemand") / 10.0 - self.config.volume_in_liter / 1000.0
        self.set(stsv, RETURN_TEMPERATURE_DHW, returned)
        received = 4180.0 * self.value(stsv, MASS_FLOW_DHW) * (self.value(stsv, SUPPLY_TEMPERATURE_DHW) - returned)
        self.set(stsv, "HeatReceived", received)


# ------------------------------------------------------------------------------------------ battery


@dataclass_json
@dataclass
class MockBatteryConfig(ConfigBase):
    """A battery sized from the PV peak power beside it, with its unit on the sized field."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockBattery"

    component_id: ComponentID
    #: One kWh per kWp of the array it is bound to, unless pinned.
    capacity_in_kwh: Sizable[float] = sized_field(
        rule=(Size.PV_PEAK_POWER_IN_WATT * 1e-3).rounded(2), unit=lt.Units.KWH
    )

    @preset
    @classmethod
    def preset_sized_to_pv(cls, name: str) -> "MockBatteryConfig":
        """Capacity AUTO."""
        return cls(component_id=ComponentID(name=name))


class MockBattery(MockComponent):
    """Publishes its capacity, and draws the power an energy manager writes into ``LoadingPowerInput``.

    The toy has no state: the power it draws (positive) or delivers (negative) is the requested
    power, limited to its capacity in kWh taken as kW; without a controller it idles. Stands in for
    the real ``Battery`` in the mock assemblies.
    """

    CAPACITY_KPI = "Battery capacity"

    SUM_KPIS = {CAPACITY_KPI: ("Capacity", KpiAggregation.MEAN)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockBatteryConfig) -> None:
        """Builds the battery: the charging power in, the capacity and the AC power out."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("LoadingPowerInput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, mandatory=False)
        self.output_port("Capacity", lt.LoadTypes.ANY, lt.Units.KWH)
        self.output_port("AcBatteryPowerUsed", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """The capacity, and the requested power within it."""
        capacity = concrete(self.config.capacity_in_kwh)
        limit = 1000.0 * capacity
        self.set(stsv, "Capacity", capacity)
        self.set(stsv, "AcBatteryPowerUsed", max(-limit, min(limit, self.value(stsv, "LoadingPowerInput"))))


@dataclass_json
@dataclass
class MockArrayBatteryConfig(ConfigBase):
    """A battery sized to every PV array its sources list names, summed (``assemblies_spec.md`` §6)."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockArrayBattery"

    component_id: ComponentID
    #: One kWh per kWp of all arrays it is bound to, unless pinned.
    capacity_in_kwh: Sizable[float] = sized_field(
        rule=(Sum(Many(Size.PV_PEAK_POWER_IN_WATT)) * 1e-3).rounded(2), unit=lt.Units.KWH
    )

    @preset
    @classmethod
    def preset_sized_to_all_arrays(cls, name: str) -> "MockArrayBatteryConfig":
        """Capacity AUTO: the sum over every array."""
        return cls(component_id=ComponentID(name=name))


class MockArrayBattery(MockBattery):
    """The mock battery sized by a sum over the arrays; stands in for a battery beside several arrays."""

    def __init__(  # pylint: disable=useless-parent-delegation  # the annotation names the config class
        self, my_simulation_parameters: SimulationParameters, config: MockArrayBatteryConfig
    ) -> None:
        """Builds the battery."""
        super().__init__(my_simulation_parameters, config)  # type: ignore[arg-type]


# ------------------------------------------------------------------- observers: meters and a manager


class MockAggregator(DynamicComponent):
    """The shared behaviour of the mock observers: outputs and dynamic default connections from the constructor.

    Like the real meters and energy manager, a mock observer states what it observes in its
    constructor — one ``add_dynamic_default_connections`` call per source class — and nowhere else.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    #: KPI name to ``(output, aggregation)``.
    SUM_KPIS: Dict[str, Tuple[str, KpiAggregation]] = {}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: Any) -> None:
        """Builds the dynamic component; the subclass's constructor adds its outputs and its feeds."""
        self.my_component_inputs: List[DynamicConnectionInput] = []
        self.my_component_outputs: List[DynamicConnectionOutput] = []
        self.config = config
        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.ports_out: Dict[str, ComponentOutput] = {}

    def output_port(self, name: str, load_type: lt.LoadTypes, unit: lt.Units) -> None:
        """Adds one output (``add_output``)."""
        self.ports_out[name] = self.add_output(self.component_name, name, load_type, unit, output_description=name)

    def observe(
        self,
        source: type,
        output: Union[str, Sequence[str]],
        tags: List[Any],
        weight: int,
        load_type: lt.LoadTypes = lt.LoadTypes.ELECTRICITY,
    ) -> None:
        """Declares the feeds of one or several outputs of one source class (``add_dynamic_default_connections``)."""
        self.add_dynamic_default_connections(
            [
                DynamicComponentConnection(
                    source_component_class=source,
                    source_class_name=source.__name__,
                    source_component_field_name=name,
                    source_load_type=load_type,
                    source_unit=lt.Units.WATT_HOUR if load_type == lt.LoadTypes.GAS else lt.Units.WATT,
                    source_tags=tags,
                    source_weight=weight,
                )
                for name in ([output] if isinstance(output, str) else output)
            ]
        )

    def channel_sum(self, stsv: SingleTimeStepValues, key: str) -> float:
        """The sum of one channel's inputs."""
        return float(sum(stsv.get_input_value(component_input=item) for item in self.get_channel_inputs(key)))

    def i_prepare_simulation(self) -> None:
        """Nothing to prepare."""

    def i_save_state(self) -> None:
        """Stateless."""

    def i_restore_state(self) -> None:
        """Stateless."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Nothing to check."""

    def get_cost_facts(self) -> None:
        """Free of cost."""
        return None

    def get_component_kpi_entries(self, all_outputs: List, postprocessing_results: pd.DataFrame) -> List[KpiEntry]:
        """The aggregates :attr:`SUM_KPIS` declares, each over the run."""
        entries: List[KpiEntry] = []
        for name, (output, aggregation) in self.SUM_KPIS.items():
            column = next(
                (
                    item.get_pretty_name()
                    for item in all_outputs
                    if item.component_name == self.component_name and item.field_name == output
                ),
                None,
            )
            total = 0.0
            if column is not None and column in postprocessing_results:
                total = aggregation.of(
                    postprocessing_results[column], self.my_simulation_parameters.seconds_per_timestep
                )
            entries.append(KpiEntry(name=name, unit="kWh", value=total, tag=KpiTagEnumClass.GENERAL))
        return entries


#: The weight of a feed an observer only measures.
MEASURED = DynamicConnectionChannel.MONITORED_ONLY_WEIGHT


@dataclass_json
@dataclass
class MockGasMeterConfig(ConfigBase):
    """A gas meter."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockGasMeter"

    component_id: ComponentID
    #: A calibration factor of the reading; dimensionless.
    calibration: float = field(default=1.0, metadata={UNIT: lt.Units.ANY})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockGasMeterConfig":
        """A calibrated meter."""
        return cls(component_id=ComponentID(name=name))


class MockGasMeter(MockAggregator):
    """Sums the gas its consumers burn on one declared channel; stands in for the real ``GasMeter``."""

    CONSUMPTION_KPI = "Gas consumption"

    CHANNELS = (
        DynamicConnectionChannel(
            key="consumption",
            tags=frozenset({lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED}),
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.WATT_HOUR,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    SUM_KPIS = {CONSUMPTION_KPI: ("GasConsumption", KpiAggregation.ENERGY_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockGasMeterConfig) -> None:
        """Builds the meter and the default feed it declares from the boiler."""
        super().__init__(my_simulation_parameters, config)
        self.ports_out["GasConsumption"] = self.add_output(
            self.component_name, "GasConsumption", lt.LoadTypes.GAS, lt.Units.WATT_HOUR, output_description="Gas"
        )
        self.observe(
            MockBoiler, "FuelUse", [lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED], MEASURED, lt.LoadTypes.GAS
        )
        self.observe(
            MockCombiBurner,
            MockCombiBurner.FUEL_OUTPUTS,
            [lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
            MEASURED,
            lt.LoadTypes.GAS,
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """The calibrated sum of the consumption channel."""
        stsv.set_output_value(
            self.ports_out["GasConsumption"], self.channel_sum(stsv, "consumption") * self.config.calibration
        )


@dataclass_json
@dataclass
class MockEnergyManagerConfig(ConfigBase):
    """A surplus controller raising set points by a fixed offset."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockEnergyManager"

    component_id: ComponentID
    offset_in_kelvin: float = field(default=2.0, metadata={UNIT: lt.Units.KELVIN})

    @preset
    @classmethod
    def preset_optimize_own_consumption(cls, name: str) -> "MockEnergyManagerConfig":
        """Raises by 2 K on surplus."""
        return cls(component_id=ComponentID(name=name))


#: The real controller's default weight per participant kind, which the mock declares its feeds at.
EMS_WEIGHTS = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS


class MockEnergyManager(MockAggregator):
    """An energy manager of the real one's shape: its channels, its weight table, a grid balance and a modifier.

    It observes electricity flows, ranks the controllable ones by weight and writes each one's
    dispatch output — the surplus — and sums what it observes into ``TotalElectricityToOrFromGrid``;
    its ``Modifier`` raises an L1's set point while there is surplus. Stands in for the real
    ``L2GenericEnergyManagementSystem`` in the mock assemblies.
    """

    GRID_BALANCE_KPI = "Grid balance"

    CHANNELS = L2GenericEnergyManagementSystem.CHANNELS
    PRODUCTION_CHANNEL = L2GenericEnergyManagementSystem.PRODUCTION_CHANNEL
    CONSUMPTION_UNCONTROLLED_CHANNEL = L2GenericEnergyManagementSystem.CONSUMPTION_UNCONTROLLED_CHANNEL

    SUM_KPIS = {GRID_BALANCE_KPI: ("TotalElectricityToOrFromGrid", KpiAggregation.POWER_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockEnergyManagerConfig) -> None:
        """Builds the manager: its two outputs and the flows it observes, at the real manager's weights."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("TotalElectricityToOrFromGrid", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        self.output_port("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN)
        controlled = lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED
        kinds = lt.ComponentType
        self.observe(
            MockOccupancy, "ElectricityConsumption", [kinds.RESIDENTS, controlled], EMS_WEIGHTS[kinds.RESIDENTS]
        )
        self.observe(MockPVSystem, "ElectricityOutput", [kinds.PV, lt.InandOutputType.ELECTRICITY_PRODUCTION], MEASURED)
        heating = kinds.ELECTRIC_HEATING_SH
        self.observe(MockHeater, "ElectricityInput", [heating, controlled], EMS_WEIGHTS[heating])
        self.observe(MockBattery, "AcBatteryPowerUsed", [kinds.BATTERY, controlled], EMS_WEIGHTS[kinds.BATTERY])
        water = kinds.ELECTRIC_HEATING_DHW
        self.observe(MockWaterHeater, "ElectricityInput", [water, controlled], EMS_WEIGHTS[water])

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production minus every consumption is the balance; the surplus is every dispatch."""
        production = self.channel_sum(stsv, self.PRODUCTION_CHANNEL)
        uncontrolled = self.channel_sum(stsv, self.CONSUMPTION_UNCONTROLLED_CHANNEL)
        ranked = [item for item in self.my_component_inputs if item.source_weight != MEASURED]
        controlled = sum(stsv.get_input_value(getattr(self, item.source_component_class)) for item in ranked)
        surplus = production - uncontrolled
        for output in self.outputs:
            if output.field_name.startswith("Dispatch"):
                stsv.set_output_value(output, max(surplus, 0.0))
        stsv.set_output_value(self.ports_out["TotalElectricityToOrFromGrid"], production - uncontrolled - controlled)
        stsv.set_output_value(self.ports_out["Modifier"], self.config.offset_in_kelvin if surplus > 0 else 0.0)


@dataclass_json
@dataclass
class MockElectricityMeterConfig(ConfigBase):
    """An electricity meter."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockElectricityMeter"

    component_id: ComponentID
    #: A calibration factor of the reading; dimensionless (a record writes a configuration's fields).
    calibration: float = field(default=1.0, metadata={UNIT: lt.Units.ANY})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockElectricityMeterConfig":
        """A meter."""
        return cls(component_id=ComponentID(name=name))


class MockElectricityMeter(MockAggregator):
    """Books production minus consumption as grid exchange, on the real meter's two channels.

    It declares the flows it may observe — the residents, a PV array and a heater — and an energy
    manager's grid balance on the production channel at 999, the feed the real meter declares from
    the real EMS (``assemblies_spec.md`` §4.3). Stands in for the real ``ElectricityMeter``.
    """

    EXCHANGE_KPI = "Grid exchange"

    CHANNELS = (
        DynamicConnectionChannel(
            key="production",
            tags=frozenset({lt.InandOutputType.ELECTRICITY_PRODUCTION}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
        DynamicConnectionChannel(
            key="consumption_uncontrolled",
            tags=frozenset({lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    SUM_KPIS = {EXCHANGE_KPI: ("ElectricityToAndFromGrid", KpiAggregation.POWER_IN_KWH)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockElectricityMeterConfig) -> None:
        """Builds the meter: its exchange output and every flow it may observe, all measured."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("ElectricityToAndFromGrid", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        uncontrolled = lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
        production = lt.InandOutputType.ELECTRICITY_PRODUCTION
        self.observe(MockOccupancy, "ElectricityConsumption", [uncontrolled], MEASURED)
        self.observe(MockPVSystem, "ElectricityOutput", [lt.ComponentType.PV, production], MEASURED)
        self.observe(MockHeater, "ElectricityInput", [lt.ComponentType.ELECTRIC_HEATING_SH, uncontrolled], MEASURED)
        self.observe(MockEnergyManager, "TotalElectricityToOrFromGrid", [production], MEASURED)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production minus consumption."""
        exchange = self.channel_sum(stsv, "production") - self.channel_sum(stsv, "consumption_uncontrolled")
        stsv.set_output_value(self.ports_out["ElectricityToAndFromGrid"], exchange * self.config.calibration)
