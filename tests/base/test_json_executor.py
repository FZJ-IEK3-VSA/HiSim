"""Test for json executor."""
import pytest
from tests.base.test_json_generator import ExampleConfig
from hisim.json_executor import JsonExecutor
from hisim import utils
from pathlib import Path

@pytest.mark.base
@utils.measure_execution_time
def test_json_executor():
    """Test json executor."""
    ex = ExampleConfig()
    ex.make_example_config()
    path = Path(__file__).parent / "json_generator_results"
    path.mkdir(parents=True, exist_ok=True)
    path = path / "cfg.json"
    json_executor = JsonExecutor(path)
    json_executor.execute_all()
