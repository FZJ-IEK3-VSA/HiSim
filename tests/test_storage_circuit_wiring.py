"""The storages refuse a charging circuit wired by half, and the hot-water generators read no flow of the tank.

A storage books each charging circuit from two inputs, the circuit's mass flow (kg/s) and its supply temperature
(°C); an input that is not wired reads 0. With the temperature wired and the flow not, the generator books heat the
storage never receives. These tests check, through the ``ComponentWrapper`` the ``Simulator`` uses (declare the
wires, prepare, resolve the wires):

* the space-heating buffer refuses a build in which its generator's or its heat distribution system's temperature is
  wired and the matching flow is not, since both flows are mandatory wherever their temperature is;
* the buffer and the hot-water tank refuse a secondary circuit with only its flow or only its temperature wired, and
  accept it with both or with neither;
* the district-heating substation and the electric heater no longer declare the tank-flow input they never read, and
  their default connections from the tank bring only the tank's return temperature.
"""

import dataclasses
from typing import List, Sequence, Union

import pytest

from hisim import component as cp
from hisim.component_wrapper import ComponentWrapper
from hisim.components.generic_district_heating import DistrictHeating, DistrictHeatingConfig
from hisim.components.generic_electric_heating import ElectricHeating, ElectricHeatingConfig
from hisim.components.simple_water_storage import (
    CoilWiredByHalfError,
    SimpleDHWStorage,
    SimpleDHWStorageConfig,
    SimpleHotWaterStorage,
    SimpleHotWaterStorageConfig,
)
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests.part_load_rigs import AllInputsRig


class StorageWiring:
    """Builds a buffer or a tank, wires the inputs a test names to a fake source and runs the simulator's build order."""

    #: The name of the fake component every wired input reads from.
    SOURCE_NAME: str = "FakeSource"

    #: The tank-flow input the district-heating substation and the electric heater declared and never read.
    REMOVED_TANK_FLOW_INPUT: str = "WaterInputMassFlowRateFromWarmWaterStorage"

    @staticmethod
    def parameters() -> SimulationParameters:
        """Return one day of 2021 at 60 s steps, a resolution every storage accepts.

        Returns:
            The simulation parameters.
        """
        return SimulationParameters.one_day_only(2021, 60)

    @classmethod
    def buffer(cls) -> SimpleHotWaterStorage:
        """Return a 200 l space-heating buffer parallel to its heat source, with nothing wired.

        Returns:
            The buffer.
        """
        config = dataclasses.replace(SimpleHotWaterStorageConfig.preset_buffer("Buffer"), volume_heating_water_storage_in_liter=200.0)
        # The constructor is wrapped by a timing decorator that mypy reads as returning Any.
        buffer: SimpleHotWaterStorage = SimpleHotWaterStorage(cls.parameters(), config)
        return buffer

    @classmethod
    def tank(cls) -> SimpleDHWStorage:
        """Return a 250 l hot-water tank with nothing wired.

        Returns:
            The tank.
        """
        config = SimpleDHWStorageConfig(component_id=ComponentID(name="DHWStorage"), volume_heating_water_storage_in_liter=250.0)
        tank: SimpleDHWStorage = SimpleDHWStorage(my_simulation_parameters=cls.parameters(), config=config)
        return tank

    @classmethod
    def build(cls, component: cp.Component, wired_field_names: Sequence[str]) -> None:
        """Wire the named inputs to a fake source, then prepare the component and resolve its inputs.

        Each named input is declared with ``connect_input``, as a system setup or the energy-system loader declares
        it, and the fake source publishes one output per named input with the input's own load type and unit. The
        component is then prepared and its inputs resolved in the order ``Simulator.run_all_timesteps`` uses, so a
        refusal of either step reaches the test as it would reach a run.

        Args:
            component: The storage under test, not yet wired.
            wired_field_names: The field names of the inputs to wire.

        Raises:
            CoilWiredByHalfError: If the component refuses a secondary circuit wired by half while it is prepared.
            ValueError: If a mandatory input is left unwired when the inputs are resolved.
        """
        inputs_by_name = {component_input.field_name: component_input for component_input in component.inputs}
        outputs: List[cp.ComponentOutput] = []
        for field_name in wired_field_names:
            component_input = inputs_by_name[field_name]
            outputs.append(
                cp.ComponentOutput(
                    cls.SOURCE_NAME, field_name, component_input.loadtype, component_input.unit, component_id=ComponentID(cls.SOURCE_NAME)
                )
            )
            component.connect_input(field_name, cls.SOURCE_NAME, field_name)
        wrapper = ComponentWrapper(component, is_cachable=False, connect_automatically=False)
        wrapper.prepare_calculation()
        wrapper.connect_inputs(outputs)

    @staticmethod
    def buffer_primary_inputs() -> List[str]:
        """Return the buffer's inputs a complete build wires: both primary circuits and the controller's state.

        Returns:
            The field names.
        """
        return [
            SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution,
            SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem,
            SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator,
            SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator,
        ]

    @staticmethod
    def tank_primary_inputs() -> List[str]:
        """Return the tank's inputs a complete build wires: the tap demand and the primary coil.

        Returns:
            The field names.
        """
        return [
            SimpleDHWStorage.WaterConsumption,
            SimpleDHWStorage.WaterTemperatureFromHeatGenerator,
            SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator,
        ]


@pytest.mark.base
@pytest.mark.parametrize(
    ["temperature_input", "unwired_flow_input"],
    [
        (SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator, SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator),
        (SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution, SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem),
    ],
)
def test_the_buffer_refuses_a_temperature_wired_without_its_flow(temperature_input: str, unwired_flow_input: str) -> None:
    """A buffer with a circuit's supply temperature wired and its flow not is refused before the first step.

    Fails if the generator's or the heat distribution system's mass flow is optional again: the unwired flow would
    then read 0 kg/s, and the generator would book heat that the buffer never receives.
    """
    buffer = StorageWiring.buffer()
    wired = [name for name in StorageWiring.buffer_primary_inputs() if name != unwired_flow_input]
    assert temperature_input in wired
    with pytest.raises(ValueError, match=f"{unwired_flow_input} .*is not connected"):
        StorageWiring.build(buffer, wired)


@pytest.mark.base
def test_the_buffer_flows_are_mandatory_for_the_energy_system_loader() -> None:
    """Both of the buffer's primary flow inputs are mandatory, which the energy-system loader's wiring check reads.

    Fails if either flow is optional, so that an energy-system file listing a temperature without its flow would load.
    """
    buffer = StorageWiring.buffer()
    assert buffer.water_mass_flow_rate_heat_generator_input_channel.is_mandatory
    assert buffer.water_mass_flow_rate_heat_distribution_system_input_channel.is_mandatory


@pytest.mark.base
def test_a_buffer_wired_completely_builds() -> None:
    """A buffer with both primary circuits wired and no secondary generator builds.

    Fails if the build refuses a buffer that has no secondary generator at all.
    """
    StorageWiring.build(StorageWiring.buffer(), StorageWiring.buffer_primary_inputs())


@pytest.mark.base
@pytest.mark.parametrize("storage_kind", ["buffer", "tank"])
@pytest.mark.parametrize(
    ["wired_secondary_input", "unwired_secondary_input"],
    [
        ("WaterTemperatureFromSecondaryHeatGenerator", "WaterMassFlowRateFromSecondaryHeatGenerator"),
        ("WaterMassFlowRateFromSecondaryHeatGenerator", "WaterTemperatureFromSecondaryHeatGenerator"),
    ],
)
def test_a_secondary_circuit_wired_by_half_is_refused(storage_kind: str, wired_secondary_input: str, unwired_secondary_input: str) -> None:
    """A buffer or a tank with only one of its secondary circuit's two inputs wired is refused when it is prepared.

    The message names the storage, both inputs and what to wire. Fails if a secondary coil with only its supply
    temperature wired could run, booking the generator's heat into no storage, or if only its flow wired could run,
    mixing in water at 0 °C.
    """
    if storage_kind == "buffer":
        storage: cp.Component = StorageWiring.buffer()
        primary_inputs = StorageWiring.buffer_primary_inputs()
    else:
        storage = StorageWiring.tank()
        primary_inputs = StorageWiring.tank_primary_inputs()
    with pytest.raises(CoilWiredByHalfError) as refusal:
        StorageWiring.build(storage, [*primary_inputs, wired_secondary_input])
    message = str(refusal.value)
    assert f"'{storage.component_name}'" in message
    assert f"'{wired_secondary_input}' wired to '{StorageWiring.SOURCE_NAME}.{wired_secondary_input}'" in message
    assert f"wire '{unwired_secondary_input}' to the same generator's matching output, or wire neither" in message


@pytest.mark.base
@pytest.mark.parametrize("storage_kind", ["buffer", "tank"])
@pytest.mark.parametrize("secondary_is_wired", [True, False])
def test_a_secondary_circuit_wired_whole_or_not_at_all_builds(storage_kind: str, secondary_is_wired: bool) -> None:
    """A buffer or a tank whose secondary circuit has both inputs wired, or neither, builds.

    Fails if the both-or-neither check refused a complete secondary circuit, such as a tank's solar coil, or a storage
    without a secondary generator.
    """
    secondary_inputs = ["WaterTemperatureFromSecondaryHeatGenerator", "WaterMassFlowRateFromSecondaryHeatGenerator"]
    if storage_kind == "buffer":
        storage: cp.Component = StorageWiring.buffer()
        primary_inputs = StorageWiring.buffer_primary_inputs()
    else:
        storage = StorageWiring.tank()
        primary_inputs = StorageWiring.tank_primary_inputs()
    StorageWiring.build(storage, [*primary_inputs, *(secondary_inputs if secondary_is_wired else [])])


@pytest.mark.base
@pytest.mark.parametrize("generator_kind", ["district_heating", "electric_heating"])
def test_the_hot_water_generators_read_no_flow_of_the_tank(generator_kind: str) -> None:
    """The district-heating substation and the electric heater read their own flow back from the tank, not its tap flow.

    Both pump their hot-water circuit themselves and book its heat from their own flow; the tank's tap flow they used
    to declare as a mandatory input was never read. Their hot-water loop's fourth wire is their own flow, passed back by
    the tank's primary coil (stage 1b of ``roadmap/fluid_loops_spec.md``). Fails if the tap-flow input or its default
    connection comes back, or if the flow back is read from anything but the coil.
    """
    generator: Union[DistrictHeating, ElectricHeating]
    if generator_kind == "district_heating":
        district_heating_config = DistrictHeatingConfig.preset_standard("DistrictHeating")
        district_heating_config.with_domestic_hot_water_preparation = True
        district_heating_config.connected_load_in_w = 20000.0
        generator = DistrictHeating(StorageWiring.parameters(), district_heating_config)
    else:
        electric_heating_config = ElectricHeatingConfig.preset_resistive("ElectricHeating")
        electric_heating_config.with_domestic_hot_water_preparation = True
        electric_heating_config.maximum_electric_power_w = 40000.0
        generator = ElectricHeating(my_simulation_parameters=StorageWiring.parameters(), config=electric_heating_config)
    assert StorageWiring.REMOVED_TANK_FLOW_INPUT not in [component_input.field_name for component_input in generator.inputs]
    assert not hasattr(type(generator), StorageWiring.REMOVED_TANK_FLOW_INPUT)
    connections = generator.get_default_connections_from_simple_dhw_storage()
    assert [(connection.target_input_name, connection.source_output_name) for connection in connections] == [
        ("WaterInputTemperatureDhw", SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius),
        ("WaterInputMassFlowRateReturningDhw", SimpleDHWStorage.WaterMassFlowRateToHeatGenerator),
    ]


@pytest.mark.base
@pytest.mark.parametrize("storage_kind", ["buffer", "tank"])
def test_the_storages_pass_every_loop_s_flow_back_unchanged(storage_kind: str) -> None:
    """Catches a storage that publishes a flow of its own on a loop it does not pump, or none.

    A storage moves no water: on each of its loops it publishes the flow it reads as the flow leaving, which the pump
    owner reads back (the fourth wire of stage 1b of ``roadmap/fluid_loops_spec.md``). Every loop is tagged whole and
    none is pumped by the storage.
    """
    storage: Union[SimpleHotWaterStorage, SimpleDHWStorage]
    if storage_kind == "buffer":
        storage = StorageWiring.buffer()
        flows = {
            (SimpleHotWaterStorage.WaterMassFlowRateFromHeatGenerator, SimpleHotWaterStorage.WaterMassFlowRateToHeatGenerator): 0.36,
            (
                SimpleHotWaterStorage.WaterMassFlowRateFromSecondaryHeatGenerator,
                SimpleHotWaterStorage.WaterMassFlowRateToSecondaryHeatGenerator,
            ): 0.1,
            (
                SimpleHotWaterStorage.WaterMassFlowRateFromHeatDistributionSystem,
                SimpleHotWaterStorage.WaterMassFlowRateToHeatDistributionSystem,
            ): 0.27,
        }
        temperatures = {
            SimpleHotWaterStorage.WaterTemperatureFromHeatGenerator: 55.0,
            SimpleHotWaterStorage.WaterTemperatureFromSecondaryHeatGenerator: 50.0,
            SimpleHotWaterStorage.WaterTemperatureFromHeatDistribution: 35.0,
        }
    else:
        storage = StorageWiring.tank()
        flows = {
            (SimpleDHWStorage.WaterMassFlowRateFromHeatGenerator, SimpleDHWStorage.WaterMassFlowRateToHeatGenerator): 0.12,
            (
                SimpleDHWStorage.WaterMassFlowRateFromSecondaryHeatGenerator,
                SimpleDHWStorage.WaterMassFlowRateToSecondaryHeatGenerator,
            ): 0.006,
        }
        temperatures = {
            SimpleDHWStorage.WaterTemperatureFromHeatGenerator: 65.0,
            SimpleDHWStorage.WaterTemperatureFromSecondaryHeatGenerator: 60.0,
        }
    assert all(len(port_set.channels) == 4 and not port_set.loop.pumps for port_set in storage.port_sets())
    outputs = AllInputsRig.step_once(storage, {**{arriving: flow for (arriving, _), flow in flows.items()}, **temperatures})
    assert {leaving: outputs[leaving] for (_, leaving) in flows} == {leaving: flow for (_, leaving), flow in flows.items()}
