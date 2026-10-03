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

For the circuit, carrier and fact ports (hisim-lt0b.2) a gas boiler charges a cylinder over a
``dhw`` circuit — the boiler owns ``MassFlowDhw`` and ``SupplyTemperatureDhw``, the cylinder owns
``ReturnTemperatureDhw``, each reading the other's by its default connections — and burns natural
gas that a gas meter observes through the default feed its class declares; the boiler declares its
energy ports, fuel in, heat out and flue loss, so the energy-balance check closes its balance every
step. A battery sizes its capacity from the PV arrays' peak power, the fact the array contributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentConnection, ComponentOutput, SingleTimeStepValues
from hisim.component_interface import ClassInterface, DeclaredFeed, DeclaredPort
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    FactContribution,
    Sizable,
    Size,
    concrete,
    preset,
    sized_field,
)
from hisim.config.channels import DispatchRule, DynamicConnectionChannel
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicComponentConnection,
    DynamicConnectionInput,
    DynamicConnectionOutput,
)
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.economics.facts import CostRelevance
from hisim.energy_port import EnergyPort
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

    #: Output name to the energy port it is added with.
    ENERGY_PORTS: Dict[str, EnergyPort] = {}

    #: KPI name to ``(output, factor)``: the KPI is the output's sum over the run times the factor.
    SUM_KPIS: Dict[str, tuple] = {}

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
                self.component_name,
                port.name,
                port.load_type,
                port.unit,
                energy_port=self.ENERGY_PORTS.get(port.name),
                output_description=port.name,
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
        """The sums :attr:`SUM_KPIS` declares, each over the run."""
        entries: List[KpiEntry] = []
        for name, (output, factor) in self.SUM_KPIS.items():
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
                total = float(postprocessing_results[column].sum()) * factor
            entries.append(KpiEntry(name=name, unit="kWh", value=total, tag=KpiTagEnumClass.GENERAL))
        return entries


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
    """A PV array: its peak power — or the share of the roof it covers instead — and its orientation."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakePVSystem"

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

    #: The array's peak power, which a battery beside it is sized from.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=("pv_peak_power_in_watt",),
            compute=lambda config, ctx: {"pv_peak_power_in_watt": config.peak_power_in_watt()},
        ),
    )

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
    #: The standby loss over the run in kWh (a power in W summed over 15-minute steps).
    SUM_KPIS = {STANDBY_KPI: ("HeatLoss", 0.25e-3)}

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
    #: Electricity in, space-heating heat out, one for one: the balance closes every step.
    ENERGY_PORTS = {
        "ElectricityInput": EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.ELECTRICITY),
        "ThermalPower": EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
    }
    #: The electricity the heater drew over the run in kWh (a power in W summed over 15-minute steps).
    SUM_KPIS = {ENERGY_KPI: ("ElectricityInput", 0.25e-3)}

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
        default_connection_sources=("FakeTank", "FakeEms", "FakeEnergyManager"),
    )
    DEFAULTS = {
        "FakeTank": {"TankTemperature": "WaterTemperature"},
        "FakeEms": {"Modifier": "Modifier"},
        "FakeEnergyManager": {"Modifier": "Modifier"},
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


# --------------------------------------------------------------------------------- the dhw circuit

#: The ``dhw`` circuit's three outputs (``hisim/energy_system/imports_model.py``, ``CircuitNaming``).
MASS_FLOW_DHW = "MassFlowDhw"
SUPPLY_TEMPERATURE_DHW = "SupplyTemperatureDhw"
RETURN_TEMPERATURE_DHW = "ReturnTemperatureDhw"


@dataclass_json
@dataclass
class FakeBoilerConfig(ConfigBase):
    """A gas boiler charging a cylinder over the dhw circuit."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeBoiler"

    component_id: ComponentID
    power_in_watt: float = field(default=3000.0, metadata={UNIT: lt.Units.WATT})
    efficiency: float = field(default=0.9, metadata={UNIT: lt.Units.ANY})

    @preset
    @classmethod
    def preset_condensing(cls, name: str) -> "FakeBoilerConfig":
        """A 3 kW condensing boiler."""
        return cls(component_id=ComponentID(name=name))


class FakeBoiler(FixtureComponent):
    """Owns the dhw circuit's mass flow and supply leg, reads its return leg, and burns natural gas.

    Every step it heats at a schedule's power; the gas it burns is that heat over its efficiency,
    and the rest leaves as flue loss, so its energy ports — gas in, heat out, flue loss — balance.
    """

    FUEL_KPI = "Boiler fuel"

    CLASS_INTERFACE = ClassInterface(
        inputs=(DeclaredPort(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),),
        outputs=(
            DeclaredPort(MASS_FLOW_DHW, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
            DeclaredPort(SUPPLY_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            DeclaredPort(
                "ThermalPowerDhw", lt.LoadTypes.HEATING, lt.Units.WATT, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT
            ),
            DeclaredPort("FuelUse", lt.LoadTypes.GAS, lt.Units.WATT_HOUR, lt.EnergyBalanceCarrier.NATURAL_GAS),
            DeclaredPort("FlueLoss", lt.LoadTypes.GAS, lt.Units.WATT, lt.EnergyBalanceCarrier.NATURAL_GAS),
        ),
        default_connection_sources=("FakeCylinder",),
        kpis=(FUEL_KPI,),
    )
    DEFAULTS = {"FakeCylinder": {RETURN_TEMPERATURE_DHW: RETURN_TEMPERATURE_DHW}}
    ENERGY_PORTS = {
        "FuelUse": EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.NATURAL_GAS, peer_output="FuelUse"),
        "ThermalPowerDhw": EnergyPort(
            lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT, peer_output=MASS_FLOW_DHW
        ),
        "FlueLoss": EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.NATURAL_GAS),
    }
    SUM_KPIS = {FUEL_KPI: ("FuelUse", 1e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeBoilerConfig) -> None:
        """Builds the boiler."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Heats one step in two at full power, supplying ten kelvin above the return leg."""
        heat = self.config.power_in_watt if timestep % 2 == 0 else 0.0
        fuel_power = heat / self.config.efficiency
        seconds = self.my_simulation_parameters.seconds_per_timestep
        self.set(stsv, MASS_FLOW_DHW, heat / (4180.0 * 10.0))
        self.set(stsv, SUPPLY_TEMPERATURE_DHW, self.value(stsv, RETURN_TEMPERATURE_DHW) + 10.0)
        self.set(stsv, "ThermalPowerDhw", heat)
        self.set(stsv, "FuelUse", fuel_power * seconds / 3600.0)
        self.set(stsv, "FlueLoss", fuel_power - heat)


@dataclass_json
@dataclass
class FakeCylinderConfig(ConfigBase):
    """A hot-water cylinder charged over the dhw circuit."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeCylinder"

    component_id: ComponentID
    volume_in_liter: float = field(default=200.0, metadata={UNIT: lt.Units.LITER})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeCylinderConfig":
        """A 200 l cylinder."""
        return cls(component_id=ComponentID(name=name))


class FakeCylinder(FixtureComponent):
    """Owns the dhw circuit's return leg and reads the boiler's mass flow and supply leg."""

    TEMPERATURE_KPI = "Cylinder heat received"

    CLASS_INTERFACE = ClassInterface(
        inputs=(
            DeclaredPort("WaterDemand", lt.LoadTypes.WARM_WATER, lt.Units.LITER_PER_TIMESTEP),
            DeclaredPort(MASS_FLOW_DHW, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
            DeclaredPort(SUPPLY_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        ),
        outputs=(
            DeclaredPort(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            DeclaredPort("HeatReceived", lt.LoadTypes.HEATING, lt.Units.WATT),
        ),
        default_connection_sources=("FakeOccupancy", "FakeBoiler"),
        kpis=(TEMPERATURE_KPI,),
    )
    DEFAULTS = {
        "FakeOccupancy": {"WaterDemand": "WaterDemand"},
        "FakeBoiler": {MASS_FLOW_DHW: MASS_FLOW_DHW, SUPPLY_TEMPERATURE_DHW: SUPPLY_TEMPERATURE_DHW},
    }
    SUM_KPIS = {TEMPERATURE_KPI: ("HeatReceived", 0.25e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeCylinderConfig) -> None:
        """Builds the cylinder."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Returns water the colder the more is drawn, and receives the circuit's heat."""
        returned = 40.0 - self.value(stsv, "WaterDemand") / 10.0 - self.config.volume_in_liter / 1000.0
        self.set(stsv, RETURN_TEMPERATURE_DHW, returned)
        received = 4180.0 * self.value(stsv, MASS_FLOW_DHW) * (self.value(stsv, SUPPLY_TEMPERATURE_DHW) - returned)
        self.set(stsv, "HeatReceived", received)


# ----------------------------------------------------------------------------------------- gas meter


@dataclass_json
@dataclass
class FakeGasMeterConfig(ConfigBase):
    """A gas meter."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeGasMeter"

    component_id: ComponentID
    #: A calibration factor of the reading; dimensionless.
    calibration: float = field(default=1.0, metadata={UNIT: lt.Units.ANY})

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeGasMeterConfig":
        """A calibrated meter."""
        return cls(component_id=ComponentID(name=name))


class FakeGasMeter(DynamicComponent):
    """Sums the gas its consumers burn, as the real gas meter does, on one declared channel."""

    cost_relevance = CostRelevance.FREE_OF_COST

    CONSUMPTION_CHANNEL: ClassVar[str] = "consumption_uncontrolled"
    CONSUMPTION_KPI: ClassVar[str] = "Gas consumption"

    CHANNELS: ClassVar[Tuple[DynamicConnectionChannel, ...]] = (
        DynamicConnectionChannel(
            key=CONSUMPTION_CHANNEL,
            tags=frozenset({lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED}),
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.WATT_HOUR,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort("GasConsumption", lt.LoadTypes.GAS, lt.Units.WATT_HOUR),),
        kpis=(CONSUMPTION_KPI,),
        default_feeds=(DeclaredFeed("FakeBoiler", "FuelUse", ("GAS_CONSUMPTION_UNCONTROLLED",), 999),),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeGasMeterConfig) -> None:
        """Builds the meter and its one declared default feed."""
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
        self.consumption_channel: ComponentOutput = self.add_output(
            self.component_name,
            "GasConsumption",
            lt.LoadTypes.GAS,
            lt.Units.WATT_HOUR,
            output_description="The gas every observed consumer burned.",
        )
        self.add_dynamic_default_connections(
            [
                DynamicComponentConnection(
                    source_component_class=FakeBoiler,
                    source_class_name=FakeBoiler.get_classname(),
                    source_component_field_name="FuelUse",
                    source_load_type=lt.LoadTypes.GAS,
                    source_unit=lt.Units.WATT_HOUR,
                    source_tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
                    source_weight=999,
                )
            ]
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """The calibrated sum of the consumption channel."""
        inputs = self.get_channel_inputs(self.CONSUMPTION_CHANNEL)
        total = sum(stsv.get_input_value(component_input=item) for item in inputs)
        stsv.set_output_value(self.consumption_channel, total * self.config.calibration)

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
        """The gas the meter read, in kWh."""
        column = next(
            (
                item.get_pretty_name()
                for item in all_outputs
                if item.component_name == self.component_name and item.field_name == "GasConsumption"
            ),
            None,
        )
        total = float(postprocessing_results[column].sum()) * 1e-3 if column in postprocessing_results else 0.0
        return [KpiEntry(name=self.CONSUMPTION_KPI, unit="kWh", value=total, tag=KpiTagEnumClass.GAS_METER)]


# ------------------------------------------------------------------------------------------ battery


@dataclass_json
@dataclass
class FakeBatteryConfig(ConfigBase):
    """A battery sized from the PV peak power beside it, with its unit on the sized field."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeBattery"

    component_id: ComponentID
    #: One kWh per kWp of the array it is bound to, unless pinned.
    capacity_in_kwh: Sizable[float] = sized_field(
        rule=(Size.PV_PEAK_POWER_IN_WATT * 1e-3).rounded(2), unit=lt.Units.KWH
    )

    @preset
    @classmethod
    def preset_sized_to_pv(cls, name: str) -> "FakeBatteryConfig":
        """Capacity AUTO."""
        return cls(component_id=ComponentID(name=name))


class FakeBattery(FixtureComponent):
    """Publishes its capacity, and charges at the power an energy manager writes into ``LoadingPowerInput``.

    The toy has no state: the AC power it draws (positive) or delivers (negative) is the requested
    power, limited to its capacity in kWh taken as kW. Without a controller the input stays
    unconnected and the battery idles.
    """

    CAPACITY_KPI = "Battery capacity"

    CLASS_INTERFACE = ClassInterface(
        inputs=(DeclaredPort("LoadingPowerInput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),),
        outputs=(
            DeclaredPort("Capacity", lt.LoadTypes.ANY, lt.Units.KWH),
            DeclaredPort("AcBatteryPowerUsed", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),
        ),
        kpis=(CAPACITY_KPI,),
    )
    OPTIONAL_INPUTS = ("LoadingPowerInput",)
    SUM_KPIS = {CAPACITY_KPI: ("Capacity", 1.0 / 96.0)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeBatteryConfig) -> None:
        """Builds the battery."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """The capacity, and the requested power within it."""
        capacity = concrete(self.config.capacity_in_kwh)
        limit = 1000.0 * capacity
        self.set(stsv, "Capacity", capacity)
        self.set(stsv, "AcBatteryPowerUsed", max(-limit, min(limit, self.value(stsv, "LoadingPowerInput"))))


# -------------------------------------------------------------------------------- a solar circuit


@dataclass_json
@dataclass
class FakeCollectorConfig(ConfigBase):
    """A solar collector, one end of a ``solar`` circuit: an end of another medium than ``dhw``."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeCollector"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeCollectorConfig":
        """A collector."""
        return cls(component_id=ComponentID(name=name))


class FakeCollector(FixtureComponent):
    """Owns the ``solar`` circuit's mass flow and supply leg."""

    CLASS_INTERFACE = ClassInterface(
        inputs=(DeclaredPort("ReturnTemperatureSolar", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),),
        outputs=(
            DeclaredPort("MassFlowSolar", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
            DeclaredPort("SupplyTemperatureSolar", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        ),
    )
    OPTIONAL_INPUTS = ("ReturnTemperatureSolar",)

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeCollectorConfig) -> None:
        """Builds the collector."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """No sun in the fixture."""
        self.set(stsv, "MassFlowSolar", 0.0)
        self.set(stsv, "SupplyTemperatureSolar", self.value(stsv, "ReturnTemperatureSolar"))


# ------------------------------------------------------------------ dhw ends that do not fit a boiler


@dataclass_json
@dataclass
class FakeDhwSinkConfig(ConfigBase):
    """A dhw end that reads the boiler's outputs but declares no default connections from it."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeDhwSink"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeDhwSinkConfig":
        """A sink."""
        return cls(component_id=ComponentID(name=name))


class FakeDhwSink(FixtureComponent):
    """Reads ``MassFlowDhw`` and ``SupplyTemperatureDhw``, owns ``ReturnTemperatureDhw``, declares no defaults."""

    CLASS_INTERFACE = ClassInterface(
        inputs=(
            DeclaredPort(MASS_FLOW_DHW, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC),
            DeclaredPort(SUPPLY_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
        ),
        outputs=(DeclaredPort(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeDhwSinkConfig) -> None:
        """Builds the sink."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A constant return leg."""
        self.set(stsv, RETURN_TEMPERATURE_DHW, 40.0)


@dataclass_json
@dataclass
class FakeDhwReturnConfig(ConfigBase):
    """A dhw end that only owns the return leg and reads nothing."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeDhwReturn"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeDhwReturnConfig":
        """A return leg."""
        return cls(component_id=ComponentID(name=name))


class FakeDhwReturn(FixtureComponent):
    """Owns ``ReturnTemperatureDhw`` and reads none of the boiler's outputs."""

    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeDhwReturnConfig) -> None:
        """Builds the end."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A constant return leg."""
        self.set(stsv, RETURN_TEMPERATURE_DHW, 40.0)


# ------------------------------------------------------------------- observers: an EMS and a meter


def _declared_connections(target: type) -> List[List[DynamicComponentConnection]]:
    """The runtime dynamic default connections of a fixture aggregator, built from its declared feeds.

    One list per source class, in declaration order, so the constructor adds exactly what the class
    interface declares (``DynamicComponent._check_declared_feed`` holds it to that).
    """
    interface = target.CLASS_INTERFACE  # type: ignore[attr-defined]
    by_class: Dict[str, List[DynamicComponentConnection]] = {}
    for feed in interface.default_feeds:
        source = globals()[feed.source_class]
        port = source.CLASS_INTERFACE.output(feed.output)
        tags: List[Any] = [lt.ComponentType[feed.component_type]] if feed.component_type else []
        tags.extend(lt.InandOutputType[tag] for tag in feed.tags)
        by_class.setdefault(feed.source_class, []).append(
            DynamicComponentConnection(
                source_component_class=source,
                source_class_name=feed.source_class,
                source_component_field_name=feed.output,
                source_load_type=port.load_type,
                source_unit=port.unit,
                source_tags=tags,
                source_weight=feed.weight,
            )
        )
    return list(by_class.values())


class FixtureAggregator(DynamicComponent):
    """The shared behaviour of the fixture observers: declared outputs, declared feeds, no state, no cost."""

    cost_relevance = CostRelevance.FREE_OF_COST

    #: KPI name to ``(output, factor)``: the KPI is the output's sum over the run times the factor.
    SUM_KPIS: Dict[str, tuple] = {}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: Any) -> None:
        """Builds the outputs and the dynamic default connections the class interface declares."""
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
        interface = type(self).CLASS_INTERFACE
        assert interface is not None
        self.ports_out: Dict[str, ComponentOutput] = {
            port.name: self.add_output(
                self.component_name, port.name, port.load_type, port.unit, output_description=port.name
            )
            for port in interface.outputs
        }
        for connections in _declared_connections(type(self)):
            self.add_dynamic_default_connections(connections)

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
        """The sums :attr:`SUM_KPIS` declares, each over the run."""
        entries: List[KpiEntry] = []
        for name, (output, factor) in self.SUM_KPIS.items():
            column = next(
                (
                    item.get_pretty_name()
                    for item in all_outputs
                    if item.component_name == self.component_name and item.field_name == output
                ),
                None,
            )
            total = float(postprocessing_results[column].sum()) * factor if column in postprocessing_results else 0.0
            entries.append(KpiEntry(name=name, unit="kWh", value=total, tag=KpiTagEnumClass.GENERAL))
        return entries


@dataclass_json
@dataclass
class FakeEnergyManagerConfig(ConfigBase):
    """A surplus controller raising set points by a fixed offset."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeEnergyManager"

    component_id: ComponentID
    offset_in_kelvin: float = field(default=2.0, metadata={UNIT: lt.Units.KELVIN})

    @preset
    @classmethod
    def preset_optimize_own_consumption(cls, name: str) -> "FakeEnergyManagerConfig":
        """Raises by 2 K on surplus."""
        return cls(component_id=ComponentID(name=name))


#: The real controller's default weight per participant kind, which the fixture declares its feeds at.
EMS_WEIGHTS = L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS


class FakeEnergyManager(FixtureAggregator):
    """An energy manager of the real one's shape: its channels, its weight table, a grid balance and a modifier.

    It observes electricity flows, ranks the controllable ones by weight and writes each one's
    dispatch output — the surplus left at its rank — and sums what it observes into
    ``TotalElectricityToOrFromGrid``; its ``Modifier`` raises an L1's set point while there is surplus.
    The weights it declares are the real controller's (``L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS``),
    the battery's feed actuates its ``LoadingPowerInput`` directly (D21), and its per-feed dispatch
    outputs are named by the format's templates, ``DispatchTo<participant>_<input>`` and
    ``DispatchFor<participant>_<output>``: they exist per feed, so no static interface lists them
    (hisim-lt0b.15).
    """

    GRID_BALANCE_KPI = "Grid balance"

    CHANNELS = L2GenericEnergyManagementSystem.CHANNELS
    PRODUCTION_CHANNEL = L2GenericEnergyManagementSystem.PRODUCTION_CHANNEL
    CONSUMPTION_UNCONTROLLED_CHANNEL = L2GenericEnergyManagementSystem.CONSUMPTION_UNCONTROLLED_CHANNEL

    CLASS_INTERFACE = ClassInterface(
        outputs=(
            DeclaredPort("TotalElectricityToOrFromGrid", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),
            DeclaredPort("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN),
        ),
        kpis=(GRID_BALANCE_KPI,),
        default_feeds=(
            DeclaredFeed(
                "FakeOccupancy",
                "ElectricityConsumption",
                ("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",),
                EMS_WEIGHTS[lt.ComponentType.RESIDENTS],
                "RESIDENTS",
            ),
            DeclaredFeed("FakePVSystem", "ElectricityOutput", ("ELECTRICITY_PRODUCTION",), 999, "PV"),
            DeclaredFeed(
                "FakeHeater",
                "ElectricityInput",
                ("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",),
                EMS_WEIGHTS[lt.ComponentType.ELECTRIC_HEATING_SH],
                "ELECTRIC_HEATING_SH",
            ),
            DeclaredFeed(
                "FakeBattery",
                "AcBatteryPowerUsed",
                ("ELECTRICITY_CONSUMPTION_EMS_CONTROLLED",),
                EMS_WEIGHTS[lt.ComponentType.BATTERY],
                "BATTERY",
                dispatch_target="LoadingPowerInput",
            ),
        ),
    )
    SUM_KPIS = {GRID_BALANCE_KPI: ("TotalElectricityToOrFromGrid", 0.25e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeEnergyManagerConfig) -> None:
        """Builds the energy manager."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production minus every consumption is the balance; the surplus before the ranked ones is dispatched."""
        production = self.channel_sum(stsv, self.PRODUCTION_CHANNEL)
        uncontrolled = self.channel_sum(stsv, self.CONSUMPTION_UNCONTROLLED_CHANNEL)
        ranked = [item for item in self.my_component_inputs if item.source_weight != 999]
        controlled = sum(stsv.get_input_value(getattr(self, item.source_component_class)) for item in ranked)
        surplus = production - uncontrolled
        for output in self.outputs:
            if output.field_name.startswith("Dispatch"):
                stsv.set_output_value(output, max(surplus, 0.0))
        stsv.set_output_value(self.ports_out["TotalElectricityToOrFromGrid"], production - uncontrolled - controlled)
        stsv.set_output_value(self.ports_out["Modifier"], self.config.offset_in_kelvin if surplus > 0 else 0.0)


@dataclass_json
@dataclass
class FakeElectricityMeterConfig(ConfigBase):
    """An electricity meter."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeElectricityMeter"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeElectricityMeterConfig":
        """A meter."""
        return cls(component_id=ComponentID(name=name))


class FakeElectricityMeter(FixtureAggregator):
    """Books production minus consumption as grid exchange, on the real meter's two channels.

    It declares the flows it may observe — the residents, a PV array and a heater — and an energy
    manager's grid balance on the production channel at 999, the feed the real meter declares from
    the real EMS (``assemblies_spec.md`` §4.3, dry run G6).
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

    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort("ElectricityToAndFromGrid", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),),
        kpis=(EXCHANGE_KPI,),
        default_feeds=(
            DeclaredFeed("FakeOccupancy", "ElectricityConsumption", ("ELECTRICITY_CONSUMPTION_UNCONTROLLED",), 999),
            DeclaredFeed("FakePVSystem", "ElectricityOutput", ("ELECTRICITY_PRODUCTION",), 999, "PV"),
            DeclaredFeed(
                "FakeHeater", "ElectricityInput", ("ELECTRICITY_CONSUMPTION_UNCONTROLLED",), 999, "ELECTRIC_HEATING_SH"
            ),
            DeclaredFeed("FakeEnergyManager", "TotalElectricityToOrFromGrid", ("ELECTRICITY_PRODUCTION",), 999),
        ),
    )
    SUM_KPIS = {EXCHANGE_KPI: ("ElectricityToAndFromGrid", 0.25e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeElectricityMeterConfig) -> None:
        """Builds the meter."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production minus consumption."""
        exchange = self.channel_sum(stsv, "production") - self.channel_sum(stsv, "consumption_uncontrolled")
        stsv.set_output_value(self.ports_out["ElectricityToAndFromGrid"], exchange)


@dataclass_json
@dataclass
class FakeLoggerConfig(ConfigBase):
    """A logger that observes other loggers."""

    MAIN_CLASS = "tests.assemblies.fixture_components.FakeLogger"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "FakeLoggerConfig":
        """A logger."""
        return cls(component_id=ComponentID(name=name))


class FakeLogger(FixtureAggregator):
    """Declares a feed from its own class, so an observer's own outputs are a candidate of nobody but another logger."""

    CHANNELS = (
        DynamicConnectionChannel(
            key="readings",
            tags=frozenset({lt.InandOutputType.ELECTRICITY_PRODUCTION}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    CLASS_INTERFACE = ClassInterface(
        outputs=(DeclaredPort("Reading", lt.LoadTypes.ELECTRICITY, lt.Units.WATT),),
        default_feeds=(DeclaredFeed("FakeLogger", "Reading", ("ELECTRICITY_PRODUCTION",), 999),),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: FakeLoggerConfig) -> None:
        """Builds the logger."""
        super().__init__(my_simulation_parameters, config)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """The sum of what it reads."""
        stsv.set_output_value(self.ports_out["Reading"], self.channel_sum(stsv, "readings"))
