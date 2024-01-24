"""Air-conditioned household."""

# clean
from typing import Optional

from system_setups.air_conditioned_house_a_with_mpc_controller import (
    air_conditioned_house,
)

from hisim.simulator import SimulationParameters
from hisim.simulator import Simulator


__authors__ = "Marwa Alfouly, Sebastian Dickler"
__copyright__ = "Copyright 2023, HiSim - Household Infrastructure and Building Simulator"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Sebastian Dickler"
__email__ = "s.dickler@fz-juelich.de"
__status__ = "development"


def setup_function(my_sim: Simulator, my_simulation_parameters: Optional[SimulationParameters] = None) -> None:
    """Simulates household with air-conditioner with ON/OFF controller."""

    air_conditioned_house(my_sim, "on_off", my_simulation_parameters)
