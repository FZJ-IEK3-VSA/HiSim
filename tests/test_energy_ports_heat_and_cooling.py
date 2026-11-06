"""The heat and cooling devices of Sankey PR 5a declare their energy ports, and one step of each books them.

Complete Sankey spec, PR 5a: the generic heat pump, the air conditioner, the simple air conditioner, the idealized
electric heater and the simple heat source state the energy they take, give and reject as energy ports. A device that
heats and cools publishes the two directions of its heat to the building as two non-negative outputs (decision D20 of
``roadmap/fluid_loops_spec.md``), and the outside air as its source while heating and its sink while cooling, two
boundary nodes (owner decision Q-M of ``roadmap/complete_energy_sankeys_spec.md``); the simple heat source takes its
heat from a "heat source" node of its own (Q-O). Each
class is driven here for one step with fake inputs, and its published ports are summed: consumption minus production
must be zero, as the energy balance requires in every step of a run. ``tests/test_energy_balance_heat_and_cooling.py``
runs the three setups that use the first three; the heater and the heat source run in no recorded setup.

The simple heat source supplies a brine loop, which carries no energy output: the balance derives its heat from the
loop's flow and temperatures with the brine's specific heat (``LoopFluid.BRINE``), so the test does the same.
"""

from typing import Callable, Dict

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.air_conditioner import AirConditioner, AirConditionerConfig
from hisim.components.generic_heat_pump import GenericHeatPump, GenericHeatPumpConfig
from hisim.components.idealized_electric_heater import IdealizedElectricHeater, IdealizedHeaterConfig
from hisim.components.simple_air_conditioner import SimpleAirConditioner, SimpleAirConditionerConfig
from hisim.components.simple_heat_source import SimpleHeatSource, SimpleHeatSourceConfig
from hisim.energy_port import LoopFluid, LoopQuantity
from tests.test_energy_ports_hydrogen_and_chp import OneStep

pytestmark = pytest.mark.base


class Roles:
    """The role and carrier pairs the devices' ports use."""

    ELECTRICITY_IN = (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY)
    SPACE_HEATING_OUT = (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT)
    COOLING_IN = (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.COOLING)
    AMBIENT_IN = (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.AMBIENT_HEAT)
    AMBIENT_OUT = (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.AMBIENT_HEAT)


def closing_step(step: OneStep, inputs: Dict[str, float], timestep: int = 0) -> Dict[str, float]:
    """Run the component of ``step`` for one step with ``inputs`` and assert that its ports close.

    Args:
        step: The component with its fake sources.
        inputs: The value of each input by its name.
        timestep: The step's index.

    Returns:
        The component's outputs by name.
    """
    values = step.run(inputs, timestep)
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-9)
    return values


def test_the_generic_heat_pump_heats_from_the_air_and_cools_into_it() -> None:
    """Heating at 2 °C takes the heat minus the electricity from the air; cooling at 30 °C rejects both to it.

    Catches the ambient heat taken as the whole heat (the electricity booked twice), cooling rejected without its
    electricity, and a direction that publishes into the other direction's output.
    """
    parameters = OneStep.parameters()
    heat_pump = OneStep(GenericHeatPump(parameters, GenericHeatPumpConfig.preset_vitocal_300_a("HeatPump")))
    assert heat_pump.ports() == {
        GenericHeatPump.ElectricityOutput: Roles.ELECTRICITY_IN,
        GenericHeatPump.Heating: Roles.SPACE_HEATING_OUT,
        GenericHeatPump.HeatDrawnForCoolingInWatt: Roles.COOLING_IN,
        GenericHeatPump.HeatFromAmbientInWatt: Roles.AMBIENT_IN,
        GenericHeatPump.HeatToAmbientInWatt: Roles.AMBIENT_OUT,
    }
    # it may start once it has been idle for its minimum idle time, one step of 900 s
    heating = closing_step(heat_pump, {GenericHeatPump.State: 1.0, GenericHeatPump.TemperatureOutside: 2.0}, timestep=1)
    assert heating[GenericHeatPump.Heating] > 0.0
    assert heating[GenericHeatPump.HeatFromAmbientInWatt] == pytest.approx(
        heating[GenericHeatPump.Heating] - heating[GenericHeatPump.ElectricityOutput], rel=1e-12
    )
    assert heating[GenericHeatPump.HeatFromAmbientInWatt] > 0.0
    assert heating[GenericHeatPump.HeatDrawnForCoolingInWatt] == heating[GenericHeatPump.HeatToAmbientInWatt] == 0.0

    cooler = OneStep(GenericHeatPump(parameters, GenericHeatPumpConfig.preset_vitocal_300_a("HeatPump")))
    cooling = closing_step(cooler, {GenericHeatPump.State: -1.0, GenericHeatPump.TemperatureOutside: 30.0}, timestep=1)
    assert cooling[GenericHeatPump.HeatDrawnForCoolingInWatt] == -cooling[GenericHeatPump.Cooling] > 0.0
    assert cooling[GenericHeatPump.HeatToAmbientInWatt] == pytest.approx(
        cooling[GenericHeatPump.HeatDrawnForCoolingInWatt] + cooling[GenericHeatPump.ElectricityOutput], rel=1e-12
    )
    assert cooling[GenericHeatPump.Heating] == cooling[GenericHeatPump.HeatFromAmbientInWatt] == 0.0


@pytest.mark.parametrize(("signal", "outside_in_celsius"), [(1.0, 5.0), (-1.0, 30.0), (0.0, 15.0)])
def test_the_air_conditioner_books_both_directions_and_the_air(signal: float, outside_in_celsius: float) -> None:
    """A heating, a cooling and an idle step each close, and only the direction of the step carries heat.

    Catches the signed ThermalPowerDelivered split into the wrong output, and cooling rejected without its electricity.
    """
    conditioner = OneStep(AirConditioner(OneStep.parameters(), AirConditionerConfig.preset_samsung_ac120("AirConditioner")))
    assert conditioner.ports() == {
        AirConditioner.ElectricalPowerConsumption: Roles.ELECTRICITY_IN,
        AirConditioner.HeatDeliveredInWatt: Roles.SPACE_HEATING_OUT,
        AirConditioner.HeatDrawnForCoolingInWatt: Roles.COOLING_IN,
        AirConditioner.HeatFromAmbientInWatt: Roles.AMBIENT_IN,
        AirConditioner.HeatToAmbientInWatt: Roles.AMBIENT_OUT,
    }
    values = closing_step(
        conditioner,
        {AirConditioner.ModulatingPowerSignal: signal, AirConditioner.OutdoorAirTemperature: outside_in_celsius},
    )
    delivered = values[AirConditioner.ThermalPowerDelivered]
    assert values[AirConditioner.HeatDeliveredInWatt] == max(0.0, delivered)
    assert values[AirConditioner.HeatDrawnForCoolingInWatt] == max(0.0, -delivered)
    assert (delivered > 0.0) == (signal > 0.0) and (delivered < 0.0) == (signal < 0.0)
    assert values[AirConditioner.HeatFromAmbientInWatt] >= 0.0 and values[AirConditioner.HeatToAmbientInWatt] >= 0.0


def test_the_simple_air_conditioner_rejects_the_heat_it_draws_and_its_electricity() -> None:
    """Cooling 2 kW at 32 °C outside and 26 °C inside rejects 2 kW plus the Carnot-scaled electricity to the air.

    Catches heat rejected without the electricity and a cooling output of the wrong sign.
    """
    conditioner = OneStep(SimpleAirConditioner(OneStep.parameters(), SimpleAirConditionerConfig.preset_standard("SimpleAirConditioner")))
    assert conditioner.ports() == {
        SimpleAirConditioner.ElectricalPowerConsumption: Roles.ELECTRICITY_IN,
        SimpleAirConditioner.HeatDrawnForCoolingInWatt: Roles.COOLING_IN,
        SimpleAirConditioner.HeatToAmbientInWatt: Roles.AMBIENT_OUT,
    }
    values = closing_step(
        conditioner,
        {
            SimpleAirConditioner.ModulatingPowerSignal: -1.0,
            SimpleAirConditioner.TemperatureOutside: 32.0,
            SimpleAirConditioner.TemperatureIndoorAir: 26.0,
        },
    )
    assert values[SimpleAirConditioner.HeatDrawnForCoolingInWatt] == 2000.0
    assert values[SimpleAirConditioner.HeatToAmbientInWatt] == pytest.approx(
        2000.0 + values[SimpleAirConditioner.ElectricalPowerConsumption], rel=1e-12
    )
    assert values[SimpleAirConditioner.ElectricalPowerConsumption] > 0.0


@pytest.mark.parametrize("thermal_power_set_in_watt", [3000.0, -2000.0, 0.0])
def test_the_idealized_heater_heats_with_electricity_and_cools_into_the_air(thermal_power_set_in_watt: float) -> None:
    """Heating turns as much electricity into space heating; cooling rejects the drawn heat to the air without electricity.

    Catches electricity booked for cooling, and heat delivered from no electricity.
    """
    heater = OneStep(IdealizedElectricHeater(OneStep.parameters(), IdealizedHeaterConfig.preset_standard("IdealizedHeater")))
    assert heater.ports() == {
        IdealizedElectricHeater.ElectricityConsumptionInWatt: Roles.ELECTRICITY_IN,
        IdealizedElectricHeater.HeatingPowerDelivered: Roles.SPACE_HEATING_OUT,
        IdealizedElectricHeater.HeatDrawnForCoolingInWatt: Roles.COOLING_IN,
        IdealizedElectricHeater.HeatToAmbientInWatt: Roles.AMBIENT_OUT,
    }
    values = closing_step(heater, {IdealizedElectricHeater.ThermalPowerSetInWatt: thermal_power_set_in_watt})
    assert values[IdealizedElectricHeater.ElectricityConsumptionInWatt] == max(0.0, thermal_power_set_in_watt)
    assert values[IdealizedElectricHeater.HeatToAmbientInWatt] == max(0.0, -thermal_power_set_in_watt)


@pytest.mark.parametrize("returning_brine_in_celsius", [5.0, 12.0])
def test_the_simple_heat_source_gives_the_brine_loop_the_heat_it_takes_from_the_environment(
    returning_brine_in_celsius: float,
) -> None:
    """At a 10 °C day the ground collector supplies 8.67 °C brine: colder returning brine takes heat, warmer gives it.

    The source tags its brine loop (the flow it reads and the flow it publishes, the returning temperature it reads and
    the supply it publishes) and pumps it by default, and its ambient ports carry the loop's heat, ``m c (T_out - T_in)`` with the brine's specific heat,
    within the difference between that and the configured mixture's (pygfunction's 3976.77 against 3976.8 J/(kg K)).
    Catches a loop tagged by half or of the wrong fluid, and ambient heat of the wrong sign or size.
    """
    source = SimpleHeatSource(OneStep.parameters(), SimpleHeatSourceConfig.preset_near_surface_brine("HeatSource"))
    step = OneStep(source)
    assert step.ports() == {
        SimpleHeatSource.HeatFromAmbientInWatt: Roles.AMBIENT_IN,
        SimpleHeatSource.HeatToAmbientInWatt: Roles.AMBIENT_OUT,
    }
    port_sets = source.port_sets()
    assert len(port_sets) == 1
    port_set = port_sets[0]
    assert (port_set.loop.name, port_set.loop.carrier, port_set.loop.fluid, port_set.loop.supplies, port_set.loop.pumps) == (
        "brine",
        lt.EnergyBalanceCarrier.AMBIENT_HEAT,
        LoopFluid.BRINE,
        True,
        True,
    )
    assert {quantity: channel.field_name for quantity, channel in port_set.channels.items()} == {
        LoopQuantity.FLOW_IN: SimpleHeatSource.MassFlow,
        LoopQuantity.FLOW_OUT: SimpleHeatSource.MassFlowOutput,
        LoopQuantity.TEMPERATURE_IN: SimpleHeatSource.TemperatureInput,
        LoopQuantity.TEMPERATURE_OUT: SimpleHeatSource.TemperatureOutput,
    }
    values = step.run(
        {
            SimpleHeatSource.DailyAverageOutsideTemperature: 10.0,
            SimpleHeatSource.MassFlow: 0.5,
            SimpleHeatSource.TemperatureInput: returning_brine_in_celsius,
        },
    )
    loop_heat_in_watt = (
        values[SimpleHeatSource.MassFlowOutput]
        * LoopFluid.BRINE.specific_heat_j_per_kg_k
        * (values[SimpleHeatSource.TemperatureOutput] - returning_brine_in_celsius)
    )
    net_ambient_in_watt = values[SimpleHeatSource.HeatFromAmbientInWatt] - values[SimpleHeatSource.HeatToAmbientInWatt]
    assert net_ambient_in_watt == pytest.approx(loop_heat_in_watt, rel=1e-5)
    assert (values[SimpleHeatSource.HeatFromAmbientInWatt] > 0.0) == (returning_brine_in_celsius < 8.0)
    assert min(values[SimpleHeatSource.HeatFromAmbientInWatt], values[SimpleHeatSource.HeatToAmbientInWatt]) == 0.0


@pytest.mark.parametrize(
    ("make_device", "boundaries"),
    [
        (
            lambda: GenericHeatPump(OneStep.parameters(), GenericHeatPumpConfig.preset_vitocal_300_a("HeatPump")),
            {"HeatFromAmbientInWatt": "ambient heat source", "HeatToAmbientInWatt": "ambient heat sink"},
        ),
        (
            lambda: AirConditioner(OneStep.parameters(), AirConditionerConfig.preset_samsung_ac120("AirConditioner")),
            {"HeatFromAmbientInWatt": "ambient heat source", "HeatToAmbientInWatt": "ambient heat sink"},
        ),
        (
            lambda: SimpleAirConditioner(OneStep.parameters(), SimpleAirConditionerConfig.preset_standard("SimpleAirConditioner")),
            {"HeatToAmbientInWatt": "ambient heat sink"},
        ),
        (
            lambda: IdealizedElectricHeater(OneStep.parameters(), IdealizedHeaterConfig.preset_standard("IdealizedHeater")),
            {"HeatToAmbientInWatt": "ambient heat sink"},
        ),
        (
            lambda: SimpleHeatSource(OneStep.parameters(), SimpleHeatSourceConfig.preset_near_surface_brine("HeatSource")),
            {"HeatFromAmbientInWatt": "heat source", "HeatToAmbientInWatt": "heat source"},
        ),
    ],
)
def test_the_outside_air_is_a_source_node_and_a_sink_node_and_the_ground_collector_has_its_own(
    make_device: Callable[[], cp.Component], boundaries: Dict[str, str]
) -> None:
    """Heat taken from the outside air comes from "ambient heat source", heat rejected to it goes to "ambient heat sink".

    Owner decisions Q-M and Q-O of the complete Sankey spec: the source and the sink of the outside air are two
    boundary nodes, so the Sankey draws the one left and the other right; the ground collector, a fixed source by
    design, books both directions of its brine's heat against its own "heat source" node. Catches a rejection booked
    on the source node, an intake on the sink node, and the collector's heat drawn from the outside air.
    """
    device = make_device()
    ambient = {
        output.field_name: output.energy_port.default_boundary()
        for output in device.outputs
        if output.energy_port is not None and output.energy_port.carrier == lt.EnergyBalanceCarrier.AMBIENT_HEAT
    }
    assert ambient == boundaries
