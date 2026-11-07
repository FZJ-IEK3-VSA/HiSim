"""The subjects of the setup rules: what each hook hands to the rules of its stage.

One frozen object per hook. ``LoadedDocument`` is the energy-system file as the reader built it (stage ``LOAD``).
``BuiltDocument`` is the expanded file with its constructed, wired components, which the declarative build hands
over (stage ``BUILD``, declarative mode only). ``BuiltSystem`` is the simulator's components with their inputs
connected, before the first step (stage ``BUILD``, both modes). ``RunResults`` is the result table and the outputs
after the run, with the energy balance read from them when a rule first asks for it (stage ``AFTER_RUN``). A rule
reads its subject and nothing else: no component module, no ``SimRepository``. The types of the energy-system format,
of the simulator and of the energy balance are imported for type checking only, so importing the rules package
imports none of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Callable, ClassVar, Mapping, Optional, Tuple

from hisim.setup_rules.rule import RuleStage, RuleSubject

if TYPE_CHECKING:
    import pandas as pd

    from hisim.component import Component, ComponentOutput
    from hisim.energy_system.model import EnergySystemFile
    from hisim.energy_system.path_resolver import PathResolver
    from hisim.energy_system.wiring import WiredSystem
    from hisim.postprocessing.energy_balance import EnergyBalanceReport


@dataclass(frozen=True)
class LoadedDocument(RuleSubject):
    """An energy-system file as the reader built it, before any component class is imported.

    ``model`` is the parsed file; ``document`` is the top-level mapping the reader parsed it from, for a rule that
    needs to point at a key the model does not keep; ``file`` is the file's path, ``None`` for a document read from
    text.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.LOAD

    model: EnergySystemFile
    document: Mapping[str, object]
    file: Optional[str] = None


@dataclass(frozen=True)
class BuiltDocument(RuleSubject):
    """An energy-system file after its imports and groups were expanded and its components built and wired.

    ``model`` is the expanded file, ``wired`` holds the constructed components and the connections the file made,
    ``path_resolver`` turns the file's ``${name}`` references into paths (``None`` for the machine's default), and
    ``file`` is the file's path, ``None`` for a document read from text.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    model: EnergySystemFile
    wired: WiredSystem
    path_resolver: Optional[PathResolver] = None
    file: Optional[str] = None


@dataclass(frozen=True)
class BuiltSystem(RuleSubject):
    """The components of one run with their inputs connected, before the first step, in either mode.

    ``components`` are in the order they were added to the simulator, which is the order of the result columns.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    components: Tuple[Component, ...]


@dataclass(frozen=True)
class RunResults(RuleSubject):
    """The results of one run, as the simulator hands them to the post-processing, and the run's energy balance.

    ``results`` is the result table, one row per step and one column per output; ``outputs`` are the outputs in the
    order of the columns; ``components`` are the components of the run in the order they were added; ``timesteps``
    is the number of steps the simulation parameters ask for, which the table has one row for each.
    ``read_energy_balance`` computes the run's energy balance (the declared ports, each component's balance, the links
    and the meters, :class:`~hisim.postprocessing.energy_balance.EnergyBalanceReport`) from those; :attr:`energy_balance`
    calls it once, when the first rule reads the balance. A rule that requires the finite-results rule therefore
    never makes the balance read a table that holds NaN, and the post-processing writes the report and the Sankeys
    from the same balance the rules judged. The subject imports nothing of the post-processing: the hook hands the
    function over.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.AFTER_RUN

    results: pd.DataFrame
    outputs: Tuple[ComponentOutput, ...]
    components: Tuple[Component, ...]
    timesteps: int
    read_energy_balance: Callable[[], EnergyBalanceReport]

    @cached_property
    def energy_balance(self) -> EnergyBalanceReport:
        """Return the run's energy balance, computed by ``read_energy_balance`` on the first call and kept for the next.

        Example: the component-balance rule and the link rule of ``hisim/setup_rules/results.py`` both read it, and the
        balance is computed once for the two.

        Raises:
            ValueError: As :meth:`~hisim.postprocessing.energy_balance.ports.DeclaredPorts.collect` raises it, for a
                port or a loop the result table cannot be read for.
        """
        return self.read_energy_balance()
