"""Tests for the electric car's charge controller and its car battery in one step's iteration.

The controller decides its state-of-charge rule on the battery's state of charge at the start of the step: below
its set point it charges at full station power, at or above it with the surplus the energy manager offers the car
when that offer exceeds the lower charging threshold. These tests run the passes of a step the way the
simulator does (restore every component's state, then simulate each in turn, until no output moves) in both
evaluation orders, with the car parked at its station and the battery starting just below its set point.
"""

from typing import List, Tuple

import pytest
from utspclient.helpers.lpgdata import ChargingStationSets

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import advanced_ev_battery_bslib, controller_l1_generic_ev_charge
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


class ParkedCarStep:
    """One step of a car parked at its home station, with its battery and charge controller wired as in a house.

    The car's consumption, its location and the energy manager's offer are fixed inputs of the step. The battery
    starts the step at a given state of charge; the controller's set point is 0.4.
    """

    #: The controller's state-of-charge set point, between 0 and 1.
    SET_STATE_OF_CHARGE: float = 0.4
    #: The car's location code for "at its home station" (``L1Controller.charging_location`` for a home station).
    AT_HOME: float = 1.0
    #: The most passes a converging step may take before the simulator forces it (``Simulator``: after 11 tries).
    PASSES_BEFORE_FORCING: int = 12

    def __init__(
        self, state_of_charge_at_start: float, surplus_offered_in_watt: float, previous_state_of_charge_at_start: float
    ) -> None:
        """Build and wire the battery, the controller and the fixed inputs.

        The step values start as the previous step left them (the simulator's warm start), so a controller
        evaluated before the battery reads the previous step's start-of-step state of charge in its first pass.

        Args:
            state_of_charge_at_start: The battery's state of charge when the step starts, between 0 and 1.
            surplus_offered_in_watt: The surplus the energy manager offers the car, in W.
            previous_state_of_charge_at_start: The state of charge the previous step started with, between 0 and 1.
        """
        parameters = SimulationParameters.one_day_only(2021, 60)
        config = controller_l1_generic_ev_charge.ChargingStationConfig.for_charging_station_set(
            "L1EVChargeControl_1", ChargingStationSets.Charging_At_Home_with_11_kW
        )
        config.battery_set_soc = self.SET_STATE_OF_CHARGE
        self.controller = controller_l1_generic_ev_charge.L1Controller(
            my_simulation_parameters=parameters, config=config
        )
        self.battery = advanced_ev_battery_bslib.CarBattery(
            my_simulation_parameters=parameters,
            config=advanced_ev_battery_bslib.CarBatteryConfig.preset_standard("CarBattery_1"),
        )
        self.battery.state = advanced_ev_battery_bslib.EVBatteryState(soc=state_of_charge_at_start)

        def fake(name: str, load_type: lt.LoadTypes, unit: lt.Units) -> cp.ComponentOutput:
            return cp.ComponentOutput(name, name, load_type, unit, component_id=ComponentID(name))

        consumption = fake("CarConsumption", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        location = fake("CarLocation", lt.LoadTypes.ANY, lt.Units.ANY)
        offer = fake("SurplusOffered", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
        self.controller.car_consumption_channel.source_output = consumption
        self.controller.car_location_channel.source_output = location
        self.controller.electricity_target_from_ems_channel.source_output = offer
        self.controller.state_of_charge_at_start_of_step_channel.source_output = (
            self.battery.state_of_charge_at_start_of_step_channel
        )
        self.controller.ac_battery_power_channel.source_output = self.battery.p_bs
        self.battery.p_set.source_output = self.controller.electricity_to_or_from_car_battery_channel

        parts = [self.controller, self.battery, consumption, location, offer]
        fft.add_global_index_of_components(parts)
        self.stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs(parts))
        self.stsv.values[consumption.global_index] = 0.0
        self.stsv.values[location.global_index] = self.AT_HOME
        self.stsv.values[offer.global_index] = surplus_offered_in_watt
        self.stsv.values[self.battery.state_of_charge_at_start_of_step_channel.global_index] = (
            previous_state_of_charge_at_start
        )
        self.stsv.values[self.battery.soc.global_index] = state_of_charge_at_start

    def run(self, controller_first: bool) -> Tuple[List[float], int]:
        """Iterate the step's passes, as the simulator does, until no output moves by more than 1e-4.

        Args:
            controller_first: Whether the controller is evaluated before the battery (the kind order) or after it
                (the order the setup adds them in).

        Returns:
            The controller's command of every pass, in W, and the number of passes.

        Raises:
            AssertionError: If the step does not converge within the passes the simulator allows before forcing.
        """
        order = [self.controller, self.battery] if controller_first else [self.battery, self.controller]
        for component in order:
            component.i_save_state()
        commands_in_watt: List[float] = []
        previous = self.stsv.clone()
        for pass_number in range(1, self.PASSES_BEFORE_FORCING + 1):
            for component in order:
                component.i_restore_state()
                component.i_simulate(1200, self.stsv, False)
            commands_in_watt.append(
                self.stsv.values[self.controller.electricity_to_or_from_car_battery_channel.global_index]
            )
            if self.stsv.is_close_enough_to_previous(previous):
                return commands_in_watt, pass_number
            previous.copy_values_from_other(self.stsv)
        raise AssertionError(
            f"The step did not converge within {self.PASSES_BEFORE_FORCING} passes: {commands_in_watt}"
        )

    def end_state_of_charge(self) -> float:
        """Return the battery's state of charge at the end of the step, between 0 and 1."""
        return self.stsv.values[self.battery.soc.global_index]


@pytest.mark.base
@pytest.mark.parametrize("controller_first", [False, True])
def test_the_step_crossing_the_set_point_has_one_answer_in_every_pass(controller_first: bool) -> None:
    """Catches the controller deciding on the state of charge its own charging produces.

    The battery starts at 0.396, below the 0.4 set point, and one minute of charging lifts it above 0.4. Decided on
    the end-of-step value the controller alternated between charging and not charging in every pass, so the step
    never converged and the simulator froze whichever phase the 12th pass hit; the evaluation order set that phase.
    Decided on the start of the step it charges at the station's 11 kW in every pass, in both orders.
    """
    step = ParkedCarStep(
        state_of_charge_at_start=0.396, surplus_offered_in_watt=-2000.0, previous_state_of_charge_at_start=0.391
    )

    commands_in_watt, _ = step.run(controller_first=controller_first)

    assert set(commands_in_watt) == {11000.0}
    assert step.end_state_of_charge() > ParkedCarStep.SET_STATE_OF_CHARGE


@pytest.mark.base
@pytest.mark.parametrize("controller_first", [False, True])
def test_a_step_crossing_the_set_point_converges_without_forcing(controller_first: bool) -> None:
    """Catches the crossing step needing the simulator's forced convergence.

    The step must settle within the three passes a step needs when one component reads another's output of the
    previous pass, far below the 12 after which the simulator forces it.
    """
    step = ParkedCarStep(
        state_of_charge_at_start=0.396, surplus_offered_in_watt=-2000.0, previous_state_of_charge_at_start=0.391
    )

    _, passes = step.run(controller_first=controller_first)

    assert passes <= 3


@pytest.mark.base
@pytest.mark.parametrize("controller_first", [False, True])
def test_below_the_set_point_the_car_charges_at_station_power_whatever_the_surplus(controller_first: bool) -> None:
    """Catches a surplus offer that slows the charging of a car below its set point.

    A car at 0.3, below its 0.4 set point, offered 3,000 W of surplus, above the 1,100 W threshold, charges at the
    station's 11 kW in every pass. When the offer took precedence it charged at 3,000 W, so it might not reach its
    set point before it left, and its charging dropped from 11 kW to the offer as soon as the offer crossed the
    threshold.
    """
    step = ParkedCarStep(
        state_of_charge_at_start=0.3, surplus_offered_in_watt=3000.0, previous_state_of_charge_at_start=0.3
    )

    commands_in_watt, passes = step.run(controller_first=controller_first)

    assert set(commands_in_watt) == {11000.0}
    assert passes <= 3


@pytest.mark.base
@pytest.mark.parametrize("controller_first", [False, True])
def test_above_the_set_point_the_car_charges_with_the_surplus_it_is_offered(controller_first: bool) -> None:
    """Catches a car above its set point that does not charge with an offered surplus above the threshold.

    A car at 0.6, above its 0.4 set point, offered 3,000 W, above the 1,100 W threshold, charges at 3,000 W in every
    pass. The offer is a fixed input here, so the test pins the rule, not the energy manager's side of the loop; that
    the manager offers the car the surplus before its own charging is pinned in the energy manager's tests.
    """
    step = ParkedCarStep(
        state_of_charge_at_start=0.6, surplus_offered_in_watt=3000.0, previous_state_of_charge_at_start=0.6
    )

    commands_in_watt, passes = step.run(controller_first=controller_first)

    assert set(commands_in_watt) == {3000.0}
    assert passes <= 3


@pytest.mark.base
@pytest.mark.parametrize("controller_first", [False, True])
def test_above_the_set_point_and_without_surplus_the_car_does_not_charge(controller_first: bool) -> None:
    """Catches the set-point rule charging a car that starts the step at or above its set point.

    The offer of 500 W is below the 1,100 W threshold. The previous step started below the set point, so a
    controller evaluated before the battery reads that value in its first pass and commands a charge; the second
    pass reads this step's start and decides no charge, and two more passes carry that through the battery's report.
    """
    step = ParkedCarStep(
        state_of_charge_at_start=0.4, surplus_offered_in_watt=500.0, previous_state_of_charge_at_start=0.395
    )

    commands_in_watt, passes = step.run(controller_first=controller_first)

    assert commands_in_watt[-1] == 0.0
    assert step.end_state_of_charge() == pytest.approx(0.4)
    assert passes <= 4
