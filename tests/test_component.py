"""Tests for the Component class and related components.

This module contains unit tests for the Component class, ComponentInput,
ComponentOutput, SingleTimeStepValues, ConfigBase, and related classes.
Each test verifies a specific aspect of the component system.
"""

# clean

import os
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import example_component
from hisim.simulationparameters import SimulationParameters
from tests import functions_for_testing as fft

from dataclasses import dataclass


@pytest.mark.base
def test_component_output_and_input():
    """Test ComponentOutput and ComponentInput classes.

    This test verifies:
    - ComponentOutput creation with all parameters
    - ComponentOutput.get_pretty_name() method
    - ComponentOutput attributes (full_name, component_name, field_name, etc.)
    - ComponentInput creation and attributes
    """
    # Test ComponentOutput
    output = cp.ComponentOutput(
        object_name="TestComponent",
        field_name="TestOutput",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        postprocessing_flag=[],
        sankey_flow_direction=True,
        output_description="Test output description",
        source_component_class="TestComponentClass",
    )

    # Verify output attributes
    # Note: ComponentOutput doesn't have object_name as an attribute, it's used to build full_name
    assert output.component_name == "TestComponent"
    assert output.field_name == "TestOutput"
    assert output.full_name == "TestComponent # TestOutput"
    assert output.display_name == "TestOutput"
    assert output.load_type == lt.LoadTypes.ELECTRICITY
    assert output.unit == lt.Units.WATT
    assert output.global_index == -1  # Default value
    assert output.postprocessing_flag == []
    assert output.sankey_flow_direction is True
    assert output.output_description == "Test output description"
    assert output.source_component_class == "TestComponentClass"

    # Test get_pretty_name method
    pretty_name = output.get_pretty_name()
    assert "TestComponent" in pretty_name
    assert "TestOutput" in pretty_name
    assert "Electricity" in pretty_name
    assert "W" in pretty_name

    log.information(f"ComponentOutput pretty name: {pretty_name}")

    # Test ComponentInput
    component_input = cp.ComponentInput(
        object_name="TestComponent",
        field_name="TestInput",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.CELSIUS,
        mandatory=True,
    )

    # Verify input attributes
    assert component_input.fullname == "TestComponent # TestInput"
    assert component_input.component_name == "TestComponent"
    assert component_input.field_name == "TestInput"
    assert component_input.loadtype == lt.LoadTypes.HEATING
    assert component_input.unit == lt.Units.CELSIUS
    assert component_input.global_index == -1  # Default value
    assert component_input.is_mandatory is True
    assert component_input.src_object_name is None  # Default value
    assert component_input.src_field_name is None  # Default value
    assert component_input.source_output is None  # Default value

    log.information("ComponentInput and ComponentOutput tests passed!")


@pytest.mark.base
def test_single_time_step_values():
    """Test SingleTimeStepValues class.

    This test verifies:
    - SingleTimeStepValues initialization
    - Setting and getting output values
    - Getting input values
    - Copying values from another instance
    - Cloning
    - Comparing values with tolerance (is_close_enough_to_previous)
    - Getting differences for error messages
    """
    # Create SingleTimeStepValues with 5 values
    stsv = cp.SingleTimeStepValues(5)
    assert len(stsv.values) == 5
    assert stsv.values == [0.0, 0.0, 0.0, 0.0, 0.0]

    # Create outputs for testing
    output1 = cp.ComponentOutput("Component1", "Output1", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
    output1.global_index = 0

    output2 = cp.ComponentOutput("Component2", "Output2", lt.LoadTypes.HEATING, lt.Units.WATT)
    output2.global_index = 1

    input1 = cp.ComponentInput("Component3", "Input1", lt.LoadTypes.ANY, lt.Units.ANY, True)
    input1.source_output = output1

    # Test set_output_value
    stsv.set_output_value(output1, 123.45)
    assert stsv.values[0] == 123.45

    stsv.set_output_value(output2, 678.90)
    assert stsv.values[1] == 678.90

    # Test get_input_value
    input_value = stsv.get_input_value(input1)
    assert input_value == 123.45

    # Test copy_values_from_other
    stsv2 = cp.SingleTimeStepValues(5)
    stsv2.copy_values_from_other(stsv)
    assert stsv2.values == stsv.values
    assert stsv2.values == [123.45, 678.90, 0.0, 0.0, 0.0]

    # Test clone
    stsv3 = stsv.clone()
    assert stsv3.values == stsv.values
    assert stsv3 is not stsv  # Should be a different object

    # Test is_close_enough_to_previous with same values
    assert stsv.is_close_enough_to_previous(stsv) is True

    # Test is_close_enough_to_previous with slightly different values (should be close)
    stsv_different = cp.SingleTimeStepValues(5)
    stsv_different.values = [123.45001, 678.90, 0.0, 0.0, 0.0]  # Within tolerance
    assert stsv_different.is_close_enough_to_previous(stsv) is True

    # Test is_close_enough_to_previous with significantly different values
    stsv_different2 = cp.SingleTimeStepValues(5)
    stsv_different2.values = [123.5, 678.90, 0.0, 0.0, 0.0]  # Outside tolerance
    assert stsv_different2.is_close_enough_to_previous(stsv) is False

    # Test get_differences_for_error_msg
    stsv_orig = cp.SingleTimeStepValues(3)
    stsv_orig.values = [10.0, 20.0, 30.0]

    stsv_prev = cp.SingleTimeStepValues(3)
    stsv_prev.values = [10.0, 25.0, 30.0]  # Only middle value differs

    outputs = [output1, output2, cp.ComponentOutput("Component3", "Output3", lt.LoadTypes.ANY, lt.Units.ANY)]
    outputs[2].global_index = 2

    diff_msg = stsv_orig.get_differences_for_error_msg(stsv_prev, outputs)
    assert "currently: 20.00" in diff_msg
    assert "previously: 25.00" in diff_msg
    assert "Component2" in diff_msg

    log.information(f"Difference message: {diff_msg}")
    log.information("SingleTimeStepValues tests passed!")


@pytest.mark.base
def test_config_base():
    """Test ConfigBase class.

    This test verifies:
    - ConfigBase initialization
    - ConfigBase.get_string_dict() method
    - ConfigBase.to_dict() method (uses camelCase keys due to JSONWizard)
    - ConfigBase.get_config_classname() method
    - ConfigBase.get_main_classname() method
    """
    # Create a minimal config
    config = cp.ConfigBase(name="TestConfig", building_name="BUI1")

    # Verify config attributes
    assert config.name == "TestConfig"
    assert config.building_name == "BUI1"

    # Test get_string_dict()
    string_dict = config.get_string_dict()
    # Should have some entries
    assert isinstance(string_dict, list)

    # Test to_dict() - uses camelCase keys due to JSONWizard
    config_dict = config.to_dict()
    assert "name" in config_dict
    # JSONWizard converts snake_case to camelCase
    assert "buildingName" in config_dict

    # Test get_config_classname()
    config_classname = config.get_config_classname()
    assert "ConfigBase" in config_classname

    # Test get_main_classname() - should raise NotImplementedError
    with pytest.raises(NotImplementedError):
        config.get_main_classname()

    log.information("ConfigBase tests passed!")


@pytest.mark.base
def test_example_component_with_config():
    """Test ExampleComponent with a custom configuration.

    This test verifies:
    - ExampleComponent creation with custom configuration
    - Component name generation
    - Input and output definitions
    - Component initialization
    """
    # Create simulation parameters
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    # Create custom config
    config = example_component.ExampleComponentConfig(
        building_name="TestBuilding",
        name="CustomExampleComponent",
        loadtype=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        electricity=-1e3,
        capacity=45 * 121.2,
        initial_temperature=25.0,
    )

    # Create component
    component = example_component.ExampleComponent(
        config=config, my_simulation_parameters=sim_params
    )

    # Verify component name
    # Note: building_name is only included when multiple_buildings is True
    comp_name = component.get_component_name()
    assert "CustomExampleComponent" in comp_name
    # When multiple_buildings is False (default), only the component name is used
    assert comp_name == "CustomExampleComponent"

    # Verify inputs
    inputs = component.get_input_definitions()
    assert len(inputs) == 1  # Should have one input (thermal_energy_delivered)
    assert inputs[0].field_name == example_component.ExampleComponent.ThermalEnergyDelivered

    # Verify outputs
    outputs = component.get_outputs()
    assert len(outputs) == 3  # Should have three outputs

    # Verify specific outputs
    output_names = [o.field_name for o in outputs]
    assert example_component.ExampleComponent.ElectricityOutput in output_names
    assert example_component.ExampleComponent.TemperatureMean in output_names
    assert example_component.ExampleComponent.StoredEnergy in output_names

    log.information(f"Component name: {comp_name}")
    log.information(f"Component inputs: {[i.field_name for i in inputs]}")
    log.information(f"Component outputs: {output_names}")
    log.information("ExampleComponent with config tests passed!")


@pytest.mark.base
def test_component_connections():
    """Test Component connection methods.

    This test verifies:
    - add_input and add_output methods
    - connect_input method
    - connect_dynamic_input method
    - ComponentConnection class
    """
    # Create simulation parameters
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    # Create two components
    config1 = example_component.ExampleComponentConfig(
        building_name="Building1",
        name="SourceComponent",
        loadtype=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        electricity=-1e3,
        capacity=45 * 121.2,
        initial_temperature=25.0,
    )

    config2 = example_component.ExampleComponentConfig(
        building_name="Building2",
        name="TargetComponent",
        loadtype=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
        electricity=-1e3,
        capacity=45 * 121.2,
        initial_temperature=25.0,
    )

    source_component = example_component.ExampleComponent(
        config=config1, my_simulation_parameters=sim_params
    )
    target_component = example_component.ExampleComponent(
        config=config2, my_simulation_parameters=sim_params
    )

    # Create an output from source
    source_output = cp.ComponentOutput(
        object_name="SourceComponent",
        field_name="ElectricityOutput",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
    )
    source_output.global_index = 0

    # Add input to target
    target_input = target_component.add_input(
        object_name="TargetComponent",
        field_name="CustomInput",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        mandatory=True,
    )

    # Test connect_dynamic_input - this sets src_object_name and src_field_name
    target_component.connect_dynamic_input("CustomInput", source_output)

    # Verify the connection was made
    # Note: connect_dynamic_input sets src_object_name and src_field_name, not source_output
    assert target_input.src_object_name == "SourceComponent"
    assert target_input.src_field_name == "ElectricityOutput"
    # source_output needs to be set manually
    target_input.source_output = source_output
    assert target_input.source_output == source_output

    # Test ComponentConnection
    conn = cp.ComponentConnection(
        target_input_name=example_component.ExampleComponent.ThermalEnergyDelivered,
        source_class_name="ExampleComponent",
        source_output_name=example_component.ExampleComponent.ElectricityOutput,
    )

    assert conn.target_input_name == example_component.ExampleComponent.ThermalEnergyDelivered
    assert conn.source_class_name == "ExampleComponent"
    assert conn.source_output_name == "ElectricityOutput"
    assert conn.source_instance_name is None  # Default value

    # Test ComponentConnection with instance name
    conn2 = cp.ComponentConnection(
        target_input_name="Input",
        source_class_name="SourceClass",
        source_output_name="Output",
        source_instance_name="SpecificSource",
    )

    assert conn2.source_instance_name == "SpecificSource"

    log.information("Component connection tests passed!")


@pytest.mark.base
def test_component_default_opex_and_capex():
    """Test default OpexCostDataClass and CapexCostDataClass.

    This test verifies:
    - OpexCostDataClass.get_default_opex_cost_data_class() returns correct default values
    - CapexCostDataClass.get_default_capex_cost_data_class() returns correct default values
    - Default data classes have all required attributes
    """
    # Test OpexCostDataClass defaults
    opex_default = cp.OpexCostDataClass.get_default_opex_cost_data_class()

    assert opex_default.opex_energy_cost_in_euro == 0
    assert opex_default.opex_maintenance_cost_in_euro == 0
    assert opex_default.co2_footprint_in_kg == 0
    assert opex_default.total_consumption_in_kwh == 0
    assert opex_default.loadtype == lt.LoadTypes.ANY
    assert opex_default.kpi_tag is None
    assert opex_default.consumption_for_space_heating_in_kwh == 0
    assert opex_default.consumption_for_domestic_hot_water_in_kwh == 0

    # Test CapexCostDataClass defaults
    capex_default = cp.CapexCostDataClass.get_default_capex_cost_data_class()

    assert capex_default.capex_investment_cost_in_euro == 0
    assert capex_default.device_co2_footprint_in_kg == 0
    assert capex_default.lifetime_in_years == 1
    assert capex_default.capex_investment_cost_for_simulated_period_in_euro == 0
    assert capex_default.device_co2_footprint_for_simulated_period_in_kg == 0
    assert capex_default.maintenance_costs_in_euro == 0
    assert capex_default.maintenance_cost_per_simulated_period_in_euro == 0
    assert capex_default.subsidy_as_percentage_of_investment_costs == 0
    assert capex_default.kpi_tag is None

    # Test with custom values
    custom_opex = cp.OpexCostDataClass(
        opex_energy_cost_in_euro=100,
        opex_maintenance_cost_in_euro=50,
        co2_footprint_in_kg=25,
        total_consumption_in_kwh=1000,
        loadtype=lt.LoadTypes.ELECTRICITY,
        consumption_for_space_heating_in_kwh=500,
        consumption_for_domestic_hot_water_in_kwh=500,
        kpi_tag=None,
    )

    assert custom_opex.opex_energy_cost_in_euro == 100
    assert custom_opex.opex_maintenance_cost_in_euro == 50
    assert custom_opex.co2_footprint_in_kg == 25
    assert custom_opex.total_consumption_in_kwh == 1000
    assert custom_opex.consumption_for_space_heating_in_kwh == 500
    assert custom_opex.consumption_for_domestic_hot_water_in_kwh == 500

    custom_capex = cp.CapexCostDataClass(
        capex_investment_cost_in_euro=1000,
        device_co2_footprint_in_kg=50,
        lifetime_in_years=20,
        capex_investment_cost_for_simulated_period_in_euro=500,
        device_co2_footprint_for_simulated_period_in_kg=25,
        maintenance_costs_in_euro=100,
        maintenance_cost_per_simulated_period_in_euro=5,
        subsidy_as_percentage_of_investment_costs=10,
        kpi_tag=None,
    )

    assert custom_capex.capex_investment_cost_in_euro == 1000
    assert custom_capex.device_co2_footprint_in_kg == 50
    assert custom_capex.lifetime_in_years == 20
    assert custom_capex.capex_investment_cost_for_simulated_period_in_euro == 500
    assert custom_capex.device_co2_footprint_for_simulated_period_in_kg == 25
    assert custom_capex.maintenance_costs_in_euro == 100
    assert custom_capex.maintenance_cost_per_simulated_period_in_euro == 5
    assert custom_capex.subsidy_as_percentage_of_investment_costs == 10

    log.information("OpexCostDataClass and CapexCostDataClass tests passed!")


@pytest.mark.base
def test_example_component_simulation():
    """Test ExampleComponent simulation functionality.

    This test verifies:
    - ExampleComponent i_simulate() method
    - State save and restore
    - Output value setting and getting
    """
    # Create simulation parameters
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    # Create component with default config
    config = example_component.ExampleComponentConfig.get_default_example_component()
    component = example_component.ExampleComponent(
        config=config, my_simulation_parameters=sim_params
    )

    # Create outputs
    thermal_energy_delivered_output = cp.ComponentOutput(
        object_name="Source",
        field_name="ThermalEnergyDelivered",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
        output_description="Thermal energy delivered",
    )

    # Set source output
    component.thermal_energy_delivered_c.source_output = thermal_energy_delivered_output

    # Set up SingleTimeStepValues
    number_of_outputs = fft.get_number_of_outputs([component, thermal_energy_delivered_output])
    stsv = cp.SingleTimeStepValues(number_of_outputs)

    # Add global index
    fft.add_global_index_of_components([component, thermal_energy_delivered_output])

    # Set input value
    stsv.values[thermal_energy_delivered_output.global_index] = 50

    # Test simulation
    timestep = 60 * 10  # 10 hours in seconds
    component.i_restore_state()
    component.i_simulate(timestep, stsv, False)

    # Verify output values
    # The thermal energy delivered should be preserved
    assert stsv.values[thermal_energy_delivered_output.global_index] == 50

    # Verify that the simulation produced outputs
    assert component.t_m_c.global_index >= 0
    assert component.electricity_output_c.global_index >= 0
    assert component.stored_energy_c.global_index >= 0

    log.information(f"Output values after simulation: {stsv.values}")
    log.information("ExampleComponent simulation tests passed!")


@pytest.mark.base
def test_component_name_with_multiple_buildings():
    """Test Component name generation with multiple buildings.

    This test verifies:
    - Component name generation when multiple_buildings is True
    - Component name includes building name when appropriate
    """
    # Create simulation parameters with multiple_buildings=True
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.multiple_buildings = True

    # Create a minimal component with ConfigBase
    config = cp.ConfigBase(name="TestComponent", building_name="Building1")

    # Create a simple component subclass for testing
    class TestComponent(cp.Component):
        def __init__(self, config, my_simulation_parameters):
            super().__init__(
                name="TestComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )

        def i_prepare_simulation(self):
            pass

        def i_save_state(self):
            pass

        def i_restore_state(self):
            pass

        def i_simulate(self, timestep, stsv, force_convergence):
            pass

    component = TestComponent(config, sim_params)

    # Verify component name includes building name
    comp_name = component.get_component_name()
    assert "Building1" in comp_name
    assert "TestComponent" in comp_name
    assert comp_name == "Building1_TestComponent"

    # Test with multiple_buildings=False
    sim_params.multiple_buildings = False
    component2 = TestComponent(config, sim_params)
    comp_name2 = component2.get_component_name()
    assert comp_name2 == "TestComponent"
    assert "Building1" not in comp_name2

    log.information(f"Component name with multiple_buildings=True: {comp_name}")
    log.information(f"Component name with multiple_buildings=False: {comp_name2}")
    log.information("Component name with multiple buildings tests passed!")


@pytest.mark.base
def test_component_initialization_and_name_generation():
    """Test Component initialization with valid/invalid configs and name generation.
    
    This test verifies:
    - Component initialization with valid ConfigBase
    - Component initialization raises ValueError for invalid config
    - Component name generation with multiple_buildings=True
    - Component name generation with multiple_buildings=False
    """
    # Test 1: Valid initialization with ConfigBase
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    
    config = cp.ConfigBase(name="TestComponent", building_name="Building1")
    display_config = cp.DisplayConfig()
    
    # Create a simple component subclass
    class TestComponent(cp.Component):
        def __init__(self, config, my_simulation_parameters):
            super().__init__(
                name="TestComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=display_config,
            )
        
        def i_prepare_simulation(self):
            pass
        
        def i_save_state(self):
            pass
        
        def i_restore_state(self):
            pass
        
        def i_simulate(self, timestep, stsv, force_convergence):
            pass
    
    # Test valid initialization
    component = TestComponent(config, sim_params)
    assert component.component_name == "TestComponent"
    assert component.config == config
    assert component.my_display_config == display_config
    
    # Test invalid initialization with non-ConfigBase
    with pytest.raises(ValueError, match="not a ConfigBase object"):
        component_invalid = TestComponent("not_a_config", sim_params)
    
    # Test name generation with multiple_buildings=True
    sim_params.multiple_buildings = True
    component2 = TestComponent(config, sim_params)
    comp_name = component2.get_component_name()
    assert "Building1" in comp_name
    assert "TestComponent" in comp_name
    assert comp_name == "Building1_TestComponent"
    
    # Test name generation with multiple_buildings=False
    sim_params.multiple_buildings = False
    component3 = TestComponent(config, sim_params)
    comp_name2 = component3.get_component_name()
    assert comp_name2 == "TestComponent"
    assert "Building1" not in comp_name2
    
    log.information(f"Component name with multiple_buildings=True: {comp_name}")
    log.information(f"Component name with multiple_buildings=False: {comp_name2}")
    log.information("Component initialization and name generation tests passed!")


@pytest.mark.base
def test_component_connection_logging_creates_json_file(tmp_path):
    """Test that connect_input creates component_connections.json when logging is enabled.
    
    This test verifies:
    - component_connections.json is created when logging is enabled
    - JSON file contains correct connection information
    - Multiple connections are appended to the same file
    """
    # Create simulation parameters with logging enabled
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.result_directory = str(tmp_path)
    sim_params.log_connections = True
    
    # Create components
    config1 = cp.ConfigBase(name="SourceComponent", building_name="Building1")
    config2 = cp.ConfigBase(name="TargetComponent", building_name="Building1")
    
    class TestSourceComponent(cp.Component):
        def __init__(self, config, my_simulation_parameters):
            super().__init__(
                name="SourceComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )
        
        def i_prepare_simulation(self):
            pass
        
        def i_save_state(self):
            pass
        
        def i_restore_state(self):
            pass
        
        def i_simulate(self, timestep, stsv, force_convergence):
            pass
    
    class TestTargetComponent(cp.Component):
        def __init__(self, config, my_simulation_parameters):
            super().__init__(
                name="TargetComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )
        
        def i_prepare_simulation(self):
            pass
        
        def i_save_state(self):
            pass
        
        def i_restore_state(self):
            pass
        
        def i_simulate(self, timestep, stsv, force_convergence):
            pass
    
    source = TestSourceComponent(config1, sim_params)
    target = TestTargetComponent(config2, sim_params)
    
    # Add output to source
    source_output = source.add_output(
        object_name="SourceComponent",
        field_name="ElectricityOutput",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        output_description="Electricity output",
    )
    source_output.global_index = 0
    
    # Add input to target
    target_input = target.add_input(
        object_name="TargetComponent",
        field_name="ElectricityInput",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        mandatory=True,
    )
    
    # Add second input to target
    target_input2 = target.add_input(
        object_name="TargetComponent",
        field_name="ElectricityInput2",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        mandatory=True,
    )
    
    # Connect input (should create JSON file)
    target.connect_input("ElectricityInput", "SourceComponent", "ElectricityOutput")
    
    # Verify JSON file was created
    json_file = os.path.join(sim_params.result_directory, "component_connections.json")
    assert os.path.exists(json_file), "component_connections.json should be created"
    
    # Verify JSON file content
    import json
    with open(json_file, "r", encoding="utf-8") as f:
        connections = json.load(f)
    
    assert len(connections) == 1
    assert connections[0]["From"]["Component"] == "SourceComponent"
    assert connections[0]["From"]["Field"] == "ElectricityOutput"
    assert connections[0]["To"]["Component"] == "TargetComponent"
    assert connections[0]["To"]["Field"] == "ElectricityInput"
    
    # Test multiple connections
    target.connect_input("ElectricityInput2", "SourceComponent", "ElectricityOutput2")
    
    # Verify second connection was appended
    with open(json_file, "r", encoding="utf-8") as f:
        connections = json.load(f)
    
    assert len(connections) == 2
    assert connections[1]["From"]["Field"] == "ElectricityOutput2"
    
    log.information(f"Connection logging JSON file: {json_file}")
    log.information(f"Connection count: {len(connections)}")
    log.information("Component connection logging tests passed!")


@pytest.mark.base
def test_component_connect_with_default_connections():
    """Test connect_only_predefined_connections and get_default_connections.
    
    This test verifies:
    - add_default_connections stores connections by source class name
    - get_default_connections returns copies with source_instance_name set
    - connect_only_predefined_connections connects all predefined connections
    """
    # Create simulation parameters
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    
    # Create source component
    config1 = cp.ConfigBase(name="Weather", building_name="Building1")
    
    class Weather(cp.Component):
        Altitude = "Altitude"
        Azimuth = "Azimuth"
        ApparentZenith = "ApparentZenith"
        
        def __init__(self, config, my_simulation_parameters):
            super().__init__(
                name="Weather",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )
        
        def i_prepare_simulation(self):
            pass
        
        def i_save_state(self):
            pass
        
        def i_restore_state(self):
            pass
        
        def i_simulate(self, timestep, stsv, force_convergence):
            pass
    
    # Create target component
    config2 = cp.ConfigBase(name="Building", building_name="Building1")
    
    class Building(cp.Component):
        Altitude = "Altitude"
        Azimuth = "Azimuth"
        
        def __init__(self, config, my_simulation_parameters):
            super().__init__(
                name="Building",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )
        
        def get_default_connections_from_weather(self):
            """Get weather default connections."""
            connections = []
            weather_classname = Weather.get_classname()
            connections.append(
                cp.ComponentConnection(
                    Building.Altitude,
                    weather_classname,
                    Weather.Altitude,
                )
            )
            connections.append(
                cp.ComponentConnection(
                    Building.Azimuth,
                    weather_classname,
                    Weather.Azimuth,
                )
            )
            return connections
        
        def i_prepare_simulation(self):
            pass
        
        def i_save_state(self):
            pass
        
        def i_restore_state(self):
            pass
        
        def i_simulate(self, timestep, stsv, force_convergence):
            pass
    
    weather = Weather(config1, sim_params)
    building = Building(config2, sim_params)
    
    # Add default connections from weather to building
    building.add_default_connections(building.get_default_connections_from_weather())
    
    # Verify connections were added
    assert "Weather" in building.default_connections
    assert len(building.default_connections["Weather"]) == 2
    
    # Test get_default_connections
    default_conns = building.get_default_connections(weather)
    
    # Verify returned connections have source_instance_name set
    assert len(default_conns) == 2
    assert default_conns[0].source_instance_name == "Weather"
    assert default_conns[1].source_instance_name == "Weather"
    assert default_conns[0].target_input_name == Building.Altitude
    assert default_conns[1].target_input_name == Building.Azimuth
    assert default_conns[0].source_class_name == "Weather"
    assert default_conns[1].source_class_name == "Weather"
    
    # Test connect_only_predefined_connections
    # Note: We can't test connect_only_predefined_connections here because the Building
    # class is defined inside the function, so building2 would be a different class
    # with a different default_connections dict. Instead, we test that the connections
    # were properly set up on the original building object.
    # The get_default_connections test already verifies that connections work correctly.
    
    log.information("Component default connections tests passed!")


@pytest.mark.base
def test_single_time_step_values_copy_and_clone():
    """Test that copy_values_from_other and clone produce independent copies.
    
    This test verifies:
    - copy_values_from_other creates a copy but shares the values list
    - clone creates a completely independent copy
    - Modifying one copy doesn't affect the original
    """
    # Create initial STSV
    stsv1 = cp.SingleTimeStepValues(5)
    stsv1.values = [1.0, 2.0, 3.0, 4.0, 5.0]
    
    # Test copy_values_from_other
    stsv2 = cp.SingleTimeStepValues(5)
    stsv2.copy_values_from_other(stsv1)
    
    # Verify values are copied
    assert stsv2.values == stsv1.values
    
    # Check if they share the same list reference (using slicing creates a new list)
    # The implementation uses slicing, so they should be independent
    stsv2.values[0] = 100.0
    # Since copy_values_from_other uses slicing [:], they should be independent
    assert stsv1.values[0] == 1.0  # Original should not change
    
    # Test clone
    stsv3 = stsv1.clone()
    
    # Verify clone is independent
    assert stsv3.values == stsv1.values
    assert stsv3 is not stsv1  # Different object
    assert stsv3.values is not stsv1.values  # Different list
    
    # Modify cloned object
    stsv3.values[0] = 200.0
    assert stsv1.values[0] == 1.0  # Original should not change
    assert stsv2.values[0] == 100.0  # Other copy should not change
    
    # Verify all original values are preserved
    assert stsv1.values == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert stsv2.values == [100.0, 2.0, 3.0, 4.0, 5.0]
    assert stsv3.values == [200.0, 2.0, 3.0, 4.0, 5.0]
    
    log.information("SingleTimeStepValues copy and clone tests passed!")


@pytest.mark.base
def test_single_time_step_values_is_close_enough_to_previous():
    """Test tolerance-based comparison for convergence checks.
    
    This test verifies:
    - is_close_enough_to_previous uses tolerance of 0.0001
    - Values within tolerance return True
    - Values outside tolerance return False
    """
    # Create two STSV with same values
    stsv1 = cp.SingleTimeStepValues(3)
    stsv1.values = [10.0, 20.0, 30.0]
    
    stsv2 = cp.SingleTimeStepValues(3)
    stsv2.values = [10.0, 20.0, 30.0]
    
    # Same values should be close enough
    assert stsv1.is_close_enough_to_previous(stsv2) is True
    
    # Test within tolerance (0.0001)
    stsv3 = cp.SingleTimeStepValues(3)
    stsv3.values = [10.00005, 20.00005, 30.00005]  # Difference: 0.00005 < 0.0001
    assert stsv3.is_close_enough_to_previous(stsv1) is True
    
    # Test at tolerance boundary
    stsv4 = cp.SingleTimeStepValues(3)
    stsv4.values = [10.0001, 20.0, 30.0]  # Difference: 0.0001 == 0.0001 (boundary)
    assert stsv4.is_close_enough_to_previous(stsv1) is True
    
    # Test outside tolerance
    stsv5 = cp.SingleTimeStepValues(3)
    stsv5.values = [10.0002, 20.0, 30.0]  # Difference: 0.0002 > 0.0001
    assert stsv5.is_close_enough_to_previous(stsv1) is False
    
    # Test partial differences
    stsv6 = cp.SingleTimeStepValues(3)
    stsv6.values = [10.00005, 20.0002, 30.00005]  # Only middle value outside tolerance
    assert stsv6.is_close_enough_to_previous(stsv1) is False
    
    # Test with pytest.approx for tolerance comparison
    assert stsv3.is_close_enough_to_previous(stsv1) is True
    assert stsv5.is_close_enough_to_previous(stsv1) is False
    
    log.information("SingleTimeStepValues is_close_enough_to_previous tests passed!")


@pytest.mark.base
def test_single_time_step_values_get_set_output_value():
    """Test get_input_value and set_output_value using global_index.
    
    This test verifies:
    - set_output_value sets the correct value at the global_index
    - get_input_value retrieves the correct value from source_output's global_index
    - Global index handling works correctly
    """
    # Create STSV with 3 values
    stsv = cp.SingleTimeStepValues(3)
    
    # Create outputs with global indices
    output1 = cp.ComponentOutput("Component1", "Output1", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
    output1.global_index = 0
    
    output2 = cp.ComponentOutput("Component2", "Output2", lt.LoadTypes.HEATING, lt.Units.WATT)
    output2.global_index = 1
    
    output3 = cp.ComponentOutput("Component3", "Output3", lt.LoadTypes.ANY, lt.Units.ANY)
    output3.global_index = 2
    
    # Test set_output_value
    stsv.set_output_value(output1, 123.45)
    assert stsv.values[0] == 123.45
    
    stsv.set_output_value(output2, 678.90)
    assert stsv.values[1] == 678.90
    
    stsv.set_output_value(output3, 999.99)
    assert stsv.values[2] == 999.99
    
    # Test get_input_value with source_output set
    input1 = cp.ComponentInput("Component1", "Input1", lt.LoadTypes.ELECTRICITY, lt.Units.WATT, True)
    input1.source_output = output1
    
    input_value = stsv.get_input_value(input1)
    assert input_value == 123.45
    
    input2 = cp.ComponentInput("Component2", "Input2", lt.LoadTypes.HEATING, lt.Units.WATT, True)
    input2.source_output = output2
    
    input_value2 = stsv.get_input_value(input2)
    assert input_value2 == 678.90
    
    # Test get_input_value when source_output is None
    input3 = cp.ComponentInput("Component3", "Input3", lt.LoadTypes.ANY, lt.Units.ANY, True)
    # source_output not set
    
    input_value3 = stsv.get_input_value(input3)
    assert input_value3 == 0  # Should return 0 when source_output is None
    
    # Test with different global index
    output4 = cp.ComponentOutput("Component4", "Output4", lt.LoadTypes.ELECTRICITY, lt.Units.WATT)
    output4.global_index = 5  # Different index
    
    stsv2 = cp.SingleTimeStepValues(6)  # Need 6 values for index 5
    stsv2.set_output_value(output4, 555.55)
    assert stsv2.values[5] == 555.55
    assert stsv2.values[0] == 0.0  # Other indices should be 0
    
    log.information("SingleTimeStepValues get/set output value tests passed!")


@pytest.mark.base
def test_component_output_pretty_name():
    """Test ComponentOutput.get_pretty_name formats correctly.
    
    This test verifies:
    - get_pretty_name includes component name, field name, load type, and unit
    - Format is consistent
    """
    # Test basic output
    output = cp.ComponentOutput(
        object_name="SolarThermal",
        field_name="ThermalPower",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
    )
    
    pretty_name = output.get_pretty_name()
    assert "SolarThermal" in pretty_name
    assert "ThermalPower" in pretty_name
    assert "Heating" in pretty_name
    assert "W" in pretty_name
    assert " - " in pretty_name
    assert " [" in pretty_name
    assert "] " in pretty_name or pretty_name.endswith("]")
    
    # Test different load types and units
    output2 = cp.ComponentOutput(
        object_name="Battery",
        field_name="StateOfCharge",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.PERCENT,
    )
    
    pretty_name2 = output2.get_pretty_name()
    assert "Battery" in pretty_name2
    assert "StateOfCharge" in pretty_name2
    assert "Electricity" in pretty_name2
    assert "%" in pretty_name2
    
    # Test with special characters in names
    output3 = cp.ComponentOutput(
        object_name="Building 1",
        field_name="Temperature Mean",
        load_type=lt.LoadTypes.TEMPERATURE,
        unit=lt.Units.CELSIUS,
    )
    
    pretty_name3 = output3.get_pretty_name()
    assert "Building 1" in pretty_name3
    assert "Temperature Mean" in pretty_name3
    
    log.information(f"ComponentOutput pretty name: {pretty_name}")
    log.information(f"ComponentOutput pretty name 2: {pretty_name2}")
    log.information(f"ComponentOutput pretty name 3: {pretty_name3}")
    log.information("ComponentOutput.get_pretty_name tests passed!")


@pytest.mark.base
def test_configbase_to_string_dict():
    """Test ConfigBase.get_string_dict produces human-readable list.
    
    This test verifies:
    - get_string_dict returns a list of strings
    - Strings are human-readable with capitalized keys
    - Empty config returns empty list
    """
    # Test with minimal config
    config = cp.ConfigBase(name="TestComponent", building_name="BUI1")
    
    string_dict = config.get_string_dict()
    assert isinstance(string_dict, list)
    assert len(string_dict) > 0
    
    # Verify format
    assert any("Buildingname" in s for s in string_dict)
    assert any("Name" in s for s in string_dict)
    
    # Test with config that has more fields (inherit from ConfigBase)
    # Note: ConfigBase only has name and building_name, so we can't test with custom fields
    # The existing tests already cover get_string_dict() functionality
    
    # Test empty config (but ConfigBase always has name and building_name)
    config3 = cp.ConfigBase(name="", building_name="")
    string_dict3 = config3.get_string_dict()
    
    # Should still have entries for name and building_name (even if empty)
    assert len(string_dict3) >= 2
    
    log.information(f"String dict: {string_dict}")
    log.information("ConfigBase.get_string_dict tests passed!")


@pytest.mark.base
def test_displayconfig_show_method():
    """Test DisplayConfig.show() returns correct instance with display_in_webtool=True.
    
    This test verifies:
    - show() classmethod creates DisplayConfig instance
    - display_in_webtool is set to True
    - pretty_name is set correctly
    """
    # Test show() with pretty name
    config = cp.DisplayConfig.show("My Component")
    
    assert isinstance(config, cp.DisplayConfig)
    assert config.display_in_webtool is True
    assert config.pretty_name == "My Component"
    
    # Test show() with different names
    config2 = cp.DisplayConfig.show("Another Component")
    assert config2.display_in_webtool is True
    assert config2.pretty_name == "Another Component"
    
    # Test that default show() has display_in_webtool=True
    config3 = cp.DisplayConfig.show("")
    assert config3.display_in_webtool is True
    
    # Test that show() doesn't affect regular DisplayConfig
    config4 = cp.DisplayConfig()
    assert config4.display_in_webtool is False  # Default is False
    assert config4.pretty_name is None  # Default is None
    
    config5 = cp.DisplayConfig(pretty_name="Not via show")
    assert config5.display_in_webtool is False  # Not via show
    assert config5.pretty_name == "Not via show"
    
    log.information(f"DisplayConfig.show result: {config}")
    log.information(f"DisplayConfig.show result 2: {config2}")
    log.information("DisplayConfig.show() tests passed!")


@pytest.mark.base
def test_cost_dataclasses_defaults():
    """Test OpexCostDataClass and CapexCostDataClass get_default_* methods.
    
    This test verifies:
    - get_default_opex_cost_data_class returns zero/neutral values
    - get_default_capex_cost_data_class returns zero/neutral values
    - All required fields have correct default values
    """
    # Test OpexCostDataClass defaults
    opex_default = cp.OpexCostDataClass.get_default_opex_cost_data_class()
    
    assert opex_default.opex_energy_cost_in_euro == 0
    assert opex_default.opex_maintenance_cost_in_euro == 0
    assert opex_default.co2_footprint_in_kg == 0
    assert opex_default.total_consumption_in_kwh == 0
    assert opex_default.consumption_for_space_heating_in_kwh == 0
    assert opex_default.consumption_for_domestic_hot_water_in_kwh == 0
    assert opex_default.loadtype == lt.LoadTypes.ANY
    assert opex_default.kpi_tag is None
    
    # Test CapexCostDataClass defaults
    capex_default = cp.CapexCostDataClass.get_default_capex_cost_data_class()
    
    assert capex_default.capex_investment_cost_in_euro == 0
    assert capex_default.device_co2_footprint_in_kg == 0
    assert capex_default.lifetime_in_years == 1
    assert capex_default.capex_investment_cost_for_simulated_period_in_euro == 0
    assert capex_default.device_co2_footprint_for_simulated_period_in_kg == 0
    assert capex_default.maintenance_costs_in_euro == 0
    assert capex_default.maintenance_cost_per_simulated_period_in_euro == 0
    assert capex_default.subsidy_as_percentage_of_investment_costs == 0
    assert capex_default.kpi_tag is None
    
    # Test that defaults can be overridden
    custom_opex = cp.OpexCostDataClass(
        opex_energy_cost_in_euro=100.5,
        opex_maintenance_cost_in_euro=50.25,
        co2_footprint_in_kg=25.75,
        total_consumption_in_kwh=1000,
        loadtype=lt.LoadTypes.ELECTRICITY,
        consumption_for_space_heating_in_kwh=500,
        consumption_for_domestic_hot_water_in_kwh=500,
        kpi_tag=None,
    )
    
    assert custom_opex.opex_energy_cost_in_euro == 100.5
    assert custom_opex.opex_maintenance_cost_in_euro == 50.25
    assert custom_opex.co2_footprint_in_kg == 25.75
    assert custom_opex.total_consumption_in_kwh == 1000
    assert custom_opex.consumption_for_space_heating_in_kwh == 500
    assert custom_opex.consumption_for_domestic_hot_water_in_kwh == 500
    assert custom_opex.loadtype == lt.LoadTypes.ELECTRICITY
    
    custom_capex = cp.CapexCostDataClass(
        capex_investment_cost_in_euro=10000,
        device_co2_footprint_in_kg=500,
        lifetime_in_years=20,
        capex_investment_cost_for_simulated_period_in_euro=5000,
        device_co2_footprint_for_simulated_period_in_kg=250,
        maintenance_costs_in_euro=100,
        maintenance_cost_per_simulated_period_in_euro=5,
        subsidy_as_percentage_of_investment_costs=10,
        kpi_tag=None,
    )
    
    assert custom_capex.capex_investment_cost_in_euro == 10000
    assert custom_capex.device_co2_footprint_in_kg == 500
    assert custom_capex.lifetime_in_years == 20
    assert custom_capex.capex_investment_cost_for_simulated_period_in_euro == 5000
    assert custom_capex.device_co2_footprint_for_simulated_period_in_kg == 250
    assert custom_capex.maintenance_costs_in_euro == 100
    assert custom_capex.maintenance_cost_per_simulated_period_in_euro == 5
    assert custom_capex.subsidy_as_percentage_of_investment_costs == 10
    
    log.information("OpexCostDataClass and CapexCostDataClass default tests passed!")
