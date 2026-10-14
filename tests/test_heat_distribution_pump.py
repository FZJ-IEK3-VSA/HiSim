"""The heat distribution system's own pump stands while its controller's valve is shut.

Where the heat distribution system pumps its design flow itself (a buffer in front of it, or a district-heating
substation without a pump), it used to go on pumping with the valve shut and returned its inlet cooled by a hundredth
of the difference to the room. The buffer then booked heat into a circuit that delivered none to the building. With
the valve shut the circuit now moves no water and returns its inlet unchanged.
"""

from dataclasses import dataclass
from typing import ClassVar, Dict

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import heat_distribution_system
from hisim.components.heat_distribution_system import HeatDistribution, PositionHotWaterStorageInSystemSetup
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@dataclass(frozen=True)
class HdsStep:

    """The inputs of one heat-distribution step and the outputs it published."""

    #: Every output of the step, by its field name.
    outputs: Dict[str, float]


class HdsRig:

    """A heat distribution system on fake inputs, simulated for single steps.

    The rig wires the four inputs a buffered system reads (the controller's valve state, the supply temperature, the
    building's theoretical demand and the indoor temperature) to fake outputs and runs ``i_simulate`` once per call.
    """

    #: The design flow of the rig's circuit, in kg/s.
    DESIGN_MASS_FLOW_IN_KG_PER_SECOND: ClassVar[float] = 0.27

    #: The floor area the rig's circuit heats, in m².
    FLOOR_AREA_IN_M2: ClassVar[float] = 121.2

    def __init__(self, position: PositionHotWaterStorageInSystemSetup, seconds_per_timestep: int = 900) -> None:
        """Build the heat distribution system and its fake sources.

        Args:
            position: Where the storage sits, which decides who pumps.
            seconds_per_timestep: The step length, in s.
        """
        config = heat_distribution_system.HeatDistributionConfig(
            component_id=ComponentID(name="HeatDistributionSystem"),
            water_mass_flow_rate_in_kg_per_second=self.DESIGN_MASS_FLOW_IN_KG_PER_SECOND,
            position_hot_water_storage_in_system=position,
            absolute_conditioned_floor_area_in_m2=self.FLOOR_AREA_IN_M2,
            heating_system=heat_distribution_system.HeatDistributionSystemType.FLOORHEATING,
        )
        self.hds = HeatDistribution(
            config=config, my_simulation_parameters=SimulationParameters.one_day_only(2021, seconds_per_timestep)
        )
        self.sources = {
            name: cp.ComponentOutput(f"Fake{name}", name, load_type, unit, component_id=ComponentID(f"Fake{name}"))
            for name, load_type, unit in (
                ("State", lt.LoadTypes.ANY, lt.Units.ANY),
                ("SupplyTemperature", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
                ("Demand", lt.LoadTypes.HEATING, lt.Units.WATT),
                ("Room", lt.LoadTypes.TEMPERATURE, lt.Units.CELSIUS),
            )
        }
        self.hds.state_channel.source_output = self.sources["State"]
        self.hds.water_temperature_input_channel.source_output = self.sources["SupplyTemperature"]
        self.hds.theoretical_thermal_building_demand_channel.source_output = self.sources["Demand"]
        self.hds.residence_temperature_input_channel.source_output = self.sources["Room"]
        everything = list(self.sources.values()) + [self.hds]
        self.stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs(everything))
        fft.add_global_index_of_components(everything)

    def step(
        self, *, valve_state: float, supply_in_celsius: float, demand_in_watt: float, room_in_celsius: float
    ) -> HdsStep:
        """Run one step from the saved state and save it, as the simulator does at the end of a converged step.

        Args:
            valve_state: The controller's valve state: 1 heating, -1 cooling, 0 shut.
            supply_in_celsius: The supply temperature the circuit receives, in °C.
            demand_in_watt: The building's theoretical demand, in W.
            room_in_celsius: The indoor air temperature, in °C.

        Returns:
            The published outputs.
        """
        values = {
            "State": valve_state,
            "SupplyTemperature": supply_in_celsius,
            "Demand": demand_in_watt,
            "Room": room_in_celsius,
        }
        for name, value in values.items():
            self.stsv.values[self.sources[name].global_index] = value
        self.hds.i_restore_state()
        self.hds.i_simulate(0, self.stsv, False)
        self.hds.i_save_state()
        return HdsStep(
            outputs={output.field_name: self.stsv.values[output.global_index] for output in self.hds.outputs}
        )


@pytest.mark.base
@pytest.mark.parametrize(
    "position",
    [PositionHotWaterStorageInSystemSetup.PARALLEL, PositionHotWaterStorageInSystemSetup.NO_STORAGE_MASS_FLOW_FIX],
)
def test_a_shut_valve_stops_the_pump_and_the_circuit_exchanges_nothing(
    position: PositionHotWaterStorageInSystemSetup,
) -> None:
    """With the valve shut no water moves and the circuit returns its inlet unchanged, at any demand and supply.

    Two steps run with the valve shut, so the second publishes the first's values too: the circuit's outputs lag one
    step, which is unchanged here.
    """
    rig = HdsRig(position)
    for _ in range(2):
        step = rig.step(valve_state=0, supply_in_celsius=35.0, demand_in_watt=0.0, room_in_celsius=20.0)
    assert step.outputs[HeatDistribution.WaterMassFlowHDS] == 0.0
    assert step.outputs[HeatDistribution.ThermalPowerDelivered] == 0.0
    assert step.outputs[HeatDistribution.WaterTemperatureOutput] == 35.0
    assert step.outputs[HeatDistribution.WaterTemperatureInlet] == 35.0


@pytest.mark.base
def test_an_open_valve_pumps_the_design_flow() -> None:
    """With the valve open for heating the circuit pumps its design flow and, a step later, delivers the demand."""
    rig = HdsRig(PositionHotWaterStorageInSystemSetup.PARALLEL)
    rig.step(valve_state=1, supply_in_celsius=35.0, demand_in_watt=3000.0, room_in_celsius=20.0)
    step = rig.step(valve_state=1, supply_in_celsius=35.0, demand_in_watt=3000.0, room_in_celsius=20.0)
    assert step.outputs[HeatDistribution.WaterMassFlowHDS] == HdsRig.DESIGN_MASS_FLOW_IN_KG_PER_SECOND
    assert step.outputs[HeatDistribution.ThermalPowerDelivered] == pytest.approx(3000.0)


@pytest.mark.base
@pytest.mark.parametrize(("valve_state", "stops"), [(0, True), (1, False), (-1, False)])
def test_only_a_shut_valve_stops_the_pump(valve_state: float, stops: bool) -> None:
    """The pump stands for the valve state 0 only."""
    assert HeatDistribution.pump_stopped_by_the_valve(valve_state) is stops
