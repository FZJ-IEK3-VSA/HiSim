"""The building's energy ports close the 5R1C model of ISO 13790 in every step.

The building declares what enters its heated zone (the space heating its heating system delivers, the heat of a CHP,
the sun through the windows, the internal gains), the heat a cooling device takes out, the zone's losses to the
outside and the change of the heat its thermal mass holds. The 5R1C model has three nodes: the indoor air and the
internal surface hold no heat, the thermal mass holds C_m. Adding the three node equations of a step cancels every
conductance between the nodes, so the heating and the gains equal the ventilation loss H_ve (theta_air - theta_e),
the transmission losses H_tr_w (theta_s - theta_e) and H_tr_em (theta_m - theta_e), the share of the gains the gain
split gives to no node, H_tr_w / (h_ms A_t) (0.5 Phi_int + Phi_sol), and C_m times the mass's temperature change.

These tests drive the ``Building`` through the synthetic day of the one-day snapshot
(``tests/test_building_one_day_snapshot.py``) and through a cooling day, and call its loss law directly. What enters
and what a cooling device removes are ports on the inputs that read them: the harness feeds the two port inputs the
two directions of the delivered heat, as a distribution system does.
"""

from typing import Dict, List, Tuple

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.caching import CacheSettings
from hisim.components.building import Building
from hisim.components.heat_distribution_system import HeatDistribution
from hisim.config import ComponentID
from tests import functions_for_testing as fft
from tests.test_building_one_day_snapshot import OneDaySnapshot, SyntheticDayProfiles


class BuildingDay:
    """Drives one snapshot variant of the building through a day of synthetic inputs and records its ports.

    The wiring and the inputs are the one-day snapshot's; a test may replace an input vector. The two port inputs of
    the delivered heat read its two directions, each >= 0, derived from the snapshot's signed delivered heat as a
    distribution system publishes them. The per-step protocol mirrors the simulator: save the state, restore it,
    simulate.
    """

    #: The channel names of the building's energy ports, input or output, by their role in its balance.
    INPUTS: List[str] = [
        Building.HeatReceivedInWatt,
        Building.ThermalPowerCHP,
        Building.SolarGainThroughWindows,
        Building.HeatingByResidents,
        Building.HeatingByDevices,
    ]
    OUTPUTS: List[str] = [Building.HeatRemovedByCoolingInWatt]
    LOSSES: List[str] = [
        Building.VentilationLossInWatt,
        Building.TransmissionLossWindowsAndDoorsInWatt,
        Building.TransmissionLossOpaqueInWatt,
        Building.GainsLostThroughWindowsInWatt,
    ]

    @staticmethod
    def directions_in_watt(delivered_in_watt: List[float]) -> Tuple[List[float], List[float]]:
        """Split a signed delivered heat into the heat received and the heat removed by cooling, each >= 0, in W.

        For example, [100.0, -40.0] gives [100.0, 0.0] received and [0.0, 40.0] removed.
        """
        return [max(0.0, value) for value in delivered_in_watt], [max(0.0, -value) for value in delivered_in_watt]

    @classmethod
    def run(cls, variant_name: str, replaced_inputs: Dict[str, List[float]]) -> List[Dict[str, float]]:
        """Simulate one variant through the synthetic day and return every output's and port input's value per step.

        Args:
            variant_name: One of :meth:`OneDaySnapshot.variant_names`.
            replaced_inputs: Input vectors that replace the synthetic day's, by input-channel attribute name.

        Returns:
            One mapping of channel name to value per step, in order: every output, and every input carrying an
            energy port.
        """
        building = Building(
            config=OneDaySnapshot.config_for(variant_name),
            my_simulation_parameters=OneDaySnapshot.simulation_parameters(),
        )
        building.set_sim_repo(OneDaySnapshot.repository_with_synthetic_weather())
        source_outputs = OneDaySnapshot.wire_synthetic_inputs(building)
        input_vectors = {**SyntheticDayProfiles.input_vectors(), **replaced_inputs}
        received, removed = cls.directions_in_watt(input_vectors["thermal_power_delivered_channel"])
        input_vectors.update({"heat_received_channel": received, "heat_removed_by_cooling_channel": removed})
        for channel_name in ("heat_received_channel", "heat_removed_by_cooling_channel"):
            channel: cp.ComponentInput = getattr(building, channel_name)
            source_output = cp.ComponentOutput(
                OneDaySnapshot.SYNTHETIC_SOURCE_NAME,
                channel.field_name,
                channel.loadtype,
                channel.unit,
                component_id=ComponentID(name=OneDaySnapshot.SYNTHETIC_SOURCE_NAME),
            )
            channel.source_output = source_output
            source_outputs[channel_name] = source_output
        fft.add_global_index_of_components([*source_outputs.values(), building])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*source_outputs.values(), building]))
        building.i_prepare_simulation()
        port_inputs = [item for item in building.inputs if item.energy_port is not None]
        steps: List[Dict[str, float]] = []
        for timestep in range(SyntheticDayProfiles.TIMESTEPS):
            for channel_name, source_output in source_outputs.items():
                stsv.values[source_output.global_index] = input_vectors[channel_name][timestep]
            building.i_save_state()
            building.i_restore_state()
            building.i_simulate(timestep, stsv, False)
            step = {output.field_name: stsv.values[output.global_index] for output in building.outputs}
            step.update({item.field_name: stsv.get_input_value(item) for item in port_inputs})
            steps.append(step)
        return steps

    @classmethod
    def residual_in_watt(cls, step: Dict[str, float]) -> float:
        """Return what enters the building minus what leaves it, is lost and is stored over one step, in W."""
        stored_in_watt = step[Building.ThermalMassStoredEnergyIncreaseInWattHour] * 3600.0 / SyntheticDayProfiles.SECONDS_PER_TIMESTEP
        return (
            sum(step[name] for name in cls.INPUTS)
            - sum(step[name] for name in cls.OUTPUTS)
            - sum(step[name] for name in cls.LOSSES)
            - stored_in_watt
        )

    @classmethod
    def throughput_in_watt(cls, step: Dict[str, float]) -> float:
        """Return the sum of the magnitudes of every port of one step, in W, the scale of its residual."""
        stored_in_watt = step[Building.ThermalMassStoredEnergyIncreaseInWattHour] * 3600.0 / SyntheticDayProfiles.SECONDS_PER_TIMESTEP
        return sum(abs(step[name]) for name in cls.INPUTS + cls.OUTPUTS + cls.LOSSES) + abs(stored_in_watt)


@pytest.fixture(name="redirected_solar_gain_cache")
def fixture_redirected_solar_gain_cache(tmp_path, monkeypatch) -> None:
    """Send the building's solar-gain cache into a temporary directory, so the test writes nothing into the tree."""
    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path))


@pytest.fixture(name="building")
def fixture_building(redirected_solar_gain_cache) -> Building:
    """The default single-family home of the one-day snapshot, built but not simulated."""
    del redirected_solar_gain_cache
    building: Building = Building(
        config=OneDaySnapshot.config_for(OneDaySnapshot.DEFAULT_VARIANT_NAME),
        my_simulation_parameters=OneDaySnapshot.simulation_parameters(),
    )
    return building


@pytest.mark.base
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_the_building_s_ports_close_in_every_step_of_the_synthetic_day(variant_name: str, redirected_solar_gain_cache) -> None:
    """In minus loss minus stored change is zero in every step: heating, CHP, sun, internal gains and airing.

    The scaled variant opens its windows in the afternoon; airing overwrites the published air temperature after the
    solve and moves no heat, so the losses must use the solve's air temperature. Catches: a loss against the
    published (aired) air temperature, a stored change of the step-average state (half the persistent change), and a
    gain share that reaches no node and no port.
    """
    del redirected_solar_gain_cache
    steps = BuildingDay.run(variant_name, {})
    unbalanced = [
        (timestep, BuildingDay.residual_in_watt(step))
        for timestep, step in enumerate(steps)
        if abs(BuildingDay.residual_in_watt(step)) > 1e-9 * BuildingDay.throughput_in_watt(step)
    ]
    assert not unbalanced, f"the building does not close in {len(unbalanced)} steps, first {unbalanced[:3]}"
    assert sum(step[Building.ThermalPowerCHP] for step in steps) > 0.0
    assert sum(step[Building.SolarGainThroughWindows] for step in steps) > 0.0
    if variant_name == OneDaySnapshot.SCALED_VARIANT_NAME:
        assert sum(step[Building.OpenWindow] for step in steps) > 0, "the scaled variant airs on the synthetic day"


@pytest.mark.base
def test_heat_from_a_cooling_device_leaves_through_the_cooling_port(redirected_solar_gain_cache) -> None:
    """A cooling device's heat, read as the heat removed by cooling, positive, closes the building with its cooling.

    Catches: a building that computes with a cooling it does not book, or books it with the wrong sign.
    """
    del redirected_solar_gain_cache
    half_day = SyntheticDayProfiles.TIMESTEPS // 2
    cooling_in_watt = [0.0] * half_day + [-1500.0] * (SyntheticDayProfiles.TIMESTEPS - half_day)
    steps = BuildingDay.run(OneDaySnapshot.DEFAULT_VARIANT_NAME, {"thermal_power_delivered_channel": cooling_in_watt})
    assert [step[Building.HeatRemovedByCoolingInWatt] for step in steps] == [-value for value in cooling_in_watt]
    assert all(step[Building.HeatReceivedInWatt] == 0.0 for step in steps)
    assert all(abs(BuildingDay.residual_in_watt(step)) <= 1e-9 * BuildingDay.throughput_in_watt(step) for step in steps)


@pytest.mark.base
def test_the_losses_are_each_conductance_times_its_node_above_the_outside(building: Building) -> None:
    """Each loss is its conductance times its node's temperature above the outside, negative when it is below.

    Catches: a loss against the wrong node (the opaque elements lose from the thermal mass, the windows from the
    internal surface) or with its sign turned.
    """
    losses = building.heat_losses_to_outside_in_watt(
        indoor_air_temperature_in_celsius=21.0,
        internal_surface_temperature_in_celsius=19.0,
        thermal_mass_temperature_in_celsius=18.0,
        outside_temperature_in_celsius=-5.0,
        internal_heat_gains_in_watt=400.0,
        solar_heat_gains_in_watt=1000.0,
    )
    assert losses.ventilation_in_watt == pytest.approx(building.thermal_conductance_by_ventilation_in_watt_per_kelvin * 26.0)
    assert losses.windows_and_doors_in_watt == pytest.approx(
        building.transmission_heat_transfer_coeff_windows_and_door_in_watt_per_kelvin * 24.0
    )
    assert losses.opaque_in_watt == pytest.approx(
        building.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin * 23.0
    )
    assert losses.gains_lost_through_windows_in_watt == pytest.approx(building.share_of_gains_lost_through_windows * 1200.0)
    warm_outside = building.heat_losses_to_outside_in_watt(
        indoor_air_temperature_in_celsius=21.0,
        internal_surface_temperature_in_celsius=21.0,
        thermal_mass_temperature_in_celsius=21.0,
        outside_temperature_in_celsius=31.0,
        internal_heat_gains_in_watt=0.0,
        solar_heat_gains_in_watt=0.0,
    )
    assert warm_outside.ventilation_in_watt < 0.0 and warm_outside.windows_and_doors_in_watt < 0.0
    assert warm_outside.opaque_in_watt < 0.0 and warm_outside.gains_lost_through_windows_in_watt == 0.0


@pytest.mark.base
def test_the_gain_split_and_the_share_lost_through_the_windows_add_up_to_the_gains(building: Building) -> None:
    """The air, surface and mass shares of ISO 13790's gain split plus the share lost through the windows are the gains.

    Catches: a share of the gains that reaches neither a node nor the loss port, so the building could not close.
    """
    internal_heat_gains_in_watt, solar_heat_gains_in_watt = 400.0, 1000.0
    to_air, to_surface, to_mass = building.calc_internal_heat_flows_from_internal_gains_and_solar_gains(
        internal_heat_gains_in_watt, solar_heat_gains_in_watt
    )
    lost = building.share_of_gains_lost_through_windows * (0.5 * internal_heat_gains_in_watt + solar_heat_gains_in_watt)
    assert to_air + to_surface + to_mass + lost == pytest.approx(internal_heat_gains_in_watt + solar_heat_gains_in_watt, rel=1e-14)
    assert 0.0 < building.share_of_gains_lost_through_windows < 0.05


@pytest.mark.base
@pytest.mark.parametrize(
    ("heating_in_watt", "outside_temperature_in_celsius", "previous_thermal_mass_temperature_in_celsius"),
    [(6000.0, -10.0, 20.0), (0.0, 5.0, 21.0), (-2500.0, 32.0, 26.0), (0.0, 30.0, 18.0)],
)
def test_a_solved_step_puts_its_heating_and_gains_into_the_losses_and_the_thermal_mass(
    building: Building,
    heating_in_watt: float,
    outside_temperature_in_celsius: float,
    previous_thermal_mass_temperature_in_celsius: float,
) -> None:
    """Heating plus gains equal the four losses plus C_m times the mass's temperature change per second.

    Catches: a loss formula that does not match the node equations the Crank-Nicolson solve uses (C.4 to C.11).
    """
    internal_heat_gains_in_watt, solar_heat_gains_in_watt = 300.0, 800.0
    (mean_mass, surface, air, _, _, next_mass, _, _) = building.calc_crank_nicolson(
        internal_heat_gains_in_watt=internal_heat_gains_in_watt,
        solar_heat_gains_in_watt=solar_heat_gains_in_watt,
        outside_temperature_in_celsius=outside_temperature_in_celsius,
        thermal_mass_temperature_prev_in_celsius=previous_thermal_mass_temperature_in_celsius,
        thermal_power_delivered_in_watt=heating_in_watt,
    )
    losses = building.heat_losses_to_outside_in_watt(
        indoor_air_temperature_in_celsius=air,
        internal_surface_temperature_in_celsius=surface,
        thermal_mass_temperature_in_celsius=mean_mass,
        outside_temperature_in_celsius=outside_temperature_in_celsius,
        internal_heat_gains_in_watt=internal_heat_gains_in_watt,
        solar_heat_gains_in_watt=solar_heat_gains_in_watt,
    )
    stored_in_watt = (
        building.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
        * (next_mass - previous_thermal_mass_temperature_in_celsius)
        / building.seconds_per_timestep
    )
    entering_in_watt = heating_in_watt + internal_heat_gains_in_watt + solar_heat_gains_in_watt
    assert entering_in_watt == pytest.approx(sum(losses) + stored_in_watt, rel=1e-12, abs=1e-9)


@pytest.mark.base
def test_the_building_tags_its_receiving_inputs_and_keeps_no_copy_outputs(building: Building) -> None:
    """What enters the building and what a cooling device removes are ports on the inputs that read them.

    The signed ``ThermalPowerDelivered``, which the building computes with, carries no port; the distribution
    system's two directions arrive on two tagged inputs through the default connections, and the internal gains on
    the occupancy's two inputs. Catches: a port left on the report column ``InternalHeatGainsFromOccupancy``, which
    would book the gains twice, and a copy-output of an input that came back.
    """
    ports = {item.field_name: item.energy_port for item in building.inputs if item.energy_port is not None}
    assert {name: (port.role, port.carrier) for name, port in ports.items()} == {
        Building.ThermalPowerCHP: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        Building.HeatReceivedInWatt: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        Building.HeatRemovedByCoolingInWatt: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.COOLING),
        Building.HeatingByResidents: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
        Building.HeatingByDevices: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ROOM_HEAT),
    }
    output_ports = {output.field_name for output in building.outputs if output.energy_port is not None}
    assert output_ports == {
        Building.SolarGainThroughWindows,
        Building.VentilationLossInWatt,
        Building.TransmissionLossWindowsAndDoorsInWatt,
        Building.TransmissionLossOpaqueInWatt,
        Building.GainsLostThroughWindowsInWatt,
        Building.ThermalMassStoredEnergyIncreaseInWattHour,
    }
    from_distribution = {
        connection.target_input_name: connection.source_output_name
        for connection in building.default_connections[HeatDistribution.get_classname()]
    }
    assert from_distribution == {
        Building.ThermalPowerDelivered: HeatDistribution.ThermalPowerDelivered,
        Building.HeatReceivedInWatt: HeatDistribution.HeatDeliveredInWatt,
        Building.HeatRemovedByCoolingInWatt: HeatDistribution.HeatDrawnForCoolingInWatt,
    }
