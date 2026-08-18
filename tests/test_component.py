"""Tests for the Component class and related components.

This module contains unit tests for the Component class, ComponentInput,
ComponentOutput, SingleTimeStepValues, ConfigBase, and related classes.
Each test verifies a specific aspect of the component system.
"""

# clean

import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim import log
from hisim.components import example_component
from hisim.simulationparameters import SimulationParameters
from hisim.component import ComponentID
from tests import functions_for_testing as fft


@pytest.mark.base
def test_component_output_and_input() -> None:
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
def test_single_time_step_values() -> None:
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
def test_config_base() -> None:
    """Test ConfigBase class.

    This test verifies:
    - ConfigBase initialization
    - ConfigBase.get_string_dict() method
    - ConfigBase.to_dict() method (uses camelCase keys due to JSONWizard)
    - ConfigBase.get_config_classname() method
    - ConfigBase.get_main_classname() method
    """
    # Create a minimal config
    config = cp.ConfigBase(component_id=ComponentID(name="TestConfig"))

    # Verify config attributes
    assert config.component_id.name == "TestConfig"
    assert config.component_id.building is None
    assert config.component_id.unit is None

    # Test get_string_dict()
    string_dict = config.get_string_dict()
    # Should have some entries
    assert isinstance(string_dict, list)

    # Test to_dict() - uses camelCase keys due to JSONWizard
    config_dict = config.to_dict()
    # JSONWizard converts snake_case to camelCase, and the identity is nested
    assert "componentId" in config_dict
    assert config_dict["componentId"]["name"] == "TestConfig"

    # Test get_config_classname()
    config_classname = config.get_config_classname()
    assert "ConfigBase" in config_classname

    # Test get_main_classname() - should raise NotImplementedError
    with pytest.raises(NotImplementedError):
        config.get_main_classname()

    log.information("ConfigBase tests passed!")


@pytest.mark.base
def test_example_component_with_config() -> None:
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
        component_id=ComponentID(name="CustomExampleComponent"),
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
    # The identity carries no building, so the runtime name is just the plain name.
    comp_name = component.get_component_name()
    assert "CustomExampleComponent" in comp_name
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
def test_component_connections() -> None:
    """Test Component connection methods.

    This test verifies:
    - add_input and add_output methods
    - connect_input method
    - connect_dynamic_input method
    - ComponentConnection class
    """
    # Create simulation parameters
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    # Create target component
    config2 = example_component.ExampleComponentConfig(
        component_id=ComponentID(name="TargetComponent", building="Building2"),
        loadtype=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
        electricity=-1e3,
        capacity=45 * 121.2,
        initial_temperature=25.0,
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
def test_add_default_connections_empty_raises() -> None:
    """Test that add_default_connections raises ValueError on an empty list.

    This test verifies:
    - Passing an empty connections list raises a ValueError (not an IndexError)
      before the unsafe ``connections[0]`` access (see issue #192).
    - The error message mentions the empty list and the component name.
    - The normal, non-empty case still registers the default connections.
    """
    # Create simulation parameters
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)

    # Create a component instance
    config = example_component.ExampleComponentConfig(
        component_id=ComponentID(name="TargetComponent", building="Building1"),
        loadtype=lt.LoadTypes.HEATING,
        unit=lt.Units.WATT,
        electricity=-1e3,
        capacity=45 * 121.2,
        initial_temperature=25.0,
    )
    component = example_component.ExampleComponent(
        config=config, my_simulation_parameters=sim_params
    )

    # An empty connections list must raise a clear ValueError, not an IndexError.
    with pytest.raises(ValueError, match="connections list is empty"):
        component.add_default_connections([])

    # The error message should also mention the component that was called.
    try:
        component.add_default_connections([])
    except ValueError as exc:
        assert "TargetComponent" in str(exc)

    # The normal (non-empty) case should still work and register the connection.
    conn = cp.ComponentConnection(
        target_input_name=example_component.ExampleComponent.ThermalEnergyDelivered,
        source_class_name="ExampleComponent",
        source_output_name=example_component.ExampleComponent.ElectricityOutput,
    )
    component.add_default_connections([conn])
    assert "ExampleComponent" in component.default_connections
    assert component.default_connections["ExampleComponent"] == [conn]

    log.information("add_default_connections empty-list guard tests passed!")


@pytest.mark.base
def test_component_default_opex_and_capex() -> None:
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
def test_example_component_simulation() -> None:
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
    timestep = 60 * 10  # 10 minutes in seconds
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
def test_component_name_with_multiple_buildings() -> None:
    """Test Component name generation for a component that carries a building.

    This test verifies:
    - Component name generation when the structured identity carries a building
    - The generated name is independent of the ``multiple_buildings`` simulation parameter
    """
    # The multiple_buildings flag must no longer influence naming at all.
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    sim_params.multiple_buildings = True

    # Create a minimal component with ConfigBase
    config = cp.ConfigBase(component_id=ComponentID(name="TestComponent", building="Building1"))

    # Create a simple component subclass for testing
    class TestComponent(cp.Component):
        """Minimal component subclass for testing component name generation."""

        def __init__(self, config: cp.ConfigBase, my_simulation_parameters: SimulationParameters) -> None:
            super().__init__(
                name="TestComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )

        def i_prepare_simulation(self) -> None:
            pass

        def i_save_state(self) -> None:
            pass

        def i_restore_state(self) -> None:
            pass

        def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
            pass

    component = TestComponent(config, sim_params)

    # Verify component name includes building name
    comp_name = component.get_component_name()
    assert "Building1" in comp_name
    assert "TestComponent" in comp_name
    assert comp_name == "Building1_TestComponent"

    # Flipping multiple_buildings must not change the name any more: the building is part of
    # the component's own identity, not of a global simulation flag.
    sim_params.multiple_buildings = False
    component2 = TestComponent(config, sim_params)
    comp_name2 = component2.get_component_name()
    assert comp_name2 == "Building1_TestComponent"

    # A component whose identity carries no building keeps the bare name in both cases.
    building_less = TestComponent(cp.ConfigBase(component_id=ComponentID(name="TestComponent")), sim_params)
    assert building_less.get_component_name() == "TestComponent"

    log.information(f"Component name with multiple_buildings=True: {comp_name}")
    log.information(f"Component name with multiple_buildings=False: {comp_name2}")
    log.information("Component name with multiple buildings tests passed!")


@pytest.mark.base
def test_stateless_component_provides_noop_state_hooks() -> None:
    """The StatelessComponent class overrides i_save_state and i_restore_state with no-ops.

    A stateless component has no mutable per-timestep state, so saving and
    restoring state are no-ops.  The overrides live directly on
    StatelessComponent (not inherited from Component) so that subclasses
    inherit them without providing their own stub implementations.
    """
    assert issubclass(cp.StatelessComponent, cp.Component)
    # The state-management hooks are overridden on StatelessComponent itself.
    assert "i_save_state" in cp.StatelessComponent.__dict__
    assert "i_restore_state" in cp.StatelessComponent.__dict__
    assert "i_prepare_simulation" in cp.StatelessComponent.__dict__
    # i_doublecheck already has a no-op default on Component; StatelessComponent
    # inherits it rather than re-declaring it.
    assert "i_doublecheck" not in cp.StatelessComponent.__dict__


@pytest.mark.base
def test_component_base_still_raises_not_implemented_for_state_hooks() -> None:
    """Component base class keeps NotImplementedError for state-management hooks.

    This is a deliberate fail-loudly guard: a stateFUL component that forgets
    to implement i_save_state / i_restore_state / i_prepare_simulation must
    crash visibly rather than silently skipping state management.  Only
    StatelessComponent (opt-in) provides the no-op defaults.
    """
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = cp.ConfigBase(component_id=ComponentID(name="TestComponent"))

    class _StatefulComponent(cp.Component):
        """A minimal Component that does NOT override the state hooks."""

        def __init__(self, config: cp.ConfigBase, my_simulation_parameters: SimulationParameters) -> None:
            super().__init__(
                name="TestComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )

        def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
            pass

        def write_to_report(self) -> list[str]:
            return []

    component = _StatefulComponent(config, sim_params)
    with pytest.raises(NotImplementedError):
        component.i_save_state()
    with pytest.raises(NotImplementedError):
        component.i_restore_state()
    with pytest.raises(NotImplementedError):
        component.i_prepare_simulation()


@pytest.mark.base
def test_stateless_component_noop_hooks_are_callable() -> None:
    """The no-op lifecycle hooks on a StatelessComponent subclass do not raise.

    A concrete StatelessComponent subclass only needs to implement i_simulate
    and write_to_report; the state-management and preparation hooks are
    inherited as no-ops.
    """
    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = cp.ConfigBase(component_id=ComponentID(name="TestComponent"))

    class _MinimalStateless(cp.StatelessComponent):
        """Minimal stateless component for testing inherited no-op hooks."""

        def __init__(self, config: cp.ConfigBase, my_simulation_parameters: SimulationParameters) -> None:
            super().__init__(
                name="TestComponent",
                my_simulation_parameters=my_simulation_parameters,
                my_config=config,
                my_display_config=cp.DisplayConfig(),
            )

        def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
            pass

        def write_to_report(self) -> list[str]:
            return []

    component = _MinimalStateless(config, sim_params)
    # These must not raise — they are inherited no-ops from StatelessComponent.
    component.i_save_state()
    component.i_restore_state()
    component.i_prepare_simulation()
    # i_doublecheck is inherited as a no-op from Component.
    component.i_doublecheck(0, cp.SingleTimeStepValues(0))


@pytest.mark.base
def test_component_input_allow_unconnected_mandatory_default() -> None:
    """Test that ComponentInput.allow_unconnected_mandatory defaults to False."""
    component_input = cp.ComponentInput(
        object_name="TestComponent",
        field_name="TestInput",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.CELSIUS,
        mandatory=True,
    )
    assert component_input.allow_unconnected_mandatory is False

    optional_input = cp.ComponentInput(
        object_name="TestComponent",
        field_name="TestInput",
        load_type=lt.LoadTypes.HEATING,
        unit=lt.Units.CELSIUS,
        mandatory=True,
        allow_unconnected_mandatory=True,
    )
    assert optional_input.allow_unconnected_mandatory is True


@pytest.mark.base
def test_connect_inputs_raises_for_unconnected_mandatory() -> None:
    """Test that connect_inputs raises ValueError for an unconnected mandatory input."""
    from hisim.component_wrapper import ComponentWrapper

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_component.ExampleComponentConfig.get_default_example_component()
    component = example_component.ExampleComponent(config=config, my_simulation_parameters=sim_params)

    mandatory_input = cp.ComponentInput(
        object_name=component.component_name,
        field_name="MandatoryUnconnected",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        mandatory=True,
    )
    mandatory_input.src_object_name = "NonExistentSource"
    mandatory_input.src_field_name = "NonExistentOutput"
    component.inputs.append(mandatory_input)

    wrapper = ComponentWrapper(component, is_cachable=False, connect_automatically=False)
    with pytest.raises(ValueError, match="not connected to any ComponentOutput"):
        wrapper.connect_inputs(all_outputs=[])


@pytest.mark.base
def test_connect_inputs_warns_for_allow_unconnected_mandatory() -> None:
    """Test that connect_inputs warns instead of raising when allow_unconnected_mandatory is True."""
    from hisim.component_wrapper import ComponentWrapper

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = example_component.ExampleComponentConfig.get_default_example_component()
    component = example_component.ExampleComponent(config=config, my_simulation_parameters=sim_params)

    optional_mandatory_input = cp.ComponentInput(
        object_name=component.component_name,
        field_name="OptionalMandatory",
        load_type=lt.LoadTypes.ELECTRICITY,
        unit=lt.Units.WATT,
        mandatory=True,
        allow_unconnected_mandatory=True,
    )
    optional_mandatory_input.src_object_name = "NonExistentSource"
    optional_mandatory_input.src_field_name = "NonExistentOutput"
    component.inputs.append(optional_mandatory_input)

    wrapper = ComponentWrapper(component, is_cachable=False, connect_automatically=False)
    # Should not raise — the input is marked as allow_unconnected_mandatory.
    wrapper.connect_inputs(all_outputs=[])
    assert optional_mandatory_input.source_output is None


@pytest.mark.base
def test_dynamic_component_connection_allow_unconnected_mandatory_field() -> None:
    """Test that DynamicComponentConnection has allow_unconnected_mandatory field."""
    from hisim.dynamic_component import DynamicComponentConnection

    conn = DynamicComponentConnection(
        source_component_class=cp.Component,
        source_class_name="TestSource",
        source_component_field_name="TestOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[],
        source_weight=1,
    )
    assert conn.allow_unconnected_mandatory is False

    conn_opt = DynamicComponentConnection(
        source_component_class=cp.Component,
        source_class_name="TestSource",
        source_component_field_name="TestOutput",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[],
        source_weight=1,
        allow_unconnected_mandatory=True,
    )
    assert conn_opt.allow_unconnected_mandatory is True


@pytest.mark.base
def test_add_component_input_and_connect_propagates_allow_unconnected() -> None:
    """Test that add_component_input_and_connect propagates allow_unconnected_mandatory."""
    from hisim.dynamic_component import DynamicComponent

    sim_params = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=60)
    config = cp.ConfigBase(component_id=ComponentID(name="TestDynamic"))
    dyn_component = DynamicComponent(
        my_component_inputs=[],
        my_component_outputs=[],
        name="TestDynamic",
        my_simulation_parameters=sim_params,
        my_config=config,
        my_display_config=cp.DisplayConfig(),
    )

    dyn_component.add_component_input_and_connect(
        source_component_output="TestOutput",
        source_object_name="TestSource",
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
        source_weight=1,
        allow_unconnected_mandatory=True,
    )

    assert len(dyn_component.inputs) == 1
    created_input = dyn_component.inputs[0]
    assert created_input.is_mandatory is True
    assert created_input.allow_unconnected_mandatory is True
    assert created_input.src_object_name == "TestSource"
    assert created_input.src_field_name == "TestOutput"


@pytest.mark.base
def test_electricity_meter_dhw_connection_allow_unconnected() -> None:
    """Test that the electricity meter marks the DHW heat-pump connection as allow_unconnected_mandatory."""
    from hisim.components.electricity_meter import ElectricityMeter
    from hisim.components.more_advanced_heat_pump_hplib import MoreAdvancedHeatPumpHPLib

    # get_default_connections_from_more_advanced_heat_pump does not use self,
    # so we bypass __init__ to avoid constructing the full component.
    meter = ElectricityMeter.__new__(ElectricityMeter)
    connections = meter.get_default_connections_from_more_advanced_heat_pump()

    dhw_connections = [
        c
        for c in connections
        if c.source_component_field_name == MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW
    ]
    assert len(dhw_connections) == 1
    assert dhw_connections[0].allow_unconnected_mandatory is True

    sh_connections = [
        c
        for c in connections
        if c.source_component_field_name == MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH
    ]
    assert len(sh_connections) == 1
    assert sh_connections[0].allow_unconnected_mandatory is False
