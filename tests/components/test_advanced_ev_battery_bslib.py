"""Tests for the advanced EV battery (bslib) config and state helpers.

Covers three pure functions of ``hisim.components.advanced_ev_battery_bslib``
that previously had no unit tests:

* ``CarBatteryConfig.preset_standard``
* ``CarBatteryConfig.get_main_classname``
* ``EVBatteryState.clone``

These tests need no simulation parameters, no bslib calls and no I/O. The tests at the end drive one step of the
battery with bslib and check its energy ports: it charges from the electricity meter, discharges vehicle electricity
into its car past every meter, and closes its balance with its inverter and cell losses and its stored change.
"""

from typing import Dict

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components.advanced_ev_battery_bslib import (
    CarBattery,
    CarBatteryConfig,
    EVBatteryState,
)
from hisim.config import ComponentID
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft


@pytest.mark.base
def test_preset_standard_returns_expected_defaults() -> None:
    """``preset_standard`` returns a deterministic config with the fleet's battery."""
    cfg = CarBatteryConfig.preset_standard("CarBattery")

    assert cfg.component_id.building is None
    assert cfg.component_id.name == "CarBattery"
    assert cfg.system_id == "SG1"
    assert cfg.p_inv_custom == 1e4
    assert cfg.e_bat_custom == 30
    assert cfg.source_weight == 1
    assert cfg.total_charged_energy_in_kilowatthour == 0
    assert cfg.total_discharged_energy_in_kilowatthour == 0


@pytest.mark.base
def test_an_assigned_component_id_overrides_building_and_name() -> None:
    """A ``component_id`` assigned after the preset overrides the identity, the rest stay."""
    cfg = CarBatteryConfig.preset_standard("CarBattery")
    cfg.component_id = ComponentID(name="Y", building="X")

    assert cfg.component_id.building == "X"
    assert cfg.component_id.name == "Y"
    # All other fields keep their defaults.
    assert cfg.system_id == "SG1"
    assert cfg.p_inv_custom == 1e4
    assert cfg.e_bat_custom == 30
    assert cfg.source_weight == 1
    assert cfg.total_charged_energy_in_kilowatthour == 0
    assert cfg.total_discharged_energy_in_kilowatthour == 0


@pytest.mark.base
def test_get_main_classname_matches_car_battery_full_classname() -> None:
    """``get_main_classname`` returns ``CarBattery.get_full_classname()``.

    The expected string is compared both literally and against the runtime value of
    ``CarBattery.get_full_classname()`` to avoid hardcoding mistakes.
    """
    expected = "hisim.components.advanced_ev_battery_bslib.CarBattery"

    assert CarBatteryConfig.get_main_classname() == expected
    assert CarBatteryConfig.get_main_classname() == CarBattery.get_full_classname()


@pytest.mark.base
def test_ev_battery_state_clone_copies_soc() -> None:
    """``clone`` returns a new ``EVBatteryState`` with the same ``soc``."""
    state = EVBatteryState(soc=0.5)
    cloned = state.clone()

    assert cloned.soc == 0.5
    assert cloned is not state


@pytest.mark.base
def test_ev_battery_state_clone_is_independent() -> None:
    """Mutating the original state does not affect the cloned state."""
    state = EVBatteryState(soc=0.5)
    cloned = state.clone()

    state.soc = 0.9

    assert cloned.soc == 0.5


@pytest.mark.base
def test_ev_battery_state_clone_edge_cases() -> None:
    """``clone`` preserves the boundary state-of-charge values 0.0 and 1.0."""
    assert EVBatteryState(soc=0.0).clone().soc == 0.0
    assert EVBatteryState(soc=1.0).clone().soc == 1.0


class OneStep:
    """One step of a standard car battery at 900 s, driven by a fake charge controller's command."""

    #: The step's duration, in s.
    SECONDS_PER_TIMESTEP: int = 900

    def __init__(self) -> None:
        """Build the battery and the fake command output, and index both for one step's values."""
        self.battery = CarBattery(
            my_simulation_parameters=SimulationParameters.one_day_only(2021, self.SECONDS_PER_TIMESTEP),
            config=CarBatteryConfig.preset_standard("CarBattery"),
        )
        self.command = cp.ComponentOutput(
            "FakeController",
            "ElectricityTargetToOrFromCarBattery",
            lt.LoadTypes.ELECTRICITY,
            lt.Units.WATT,
            component_id=ComponentID("FakeController"),
        )
        self.battery.p_set.source_output = self.command
        fft.add_global_index_of_components([self.battery, self.command])

    def run(self, command_in_watt: float) -> Dict[str, float]:
        """Simulate one step with the given command and return every output of the battery by name.

        Args:
            command_in_watt: The power the controller commands, in W; positive to charge, negative to discharge.

        Returns:
            The battery's outputs, in their own units.
        """
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([self.battery, self.command]))
        stsv.values[self.command.global_index] = command_in_watt
        self.battery.i_simulate(0, stsv, False)
        return {output.field_name: stsv.values[output.global_index] for output in self.battery.outputs}

    @classmethod
    def residual_in_watt(cls, values: Dict[str, float]) -> float:
        """The battery's balance over the step from its published ports, in W: in minus out, losses and stored change."""
        stored_in_watt = values[CarBattery.StoredEnergyIncreaseInWattHour] * 3600.0 / cls.SECONDS_PER_TIMESTEP
        return (
            values[CarBattery.AcBatteryChargingPower]
            - values[CarBattery.AcBatteryDischargingPower]
            - values[CarBattery.InverterLossInWatt]
            - values[CarBattery.CellLossInWatt]
            - stored_in_watt
        )


@pytest.mark.base
def test_the_car_battery_charges_from_the_meter_and_discharges_into_its_car_past_every_meter() -> None:
    """Charging is metered electricity, the discharge vehicle electricity, both losses losses, the stored change stored.

    Catches the discharge declared as electricity, which the electricity meter would read (the car's driving would
    count as the house's production and consumption), and a port of the wrong role or carrier.
    """
    ports = {output.field_name: output.energy_port for output in OneStep().battery.outputs if output.energy_port is not None}
    roles = {name: (port.role, port.carrier, port.is_metered) for name, port in ports.items()}
    electricity = lt.EnergyBalanceCarrier.ELECTRICITY
    assert roles == {
        CarBattery.AcBatteryChargingPower: (lt.EnergyRole.CONSUMPTION, electricity, True),
        CarBattery.AcBatteryDischargingPower: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.VEHICLE_ELECTRICITY, False),
        CarBattery.StoredEnergyIncreaseInWattHour: (lt.EnergyRole.STORED_CHANGE, electricity, False),
        CarBattery.InverterLossInWatt: (lt.EnergyRole.LOSS, electricity, False),
        CarBattery.CellLossInWatt: (lt.EnergyRole.LOSS, electricity, False),
    }


@pytest.mark.base
def test_a_charging_step_closes_with_the_inverter_and_the_cells_losing() -> None:
    """4 kW for 900 s: the inverter loses AC minus DC, the cells the rest of what is not stored, and the step closes.

    Catches a stored change taken in kWh or per hour instead of per step, and a loss with the wrong sign.
    """
    values = OneStep().run(4000.0)
    assert values[CarBattery.AcBatteryChargingPower] == 4000.0 and values[CarBattery.AcBatteryDischargingPower] == 0.0
    assert values[CarBattery.InverterLossInWatt] == pytest.approx(4000.0 - values[CarBattery.DcBatteryChargingPower], abs=1e-9)
    assert 0.0 < values[CarBattery.InverterLossInWatt] < 400.0 and 0.0 < values[CarBattery.CellLossInWatt] < 400.0
    assert values[CarBattery.StoredEnergyIncreaseInWattHour] == pytest.approx(
        (values[CarBattery.StateOfCharge] - CarBattery.INITIAL_STATE_OF_CHARGE) * 30000.0, rel=1e-12
    )
    assert OneStep.residual_in_watt(values) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.base
def test_a_discharging_step_loses_nothing_and_drains_the_store_by_what_the_car_takes() -> None:
    """2 kW to the car for 900 s: 500 Wh leave the store, no loss, and the step closes.

    Catches a discharge booked as a loss, or a stored change that does not follow the state of charge.
    """
    values = OneStep().run(-2000.0)
    assert values[CarBattery.AcBatteryDischargingPower] == 2000.0 and values[CarBattery.AcBatteryChargingPower] == 0.0
    assert values[CarBattery.StoredEnergyIncreaseInWattHour] == pytest.approx(-500.0, rel=1e-12)
    assert values[CarBattery.InverterLossInWatt] == 0.0
    assert values[CarBattery.CellLossInWatt] == pytest.approx(0.0, abs=1e-9)
    assert OneStep.residual_in_watt(values) == pytest.approx(0.0, abs=1e-9)
