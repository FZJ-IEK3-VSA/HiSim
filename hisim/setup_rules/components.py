"""The component-build rules: what the components of a run, as added and as connected, must obey before the first step.

The rules read one of two subjects of the build stage. Five of them read the components as they were added
(``AddedSystem``), which the simulator hands over first thing in ``Simulator.run_all_timesteps``, before it prepares
a result directory or a component: an empty run (``CB-14``), a class without one kind (``CB-01``), a name added twice
(``CB-02``), and, when lifecycle costs were requested, a class that declares no cost role (``CB-08``) or a ``PRICED``
class nothing can describe (``CB-09``). The others read the components with their inputs connected
(``BuiltSystem``), before the first step: two outputs of one name (``CB-03``), a connection whose load types or units
differ (``CB-04``, ``CB-05``), a mandatory input left unconnected (``CB-06``), an automatic connection that finds
nothing (``CB-07``), an input resolved to an output no component owns (``CB-10``), a kind the evaluation order does
not place (``CB-11``), and, only logged, the cycles the evaluation order has to break (``CB-12``, ``CB-13``).

The simulator used to refuse each of these at the first one, when the component was added, when its inputs were
connected or at the first step; here every rule reports every fault, and the registry raises them together. The
cost rules keep the errors they raised before they moved, ``UndeclaredCostRelevanceError`` and
``UnpriceableComponentError``; the other rules raise ``SetupRuleViolation``, a ``ValueError`` as before. The
component layer, the simulator and the cost adapter are imported when a rule runs, not with the module, so that
collecting the rules imports no component module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, FrozenSet, List, Optional, Sequence, Set, Type

from hisim import loadtypes as lt
from hisim.setup_rules.rule import Fault, RuleStage, SetupRule, SetupRuleViolation
from hisim.setup_rules.subjects import AddedSystem, BuiltSystem

if TYPE_CHECKING:
    from hisim.component import Component
    from hisim.evaluation_order import EvaluationOrder


class ConnectionRules:
    """The codes of the rules that judge a connection, which every rule reading the wiring of a built system requires.

    A connection between ports whose load types (``CB-04``) or units (``CB-05``) differ, or a mandatory input left
    unconnected (``CB-06``), is the first fault of such a system: the fluid-loop and energy-port rules would follow
    the broken wiring and report its consequences as faults of their own. Requiring these codes skips them, visibly,
    so the build refuses a broken connection alone, as it did when the connection itself raised. For example,
    ``FL-02`` declares ``REQUIRES = frozenset({"FL-01"}) | ConnectionRules.CODES``.
    """

    #: The codes of the three connection rules.
    CODES: ClassVar[FrozenSet[str]] = frozenset({"CB-04", "CB-05", "CB-06"})


class UndeclaredCostRelevanceError(SetupRuleViolation):
    """A lifecycle-cost run with a component class that declares no ``cost_relevance``, refused before it prepares anything.

    The registry raises it when every refusing fault of the components as added comes from the setup rule ``CB-08``
    (:class:`CostRelevanceDeclared`), so a year-long simulation refuses in its first second instead of failing in
    the post-processing. It is a ``ValueError``, as it was before the rule moved here. The bridge does not raise it;
    an undeclared component reaching the bridge becomes an ``UnresolvedSubject``.
    """


class UnpriceableComponentError(SetupRuleViolation):
    """A lifecycle-cost run with a ``PRICED`` component class nothing can describe, refused before it prepares anything.

    The registry raises it when every refusing fault of the components as added comes from the setup rule ``CB-09``
    (:class:`PricedHasCostFacts`). The fix differs from an undeclared class's: the component needs a
    ``get_cost_facts`` hook or an adapter entry, and usually a ``devices_<COUNTRY>.json`` row. It is a ``ValueError``,
    as it was before the rule moved here.
    """


class OneKindBase(SetupRule[AddedSystem]):
    """What it checks: the class of every component inherits exactly one of the five kind base classes of ``hisim.component``.

    The kinds are data source, L2 controller, L1 controller, physics and meter
    (``system_docs/component_design_principles.md`` §A); a class states its kind by inheriting one of
    ``DataSourceComponent``, ``L2ControllerComponent``, ``L1ControllerComponent``, ``PhysicsComponent`` and
    ``MeterComponent``. A class added several times is reported once, at its first component.

    Why: a pass evaluates the components kind by kind; a component without a kind, or with two, has no place in it.

    Example of a fault: ``The component class tests.Probe must inherit exactly one kind base class of
    ['DataSourceComponent', 'L2ControllerComponent', 'L1ControllerComponent', 'PhysicsComponent', 'MeterComponent']
    from hisim.component; it inherits none.``

    How to fix: derive the class from the one kind base class that states its role, for example
    ``class Battery(PhysicsComponent)``, or ``class Transformer(StatelessComponent, PhysicsComponent)``.
    """

    CODE = "CB-01"
    NAME = "one-kind-base"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: AddedSystem) -> Sequence[Fault]:
        """Return one fault per component class that inherits no kind base class, or more than one."""
        # The kinds live with the component layer, which collecting the rules must not import (the module docstring).
        from hisim.component import ComponentKinds  # pylint: disable=import-outside-toplevel

        faults: List[Fault] = []
        seen: Set[type] = set()
        for component in subject.components:
            component_class = type(component)
            if component_class in seen:
                continue
            seen.add(component_class)
            problem = ComponentKinds.problem(component_class)
            if problem is not None:
                faults.append(self.fault(problem, component=component.component_name))
        return faults


class UniqueName(SetupRule[AddedSystem]):
    """What it checks: no two components of a run share one name (``component_id.key``).

    Why: a component's name is its identity: an input names its source by it, and every result column carries it.
    Two components of one name would make every connection to either of them ambiguous.

    Example of a fault: ``duplicate component name : Battery``.

    How to fix: give the second component another name through its configuration's ``component_id``, for example
    ``ComponentID(name="Battery2")``.
    """

    CODE = "CB-02"
    NAME = "unique-name"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: AddedSystem) -> Sequence[Fault]:
        """Return one fault per component whose name an earlier component has."""
        faults: List[Fault] = []
        seen: Set[str] = set()
        for component in subject.components:
            if component.component_name in seen:
                faults.append(self.fault(f"duplicate component name : {component.component_name}", component=component.component_name))
            seen.add(component.component_name)
        return faults


class UniqueOutputKey(SetupRule[BuiltSystem]):
    """What it checks: no two registered outputs share one full name (component name and field name).

    The outputs are every result column of the run, those a dynamic component grew while it was connected included.

    Why: an input finds its source output by that name, and the result table names its column by it; two outputs of
    one name would both answer one input and write two columns that cannot be told apart.

    Example of a fault: ``Trying to register the same key twice: Building # TemperatureMeanThermalMass. Check if
    more than one building is being modeled.``

    How to fix: give one of the components another name, or let the component declare the output once.
    """

    CODE = "CB-03"
    NAME = "unique-output-key"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per registered output whose full name an earlier output has."""
        faults: List[Fault] = []
        seen: Set[str] = set()
        for output in subject.outputs:
            if output.full_name in seen:
                faults.append(
                    self.fault(
                        f"Trying to register the same key twice: {output.full_name}. Check if more than one building is being modeled.",
                        component=output.component_name,
                        port=output.field_name,
                    )
                )
            seen.add(output.full_name)
        return faults


class LoadTypeAgrees(SetupRule[BuiltSystem]):
    """What it checks: a connected input and the output it reads share one load type; ``LoadTypes.ANY`` on either side matches any.

    Why: the load type says what a value is (electricity, heating, a temperature); an input that reads an output of
    another load type reads a quantity it does not model. ``ANY`` is the legitimate wildcard of control and state
    signals.

    Example of a fault: ``The input ThermalPowerInput (cp: Tank, load type: LoadTypes.HEATING) and output
    ElectricityOutput (cp: Battery, load type: LoadTypes.ELECTRICITY) do not have the same load type! ...``

    How to fix: connect the input to the output that carries its quantity, or align the two port declarations
    (temperature signals are ``TEMPERATURE``, water mass flows are ``WARM_WATER``).
    """

    CODE = "CB-04"
    NAME = "load-type-agrees"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per connected input whose load type differs concretely from its output's."""
        faults: List[Fault] = []
        for component in subject.components:
            for component_input in component.inputs:
                output = component_input.source_output
                if output is None or component_input.loadtype == output.load_type or lt.LoadTypes.ANY in (component_input.loadtype, output.load_type):
                    continue
                faults.append(
                    self.fault(
                        f"The input {component_input.field_name} (cp: {component_input.component_name}, "
                        f"load type: {component_input.loadtype}) and output {output.field_name} "
                        f"(cp: {output.component_name}, load type: {output.load_type}) "
                        f"do not have the same load type! Align the two port declarations (see the "
                        f"LoadTypes canonicalization convention: temperature signals are TEMPERATURE, "
                        f"water mass flows are WARM_WATER).",
                        component=component_input.component_name,
                        port=component_input.field_name,
                    )
                )
        return faults


class UnitAgrees(SetupRule[BuiltSystem]):
    """What it checks: a connected input and the output it reads share one unit, unless one of them is ``Units.ANY``.

    A unit ``Units.ANY`` on one side only is connected with a warning that the units might not fit, logged by the
    connection (``ComponentWrapper.connect_inputs``), not by this rule.

    Why: a value read in another unit than it was written in is wrong by the factor between the units, silently: a
    power in kW read as W is a thousand times too small.

    Example of a fault: ``The input ElectricityInput (cp: Battery, unit: Units.WATT) and output
    ElectricityOutput(cp: PV, unit: Units.KILOWATT) do not have the same unit!``

    How to fix: declare both ports in the same unit, converting inside the component that publishes the value.
    """

    CODE = "CB-05"
    NAME = "unit-agrees"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per connected input whose unit differs concretely from its output's."""
        faults: List[Fault] = []
        for component in subject.components:
            for component_input in component.inputs:
                output = component_input.source_output
                if output is None or component_input.unit == output.unit or lt.Units.ANY in (component_input.unit, output.unit):
                    continue
                faults.append(
                    self.fault(
                        f"The input {component_input.field_name} (cp: {component_input.component_name}, unit: {component_input.unit}) and "
                        f"output {output.field_name}(cp: {output.component_name}, unit: {output.unit}) "
                        f"do not have the same unit!",
                        component=component_input.component_name,
                        port=component_input.field_name,
                    )
                )
        return faults


class MandatoryInputConnected(SetupRule[BuiltSystem]):
    """What it checks: every mandatory input is connected to an output, unless it is marked ``allow_unconnected_mandatory``.

    An input is connected when the output it names, by its source component's name and the output's field name, is
    registered. A mandatory input marked ``allow_unconnected_mandatory`` names an output its source publishes only in
    some configurations; it stays unconnected with a warning, logged by the connection
    (``ComponentWrapper.connect_inputs``), not by this rule.

    Why: a mandatory input is one the component cannot compute without; left unconnected it reads zero in every step,
    and the component computes a plausible wrong answer.

    Example of a fault: ``The ComponentInput ThermalPowerInput (cp: Tank, unit: Units.WATT) is not connected to any
    ComponentOutput. ... Likely, no match was found between Boiler and [...]``

    How to fix: connect the input (``connect_input`` or a default connection) to an output the run has, check the
    spelling of the source's name and field, or add the source component.
    """

    CODE = "CB-06"
    NAME = "mandatory-input-connected"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per mandatory input that is connected to no output and may not stay so."""
        faults: List[Fault] = []
        for component in subject.components:
            for component_input in component.inputs:
                if not component_input.is_mandatory or component_input.source_output is not None or component_input.allow_unconnected_mandatory:
                    continue
                faults.append(
                    self.fault(
                        f"The ComponentInput {component_input.field_name} (cp: {component_input.component_name}, "
                        f"unit: {component_input.unit}) is not connected to any ComponentOutput. "
                        "You could run debug mode (logging_level=4) to check all inputs, outputs and connections. "
                        f"Likely, no match was found between {component_input.src_object_name} and "
                        f"{[output.component_name for output in subject.outputs]} & "
                        f"and between {component_input.src_field_name} and {[output.field_name for output in subject.outputs]}.",
                        component=component_input.component_name,
                        port=component_input.field_name,
                    )
                )
        return faults


class AutoConnectPossible(SetupRule[BuiltSystem]):
    """What it checks: a component added with ``connect_automatically=True`` declares default connections, and one from a component of the run.

    A plain component declares its default connections by source class (``add_default_connections``); a dynamic
    component declares them by class too, or by the ports a source declares (``DynamicComponent.default_connections_for``,
    the electricity meter), and never reads itself.

    Why: asking for automatic connection states that the component reads something of the run; when nothing in the
    run matches, its inputs stay unconnected, and a component that should be wired is silently not.

    Example of a fault: ``No component in the system setup matches the default connections of HeatPumpController.``

    How to fix: add the component the default connections name, connect the component by hand and add it with
    ``connect_automatically=False``, or declare the missing default connection in the component.
    """

    CODE = "CB-07"
    NAME = "auto-connect-possible"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per automatically connected component that declares no default connection, or none the run can apply."""
        # The dynamic component lives with the component layer, which collecting the rules must not import.
        from hisim.dynamic_component import DynamicComponent  # pylint: disable=import-outside-toplevel

        faults: List[Fault] = []
        for target in subject.components:
            if target.component_name not in subject.connects_automatically:
                continue
            if isinstance(target, DynamicComponent):
                declares = target.declares_default_connections()
                matches = any(source is not target and target.default_connections_for(source) for source in subject.components)
            else:
                declares = bool(target.default_connections)
                matches = any(source.get_classname() in target.default_connections for source in subject.components)
            if not declares:
                faults.append(
                    self.fault(
                        f"Automatic connection does not work for {target.component_name} because no default "
                        "connections were found. Please check if a connection is needed and if yes, create the missing "
                        "default connection in your component.",
                        component=target.component_name,
                    )
                )
            elif not matches:
                faults.append(
                    self.fault(f"No component in the system setup matches the default connections of {target.component_name}.", component=target.component_name)
                )
        return faults


class CostDeclarationRule(SetupRule[AddedSystem]):
    """The shared base of the two cost-declaration rules: when they apply, and which classes the cost model refuses.

    It declares no ``CODE``, so it is no rule itself. Both rules apply only to a run that requests
    ``COMPUTE_LIFECYCLE_COSTS`` or ``LIFECYCLE_COST_REPORT``: components are free to be undeclared in a run that never
    asks what anything costs. Both judge what is visible without the cost database (roadmap/cost_spec.md §9.1 and
    §9.2); whether the facts a source produces can be priced against a country's database stays with the D7 check in
    the post-processing.
    """

    @staticmethod
    def requested(subject: AddedSystem) -> bool:
        """Return whether the run requests lifecycle costs, through ``COMPUTE_LIFECYCLE_COSTS`` or ``LIFECYCLE_COST_REPORT``.

        ``LIFECYCLE_COST_REPORT`` implies the computation in the post-processing, so a rule that looked only at the
        first option would let a report run reach the bridge and fail there, after the whole simulation.
        """
        # The options live beside the simulation parameters, which collecting the rules need not import.
        from hisim.postprocessingoptions import PostProcessingOptions  # pylint: disable=import-outside-toplevel

        wanted = (PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS, PostProcessingOptions.LIFECYCLE_COST_REPORT)
        return any(option in subject.post_processing_options for option in wanted)

    @staticmethod
    def has_cost_facts_source(component_class: type) -> bool:
        """Return whether anything can produce ``ComponentCostFacts`` for a class: its own ``get_cost_facts``, or an adapter entry.

        It applies the precedence ``adapter.extract_cost_facts`` applies in the post-processing, hook first, table
        second, so that this refusal and that one never disagree about which classes have a facts source. "Its own"
        means "not the one ``Component`` defines", so a class that inherits a working hook from a component base class
        of its own has one. For example, ``Battery`` implements ``get_cost_facts`` and has a source; a ``PRICED`` class
        outside ``hisim.components`` without the hook has none.

        Args:
            component_class: A component's class.

        Returns:
            True when the class implements the hook or the adapter table knows its name.
        """
        # The adapter reads the KPI structures and the component layer, which collecting the rules must not import.
        from hisim.component import Component  # pylint: disable=import-outside-toplevel
        from hisim.economics.adapter import FactsExtractors  # pylint: disable=import-outside-toplevel

        hook = getattr(component_class, "get_cost_facts", None)
        if hook is not None and hook is not Component.get_cost_facts:
            return True
        return component_class.__name__ in FactsExtractors.BY_CLASS_NAME

    @classmethod
    def refusal(cls, component_class: type) -> Optional[Type[SetupRuleViolation]]:
        """Return the error a lifecycle-cost run refuses a class with, or ``None`` for a class it lets through.

        The one statement of the rule for one class, so that the two rules and anything that asks the same question
        ahead of a run (a test over the classes a recorded energy system wires) cannot disagree. A class that keeps the
        ``UNDECLARED`` default, or carries no ``cost_relevance`` at all, gives ``UndeclaredCostRelevanceError``; a
        ``PRICED`` class without a facts source (:meth:`has_cost_facts_source`) gives ``UnpriceableComponentError``;
        ``Battery`` gives ``None``.

        Args:
            component_class: A component's class.

        Returns:
            The error class, or ``None``.
        """
        # The cost roles live with the cost model, which collecting the rules need not import.
        from hisim.economics.facts import CostRelevance  # pylint: disable=import-outside-toplevel

        relevance = getattr(component_class, "cost_relevance", CostRelevance.UNDECLARED)
        if relevance is CostRelevance.UNDECLARED:
            return UndeclaredCostRelevanceError
        if relevance is CostRelevance.PRICED and not cls.has_cost_facts_source(component_class):
            return UnpriceableComponentError
        return None

    @classmethod
    def classes_refused_as(cls, subject: AddedSystem, error: Type[SetupRuleViolation]) -> List[type]:
        """Return the classes of the components that :meth:`refusal` refuses with ``error``, each once, in the order added."""
        found: List[type] = []
        for component in subject.components:
            component_class = type(component)
            if component_class not in found and cls.refusal(component_class) is error:
                found.append(component_class)
        return found


class CostRelevanceDeclared(CostDeclarationRule):
    """What it checks: in a lifecycle-cost run, every component class declares its ``cost_relevance`` in its own body.

    The run requests lifecycle costs through ``COMPUTE_LIFECYCLE_COSTS`` or ``LIFECYCLE_COST_REPORT``; a class that
    keeps the base-class default ``UNDECLARED`` declares nothing. All such classes are listed in one fault.

    Why: the cost adapter reads the declaration and infers nothing; an undeclared component becomes an unresolved
    subject in the post-processing and aborts the cost evaluation, after the whole simulation (roadmap/cost_spec.md
    §9.2). Refusing it before the run saves the hours of simulation.

    Example of a fault: ``Lifecycle cost computation was requested, but 1 component class(es) in this simulation
    declare no cost_relevance, ... - UndeclaredDevice (module tests.x) declares no cost_relevance, ...``

    How to fix: declare ``cost_relevance = CostRelevance.PRICED``, ``CostRelevance.METER`` or
    ``CostRelevance.FREE_OF_COST`` in the class body.
    """

    CODE = "CB-08"
    NAME = "cost-relevance-declared"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    VIOLATION = UndeclaredCostRelevanceError

    def check(self, subject: AddedSystem) -> Sequence[Fault]:
        """Return one fault listing every undeclared component class, when the run requests lifecycle costs."""
        if not self.requested(subject):
            return []
        undeclared = self.classes_refused_as(subject, UndeclaredCostRelevanceError)
        if not undeclared:
            return []
        # The sentence is shared with the cost bridge, which reports the same defect the same way.
        from hisim.economics.facts import describe_undeclared_class  # pylint: disable=import-outside-toplevel

        bullets = "\n".join(f"  - {describe_undeclared_class(component_class)}" for component_class in undeclared)
        return [
            self.fault(
                f"Lifecycle cost computation was requested, but {len(undeclared)} "
                "component class(es) in this simulation declare no cost_relevance, so the cost model "
                "cannot describe them (roadmap/cost_spec.md §9.2). The run is refused before the first "
                "timestep rather than after the last one.\n" + bullets
            )
        ]


class PricedHasCostFacts(CostDeclarationRule):
    """What it checks: in a lifecycle-cost run, every ``PRICED`` component class has a source of cost facts.

    A source is a ``get_cost_facts`` of the class's own, or an entry in ``hisim.economics.adapter.FactsExtractors``
    (:meth:`CostDeclarationRule.has_cost_facts_source`). All such classes are listed in one fault. It waits for
    ``CB-08``: an undeclared class is the first defect to fix, as before the rule moved here.

    Why: a ``PRICED`` component nothing can describe becomes an unresolved subject in the post-processing, and the
    cost evaluation aborts after the whole simulation (roadmap/cost_spec.md §9.1, decision D7).

    Example of a fault: ``Lifecycle cost computation was requested, but 1 component class(es) in this simulation
    declare cost_relevance PRICED while nothing can produce cost facts for them, ... - PricedWithoutFacts (module
    tests.x) declares cost_relevance PRICED, but it implements no get_cost_facts() ...``

    How to fix: implement ``get_cost_facts()`` on the class or register an extractor for it in the adapter, and
    usually add a ``devices_<COUNTRY>.json`` row.
    """

    CODE = "CB-09"
    NAME = "priced-has-cost-facts"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # An undeclared class is reported first, alone: before the rules moved, the run refused it before it looked at PRICED.
    REQUIRES = frozenset({"CB-08"})
    VIOLATION = UnpriceableComponentError

    def check(self, subject: AddedSystem) -> Sequence[Fault]:
        """Return one fault listing every PRICED component class without a facts source, when the run requests lifecycle costs."""
        if not self.requested(subject):
            return []
        unpriceable = self.classes_refused_as(subject, UnpriceableComponentError)
        if not unpriceable:
            return []
        # The sentence is shared with the cost adapter, which reports the same defect the same way.
        from hisim.economics.facts import describe_unpriceable_class  # pylint: disable=import-outside-toplevel

        bullets = "\n".join(f"  - {describe_unpriceable_class(component_class)}" for component_class in unpriceable)
        return [
            self.fault(
                f"Lifecycle cost computation was requested, but {len(unpriceable)} "
                "component class(es) in this simulation declare cost_relevance PRICED while nothing "
                "can produce cost facts for them, so the evaluation would abort after the run "
                "(roadmap/cost_spec.md §9.1/§9.2, decision D7). The run is refused before the first timestep "
                "rather than after the last one.\n" + bullets
            )
        ]


class SourceOutputRegistered(SetupRule[BuiltSystem]):
    """What it checks: every connected input reads an output that a component of the run owns.

    All such inputs are listed in one fault, by their full names.

    Why: the evaluation order places a component after the components whose outputs it reads; an output no component
    of the run owns has no position, so the constraint of the input that reads it would vanish without a word.

    Example of a fault: ``The inputs ['C0 # FromP1'] resolved to outputs that no added component registered, so the
    evaluation order cannot place their sources.``

    How to fix: connect the input through ``connect_input`` and the simulator, which resolve it to a registered
    output, rather than setting its ``source_output`` by hand; or add the component that owns the output.
    """

    CODE = "CB-10"
    NAME = "source-output-registered"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault listing every input whose source output no component of the run owns."""
        owned = {id(output) for component in subject.components for output in component.outputs}
        unregistered = [
            component_input.fullname
            for component in subject.components
            for component_input in component.inputs
            if component_input.source_output is not None and id(component_input.source_output) not in owned
        ]
        if not unregistered:
            return []
        return [
            self.fault(
                f"The inputs {unregistered} resolved to outputs that no added component registered, so the "
                "evaluation order cannot place their sources."
            )
        ]


class KindInEvaluationOrder(SetupRule[BuiltSystem]):
    """What it checks: the kind of every component is one of the kinds the evaluation order places, ``Simulator.EVALUATION_ORDER_OF_KINDS``.

    All components whose kind the order lacks are listed in one fault, by the name of their kind. A component whose
    class has no one kind is ``CB-01``'s fault and is left out here.

    Why: a pass evaluates the components kind by kind in that order; a kind it does not list has no place in a pass.
    The kind base classes (``ComponentKinds.BASES``) and the order are two lists of the code, and this rule holds them
    in step.

    Example of a fault: ``Not kind base classes, so not placeable in a pass: ['MeterComponent'].``

    How to fix: add the kind to ``Simulator.EVALUATION_ORDER_OF_KINDS`` at the place a pass evaluates it.
    """

    CODE = "CB-11"
    NAME = "kind-in-evaluation-order"
    STAGE = RuleStage.BUILD
    ENFORCED = True
    # A component's kind is looked up; a class without one kind is the fault of that rule.
    REQUIRES = frozenset({"CB-01"})

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault listing the kinds of the components that the evaluation order does not place."""
        # The kinds and the order live with the component layer and the simulator, which collecting the rules must not import.
        from hisim.component import ComponentKinds  # pylint: disable=import-outside-toplevel
        from hisim.simulator import Simulator  # pylint: disable=import-outside-toplevel

        unknown = [
            ComponentKinds.of(type(component)).__name__
            for component in subject.components
            if ComponentKinds.problem(type(component)) is None and ComponentKinds.of(type(component)) not in Simulator.EVALUATION_ORDER_OF_KINDS
        ]
        if not unknown:
            return []
        return [self.fault(f"Not kind base classes, so not placeable in a pass: {unknown}.")]


class EvaluationOrderRule(SetupRule[BuiltSystem]):
    """The shared base of the two rules of the evaluation order's cycles: the order they both read.

    It declares no ``CODE``, so it is no rule itself. Both rules only log: a cycle is evaluated as one block in the
    order it was added in, which may cost passes, and the run goes on.
    """

    @staticmethod
    def order_of(subject: BuiltSystem) -> Optional[EvaluationOrder]:
        """Return the evaluation order of the system's components, as the run computes it, or ``None`` when a class has no one kind.

        Example: a controller that commands a device and reads a store, added store, controller, device, gives the
        positions (1, 2, 0) and no cycle. A class without one kind is ``CB-01``'s fault; the order cannot be computed
        without it, and the rules report nothing.

        Args:
            subject: The built system.

        Returns:
            The order with its cycles (``Simulator.evaluation_order_of``), or ``None``.
        """
        # The order lives with the simulator, which collecting the rules must not import (the module docstring).
        from hisim.component import ComponentKinds  # pylint: disable=import-outside-toplevel
        from hisim.simulator import Simulator  # pylint: disable=import-outside-toplevel

        if any(ComponentKinds.problem(type(component)) is not None for component in subject.components):
            return None
        order: EvaluationOrder = Simulator.evaluation_order_of(subject.components)
        return order

    @staticmethod
    def names(components: Sequence[Component], cycle: Sequence[int]) -> str:
        """Return the names of the components at the positions of a cycle, joined by commas: ``P0, P2``."""
        return ", ".join(components[position].component_name for position in cycle)


class NoPhysicsCycle(EvaluationOrderRule):
    """What it checks: the physics components an L1 controller requires to be evaluated before the other physics it reads form no cycle.

    Every device an L1 controller commands goes before the other physics the controller reads
    (``hisim/evaluation_order.py``, ``PhysicsOrder``). When those constraints form a cycle, the physics of the cycle
    keep the order they were added in, and the rule logs one warning per cycle; it refuses nothing.

    Why: a controller whose store is evaluated before its device pairs the device's report with the store's answer of
    the pass before; its iteration may circle its fixed point instead of settling.

    Example of a fault: ``The physics components Tank, Boiler require one another to be evaluated first: each is a
    device of an L1 controller that reads another of them. ...``

    How to fix: let one of the controllers read a component that is not the other's device, or merge the two
    controllers, so that the order of the devices is no longer circular.
    """

    CODE = "CB-12"
    NAME = "no-physics-cycle"
    STAGE = RuleStage.BUILD
    ENFORCED = False
    # The order reads every component's kind and the position of every source output.
    REQUIRES = frozenset({"CB-10", "CB-11"})

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per cycle of physics components, naming them."""
        order = self.order_of(subject)
        if order is None:
            return []
        return [
            self.fault(
                f"The physics components {self.names(subject.components, cycle)} require one another to be evaluated first: each is a device "
                "of an L1 controller that reads another of them. They keep the order they were added in, so a "
                "controller among them may pair its device's report with a store's answer of the pass before, "
                "and its iteration may circle instead of settling."
            )
            for cycle in order.physics_cycles
        ]


class NoL2Cycle(EvaluationOrderRule):
    """What it checks: the L2 controllers read one another in no cycle.

    An L2 controller that reads another L2 controller's output is evaluated after it (``hisim/evaluation_order.py``,
    ``L2Order``). L2 controllers that read one another in a cycle keep the order they were added in, and the rule
    logs one warning per cycle; it refuses nothing.

    Why: each L2 controller of the cycle that reads a later one applies that one's signal of the pass before, so the
    iteration may need more passes, or circle instead of settling.

    Example of a fault: ``The L2 controllers EnergyManager, HeatingCurve read one another in a cycle. ...``

    How to fix: let only one direction carry a signal, or merge the two controllers.
    """

    CODE = "CB-13"
    NAME = "no-l2-cycle"
    STAGE = RuleStage.BUILD
    ENFORCED = False
    # The order reads every component's kind and the position of every source output.
    REQUIRES = frozenset({"CB-10", "CB-11"})

    def check(self, subject: BuiltSystem) -> Sequence[Fault]:
        """Return one fault per cycle of L2 controllers, naming them."""
        order = self.order_of(subject)
        if order is None:
            return []
        return [
            self.fault(
                f"The L2 controllers {self.names(subject.components, cycle)} read one another in a cycle. They keep the order they were added "
                "in, so each of them that reads a later one applies its signal of the pass before, and the "
                "iteration may need more passes or circle instead of settling."
            )
            for cycle in order.l2_cycles
        ]


class NotEmpty(SetupRule[AddedSystem]):
    """What it checks: the run has at least one component, and its components registered at least one output.

    Why: a run without a component or without an output computes nothing and writes an empty result table; that is a
    setup that forgot to add its components, not a result.

    Example of a fault: ``Not a single component was defined. Quitting.``; ``Not a single column was defined.``

    How to fix: add the components in the setup function (``my_sim.add_component(...)``), or list them under
    ``components`` in the energy-system file.
    """

    CODE = "CB-14"
    NAME = "not-empty"
    STAGE = RuleStage.BUILD
    ENFORCED = True

    def check(self, subject: AddedSystem) -> Sequence[Fault]:
        """Return a fault for a run without a component, or else for one without an output."""
        if not subject.components:
            return [self.fault("Not a single component was defined. Quitting.")]
        if not subject.outputs:
            return [self.fault("Not a single column was defined.")]
        return []
