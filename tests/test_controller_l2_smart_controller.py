"""Test for controller l2 smart controller."""

import pytest
from hisim.components import controller_l2_smart_controller


@pytest.mark.base
def test_smart_controller_default_config_name() -> None:
    """Test that the default SmartController config name has no leading space."""
    # Get default config
    default_config = controller_l2_smart_controller.SmartControllerConfig.get_default_config_ems()
    
    # Verify the name does not have a leading space
    assert not default_config.name.startswith(" "), (
        f"SmartController default name has a leading space: '{default_config.name}'"
    )
    
    # Verify the expected name
    assert default_config.name == "SmartController", (
        f"SmartController default name should be 'SmartController', got '{default_config.name}'"
    )


@pytest.mark.base
def test_smart_controller_default_config_building_name() -> None:
    """Test that the default SmartController config building_name is correct."""
    default_config = controller_l2_smart_controller.SmartControllerConfig.get_default_config_ems()
    assert default_config.building_name == "BUI1"


@pytest.mark.base
def test_smart_controller_custom_config() -> None:
    """Test that SmartController can be configured with custom values."""
    custom_name = "MyCustomController"
    custom_building = "MyBuilding"
    
    config = controller_l2_smart_controller.SmartControllerConfig(
        building_name=custom_building,
        name=custom_name,
    )
    
    assert config.name == custom_name
    assert config.building_name == custom_building
