"""The building reads the heat devices release into its heated zone and publishes an unheated cellar's temperature.

Bead hisim-device-location-820d.1. The building is a dynamic component: for every output of another component that
declares ``EnergyPort(PRODUCTION, ROOM_HEAT)`` it grows one input carrying ``EnergyPort(CONSUMPTION, ROOM_HEAT)``,
found by the port and not by the device's class, and the heat enters its internal gains beside the occupancy's. The
occupancy's two room-heat outputs are read by name already and are not read a second time. The cellar below the
heated zone is at T_in - b (T_in - T_out), b the floor's adjustment factor.

The tests drive the building through the synthetic day of the one-day snapshot (``tests/test_building_one_day_snapshot.py``)
beside a test double that releases a constant heat from a store of its own, and build the energy balance of the two
from the columns of that day.
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, Dict, List, Tuple

import numpy as np
import pandas as pd
import pytest
from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import simulator as sim
from hisim.caching import CacheSettings
from hisim.components.building import Building
from hisim.components.loadprofilegenerator_utsp_connector import UtspLpgConnector, UtspLpgConnectorConfig
from hisim.config import ComponentID, ConfigBase, DisplayConfig
from hisim.energy_port import EnergyPort
from hisim.energy_system.feed_resolution import DynamicConnectionResolver
from hisim.postprocessing.energy_balance import EnergyBalanceReport
from hisim.postprocessing.energy_balance.ports import Peer, PeerKind
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft
from tests.test_building_one_day_snapshot import OneDaySnapshot, SyntheticDayProfiles


@dataclass_json
@dataclass
class RoomHeaterConfig(ConfigBase):
    """The configuration of the test double :class:`RoomHeater`: the heat it releases, in W."""

    component_id: ComponentID
    heat_released_in_watt: float = 300.0

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the test double's class, as the ConfigBase contract requires."""
        return "tests.test_building_device_heat.RoomHeater"


class RoomHeater(cp.PhysicsComponent):
    """A test double of a device standing in the heated zone: it releases a constant heat drawn from a store of its own.

    Its released heat is ``EnergyPort.for_heat_released(HEATED_ZONE, ...)``, a ``PRODUCTION`` port of ``ROOM_HEAT``,
    and its store gives up the same energy (a ``STORED_CHANGE`` port), so its own balance closes in every step.
    """

    HeatReleasedInWatt = "HeatReleasedInWatt"
    StoredEnergyIncreaseInWattHour = "StoredEnergyIncreaseInWattHour"

    def __init__(self, my_simulation_parameters: SimulationParameters, config: RoomHeaterConfig) -> None:
        """Declare the released heat and the store's change."""
        self.config = config
        super().__init__(
            name=config.component_id.key,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=DisplayConfig(),
        )
        self.heat_released_channel = self.add_output(
            self.component_name,
            self.HeatReleasedInWatt,
            lt.LoadTypes.HEATING,
            lt.Units.WATT,
            output_description="The heat released into the heated zone, in W.",
            energy_port=EnergyPort.for_heat_released(
                lt.InstallationLocation.HEATED_ZONE, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT
            ),
        )
        self.stored_channel = self.add_output(
            self.component_name,
            self.StoredEnergyIncreaseInWattHour,
            lt.LoadTypes.HEATING,
            lt.Units.WATT_HOUR,
            output_description="The increase of the heat the store holds over the step, in Wh; negative.",
            energy_port=EnergyPort(lt.EnergyRole.STORED_CHANGE, lt.EnergyBalanceCarrier.ROOM_HEAT),
        )

    def i_prepare_simulation(self) -> None:
        """Nothing to prepare."""

    def i_save_state(self) -> None:
        """No state."""

    def i_restore_state(self) -> None:
        """No state."""

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Nothing to check."""

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Release the configured heat and take it from the store."""
        seconds_per_timestep = self.my_simulation_parameters.seconds_per_timestep
        stsv.set_output_value(self.heat_released_channel, self.config.heat_released_in_watt)
        stsv.set_output_value(self.stored_channel, -self.config.heat_released_in_watt * seconds_per_timestep / 3600.0)


class HeatedZoneDay:
    """Drives the snapshot's default building through the synthetic day, with or without a room heater beside it.

    The heating system's delivered heat is zero, since it arrives on the building's port inputs only when a
    distribution system feeds them; the CHP's heat, the sun and the occupancy's gains arrive as in the snapshot.
    """

    #: The synthetic inputs replaced: no heating system delivers heat to the building.
    NO_HEATING: ClassVar[Dict[str, List[float]]] = {
        "thermal_power_delivered_channel": [0.0] * SyntheticDayProfiles.TIMESTEPS,
    }

    def __init__(self, with_heater: bool, replaced_inputs: Dict[str, List[float]]) -> None:
        """Build the building, wire the synthetic inputs and, if asked, the heater by the building's default connection.

        Args:
            with_heater: Whether a room heater stands in the heated zone.
            replaced_inputs: Input vectors replacing the synthetic day's, by input-channel attribute name.
        """
        parameters = OneDaySnapshot.simulation_parameters()
        self.building = Building(
            config=OneDaySnapshot.config_for(OneDaySnapshot.DEFAULT_VARIANT_NAME), my_simulation_parameters=parameters
        )
        self.building.set_sim_repo(OneDaySnapshot.repository_with_synthetic_weather())
        self.source_outputs = OneDaySnapshot.wire_synthetic_inputs(self.building)
        for channel_name, source_output in self.source_outputs.items():
            # The balance finds an input port's column by the source the wiring recorded, as a run's connection does.
            channel: cp.ComponentInput = getattr(self.building, channel_name)
            channel.src_object_name, channel.src_field_name = source_output.component_name, source_output.field_name
        self.input_vectors = {**SyntheticDayProfiles.input_vectors(), **self.NO_HEATING, **replaced_inputs}
        self.heater = RoomHeater(parameters, RoomHeaterConfig(component_id=ComponentID("RoomHeater")))
        self.components: List[cp.Component] = [self.building]
        if with_heater:
            self.components.append(self.heater)
            self.building.connect_with_dynamic_connections_list(self.building.get_dynamic_default_connections(self.heater))
            for created_input in self.building.inputs:
                if created_input.src_object_name == self.heater.component_name:
                    created_input.source_output = self.heater.heat_released_channel
        fft.add_global_index_of_components([*self.source_outputs.values(), *self.components])
        self.all_outputs = sorted(
            [*self.source_outputs.values(), *(output for component in self.components for output in component.outputs)],
            key=lambda output: output.global_index,
        )

    def run(self) -> pd.DataFrame:
        """Simulate the day and return every output's value per step, one column per output in global-index order."""
        stsv = cp.SingleTimeStepValues(len(self.all_outputs))
        self.building.i_prepare_simulation()
        rows = []
        for timestep in range(SyntheticDayProfiles.TIMESTEPS):
            for channel_name, source_output in self.source_outputs.items():
                stsv.values[source_output.global_index] = self.input_vectors[channel_name][timestep]
            for component in self.components:
                component.i_save_state()
                component.i_restore_state()
            if self.heater in self.components:
                self.heater.i_simulate(timestep, stsv, False)
            self.building.i_simulate(timestep, stsv, False)
            rows.append(list(stsv.values))
        return pd.DataFrame(
            np.array(rows, dtype=float),
            index=pd.date_range("2021-01-01", periods=len(rows), freq=f"{SyntheticDayProfiles.SECONDS_PER_TIMESTEP}s"),
        )

    def column(self, table: pd.DataFrame, component_name: str, field_name: str) -> np.ndarray:
        """Return one output's values over the day."""
        (position,) = [
            index
            for index, output in enumerate(self.all_outputs)
            if output.component_name == component_name and output.field_name == field_name
        ]
        values: np.ndarray = table.iloc[:, position].to_numpy()
        return values

    def balance_report(self, table: pd.DataFrame) -> EnergyBalanceReport:
        """Return the energy balance of the day: the building, the heater and the synthetic source without ports."""
        synthetic_source = SimpleNamespace(
            component_name=OneDaySnapshot.SYNTHETIC_SOURCE_NAME, inputs=[], outputs=list(self.source_outputs.values())
        )
        wrapped = [SimpleNamespace(my_component=component) for component in [synthetic_source, *self.components]]
        return EnergyBalanceReport(table, self.all_outputs, wrapped, SyntheticDayProfiles.SECONDS_PER_TIMESTEP)


@pytest.fixture(name="redirected_solar_gain_cache")
def fixture_redirected_solar_gain_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send the building's solar-gain cache into a temporary directory, so the test writes nothing into the tree."""
    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path))


@pytest.mark.base
@pytest.mark.parametrize(
    ("indoor_in_celsius", "outside_in_celsius", "expected_in_celsius"),
    [(20.0, -7.0, 6.5), (20.0, 10.0, 15.0), (24.0, 25.0, 24.5)],
)
def test_the_cellar_lies_the_floor_s_factor_of_the_way_from_inside_to_outside(
    indoor_in_celsius: float, outside_in_celsius: float, expected_in_celsius: float
) -> None:
    """With b = 0.5 the cellar is halfway between the heated zone and the outside: 6.5, 15 and 24.5 °C.

    Catches: the factor applied from the outside instead of from the inside, and the sign of a summer difference.
    """
    assert Building.unheated_cellar_temperature_in_celsius(
        indoor_air_temperature_in_celsius=indoor_in_celsius,
        outside_temperature_in_celsius=outside_in_celsius,
        floor_adjustment_factor=0.5,
    ) == pytest.approx(expected_in_celsius)


@pytest.mark.base
@pytest.mark.parametrize(("factor", "expected_in_celsius"), [(0.0, 20.0), (1.0, -7.0)])
def test_a_factor_of_zero_or_one_puts_the_cellar_inside_or_outside(factor: float, expected_in_celsius: float) -> None:
    """A factor b of 0 is a cellar as warm as the heated zone, b = 1 one as cold as the outside; both are accepted.

    Catches: a range check that refuses its own edges.
    """
    assert Building.unheated_cellar_temperature_in_celsius(
        indoor_air_temperature_in_celsius=20.0, outside_temperature_in_celsius=-7.0, floor_adjustment_factor=factor
    ) == pytest.approx(expected_in_celsius)


@pytest.mark.base
@pytest.mark.parametrize("factor", [-0.1, 1.1, float("nan")])
def test_a_factor_outside_zero_to_one_is_refused(factor: float) -> None:
    """A factor outside [0, 1] would put the cellar beyond the indoor or the outside temperature, and is refused.

    Catches: a cellar warmer than the house or colder than the outside, computed without complaint.
    """
    with pytest.raises(ValueError, match="adjustment factor b"):
        Building.unheated_cellar_temperature_in_celsius(
            indoor_air_temperature_in_celsius=20.0, outside_temperature_in_celsius=-7.0, floor_adjustment_factor=factor
        )


@pytest.mark.base
def test_the_building_publishes_the_cellar_from_its_start_of_step_air_and_the_outside(redirected_solar_gain_cache) -> None:
    """Every step's cellar temperature follows from the start-of-step air temperature, the outside and the floor's b.

    Catches: a cellar computed from the air temperature of the pass (which moves while the step iterates) or from the
    set point.
    """
    del redirected_solar_gain_cache
    day = HeatedZoneDay(with_heater=False, replaced_inputs={})
    table = day.run()
    start_air = day.column(table, "Building", Building.TemperatureIndoorAirAtStartOfStepInCelsius)
    outside = np.array(day.input_vectors["temperature_outside_channel"])
    factor = day.building.floor_adjustment_factor
    assert factor == 0.5, "the default house's floor to its cellar has b = 0.5"
    np.testing.assert_allclose(
        day.column(table, "Building", Building.TemperatureUnheatedCellarInCelsius),
        start_air - factor * (start_air - outside),
        rtol=0.0,
        atol=1e-12,
    )


@pytest.mark.base
def test_the_building_reads_a_room_heat_port_by_default_connection_on_an_input_with_its_port(
    redirected_solar_gain_cache,
) -> None:
    """A room heater's released heat is read through one dynamic input carrying the building's half of the transfer.

    Catches: a building that reads devices by class, an input without its port (a one-sided booking), and a store's
    change of the same carrier read as heat.
    """
    del redirected_solar_gain_cache
    day = HeatedZoneDay(with_heater=True, replaced_inputs={})
    (connection,) = day.building.default_connections_for(day.heater)
    assert connection.source_component_field_name == RoomHeater.HeatReleasedInWatt
    dynamic_inputs = day.building.get_channel_inputs(Building.DEVICE_HEAT_CHANNEL)
    assert [(item.src_object_name, item.src_field_name) for item in dynamic_inputs] == [
        ("RoomHeater", RoomHeater.HeatReleasedInWatt)
    ]
    assert dynamic_inputs[0].energy_port == EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ROOM_HEAT)
    assert day.building.default_connections_for(day.building) == []


@pytest.mark.base
def test_a_file_s_bare_item_grows_the_same_input_with_the_same_port(redirected_solar_gain_cache) -> None:
    """A declarative file's bare item naming the heater grows one input of the device-heat channel carrying the port.

    The loader expands a bare item through ``default_connections_for`` and creates the input from the matched
    channel, so the file path and the imperative path give the building the same port. Catches: a channel without the
    port (a file-built building that books nothing) and a feed the channel refuses for its load type.
    """
    del redirected_solar_gain_cache
    day = HeatedZoneDay(with_heater=False, replaced_inputs={})
    resolver = DynamicConnectionResolver({"Building": day.building, "RoomHeater": day.heater})
    feeds = resolver.expand_default_item("Building", "RoomHeater")
    wires = resolver.resolve_all({"Building": feeds})
    assert len(wires) == 1
    wire = wires[0]
    assert (wire.source_name, wire.source_output) == ("RoomHeater", RoomHeater.HeatReleasedInWatt)
    (created,) = day.building.get_channel_inputs(Building.DEVICE_HEAT_CHANNEL)
    assert created.field_name == wire.target_input
    assert created.energy_port == EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ROOM_HEAT)
    assert not resolver.default_feeds_of(day.building, day.building)


@pytest.mark.base
def test_a_device_s_heat_enters_the_gains_like_the_occupancy_s(redirected_solar_gain_cache) -> None:
    """300 W from a room heater moves the building exactly as 300 W more from the occupancy's appliances would.

    Catches: device heat that bypasses the internal gains (no ISO 13790 split, no share lost through the windows) or
    is added to the delivered heat instead.
    """
    del redirected_solar_gain_cache
    heated = HeatedZoneDay(with_heater=True, replaced_inputs={})
    heated_table = heated.run()
    appliances = [value + 300.0 for value in SyntheticDayProfiles.input_vectors()["device_heat_gain_channel"]]
    reference = HeatedZoneDay(with_heater=False, replaced_inputs={"device_heat_gain_channel": appliances})
    reference_table = reference.run()
    np.testing.assert_array_equal(heated.column(heated_table, "Building", Building.HeatReleasedByDevicesInWatt), 300.0)
    np.testing.assert_array_equal(reference.column(reference_table, "Building", Building.HeatReleasedByDevicesInWatt), 0.0)
    for output in (
        Building.TemperatureIndoorAir,
        Building.TemperatureMeanThermalMass,
        Building.TemperatureInternalSurface,
        Building.TotalThermalPowerToResidence,
        Building.VentilationLossInWatt,
        Building.GainsLostThroughWindowsInWatt,
        Building.TheoreticalThermalBuildingDemand,
    ):
        np.testing.assert_allclose(
            heated.column(heated_table, "Building", output),
            reference.column(reference_table, "Building", output),
            rtol=1e-12,
            atol=1e-9,
            err_msg=output,
        )


@pytest.mark.base
def test_the_heater_and_the_building_book_the_same_column_and_both_close(redirected_solar_gain_cache) -> None:
    """The heater's released heat and the building's input of it are one column, so the link and both balances close.

    Catches: a building input without a port (the heater's transfer booked by one side only fails the run) and a
    building balance that leaves the device's heat out of its gains.
    """
    del redirected_solar_gain_cache
    day = HeatedZoneDay(with_heater=True, replaced_inputs={})
    report = day.balance_report(day.run())
    assert {balance.component: balance.closes for balance in report.balances} == {"Building": True, "RoomHeater": True}
    links = {(link.sender, link.receiver, link.carrier): link for link in report.links}
    room_heat = links[("RoomHeater", "Building", "room_heat")]
    expected_kilowatt_hours = 300.0 * SyntheticDayProfiles.SECONDS_PER_TIMESTEP / 3.6e6 * SyntheticDayProfiles.TIMESTEPS
    assert room_heat.closes and room_heat.sent == room_heat.received == pytest.approx(expected_kilowatt_hours)
    (building_side,) = [
        series for series in report.declared.by_component["Building"] if series.reads == ("RoomHeater", "HeatReleasedInWatt")
    ]
    assert building_side.peer == Peer(PeerKind.COMPONENT, "RoomHeater")
    (heater_side,) = [
        series for series in report.declared.by_component["RoomHeater"] if series.channel == RoomHeater.HeatReleasedInWatt
    ]
    assert heater_side.peer == Peer(PeerKind.COMPONENT, "Building")
    np.testing.assert_array_equal(building_side.kilowatt_hours, heater_side.kilowatt_hours)


@pytest.mark.base
def test_the_occupancy_s_room_heat_is_read_once_by_name_when_connected_automatically(tmp_path: Path) -> None:
    """Connected automatically, the building reads the occupancy's two room-heat outputs by name and nothing more.

    The occupancy declares ``PRODUCTION`` of ``ROOM_HEAT`` on both, like a device in the heated zone; the building
    grows a dynamic input for the room heater only. Catches: the occupants' heat counted twice, once on the named
    input and once on a dynamic one.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=900)
    parameters.result_directory = str(tmp_path)
    simulator = sim.Simulator(
        module_directory=str(tmp_path), module_filename="building_device_heat", my_simulation_parameters=parameters
    )
    simulator.set_simulation_parameters(parameters)
    occupancy = UtspLpgConnector(
        config=UtspLpgConnectorConfig.preset_couple_both_at_work("UTSPConnector"), my_simulation_parameters=parameters
    )
    heater = RoomHeater(parameters, RoomHeaterConfig(component_id=ComponentID("RoomHeater")))
    building = Building(
        config=dataclasses.replace(OneDaySnapshot.config_for(OneDaySnapshot.DEFAULT_VARIANT_NAME)),
        my_simulation_parameters=parameters,
    )
    simulator.add_component(occupancy)
    simulator.add_component(heater)
    simulator.add_component(building, connect_automatically=True)
    assert building.default_connections_for(occupancy) == []
    simulator.connect_everything_automatically([occupancy, heater, building], building)
    reads: List[Tuple[str, str, str]] = [
        (item.field_name, item.src_object_name or "", item.src_field_name or "")
        for item in building.inputs
        if item.src_object_name in ("UTSPConnector", "RoomHeater")
    ]
    assert sorted(source for _, *source in reads) == sorted(
        [
            ["RoomHeater", RoomHeater.HeatReleasedInWatt],
            ["UTSPConnector", UtspLpgConnector.HeatingByDevices],
            ["UTSPConnector", UtspLpgConnector.HeatingByResidents],
        ]
    )
    named = {field: source for field, *source in reads if field in (Building.HeatingByResidents, Building.HeatingByDevices)}
    assert named == {
        Building.HeatingByResidents: ["UTSPConnector", UtspLpgConnector.HeatingByResidents],
        Building.HeatingByDevices: ["UTSPConnector", UtspLpgConnector.HeatingByDevices],
    }
