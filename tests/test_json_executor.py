"""Test for json executor."""
import pytest
from tests.test_json_generator import ExampleConfig
from hisim.json_executor import JsonExecutor
from hisim import utils


@pytest.mark.base
@utils.measure_execution_time
def test_json_executor():
    """Test json executor."""
    ex = ExampleConfig()
    ex.make_example_config()

    json_executor = JsonExecutor("cfg.json")
    json_executor.execute_all()
