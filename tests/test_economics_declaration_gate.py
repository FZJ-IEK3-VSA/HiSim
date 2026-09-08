"""Tests for the §9.2 completeness check: a lifecycle-cost run refuses an undeclared fleet.

Every `Component` subclass must declare `cost_relevance`, because the cost adapter reads the
declaration and infers nothing: a class that declares none is `UNDECLARED`, becomes an
`UnresolvedSubject` in the postprocessing bridge and aborts the evaluation under decision D7. That
abort is correct but arrives late — after a full simulation — so cost_spec.md §9.2 asks for the
same refusal "at simulation start, not end". `Simulator.check_cost_declarations` is that refusal,
and this file pins both halves of its contract: it fires when lifecycle costs were requested, and
it stays out of the way when they were not.

**Two failure paths, deliberately both alive.** The pre-run check is a convenience, not the
enforcement: it can only see the components a run registered, so it is the bridge's D7 path that
remains the actual guarantee (a component could be added to a run after the check, and an
`economic_inputs.json` re-priced later never passes through a simulator at all). Hence
`TestBridgeStillFailsOnUndeclaredComponents` here as well — if the pre-run check were ever the only
one, removing it would silently restore the leniency §9.2 forbids.

**Error class.** A failure in this file is a *fail-loudly* regression, never a wrong number: it
means an undeclared component can once again reach a cost result, where it contributes nothing and
says nothing — the silent omission the whole §9.2 declaration exists to prevent.
"""

# clean

import datetime
from typing import Any, cast

import pandas as pd
import pytest

from hisim.component_wrapper import ComponentWrapper
from hisim.components.advanced_battery_bslib import Battery
from hisim.components.electricity_meter import ElectricityMeter
from hisim.components.generic_pv_system import PVSystem
from hisim.components.sumbuilder import SumBuilderForTwoInputs
from hisim.components.tariff_provider import TariffProvider
from hisim.economics.bridge import build_evaluation_inputs
from hisim.economics.facts import (
    CostRelevance,
    UndeclaredCostRelevanceError,
    UnpriceableComponentError,
)
from hisim.postprocessingoptions import PostProcessingOptions
from hisim.simulationparameters import SimulationParameters
from hisim.simulator import Simulator

pytestmark = pytest.mark.base


class UndeclaredDevice:
    """A component class carrying no cost role — the defect both failure paths must catch.

    `UNDECLARED` is spelled out rather than inherited so the stub needs no `Component` base class:
    the check and the adapter both read only `type(component).cost_relevance`, and an explicit
    `UNDECLARED` is indistinguishable from the base-class default they are written against.

    Living here rather than under `hisim.components` is equally deliberate — the fleet-wide
    contract test in `tests/test_economics_adapter_contract.py` scans that package and would
    otherwise fail on this very stub.
    """

    cost_relevance = CostRelevance.UNDECLARED

    def __init__(self, component_name: str = "UndeclaredDevice") -> None:
        """The two attributes the bridge reads off a component before classifying it."""
        self.component_name = component_name
        self.config = None


class _Wrapper:
    """Stand-in for `ComponentWrapper`: both the check and the bridge only read `.my_component`.

    Registering real components through `Simulator.add_component` would drag in configs, a result
    directory and the connection machinery, none of which the declaration check touches. Appending
    wrappers directly keeps the test about the check.
    """

    def __init__(self, component: Any) -> None:
        self.my_component = component


def _register(simulator: Simulator, component: Any) -> None:
    """Puts one stand-in wrapper on the simulator's component list.

    `wrapped_components` is typed `List[ComponentWrapper]` and `_Wrapper` deliberately is not one,
    so the cast is where that shortcut is recorded: the declaration check reads nothing but
    `wrapper.my_component`, and building a real wrapper would add construction cost without adding
    coverage.

    Args:
        simulator: The simulator to register with; its list is mutated.
        component: The component object the wrapper should carry.
    """
    simulator.wrapped_components.append(cast(ComponentWrapper, _Wrapper(component)))


class _SimulationParameters:
    """The attributes `build_evaluation_inputs` reads, for the bridge half of this file.

    A one-day 2024 run at quarter-hour resolution; the bridge needs no more than the year, the
    timestep, the date range and a result directory it never writes to here.
    """

    def __init__(self) -> None:
        self.year = 2024
        self.seconds_per_timestep = 900
        self.start_date = datetime.datetime(2024, 1, 1)
        self.end_date = self.start_date + datetime.timedelta(days=1)
        self.result_directory = ""
        # None means "no setup-declared economics", matching the real SimulationParameters; the
        # database-failure test below replaces it with parameters naming an unreadable path.
        self.economic_parameters: Any = None


def _simulator(*options: PostProcessingOptions) -> Simulator:
    """A simulator whose parameters request exactly the given post-processing options.

    Constructing one touches no file — the module directory and filename are read only when a
    setup function is imported, which never happens here — so the check can be exercised without
    a result directory or a single simulated timestep.
    """
    parameters = SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600)
    parameters.post_processing_options = list(options)
    # Annotated rather than inferred, matching tests/test_simulator_simulation_parameters.py:
    # the constructor is untyped, so mypy sees Any without it.
    simulator: Simulator = Simulator(
        module_directory="system_setups",
        module_filename="does_not_need_to_exist.py",
        my_simulation_parameters=parameters,
    )
    return simulator


def _uninitialized(component_class: Any) -> Any:
    """An instance of a real component class with `__init__` deliberately not run.

    The check reads nothing but `type(component).cost_relevance`, while constructing a real
    component properly needs a config, simulation parameters and a display config. Bypassing
    `__init__` keeps the *class identity* — which is the whole input to the check — without
    building the world around it.

    Args:
        component_class: The `Component` subclass to get an instance of. Typed `Any` because
            `type.__new__` has no signature that accepts an arbitrary class object.

    Returns:
        An instance of that class with none of its attributes set.
    """
    return component_class.__new__(component_class)


class TestPreRunCheckRefusesAnUndeclaredFleet:
    """`check_cost_declarations` when lifecycle costs were requested."""

    @pytest.mark.parametrize(
        "option",
        [PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS, PostProcessingOptions.LIFECYCLE_COST_REPORT],
    )
    def test_an_undeclared_component_aborts_the_run(self, option):
        """Both options that imply the computation refuse the run, naming class and module.

        `LIFECYCLE_COST_REPORT` implies `COMPUTE_LIFECYCLE_COSTS` in postprocessing, so a check
        that only looked at the latter would let a report run reach the bridge and die there —
        exactly the late failure §9.2 replaces.
        """
        simulator = _simulator(option)
        _register(simulator, UndeclaredDevice())

        with pytest.raises(UndeclaredCostRelevanceError) as raised:
            simulator.check_cost_declarations()
        message = str(raised.value)
        assert "UndeclaredDevice" in message  # which class ...
        assert __name__ in message  # ... and which module it lives in
        assert "cost_relevance" in message  # what is missing ...
        assert "PRICED" in message and "METER" in message and "FREE_OF_COST" in message
        assert raised.value.component_classes == (UndeclaredDevice,)

    def test_it_is_a_value_error_so_the_documented_refusal_type_still_holds(self):
        """`run_all_timesteps` documents `ValueError`; the new refusal must not escape that."""
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        _register(simulator, UndeclaredDevice())

        with pytest.raises(ValueError):
            simulator.check_cost_declarations()

    def test_a_fully_declared_fleet_passes(self):
        """Real shipped components, declared in their own class bodies, raise nothing.

        Uses real classes rather than stubs so that the check is shown to agree with the fleet as
        it is actually declared — a `PRICED` device, a `METER` and two `FREE_OF_COST` helpers.
        """
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        for component_class in (Battery, ElectricityMeter, TariffProvider, SumBuilderForTwoInputs):
            _register(simulator, _uninitialized(component_class))

        simulator.check_cost_declarations()  # must not raise

    def test_each_offending_class_is_reported_once(self):
        """Ten instances of one undeclared class are one defect, not ten bullets."""
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        for _ in range(10):
            _register(simulator, UndeclaredDevice())

        with pytest.raises(UndeclaredCostRelevanceError) as raised:
            simulator.check_cost_declarations()
        assert raised.value.component_classes == (UndeclaredDevice,)


class PricedWithoutFacts:
    """A PRICED component class nothing can produce facts for — the second §9.1 defect.

    It implements no `get_cost_facts` and, living outside `hisim.components`, can have no entry in
    the adapter's class-name table either, which is exactly the state the pre-run check has to
    catch: the component would be extracted, found undescribable and turned into an unresolved
    subject, and the D7 check would abort the evaluation — after the whole simulation had run.

    It carries `get_cost_facts` from nowhere on purpose: the check compares the class's method
    against `Component.get_cost_facts`, and a stub that inherits from nothing has no such method
    at all, which is the same answer.
    """

    cost_relevance = CostRelevance.PRICED

    def __init__(self, component_name: str = "PricedWithoutFacts") -> None:
        """The two attributes the check and the bridge read off a component."""
        self.component_name = component_name
        self.config = None


class TestPreRunCheckRefusesAPricedComponentNothingCanDescribe:
    """`check_cost_declarations` on the §9.1 half: PRICED with no hook and no adapter entry."""

    @pytest.mark.parametrize(
        "option",
        [PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS, PostProcessingOptions.LIFECYCLE_COST_REPORT],
    )
    def test_a_priced_component_without_a_facts_source_aborts_the_run(self, option):
        """Catches an unpriceable device costing a full simulation before anyone is told.

        The bridge already refuses such a component under D7, but only after the last timestep. A
        year-long run then spends hours producing exactly the cost report it was started for, minus
        the cost report. The pre-run check answers the same question from the class alone.
        """
        simulator = _simulator(option)
        _register(simulator, PricedWithoutFacts())

        with pytest.raises(UnpriceableComponentError) as raised:
            simulator.check_cost_declarations()

        assert "PricedWithoutFacts" in str(raised.value)
        assert "get_cost_facts" in str(raised.value)
        assert "BY_CLASS_NAME" in str(raised.value)

    def test_it_is_a_value_error_so_the_documented_refusal_type_still_holds(self):
        """`run_all_timesteps` documents ValueError; this refusal must not widen that contract."""
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        _register(simulator, PricedWithoutFacts())

        with pytest.raises(ValueError):
            simulator.check_cost_declarations()

    def test_a_priced_component_with_a_hook_passes(self):
        """A class implementing `get_cost_facts` itself is describable, and must not be refused.

        `Battery` is a real PRICED component with an adapter entry; `PVSystem` implements the hook.
        Both have to pass, because the check mirrors the adapter's hook-first-table-second
        precedence rather than insisting on one of the two.
        """
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        _register(simulator, _uninitialized(PVSystem))
        _register(simulator, _uninitialized(Battery))

        simulator.check_cost_declarations()

    def test_a_priced_component_with_only_an_adapter_entry_passes(self):
        """The table half of the precedence: no hook, but the adapter knows the class name."""
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        _register(simulator, _uninitialized(Battery))

        simulator.check_cost_declarations()

    def test_undeclared_is_reported_before_unpriceable(self):
        """Two different defects, two different fixes — and the undeclared one is reported first.

        A class that declares nothing cannot also be judged on whether it is priceable: naming its
        role is the first thing its author has to do, and the message for the second defect would
        be noise until they have.
        """
        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        _register(simulator, UndeclaredDevice())
        _register(simulator, PricedWithoutFacts())

        with pytest.raises(UndeclaredCostRelevanceError):
            simulator.check_cost_declarations()

    def test_without_the_option_an_unpriceable_component_is_fine(self):
        """A run that never asks what anything costs may hold any component at all."""
        simulator = _simulator(PostProcessingOptions.PLOT_LINE)
        _register(simulator, PricedWithoutFacts())

        simulator.check_cost_declarations()


class TestPreRunCheckStaysOutOfTheWayOtherwise:
    """A run that never asks what anything costs must be unaffected by §9.2."""

    def test_without_the_option_an_undeclared_component_is_fine(self):
        """No lifecycle-cost option, no check: declaration is a cost-model requirement only."""
        simulator = _simulator()
        _register(simulator, UndeclaredDevice())

        simulator.check_cost_declarations()  # must not raise

    def test_an_unrelated_option_does_not_trigger_it(self):
        """Requesting KPIs or CSVs is not requesting lifecycle costs."""
        simulator = _simulator(PostProcessingOptions.COMPUTE_KPIS, PostProcessingOptions.EXPORT_TO_CSV)
        _register(simulator, UndeclaredDevice())

        simulator.check_cost_declarations()  # must not raise


class TestTheCheckRunsBeforeTheTimestepLoop:
    """The point of §9.2's "at simulation start": no hours of simulation before the refusal."""

    def test_run_all_timesteps_refuses_before_touching_anything(self, monkeypatch):
        """The abort happens before the result directory is prepared and before any timestep.

        Both collaborators are replaced by exploding stand-ins rather than counted, because a
        check that ran *after* either of them would already have cost the caller the thing §9.2
        is trying to save: a prepared output directory and, worse, a simulated year.
        """

        def explode(*_args, **_kwargs):
            """Fails the test if the simulation got this far."""
            raise AssertionError("the run started before the declaration check refused it")

        simulator = _simulator(PostProcessingOptions.COMPUTE_LIFECYCLE_COSTS)
        _register(simulator, UndeclaredDevice())
        monkeypatch.setattr(Simulator, "prepare_simulation_directory", explode)
        monkeypatch.setattr(Simulator, "process_one_timestep", explode)

        with pytest.raises(UndeclaredCostRelevanceError):
            simulator.run_all_timesteps()


class TestBridgeStillFailsOnUndeclaredComponents:
    """The bridge's D7 path is the actual guarantee; the pre-run check only makes it early."""

    def test_the_bridge_turns_an_undeclared_component_into_a_blocking_subject(self):
        """Extraction records it as unresolved and the resolution check refuses to price anything.

        This is the path a re-priced `economic_inputs.json` and any run that registered a
        component after the pre-run check take, so it must keep failing on its own.
        """
        from hisim.economics.database import CostDatabase
        from hisim.economics.evaluator import (
            EconomicEvaluator,
            UnresolvableSubjectsError,
            require_resolvable_subjects,
        )
        from hisim.economics.parameters import EconomicParameters

        inputs = build_evaluation_inputs(
            [_Wrapper(UndeclaredDevice("SomeUndeclaredDevice"))],
            [],
            pd.DataFrame(),
            _SimulationParameters(),
        )

        assert inputs.cost_facts == []
        assert [item.subject for item in inputs.unresolved_subjects] == ["SomeUndeclaredDevice"]
        assert "UndeclaredDevice" in inputs.unresolved_subjects[0].reason

        evaluator = EconomicEvaluator(
            CostDatabase(), EconomicParameters(country="DE", price_basis_year=2024)
        )
        with pytest.raises(UnresolvableSubjectsError) as raised:
            require_resolvable_subjects(inputs, evaluator)
        assert "SomeUndeclaredDevice" in str(raised.value)
        assert "no partial cost results" in str(raised.value)


class TestPostprocessingLetsTheFailurePropagate:
    """The D7 abort must reach the caller, not end up in `log.error` (README, §9.2).

    `postprocessing_main` wraps the bridge call in a broad handler so an *accident* in the
    parallel cost engine cannot cost a user their simulation results. That handler used to
    swallow the deliberate D7 failure too, which is what made "undeclared" survivable in
    practice however loudly the bridge complained. It now re-raises the typed cost errors and
    logs only the rest, and `_propagating_cost_errors` is the list it re-raises on.
    """

    def test_the_exempted_type_covers_the_d7_abort(self):
        """`CostDataError` is the marker, and `UnresolvableSubjectsError` is one of them.

        Failure mode caught: the exemption being narrowed to `UnresolvableSubjectsError` alone (a
        missing device entry raised straight out of the database would start being swallowed
        again) or the error hierarchy changing so that the D7 abort is no longer a
        `CostDataError` and falls through to the log.
        """
        from hisim.economics.catalog_entries import CostDataError
        from hisim.economics.evaluator import UnresolvableSubjectsError
        from hisim.postprocessing.postprocessing_main import _propagating_cost_errors

        exempted = _propagating_cost_errors()
        assert CostDataError in exempted
        assert issubclass(UnresolvableSubjectsError, exempted)
        assert isinstance(CostDataError("no device entry"), exempted)

    def test_an_unrelated_accident_is_not_exempted(self):
        """A bug in the engine still degrades to a logged error rather than failing the run."""
        from hisim.postprocessing.postprocessing_main import _propagating_cost_errors

        assert not isinstance(RuntimeError("some engine bug"), _propagating_cost_errors())
        assert not isinstance(KeyError("a renamed column"), _propagating_cost_errors())

    def test_an_unreadable_cost_database_fails_the_run_instead_of_skipping_the_costs(self):
        """`compute_lifecycle_costs` lets a database that will not load raise, writing nothing.

        Failure mode caught: the bridge quietly returning after a `log.error` when the configured
        `cost_database_path` is wrong — the run would finish with no cost files at all, which is
        the same silent omission as an undeclared component, only fleet-wide. The raise happens
        before `economic_inputs.json` is written, so a misconfigured run leaves no half-extract.
        """
        from hisim.economics.bridge import compute_lifecycle_costs
        from hisim.economics.catalog_entries import CostDataError
        from hisim.economics.parameters import EconomicParameters

        simulation_parameters: Any = _SimulationParameters()
        simulation_parameters.economic_parameters = EconomicParameters(
            country="DE", cost_database_path="/nonexistent/cost_database"
        )
        with pytest.raises(CostDataError):
            compute_lifecycle_costs([], [], pd.DataFrame(), simulation_parameters)
