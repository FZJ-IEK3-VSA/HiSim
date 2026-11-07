"""The hplib heat pump books the heat of its source in three parts, so its cooling rejection lands on the sink.

``ThermalPowerInputFromEnvironment`` is signed: the heat delivered minus the electricity. The energy balance reads its
three non-negative parts instead (bead hisim-hplib-cooling-rejection-node-q4m7, owner decision Q-M of
``roadmap/complete_energy_sankeys_spec.md``): ``HeatFromAmbientInWatt``, the heat taken from the source while the
machine does not cool, to "ambient heat source"; ``HeatToAmbientInWatt``, the drawn heat plus the electricity it
rejects while cooling, to "ambient heat sink"; and ``ElectricityLostInWatt``, the electricity beyond the heat in a step
that heats at a calibrated coefficient of performance below 1, to "outdoors". Their difference is the signed output in
every step.
"""

import pytest

from hisim import loadtypes as lt
from hisim.components.more_advanced_heat_pump.heat_pump import MoreAdvancedHeatPumpHPLib, PositionHotWaterStorageInSystemSetup
from hisim.components.more_advanced_heat_pump.hplib_operation import HeatPumpOperation
from hisim.energy_port import EnergyPort
from tests.test_heat_pump_flow_booking import SingleStep
from tests.test_heat_pump_space_heating_part_load import HeatPumpStep

pytestmark = pytest.mark.base


class Steps:

    """Hand-built steps of the heat pump: heating, heating with the backup rod, heating at a COP below 1, active and passive cooling, off."""

    @staticmethod
    def operation(
        *,
        space_heating_in_watt: float,
        compressor_in_watt: float,
        cooling_in_watt: float = 0.0,
        brine_pump_in_watt: float = 0.0,
        heating_rod_in_watt: float = 0.0,
    ) -> HeatPumpOperation:
        """A step whose space-heating circuit carries ``space_heating_in_watt`` (negative while cooling)."""
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=space_heating_in_watt,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=compressor_in_watt,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=cooling_in_watt,
            electrical_power_brine_pump_in_watt=brine_pump_in_watt,
            electrical_power_heating_rod_in_watt=heating_rod_in_watt,
            cop=0.0,
            eer=0.0,
            outlet_temperature_space_heating_in_celsius=35.0,
            outlet_temperature_hot_water_in_celsius=45.0,
            mass_flow_space_heating_in_kg_per_second=0.3,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )


@pytest.mark.parametrize(
    ("operation", "from_ambient_in_watt", "to_ambient_in_watt", "lost_in_watt"),
    [
        (Steps.operation(space_heating_in_watt=8000.0, compressor_in_watt=2000.0), 6000.0, 0.0, 0.0),
        (Steps.operation(space_heating_in_watt=10000.0, compressor_in_watt=2000.0, heating_rod_in_watt=2000.0), 6000.0, 0.0, 0.0),
        (Steps.operation(space_heating_in_watt=3520.0, compressor_in_watt=4000.0), 0.0, 0.0, 480.0),
        (Steps.operation(space_heating_in_watt=-6000.0, compressor_in_watt=0.0, cooling_in_watt=2000.0), 0.0, 8000.0, 0.0),
        (Steps.operation(space_heating_in_watt=-3000.0, compressor_in_watt=0.0, brine_pump_in_watt=50.0), 0.0, 3050.0, 0.0),
        (Steps.operation(space_heating_in_watt=0.0, compressor_in_watt=0.0), 0.0, 0.0, 0.0),
    ],
    ids=["heating", "heating with the backup rod", "heating at a COP of 0.88", "active cooling", "passive cooling", "off"],
)
def test_the_three_parts_are_non_negative_and_their_difference_is_the_signed_heat(
    operation: HeatPumpOperation, from_ambient_in_watt: float, to_ambient_in_watt: float, lost_in_watt: float
) -> None:
    """Catches cooling rejection booked as negative intake, or a COP below 1 booked as heat rejected while cooling."""
    assert operation.heat_from_ambient_in_watt == pytest.approx(from_ambient_in_watt)
    assert operation.heat_to_ambient_in_watt == pytest.approx(to_ambient_in_watt)
    assert operation.electricity_lost_in_watt == pytest.approx(lost_in_watt)
    assert operation.heat_from_ambient_in_watt - operation.heat_to_ambient_in_watt - operation.electricity_lost_in_watt == pytest.approx(
        operation.thermal_power_from_environment_in_watt
    )
    assert operation.cools is (operation.thermal_power_space_heating_in_watt < 0.0)


def test_the_parts_carry_the_ports_and_the_signed_output_none() -> None:
    """Catches the signed output booked beside its parts, which would count the source twice."""
    heat_pump = SingleStep.heat_pump(PositionHotWaterStorageInSystemSetup.PARALLEL)
    ports = {output.field_name: output.energy_port for output in heat_pump.outputs}
    assert ports[MoreAdvancedHeatPumpHPLib.ThermalPowerFromEnvironment] is None
    assert ports[MoreAdvancedHeatPumpHPLib.HeatFromAmbientInWatt] == EnergyPort(lt.EnergyRole.CONSUMPTION, lt.EnergyBalanceCarrier.AMBIENT_HEAT)
    assert ports[MoreAdvancedHeatPumpHPLib.HeatToAmbientInWatt] == EnergyPort(lt.EnergyRole.PRODUCTION, lt.EnergyBalanceCarrier.AMBIENT_HEAT)
    assert ports[MoreAdvancedHeatPumpHPLib.ElectricityLostInWatt] == EnergyPort(lt.EnergyRole.LOSS, lt.EnergyBalanceCarrier.ELECTRICITY)
    intake, rejection = ports[MoreAdvancedHeatPumpHPLib.HeatFromAmbientInWatt], ports[MoreAdvancedHeatPumpHPLib.HeatToAmbientInWatt]
    assert intake is not None and intake.default_boundary() == lt.EnvironmentNode.AMBIENT_HEAT_SOURCE.value
    assert rejection is not None and rejection.default_boundary() == lt.EnvironmentNode.AMBIENT_HEAT_SINK.value


def test_a_heating_step_publishes_its_source_heat_on_the_intake() -> None:
    """Catches a published step whose parts do not add up to the signed output it publishes beside them."""
    outputs = HeatPumpStep.outputs()
    signed = outputs[MoreAdvancedHeatPumpHPLib.ThermalPowerFromEnvironment]
    assert signed > 0.0
    assert outputs[MoreAdvancedHeatPumpHPLib.HeatFromAmbientInWatt] == signed
    assert outputs[MoreAdvancedHeatPumpHPLib.HeatToAmbientInWatt] == 0.0
    assert outputs[MoreAdvancedHeatPumpHPLib.ElectricityLostInWatt] == 0.0
