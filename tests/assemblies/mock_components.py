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
one-day run is fast. The circuit, carrier and fact mocks return with part 2 of the v1 work.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentConnection, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.config import ComponentID, ConfigBase, DisplayConfig, preset
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
    ) -> None:
        """Adds one output (``add_output``), with its post-processing flags where it has them."""
        self.ports_out[name] = self.add_output(
            self.component_name,
            name,
            load_type,
            unit,
            postprocessing_flag=postprocessing_flag,
            output_description=name,
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
        self.output_port("ElectricityConsumption", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Draws in the morning and the evening."""
        draw = 10.0 if timestep % 96 in (28, 29, 76, 77) else 0.0
        self.set(stsv, "WaterDemand", draw)
        self.set(stsv, "ElectricityConsumption", 150.0 * self.config.residents)


# ------------------------------------------------------------------------------------------------ pv


@dataclass_json
@dataclass
class MockPVSystemConfig(ConfigBase):
    """A PV array: its peak power — or the share of the roof it covers instead — and its orientation."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockPVSystem"

    #: The peak power of the whole roof, which a share of it is taken of.
    ROOF_PEAK_POWER_IN_WATT: ClassVar[float] = 20000.0

    component_id: ComponentID
    power_in_watt: Optional[float] = field(default=5000.0, metadata={UNIT: lt.Units.WATT})
    #: The share of the roof the array covers, the alternative to its peak power; dimensionless.
    share_of_roof: Optional[float] = field(default=None, metadata={UNIT: lt.Units.ANY})
    azimuth: float = field(default=180.0, metadata={UNIT: lt.Units.DEGREES})
    tilt: float = field(default=30.0, metadata={UNIT: lt.Units.DEGREES})
    #: A field without a declared unit, for the refusal of a fed field without one.
    shading_factor: float = 1.0

    def peak_power_in_watt(self) -> float:
        """The peak power: the one given, or the share of the roof's; exactly one of the two is set."""
        if (self.power_in_watt is None) == (self.share_of_roof is None):
            raise ValueError(
                f"{self.component_id.name} sets power_in_watt={self.power_in_watt!r} and "
                f"share_of_roof={self.share_of_roof!r}; exactly one of the two is given."
            )
        if self.power_in_watt is not None:
            return self.power_in_watt
        return float(self.share_of_roof or 0.0) * self.ROOF_PEAK_POWER_IN_WATT

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
        self.output_port("ThermalPower", lt.LoadTypes.HEATING, lt.Units.WATT)
        self.output_port("ElectricityInput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        self.defaults_from("MockController", {"Signal": "Signal"})

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Full power when on."""
        power = self.config.power_in_watt * (1.0 if self.value(stsv, "Signal") > 0.5 else 0.0)
        self.set(stsv, "ThermalPower", power)
        self.set(stsv, "ElectricityInput", power)


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
