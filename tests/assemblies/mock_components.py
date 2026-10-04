"""Mock components for the mock assemblies: small, deterministic, and fully declared.

A mock assembly or a mock class exists only for a shape the real library cannot show; everything
that can run on a real assembly moves to the real library in step 4 (hisim-lt0b.5). What stays here
is test-only, and its names say so: the family ``mock/``, the classes ``Mock*``.

The mock assemblies under ``tests/assemblies/mock_assemblies/library`` are built from these classes
and nothing else (``assemblies_spec.md`` §13 step 1). Like every HiSim component, each mock creates
its inputs, its outputs and its default connections in its constructor (``add_input``,
``add_output``, ``add_default_connections``) and nowhere else; the post-construction port check of a
build reads them off the constructed instance. Its configuration declares the unit of every field an
assembly parameter feeds (D16 b). :class:`MockBareDevice` declares no default connections at all,
for the refusal of a port lowered into a member without them.

The physics is a toy: a weather series, an occupancy drawing hot water and electricity, a PV array
producing from the temperature, a tank losing heat and filled by a heater a thermostat switches,
and an energy manager whose modifier raises the thermostat's set point. Every value converges in a
few iterations, so a one-day run is fast.

For the circuit, carrier and fact ports (hisim-lt0b.2) a gas boiler charges a cylinder over a
``dhw`` circuit — the boiler owns ``MassFlowDhw`` and ``SupplyTemperatureDhw``, the cylinder owns
``ReturnTemperatureDhw``, each reading the other's by its default connections — and burns natural
gas that a gas meter observes through the default feed its constructor declares; the boiler declares its
energy ports, fuel in, heat out and flue loss, so the energy-balance check closes its balance every
step. A battery sizes its capacity from the PV arrays' peak power, the fact the array contributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from dataclasses_json import dataclass_json

from hisim import loadtypes as lt
from hisim.component import Component, ComponentConnection, ComponentInput, ComponentOutput, SingleTimeStepValues
from hisim.config import (
    ComponentID,
    ConfigBase,
    DisplayConfig,
    FactContribution,
    Many,
    Sizable,
    Size,
    Sum,
    concrete,
    preset,
    sized_field,
)
from hisim.config.channels import DispatchRule, DynamicConnectionChannel, ObservableFeed
from hisim.dynamic_component import (
    DynamicComponent,
    DynamicComponentConnection,
    DynamicConnectionInput,
    DynamicConnectionOutput,
)
from hisim.components.controller_l2_energy_management_system import L2GenericEnergyManagementSystem
from hisim.components.generic_boiler import BoilerType, GenericBoilerConfig
from hisim.economics.facts import CostRelevance
from hisim.energy_port import EnergyPort
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass
from hisim.simulationparameters import SimulationParameters

#: The metadata key a configuration field declares its unit under (``SizedFieldMetadata.UNIT``).
UNIT = "unit"


class MockComponent(Component):
    """The shared behaviour of every mock: helpers its constructor adds ports and default connections with."""

    cost_relevance = CostRelevance.FREE_OF_COST

    #: KPI name to ``(output, factor)``: the KPI is the output's sum over the run times the factor.
    SUM_KPIS: Dict[str, tuple] = {}

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
        self, name: str, load_type: lt.LoadTypes, unit: lt.Units, energy_port: Optional[EnergyPort] = None
    ) -> None:
        """Adds one output (``add_output``), with its energy port where it carries energy."""
        self.ports_out[name] = self.add_output(
            self.component_name, name, load_type, unit, energy_port=energy_port, output_description=name
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

    #: The array's peak power, which a battery beside it is sized from.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=("pv_peak_power_in_watt",),
            compute=lambda config, ctx: {"pv_peak_power_in_watt": config.peak_power_in_watt()},
        ),
    )

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
        self.output_port("ElectricityOutput", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
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
    SUM_KPIS = {STANDBY_KPI: ("HeatLoss", 0.25e-3)}

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
    SUM_KPIS = {ENERGY_KPI: ("ElectricityInput", 0.25e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockHeaterConfig) -> None:
        """Builds the heater."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("Signal", lt.LoadTypes.ON_OFF, lt.Units.ANY, mandatory=False)
        # Electricity in, space-heating heat out, one for one: the balance closes every step.
        self.output_port(
            "ThermalPower",
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        )
        self.output_port(
            "ElectricityInput",
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.ELECTRICITY),
        )
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

#: The ``dhw`` circuit's three outputs (``hisim/energy_system/imports_model.py``, ``CircuitNaming``).
MASS_FLOW_DHW = "MassFlowDhw"
SUPPLY_TEMPERATURE_DHW = "SupplyTemperatureDhw"
RETURN_TEMPERATURE_DHW = "ReturnTemperatureDhw"


def fuel_facts(carrier: lt.LoadTypes, boiler_type: BoilerType) -> Dict[str, Any]:
    """The three fuel facts of a burner, derived as the real boiler derives them."""
    heating_value, density = GenericBoilerConfig.fuel_constants(carrier, boiler_type)
    return {
        "energy_carrier": carrier,
        "heating_value_of_fuel_in_kwh_per_liter": heating_value,
        "fuel_density_in_kg_per_m3": density,
    }


@dataclass_json
@dataclass
class MockBoilerConfig(ConfigBase):
    """A gas boiler charging a cylinder over the dhw circuit."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockBoiler"

    component_id: ComponentID
    power_in_watt: float = field(default=3000.0, metadata={UNIT: lt.Units.WATT})
    efficiency: float = field(default=0.9, metadata={UNIT: lt.Units.ANY})

    #: The natural gas it burns, for the meter accounting it: a condensing gas boiler's constants,
    #: derived the way the real boiler derives them (``GenericBoilerConfig.fuel_constants``).
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=("energy_carrier", "heating_value_of_fuel_in_kwh_per_liter", "fuel_density_in_kg_per_m3"),
            compute=lambda config, ctx: fuel_facts(lt.LoadTypes.GAS, BoilerType.CONDENSING),
        ),
    )

    @preset
    @classmethod
    def preset_condensing(cls, name: str) -> "MockBoilerConfig":
        """A 3 kW condensing boiler."""
        return cls(component_id=ComponentID(name=name))


class MockBoiler(MockComponent):
    """Owns the dhw circuit's mass flow and supply leg, reads its return leg, and burns natural gas.

    Every step it heats at a schedule's power; the gas it burns is that heat over its efficiency,
    and the rest leaves as flue loss, so its energy ports — gas in, heat out, flue loss — balance.

    Stands in for the real ``GenericBoiler`` in the mock assemblies.
    """

    FUEL_KPI = "Boiler fuel"

    SUM_KPIS = {FUEL_KPI: ("FuelUse", 1e-3)}

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
            EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT, peer_output=MASS_FLOW_DHW),
        )
        self.output_port(
            "FuelUse",
            lt.LoadTypes.GAS,
            lt.Units.WATT_HOUR,
            EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.NATURAL_GAS, peer_output="FuelUse"),
        )
        self.output_port(
            "FlueLoss",
            lt.LoadTypes.GAS,
            lt.Units.WATT,
            EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.NATURAL_GAS),
        )
        self.defaults_from("MockCylinder", {RETURN_TEMPERATURE_DHW: RETURN_TEMPERATURE_DHW})

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

    TEMPERATURE_KPI = "Cylinder heat received"

    SUM_KPIS = {TEMPERATURE_KPI: ("HeatReceived", 0.25e-3)}

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


# ----------------------------------------------------------------------------------------- gas meter


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


class MockGasMeter(DynamicComponent):
    """Sums the gas its consumers burn, as the real gas meter does, on one declared channel.

    Stands in for the real ``GasMeter`` in the mock assemblies.
    """

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

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockGasMeterConfig) -> None:
        """Builds the meter and its declared default feeds, one per consuming class."""
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
                    source_component_class=MockBoiler,
                    source_class_name=MockBoiler.get_classname(),
                    source_component_field_name="FuelUse",
                    source_load_type=lt.LoadTypes.GAS,
                    source_unit=lt.Units.WATT_HOUR,
                    source_tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
                    source_weight=999,
                )
            ]
        )
        self.add_dynamic_default_connections(
            [
                DynamicComponentConnection(
                    source_component_class=MockBurner,
                    source_class_name=MockBurner.get_classname(),
                    source_component_field_name="FuelUse",
                    source_load_type=lt.LoadTypes.GAS,
                    source_unit=lt.Units.WATT_HOUR,
                    source_tags=[lt.InandOutputType.GAS_CONSUMPTION_UNCONTROLLED],
                    source_weight=999,
                ),
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
    """Publishes its capacity, and charges at the power an energy manager writes into ``LoadingPowerInput``.

    The toy has no state: the AC power it draws (positive) or delivers (negative) is the requested
    power, limited to its capacity in kWh taken as kW. Without a controller the input stays
    unconnected and the battery idles.

    Stands in for the real ``Battery`` in the mock assemblies.
    """

    CAPACITY_KPI = "Battery capacity"

    SUM_KPIS = {CAPACITY_KPI: ("Capacity", 1.0 / 96.0)}

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
    """The mock battery, sized by a sum over the arrays (a many read) instead of one array.

    Stands in for a battery sized to every PV array beside it in the mock assemblies.
    """

    def __init__(  # pylint: disable=useless-parent-delegation  # the annotation names the config class
        self, my_simulation_parameters: SimulationParameters, config: MockArrayBatteryConfig
    ) -> None:
        """Builds the battery."""
        super().__init__(my_simulation_parameters, config)  # type: ignore[arg-type]


# ------------------------------------------------------------------- burners and a buffer (§6)


@dataclass_json
@dataclass
class MockBurnerConfig(ConfigBase):
    """A gas burner of a fixed power: a heat generator's power and fuel, for sizing across assemblies."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockBurner"

    component_id: ComponentID
    power_in_watt: float = field(default=10000.0, metadata={UNIT: lt.Units.WATT})
    boiler_type: BoilerType = BoilerType.CONDENSING

    #: Its power, which a buffer beside it is sized from, and the gas it burns, for its meter.
    SIZING_CONTRIBUTIONS: ClassVar[Tuple[FactContribution, ...]] = (
        FactContribution(
            facts=("maximal_thermal_power_in_watt",),
            compute=lambda config, ctx: {"maximal_thermal_power_in_watt": config.power_in_watt},
        ),
        FactContribution(
            facts=("energy_carrier", "heating_value_of_fuel_in_kwh_per_liter", "fuel_density_in_kg_per_m3"),
            compute=lambda config, ctx: fuel_facts(lt.LoadTypes.GAS, config.boiler_type),
        ),
    )

    @preset
    @classmethod
    def preset_condensing(cls, name: str) -> "MockBurnerConfig":
        """A 10 kW condensing gas burner."""
        return cls(component_id=ComponentID(name=name))

    @preset
    @classmethod
    def preset_conventional(cls, name: str) -> "MockBurnerConfig":
        """A 10 kW conventional gas burner, whose gas has the lower heating value."""
        return cls(component_id=ComponentID(name=name), boiler_type=BoilerType.CONVENTIONAL)


class MockBurner(MockComponent):
    """Burns gas at a constant half of its power and loses a tenth of it up the flue.

    Stands in for a gas-fired heat generator in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockBurnerConfig) -> None:
        """Builds the burner: heat out, gas in, flue loss, each with its energy port."""
        super().__init__(my_simulation_parameters, config)
        self.output_port(
            "ThermalPower",
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            EnergyPort(lt.EnergyRole.OUT, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        )
        self.output_port(
            "FuelUse",
            lt.LoadTypes.GAS,
            lt.Units.WATT_HOUR,
            EnergyPort(lt.EnergyRole.IN, lt.EnergyBalanceCarrier.NATURAL_GAS, peer_output="FuelUse"),
        )
        self.output_port(
            "FlueLoss",
            lt.LoadTypes.GAS,
            lt.Units.WATT,
            EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.NATURAL_GAS),
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Half power, nine tenths of the fuel as heat; the fuel booked as energy per step, as the boiler books it."""
        fuel_power = 0.5 * self.config.power_in_watt
        self.set(stsv, "FuelUse", fuel_power * self.my_simulation_parameters.seconds_per_timestep / 3600.0)
        self.set(stsv, "ThermalPower", 0.9 * fuel_power)
        self.set(stsv, "FlueLoss", 0.1 * fuel_power)


@dataclass_json
@dataclass
class MockBufferConfig(ConfigBase):
    """A buffer vessel sized from the heat generator's power, as the real buffer's volume law reads it."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockBuffer"

    component_id: ComponentID
    #: Twenty litres per kilowatt of the generator it buffers, unless pinned.
    volume_in_liter: Sizable[float] = sized_field(
        rule=(Size.MAXIMAL_THERMAL_POWER_IN_WATT * 0.02).rounded(2), unit=lt.Units.LITER
    )

    @preset
    @classmethod
    def preset_sized_to_generator(cls, name: str) -> "MockBufferConfig":
        """Volume AUTO."""
        return cls(component_id=ComponentID(name=name))


class MockBuffer(MockComponent):
    """Holds water at a temperature that falls with its volume (a toy).

    Stands in for a heating buffer vessel in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockBufferConfig) -> None:
        """Builds the buffer."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("WaterTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A temperature from the volume."""
        self.set(stsv, "WaterTemperature", 60.0 - concrete(self.config.volume_in_liter) / 100.0)


# -------------------------------------------------------------------------------- a solar circuit


@dataclass_json
@dataclass
class MockCollectorConfig(ConfigBase):
    """A solar collector, one end of a ``solar`` circuit: an end of another medium than ``dhw``."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockCollector"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockCollectorConfig":
        """A collector."""
        return cls(component_id=ComponentID(name=name))


class MockCollector(MockComponent):
    """Owns the ``solar`` circuit's mass flow and supply leg.

    Stands in for the collector of a ``SolarThermalSystem`` in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockCollectorConfig) -> None:
        """Builds the collector."""
        super().__init__(my_simulation_parameters, config)
        self.input_port("ReturnTemperatureSolar", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS, mandatory=False)
        self.output_port("MassFlowSolar", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
        self.output_port("SupplyTemperatureSolar", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """No sun in the mock."""
        self.set(stsv, "MassFlowSolar", 0.0)
        self.set(stsv, "SupplyTemperatureSolar", self.value(stsv, "ReturnTemperatureSolar"))


# ------------------------------------------------------------------ dhw ends that do not fit a boiler


@dataclass_json
@dataclass
class MockDhwSinkConfig(ConfigBase):
    """A dhw end that reads the boiler's outputs but declares no default connections from it."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockDhwSink"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockDhwSinkConfig":
        """A sink."""
        return cls(component_id=ComponentID(name=name))


class MockDhwSink(MockComponent):
    """Reads ``MassFlowDhw`` and ``SupplyTemperatureDhw``, owns ``ReturnTemperatureDhw``, declares no defaults.

    Stands in for a dhw circuit end that declares no default connections in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockDhwSinkConfig) -> None:
        """Builds the sink: reads the boiler's two outputs, owns the return leg, no default connections."""
        super().__init__(my_simulation_parameters, config)
        self.input_port(MASS_FLOW_DHW, lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC)
        self.input_port(SUPPLY_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)
        self.output_port(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A constant return leg."""
        self.set(stsv, RETURN_TEMPERATURE_DHW, 40.0)


@dataclass_json
@dataclass
class MockDhwReturnConfig(ConfigBase):
    """A dhw end that only owns the return leg and reads nothing."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockDhwReturn"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockDhwReturnConfig":
        """A return leg."""
        return cls(component_id=ComponentID(name=name))


class MockDhwReturn(MockComponent):
    """Owns ``ReturnTemperatureDhw`` and reads none of the boiler's outputs.

    Stands in for a dhw circuit end that only owns the return leg in the mock assemblies.
    """

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockDhwReturnConfig) -> None:
        """Builds the end: owns the return leg, reads nothing."""
        super().__init__(my_simulation_parameters, config)
        self.output_port(RETURN_TEMPERATURE_DHW, lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS)

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """A constant return leg."""
        self.set(stsv, RETURN_TEMPERATURE_DHW, 40.0)


# ------------------------------------------------------------------- observers: an EMS and a meter


class MockAggregator(DynamicComponent):
    """The shared behaviour of the mock observers: outputs and dynamic default connections from the constructor.

    Like the real meter and energy manager, a mock observer states what it observes in its
    constructor — one ``add_dynamic_default_connections`` call per source class — and nowhere else;
    an assembly's selection reads those connections off the constructed observer.
    """

    cost_relevance = CostRelevance.FREE_OF_COST

    #: KPI name to ``(output, factor)``: the KPI is the output's sum over the run times the factor.
    SUM_KPIS: Dict[str, tuple] = {}

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

    def observe(self, source: type, outputs: Sequence[Tuple[str, Sequence[Any], int]]) -> None:
        """Declares the feeds from one source class, ``(output, tags, weight)`` each.

        One ``add_dynamic_default_connections`` call per source class, as the real observers make.

        Every observed output is an electricity power in watts, the one quantity the mock observers sum.
        """
        self.add_dynamic_default_connections(
            [
                DynamicComponentConnection(
                    source_component_class=source,
                    source_class_name=source.__name__,
                    source_component_field_name=output,
                    source_load_type=lt.LoadTypes.ELECTRICITY,
                    source_unit=lt.Units.WATT,
                    source_tags=list(tags),
                    source_weight=weight,
                )
                for output, tags, weight in outputs
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

#: The weight of a feed an observer only measures.
MEASURED = ObservableFeed.MEASURED_ONLY_WEIGHT


class MockEnergyManager(MockAggregator):
    """An energy manager of the real one's shape: its channels, its weight table, a grid balance and a modifier.

    It observes electricity flows, ranks the controllable ones by weight and writes each one's
    dispatch output — the surplus left at its rank — and sums what it observes into
    ``TotalElectricityToOrFromGrid``; its ``Modifier`` raises an L1's set point while there is surplus.
    The weights it declares are the real controller's (``L2GenericEnergyManagementSystem.DEFAULT_WEIGHTS``);
    a battery's feed is ranked on the storage channel, which takes a dispatch, so an assembly may state
    its ``LoadingPowerInput`` as the target the manager actuates directly (D21). Its per-feed dispatch
    outputs are named by the format's templates, ``DispatchTo<participant>_<input>`` and
    ``DispatchFor<participant>_<output>``: they exist per feed (hisim-lt0b.15).

    Stands in for the real ``L2GenericEnergyManagementSystem`` in the mock assemblies.
    """

    GRID_BALANCE_KPI = "Grid balance"

    CHANNELS = L2GenericEnergyManagementSystem.CHANNELS
    PRODUCTION_CHANNEL = L2GenericEnergyManagementSystem.PRODUCTION_CHANNEL
    CONSUMPTION_UNCONTROLLED_CHANNEL = L2GenericEnergyManagementSystem.CONSUMPTION_UNCONTROLLED_CHANNEL

    SUM_KPIS = {GRID_BALANCE_KPI: ("TotalElectricityToOrFromGrid", 0.25e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockEnergyManagerConfig) -> None:
        """Builds the energy manager: its two outputs and the flows it observes, at the real manager's weights."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("TotalElectricityToOrFromGrid", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        self.output_port("Modifier", lt.LoadTypes.TEMPERATURE, lt.Units.KELVIN)
        controlled = lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED
        self.observe(
            MockOccupancy,
            [
                (
                    "ElectricityConsumption",
                    [lt.ComponentType.RESIDENTS, controlled],
                    EMS_WEIGHTS[lt.ComponentType.RESIDENTS],
                )
            ],
        )
        self.observe(
            MockPVSystem,
            [("ElectricityOutput", [lt.ComponentType.PV, lt.InandOutputType.ELECTRICITY_PRODUCTION], MEASURED)],
        )
        self.observe(
            MockHeater,
            [
                (
                    "ElectricityInput",
                    [lt.ComponentType.ELECTRIC_HEATING_SH, controlled],
                    EMS_WEIGHTS[lt.ComponentType.ELECTRIC_HEATING_SH],
                )
            ],
        )
        self.observe(
            MockBattery,
            [("AcBatteryPowerUsed", [lt.ComponentType.BATTERY, controlled], EMS_WEIGHTS[lt.ComponentType.BATTERY])],
        )

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production minus every consumption is the balance; the surplus before the ranked ones is dispatched."""
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

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockElectricityMeterConfig":
        """A meter."""
        return cls(component_id=ComponentID(name=name))


class MockElectricityMeter(MockAggregator):
    """Books production minus consumption as grid exchange, on the real meter's two channels.

    It declares the flows it may observe — the residents, a PV array and a heater — and an energy
    manager's grid balance on the production channel at 999, the feed the real meter declares from
    the real EMS (``assemblies_spec.md`` §4.3, dry run G6).

    Stands in for the real ``ElectricityMeter`` in the mock assemblies.
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

    SUM_KPIS = {EXCHANGE_KPI: ("ElectricityToAndFromGrid", 0.25e-3)}

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockElectricityMeterConfig) -> None:
        """Builds the meter: its exchange output and every flow it may observe, all measured."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("ElectricityToAndFromGrid", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        uncontrolled = lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
        production = lt.InandOutputType.ELECTRICITY_PRODUCTION
        self.observe(MockOccupancy, [("ElectricityConsumption", [uncontrolled], MEASURED)])
        self.observe(MockPVSystem, [("ElectricityOutput", [lt.ComponentType.PV, production], MEASURED)])
        self.observe(
            MockHeater, [("ElectricityInput", [lt.ComponentType.ELECTRIC_HEATING_SH, uncontrolled], MEASURED)]
        )
        self.observe(MockEnergyManager, [("TotalElectricityToOrFromGrid", [production], MEASURED)])

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """Production minus consumption."""
        exchange = self.channel_sum(stsv, "production") - self.channel_sum(stsv, "consumption_uncontrolled")
        stsv.set_output_value(self.ports_out["ElectricityToAndFromGrid"], exchange)


@dataclass_json
@dataclass
class MockLoggerConfig(ConfigBase):
    """A logger that observes other loggers."""

    MAIN_CLASS = "tests.assemblies.mock_components.MockLogger"

    component_id: ComponentID

    @preset
    @classmethod
    def preset_standard(cls, name: str) -> "MockLoggerConfig":
        """A logger."""
        return cls(component_id=ComponentID(name=name))


class MockLogger(MockAggregator):
    """Declares a feed from its own class, so an observer's own outputs are a candidate of nobody but another logger.

    Stands in for an observer that observes other observers in the mock assemblies.
    """

    CHANNELS = (
        DynamicConnectionChannel(
            key="readings",
            tags=frozenset({lt.InandOutputType.ELECTRICITY_PRODUCTION}),
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            dispatch=DispatchRule.FORBIDDEN,
        ),
    )

    def __init__(self, my_simulation_parameters: SimulationParameters, config: MockLoggerConfig) -> None:
        """Builds the logger: its reading, and a feed from other loggers' readings."""
        super().__init__(my_simulation_parameters, config)
        self.output_port("Reading", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        self.observe(MockLogger, [("Reading", [lt.InandOutputType.ELECTRICITY_PRODUCTION], MEASURED)])

    def i_simulate(self, timestep: int, stsv: SingleTimeStepValues, force_convergence: bool) -> None:
        """The sum of what it reads."""
        stsv.set_output_value(self.ports_out["Reading"], self.channel_sum(stsv, "readings"))
