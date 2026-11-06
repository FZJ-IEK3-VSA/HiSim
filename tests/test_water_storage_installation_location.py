"""The buffer and the hot-water tank lose their standby heat to the place they stand in, and hand it where that place says.

Bead hisim-device-location-820d.2. Each tank's ``installation_location`` is a sized field that follows the building's
technical room (the fact ``technical_room_location``) and that a file may pin. The location decides two things:

* the temperature the standby loss ``UA (T_tank - T_surrounding)`` is computed against, read on the input
  ``SurroundingTemperatureInCelsius`` whose default connection the location names: the building's air temperature at
  the start of the step in the heated zone, the building's unheated cellar in an unheated space, the weather's outside
  temperature outdoors;
* the port of the standby loss: room heat the building takes in as an internal gain in the heated zone
  (``PRODUCTION`` of ``ROOM_HEAT``), a ``LOSS`` of the tank's own carrier that leaves the house elsewhere.

The first tests build a tank alone and step it on fake inputs; the last ones run the heat-pump and the gas
building-sizer twins for three January days at 900 s with the tanks pinned to each location, and read the run's
results and its energy balance report.
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple, Union

import pandas as pd
import pytest
import yaml

from hisim import loadtypes as lt
from hisim.components.building.building import Building
from hisim.components.simple_water_storage import (
    SimpleDHWStorage,
    SimpleDHWStorageConfig,
    SimpleHotWaterStorage,
    SimpleHotWaterStorageConfig,
    SimpleWaterStorageState,
)
from hisim.components.weather import Weather
from hisim.config import ComponentID, ConfigSizingError, SizingContext
from hisim.energy_port import EnergyPort
from tests.part_load_rigs import AllInputsRig, Parameters

#: Either tank class.
TankClass = Union[type[SimpleHotWaterStorage], type[SimpleDHWStorage]]


class Tanks:
    """Builds a buffer or a hot-water tank standing in a given place, sized and stepped without a system around it."""

    #: The buffer's volume, l.
    BUFFER_VOLUME_IN_LITER: ClassVar[float] = 500.0
    #: The hot-water tank's volume, l.
    TANK_VOLUME_IN_LITER: ClassVar[float] = 250.0
    #: The carrier each tank stores, which its standby loss is a loss of outside the heated zone.
    CARRIER: ClassVar[Dict[str, lt.EnergyBalanceCarrier]] = {
        SimpleHotWaterStorage.__name__: lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT,
        SimpleDHWStorage.__name__: lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
    }

    @classmethod
    def build(
        cls, tank_class: TankClass, location: lt.InstallationLocation, seconds_per_timestep: int = 900
    ) -> Union[SimpleHotWaterStorage, SimpleDHWStorage]:
        """Return a buffer of 500 l or a hot-water tank of 250 l standing at ``location``, for one day.

        Args:
            tank_class: Which tank.
            location: Where it stands.
            seconds_per_timestep: The step length, s.

        Returns:
            The tank, not wired.
        """
        parameters = Parameters.one_day(seconds_per_timestep)
        if tank_class is SimpleHotWaterStorage:
            buffer_config = SimpleHotWaterStorageConfig.preset_buffer("Buffer")
            buffer_config.volume_heating_water_storage_in_liter = cls.BUFFER_VOLUME_IN_LITER
            buffer_config.installation_location = location
            buffer: SimpleHotWaterStorage = SimpleHotWaterStorage(parameters, buffer_config)
            return buffer
        tank_config = SimpleDHWStorageConfig(
            component_id=ComponentID(name="DHWStorage"),
            volume_heating_water_storage_in_liter=cls.TANK_VOLUME_IN_LITER,
            installation_location=location,
        )
        tank: SimpleDHWStorage = SimpleDHWStorage(my_simulation_parameters=parameters, config=tank_config)
        return tank


@pytest.mark.base
@pytest.mark.parametrize("tank_class", [SimpleHotWaterStorage, SimpleDHWStorage])
@pytest.mark.parametrize(
    "location, source_class, source_output",
    [
        (lt.InstallationLocation.HEATED_ZONE, Building, Building.TemperatureIndoorAirAtStartOfStepInCelsius),
        (lt.InstallationLocation.UNHEATED_SPACE, Building, Building.TemperatureUnheatedCellarInCelsius),
        (lt.InstallationLocation.OUTDOORS, Weather, Weather.TemperatureOutside),
    ],
)
def test_each_location_wires_the_surrounding_temperature_to_the_place_the_tank_stands_in(
    tank_class: TankClass, location: lt.InstallationLocation, source_class: type, source_output: str
) -> None:
    """A tank whose default connection read another place would compute its loss against the wrong temperature.

    The air temperature in the heated zone is the one at the start of the step, which the tank's own heat does not
    move while the step iterates.
    """
    tank = Tanks.build(tank_class, location)
    surrounding = [
        connection
        for connections in tank.default_connections.values()
        for connection in connections
        if connection.target_input_name == tank_class.SurroundingTemperatureInCelsius
    ]
    assert [(connection.source_class_name, connection.source_output_name) for connection in surrounding] == [
        (source_class.__name__, source_output)
    ]
    assert tank.surrounding_temperature_channel.field_name == tank_class.SurroundingTemperatureInCelsius
    assert tank.surrounding_temperature_channel.is_mandatory
    assert tank.surrounding_temperature_channel.unit == lt.Units.CELSIUS


@pytest.mark.base
@pytest.mark.parametrize("tank_class", [SimpleHotWaterStorage, SimpleDHWStorage])
def test_the_standby_loss_is_room_heat_in_the_heated_zone_and_a_loss_of_the_tank_s_carrier_elsewhere(
    tank_class: TankClass,
) -> None:
    """A loss port that ignored the location would send a heated room's tank heat out of the house, or the reverse."""
    carrier = Tanks.CARRIER[tank_class.__name__]
    expected = {
        lt.InstallationLocation.HEATED_ZONE: EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
        lt.InstallationLocation.UNHEATED_SPACE: EnergyPort(lt.EnergyRole.LOSS, carrier),
        lt.InstallationLocation.OUTDOORS: EnergyPort(lt.EnergyRole.LOSS, carrier),
    }
    for location, port in expected.items():
        tank = Tanks.build(tank_class, location)
        assert tank.stand_by_heat_loss_channel.energy_port == port, location
        description = tank.stand_by_heat_loss_channel.output_description
        assert description is not None
        assert tank_class.STANDBY_LOSS_DESTINATION_BY_LOCATION[location] in description
        assert tank_class.SurroundingTemperatureInCelsius in description


@pytest.mark.base
def test_every_location_names_a_source_and_a_destination() -> None:
    """A location without an entry would fail only when a tank first stood there, not when the code changed."""
    for table in (
        SimpleHotWaterStorage.SURROUNDING_TEMPERATURE_SOURCE_BY_LOCATION,
        SimpleHotWaterStorage.STANDBY_LOSS_DESTINATION_BY_LOCATION,
    ):
        assert set(table) == set(lt.InstallationLocation)


@pytest.mark.base
def test_a_buffer_in_an_unheated_space_at_50_degrees_loses_ua_times_43_5_kelvin_to_a_6_5_degree_cellar() -> None:
    """A buffer that still lost against the former fixed 20 °C would lose UA · 30 W here instead of UA · 43.5 W.

    At 900 s the buffer is a start-of-step node: its loss over the step is ``UA (T0 - T_surrounding)`` with ``T0`` its
    start temperature, so 50 °C against the 6.5 °C of a cellar at -7 °C outside (b = 0.5, 20 °C inside) is
    ``UA · 43.5 K``.
    """
    buffer = Tanks.build(SimpleHotWaterStorage, lt.InstallationLocation.UNHEATED_SPACE)
    assert isinstance(buffer, SimpleHotWaterStorage)
    buffer.state = SimpleWaterStorageState(mean_water_temperature_in_celsius=50.0)
    outputs = AllInputsRig.step_once(buffer, {SimpleHotWaterStorage.SurroundingTemperatureInCelsius: 6.5})
    ua_in_watt_per_kelvin = buffer.heat_loss_coefficient_in_watt_per_kelvin
    assert outputs[SimpleHotWaterStorage.StandbyHeatLoss] == pytest.approx(ua_in_watt_per_kelvin * 43.5, rel=1e-12)


@pytest.mark.base
@pytest.mark.parametrize("surrounding_temperature_in_celsius", [6.5, 50.0, 70.0])
def test_the_hot_water_tank_loses_against_the_surrounding_temperature_it_reads(
    surrounding_temperature_in_celsius: float,
) -> None:
    """A tank that ignored its surroundings would lose the same heat in a cold cellar as in a warm room.

    Without draw and charge the tank is a mixed node relaxing towards its surroundings: its loss is
    ``UA (T̄ - T_surrounding)`` with ``T̄`` its step mean, positive in colder surroundings, zero in equal ones and
    negative in warmer ones.
    """
    tank = Tanks.build(SimpleDHWStorage, lt.InstallationLocation.UNHEATED_SPACE)
    assert isinstance(tank, SimpleDHWStorage)
    outputs = AllInputsRig.step_once(
        tank, {SimpleDHWStorage.SurroundingTemperatureInCelsius: surrounding_temperature_in_celsius}
    )
    step_mean_in_celsius = outputs[SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius]
    expected_in_watt = tank.tank.loss_coefficient_in_watt_per_kelvin * (
        step_mean_in_celsius - surrounding_temperature_in_celsius
    )
    assert outputs[SimpleDHWStorage.StandbyHeatLoss] == pytest.approx(expected_in_watt, rel=1e-9, abs=1e-12)
    start_in_celsius = SimpleDHWStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    assert (outputs[SimpleDHWStorage.StandbyHeatLoss] > 0.0) == (surrounding_temperature_in_celsius < start_in_celsius)
    if surrounding_temperature_in_celsius == start_in_celsius:
        assert outputs[SimpleDHWStorage.StandbyHeatLoss] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.base
@pytest.mark.parametrize("preset", [SimpleHotWaterStorageConfig.preset_buffer, SimpleDHWStorageConfig.preset_standard])
def test_an_auto_location_without_a_building_fails_naming_the_fact(preset: Any) -> None:
    """A tank sized in a system without a building would otherwise stand nowhere and lose its heat against nothing."""
    config = preset("Tank")
    config.volume_heating_water_storage_in_liter = 300.0
    with pytest.raises(ConfigSizingError, match="technical_room_location"):
        config.resolve(SizingContext())


@pytest.mark.base
@pytest.mark.parametrize("preset", [SimpleHotWaterStorageConfig.preset_buffer, SimpleDHWStorageConfig.preset_standard])
def test_an_auto_location_follows_the_technical_room_and_a_pinned_one_stays(preset: Any) -> None:
    """A tank that ignored the building, or overrode a file's pin, would stand where nobody put it."""
    config = preset("Tank")
    config.volume_heating_water_storage_in_liter = 300.0
    heated_zone = SizingContext(technical_room_location=lt.InstallationLocation.HEATED_ZONE)
    assert config.resolve(heated_zone).installation_location is lt.InstallationLocation.HEATED_ZONE
    config.installation_location = lt.InstallationLocation.OUTDOORS
    assert config.resolve(heated_zone).installation_location is lt.InstallationLocation.OUTDOORS


class PinnedTwin:
    """A building-sizer twin run for three January days at 900 s with both tanks pinned to one location, or left AUTO."""

    #: The period: three January days, cold enough for the cellar to sit well below the house.
    START: ClassVar[str] = "2021-01-10"
    END: ClassVar[str] = "2021-01-13"
    #: The tanks of every building-sizer twin.
    TANKS: ClassVar[Tuple[str, str]] = ("SimpleHotWaterStorage", "DHWStorage")
    #: The source class each location reads its surrounding temperature from, as an input item names it.
    SOURCE: ClassVar[Dict[lt.InstallationLocation, str]] = {
        lt.InstallationLocation.HEATED_ZONE: "Building",
        lt.InstallationLocation.UNHEATED_SPACE: "Building",
        lt.InstallationLocation.OUTDOORS: "Weather",
    }

    @classmethod
    def run(cls, directory: Path, twin: str, location: Optional[lt.InstallationLocation]) -> Tuple[Any, Path]:
        """Run the twin with both tanks at ``location`` (``None``: as recorded) and return the built system and results.

        Outdoors, each tank's bare item ``Building`` becomes ``Weather``, whose default connection the location names.

        Args:
            directory: Where the files and results go.
            twin: The twin's stem.
            location: Where both tanks stand, or ``None`` to leave the recorded ``AUTO``.

        Returns:
            The built system after the run, and the result directory.
        """
        from hisim.energy_system.executor import run_energy_system  # pylint: disable=import-outside-toplevel

        root = Path(__file__).resolve().parent.parent
        text = (root / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        document = yaml.safe_load(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"))
        if location is not None:
            for tank in cls.TANKS:
                entry = document["components"][tank]
                entry.setdefault("config", {})["installation_location"] = location.name
                entry["inputs"] = [cls.SOURCE[location] if item == "Building" else item for item in entry["inputs"]]
        energy_system = directory / "pinned.energy_system.yaml"
        energy_system.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        parameters = directory / "period.simulation.yaml"
        parameters.write_text(
            f"start_date: '{cls.START}T00:00:00'\n"
            f"end_date: '{cls.END}T00:00:00'\n"
            "seconds_per_timestep: 900\n"
            "country: DE\n"
            "logging_level: 3\n"
            "post_processing_options: [EXPORT_ENERGY_BALANCE]\n",
            encoding="utf-8",
        )
        results = directory / "results"
        built = run_energy_system(energy_system, parameters, result_directory=str(results))
        return built, results

    @staticmethod
    def column(frame: pd.DataFrame, component: str, output: str) -> pd.Series:
        """Return one output's column of the run's results, found by its component and field name."""
        (name,) = [name for name in frame.columns if str(name).startswith(f"{component} - {output} [")]
        series: pd.Series = frame[name]
        return series

    @staticmethod
    def component(built: Any, name: str) -> Any:
        """Return the component of the run named ``name``."""
        (found,) = [wrapper.my_component for wrapper in built.simulator.wrapped_components if wrapper.my_component.component_name == name]
        return found

    @staticmethod
    def balance_entries(results: Path) -> Mapping[str, Dict[str, Any]]:
        """Return the run's balance report entries by component, after asserting that the whole report closes."""
        report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
        assert report["verdict"] == "closes"
        return {entry["component"]: entry for entry in report["components"]}


@pytest.fixture(name="pinned_run", scope="module", params=[*lt.InstallationLocation, None], ids=lambda value: getattr(value, "name", "AUTO"))
def fixture_pinned_run(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Tuple[Optional[lt.InstallationLocation], Any, Path]:
    """The heat-pump twin with both tanks at each location and as recorded, run once per location for this module."""
    location: Optional[lt.InstallationLocation] = request.param
    built, results = PinnedTwin.run(
        tmp_path_factory.mktemp(getattr(location, "name", "AUTO")), "household_heatpump_building_sizer", location
    )
    return location, built, results


@pytest.mark.extendedbase
def test_both_tanks_close_their_balance_in_every_step_at_every_location(
    pinned_run: Tuple[Optional[lt.InstallationLocation], Any, Path],
) -> None:
    """A tank whose loss port did not match its booked loss would fail the balance in the location that differs.

    The recorded twin stands on a TABULA row with an unheated cellar, so its tanks stand in an unheated space.
    """
    location, _, results = pinned_run
    effective = location or lt.InstallationLocation.UNHEATED_SPACE
    entries = PinnedTwin.balance_entries(results)
    for tank in PinnedTwin.TANKS:
        entry = entries[tank]
        assert entry["verdict"] == "closes" and entry["failing_steps"] == 0, entry
        ports = {port["output"]: port for port in entry["ports"]}
        loss = ports[SimpleHotWaterStorage.StandbyHeatLoss]
        if effective is lt.InstallationLocation.HEATED_ZONE:
            assert (loss["role"], loss["carrier"]) == ("production", "room_heat")
            assert (loss["peer_kind"], loss["peer"]) == ("component", "Building")
        else:
            assert loss["role"] == "loss"
            assert loss["peer_kind"] == "boundary"
    assert entries["Building"]["verdict"] == "closes" and entries["Building"]["failing_steps"] == 0


@pytest.mark.extendedbase
def test_each_tank_loses_against_the_temperature_of_its_place_and_the_building_books_what_it_releases(
    pinned_run: Tuple[Optional[lt.InstallationLocation], Any, Path],
) -> None:
    """A loss against another temperature than the place's, or a heated-zone loss the building did not take in, fails.

    The buffer at 900 s loses ``UA (T0 - T_surrounding)`` with ``T0`` its start temperature, the hot-water tank
    ``UA (T̄ - T_surrounding)`` with ``T̄`` its step mean. In the heated zone the building's
    ``HeatReleasedByDevicesInWatt`` is the sum of both losses in every step; elsewhere it is zero.
    """
    location, built, _ = pinned_run
    effective = location or lt.InstallationLocation.UNHEATED_SPACE
    frame = built.simulator.results_data_frame
    surrounding_in_celsius = {
        lt.InstallationLocation.HEATED_ZONE: PinnedTwin.column(frame, "Building", Building.TemperatureIndoorAirAtStartOfStepInCelsius),
        lt.InstallationLocation.UNHEATED_SPACE: PinnedTwin.column(frame, "Building", Building.TemperatureUnheatedCellarInCelsius),
        lt.InstallationLocation.OUTDOORS: PinnedTwin.column(frame, "Weather", Weather.TemperatureOutside),
    }[effective].to_numpy()
    buffer = PinnedTwin.component(built, "SimpleHotWaterStorage")
    tank = PinnedTwin.component(built, "DHWStorage")
    buffer_loss_in_watt = PinnedTwin.column(frame, "SimpleHotWaterStorage", SimpleHotWaterStorage.StandbyHeatLoss).to_numpy()
    tank_loss_in_watt = PinnedTwin.column(frame, "DHWStorage", SimpleDHWStorage.StandbyHeatLoss).to_numpy()
    buffer_start_in_celsius = PinnedTwin.column(
        frame, "SimpleHotWaterStorage", SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius
    ).to_numpy()
    tank_mean_in_celsius = PinnedTwin.column(
        frame, "DHWStorage", SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius
    ).to_numpy()
    assert buffer_loss_in_watt == pytest.approx(
        buffer.heat_loss_coefficient_in_watt_per_kelvin * (buffer_start_in_celsius - surrounding_in_celsius), rel=1e-9, abs=1e-9
    )
    assert tank_loss_in_watt == pytest.approx(
        tank.tank.loss_coefficient_in_watt_per_kelvin * (tank_mean_in_celsius - surrounding_in_celsius), rel=1e-9, abs=1e-9
    )
    released_in_watt = PinnedTwin.column(frame, "Building", Building.HeatReleasedByDevicesInWatt).to_numpy()
    if effective is lt.InstallationLocation.HEATED_ZONE:
        assert released_in_watt == pytest.approx(buffer_loss_in_watt + tank_loss_in_watt, rel=1e-9, abs=1e-9)
    else:
        assert not released_in_watt.any()


@pytest.mark.extendedbase
def test_the_gas_sizer_twin_closes_its_balance_in_every_step(tmp_path: Path) -> None:
    """The gas twin's tanks, in the unheated cellar its TABULA row gives, must close as the heat-pump twin's do."""
    _, results = PinnedTwin.run(tmp_path, "household_gas_building_sizer", None)
    entries = PinnedTwin.balance_entries(results)
    for name in (*PinnedTwin.TANKS, "Building", "CondensingGasBoiler"):
        assert entries[name]["verdict"] == "closes" and entries[name]["failing_steps"] == 0, entries[name]
