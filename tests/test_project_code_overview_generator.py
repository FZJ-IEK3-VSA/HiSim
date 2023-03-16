import pytest
from hisim.project_code_overview_generator import OverviewGenerator
from hisim import utils


@pytest.mark.base
@utils.measure_execution_time
def test_project_code_overview_generator():
    cf = OverviewGenerator()
    cf.run()
