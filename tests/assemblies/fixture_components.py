"""Fake components for the assembly fixtures: small, deterministic, and fully declared.

The fixture assemblies under ``tests/assemblies/fixtures/library`` are built from these classes and
nothing else (``assemblies_spec.md`` §13 step 1: fixtures only). Each class declares, at class
level, what the expansion of imports checks at load time — its inputs and outputs with their load
types and units, the classes it declares default connections from, and the KPIs it reports
(:class:`~hisim.component_interface.ClassInterface`) — and its configuration declares the unit of
every field an assembly parameter feeds (D16 b). The constructors build their ports and default
connections *from* those declarations, so a fixture cannot drift from what it declares.

The physics is a toy: a weather series, an occupancy drawing hot water and electricity, a PV array
producing from the temperature, a tank losing heat and filled by a heater a thermostat switches,
and an energy manager whose modifier raises the thermostat's set point. Every value converges in a
few iterations, so a one-day run is fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentConnection, ComponentOutput, SingleTimeStepValues
from hisim.component_interface import ClassInterface, DeclaredPort
from hisim.config import ComponentID, ConfigBase, DisplayConfig, preset
from hisim.economics.facts import CostRelevance
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters

#: The metadata key a configuration field declares its unit under (``SizedFieldMetadata.UNIT``).
UNIT = "unit"


class FixtureComponent(Component):
    """The shared behaviour of every fixture: ports and default connections from its interface."""

    cost_relevance = CostRelevance.FREE_OF_COST

    #: Source class name to ``{input: output}`` of its default connections.
    DEFAULTS: Dict[str, Dict[str, str]] = {}

    #: Inputs that may stay unconnected.
    OPTIONAL_INPUTS: tuple = ()

    def __init__(self, my_simulation_parameters: SimulationParameters, config: Any) -> None:
        """Builds the ports and the default connections the class interface declares."""
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        interface = type(self).CLASS_INTERFACE
        assert interface is not None
        self.ports_in = {
            port.name: self.add_input(
                self.component_name, port.name, port.load_type, port.unit, port.name not in self.OPTIONAL_INPUTS
            )
            for port in interface.inputs
        }
        self.ports_out: Dict[str, ComponentOutput] = {
            port.name: self.add_output(
                self.component_name, port.name, port.load_type, port.unit, output_description=port.name
            )
            for port in interface.outputs
        }
        for source, wires in self.DEFAULTS.items():
            self.add_default_connections(
                [ComponentConnection(target, source, output) for target, output in wires.items()]
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
        """No KPIs unless the fixture reports one."""
        return []


# ------------------------------------------------------------------------------------------ weather


@dataclass_json
@dataclass
class FakeWeatherConfig(ConfigBase):
    """A constant-ish outside temperature."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeWeather"

    component_id: ComponentID
    mean_temperature_in_celsius: float = field(default=5.0, metadata={UNIT: lt.Units.CELSIUS})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeWeatherConfig":
        """A winter day around 5 °C."""
        return cls(component_id=ComponentID(name=name))


class FakeWeather(FixtureComponent):
    """Outputs the outside temperature."""

    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort("TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeWeatherConfig) -> None:
        """Builds the weather."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A daily wave around the mean."""
        self.set(stsv, "TemperatureOutside", self.config.mean_temperature_in_celsius + (timestep % 96) / 24.0)


# ---------------------------------------------------------------------------------------- occupancy


@dataclass_json
@dataclass
class FakeOccupancyConfig(ConfigBase):
    """Residents drawing hot water and electricity."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeOccupancy"

    component_id: ComponentID
    residents: int = 2

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeOccupancyConfig":
        """Two residents."""
        return cls(component_id=ComponentID(name=name))


class FakeOccupancy(FixtureComponent):
    """Outputs a hot-water draw and an electricity consumption."""

    CLASS_INTERFACE = ClassInterface(
        outputs=(
            DeclaredPort("WaterDemand", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP),
            DeclaredPort("ElectricityConsumption", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),
        ),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeOccupancyConfig) -> None:
        """Builds the occupancy."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Draws in the morning and the evening."""
        draw = 10.0 if timestep % 96 in (28, 29, 76, 77) else 0.0
        self.set(stsv, "WaterDemand", draw)
        self.set(stsv, "ElectricityConsumption", 150.0 * self.config.residents)


# ------------------------------------------------------------------------------------------------ pv


@dataclass_json
@dataclass
class FakePVSystemConfig(ConfigBase):
    """A PV array: its peak power and its orientation, each with its unit."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakePVSystem"

    component_id: ComponentID
    power_in_watt: float = field(default=5000.0, metadata={UNIT: lt.Units.WATT})
    azimuth: float = field(default=180.0, metadata={UNIT: lt.Units.DEGREES})
    tilt: float = field(default=30.0, metadata={UNIT: lt.Units.DEGREES})
    #: A field without a declared unit, for the refusal of a fed field without one.
    shading_factor: float = 1.0

    @preset
    @classmethod
    def preset_rooftop(cls, name: str) -> "FakePVSystemConfig":
        """A south-facing roof array."""
        return cls(component_id=ComponentID(name=name))


class FakePVSystem(FixtureComponent):
    """Produces electricity from the outside temperature (a toy stand-in for irradiance)."""

    PRODUCTION_KPI = "PV production"

    CLASS_INTERFACE = ClassInterface(
        inputs=(DeclaredPort("TemperatureOutside", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),),
        outputs=(DeclaredPort("ElectricityOutput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),),
        default_connection_sources=("FakeWeather",),
        kpis=(PRODUCTION_KPI,),
    )
    DEFAULTS = {"FakeWeather": {"TemperatureOutside": "TemperatureOutside"}}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakePVSystemConfig) -> None:
        """Builds the array."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production proportional to power, orientation and temperature."""
        orientation = 1.0 - abs(self.config.azimuth - 180.0) / 360.0
        temperature = self.value(stsv, "TemperatureOutside")
        self.set(stsv, "ElectricityOutput", self.config.power_in_watt * orientation * max(temperature, 0.0) / 50.0)

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
class FakeTankConfig(ConfigBase):
    """A hot-water tank."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeTank"

    component_id: ComponentID
    volume_in_liter: float = field(default=150.0, metadata={UNIT: lt.Units.LITER})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeTankConfig":
        """A 150 l tank."""
        return cls(component_id=ComponentID(name=name))


class FakeTank(FixtureComponent):
    """Mixes the heater's power and the residents' draw into a temperature."""

    STANDBY_KPI = "Standby heat losses"

    CLASS_INTERFACE = ClassInterface(
        inputs=(
            DeclaredPort("WaterDemand", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP),
            DeclaredPort("ThermalPower", lt.LoadTypes.HEATING, lt.Units.WATT),
        ),
        outputs=(
            DeclaredPort("WaterTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            DeclaredPort("HeatLoss", lt.LoadTypes.HEATING, lt.Units.WATT),
        ),
        default_connection_sources=("FakeOccupancy", "FakeHeater"),
        kpis=(STANDBY_KPI,),
    )
    DEFAULTS = {
        "FakeOccupancy": {"WaterDemand": "WaterDemand"},
        "FakeHeater": {"ThermalPower": "ThermalPower"},
    }

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeTankConfig) -> None:
        """Builds the tank."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A quasi-static balance: heat in minus draw and standby loss."""
        loss = 0.2 * self.config.volume_in_liter
        temperature = 40.0 + (self.value(stsv, "ThermalPower") - loss) / 100.0 - self.value(stsv, "WaterDemand") / 10.0
        self.set(stsv, "WaterTemperature", temperature)
        self.set(stsv, "HeatLoss", loss)


# -------------------------------------------------------------------------------------------- heater


@dataclass_json
@dataclass
class FakeHeaterConfig(ConfigBase):
    """An electric heater."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeHeater"

    component_id: ComponentID
    power_in_watt: float = field(default=2000.0, metadata={UNIT: lt.Units.WATT})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeHeaterConfig":
        """A 2 kW heater."""
        return cls(component_id=ComponentID(name=name))


class FakeHeater(FixtureComponent):
    """Heats when its controller says so."""

    ENERGY_KPI = "Heater energy"

    CLASS_INTERFACE = ClassInterface(
        inputs=(DeclaredPort("Signal", lt.LoadTypes.ON_OFF, lt.Units.ANY),),
        outputs=(
            DeclaredPort("ThermalPower", lt.LoadTypes.HEATING, lt.Units.WATT),
            DeclaredPort("ElectricityInput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),
        ),
        default_connection_sources=("FakeController",),
        kpis=(ENERGY_KPI,),
    )
    DEFAULTS = {"FakeController": {"Signal": "Signal"}}
    OPTIONAL_INPUTS = ("Signal",)

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeHeaterConfig) -> None:
        """Builds the heater."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Full power when on."""
        power = self.config.power_in_watt * (1.0 if self.value(stsv, "Signal") > 0.5 else 0.0)
        self.set(stsv, "ThermalPower", power)
        self.set(stsv, "ElectricityInput", power)


# ---------------------------------------------------------------------------------------- controller


@dataclass_json
@dataclass
class FakeControllerConfig(ConfigBase):
    """A thermostat on the tank."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeController"

    component_id: ComponentID
    set_temperature_in_celsius: float = field(default=45.0, metadata={UNIT: lt.Units.CELSIUS})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeControllerConfig":
        """45 °C."""
        return cls(component_id=ComponentID(name=name))

    @preset
    @classmethod
    def preset_eco(cls, name: str) -> "FakeControllerConfig":
        """40 °C."""
        return cls(component_id=ComponentID(name=name), set_temperature_in_celsius=40.0)


class FakeController(FixtureComponent):
    """Switches the heater on below the set point, which an energy manager may raise."""

    CLASS_INTERFACE = ClassInterface(
        inputs=(
            DeclaredPort("TankTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            DeclaredPort("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),
        ),
        outputs=(DeclaredPort("Signal", lt.LoadTypes.ON_OFF, lt.Units.ANY),),
        default_connection_sources=("FakeTank", "FakeEms"),
    )
    DEFAULTS = {
        "FakeTank": {"TankTemperature": "WaterTemperature"},
        "FakeEms": {"Modifier": "Modifier"},
    }
    OPTIONAL_INPUTS = ("Modifier", "TankTemperature")

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeControllerConfig) -> None:
        """Builds the controller."""
        super().__init__(my_simulation_parameters, config)

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
class FakeEmsConfig(ConfigBase):
    """An energy manager raising set points by a fixed offset."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeEms"

    component_id: ComponentID
    offset_in_kelvin: float = field(default=2.0, metadata={UNIT: lt.Units.KELVIN})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeEmsConfig":
        """Raises by 2 K."""
        return cls(component_id=ComponentID(name=name))


class FakeEms(FixtureComponent):
    """Outputs a set-point modifier."""

    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeEmsConfig) -> None:
        """Builds the energy manager."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A constant offset."""
        self.set(stsv, "Modifier", self.config.offset_in_kelvin)


# --------------------------------------------------------------------------------- undeclared class


@dataclass_json
@dataclass
class UndeclaredDeviceConfig(ConfigBase):
    """A component class that makes no class-level statement, for the refusals that need one."""

    MAIN_CLASS = "tests.assemblies.fixture_components.UndeclaredDevice"

    component_id: ComponentID
    rating_in_watt: float = 100.0

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "UndeclaredDeviceConfig":
        """Nothing to configure."""
        return cls(component_id=ComponentID(name=name))


class UndeclaredDevice(Component):
    """Declares no CLASS_INTERFACE."""

    cost_relevance = CostRelevance.FREE_OF_COST

    def __init__(self, my_simulation_parameters: SimulationParameters, config: UndeclaredDeviceConfig) -> None:
        """Builds the device."""
        super().__init__(config.component_id.key, my_simulation_parameters, config, DisplayConfig())

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Nothing."""

    def i_prepare_simulation(self) -> None:
        """Nothing."""

    def i_save_state(self) -> None:
        """Stateless."""

    def i_restore_state(self) -> None:
        """Stateless."""

    def i_doublecheck(self, timestep: int, stsv: SingleTimeStepValues) -> None:
        """Nothing."""
