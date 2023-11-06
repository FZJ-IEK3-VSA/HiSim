"""Test for project code overview generator."""
import pytest
from hisim.project_code_overview_generator import OverviewGenerator
from hisim import utils


@pytest.mark.base
@utils.measure_execution_time
def test_project_code_overview_generator():
    """Test project code overview generator."""
    c_f = OverviewGenerator()
    c_f.run()
