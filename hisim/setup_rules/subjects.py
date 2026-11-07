"""The subjects of the setup rules: what each hook hands to the rules of its stage.

One frozen object per hook. ``LoadedDocument`` is the energy-system file before any class is imported: as the reader
built it, or, in a run, with its imports and groups expanded (stage ``LOAD``).
``ImportedAssemblies`` and ``ExpandedImports`` are a file's imports as their expansion met them, the assemblies it
found and then what it could not select, bind or order (stage ``BUILD``, declarative mode only; the expansion runs
before the file is checked as ``LoadedDocument``, since a file names its imported members only once they are expanded).
``BuiltDocument`` is the expanded file with its constructed, wired components, which the declarative build hands
over (stage ``BUILD``, declarative mode only); before it, ``BoundDocument`` is the expanded file with its entries bound to
their classes and ``ConfiguredDocument`` the file with its configurations built and sized, which the binding and the
configuring of a declarative build hand over (stage ``BUILD``). ``AddedSystem`` is the simulator's components as they were added, which
the run hands over first, before it prepares anything (stage ``BUILD``, both modes). ``BuiltSystem`` is the
simulator's components with their inputs connected, before the first step (stage ``BUILD``, both modes).
``RunResults`` is the result table and the outputs after the run, with the energy balance read from them when a rule
first asks for it (stage ``AFTER_RUN``). A rule reads its subject and nothing else: no component module, no
``SimRepository``. The types of the energy-system format, of the simulator and of the energy balance are imported for
type checking only, so importing the rules package imports none of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Callable, ClassVar, FrozenSet, Mapping, Optional, Tuple

from hisim.setup_rules.rule import RuleStage, RuleSubject

if TYPE_CHECKING:
    import pandas as pd

    from hisim.component import Component, ComponentOutput
    from hisim.energy_system.assemblies.record import SourceMap
    from hisim.energy_system.assemblies.resolver import ResolvedAssembly
    from hisim.energy_system.bindings import ClassBindings
    from hisim.energy_system.configure import ConfigurationOutcome
    from hisim.energy_system.errors import ImportRefusal
    from hisim.energy_system.model import EnergySystemFile
    from hisim.energy_system.path_resolver import PathResolver
    from hisim.energy_system.wiring import WiredSystem
    from hisim.postprocessing.energy_balance import EnergyBalanceReport


@dataclass(frozen=True)
class LoadedDocument(RuleSubject):
    """An energy-system file as it will be built, before any component class is imported.

    ``model`` is the file the rules check: the file as the reader built it when it is only loaded
    (``load_energy_system``), and, in a run, the file after its imports and groups were expanded, the selected world
    the run builds. A file that imports assemblies names its members only once they are expanded, so a run checks the
    expanded file. ``document`` is the top-level mapping the reader parsed it from, for a rule that needs to point at
    a key the model does not keep, empty when the caller holds none; ``file`` is the file's path, ``None`` for a
    document read from text. ``source_map`` says which import produced a component, for a message to name it
    (``None`` for a file without imports). ``rerun`` says whether a run declared itself the re-run of a record: ``True``
    or ``False`` in a run, ``None`` when the file is only loaded.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.LOAD

    model: EnergySystemFile
    document: Mapping[str, object]
    file: Optional[str] = None
    source_map: Optional[SourceMap] = None
    rerun: Optional[bool] = None


@dataclass(frozen=True)
class ImportedAssemblies(RuleSubject):
    """The assemblies an energy-system file imports, as the expansion of its imports found them, before it selects anything.

    ``model`` is the file as read, its ``imports`` block unexpanded. ``assemblies`` maps each import key to the
    assembly it names, for every import whose assembly was found once; ``refusals`` holds what the lookup of the
    others recorded (an assembly found nowhere, or in two library directories). ``file`` is the file's path,
    ``None`` for a file read from text. The library check reads the assemblies; nothing is selected or bound before
    the rules of this subject found nothing.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    model: EnergySystemFile
    assemblies: Mapping[str, ResolvedAssembly]
    refusals: Tuple[ImportRefusal, ...] = ()
    file: Optional[str] = None


@dataclass(frozen=True)
class ExpandedImports(RuleSubject):
    """An energy-system file's imports after the expansion selected their parameters, bound their ports and ordered them.

    ``model`` is the file as read; ``refusals`` holds what the expansion could not do: a parameter or a constraint an
    import breaks, a port it cannot bind, an evaluation order that names one number twice, a record whose components
    left the sequence its import record states. The expansion stops at the first of its stages that recorded a
    refusal, since the next would read what the refusal left undecided, so the refusals are those of one stage.
    ``file`` is the file's path, ``None`` for a file read from text. A file without imports is this subject too, with
    the one check a re-run record of an expansion gets: its sequence.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    model: EnergySystemFile
    refusals: Tuple[ImportRefusal, ...] = ()
    file: Optional[str] = None


@dataclass(frozen=True)
class BuiltDocument(RuleSubject):
    """An energy-system file after its imports and groups were expanded and its components built and wired.

    ``model`` is the expanded file, ``wired`` holds the constructed components, the connections the file asks for
    (made only once the rules found nothing) and what the wiring could not do, ``path_resolver`` turns the file's
    ``${name}`` references into paths (``None`` for the machine's default), ``file`` is the file's path, ``None`` for
    a document read from text, and ``source_map`` says which import produced a component, for a message to name it
    (``None`` for a file without imports).
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    model: EnergySystemFile
    wired: WiredSystem
    path_resolver: Optional[PathResolver] = None
    file: Optional[str] = None
    source_map: Optional[SourceMap] = None


@dataclass(frozen=True)
class BoundDocument(RuleSubject):
    """An energy-system file after its imports and groups were expanded, its entries bound to the classes they name.

    The binding of a declarative build hands it over before any configuration is built. ``model`` is the expanded
    file; ``bindings`` holds, per entry whose class resolved, the component class, the configuration class, its
    description and the preset or constructor the entry names when the class declares it, and the refusals of the
    entries whose class did not resolve (``ClassBindings.refusals``); ``file`` is the file's path, ``None`` for a
    document read from text or a model built in Python; ``source_map`` says which import produced a component, for a
    message to name it (``None`` for a file without imports).
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    model: EnergySystemFile
    bindings: ClassBindings
    file: Optional[str] = None
    source_map: Optional[SourceMap] = None


@dataclass(frozen=True)
class ConfiguredDocument(RuleSubject):
    """An energy-system file with its configurations built and its sizing resolved, before any component exists.

    The configuring of a declarative build hands it over once every entry's configuration was built and the sizing
    kernel ran. ``model`` is the expanded file; ``outcome`` holds the configurations, the refusals of the entries
    whose configuration could not be built (a value that does not decode, a path variable nobody registered) and
    what the sizing kernel did: its report, or the failure it raised, or nothing when an entry was refused and the
    sizing did not run. ``file`` and ``source_map`` are those of :class:`BoundDocument`.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    model: EnergySystemFile
    outcome: ConfigurationOutcome
    file: Optional[str] = None
    source_map: Optional[SourceMap] = None


@dataclass(frozen=True)
class AddedSystem(RuleSubject):
    """The components of one run as they were added to the simulator, before the run prepares or connects them.

    The run hands it over first thing, so that a defect visible in the list of components alone (a class without a
    kind, a name used twice, a lifecycle-cost run with a class no cost model describes) refuses the run before it
    prepares a result directory or a component. ``components`` are in the order they were added; ``outputs`` are the
    outputs registered so far, in the order of the result columns (a dynamic component grows more while it is
    connected); ``post_processing_options`` are the options the run's parameters request, members of
    ``PostProcessingOptions`` as ``SimulationParameters`` holds them, for example ``COMPUTE_LIFECYCLE_COSTS``.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    components: Tuple[Component, ...]
    outputs: Tuple[ComponentOutput, ...]
    post_processing_options: Tuple[int, ...] = ()


@dataclass(frozen=True)
class BuiltSystem(RuleSubject):
    """The components of one run with their inputs connected, before the first step, in either mode.

    ``components`` are in the order they were added to the simulator, which is the order of the result columns.
    ``outputs`` are the registered outputs, every result column in order, including those a dynamic component grew
    while it was connected; empty when the caller hands over only components (a test of one rule family).
    ``connects_automatically`` holds the names of the components that were added with ``connect_automatically=True``,
    whose default connections the run applied.
    """

    STAGE: ClassVar[RuleStage] = RuleStage.BUILD

    components: Tuple[Component, ...]
    outputs: Tuple[ComponentOutput, ...] = ()
    connects_automatically: FrozenSet[str] = frozenset()


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
