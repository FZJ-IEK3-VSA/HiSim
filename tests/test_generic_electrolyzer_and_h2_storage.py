"""Tests for generic electrolyzer and hydrogen storage components.

This module contains pytest tests for the AdvancedElectrolyzer and
HydrogenStorage components from
hisim.components.generic_electrolyzer_and_h2_storage. It verifies
hydrogen production, water consumption, unused power calculations, and
storage charging behavior under a fixed-input single-timestep scenario.
"""

import pytest
from tests import functions_for_testing as fft
from hisim import component as cp
from hisim.components import generic_electrolyzer_and_h2_storage
from hisim import loadtypes as lt
from hisim.simulationparameters import SimulationParameters
from hisim import log
from hisim.config import ComponentID, DisplayConfig


@pytest.mark.base
def test_hydrogen_generator() -> None:
    """Verify electrolyzer output and hydrogen-storage charging at one fixed timestep.

    Builds an AdvancedElectrolyzer and a HydrogenStorage from the two
    presets -- the 2.4 kW machine and the 500 kg tank -- wires fake
    ComponentOutputs (4000 W electricity input, zero hydrogen-not-stored,
    zero discharge target), runs a single simulate step at timestep 1000,
    and asserts the resulting water demand, unused power, and storage
    delta match the expected constants.
    """

    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    # ===================================================================================================================
    # Set Hydrogen Generator
    my_electrolyzer_config = generic_electrolyzer_and_h2_storage.ElectrolyzerWithStorageConfig.preset_standard(
        "ElectrolyzerWithStorage"
    )
    my_electrolyzer = generic_electrolyzer_and_h2_storage.AdvancedElectrolyzer(
        my_simulation_parameters=my_simulation_parameters, config=my_electrolyzer_config
    )
    my_hydrogen_storage_config = (
        generic_electrolyzer_and_h2_storage.ElectrolyzerWithHydrogenStorageConfig.preset_standard(
            "ElectrolyzerWithHydrogenStorage"
        )
    )
    # The expected storage delta below is the charge into an empty tank, which is the one
    # figure the preset does not carry: it ships the 400 kg the module has always defaulted to.
    my_hydrogen_storage_config.starting_fill = 0

    my_hydrogen_storage = generic_electrolyzer_and_h2_storage.HydrogenStorage(
        my_simulation_parameters=my_simulation_parameters,
        config=my_hydrogen_storage_config,
    )

    # Set Fake Outputs for Gas Heater
    electricity_input = cp.ComponentOutput(
        "FakeElectricityInput",
        "ElectricityInput",
        lt.LoadTypes.ELECTRICITY,
        lt.Units.WATT,
        component_id=ComponentID("FakeElectricityInput"),
    )
    hydrogen_not_stored = cp.ComponentOutput(
        "FakeHydrogenNotStored",
        "HydrogenNotStored",
        lt.LoadTypes.GREEN_HYDROGEN,
        lt.Units.KG,
        component_id=ComponentID("FakeHydrogenNotStored"),
    )
    discharging_hydrogen_amount_target = cp.ComponentOutput(
        "DischargingHydrogenAmountTarget",
        "DischargingHydrogenAmountTarget",
        lt.LoadTypes.GREEN_HYDROGEN,
        lt.Units.KG_PER_SEC,
        component_id=ComponentID("DischargingHydrogenAmountTarget"),
    )

    number_of_outputs = fft.get_number_of_outputs(
        [
            electricity_input,
            hydrogen_not_stored,
            discharging_hydrogen_amount_target,
            my_electrolyzer,
            my_hydrogen_storage,
        ]
    )
    stsv: cp.SingleTimeStepValues = cp.SingleTimeStepValues(number_of_outputs)

    # Link inputs and outputs
    my_electrolyzer.electricity_input_channel.source_output = electricity_input
    my_electrolyzer.hydrogen_not_stored_channel.source_output = hydrogen_not_stored
    my_hydrogen_storage.discharging_hydrogen.source_output = discharging_hydrogen_amount_target
    my_hydrogen_storage.charging_hydrogen.source_output = my_electrolyzer.hydrogen_output_channel

    # Add Global Index and set values for fake Inputs
    fft.add_global_index_of_components(
        [
            electricity_input,
            hydrogen_not_stored,
            discharging_hydrogen_amount_target,
            my_electrolyzer,
            my_hydrogen_storage,
        ]
    )
    stsv.values[electricity_input.global_index] = 4000
    stsv.values[hydrogen_not_stored.global_index] = 0
    stsv.values[discharging_hydrogen_amount_target.global_index] = 0

    timestep = 1000

    # Simulate
    my_electrolyzer.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    my_hydrogen_storage.i_simulate(timestep, stsv, False)
    log.information(str(stsv.values))

    # Water Demand to produce Hydrogen
    assert stsv.values[my_electrolyzer.water_demand_channel.global_index] == pytest.approx(0.001114707341269841)
    # Unused Power of Electrolyzer
    assert stsv.values[my_electrolyzer.unused_power_channel.global_index] == 1600
    # Amount of Hydrogen that is stored in Hydrogen-Storage
    assert stsv.values[my_hydrogen_storage.storage_delta.global_index] == pytest.approx(0.0001248472222222222)


@pytest.mark.base
def test_display_config_instance_isolation() -> None:
    """Verify that each component instance gets its own DisplayConfig.

    Regression test: mutable default arguments would share the same
    DisplayConfig instance across all component instances.
    """
    seconds_per_timestep = 60
    my_simulation_parameters = SimulationParameters.one_day_only(2017, seconds_per_timestep)

    electrolyzer_config = generic_electrolyzer_and_h2_storage.ElectrolyzerWithStorageConfig.preset_standard(
        "ElectrolyzerWithStorage"
    )

    storage_config = generic_electrolyzer_and_h2_storage.ElectrolyzerWithHydrogenStorageConfig.preset_standard(
        "HydrogenStorage"
    )
    # The tank starts empty here, which is the one figure the preset does not carry: it ships
    # the 400 kg the module has always defaulted to.
    storage_config.starting_fill = 0

    # Create multiple instances without passing my_display_config
    electrolyzer_1 = generic_electrolyzer_and_h2_storage.AdvancedElectrolyzer(
        my_simulation_parameters=my_simulation_parameters,
        config=electrolyzer_config,
    )
    electrolyzer_2 = generic_electrolyzer_and_h2_storage.AdvancedElectrolyzer(
        my_simulation_parameters=my_simulation_parameters,
        config=electrolyzer_config,
    )
    storage_1 = generic_electrolyzer_and_h2_storage.HydrogenStorage(
        my_simulation_parameters=my_simulation_parameters,
        config=storage_config,
    )
    storage_2 = generic_electrolyzer_and_h2_storage.HydrogenStorage(
        my_simulation_parameters=my_simulation_parameters,
        config=storage_config,
    )

    # Verify each instance has its own DisplayConfig (not shared)
    assert electrolyzer_1.my_display_config is not electrolyzer_2.my_display_config
    assert storage_1.my_display_config is not storage_2.my_display_config
    assert electrolyzer_1.my_display_config is not storage_1.my_display_config

    # Verify type correctness
    assert isinstance(electrolyzer_1.my_display_config, DisplayConfig)
    assert isinstance(storage_1.my_display_config, DisplayConfig)
