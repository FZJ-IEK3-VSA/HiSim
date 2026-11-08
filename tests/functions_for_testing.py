"""Helper functions for testing."""
import dataclasses
from types import SimpleNamespace
from typing import Any, ClassVar, Optional, Sequence, Tuple, Type

import pandas as pd
import yaml

from hisim.component import ComponentOutput
from hisim.components.example_component import ExampleComponentConfig
from hisim.config import ComponentID, SizingContext
from hisim.energy_system.codec import ConfigValueCodec
from hisim.energy_system.errors import WiringRefusal
from hisim.energy_system.loader import parse_energy_system
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.path_resolver import PathResolver
from hisim.energy_system.record import ConfigBlockWriter
from hisim.energy_system.wiring import WiredSystem
from hisim.postprocessing.energy_balance import EnergyBalanceReport
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.registry import SetupRuleRegistry, StageFindings
from hisim.setup_rules.rule import RuleCodes, RuleStage, SetupRule
from hisim.setup_rules.subjects import BuiltDocument, BuiltSystem, LoadedDocument, RunResults
from hisim.simulationparameters import SimulationParameters

#: Conditioned floor area of the default TABULA building (``BuildingConfig.preset_german_single_family_home``,
#: building code ``DE.N.SFH.05.Gen.ReEx.001.002``) in m². The two example components size
#: fields from this fact, and the tests below resolve their configs against exactly this
#: value, which is what keeps their numbers identical to the literals the modules used to
#: carry (45 J/K/m² x 121.2 m² = 5454.0 J/K for the example component's capacity).
DEFAULT_CONDITIONED_FLOOR_AREA_IN_M2: float = 121.2


def default_building_sizing_context() -> SizingContext:
    """The sizing context of a default-building scenario, for tests that size one component.

    A real run gets its context from the building via ``SizingContext.for_building``, which
    reads the TABULA catalogue; a unit test that only needs one fact states that fact instead,
    so it stays fast and its numbers are visible in the test.

    Returns:
        SizingContext: a context carrying the default building's conditioned floor area.
    """
    return SizingContext(conditioned_floor_area_in_m2=DEFAULT_CONDITIONED_FLOOR_AREA_IN_M2)


def sized_example_component_config(component_id: Optional[ComponentID] = None) -> ExampleComponentConfig:
    """The default example component configuration, resolved so a component may be built from it.

    ``ExampleComponentConfig.capacity`` is a sizable field, so the preset hands back a config
    carrying ``AUTO`` and ``Component.__init__`` refuses it. Every test that constructs an
    ``ExampleComponent`` therefore resolves first, and does it through this one helper rather
    than repeating the context.

    Args:
        component_id: the identity to build the configuration for. A preset takes only an
            instance name, so an identity that carries a building is substituted afterwards;
            None means the plain ``"ExampleComponent"`` the preset builds.

    Returns:
        ExampleComponentConfig: the resolved configuration, with ``capacity`` a real number.
    """
    config = ExampleComponentConfig.preset_standard("ExampleComponent")
    if component_id is not None:
        config = dataclasses.replace(config, component_id=component_id)
    return config.resolve(default_building_sizing_context())


def round_trip_config_block(config: Any, config_class: Type, component_name: str) -> Any:
    """Sends a configuration through the record's own writer, a YAML file and the reader.

    This is line for line what ``EntryConfigurator._realize_origin``
    (:mod:`hisim.energy_system.configure`) does for an entry configured by a complete ``config``
    block: the block writer renders the configuration, the value codec turns the block back into a
    deserializer payload, the entry's key is injected as the identity the block itself never
    carries, and the class reads it. The YAML pass in the middle is what makes it a file somebody
    runs rather than a dictionary handed straight back.

    Args:
        config: The configuration to write out.
        config_class: The configuration's class, which reads the block back.
        component_name: The entry key the component is recorded under; also its identity.

    Returns:
        The configuration as its own record rebuilds it.
    """
    block = ConfigBlockWriter(PathResolver.default()).block(component_name, config)
    reloaded = yaml.safe_load(yaml.safe_dump(block))
    payload = ConfigValueCodec(config_class).to_deserializer_payload(
        reloaded, f"components.{component_name}.config", component_name
    )
    payload[ConfigBlockWriter.IDENTITY_FIELD] = {"name": component_name}
    return config_class.from_dict(payload)


class FamilyRules:
    """The setup rules of one family run alone on components, as the build runs them.

    A test that wires stand-ins by hand runs only one family, so that the rules of the other families, which may read
    what a stand-in lacks, stay out of it. A registry needs every code a rule requires, so a rule the family requires
    from another family is taken as met: a sample rule with its code that finds nothing stands in for it. For example,
    the loop-wiring rules require the fluid-loop rules, which cannot walk a hand-built loop whose channels carry no loop
    tags; the energy-port tests judge the energy ports, not the loops.
    """

    #: The family of the rules, the module of ``hisim/setup_rules/`` that holds them.
    FAMILY: ClassVar[str]

    @classmethod
    def registry(cls, rules: Optional[Sequence[Type[SetupRule]]] = None) -> SetupRuleRegistry:
        """Return a registry of the given rules, by default every rule of the family, with their other requirements taken as met.

        Args:
            rules: The rules to run; ``None`` for every rule of the family.

        Returns:
            The registry: the rules, and for every code they require that is none of them, a sample rule that finds nothing.
        """
        # The sample rules are a tool of the tests of the rules package.
        from tests.rules.sample_rules import SampleRules  # pylint: disable=import-outside-toplevel

        chosen = list(rules) if rules is not None else [rule for rule in SetupRuleRegistry.discover() if RuleCodes.family_of(rule.__module__) == cls.FAMILY]
        codes = {rule.CODE for rule in chosen}
        met = sorted({code for rule in chosen for code in rule.REQUIRES} - codes)
        return SetupRuleRegistry(chosen + [SampleRules.rule(code, f"taken-as-met-{code.lower()}") for code in met])

    @classmethod
    def refuse_a_broken_wiring(cls, components: Sequence[Any], kind: SetupKind = SetupKind.RESIDENTIAL) -> None:
        """Run the family's rules on components whose inputs are connected, as the simulator does before the first step.

        Args:
            components: The components, or stand-ins with ``component_name``, ``inputs``, ``outputs`` and ``port_sets``.
            kind: The kind the system declares.

        Raises:
            SetupRuleViolation: Listing every fault the rules found, as the family's error (for example
                ``EnergyPortWiringError``).
        """
        cls.registry().run(RuleStage.BUILD, BuiltSystem(components=tuple(components)), kind)


class EnergyPortRules(FamilyRules):
    """The energy-port setup rules (``hisim/setup_rules/energy_ports.py``) run on components; their faults raise ``EnergyPortWiringError``."""

    FAMILY: ClassVar[str] = "energy_ports"


class FluidLoopRules(FamilyRules):
    """The fluid-loop setup rules (``hisim/setup_rules/fluid_loops.py``) run on components; their faults raise ``FluidLoopWiringError``."""

    FAMILY: ClassVar[str] = "fluid_loops"


class ComponentRules(FamilyRules):
    """The component-build setup rules (``hisim/setup_rules/components.py``) run alone; their faults raise ``SetupRuleViolation``, or the cost rules' errors."""

    FAMILY: ClassVar[str] = "components"


class WiringRules(FamilyRules):
    """The wiring setup rules (``hisim/setup_rules/wiring.py``) run on a built file; their faults raise ``EnergySystemWiringError``.

    A test that drives the feed resolver or the channel matcher directly holds the refusals they recorded; :meth:`refuse`
    hands them to the rules as the wiring of a file with no wire, as the build does, so the test sees the error the build
    would raise.
    """

    FAMILY: ClassVar[str] = "wiring"

    @classmethod
    def subject(cls, wired: Any, name: str = "wiring under test") -> BuiltDocument:
        """The built file of a wired system, its model a file of this name that holds nothing else."""
        model = EnergySystemFile.model_construct(name=name, components={}, setup_kind=SetupKind.DEMO)
        return BuiltDocument(model=model, wired=wired)

    @classmethod
    def refuse(cls, refusals: Sequence[WiringRefusal]) -> None:
        """Run the wiring rules on the refusals a resolver or a matcher recorded, as the build does.

        The wired system holds no component and no wire, so that no rule of the plan judges components the test never
        meant to wire.

        Args:
            refusals: The refusals the test's resolver or matcher recorded.

        Raises:
            EnergySystemWiringError: Listing every refusal, when there is one.
        """
        wired = WiredSystem(components=(), wires=(), resolved_feeds=(), refusals=tuple(refusals))
        cls.registry().run(RuleStage.BUILD, cls.subject(wired), SetupKind.DEMO)


class DocumentRules(FamilyRules):
    """The structural setup rules (``hisim/setup_rules/document.py``) run on a document; their faults raise ``EnergySystemStructureError``."""

    FAMILY: ClassVar[str] = "document"

    @staticmethod
    def subject(text: str, rerun: Optional[bool] = None) -> LoadedDocument:
        """The document of an energy-system file written as YAML text, as the reader built it, for a run when ``rerun`` is not ``None``."""
        return LoadedDocument(model=parse_energy_system(text), document={}, rerun=rerun)

    @classmethod
    def findings(cls, text: str, rerun: Optional[bool] = None) -> StageFindings:
        """Run the structural rules alone on a document written as YAML text, and return what they found without raising."""
        subject = cls.subject(text, rerun)
        return cls.registry().findings(RuleStage.LOAD, subject, subject.model.setup_kind)


class AfterRunRules:
    """The setup rules after the run on an energy balance built by hand, as the post-processing runs them.

    A test of the balance builds an :class:`~hisim.postprocessing.energy_balance.EnergyBalanceReport` from a hand-made
    result table and stand-ins; these helpers hand it to the rules as the run's balance, beside an empty result table
    with no outputs and no steps, which the rules of the table (``AR-01``, ``AR-02``) pass. So the rules judge exactly the balance the test
    built, and a port the test fills with NaN reaches the port rule ``AR-03``, as it did when the balance judged itself.
    """

    @staticmethod
    def subject(balance: EnergyBalanceReport) -> RunResults:
        """The results of a run whose energy balance is ``balance``."""
        return RunResults(results=pd.DataFrame(), outputs=(), components=(), timesteps=0, read_energy_balance=lambda: balance)

    @classmethod
    def refuse(cls, balance: EnergyBalanceReport, kind: SetupKind = SetupKind.RESIDENTIAL) -> None:
        """Run every rule after the run on ``balance``, as the post-processing does first thing.

        Raises:
            EnergyBalanceError: Listing every fault of the balance's rules.
        """
        SetupRuleRegistry.collect().run(RuleStage.AFTER_RUN, cls.subject(balance), kind)

    @staticmethod
    def post_processing(result_directory: str, export: bool, kind: SetupKind = SetupKind.RESIDENTIAL) -> Any:
        """The part of a post-processing transfer the judging reads: options with or without ``EXPORT_ENERGY_BALANCE``, a directory, a kind."""
        return SimpleNamespace(
            post_processing_options=[PostProcessingOptions.EXPORT_ENERGY_BALANCE] if export else [],
            simulation_parameters=SimpleNamespace(result_directory=result_directory, seconds_per_timestep=900),
            setup_kind=kind,
        )


class SetupTestParameters:
    """Simulation parameters for a system-setup test, with the KPI machinery switched on.

    ``SimulationParameters.one_day_only`` enables no post-processing at all, and every setup test
    in this suite used it as-is. A setup could therefore run every timestep, write finished.flag,
    satisfy its test, and still fail the instant anyone asked it for KPIs -- which is precisely
    what four of them did, each discovered only when something else went looking. The failures
    lived entirely in post-processing and nothing in the tests reached it.

    Building the parameters here rather than in each test means a new setup test exercises that
    path by default instead of by remembering to.
    """

    OPTIONS: ClassVar[Tuple[PostProcessingOptions, ...]] = (
        PostProcessingOptions.COMPUTE_KPIS,
        PostProcessingOptions.WRITE_KPIS_TO_JSON,
        PostProcessingOptions.COMPUTE_CAPEX,
        PostProcessingOptions.COMPUTE_OPEX,
    )

    @classmethod
    def one_day_with_kpis(cls, year: int = 2021, seconds_per_timestep: int = 60) -> SimulationParameters:
        """Return one-day parameters that compute KPIs and costs.

        Args:
            year: the simulation year.
            seconds_per_timestep: the resolution.

        Returns:
            SimulationParameters: one day, with the KPI and cost options enabled.
        """
        parameters = SimulationParameters.one_day_only(year=year, seconds_per_timestep=seconds_per_timestep)
        parameters.post_processing_options.extend(cls.OPTIONS)
        return parameters


def get_number_of_outputs(list_of_components: list) -> int:
    """Calculates the number of outputs for a list of components or individual outputs.

    Iterates over the list and, for each entry, either counts it as a single
    output (if it is a ``ComponentOutput`` instance) or sums the length of the
    component's ``.outputs`` collection.

    Args:
        list_of_components (list): Mixed list of component instances (which
            expose an ``.outputs`` attribute) and individual ``ComponentOutput``
            objects.

    Returns:
        int: Total number of outputs across all entries in the list.
    """
    number_of_outputs = 0
    for component in list_of_components:
        if isinstance(component, ComponentOutput):
            number_of_outputs = number_of_outputs + 1
        else:
            number_of_outputs = number_of_outputs + len(component.outputs)
    return number_of_outputs


def add_global_index_of_real_components(
    list_of_components: list, number_of_fake_inputs: int
) -> None:
    """Sets the global index for real component outputs.

    Iterates over each component in the list and assigns a global_index to every output it declared, in the order of
    its ``.outputs`` collection, as the simulator registers them. This includes outputs a component declared through a
    helper part it composes (such as :class:`hisim.part_load.PartLoadControl`), which no attribute of the component
    holds.

    The indices start after the offset provided by number_of_fake_inputs,
    ensuring fake-component indices come first in the global ordering.

    Args:
        list_of_components (list): List of component instances, each with its ``.outputs`` collection.
        number_of_fake_inputs (int): Offset to apply so fake-component indices
            come first in the global index sequence.

    Returns:
        None: Mutates the global_index attribute on each ComponentOutput.
    """
    counter = 0 + number_of_fake_inputs
    for component in list_of_components:
        for output in component.outputs:
            output.global_index = counter
            counter = counter + 1


def add_global_index_of_fake_components(list_of_components: list) -> int:
    """Sets global index for fake components (individual ComponentOutput objects).

    Assigns sequential global_index values starting from 0 to each ComponentOutput
    in the list. These "fake components" are individual ComponentOutput objects
    rather than full component instances.

    Args:
        list_of_components (list): List of ComponentOutput instances (referred to
            as "fake components" in this context).

    Returns:
        int: Count of fake components (used as offset by callers to ensure
            non-overlapping indices with real components).
    """
    number_of_fake_inputs = len(list_of_components)
    counter = 0
    for component in list_of_components:
        component.global_index = counter
        counter = counter + 1
    return number_of_fake_inputs


def add_global_index_of_components(list_of_components: list) -> None:
    """Adds the global index of components.

    Partitions the mixed list into real components (full component instances) and
    fake components (individual ComponentOutput objects). Assigns non-overlapping
    global indices starting with fake components (indices 0 to n-1), followed by
    real component outputs (indices n onwards).

    Args:
        list_of_components (list): Mixed list of component instances and
            ComponentOutput objects.

    Returns:
        None: Mutates the global_index attribute on each ComponentOutput.
    """
    list_of_real_components = []
    list_of_fake_components = []
    for component in list_of_components:
        if isinstance(component, ComponentOutput):
            list_of_fake_components.append(component)
        else:
            list_of_real_components.append(component)
    number_of_fake_inputs = add_global_index_of_fake_components(list_of_fake_components)
    add_global_index_of_real_components(
        list_of_components=list_of_real_components,
        number_of_fake_inputs=number_of_fake_inputs,
    )
