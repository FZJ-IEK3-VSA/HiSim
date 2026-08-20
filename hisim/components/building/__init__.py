"""Building module.

This module simulates the thermal behaviour of a building using reference data from the EPISCOPE/TABULA project and
a resistor-capacitor (RC) model from the RC_BuildingSimulator project and the ISO 13790 norm.

The module contains the following classes:
    1. class BuildingState - constructs the building state.
    2. class BuildingControllerState - constructs the building controller state.
    3. class BuildingConfig - json dataclass, configurates the building class.
    4. class BuildingControllerConfig -  json dataclass, configurates the building controller class.
    5. class Building -  main class, involves building properties taken from the EPISCOPE/TABULA project database and
       calculations from the RC_BuildingSimulator project (see notes below).
       Inputs of the building class are the heating device, occupancy and weather.
       Outputs are for example temperature, stored energy and solar gains through windows.
    6. class Window - taken from the RC simulator project, calculates for example solar gains through windows.
    7. class BuildingInformation - gets important building parameters and properties

Note on the EPISCOPE/TABULA project:
     Ths project involves a collection of multiple typologies of residences from 12 European countries, listing among others,
     heat coefficient, area, volumes, light transmissibility, house heat capacity for various residence construction elements.
     These typologies are categorized by year of construction, residence type and degree of refurbishment.
     For information, please access site: https://episcope.eu/building-typology/webtool/

Note on the RC_BuildingSimulator project:
    The functions cited in this module are at some degree based on the RC_BuildingSimulator project:
    [rc_buildingsimulator-jayathissa]:
    [1] Jayathissa, Prageeth, et al. "Optimising building net energy demand with dynamic BIPV shading." Applied Energy 202 (2017): 726-735.
    The implementation of the RC_BuildingSimulator project can be found under the following repository:
    https://github.com/architecture-building-systems/RC_BuildingSimulator

    The RC model (5R1C model) is based on the EN ISO 13790 standard and explains thermal physics with the help of an electrical circuit analogy.
    Principal components of this model are the heat fluxes Phi which are analogous to the electrical current I,
    the thermal conductances H which are the inverse of the thermal resistance and analogous to the electrical conductance G (=1/R),
    the thermal capacitance C analogous to the electrical capacitance C and
    the temperatures T which are analogous to the electrical voltage V.

Another paper using the 5R1C model from EN ISO 13790:
    [2] I. Horvat et al. "Dynamic method for calculating energy need in HVAC systems." Transactions of Famena 40 (2016): 47-62.


Note on the package layout:
    This module was split mechanically (zero behavior change) from a single 3,000-line
    ``building.py`` into a package: ``config.py`` holds ``BuildingConfig``,
    ``information.py`` holds ``BuildingInformation``, ``window.py`` holds ``Window`` and
    ``building.py`` holds ``Building`` together with its ``BuildingState``. This
    ``__init__`` re-exports all public names, so the public import path is stable:
    ``hisim.components.building.Building`` (and the other classes) keep resolving both
    for Python imports and for the fully-qualified classname strings stored in committed
    scenario JSONs.

"""

# clean

from hisim.components.building.building import Building, BuildingState
from hisim.components.building.config import BuildingConfig
from hisim.components.building.information import BuildingInformation
from hisim.components.building.window import Window

__authors__ = "Vitor Hugo Bellotto Zago"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Dr. Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"

__all__ = [
    "Building",
    "BuildingConfig",
    "BuildingInformation",
    "BuildingState",
    "Window",
]
