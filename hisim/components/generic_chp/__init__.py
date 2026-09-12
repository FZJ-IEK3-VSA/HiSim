"""Combined heat and power plant (CHP) module.

A CHP is either a natural gas driven turbine producing both electricity and heat, or a fuel
cell. It does not modulate: it is either on or off, and when it runs it outputs a constant
thermal and electrical power and consumes a constant flow of hydrogen or natural gas. The
plant is switched by its own L1 controller, which asks for both a thermal and an electrical
demand before it turns the plant on.

The package contains the following classes:
    1. class CHPConfig - json dataclass, configurates the CHP class.
    2. class GenericCHPState - constructs the CHP state.
    3. class SimpleCHP - main class, the plant itself.
    4. class L1CHPControllerConfig - json dataclass, configurates the CHP controller class.
    5. class L1CHPControllerState - constructs the CHP controller state.
    6. class L1CHPController - the L1 controller that switches the plant.

Note on the package layout:
    One component and its helpers are one directory. ``chp.py`` holds the plant (the former
    ``hisim/components/generic_chp.py``) and ``controller.py`` holds its L1 controller (the
    former ``hisim/components/controller_l1_chp.py``), which nothing outside this package
    imports. This ``__init__`` re-exports all public names, so the public import path is
    stable: ``hisim.components.generic_chp.SimpleCHP`` (and the other classes) keep
    resolving both for Python imports and for fully-qualified classname strings.

"""

# clean

from hisim.components.generic_chp.chp import CHPConfig, GenericCHPState, SimpleCHP
from hisim.components.generic_chp.controller import (
    L1CHPController,
    L1CHPControllerConfig,
    L1CHPControllerState,
)

__authors__ = "Frank Burkrad, Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = "development"

__all__ = [
    "CHPConfig",
    "GenericCHPState",
    "L1CHPController",
    "L1CHPControllerConfig",
    "L1CHPControllerState",
    "SimpleCHP",
]
