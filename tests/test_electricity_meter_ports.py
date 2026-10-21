"""The electricity meter reads every electricity port by its role and bills the grid exchange itself.

Bead hisim-component-physics-4g9.39. Owner decision of 2026-10-10: a meter reads physics ports only, and nothing
reads a meter. The meter's default connections are therefore found by port role, not by class: every output that
declares an electricity energy port, an ``IN`` port as consumption and an ``OUT`` port as production. Until this
change, a household with an energy manager billed the manager's signed balance as production, so a consumer the
manager does not read (the heat pump's cooling and its brine pump) was never billed, and the meter's consumption was
always zero.

The tests build a heat pump, a battery and a meter on one simulator, connect the meter automatically as every
household setup does, and drive one step of the meter by hand with chosen port values, so no weather, building or
occupancy is needed.
"""

from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import simulator as sim
from hisim.components import advanced_battery_bslib, controller_l2_energy_management_system, electricity_meter
from hisim.components import more_advanced_heat_pump_hplib
from hisim.config import SizingContext
from hisim.simulationparameters import SimulationParameters


class MeteredHouse:
    """A heat pump, a battery and an electricity meter connected automatically, ready to be driven one step."""

    #: The heat pump's electricity ports, all four read as consumption.
    HEAT_PUMP_PORTS: Tuple[str, ...] = (
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.ElectricalInputPowerForCooling,
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.ElectricalInputPowerBrinePump,
        more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
    )

    def __init__(self, directory: Path, with_domestic_hot_water_preparation: bool = True) -> None:
        """Build the three components on one simulator and connect the meter automatically.

        Args:
            directory: A directory of the test's own, for the simulator's module and results.
            with_domestic_hot_water_preparation: Whether the heat pump prepares hot water, and so publishes its DHW
                electricity.
        """
        parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15)
        parameters.result_directory = str(directory)
        self.simulator = sim.Simulator(
            module_directory=str(directory),
            module_filename="household_for_test_meter_ports",
            my_simulation_parameters=parameters,
        )
        self.simulator.set_simulation_parameters(parameters)
        heat_pump_config = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLibConfig.preset_air_water("HeatPump")
        heat_pump_config.set_thermal_output_power_in_watt = 8000.0
        heat_pump_config.heating_reference_temperature_in_celsius = -7.0
        heat_pump_config.with_domestic_hot_water_preparation = with_domestic_hot_water_preparation
        self.heat_pump = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib(
            my_simulation_parameters=parameters, config=heat_pump_config
        )
        battery_config = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv("Battery").resolve(
            SizingContext(pv_peak_power_in_watt=(5000.0,))
        )
        self.battery = advanced_battery_bslib.Battery(my_simulation_parameters=parameters, config=battery_config)
        self.meter = electricity_meter.ElectricityMeter(
            my_simulation_parameters=parameters,
            config=electricity_meter.ElectricityMeterConfig.preset_standard("ElectricityMeter"),
        )
        self.simulator.add_component(self.heat_pump)
        self.simulator.add_component(self.battery)
        self.simulator.add_component(self.meter, connect_automatically=True)

    def wire(self) -> None:
        """Connect the meter's inputs as the run does, leaving the other components' own inputs alone."""
        self.simulator.prepare_calculation()
        for wrapper in self.simulator.wrapped_components:
            if wrapper.my_component is self.meter:
                wrapper.connect_inputs(self.simulator.all_outputs)

    def reads(self) -> List[Tuple[str, str, List[lt.InandOutputType]]]:
        """What the meter reads: source, output and the flow tags of each input, in the order it created them."""
        sources = {item.field_name: (item.src_object_name, item.src_field_name) for item in self.meter.inputs}
        return [
            (
                *sources[entry.source_component_class],
                [tag for tag in entry.source_tags if isinstance(tag, lt.InandOutputType)],
            )
            for entry in self.meter.my_component_inputs
        ]

    def step(self, values: Dict[Tuple[str, str], float]) -> Dict[str, float]:
        """Drive one step of the meter with the given port values and return what it publishes, in W.

        Args:
            values: The value of each output the step sets, by (component, output); every other output is zero.

        Returns:
            The meter's power outputs by name.
        """
        stsv = cp.SingleTimeStepValues(len(self.simulator.all_outputs))
        by_name = {(output.component_name, output.field_name): output for output in self.simulator.all_outputs}
        for key, value in values.items():
            stsv.set_output_value(by_name[key], value)
        self.meter.i_simulate(0, stsv, False)
        names = (
            electricity_meter.ElectricityMeter.ElectricityFromGridInWatt,
            electricity_meter.ElectricityMeter.ElectricityToGridInWatt,
            electricity_meter.ElectricityMeter.ElectricityConsumptionInWatt,
            electricity_meter.ElectricityMeter.ElectricityProductionInWatt,
        )
        return {name: stsv.values[by_name[(self.meter.component_name, name)].global_index] for name in names}


CONSUMPTION = lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED
PRODUCTION = lt.InandOutputType.ELECTRICITY_PRODUCTION


@pytest.mark.base
def test_a_consumer_the_energy_manager_does_not_read_is_billed(tmp_path: Path) -> None:
    """The heat pump's cooling and brine pump, which no energy manager reads, are billed as consumption.

    Catches the meter reading only the outputs its class lists by name (the heat pump's space heating and hot water),
    which left the cooling and brine-pump electricity out of every bill.
    """
    house = MeteredHouse(tmp_path)
    house.wire()
    heat_pump_reads = [(output, tags) for source, output, tags in house.reads() if source == "HeatPump"]
    assert sorted(heat_pump_reads) == sorted((output, [CONSUMPTION]) for output in MeteredHouse.HEAT_PUMP_PORTS)
    published = house.step(
        {
            ("HeatPump", "ElectricalInputPowerSH"): 2000.0,
            ("HeatPump", "ElectricalInputPowerForCooling"): 700.0,
            ("HeatPump", "ElectricalInputPowerBrinePump"): 100.0,
            ("HeatPump", "ElectricalInputPowerDHW"): 200.0,
        }
    )
    assert published["ElectricityConsumptionInWatt"] == pytest.approx(3000.0)
    assert published["ElectricityFromGridInWatt"] == pytest.approx(3000.0)
    assert published["ElectricityToGridInWatt"] == 0.0


@pytest.mark.base
def test_the_battery_is_read_by_role_and_its_discharging_with_its_sign_turned(tmp_path: Path) -> None:
    """Charging is consumption, discharging (published <= 0) production; losses and the stored change are not read.

    Catches the discharging power summed with its published negative sign, which would bill a discharging battery as
    a consumer, and a battery loss or stored change counted at the grid boundary.
    """
    house = MeteredHouse(tmp_path)
    house.wire()
    battery_reads = sorted((output, tags) for source, output, tags in house.reads() if source == "Battery")
    assert battery_reads == [("ChargingPower", [CONSUMPTION]), ("DischargingPower", [PRODUCTION])]
    published = house.step(
        {("HeatPump", "ElectricalInputPowerSH"): 1500.0, ("Battery", "DischargingPower"): -2000.0}
    )
    assert published["ElectricityProductionInWatt"] == pytest.approx(2000.0)
    assert published["ElectricityConsumptionInWatt"] == pytest.approx(1500.0)
    assert published["ElectricityToGridInWatt"] == pytest.approx(500.0)
    assert published["ElectricityFromGridInWatt"] == 0.0


@pytest.mark.base
def test_a_heat_pump_without_hot_water_gives_the_meter_no_hot_water_feed(tmp_path: Path) -> None:
    """A port the device does not publish is no feed: the meter reads only the outputs that exist."""
    house = MeteredHouse(tmp_path, with_domestic_hot_water_preparation=False)
    house.wire()
    heat_pump_reads = sorted(output for source, output, _ in house.reads() if source == "HeatPump")
    assert heat_pump_reads == sorted(MeteredHouse.HEAT_PUMP_PORTS[:3])


@pytest.mark.base
def test_the_meter_reads_nothing_of_the_energy_manager(tmp_path: Path) -> None:
    """The energy manager's balance is a controller's sum, not a port: the meter declares no feed from it."""
    house = MeteredHouse(tmp_path)
    manager = controller_l2_energy_management_system.L2GenericEnergyManagementSystem(
        my_simulation_parameters=SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60 * 15),
        config=controller_l2_energy_management_system.EMSConfig.preset_optimize_own_consumption("EMS"),
    )
    assert not house.meter.default_connections_for(manager)
    assert not house.meter.default_connections_for(house.meter)


@pytest.mark.base
def test_a_port_fed_through_the_channel_of_the_other_role_is_refused(tmp_path: Path) -> None:
    """A discharging port written by hand as consumption fails at the first step, naming the port and its role."""
    house = MeteredHouse(tmp_path)
    house.meter.add_component_input_and_connect(
        source_object_name="Battery",
        source_component_output="DischargingPower",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[CONSUMPTION],
        source_weight=999,
    )
    house.simulator.wrapped_components[-1].connect_automatically = False
    house.wire()
    with pytest.raises(ValueError, match="Battery.DischargingPower, an electricity out port"):
        house.step({})
