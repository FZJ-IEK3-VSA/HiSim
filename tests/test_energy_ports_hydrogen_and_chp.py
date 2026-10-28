"""The hydrogen components, both CHPs and the transformer declare their energy ports, and one step of each books them.

Complete Sankey spec, PR 5c: the electrolyzers, the hydrogen tank, the fuel cell, the two CHPs and the transformer and
rectifier state the energy they take, give, lose and store as energy ports. Hydrogen and the CHPs' fuel count by the
fuel's lower heating value. Most of these components are run by no setup, so each is driven here for one step with
fake inputs, and its published ports are summed: consumption minus production, losses and stored change must be
zero, as the energy balance requires in every step of a run. ``tests/test_energy_balance_hydrogen.py`` runs the two
setups that use some of them.

The advanced electrolyzer does not close: its supplier's hydrogen rates give more hydrogen than the electricity it
uses (bead hisim-advanced-electrolyzer-energy-ws1d), so it is checked here for its ports only. The tank closes in the
steps tested here; two branches that drop hydrogen without releasing it are bead hisim-hydrogen-tank-drops-hn7k.
"""

from typing import Callable, Dict, Tuple

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.component import Component
from hisim.components.advanced_fuel_cell import CHP, CHPConfig
from hisim.components.configuration import PhysicsConfig
from hisim.components.generic_chp.chp import CHPConfig as SimpleCHPConfig
from hisim.components.generic_chp.chp import SimpleCHP
from hisim.components.generic_electrolyzer_and_h2_storage import (
    AdvancedElectrolyzer,
    ElectrolyzerWithHydrogenStorageConfig,
    ElectrolyzerWithStorageConfig,
    HydrogenStorage,
)
from hisim.components.generic_electrolyzer_h2 import Electrolyzer, ElectrolyzerConfig
from hisim.components.generic_fuel_cell import FuelCell, FuelCellConfig
from hisim.components.transformer_rectifier import Transformer, TransformerConfig
from hisim.config import ComponentID, SizingContext
from hisim.energy_port import EnergyPort
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

pytestmark = pytest.mark.base


class OneStep:
    """One step of a component whose every input reads a fake source, and the energy ports it publishes."""

    #: The step's duration, in s.
    SECONDS_PER_TIMESTEP: int = 900
    #: Joules per kilowatt hour, to turn a step's kWh into a mean power.
    JOULES_PER_KILOWATT_HOUR: float = 3.6e6

    def __init__(self, component: Component) -> None:
        """Give every input of ``component`` a fake source and index all outputs for one step's values.

        Args:
            component: A component built with :meth:`parameters`.
        """
        self.component = component
        self.sources: Dict[str, cp.ComponentOutput] = {}
        for component_input in component.inputs:
            source = cp.ComponentOutput(
                "FakeSource",
                component_input.field_name,
                component_input.loadtype,
                component_input.unit,
                component_id=ComponentID("FakeSource"),
            )
            component_input.source_output = source
            self.sources[component_input.field_name] = source
        fft.add_global_index_of_components([component, *self.sources.values()])

    @classmethod
    def parameters(cls) -> SimulationParameters:
        """One day at :attr:`SECONDS_PER_TIMESTEP`, the parameters every component here is built with."""
        return SimulationParameters.one_day_only(2021, cls.SECONDS_PER_TIMESTEP)

    def run(self, inputs: Dict[str, float], timestep: int = 0) -> Dict[str, float]:
        """Simulate one step with the given input values and return every output of the component by name.

        Args:
            inputs: The value of each input by its name, in the input's unit; an input not named reads 0.
            timestep: The step's index.

        Returns:
            The component's outputs, in their own units.
        """
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([self.component, *self.sources.values()]))
        for name, value in inputs.items():
            stsv.values[self.sources[name].global_index] = value
        self.component.i_simulate(timestep, stsv, False)
        return {output.field_name: stsv.values[output.global_index] for output in self.component.outputs}

    def ports(self) -> Dict[str, Tuple[lt.EnergyRole, lt.EnergyBalanceCarrier]]:
        """The component's energy ports as ``output -> (role, carrier)``."""
        return {output.field_name: (output.energy_port.role, output.energy_port.carrier) for output in self.component.outputs if output.energy_port is not None}

    def residual_in_watt(self, values: Dict[str, float]) -> float:
        """The component's balance over the step from its published ports, as a mean power in W.

        Consumption minus production, losses and stored change, each port converted from its unit as the energy
        balance converts it (:meth:`hisim.energy_port.EnergyPort.kilowatt_hours_per_step`).
        """
        sign_of_role = {
            lt.EnergyRole.CONSUMPTION: 1.0,
            lt.EnergyRole.PRODUCTION: -1.0,
            lt.EnergyRole.LOSS: -1.0,
            lt.EnergyRole.STORED_CHANGE: -1.0,
        }
        residual_in_kilowatt_hour = sum(
            sign_of_role[output.energy_port.role] * values[output.field_name] * EnergyPort.kilowatt_hours_per_step(output.unit, self.SECONDS_PER_TIMESTEP)
            for output in self.component.outputs
            if output.energy_port is not None
        )
        return residual_in_kilowatt_hour * self.JOULES_PER_KILOWATT_HOUR / self.SECONDS_PER_TIMESTEP


def hydrogen_lower_heating_value_in_joule_per_kg() -> float:
    """The lower heating value of hydrogen every hydrogen port counts by, in J/kg."""
    return PhysicsConfig.get_properties_for_energy_carrier(lt.LoadTypes.GREEN_HYDROGEN).lower_heating_value_in_joule_per_kg


def test_the_transformer_receives_delivers_and_loses_electricity() -> None:
    """1000 kW received at 95 % are 950 kW delivered and 50 kW lost, and the three close.

    Catches the received electricity booked as production, a loss with the wrong sign, and a delivered power that
    the new outputs changed.
    """
    step = OneStep(Transformer(OneStep.parameters(), TransformerConfig.preset_standard("Transformer")))
    assert step.ports() == {
        Transformer.TransformerOutput: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        Transformer.ElectricityReceivedInKilowatt: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        Transformer.ConversionLossInKilowatt: (lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
    }
    values = step.run({Transformer.TransformerInput: 1000.0})
    assert values[Transformer.TransformerOutput] == pytest.approx(950.0, rel=1e-12)
    assert values[Transformer.ElectricityReceivedInKilowatt] == 1000.0
    assert values[Transformer.ConversionLossInKilowatt] == pytest.approx(50.0, rel=1e-12)
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-9)


def test_the_electrolyzer_turns_electricity_into_hydrogen_and_loses_the_rest() -> None:
    """A running step books its load as electricity in, its hydrogen by its heating value out, and the rest as loss.

    The hydrogen rate is computed in kg/h, so the hydrogen power is the rate divided by 3600 times the heating
    value. Catches the rate read as kg/s (3600 times too much hydrogen, a negative loss), and a loss that does not
    close the step.
    """
    config = ElectrolyzerConfig.for_device("Electrolyzer", electrolyzer_name="HTecME450")
    step = OneStep(Electrolyzer(OneStep.parameters(), config))
    assert step.ports() == {
        Electrolyzer.CurrentLoad: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        Electrolyzer.HydrogenProductionInWatt: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.HYDROGEN),
        Electrolyzer.ConversionLossInWatt: (lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
    }
    values = step.run({Electrolyzer.LoadInput: config.nom_load, Electrolyzer.InputState: 1.0})
    hydrogen_in_watt = values[Electrolyzer.CurrentHydrogenFlowRate] / 3600.0 * hydrogen_lower_heating_value_in_joule_per_kg()
    assert values[Electrolyzer.HydrogenProductionInWatt] == pytest.approx(hydrogen_in_watt, rel=1e-12)
    assert 0.4 < values[Electrolyzer.HydrogenProductionInWatt] / values[Electrolyzer.CurrentLoad] < 0.8
    assert values[Electrolyzer.ConversionLossInWatt] > 0.0
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-6)


def test_the_fuel_cell_turns_hydrogen_into_electricity_and_loses_the_rest() -> None:
    """A running step books its hydrogen by its heating value in, its power out, and the difference as loss.

    The cell is the 10 kW Nedstack stack of ``tests/test_generic_fuel_cell.py``: the preset's 100 kW cell cannot
    simulate a step (bead hisim-fuel-cell-hydrogen-flow-q2zc). Catches the hydrogen rate in kg/h read as kg/s and a
    loss that does not close the step.
    """
    config = FuelCellConfig(
        component_id=ComponentID(name="FuelCell"),
        nom_output_in_kilowatt=10.3,
        max_output_in_kilowatt=8.81,
        min_output_in_kilowatt=0.0,
        nom_h2_flow_rate_in_m3_per_h=0.647,
        faraday_eff=0.98,
        i_cell_nom_in_ampere_per_cm2=0.7,
    )
    step = OneStep(FuelCell(OneStep.parameters(), config))
    assert step.ports() == {
        FuelCell.PowerOutput: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        FuelCell.HydrogenConsumptionInWatt: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.HYDROGEN),
        FuelCell.ConversionLossInWatt: (lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.HYDROGEN),
    }
    values = step.run({FuelCell.DemandProfile: 7.6, FuelCell.ControlSignal: 1.0})
    hydrogen_in_watt = values[FuelCell.CurrentHydrogenFlowRate] / 3600.0 * hydrogen_lower_heating_value_in_joule_per_kg()
    assert values[FuelCell.PowerOutput] == pytest.approx(7600.0, rel=1e-12)
    assert values[FuelCell.HydrogenConsumptionInWatt] == pytest.approx(hydrogen_in_watt, rel=1e-12)
    assert values[FuelCell.ConversionLossInWatt] > 0.0
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(("gas_type", "fuel_carrier"), [("Hydrogen", lt.EnergyBalanceCarrier.HYDROGEN), ("Methan", lt.EnergyBalanceCarrier.NATURAL_GAS)])
def test_the_modulating_chp_burns_its_fuel_into_electricity_heat_and_loss(gas_type: str, fuel_carrier: lt.EnergyBalanceCarrier) -> None:
    """At full load the hydrogen preset burns 7,000 W for 1,200 W of electricity and 2,200 W of heat, losing 3,600 W.

    The fuel port's carrier follows the configured gas. Catches a fuel port of the wrong carrier, a fuel power that
    is not the drawn mass flow times the fuel's heating value, and a loss that does not close the step.
    """
    config = CHPConfig.preset_hydrogen("CHP")
    config.gas_type = gas_type
    config.operating_mode = "heat"
    step = OneStep(CHP(OneStep.parameters(), config))
    assert step.ports() == {
        CHP.ElectricityOutput: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        CHP.ThermalOutputPower: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        CHP.FuelConsumptionInWatt: (lt.EnergyRole.CONSUMPTION, fuel_carrier),
        CHP.ConversionLossInWatt: (lt.EnergyRole.LOSS, fuel_carrier),
    }
    values = step.run({CHP.ControlSignal: 1.0, CHP.MassflowInputTemperature: 40.0}, timestep=100)
    assert values[CHP.ElectricityOutput] == pytest.approx(1200.0, rel=1e-12)
    assert values[CHP.ThermalOutputPower] == pytest.approx(2200.0, rel=1e-9)
    assert values[CHP.FuelConsumptionInWatt] == pytest.approx(7000.0, rel=1e-9)
    assert values[CHP.ConversionLossInWatt] == pytest.approx(3600.0, rel=1e-9)
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    ("preset", "fuel_carrier"),
    [(SimpleCHPConfig.preset_gas, lt.EnergyBalanceCarrier.NATURAL_GAS), (SimpleCHPConfig.preset_hydrogen, lt.EnergyBalanceCarrier.HYDROGEN)],
)
@pytest.mark.parametrize("heating_mode", [0.0, 1.0])
def test_the_simple_chp_burns_its_fuel_into_electricity_heat_and_loss(
    preset: Callable[[str], SimpleCHPConfig], fuel_carrier: lt.EnergyBalanceCarrier, heating_mode: float
) -> None:
    """While it runs, the fuel power of its configuration goes to electricity, one of the two heats and the loss.

    For example, the gas preset at 500 W of heat burns 1,000 W for 330 W of electricity and loses 170 W; switched
    off it books nothing. Catches the fuel port in the units of FuelDelivered (kg/s or Wh), a heat port of the wrong
    carrier and a loss that does not close the step.
    """
    config = preset("CHP").resolve(SizingContext())
    step = OneStep(SimpleCHP(OneStep.parameters(), config))
    assert step.ports() == {
        SimpleCHP.ThermalPowerOutputBuilding: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT),
        SimpleCHP.ThermalPowerOutputDHW: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT),
        SimpleCHP.ElectricityOutput: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        SimpleCHP.FuelConsumptionInWatt: (lt.EnergyRole.CONSUMPTION, fuel_carrier),
        SimpleCHP.ConversionLossInWatt: (lt.EnergyRole.LOSS, fuel_carrier),
    }
    values = step.run({SimpleCHP.CHPControllerOnOffSignal: 1.0, SimpleCHP.CHPControllerHeatingModeSignal: heating_mode})
    assert values[SimpleCHP.FuelConsumptionInWatt] == pytest.approx(config.p_fuel, rel=1e-12)
    assert values[SimpleCHP.ConversionLossInWatt] > 0.0
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-9)
    if fuel_carrier == lt.EnergyBalanceCarrier.NATURAL_GAS:
        assert values[SimpleCHP.ConversionLossInWatt] == pytest.approx(170.0, rel=1e-12)
    switched_off = step.run({SimpleCHP.CHPControllerOnOffSignal: 0.0, SimpleCHP.CHPControllerHeatingModeSignal: heating_mode})
    assert all(switched_off[name] == 0.0 for name in step.ports())


def test_the_advanced_electrolyzer_declares_its_electricity_hydrogen_and_waste_heat() -> None:
    """The electricity it uses, the hydrogen it produces by its heating value and its stated waste heat are its ports.

    The three do not close (bead hisim-advanced-electrolyzer-energy-ws1d), so only the ports and the hydrogen
    power's conversion are checked. Catches a hydrogen port in kg/s and the electricity offered booked instead of
    the electricity used.
    """
    step = OneStep(AdvancedElectrolyzer(OneStep.parameters(), ElectrolyzerWithStorageConfig.preset_standard("Electrolyzer")))
    assert step.ports() == {
        AdvancedElectrolyzer.ElectricityRealNeeded: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        AdvancedElectrolyzer.EnergyLosses: (lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
        AdvancedElectrolyzer.HydrogenProductionInWatt: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.HYDROGEN),
    }
    values = step.run({AdvancedElectrolyzer.ElectricityInput: 3000.0})
    assert values[AdvancedElectrolyzer.ElectricityRealNeeded] == 2400.0
    assert values[AdvancedElectrolyzer.HydrogenProductionInWatt] == pytest.approx(
        values[AdvancedElectrolyzer.HydrogenOutput] * hydrogen_lower_heating_value_in_joule_per_kg(), rel=1e-12
    )


class TankSteps:
    """Steps of the 500 kg preset tank, half full, with a daily loss so that its loss port carries energy."""

    #: The tank's daily loss share, in percent: enough to make its loss port carry energy.
    LOSS_FACTOR_PER_DAY_IN_PERCENT: float = 1.0

    @classmethod
    def build(cls) -> OneStep:
        """The tank, ready for one step at a time."""
        config = ElectrolyzerWithHydrogenStorageConfig.preset_standard("HydrogenStorage")
        config.loss_factor_per_day = cls.LOSS_FACTOR_PER_DAY_IN_PERCENT
        config.energy_for_charge = 1.0
        return OneStep(HydrogenStorage(OneStep.parameters(), config))

    @staticmethod
    def kilograms_per_second(kilograms_per_hour: float) -> float:
        """A hydrogen rate in kg/s from kg/h."""
        return kilograms_per_hour / 3600.0


def test_the_hydrogen_tank_declares_hydrogen_in_out_lost_and_stored_and_its_compression() -> None:
    """The tank's ports: hydrogen stored, released, lost and the change of its fill; compression electricity in and lost.

    Catches a storage change taken from the step's own flows instead of the fill it carries into the next step,
    and a hydrogen port in kg instead of W.
    """
    assert TankSteps.build().ports() == {
        HydrogenStorage.HydrogenStorageEnergyDemand: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.ELECTRICITY),
        HydrogenStorage.CompressionLossInWatt: (lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY),
        HydrogenStorage.HydrogenReceivedInWatt: (lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.HYDROGEN),
        HydrogenStorage.HydrogenReleasedInWatt: (lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.HYDROGEN),
        HydrogenStorage.HydrogenLossInWatt: (lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.HYDROGEN),
        HydrogenStorage.StoredEnergyIncreaseInWattHour: (lt.EnergyRole.STORED_CHANGE, lt.EnergyBalanceCarrier.HYDROGEN),
    }


@pytest.mark.parametrize(("charging_in_kg_per_h", "discharging_in_kg_per_h"), [(1.0, 0.0), (0.0, 1.5), (1.5, 0.5), (0.0, 0.0)])
def test_a_step_of_the_hydrogen_tank_closes(charging_in_kg_per_h: float, discharging_in_kg_per_h: float) -> None:
    """Charging, discharging, both netted, and idle: stored, released and lost hydrogen add up to the fill's change.

    Catches the stored change or a flow converted with a different heating value or step length than the others,
    and a loss that leaves the fill without being booked.
    """
    step = TankSteps.build()
    values = step.run(
        {
            HydrogenStorage.ChargingHydrogenAmount: TankSteps.kilograms_per_second(charging_in_kg_per_h),
            HydrogenStorage.DischargingHydrogenAmountTarget: TankSteps.kilograms_per_second(discharging_in_kg_per_h),
        }
    )
    assert values[HydrogenStorage.HydrogenLossInWatt] > 0.0
    assert step.residual_in_watt(values) == pytest.approx(0.0, abs=1e-6)
    netted_charging_in_kg_per_h = charging_in_kg_per_h - discharging_in_kg_per_h
    assert (values[HydrogenStorage.HydrogenReceivedInWatt] > 0.0) == (netted_charging_in_kg_per_h > 0.0)
    assert (values[HydrogenStorage.HydrogenReleasedInWatt] > 0.0) == (netted_charging_in_kg_per_h < 0.0)
    assert values[HydrogenStorage.CompressionLossInWatt] == values[HydrogenStorage.HydrogenStorageEnergyDemand]
