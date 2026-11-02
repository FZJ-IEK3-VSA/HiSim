"""The building's thermal mass closes its energy balance in every step.

The 5R1C model of ISO 13790 (annex C) integrates the thermal mass node with the Crank-Nicolson scheme: from the
temperature the mass starts a step with, T_m,t-1, equation C.4 gives the temperature it ends the step with, T_m,t,
and the step average T_m = (T_m,t-1 + T_m,t) / 2 (C.9) is the temperature the surface and air equations of that step
use. Over one step the mass then gains exactly

    C_m * (T_m,t - T_m,t-1) = dt * (Phi_m + H_tr,em * (T_e - T_m) + H_tr,ms * (T_s - T_m)),

with Phi_m the internal and solar gains that reach the mass, T_e the outside and T_s the internal surface
temperature. The next step must start from T_m,t. A building that carried the average forward instead would move
its state by only half of that, so it would keep half its net heat per step and react as if it were about twice as
heavy. This module drives the ``Building`` through the synthetic day of the one-day snapshot
(``tests/test_building_one_day_snapshot.py``) and checks the balance in every step.
"""

from typing import List

import pytest

from hisim import component as cp
from hisim.caching import CacheSettings
from hisim.components.building import Building
from tests import functions_for_testing as fft
from tests.test_building_one_day_snapshot import OneDaySnapshot, SyntheticDayProfiles


class ThermalMassStep:
    """What one simulated step did to the thermal mass, and the heat that reached it.

    The record holds the state temperature before and after the step and the outputs the balance needs, so a
    failing step can be reported with every number that went into it.
    """

    def __init__(
        self,
        *,
        timestep: int,
        state_temperature_at_start_in_celsius: float,
        state_temperature_at_end_in_celsius: float,
        published_thermal_mass_temperature_in_celsius: float,
        net_heat_into_thermal_mass_in_joule: float,
        heat_stored_in_thermal_mass_in_joule: float,
    ) -> None:
        """Store one step's record.

        Args:
            timestep: The step's index.
            state_temperature_at_start_in_celsius: The state's thermal mass temperature before the step, in °C.
            state_temperature_at_end_in_celsius: The state's thermal mass temperature after the step, which the
                next step starts from, in °C.
            published_thermal_mass_temperature_in_celsius: The value of the output ``TemperatureMeanThermalMass``,
                in °C.
            net_heat_into_thermal_mass_in_joule: The heat that flowed into the mass over the step from the gains,
                the outside and the internal surface, in J.
            heat_stored_in_thermal_mass_in_joule: C_m times the state's temperature change over the step, in J.
        """
        self.timestep = timestep
        self.state_temperature_at_start_in_celsius = state_temperature_at_start_in_celsius
        self.state_temperature_at_end_in_celsius = state_temperature_at_end_in_celsius
        self.published_thermal_mass_temperature_in_celsius = published_thermal_mass_temperature_in_celsius
        self.net_heat_into_thermal_mass_in_joule = net_heat_into_thermal_mass_in_joule
        self.heat_stored_in_thermal_mass_in_joule = heat_stored_in_thermal_mass_in_joule

    def describe(self) -> str:
        """Return the step's numbers in one line, for a failure message."""
        return (
            f"step {self.timestep}: state {self.state_temperature_at_start_in_celsius:.6f} -> "
            f"{self.state_temperature_at_end_in_celsius:.6f} °C, "
            f"published {self.published_thermal_mass_temperature_in_celsius:.6f} °C, "
            f"stored {self.heat_stored_in_thermal_mass_in_joule:.1f} J, "
            f"net heat in {self.net_heat_into_thermal_mass_in_joule:.1f} J"
        )


class ThermalMassBalanceRun:
    """Drives the building through the synthetic day and records every step's thermal mass balance.

    The wiring and the inputs are the one-day snapshot's, so both variants it covers (the default single-family
    home, and a scaled one that heats, overheats and opens its windows) are checked here too. The per-step protocol
    mirrors the simulator: save the state, restore it, simulate.
    """

    #: Relative tolerance of the balance: the two sides are the same arithmetic, so only rounding separates them.
    RELATIVE_TOLERANCE: float = 1e-9
    #: Absolute tolerance of the balance in joules, for steps in which almost no heat moves.
    ABSOLUTE_TOLERANCE_IN_JOULE: float = 1e-3

    @classmethod
    def net_heat_into_thermal_mass_in_watt(cls, building: Building, stsv: cp.SingleTimeStepValues) -> float:
        """Return the heat flow into the thermal mass node over the step just simulated, in W.

        The flow is the gains that reach the mass node plus the conduction from the outside through the external
        part of the opaque elements and from the internal surface node, both against the step-average mass
        temperature the building publishes. For example, with no gains, a mass at 20 °C, the surface at 19 °C and
        the outside at 0 °C, the mass loses H_tr,ms * 1 K + H_tr,em * 20 K.

        Args:
            building: The building, after its ``i_simulate`` for the step.
            stsv: The value array the step wrote its outputs to.

        Returns:
            The net heat flow into the mass, in W.
        """
        mean_thermal_mass_temperature_in_celsius = stsv.values[building.thermal_mass_temperature_channel.global_index]
        internal_surface_temperature_in_celsius = stsv.values[
            building.internal_surface_temperature_channel.global_index
        ]
        heat_flow_from_gains_in_watt = stsv.values[building.heat_flow_rate_to_thermal_mass_node_channel.global_index]
        outside_temperature_in_celsius = stsv.get_input_value(building.temperature_outside_channel)
        return (
            heat_flow_from_gains_in_watt
            + building.external_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
            * (outside_temperature_in_celsius - mean_thermal_mass_temperature_in_celsius)
            + building.internal_part_of_transmission_heat_transfer_coeff_opaque_elements_in_watt_per_kelvin
            * (internal_surface_temperature_in_celsius - mean_thermal_mass_temperature_in_celsius)
        )

    @classmethod
    def run(cls, variant_name: str) -> List[ThermalMassStep]:
        """Simulate one snapshot variant through the synthetic day and record each step's balance.

        Args:
            variant_name: One of :meth:`OneDaySnapshot.variant_names`.

        Returns:
            One record per step, in order.
        """
        simulation_parameters = OneDaySnapshot.simulation_parameters()
        building = Building(
            config=OneDaySnapshot.config_for(variant_name), my_simulation_parameters=simulation_parameters
        )
        building.set_sim_repo(OneDaySnapshot.repository_with_synthetic_weather())
        source_outputs = OneDaySnapshot.wire_synthetic_inputs(building)
        input_vectors = SyntheticDayProfiles.input_vectors()
        fft.add_global_index_of_components([*source_outputs.values(), building])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*source_outputs.values(), building]))
        building.i_prepare_simulation()
        thermal_capacity_in_joule_per_kelvin = (
            building.my_building_information.thermal_capacity_of_building_thermal_mass_in_joule_per_kelvin
        )

        steps: List[ThermalMassStep] = []
        for timestep in range(SyntheticDayProfiles.TIMESTEPS):
            for channel_name, source_output in source_outputs.items():
                stsv.values[source_output.global_index] = input_vectors[channel_name][timestep]
            building.i_save_state()
            building.i_restore_state()
            state_temperature_at_start_in_celsius = building.state.thermal_mass_temperature_in_celsius
            building.i_simulate(timestep, stsv, False)
            state_temperature_at_end_in_celsius = building.state.thermal_mass_temperature_in_celsius
            steps.append(
                ThermalMassStep(
                    timestep=timestep,
                    state_temperature_at_start_in_celsius=state_temperature_at_start_in_celsius,
                    state_temperature_at_end_in_celsius=state_temperature_at_end_in_celsius,
                    published_thermal_mass_temperature_in_celsius=stsv.values[
                        building.thermal_mass_temperature_channel.global_index
                    ],
                    net_heat_into_thermal_mass_in_joule=cls.net_heat_into_thermal_mass_in_watt(building, stsv)
                    * simulation_parameters.seconds_per_timestep,
                    heat_stored_in_thermal_mass_in_joule=thermal_capacity_in_joule_per_kelvin
                    * (state_temperature_at_end_in_celsius - state_temperature_at_start_in_celsius),
                )
            )
        return steps


@pytest.fixture(name="redirected_solar_gain_cache")
def fixture_redirected_solar_gain_cache(tmp_path, monkeypatch) -> None:
    """Send the building's solar-gain cache into a temporary directory, so the test writes nothing into the tree."""
    monkeypatch.setenv(CacheSettings.Variables.DIRECTORY, str(tmp_path))


@pytest.mark.base
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_thermal_mass_stores_the_net_heat_it_receives_in_every_step(
    variant_name: str, redirected_solar_gain_cache
) -> None:
    """Verify C_m times the state's temperature change equals the net heat into the mass, step by step.

    A building that carries the step-average temperature into the next step moves its state by half the change
    C.4 computes, so the stored heat is half the net heat in every step that is not in equilibrium; this test
    then fails on almost every step of the synthetic day.
    """
    del redirected_solar_gain_cache
    steps = ThermalMassBalanceRun.run(variant_name)
    unbalanced = [
        step
        for step in steps
        if step.heat_stored_in_thermal_mass_in_joule
        != pytest.approx(
            step.net_heat_into_thermal_mass_in_joule,
            rel=ThermalMassBalanceRun.RELATIVE_TOLERANCE,
            abs=ThermalMassBalanceRun.ABSOLUTE_TOLERANCE_IN_JOULE,
        )
    ]
    assert not unbalanced, (
        f"The thermal mass of the '{variant_name}' variant does not store the net heat it receives in "
        f"{len(unbalanced)} of {len(steps)} steps:\n" + "\n".join(step.describe() for step in unbalanced[:5])
    )


@pytest.mark.base
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_published_thermal_mass_temperature_is_the_step_average(variant_name: str, redirected_solar_gain_cache) -> None:
    """Verify ``TemperatureMeanThermalMass`` is the mean of the temperatures the mass starts and ends the step with.

    The output is the step-average mass temperature (C.9), the value the surface and air temperatures of the same
    step are computed from, while the state carries the end temperature into the next step. A heated step from
    20.0 °C to 20.2 °C publishes 20.1 °C.
    """
    del redirected_solar_gain_cache
    steps = ThermalMassBalanceRun.run(variant_name)
    for step in steps:
        assert step.published_thermal_mass_temperature_in_celsius == pytest.approx(
            (step.state_temperature_at_start_in_celsius + step.state_temperature_at_end_in_celsius) / 2, abs=1e-12
        ), step.describe()
