"""Test for generic dhw boiler."""

from collections import namedtuple

import pytest
from repositories.HiSim.obsolete import generic_hot_water_storage_modular
from repositories.HiSim.obsolete import generic_heat_source
from hisim.components import controller_l1_heatpump

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters


# Timestep (s) shared by every scenario below; matches the simulation parameters.
DEFAULT_TIMESTEP = 60


@pytest.fixture
def boiler_setup():
    """Build a fresh one-day, 60 s/timestep DHW-boiler rig for each test.

    Wires a 200 L :class:`HotWaterStorage` to a district-heating
    :class:`HeatSource` governed by an :class:`L1HeatPumpController`, driven by
    a fake warm-water consumption output, and assigns the same global indices
    used by the original monolithic test so that observed behaviour is
    preserved. Each test receives its own freshly constructed components and
    ``SingleTimeStepValues`` so that no mutable state leaks between scenarios.
    """
    seconds_per_timestep = DEFAULT_TIMESTEP
    my_simulation_parameters = SimulationParameters.one_day_only(
        2017, seconds_per_timestep
    )

    # Component configs
    l1_config = (
        controller_l1_heatpump.L1HeatPumpConfig.get_default_config_heat_source_controller_dhw(
            "HP Controller"
        )
    )
    boiler_config = (
        generic_hot_water_storage_modular.StorageConfig.get_default_config_for_boiler()
    )
    boiler_config.volume = 200
    heater_config = generic_heat_source.HeatSourceConfig.get_default_config_waterheating(
        heating_system=lt.HeatingSystems.DISTRICT_HEATING,
        max_warm_water_demand_in_liter=200,
        scaling_factor_according_to_number_of_apartments=1,
        seconds_per_timestep=seconds_per_timestep,
    )

    # definition of outputs
    number_of_outputs = 5
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Set Boiler
    my_boiler = generic_hot_water_storage_modular.HotWaterStorage(
        config=boiler_config, my_simulation_parameters=my_simulation_parameters
    )
    my_heater = generic_heat_source.HeatSource(
        config=heater_config, my_simulation_parameters=my_simulation_parameters
    )
    # Set L1 Boiler Controller
    my_boiler_controller_l1 = controller_l1_heatpump.L1HeatPumpController(
        config=l1_config, my_simulation_parameters=my_simulation_parameters
    )

    # definition of hot water use
    ww_use = cp.ComponentOutput(
        "FakeWarmwaterUse", "WaterConsumption", lt.LoadTypes.WARM_WATER, lt.Units.LITER
    )

    # connection of in- and outputs
    my_boiler_controller_l1.storage_temperature_channel.source_output = (
        my_boiler.temperature_mean_channel
    )
    my_boiler.water_consumption_channel.source_output = ww_use
    my_boiler.thermal_power_delivered_channel.source_output = (
        my_heater.thermal_power_delivered_channel
    )
    my_heater.l1_heatsource_taget_percentage.source_output = (
        my_boiler_controller_l1.heat_pump_target_percentage_channel
    )

    # indexing of in- and outputs
    ww_use.global_index = 0
    my_boiler.temperature_mean_channel.global_index = 1
    my_heater.thermal_power_delivered_channel.global_index = 2
    my_boiler_controller_l1.heat_pump_target_percentage_channel.global_index = 3
    my_heater.fuel_delivered_channel.global_index = 4

    BoilerRig = namedtuple(  # pylint: disable=invalid-name
        "BoilerRig",
        ["boiler", "heater", "controller", "stsv", "warm_water_use"],
    )
    return BoilerRig(
        boiler=my_boiler,
        heater=my_heater,
        controller=my_boiler_controller_l1,
        stsv=stsv,
        warm_water_use=ww_use,
    )


@pytest.mark.base
def test_boiler_heat_loss_matches_draw_off_and_standing_losses(  # pylint: disable=redefined-outer-name
    boiler_setup,
) -> None:
    """With a 1 L draw-off the mean boiler temperature settles in [59.6, 59.7) °C.

    The temperature drop matches the heat losses from the 1 L hot-water draw-off
    plus the standing (U-value) heat loss of the 200 L storage over one 60 s
    timestep, starting from the storage's initial 60 °C.
    """
    boiler, heater, controller, stsv, _ = boiler_setup

    # 1 L warm-water consumption during this timestep.
    stsv.values[0] = 1

    boiler.i_restore_state()
    boiler.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    controller.i_restore_state()
    controller.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    heater.i_restore_state()
    heater.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    boiler.i_restore_state()
    boiler.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    assert 59.6 <= stsv.values[1] < 59.7


@pytest.mark.base
def test_heater_delivers_full_power_when_boiler_cold(  # pylint: disable=redefined-outer-name
    boiler_setup,
) -> None:
    """Forcing the storage temperature to 20 °C makes the heater deliver full power.

    With the boiler forced cold (well below the controller's lower threshold) the
    L1 controller requests maximum output and the heat source delivers
    ``thermal_power_in_watt * efficiency``.
    """
    boiler, heater, controller, stsv, _ = boiler_setup

    # No draw-off; force the storage temperature seen by the controller to 20 °C.
    stsv.values[0] = 0
    stsv.values[1] = 20

    controller.i_restore_state()
    controller.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    heater.i_restore_state()
    heater.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    boiler.i_restore_state()
    boiler.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    assert (
        stsv.values[2]
        == heater.config.thermal_power_in_watt * heater.config.efficiency
    )


@pytest.mark.base
def test_heater_off_when_boiler_hot(  # pylint: disable=redefined-outer-name
    boiler_setup,
) -> None:
    """Forcing the storage temperature to 100 °C makes the heater shut off.

    With the boiler forced hot (above the controller's upper threshold) the L1
    controller requests no heating and the heat source delivers 0 W.
    """
    boiler, heater, controller, stsv, _ = boiler_setup

    # No draw-off; force the storage temperature seen by the controller to 100 °C.
    stsv.values[0] = 0
    stsv.values[1] = 100

    controller.i_restore_state()
    controller.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    heater.i_restore_state()
    heater.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    boiler.i_restore_state()
    boiler.i_simulate(DEFAULT_TIMESTEP, stsv, False)

    assert stsv.values[2] == 0
