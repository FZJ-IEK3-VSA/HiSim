"""Every component class declares its energy ports or is exempt from the energy balance, and says which.

The energy balance (:mod:`hisim.postprocessing.energy_balance`) reads a component through its energy ports: the
``energy_port`` of its outputs and inputs, the fluid loops it tags on its channels (its port sets) and, for a meter,
what it meters. A component that has none of them is either *exempt*, never a node and never a peer, or
*undeclared*, a grey node that hides energy the balance cannot see (owner decision Q-B of
``roadmap/complete_energy_sankeys_spec.md``, §5.5 there for every class's verdict). A component is exempt

* by its kind: an L1 or L2 controller decides, it moves no energy, and a meter that declares no metered exchange
  publishes nothing the balance can check (:class:`hisim.postprocessing.energy_balance.ports.Exemptions`);
* or by a reason its own class states in ``ENERGY_PORTS_EXEMPT_BECAUSE``, for a physics component or a data source
  that moves no energy: a helper that sums two inputs, the weather, a teaching example.

This test builds every component class of ``hisim.components`` from each configuration it ships
(:class:`tests.component_sweep.ComponentSweep`), without a run, and refuses a class that is neither declared nor
exempt, an exempt class that declares a port, and a reason stated where it does not belong. A new component class
therefore states its ports or its reason in the change that adds it.
"""

import dataclasses
import enum
from typing import ClassVar, Dict, List, Mapping, Optional, Tuple, Type

import pytest

from hisim.component import (
    Component,
    ComponentKinds,
    L1ControllerComponent,
    L2ControllerComponent,
    MeterComponent,
)
from hisim.components.example_template import ComponentName
from hisim.components.generic_car import Car
from hisim.components.simple_water_storage import SimpleDHWStorage, SimpleHotWaterStorage, SimpleWaterStorage
from hisim.components.sumbuilder import SumBuilderForThreeInputs
from hisim.components.weather.weather import Weather
from hisim.postprocessing.energy_balance.ports import Exemptions
from hisim.simulationparameters import SimulationParameters
from tests.component_sweep import BuiltComponent, ComponentSweep

pytestmark = pytest.mark.base


class Verdict(enum.Enum):
    """What a component class is in the energy balance, as :meth:`PortCoverage.verdict` finds it."""

    #: It declares an energy port, a fluid loop or a metered exchange in at least one configuration: a node.
    DECLARED = "declared"
    #: An L1 or L2 controller, or a meter that declares no metered exchange.
    EXEMPT_BY_KIND = "exempt by kind"
    #: A physics component or a data source whose class states ``ENERGY_PORTS_EXEMPT_BECAUSE``.
    EXEMPT_BY_REASON = "exempt by reason"
    #: Neither: the balance would draw it as undeclared.
    UNDECLARED = "undeclared"
    #: Not built by the sweep and not exempt, so the test cannot tell whether it declares a port.
    NOT_BUILT = "not built"


@dataclasses.dataclass(frozen=True)
class ClassChannels:
    """What one component class declares to the energy balance over every configuration the sweep built of it.

    Attributes:
        component_class: The class.
        built_from: The labels of the configurations the sweep built it from; empty when it built none.
        ports: Every channel that carries an energy port, ``"<label>: <channel>"`` each.
        port_sets: Every fluid loop it declares, ``"<label>: <loop>"`` each.
        metered_carriers: Every carrier it meters, ``"<label>: <carrier>"`` each.
    """

    component_class: Type[Component]
    built_from: Tuple[str, ...]
    ports: Tuple[str, ...]
    port_sets: Tuple[str, ...]
    metered_carriers: Tuple[str, ...]

    @classmethod
    def of(cls, component_class: Type[Component], built: List[BuiltComponent]) -> "ClassChannels":
        """Collect what ``component_class`` declares in each configuration of ``built`` that is of that class.

        Args:
            component_class: The class.
            built: Every component the sweep built.

        Returns:
            The class's channels, summed over its configurations.
        """
        mine = [entry for entry in built if entry.component_class is component_class]
        return cls(
            component_class=component_class,
            built_from=tuple(entry.label for entry in mine),
            ports=tuple(
                f"{entry.label}: {channel.field_name}"
                for entry in mine
                for channel in list(entry.component.outputs) + list(entry.component.inputs)
                if channel.energy_port is not None
            ),
            port_sets=tuple(f"{entry.label}: {port_set.loop.name}" for entry in mine for port_set in entry.component.port_sets()),
            metered_carriers=tuple(
                f"{entry.label}: {exchange.carrier.name}" for entry in mine for exchange in entry.component.metered_exchanges()
            ),
        )

    @property
    def declares(self) -> bool:
        """Whether the class declares anything the balance reads in at least one configuration."""
        return bool(self.ports or self.port_sets or self.metered_carriers)


class PortCoverage:
    """Finds every component class's verdict in the energy balance, and the classes the test leaves out."""

    #: The kinds that are exempt as a whole: a controller decides, it moves no energy.
    CONTROLLER_KINDS: ClassVar[Tuple[Type[Component], ...]] = (L1ControllerComponent, L2ControllerComponent)

    #: Component classes that are never built, each with the reason. They carry no verdict of their own.
    NEVER_BUILT: ClassVar[Mapping[Type[Component], str]] = {
        SimpleWaterStorage: (
            "the base of SimpleHotWaterStorage and SimpleDHWStorage with their shared functions; never built itself, "
            "its two subclasses declare their ports"
        ),
    }

    @staticmethod
    def stated_reason(component_class: type) -> Optional[str]:
        """The reason ``component_class`` states on its own class body, not inherited, or None.

        For example, ``SumBuilderForTwoInputs`` states "an arithmetic helper; summing energy is not moving it". A
        reason stated by a base class does not count: each class states its own (Q-B, a black box).
        """
        stated = vars(component_class).get("ENERGY_PORTS_EXEMPT_BECAUSE")
        return stated if isinstance(stated, str) else None

    @classmethod
    def verdict(cls, channels: ClassChannels) -> Verdict:
        """Return what ``channels``'s class is in the energy balance.

        A controller is exempt by its kind. A meter is declared when it meters a carrier, else exempt by its kind.
        Any other class is exempt by its reason when it states one, declared when it declares a port, not built when
        the sweep built it from no configuration, and undeclared otherwise.

        Args:
            channels: What the class declares over the configurations the sweep built.

        Returns:
            The verdict.
        """
        kind = ComponentKinds.of(channels.component_class)
        if kind in cls.CONTROLLER_KINDS:
            return Verdict.EXEMPT_BY_KIND
        if kind is MeterComponent:
            return Verdict.DECLARED if channels.metered_carriers else Verdict.EXEMPT_BY_KIND
        if cls.stated_reason(channels.component_class) is not None:
            return Verdict.EXEMPT_BY_REASON
        if channels.declares:
            return Verdict.DECLARED
        return Verdict.UNDECLARED if channels.built_from else Verdict.NOT_BUILT


@pytest.fixture(name="built", scope="module")
def fixture_built() -> List[BuiltComponent]:
    """Every component class built from each of its configurations; built once per module."""
    return ComponentSweep.built_components(SimulationParameters.one_day_only(year=2021, seconds_per_timestep=3600))


@pytest.fixture(name="channels", scope="module")
def fixture_channels(built: List[BuiltComponent]) -> Dict[Type[Component], ClassChannels]:
    """What every component class declares, by class, without the classes of :attr:`PortCoverage.NEVER_BUILT`."""
    return {
        component_class: ClassChannels.of(component_class, built)
        for component_class in ComponentSweep.component_classes()
        if component_class not in PortCoverage.NEVER_BUILT
    }


def test_the_sweep_reaches_the_classes_it_must(channels: Dict[Type[Component], ClassChannels]) -> None:
    """Catches a sweep that builds too little, or a verdict that misreads a class, which would let the test pass."""
    assert channels[ComponentName].declares, "the example template teaches the energy port and declares one"
    assert channels[Car].built_from, "the car ships no preset; the sweep builds it by its constructor"
    assert channels[SumBuilderForThreeInputs].built_from, "the sum builder's configuration builds three classes"
    assert PortCoverage.verdict(channels[Weather]) is Verdict.EXEMPT_BY_REASON


def test_every_component_class_declares_ports_or_is_exempt(channels: Dict[Type[Component], ClassChannels]) -> None:
    """Catches a component class that moves energy the balance cannot see: no port, no kind and no stated reason."""
    failures = []
    for component_class, class_channels in channels.items():
        verdict = PortCoverage.verdict(class_channels)
        if verdict is Verdict.UNDECLARED:
            failures.append(
                f"{component_class.__module__}.{component_class.__name__} declares no energy port in "
                f"{list(class_channels.built_from)}: declare its ports, or state ENERGY_PORTS_EXEMPT_BECAUSE on it"
            )
        elif verdict is Verdict.NOT_BUILT:
            failures.append(
                f"{component_class.__module__}.{component_class.__name__} is built from no configuration, so whether it "
                "declares a port is unknown: give its configuration a preset or an entry in ComponentSweep.CONSTRUCTED"
            )
    assert not failures, "component classes neither declared nor exempt:\n" + "\n".join(failures)


def test_an_exempt_class_declares_no_port(channels: Dict[Type[Component], ClassChannels]) -> None:
    """Catches a controller, a meter or a stated exemption that declares a port, which the balance refuses in a run."""
    failures = []
    for component_class, class_channels in channels.items():
        verdict = PortCoverage.verdict(class_channels)
        exempt = verdict in (Verdict.EXEMPT_BY_KIND, Verdict.EXEMPT_BY_REASON)
        if (exempt and class_channels.declares) or (
            ComponentKinds.of(component_class) is MeterComponent and (class_channels.ports or class_channels.port_sets)
        ):
            failures.append(
                f"{component_class.__name__} ({verdict.value}): ports {list(class_channels.ports)}, "
                f"fluid loops {list(class_channels.port_sets)}, metered {list(class_channels.metered_carriers)}"
            )
    assert not failures, "exempt classes that declare ports:\n" + "\n".join(failures)


def test_a_reason_is_stated_only_where_it_exempts(channels: Dict[Type[Component], ClassChannels]) -> None:
    """Catches a reason on a controller or a meter, which their kind already exempts, and an empty reason.

    A class exempt by its kind states no reason of its own, so that each exempt class is exempt in one way only.
    A class that declares a port carries no reason either, also none inherited from a base class: the balance
    would take it as exempt and refuse its ports in every run (an exemption is inherited at run time,
    :meth:`hisim.postprocessing.energy_balance.ports.Exemptions.reason`).
    """
    failures = []
    for component_class, class_channels in channels.items():
        stated = vars(component_class).get("ENERGY_PORTS_EXEMPT_BECAUSE")
        inherited = getattr(component_class, "ENERGY_PORTS_EXEMPT_BECAUSE", None)
        kind = ComponentKinds.of(component_class)
        if stated is not None and (not isinstance(stated, str) or not stated.strip()):
            failures.append(f"{component_class.__name__} states the reason {stated!r}, which says nothing")
        if stated is not None and (kind in PortCoverage.CONTROLLER_KINDS or kind is MeterComponent):
            failures.append(f"{component_class.__name__} states a reason, but its kind {kind.__name__} decides")
        if inherited is not None and class_channels.declares:
            failures.append(f"{component_class.__name__} declares ports and carries the exemption {inherited!r}")
    assert not failures, "misplaced exemption reasons:\n" + "\n".join(failures)


def test_the_run_reads_each_built_component_as_the_verdict_says(
    built: List[BuiltComponent], channels: Dict[Type[Component], ClassChannels]
) -> None:
    """Catches a verdict of this test that the balance of a run would not reach, so the two never drift apart.

    The run asks :meth:`Exemptions.reason` of every component that meters nothing: a class exempt by its reason
    gets that reason, a controller its kind's, a declared class none.
    """
    failures = []
    for entry in built:
        if entry.component_class in PortCoverage.NEVER_BUILT or entry.component.metered_exchanges():
            continue
        verdict = PortCoverage.verdict(channels[entry.component_class])
        reason = Exemptions.reason(entry.component)
        expected: Optional[str] = None
        if verdict is Verdict.EXEMPT_BY_REASON:
            expected = PortCoverage.stated_reason(entry.component_class)
        elif verdict is Verdict.EXEMPT_BY_KIND:
            expected = Exemptions.REASON_OF_KIND[ComponentKinds.of(entry.component_class)]
        if reason != expected:
            failures.append(f"{entry.component_class.__name__} ({entry.label}, {verdict.value}): the run reads {reason!r}")
    assert not failures, "verdicts the run does not share:\n" + "\n".join(failures)


def test_every_never_built_class_is_a_base_the_sweep_does_not_build(built: List[BuiltComponent]) -> None:
    """Catches an entry of :attr:`PortCoverage.NEVER_BUILT` that is now built, or that no class derives from."""
    built_classes = {entry.component_class for entry in built}
    stale = [
        component_class.__name__
        for component_class in PortCoverage.NEVER_BUILT
        if component_class in built_classes
        or not any(issubclass(other, component_class) and other is not component_class for other in ComponentSweep.component_classes())
    ]
    assert not stale, f"listed in PortCoverage.NEVER_BUILT but built or derived by nothing: {stale}"
    assert {SimpleHotWaterStorage, SimpleDHWStorage} <= built_classes
