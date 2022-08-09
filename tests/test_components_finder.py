from hisim.components_finder import Components_Finder
from hisim import utils


@utils.measure_execution_time
def test_componentsfinder():
    cf = Components_Finder()
    cf.run()
