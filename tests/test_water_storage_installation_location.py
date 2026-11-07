"""The buffer and the hot-water tank lose their standby heat to the place they stand in, and hand it where that place says.

Bead hisim-device-location-820d.2, in the shape of owner decision D-h of ``roadmap/device_location_spec.md``. Each
tank's ``installation_location`` is a sized field that follows the building's technical room (the fact
``technical_room_location``) and that a file may pin. Nothing in a tank's wiring depends on it:

* three mandatory temperature inputs with one static default connection each, the same in every location
  (``Building.TemperatureIndoorAirAtStartOfStepInCelsius``, ``Building.TemperatureUnheatedCellarInCelsius``,
  ``Weather.TemperatureOutside``); the location picks the one the standby loss ``UA (T_tank - T_surrounding)`` is
  computed against, published as ``SurroundingTemperatureInCelsius``;
* two fixed outputs of the standby heat, ``HeatToHeatedZoneInWatt`` (``PRODUCTION`` of ``ROOM_HEAT``, read by the
  building) and ``HeatLossInWatt`` (a ``LOSS`` of the tank's carrier); the location fills one, the other is 0.

The first tests build a tank alone and step it on fake inputs; the last ones run the building-sizer twins for three
January days at 900 s with the tanks pinned to each location, or with the building's row replaced by a heated-zone
row, and read the run's results and its energy balance report.
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
from hisim.caching import CacheSettings
from hisim.energy_port import EnergyPort
from hisim.energy_system.recording.observe import SystemObserver
from tests.part_load_rigs import AllInputsRig, Parameters
from tests.test_building_one_day_snapshot import OneDaySnapshot

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

    #: The source of each temperature input, the same in every location.
    TEMPERATURE_SOURCES: ClassVar[Dict[str, Tuple[str, str]]] = {
        SimpleHotWaterStorage.IndoorAirTemperatureInCelsius: (
            Building.__name__,
            Building.TemperatureIndoorAirAtStartOfStepInCelsius,
        ),
        SimpleHotWaterStorage.UnheatedCellarTemperatureInCelsius: (
            Building.__name__,
            Building.TemperatureUnheatedCellarInCelsius,
        ),
        SimpleHotWaterStorage.OutsideAirTemperatureInCelsius: (Weather.__name__, Weather.TemperatureOutside),
    }
    #: Three different temperatures on the three inputs, all below the tanks' start temperature, °C.
    DISTINCT_TEMPERATURES_IN_CELSIUS: ClassVar[Dict[str, float]] = {
        SimpleHotWaterStorage.IndoorAirTemperatureInCelsius: 21.0,
        SimpleHotWaterStorage.UnheatedCellarTemperatureInCelsius: 9.0,
        SimpleHotWaterStorage.OutsideAirTemperatureInCelsius: -4.0,
    }

    @staticmethod
    def expected_standby_loss_in_watt(
        tank: Union[SimpleHotWaterStorage, SimpleDHWStorage], outputs: Mapping[str, float], surrounding_in_celsius: float
    ) -> float:
        """Return ``UA (T - T_surrounding)`` of one step: the buffer's start temperature at 900 s, the tank's step mean.

        Args:
            tank: The stepped tank.
            outputs: Its outputs of the step.
            surrounding_in_celsius: The temperature it should have lost against, °C.

        Returns:
            The standby loss, W.
        """
        if isinstance(tank, SimpleDHWStorage):
            return float(
                tank.tank.loss_coefficient_in_watt_per_kelvin
                * (outputs[SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius] - surrounding_in_celsius)
            )
        assert isinstance(tank, SimpleHotWaterStorage)
        return float(
            tank.heat_loss_coefficient_in_watt_per_kelvin
            * (outputs[SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius] - surrounding_in_celsius)
        )

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
@pytest.mark.parametrize("location", list(lt.InstallationLocation))
def test_the_three_temperature_inputs_are_wired_by_the_same_defaults_in_every_location(
    tank_class: TankClass, location: lt.InstallationLocation
) -> None:
    """Every location wires the same three temperature inputs, all mandatory.

    A default connection that followed the location would make a recorded file right only for the house it was
    recorded on (D-h); because all three inputs are mandatory, a file without a building or a weather is refused.
    """
    tank = Tanks.build(tank_class, location)
    wired = {
        connection.target_input_name: (connection.source_class_name, connection.source_output_name)
        for connections in tank.default_connections.values()
        for connection in connections
        if connection.target_input_name in Tanks.TEMPERATURE_SOURCES
    }
    assert wired == Tanks.TEMPERATURE_SOURCES
    inputs = {component_input.field_name: component_input for component_input in tank.inputs}
    for field_name in Tanks.TEMPERATURE_SOURCES:
        assert inputs[field_name].is_mandatory and inputs[field_name].unit == lt.Units.CELSIUS


@pytest.mark.base
@pytest.mark.parametrize("tank_class", [SimpleHotWaterStorage, SimpleDHWStorage])
def test_the_two_released_heat_outputs_have_the_same_ports_in_every_location(tank_class: TankClass) -> None:
    """The two released-heat outputs carry the same energy ports in every location.

    A port that followed the location would bring back the location-dependent wiring D-h removed; the standby loss
    output of the first build is gone, since the two channels say all it said.
    """
    carrier = Tanks.CARRIER[tank_class.__name__]
    for location in lt.InstallationLocation:
        tank = Tanks.build(tank_class, location)
        assert tank.heat_to_heated_zone_channel.energy_port == EnergyPort.HEAT_RELEASED_INTO_THE_HEATED_ZONE
        assert tank.heat_loss_channel.energy_port == EnergyPort(lt.EnergyRole.LOSS, carrier)
        assert tank.surrounding_temperature_output_channel.energy_port is None
        assert "StandbyHeatLoss" not in {output.field_name for output in tank.outputs}


@pytest.mark.base
def test_every_location_picks_a_temperature_input() -> None:
    """A location without an entry would fail only when a tank first stood there, not when the code changed."""
    assert set(SimpleHotWaterStorage.SURROUNDING_TEMPERATURE_INPUT_BY_LOCATION) == set(lt.InstallationLocation)


@pytest.mark.base
@pytest.mark.parametrize("tank_class", [SimpleHotWaterStorage, SimpleDHWStorage])
@pytest.mark.parametrize("location", list(lt.InstallationLocation))
def test_each_location_picks_its_temperature_and_fills_its_own_released_heat_output(
    tank_class: TankClass, location: lt.InstallationLocation
) -> None:
    """The tank loses against its own place's temperature and books the loss on its place's output.

    With three different temperatures on the three inputs, the tank computes its loss against the temperature of the
    place it stands in and publishes it on that place's output; the other output is 0.

    Catches: a tank that reads another place's temperature, publishes another than it computed with, or books its heat
    on the output of another destination.
    """
    tank = Tanks.build(tank_class, location)
    outputs = AllInputsRig.step_once(tank, dict(Tanks.DISTINCT_TEMPERATURES_IN_CELSIUS))
    picked_in_celsius = Tanks.DISTINCT_TEMPERATURES_IN_CELSIUS[tank_class.SURROUNDING_TEMPERATURE_INPUT_BY_LOCATION[location]]
    assert outputs[tank_class.SurroundingTemperatureInCelsius] == picked_in_celsius
    expected_in_watt = Tanks.expected_standby_loss_in_watt(tank, outputs, picked_in_celsius)
    assert expected_in_watt > 0.0
    filled, idle = (
        (tank_class.HeatToHeatedZoneInWatt, tank_class.HeatLossInWatt)
        if location is lt.InstallationLocation.HEATED_ZONE
        else (tank_class.HeatLossInWatt, tank_class.HeatToHeatedZoneInWatt)
    )
    assert outputs[filled] == pytest.approx(expected_in_watt, rel=1e-9)
    assert outputs[idle] == 0.0


@pytest.mark.base
def test_a_buffer_in_an_unheated_space_at_50_degrees_loses_ua_times_43_5_kelvin_to_a_6_5_degree_cellar() -> None:
    """A buffer that still lost against the former fixed 20 °C would lose UA · 30 W here instead of UA · 43.5 W.

    At 900 s the buffer is a start-of-step node: its loss over the step is ``UA (T0 - T_surrounding)`` with ``T0`` its
    start temperature, so 50 °C against the 6.5 °C of a cellar at -7 °C outside (b = 0.5, 20 °C inside) is
    ``UA · 43.5 K``, on ``HeatLossInWatt``; ``HeatToHeatedZoneInWatt`` is 0.
    """
    buffer = Tanks.build(SimpleHotWaterStorage, lt.InstallationLocation.UNHEATED_SPACE)
    assert isinstance(buffer, SimpleHotWaterStorage)
    buffer.state = SimpleWaterStorageState(mean_water_temperature_in_celsius=50.0)
    outputs = AllInputsRig.step_once(
        buffer,
        {
            SimpleHotWaterStorage.IndoorAirTemperatureInCelsius: 20.0,
            SimpleHotWaterStorage.UnheatedCellarTemperatureInCelsius: 6.5,
            SimpleHotWaterStorage.OutsideAirTemperatureInCelsius: -7.0,
        },
    )
    ua_in_watt_per_kelvin = buffer.heat_loss_coefficient_in_watt_per_kelvin
    assert outputs[SimpleHotWaterStorage.HeatLossInWatt] == pytest.approx(ua_in_watt_per_kelvin * 43.5, rel=1e-12)
    assert outputs[SimpleHotWaterStorage.HeatToHeatedZoneInWatt] == 0.0


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
        tank, {SimpleDHWStorage.UnheatedCellarTemperatureInCelsius: surrounding_temperature_in_celsius}
    )
    step_mean_in_celsius = outputs[SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius]
    expected_in_watt = tank.tank.loss_coefficient_in_watt_per_kelvin * (
        step_mean_in_celsius - surrounding_temperature_in_celsius
    )
    loss_in_watt = outputs[SimpleDHWStorage.HeatLossInWatt]
    assert loss_in_watt == pytest.approx(expected_in_watt, rel=1e-9, abs=1e-12)
    start_in_celsius = SimpleDHWStorage.INITIAL_TEMPERATURE_IN_CELSIUS
    assert (loss_in_watt > 0.0) == (surrounding_temperature_in_celsius < start_in_celsius)
    if surrounding_temperature_in_celsius == start_in_celsius:
        assert loss_in_watt == pytest.approx(0.0, abs=1e-12)


@pytest.mark.base
def test_the_standby_loss_kpi_sums_both_channels_and_counts_only_the_steps_that_lose() -> None:
    """The KPIs "Standby heat loss of ..." keep their numbers: the positive steps of the sum of the two channels.

    Four 900 s steps losing 100 W, two on each channel, and a fifth gaining 50 W lose 0.1 kWh. Catches: a KPI that reads
    one channel only (half of a tank that moved), and one that nets the gaining step against the losing ones.
    """
    heat_to_heated_zone_in_watt = pd.Series([100.0, 100.0, 0.0, 0.0, 0.0])
    heat_loss_in_watt = pd.Series([0.0, 0.0, 100.0, 100.0, -50.0])
    assert SimpleHotWaterStorage.standby_heat_loss_in_kilowatt_hour(heat_to_heated_zone_in_watt, heat_loss_in_watt, 900) == 0.1
    assert SimpleHotWaterStorage.standby_heat_loss_in_kilowatt_hour(pd.Series([-1.0]), pd.Series([0.0]), 900) == 0.0


@pytest.mark.base
def test_the_recorder_sees_the_building_read_each_tank_s_heat_into_the_heated_zone_in_every_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The building reads ``HeatToHeatedZoneInWatt`` of a tank in an unheated cellar too, so a twin lists every tank.

    Catches a transfer the recorder would only see in the heated zone, which would make a file recorded on one house
    wrong for another (D-h); a tank grows no inputs and declares none.
    """
    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path))
    building = Building(
        config=OneDaySnapshot.config_for(OneDaySnapshot.DEFAULT_VARIANT_NAME),
        my_simulation_parameters=OneDaySnapshot.simulation_parameters(),
    )
    for location in lt.InstallationLocation:
        buffer = Tanks.build(SimpleHotWaterStorage, location)
        tank = Tanks.build(SimpleDHWStorage, location)
        components = (building, buffer, tank)
        assert SystemObserver._transfer_defaults(building, components) == {  # pylint: disable=protected-access
            buffer.component_name: (SimpleHotWaterStorage.HeatToHeatedZoneInWatt,),
            tank.component_name: (SimpleDHWStorage.HeatToHeatedZoneInWatt,),
        }
        assert not SystemObserver._transfer_defaults(buffer, components)  # pylint: disable=protected-access


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
    """A building-sizer twin run for three January days at 900 s as recorded, with only one setting changed.

    Either the located devices of :attr:`PINNED`, both tanks, are pinned to one location, or the building's TABULA row is
    replaced by a heated-zone row. Nothing else of the file changes: its wiring is the same for every house and every
    location (D-h). A subclass pins other devices or runs other days by overriding the class attributes.
    """

    #: The period: three January days, cold enough for the cellar to sit well below the house.
    START: ClassVar[str] = "2021-01-10"
    END: ClassVar[str] = "2021-01-13"
    #: The tanks of every building-sizer twin.
    TANKS: ClassVar[Tuple[str, str]] = ("SimpleHotWaterStorage", "DHWStorage")
    #: The components :meth:`run` pins to the location it is given.
    PINNED: ClassVar[Tuple[str, ...]] = TANKS
    #: A single-family house of 2002 to 2009 without a cellar (cellar code ``-``), whose technical room is in the
    #: heated zone; the twins stand on ``DE.N.SFH.09.Gen.ReEx.001.002``, a house with an unheated cellar.
    HEATED_ZONE_ROW: ClassVar[str] = "DE.N.SFH.11.Gen.ReEx.001.002"

    @classmethod
    def run(
        cls,
        directory: Path,
        twin: str,
        location: Optional[lt.InstallationLocation],
        building_code: Optional[str] = None,
    ) -> Tuple[Any, Path]:
        """Run the twin with the devices of :attr:`PINNED` at ``location`` (``None``: as recorded), or on another row.

        Args:
            directory: Where the files and results go.
            twin: The twin's stem.
            location: Where the devices of :attr:`PINNED` stand, or ``None`` to leave the recorded ``AUTO``.
            building_code: The TABULA row to put the building on, or ``None`` for the recorded one.

        Returns:
            The built system after the run, and the result directory.
        """
        from hisim.energy_system.executor import run_energy_system  # pylint: disable=import-outside-toplevel

        root = Path(__file__).resolve().parent.parent
        text = (root / "energy_systems" / f"{twin}.energy_system.yaml").read_text(encoding="utf-8")
        document = yaml.safe_load(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"))
        if location is not None:
            for pinned in cls.PINNED:
                document["components"][pinned].setdefault("config", {})["installation_location"] = location.name
        if building_code is not None:
            document["components"]["Building"].setdefault("config", {})["building_code"] = building_code
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
        return built.wired.component_of(name)

    @staticmethod
    def balance_report(results: Path) -> Dict[str, Any]:
        """Return the run's balance report, after asserting that the whole report closes and lists nothing unlinked."""
        report: Dict[str, Any] = json.loads((results / "balance_report.json").read_text(encoding="utf-8"))
        assert report["verdict"] == "closes"
        assert report["unlinked"] == [], report["unlinked"]
        return report

    @classmethod
    def balance_entries(cls, results: Path) -> Mapping[str, Dict[str, Any]]:
        """Return the run's balance report entries by component (:meth:`balance_report`)."""
        return {entry["component"]: entry for entry in cls.balance_report(results)["components"]}

    @classmethod
    def check_tank_losses(cls, built: Any, location: lt.InstallationLocation) -> None:
        """Assert that each tank lost against its place's temperature, on its place's output, and the building got it.

        The buffer at 900 s loses ``UA (T0 - T_surrounding)`` with ``T0`` its start temperature, the hot-water tank
        ``UA (T̄ - T_surrounding)`` with ``T̄`` its step mean. In the heated zone the building's
        ``HeatReleasedByDevicesInWatt`` less what the other located devices (the battery) release there is the sum of
        both losses in every step; elsewhere it is that of the other devices alone.

        Args:
            built: The built system after the run.
            location: Where the tanks stood.
        """
        frame = built.simulator.results_data_frame
        surrounding_in_celsius = {
            lt.InstallationLocation.HEATED_ZONE: cls.column(frame, "Building", Building.TemperatureIndoorAirAtStartOfStepInCelsius),
            lt.InstallationLocation.UNHEATED_SPACE: cls.column(frame, "Building", Building.TemperatureUnheatedCellarInCelsius),
            lt.InstallationLocation.OUTDOORS: cls.column(frame, "Weather", Weather.TemperatureOutside),
        }[location].to_numpy()
        filled, idle = (
            (SimpleHotWaterStorage.HeatToHeatedZoneInWatt, SimpleHotWaterStorage.HeatLossInWatt)
            if location.releases_into_heated_zone
            else (SimpleHotWaterStorage.HeatLossInWatt, SimpleHotWaterStorage.HeatToHeatedZoneInWatt)
        )
        losses_in_watt = []
        for tank_name, tank_temperature_output in (
            ("SimpleHotWaterStorage", SimpleHotWaterStorage.WaterTemperatureAtStartOfStepInCelsius),
            ("DHWStorage", SimpleDHWStorage.StepMeanWaterTemperatureToHeatGeneratorInCelsius),
        ):
            tank = cls.component(built, tank_name)
            ua_in_watt_per_kelvin = (
                tank.heat_loss_coefficient_in_watt_per_kelvin
                if isinstance(tank, SimpleHotWaterStorage)
                else tank.tank.loss_coefficient_in_watt_per_kelvin
            )
            assert cls.column(frame, tank_name, SimpleHotWaterStorage.SurroundingTemperatureInCelsius).to_numpy() == (
                pytest.approx(surrounding_in_celsius, rel=0.0, abs=0.0)
            )
            loss_in_watt = cls.column(frame, tank_name, filled).to_numpy()
            assert loss_in_watt == pytest.approx(
                ua_in_watt_per_kelvin * (cls.column(frame, tank_name, tank_temperature_output).to_numpy() - surrounding_in_celsius),
                rel=1e-9,
                abs=1e-9,
            )
            assert not cls.column(frame, tank_name, idle).to_numpy().any()
            losses_in_watt.append(loss_in_watt)
        released_in_watt = cls.column(frame, "Building", Building.HeatReleasedByDevicesInWatt).to_numpy()
        for name in frame.columns:
            device = str(name).split(" - ", 1)[0]
            if str(name).startswith(f"{device} - {SimpleHotWaterStorage.HeatToHeatedZoneInWatt} [") and device not in cls.TANKS:
                released_in_watt = released_in_watt - frame[name].to_numpy()
        if location.releases_into_heated_zone:
            # A battery in the heated zone settles with the building to the simulator's convergence tolerance only
            # (its set point depends on the heat pump, the heat pump on the building), a few microwatts.
            assert released_in_watt == pytest.approx(losses_in_watt[0] + losses_in_watt[1], rel=1e-9, abs=1e-5)
        else:
            assert not released_in_watt.any()


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
    """A tank whose ports did not match its booked loss would fail the balance in the location that differs.

    The recorded twin stands on a TABULA row with an unheated cellar, so its AUTO tanks stand in an unheated space. The
    output a location leaves idle books nothing: no link, and nothing unlinked.
    """
    _, _, results = pinned_run
    entries = PinnedTwin.balance_entries(results)
    for tank in PinnedTwin.TANKS:
        entry = entries[tank]
        assert entry["verdict"] == "closes" and entry["failing_steps"] == 0, entry
        ports = {port["output"]: port for port in entry["ports"]}
        heat_to_zone = ports[SimpleHotWaterStorage.HeatToHeatedZoneInWatt]
        heat_loss = ports[SimpleHotWaterStorage.HeatLossInWatt]
        assert (heat_to_zone["role"], heat_to_zone["carrier"]) == ("production", "room_heat")
        assert (heat_to_zone["peer_kind"], heat_to_zone["peer"]) == ("component", "Building")
        assert (heat_loss["role"], heat_loss["peer_kind"]) == ("loss", "boundary")
    assert entries["Building"]["verdict"] == "closes" and entries["Building"]["failing_steps"] == 0


@pytest.mark.extendedbase
def test_each_tank_loses_against_the_temperature_of_its_place_and_the_building_books_what_it_releases(
    pinned_run: Tuple[Optional[lt.InstallationLocation], Any, Path],
) -> None:
    """The pinned twin's tanks lose against their place's temperature, and the building takes in a heated-zone loss.

    A loss against another temperature than the place's, on another output, or a heated-zone loss the building did
    not take in, fails; the wiring is the recorded file's in every location.
    """
    location, built, _ = pinned_run
    PinnedTwin.check_tank_losses(built, location or lt.InstallationLocation.UNHEATED_SPACE)


@pytest.mark.extendedbase
def test_one_file_recorded_on_an_unheated_cellar_heats_a_house_without_a_cellar_with_its_tanks(tmp_path: Path) -> None:
    """The heat-pump twin, recorded on a row with an unheated cellar, run on a row without a cellar and no other edit.

    Owner decision D-h: the tanks follow the building to the heated zone, their losses arrive in the building's
    ``HeatReleasedByDevicesInWatt`` through the wires the file already has, the balance closes and nothing is unlinked.
    The first build of PR 2 failed exactly this: the tanks published room heat nobody read.
    """
    built, results = PinnedTwin.run(
        tmp_path, "household_heatpump_building_sizer", None, building_code=PinnedTwin.HEATED_ZONE_ROW
    )
    assert PinnedTwin.component(built, "Building").config.technical_room_location is lt.InstallationLocation.HEATED_ZONE
    PinnedTwin.check_tank_losses(built, lt.InstallationLocation.HEATED_ZONE)
    entries = PinnedTwin.balance_entries(results)
    for name in (*PinnedTwin.TANKS, "Building"):
        assert entries[name]["verdict"] == "closes" and entries[name]["failing_steps"] == 0, entries[name]


@pytest.mark.extendedbase
def test_the_gas_sizer_twin_closes_its_balance_in_every_step(tmp_path: Path) -> None:
    """The gas twin's tanks, in the unheated cellar its TABULA row gives, must close as the heat-pump twin's do."""
    _, results = PinnedTwin.run(tmp_path, "household_gas_building_sizer", None)
    entries = PinnedTwin.balance_entries(results)
    for name in (*PinnedTwin.TANKS, "Building", "CondensingGasBoiler"):
        assert entries[name]["verdict"] == "closes" and entries[name]["failing_steps"] == 0, entries[name]
