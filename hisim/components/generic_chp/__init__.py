"""Combined heat and power plant (CHP) module.

A CHP is either a natural gas driven turbine producing both electricity and heat, or a fuel
cell. It does not modulate: it is either on or off, and when it runs it outputs a constant
thermal and electrical power and consumes a constant flow of hydrogen or natural gas. The
plant is switched by its own L1 controller, which asks for a thermal demand and, when an
electricity signal is connected, an electricity deficit before it turns the plant on.

Note on the package layout:
    One component and its helpers are one directory, following the ``hisim.components.building``
    precedent. ``chp.py`` holds the plant (the former ``hisim/components/generic_chp.py``) and
    ``controller.py`` holds its L1 controller (the former
    ``hisim/components/controller_l1_chp.py``), which nothing outside this package imports. This
    ``__init__`` re-exports all public names, so every name keeps resolving under the package
    path it had before the split: ``from hisim.components.generic_chp import SimpleCHP`` imports
    exactly what it did, and the two component classes report the package as their own path as
    well, because their ``__module__`` is pinned below.

Note on ``SimpleCHP.__module__`` and ``L1CHPController.__module__``:
    Both are pinned to this package below. ``get_full_classname`` and the energy-system recorder
    derive a component's class path from ``__module__``, so without the pin the split would move
    the two component classes to ``hisim.components.generic_chp.chp.SimpleCHP`` and
    ``hisim.components.generic_chp.controller.L1CHPController``. Nothing recorded names a CHP
    class yet, so nothing would break today; the pin is for consistency with the other component
    packages, which pin for exactly this reason, and it is what makes this docstring's claim
    about fully-qualified classname strings true rather than half true:
    ``SimpleCHP.get_full_classname()`` is ``hisim.components.generic_chp.SimpleCHP`` and
    ``L1CHPController.get_full_classname()`` is ``hisim.components.generic_chp.L1CHPController``.

    The configuration dataclasses are not pinned -- they are not in the other packages either --
    so ``CHPConfig.get_config_classname()`` is ``hisim.components.generic_chp.chp.CHPConfig`` and
    ``L1CHPControllerConfig.get_config_classname()`` is
    ``hisim.components.generic_chp.controller.L1CHPControllerConfig``. Nothing an author writes
    carries those strings -- an energy-system file names the component class -- and
    ``ComponentClassScan.paths_of`` reports both the package and the submodule spelling for a
    re-exported class anyway, so either form resolves.

    The one cost of the pin is that ``inspect.getsourcelines(SimpleCHP)`` no longer finds the
    class body (the overview generator already handles that case and reports a zero line count);
    imports, pickling and ``describe`` are unaffected.

"""

# clean

from hisim.components.generic_chp.chp import CHPConfig, GenericCHPState, SimpleCHP
from hisim.components.generic_chp.controller import (
    L1CHPController,
    L1CHPControllerConfig,
    L1CHPControllerState,
)

__authors__ = "Frank Burkrad, Maximilian Hillen, Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt", "Vitor Hugo Bellotto Zago"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Johanna Ganglbauer"
__email__ = "johanna.ganglbauer@4wardenergy.at"
__status__ = "development"

# The public name of these classes is the package, not the submodule; see the note above.
SimpleCHP.__module__ = __name__
L1CHPController.__module__ = __name__

__all__ = [
    "CHPConfig",
    "GenericCHPState",
    "L1CHPController",
    "L1CHPControllerConfig",
    "L1CHPControllerState",
    "SimpleCHP",
]
