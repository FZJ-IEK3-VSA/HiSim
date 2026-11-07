"""The house battery's losses go where the battery stands: into the building's heated zone, or out of the house.

Bead hisim-device-location-820d.3, in the shape of owner decision D-h of ``roadmap/device_location_spec.md``. The
battery's ``installation_location`` is a sized field that follows the building's technical room (the fact
``technical_room_location``) and that a file may pin, ``OUTDOORS`` for a battery in the garage. bslib's losses do not
depend on the temperature of the surroundings, so the battery reads no temperature; the location decides only where
its losses go:

* two fixed outputs carry the sum of the inverter's and the cells' loss, ``HeatToHeatedZoneInWatt`` (``PRODUCTION`` of
  ``ROOM_HEAT``, read by the building) and ``HeatLossInWatt`` (a ``LOSS`` of electricity); the location fills one, the
  other is 0;
* ``InverterLossInWatt`` and ``CellLossInWatt`` keep their names and numbers and carry no port.

The first tests build a battery alone and step it on a fake set point; the last ones run the heat-pump sizer twin for
three April days at 900 s with the battery pinned to each location, and read the run's results and its energy balance
report.
"""

from pathlib import Path
from typing import Any, ClassVar, Optional, Tuple

import numpy as np
import pytest

from hisim import loadtypes as lt
from hisim.caching import CacheSettings
from hisim.components.advanced_battery_bslib import Battery, BatteryConfig
from hisim.components.building.building import Building
from hisim.config import ComponentID, ConfigSizingError, SizingContext
from hisim.energy_port import EnergyPort
from hisim.energy_system.recording.observe import SystemObserver
from tests.part_load_rigs import AllInputsRig, Parameters
from tests.test_building_one_day_snapshot import OneDaySnapshot
from tests.test_water_storage_installation_location import PinnedTwin


class Batteries:
    """Builds a generic 10 kWh, 5 kW battery standing in a given place, without a system around it."""

    #: The battery's capacity, kWh.
    CAPACITY_IN_KILOWATT_HOUR: ClassVar[float] = 10.0
    #: The battery's inverter power, W.
    INVERTER_POWER_IN_WATT: ClassVar[float] = 5000.0
    #: The set point the battery is stepped with, W: charging at 4 kW from half charge, which loses in both stages.
    SET_POINT_IN_WATT: ClassVar[float] = 4000.0

    @classmethod
    def build(cls, location: lt.InstallationLocation) -> Battery:
        """Return the battery standing at ``location``, for one day at 900 s.

        Args:
            location: Where it stands.

        Returns:
            The battery, not wired.
        """
        config = BatteryConfig(
            component_id=ComponentID(name="Battery"),
            custom_pv_inverter_power_generic_in_watt=cls.INVERTER_POWER_IN_WATT,
            custom_battery_capacity_generic_in_kilowatt_hour=cls.CAPACITY_IN_KILOWATT_HOUR,
            installation_location=location,
        )
        return Battery(my_simulation_parameters=Parameters.one_day(900), config=config)


@pytest.mark.base
@pytest.mark.parametrize("location", list(lt.InstallationLocation))
def test_the_two_released_heat_outputs_have_the_same_ports_in_every_location_and_the_two_losses_none(
    location: lt.InstallationLocation,
) -> None:
    """A port that followed the location would make a recorded file right only for the house it was recorded on (D-h);
    a port left on a loss output would count the battery's losses twice in the balance.
    """
    battery = Batteries.build(location)
    assert battery.heat_to_heated_zone_channel.energy_port == EnergyPort.HEAT_RELEASED_INTO_THE_HEATED_ZONE
    assert battery.heat_loss_channel.energy_port == EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY)
    assert battery.inverter_loss_channel.energy_port is None
    assert battery.cell_loss_channel.energy_port is None
    assert [component_input.field_name for component_input in battery.inputs] == [Battery.LoadingPowerInput]


@pytest.mark.base
@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (lt.InstallationLocation.HEATED_ZONE, (80.0, 0.0)),
        (lt.InstallationLocation.UNHEATED_SPACE, (0.0, 80.0)),
        (lt.InstallationLocation.OUTDOORS, (0.0, 80.0)),
    ],
)
def test_the_location_puts_the_losses_on_its_own_output_and_zero_on_the_other(
    location: lt.InstallationLocation, expected: Tuple[float, float]
) -> None:
    """80 W of losses go into the heated zone there, and out of the house from an unheated space or outdoors."""
    assert Battery.released_heat_in_watt(location, 80.0) == expected


@pytest.mark.base
@pytest.mark.parametrize("location", list(lt.InstallationLocation))
def test_a_stepped_battery_books_the_sum_of_its_losses_on_its_place_s_output(location: lt.InstallationLocation) -> None:
    """Charging at 4 kW, the battery's place's output carries the inverter's plus the cells' loss and the other is 0.

    Catches: a battery that books one loss only, books its losses on the other destination's output, or changes the
    two losses it keeps publishing under their own names.
    """
    battery = Batteries.build(location)
    outputs = AllInputsRig.step_once(battery, {Battery.LoadingPowerInput: Batteries.SET_POINT_IN_WATT})
    inverter_loss_in_watt, cell_loss_in_watt = Battery.losses_in_watt(
        ac_power_in_watt=outputs[Battery.AcBatteryPowerUsed],
        dc_power_in_watt=outputs[Battery.DcBatteryPowerUsed],
        stored_energy_increase_in_watt_hour=outputs[Battery.StoredEnergyIncreaseInWattHour],
        seconds_per_timestep=900,
    )
    assert outputs[Battery.InverterLossInWatt] == inverter_loss_in_watt
    assert outputs[Battery.CellLossInWatt] == cell_loss_in_watt
    assert inverter_loss_in_watt > 0.0 and cell_loss_in_watt > 0.0
    filled, idle = (
        (Battery.HeatToHeatedZoneInWatt, Battery.HeatLossInWatt)
        if location.releases_into_heated_zone
        else (Battery.HeatLossInWatt, Battery.HeatToHeatedZoneInWatt)
    )
    assert outputs[filled] == inverter_loss_in_watt + cell_loss_in_watt
    assert outputs[idle] == 0.0


@pytest.mark.base
def test_an_auto_location_without_a_building_fails_naming_the_fact() -> None:
    """A battery sized in a system without a building would otherwise stand nowhere."""
    with pytest.raises(ConfigSizingError, match="technical_room_location"):
        BatteryConfig.preset_sized_to_pv("Battery").resolve(SizingContext(pv_peak_power_in_watt=(10000.0,)))


@pytest.mark.base
def test_an_auto_location_follows_the_technical_room_and_a_pinned_one_stays() -> None:
    """A battery that ignored the building, or overrode a file's pin of the garage, would stand where nobody put it."""
    config = BatteryConfig.preset_sized_to_pv("Battery")
    heated_zone = SizingContext(pv_peak_power_in_watt=(10000.0,), technical_room_location=lt.InstallationLocation.HEATED_ZONE)
    assert config.resolve(heated_zone).installation_location is lt.InstallationLocation.HEATED_ZONE
    config.installation_location = lt.InstallationLocation.OUTDOORS
    assert config.resolve(heated_zone).installation_location is lt.InstallationLocation.OUTDOORS


@pytest.mark.base
def test_the_recorder_sees_the_building_read_the_battery_s_heat_into_the_heated_zone_in_every_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The building reads ``HeatToHeatedZoneInWatt`` of a battery in an unheated cellar too, so a twin lists it.

    Catches a transfer the recorder would only see in the heated zone, which would make a file recorded on one house
    wrong for another (D-h).
    """
    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path))
    building = Building(
        config=OneDaySnapshot.config_for(OneDaySnapshot.DEFAULT_VARIANT_NAME),
        my_simulation_parameters=OneDaySnapshot.simulation_parameters(),
    )
    for location in lt.InstallationLocation:
        battery = Batteries.build(location)
        components = (building, battery)
        assert SystemObserver._transfer_defaults(building, components) == {  # pylint: disable=protected-access
            battery.component_name: (Battery.HeatToHeatedZoneInWatt,),
        }
        assert not SystemObserver._transfer_defaults(battery, components)  # pylint: disable=protected-access


class PinnedBattery(PinnedTwin):
    """The heat-pump sizer twin with only its battery pinned, for three April days, when the battery cycles daily."""

    PINNED: ClassVar[Tuple[str, ...]] = ("Battery",)
    START: ClassVar[str] = "2021-04-10"
    END: ClassVar[str] = "2021-04-13"


@pytest.fixture(name="battery_run", scope="module", params=[*lt.InstallationLocation, None], ids=lambda value: getattr(value, "name", "AUTO"))
def fixture_battery_run(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Tuple[Optional[lt.InstallationLocation], Any, Path]:
    """The heat-pump twin with its battery at each location and as recorded, run once per location for this module."""
    location: Optional[lt.InstallationLocation] = request.param
    built, results = PinnedBattery.run(
        tmp_path_factory.mktemp(getattr(location, "name", "AUTO")), "household_heatpump_building_sizer", location
    )
    return location, built, results


@pytest.mark.extendedbase
def test_the_battery_and_the_building_close_their_balance_in_every_step_at_every_location(
    battery_run: Tuple[Optional[lt.InstallationLocation], Any, Path],
) -> None:
    """A battery whose ports did not match its booked losses would fail the balance in the location that differs.

    The recorded twin stands on a TABULA row with an unheated cellar, so its AUTO battery stands in an unheated space.
    The output a location leaves idle books nothing: no link, and nothing unlinked. The two loss outputs carry no port.
    """
    _, _, results = battery_run
    entries = PinnedBattery.balance_entries(results)
    entry = entries["Battery"]
    assert entry["verdict"] == "closes" and entry["failing_steps"] == 0, entry
    ports = {port["output"]: port for port in entry["ports"]}
    assert Battery.InverterLossInWatt not in ports and Battery.CellLossInWatt not in ports
    heat_to_zone = ports[Battery.HeatToHeatedZoneInWatt]
    heat_loss = ports[Battery.HeatLossInWatt]
    assert (heat_to_zone["role"], heat_to_zone["carrier"]) == ("production", "room_heat")
    assert (heat_to_zone["peer_kind"], heat_to_zone["peer"]) == ("component", "Building")
    assert (heat_loss["role"], heat_loss["carrier"], heat_loss["peer_kind"]) == ("loss", "electricity", "boundary")
    assert entries["Building"]["verdict"] == "closes" and entries["Building"]["failing_steps"] == 0


@pytest.mark.extendedbase
def test_the_battery_s_losses_go_where_it_stands_and_the_building_books_them_in_the_heated_zone(
    battery_run: Tuple[Optional[lt.InstallationLocation], Any, Path],
) -> None:
    """The location's output carries the inverter's plus the cells' loss in every step, the other is 0, and in the
    heated zone the building's ``HeatReleasedByDevicesInWatt`` is the battery's heat (the tanks stand in the cellar).
    """
    location, built, _ = battery_run
    location = location or lt.InstallationLocation.UNHEATED_SPACE
    frame = built.simulator.results_data_frame
    losses_in_watt = (
        PinnedBattery.column(frame, "Battery", Battery.InverterLossInWatt).to_numpy()
        + PinnedBattery.column(frame, "Battery", Battery.CellLossInWatt).to_numpy()
    )
    assert losses_in_watt.sum() > 0.0
    filled, idle = (
        (Battery.HeatToHeatedZoneInWatt, Battery.HeatLossInWatt)
        if location.releases_into_heated_zone
        else (Battery.HeatLossInWatt, Battery.HeatToHeatedZoneInWatt)
    )
    np.testing.assert_array_equal(PinnedBattery.column(frame, "Battery", filled).to_numpy(), losses_in_watt)
    assert not PinnedBattery.column(frame, "Battery", idle).to_numpy().any()
    released_in_watt = PinnedBattery.column(frame, "Building", Building.HeatReleasedByDevicesInWatt).to_numpy()
    if location.releases_into_heated_zone:
        # The building reads the battery's heat as the battery published it when the building last ran in the step;
        # the battery's set point depends on the heat pump, the heat pump on the building, so the two settle to the
        # simulator's convergence tolerance, a few microwatts here, not to the bit.
        np.testing.assert_allclose(released_in_watt, losses_in_watt, rtol=1e-6, atol=1e-5)
    else:
        assert not released_in_watt.any()
