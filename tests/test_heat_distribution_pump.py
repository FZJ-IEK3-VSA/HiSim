"""The heat distribution system's own pump stands while its controller's valve is shut.

Where the heat distribution system pumps its design flow itself (a buffer in front of it, or a district-heating
substation without a pump), it used to go on pumping with the valve shut and returned its inlet cooled by a hundredth
of the difference to the room. The buffer then booked heat into a circuit that delivered none to the building. With
the valve shut the circuit now moves no water and returns its inlet unchanged.
"""

from dataclasses import dataclass
from typing import ClassVar, Mapping

import pytest

from hisim import loadtypes as lt
from hisim.components import heat_distribution_system
from hisim.components.heat_distribution_system import HeatDistribution, PositionHotWaterStorageInSystemSetup
from hisim.config import ComponentID
from hisim.energy_port import EnergyPort, LoopQuantity
from hisim.simulationparameters import SimulationParameters
from tests.part_load_rigs import AllInputsRig


@dataclass(frozen=True)
class HdsStep:
    """The outputs one heat-distribution step published, by field name."""

    #: Every output of the step, by its field name; read-only.
    outputs: Mapping[str, float]


class HdsRig:
    """A heat distribution system on fake inputs, simulated for single steps.

    The rig wires the four inputs a circuit that pumps its own design flow reads (the controller's valve state, the
    supply temperature, the building's theoretical demand and the indoor temperature) to fake outputs, through
    :class:`tests.part_load_rigs.AllInputsRig`, and runs ``i_simulate`` once per call. It serves both such positions:
    behind a buffer (``PARALLEL``) and at a district-heating substation without a pump (``NO_STORAGE_MASS_FLOW_FIX``).
    """

    #: The design flow of the rig's circuit, in kg/s.
    DESIGN_MASS_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.27

    #: The floor area the rig's circuit heats, in m².
    FLOOR_AREA_IN_M2: ClassVar[float] = 121.2

    def __init__(
        self, position: PositionHotWaterStorageInSystemSetup, seconds_per_timestep: int = 900, *, cooling_enabled: bool = False
    ) -> None:
        """Build the heat distribution system and its fake sources.

        Args:
            position: Where the storage sits, which decides who pumps.
            seconds_per_timestep: The step length, in s.
            cooling_enabled: Whether the circuit may cool; off, as in every preset, unless a test cools.
        """
        config = heat_distribution_system.HeatDistributionConfig(
            component_id=ComponentID(name="HeatDistributionSystem"),
            water_mass_flow_rate_in_kg_per_second=self.DESIGN_MASS_FLOW_IN_KG_PER_SECOND,
            position_hot_water_storage_in_system=position,
            absolute_conditioned_floor_area_in_m2=self.FLOOR_AREA_IN_M2,
            heating_system=heat_distribution_system.HeatDistributionSystemType.FLOORHEATING,
            cooling_enabled=cooling_enabled,
        )
        self.hds = HeatDistribution(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep)
        )
        self.stsv, self.fakes = AllInputsRig.build(self.hds)

    def step(
        self,
        *,
        valve_state: float,
        supply_in_celsius: float,
        demand_in_watt: float,
        room_in_celsius: float,
        part_load_ratio: float = 1.0,
    ) -> HdsStep:
        """Run one step from the saved state and save it, as the simulator does at the end of a converged step.

        Args:
            valve_state: The controller's valve state: 1 heating, -1 cooling, 0 shut.
            supply_in_celsius: The supply temperature the circuit receives, in °C.
            demand_in_watt: The building's theoretical demand, in W.
            room_in_celsius: The indoor air temperature, in °C.
            part_load_ratio: The fraction of the step the controller commands, 1 by default.

        Returns:
            The published outputs.
        """
        values = {
            HeatDistribution.State: valve_state,
            HeatDistribution.WaterTemperatureInput: supply_in_celsius,
            HeatDistribution.TheoreticalThermalBuildingDemand: demand_in_watt,
            HeatDistribution.ResidenceTemperatureIndoorAir: room_in_celsius,
            HeatDistribution.RequestedPartLoadRatio: part_load_ratio,
        }
        self.hds.i_restore_state()
        outputs = AllInputsRig.step(self.hds, self.stsv, self.fakes, values)
        self.hds.i_save_state()
        return HdsStep(outputs=outputs)


@pytest.mark.base
@pytest.mark.parametrize("position", HeatDistribution.POSITIONS_PUMPING_THE_DESIGN_FLOW)
@pytest.mark.parametrize("demand_in_watt", [0.0, 3000.0])
def test_a_shut_valve_stops_the_pump_and_the_circuit_exchanges_nothing(
    position: PositionHotWaterStorageInSystemSetup, demand_in_watt: float
) -> None:
    """With the valve shut no water moves and the circuit returns its inlet unchanged, with or without a demand.

    A demand with a shut valve is, for example, a step outside the heating season. Two steps run with the valve shut,
    so the second publishes the first's values too: the circuit's outputs lag one step, which is unchanged here.
    """
    rig = HdsRig(position)
    for _ in range(2):
        step = rig.step(valve_state=0, supply_in_celsius=35.0, demand_in_watt=demand_in_watt, room_in_celsius=20.0)
    assert step.outputs[HeatDistribution.WaterMassFlowHDS] == 0.0
    assert step.outputs[HeatDistribution.ThermalPowerDelivered] == 0.0
    assert step.outputs[HeatDistribution.WaterTemperatureOutput] == 35.0
    assert step.outputs[HeatDistribution.WaterTemperatureInlet] == 35.0


@pytest.mark.base
@pytest.mark.parametrize("position", HeatDistribution.POSITIONS_PUMPING_THE_DESIGN_FLOW)
@pytest.mark.parametrize(
    ("valve_state", "supply_in_celsius", "demand_in_watt", "room_in_celsius"),
    [(1, 35.0, 3000.0, 20.0), (-1, 16.0, -2000.0, 26.0)],
    ids=["heating", "cooling"],
)
def test_an_open_valve_pumps_the_design_flow(
    position: PositionHotWaterStorageInSystemSetup,
    valve_state: float,
    supply_in_celsius: float,
    demand_in_watt: float,
    room_in_celsius: float,
) -> None:
    """With the valve open, for heating or cooling, the circuit pumps its design flow and delivers the demand.

    The delivered heat is read on the second step, since the outputs lag one step. For cooling it is negative: 2000 W
    taken out of a room at 26 °C by water supplied at 16 °C.
    """
    rig = HdsRig(position, cooling_enabled=True)
    inputs = {
        "valve_state": valve_state,
        "supply_in_celsius": supply_in_celsius,
        "demand_in_watt": demand_in_watt,
        "room_in_celsius": room_in_celsius,
    }
    rig.step(**inputs)
    step = rig.step(**inputs)
    assert step.outputs[HeatDistribution.WaterMassFlowHDS] == HdsRig.DESIGN_MASS_FLOW_IN_KG_PER_SECOND
    assert step.outputs[HeatDistribution.ThermalPowerDelivered] == pytest.approx(demand_in_watt)


@pytest.mark.base
@pytest.mark.parametrize("position", HeatDistribution.POSITIONS_PUMPING_THE_DESIGN_FLOW)
def test_a_circuit_that_pumps_its_own_design_flow_refuses_a_design_flow_of_zero(
    position: PositionHotWaterStorageInSystemSetup,
) -> None:
    """A design flow of 0 kg/s, for example a tiny one rounded to two decimals, is refused when the system is built.

    Such a circuit would move no water at any valve state, so the run would deliver no heat to the building without a
    word.
    """
    config = heat_distribution_system.HeatDistributionConfig(
        component_id=ComponentID(name="HeatDistributionSystem"),
        water_mass_flow_rate_in_kg_per_second=0.0,
        position_hot_water_storage_in_system=position,
        absolute_conditioned_floor_area_in_m2=HdsRig.FLOOR_AREA_IN_M2,
        heating_system=heat_distribution_system.HeatDistributionSystemType.FLOORHEATING,
        cooling_enabled=False,
    )
    with pytest.raises(ValueError, match="water_mass_flow_rate_in_kg_per_second is 0.0 kg/s"):
        HeatDistribution(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900))


@pytest.mark.base
@pytest.mark.parametrize(("valve_state", "stops"), [(0, True), (1, False), (-1, False)])
def test_only_a_shut_valve_stops_the_pump(valve_state: float, stops: bool) -> None:
    """The pump stands for the valve state 0 only."""
    assert HeatDistribution.pump_stopped_by_the_valve(valve_state) is stops


@pytest.mark.base
def test_behind_a_buffer_the_circuit_runs_the_commanded_fraction_of_its_flow_and_heat() -> None:
    """At a ratio of 0.4 the circuit pumps 0.4 of its design flow, delivers 0.4 of the demand and reports 0.4.

    Its return is that of the whole step, so the heat the buffer books, the flow times ``c`` times the drop, is the
    delivered heat.
    """
    rig = HdsRig(PositionHotWaterStorageInSystemSetup.PARALLEL)
    whole = rig.step(valve_state=1, supply_in_celsius=35.0, demand_in_watt=3000.0, room_in_celsius=20.0)
    part = rig.step(
        valve_state=1, supply_in_celsius=35.0, demand_in_watt=3000.0, room_in_celsius=20.0, part_load_ratio=0.4
    )
    design_flow_in_kg_per_second = HdsRig.DESIGN_MASS_FLOW_IN_KG_PER_SECOND
    assert part.outputs[HeatDistribution.WaterMassFlowHDS] == pytest.approx(0.4 * design_flow_in_kg_per_second)
    assert part.outputs[HeatDistribution.ThermalPowerDelivered] == pytest.approx(1200.0)
    outlet = HeatDistribution.WaterTemperatureOutput
    assert part.outputs[outlet] == whole.outputs[outlet]
    assert part.outputs[HeatDistribution.ActualPartLoadRatio] == 0.4


@pytest.mark.base
def test_a_circuit_without_the_cooling_option_refuses_a_cooling_command() -> None:
    """Catches a circuit that cools on its controller's command although its configuration does not enable cooling.

    Only a controller whose configuration enables cooling sends ``State`` -1, and the circuit copies that option, so
    a cooling command it may not follow means the two disagree; the run fails instead of taking the room's heat into
    the buffer.
    """
    rig = HdsRig(PositionHotWaterStorageInSystemSetup.PARALLEL)
    with pytest.raises(ValueError, match="cooling_enabled is false"):
        rig.step(valve_state=-1, supply_in_celsius=16.0, demand_in_watt=-2000.0, room_in_celsius=26.0)


@pytest.mark.base
@pytest.mark.parametrize(
    ("valve_state", "supply_in_celsius", "demand_in_watt", "room_in_celsius", "heat_delivered_in_watt", "heat_drawn_in_watt"),
    [(1, 35.0, 3000.0, 20.0, 3000.0, 0.0), (-1, 16.0, -2000.0, 26.0, 0.0, 2000.0), (0, 35.0, 3000.0, 20.0, 0.0, 0.0)],
    ids=["heating", "cooling", "shut"],
)
def test_the_signed_heat_is_published_as_heating_and_cooling_each_at_least_zero(
    valve_state: float,
    supply_in_celsius: float,
    demand_in_watt: float,
    room_in_celsius: float,
    heat_delivered_in_watt: float,
    heat_drawn_in_watt: float,
) -> None:
    """Catches a split that books cooling as negative heating, or a direction in the wrong output.

    Behind a buffer the outputs are this step's. ``HeatDeliveredInWatt`` is the positive part of the signed
    ``ThermalPowerDelivered``, ``HeatDrawnForCoolingInWatt`` minus its negative part, so their difference is the
    signed heat in every step.
    """
    rig = HdsRig(PositionHotWaterStorageInSystemSetup.PARALLEL, cooling_enabled=True)
    step = rig.step(
        valve_state=valve_state,
        supply_in_celsius=supply_in_celsius,
        demand_in_watt=demand_in_watt,
        room_in_celsius=room_in_celsius,
    )
    assert step.outputs[HeatDistribution.HeatDeliveredInWatt] == pytest.approx(heat_delivered_in_watt)
    assert step.outputs[HeatDistribution.HeatDrawnForCoolingInWatt] == pytest.approx(heat_drawn_in_watt)
    assert step.outputs[HeatDistribution.HeatDeliveredInWatt] - step.outputs[
        HeatDistribution.HeatDrawnForCoolingInWatt
    ] == pytest.approx(step.outputs[HeatDistribution.ThermalPowerDelivered])


@pytest.mark.base
def test_the_two_directions_carry_the_ports_and_the_signed_heat_carries_none() -> None:
    """Catches a port left on the signed heat, or a direction booked with the wrong role or carrier.

    Behind a buffer the heating direction is space-heating heat the circuit produces, the cooling direction cooling it
    consumes, and the signed ``ThermalPowerDelivered`` stays the physics channel the building computes with, without
    a port: one port books one direction only.
    """
    hds = HdsRig(PositionHotWaterStorageInSystemSetup.PARALLEL).hds
    ports = {output.field_name: output.energy_port for output in hds.outputs}
    assert ports[HeatDistribution.ThermalPowerDelivered] is None
    assert ports[HeatDistribution.HeatDeliveredInWatt] == EnergyPort(
        lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
    )
    assert ports[HeatDistribution.HeatDrawnForCoolingInWatt] == EnergyPort(
        lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.COOLING
    )


@pytest.mark.base
@pytest.mark.parametrize("cooling_enabled", [False, True])
def test_the_distribution_loop_may_cool_exactly_where_the_configuration_enables_cooling(cooling_enabled: bool) -> None:
    """Catches a loop that declares it may cool although the circuit refuses every cooling command, or the reverse.

    The circuit's ``FluidLoop`` states ``may_cool`` from ``cooling_enabled``, so one configuration line decides both
    what the circuit does and what its loop declares.
    """
    hds = HdsRig(PositionHotWaterStorageInSystemSetup.PARALLEL, cooling_enabled=cooling_enabled).hds
    assert hds.distribution_loop.may_cool is cooling_enabled


@pytest.mark.base
@pytest.mark.parametrize("position", list(PositionHotWaterStorageInSystemSetup))
def test_the_distribution_loop_is_tagged_whole_in_every_position_and_pumps_where_the_position_pumps(
    position: PositionHotWaterStorageInSystemSetup,
) -> None:
    """Catches a loop the build cannot walk in a position, or a pumping flag that disagrees with the circuit's flow.

    The circuit tags its four channels in every position (stage 1b of ``roadmap/fluid_loops_spec.md``); it pumps where
    the position makes it pump its design flow, and a ``pumps_the_distribution_loop`` that says otherwise is refused
    when the system is built, naming both. The energy balance books the loop only behind a buffer (hisim-pnw7).
    """
    pumps = position in HeatDistribution.POSITIONS_PUMPING_THE_DESIGN_FLOW
    config = heat_distribution_system.HeatDistributionConfig(
        component_id=ComponentID(name="HeatDistributionSystem"),
        water_mass_flow_rate_in_kg_per_second=HdsRig.DESIGN_MASS_FLOW_IN_KG_PER_SECOND,
        position_hot_water_storage_in_system=position,
        absolute_conditioned_floor_area_in_m2=HdsRig.FLOOR_AREA_IN_M2,
        heating_system=heat_distribution_system.HeatDistributionSystemType.FLOORHEATING,
        cooling_enabled=False,
        pumps_the_distribution_loop=pumps,
    )
    hds = HeatDistribution(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900))
    port_sets = hds.port_sets()
    assert len(port_sets) == 1
    port_set = port_sets[0]
    assert port_set.loop.pumps is pumps
    assert {quantity: channel.field_name for quantity, channel in port_set.channels.items()} == {
        LoopQuantity.TEMPERATURE_IN: HeatDistribution.WaterTemperatureInput,
        LoopQuantity.FLOW_IN: HeatDistribution.WaterMassFlowInput,
        LoopQuantity.TEMPERATURE_OUT: HeatDistribution.WaterTemperatureOutput,
        LoopQuantity.FLOW_OUT: HeatDistribution.WaterMassFlowHDS,
    }
    assert hds.port_sets_in_the_energy_balance() == (
        (port_set,) if position is PositionHotWaterStorageInSystemSetup.PARALLEL else ()
    )
    config.pumps_the_distribution_loop = not pumps
    with pytest.raises(ValueError, match=f"pumps_the_distribution_loop is {str(not pumps).lower()}, but at position {position.value}"):
        HeatDistribution(config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, 900))
