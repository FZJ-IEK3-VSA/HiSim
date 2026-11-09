"""The declared energy ports of a finished run, as kWh per step, and who is on the other side of each (hisim-energy-sankeys-9uoo.2).

:class:`DeclaredPorts` reads the result table once: every output that declares an
:class:`~hisim.energy_port.EnergyPort` becomes a :class:`PortSeries`, its column converted to kWh per step
(a power with the timestep) and turned round when the port is declared ``negated``; the sign of its role is
applied by the check (:mod:`.check`). An input that declares a port becomes one too, with the column of the output
connected to it (decision D9 of ``roadmap/fluid_loops_spec.md``); an input that is not connected is left out. A component that declares what it meters
(:meth:`hisim.component.Component.metered_exchanges`) is a *meter*, a node of its own (owner decision Q-J of
``roadmap/complete_energy_sankeys_spec.md``). Any other component none of whose outputs declares a port is
*undeclared*, unless it is *exempt* (:class:`Exemptions`): a controller decides, it moves no energy (owner decision
D1). The peer of each port comes from the wiring the simulator left on the components' inputs
(:attr:`~hisim.component.ComponentInput.src_object_name`), skipping exempt components (:class:`Wiring`): an input
port's peer is the component it reads; an output port's is the component that reads it through an input carrying a
port it pairs with, else, for a ``PRODUCTION`` port, the component that reads it; a metered port of electricity or a
bought carrier goes to the meter that reads it, directly or through exempt components, or to a component that reads
it. A port with no peer falls back to its carrier's boundary default
(:meth:`hisim.energy_port.EnergyPort.default_boundary`), such as "natural gas supply" or "heat loss to outdoors", or to nobody, which
leaves the port *unlinked*. A metered port whose peer is no meter and no component is *unmetered*
(:meth:`DeclaredPorts.unmetered`).

:meth:`DeclaredPorts.declared_links` pairs a sender's ``PRODUCTION`` port with its receiver's ``CONSUMPTION`` port where both
describe the same transfer, and lists a transfer between two declared components that only one side books with
the other side None: the check compares the totals of a pair and fails a one-sided booking, the Sankey draws a
pair once (:meth:`DeclaredPorts.paired_links`). A booking toward a component that declares no ports at all, a
meter, a boundary node or nobody stays one-sided without failing.

A fluid loop is declared by the loop tags on its members' channels
(:class:`~hisim.energy_port.FluidLoopChannel`); each member's tagged channels of one loop are its port set
(:class:`~hisim.component.FluidLoopPortSet`), and each port set is one *end* of the loop here.
:meth:`DeclaredPorts.collect` follows each end's inputs to the outputs connected to them (:class:`LoopColumns`),
derives the loop's heat once from the three columns and gives both ends that one series, booked by the supplier's
sign (§3.4 of ``roadmap/fluid_loops_spec.md``): where the supplier heats the water, the supplier's ``PRODUCTION`` port
and the receiver's ``CONSUMPTION`` port of the loop's carrier; where it cools it, the receiver's ``PRODUCTION`` port and the
supplier's ``CONSUMPTION`` port of ``COOLING``. Each end has the other as its peer (:meth:`Wiring.loop_peer`). So a loop
is a transfer like any other, which :meth:`DeclaredPorts.declared_links` pairs, and it cannot disagree with itself.
What breaks the rules of :mod:`hisim.energy_port` (two readers of one flow, an end without its match, no or two
suppliers, an end on an exempt component or a meter, a loop that pumps water it cannot account for) is a
:attr:`DeclaredPorts.loop_breaches` entry, which the setup rule ``AR-06`` of ``hisim/setup_rules/results.py``
refuses after the run. The rules that need no value of the run refuse it before its first step: the energy-port
rules of ``hisim/setup_rules/energy_ports.py``, which read the wiring through :class:`Wiring`.

Beside the ports, :class:`DeclaredPorts` reads what each meter publishes (:class:`MeterReading`, from
:meth:`hisim.component.Component.metered_exchanges`), which :mod:`.meters` checks against the ports it reads.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple, Type

import numpy as np
import pandas as pd

from hisim import hydronics
from hisim import loadtypes as lt
from hisim.component import (
    Component,
    ComponentKindNotDeclaredError,
    ComponentKinds,
    ComponentOutput,
    FluidLoopPortSet,
    L1ControllerComponent,
    L2ControllerComponent,
    MeterComponent,
)
from hisim.config.channels import PortTypeCompatibility
from hisim.energy_port import EnergyPort, LoopQuantity, MeteredExchange


@enum.unique
class PeerKind(str, enum.Enum):

    """What the other side of a port is (complete Sankey spec, §4.1 and §4.3).

    ``COMPONENT`` is another component, declared or not; ``METER`` a meter that reads the port (owner decision Q-J);
    ``BOUNDARY`` a node where energy enters or leaves the house ("natural gas supply", "ambient heat source",
    "unconverted sunlight", "heat loss to outdoors");
    ``STORE`` the component's own store, the other side of its storage change; ``UNLINKED`` nobody, a port whose
    wiring names no meter and no component that moves energy and whose carrier has no default.
    """

    COMPONENT = "component"
    METER = "meter"
    BOUNDARY = "boundary"
    STORE = "store"
    UNLINKED = "unlinked"


@dataclass(frozen=True)
class Peer:
    """The other side of a port: a component, a meter, a boundary node, the component's store, or nobody."""

    kind: PeerKind
    name: str

    #: Joins a component's name and a port's name into the name of its loss or storage node.
    NODE_SEPARATOR: ClassVar[str] = " · "

    @classmethod
    def store_of(cls, component_name: str) -> "Peer":
        """The component's own store, the other side of its storage change, named "<component> · stored"."""
        return cls(PeerKind.STORE, f"{component_name}{cls.NODE_SEPARATOR}stored")

    @classmethod
    def meter(cls, meter_name: str) -> "Peer":
        """A meter that reads the port, drawn under its component name, for example "ElectricityMeter"."""
        return cls(PeerKind.METER, meter_name)

    @classmethod
    def unlinked(cls) -> "Peer":
        """A port whose peer neither the wiring nor its carrier names."""
        return cls(PeerKind.UNLINKED, "unlinked")


class Exemptions:
    """Which components are never a node or a peer of the energy balance, and why (owner decisions D1 and Q-J).

    A component is exempt by its kind, an L1 or L2 controller (:meth:`hisim.component.ComponentKinds.of`): a
    controller decides, it moves no energy. A meter is a node when it declares what it meters
    (:meth:`hisim.component.Component.metered_exchanges`); one that declares nothing is exempt by its kind, since the
    balance cannot read it. Any other component is exempt when its class states a reason in
    :attr:`hisim.component.Component.ENERGY_PORTS_EXEMPT_BECAUSE`. An exempt component is listed in the report with
    its reason, never as undeclared, and the wiring skips it when it looks for a port's peer.
    """

    #: The reason of every kind that is exempt as a whole (read-only); a meter's applies only when it meters nothing.
    REASON_OF_KIND: ClassVar[Mapping[Type[Component], str]] = MappingProxyType(
        {
            L1ControllerComponent: "an L1 controller decides for its device, it moves no energy",
            L2ControllerComponent: "an L2 controller coordinates devices, it moves no energy",
            MeterComponent: "a meter that declares no metered exchange publishes nothing the balance can check",
        }
    )

    @classmethod
    def reason(cls, component: Any) -> Optional[str]:
        """Why ``component`` is exempt from the energy balance, or None when it is not.

        For example, the energy management system is exempt because "an L2 controller coordinates devices, it moves
        no energy". A component whose class inherits no kind (a test double) is exempt only by its own attribute. A
        meter that declares a metered exchange is not asked: it is a node (:meth:`DeclaredPorts.collect`).

        Args:
            component: A component of the run.

        Returns:
            The reason, or None for a component that must declare its ports.
        """
        try:
            kind: Optional[Type[Component]] = ComponentKinds.of(type(component))
        except ComponentKindNotDeclaredError:
            kind = None
        if kind is not None and kind in cls.REASON_OF_KIND:
            return cls.REASON_OF_KIND[kind]
        stated = getattr(type(component), "ENERGY_PORTS_EXEMPT_BECAUSE", None)
        return stated if isinstance(stated, str) else None


@dataclass(frozen=True)
class LoopColumns:
    """The three outputs a fluid loop's heat is derived from, each as ``(component, output)``.

    Both ends of a loop resolve to the same three (:meth:`Wiring.resolve_loop`): for example the boiler's
    ``WaterOutputMassFlowSh`` and ``WaterOutputTemperatureSh`` and the buffer's ``WaterTemperatureToHeatGenerator``.
    """

    mass_flow: Tuple[str, str]
    supply_temperature: Tuple[str, str]
    return_temperature: Tuple[str, str]

    #: The three legs of a loop, as the fields name them.
    LEGS: ClassVar[Tuple[str, ...]] = ("mass_flow", "supply_temperature", "return_temperature")

    def as_dict(self) -> Dict[str, str]:
        """The three outputs as the balance report lists them, ``component.output`` each."""
        return {leg: ".".join(getattr(self, leg)) for leg in self.LEGS}

    def joins_the_same_outputs(self, other: "LoopColumns") -> bool:
        """Whether ``other`` names the same flow and the same two temperatures, whichever it takes for the supply.

        Two members of one loop resolve to the same three outputs when exactly one of them supplies it; when both or
        neither state ``supplies``, they read the two temperatures the other way round, and this still finds them one
        loop, so that the fault names the suppliers rather than a missing member.
        """
        return self.mass_flow == other.mass_flow and {self.supply_temperature, self.return_temperature} == {
            other.supply_temperature,
            other.return_temperature,
        }

    @staticmethod
    def heat_in_kilowatt_hours_per_step(
        *,
        mass_flow_in_kg_per_second: np.ndarray,
        supply_temperature_in_celsius: np.ndarray,
        return_temperature_in_celsius: np.ndarray,
        seconds_per_timestep: float,
        specific_heat_in_joule_per_kilogram_kelvin: float,
    ) -> np.ndarray:
        """Return the heat a loop's fluid carries in every step of a run, ``m c (T_sup - T_ret) dt``, in kWh.

        The rule of :func:`hisim.hydronics.circuit_power_w` times the step, converted from J to kWh, the same
        operations in the same order, applied to the three columns of the result table at once; so for water it
        agrees to the last bit with the power a component books for the same three values. ``c`` is the loop's fluid's
        (:attr:`hisim.energy_port.LoopFluid.specific_heat_j_per_kg_k`). For example, flows of 0.1 and 0 kg/s of water
        over 10 K for one hour carry 4.18 and 0 kWh.

        Args:
            mass_flow_in_kg_per_second: The loop's mass flow per step, in kg/s, at least 0 in every step.
            supply_temperature_in_celsius: The supply temperature per step, in °C.
            return_temperature_in_celsius: The return temperature per step, in °C.
            seconds_per_timestep: The step duration, in s.
            specific_heat_in_joule_per_kilogram_kelvin: The fluid's specific heat capacity, in J/(kg K).

        Returns:
            The heat per step, in kWh; negative in a step in which the supplier cools the fluid.

        Raises:
            HydronicsError: If a step's flow is negative (``NegativeMassFlowError``, naming the first such step) or
                the step is not positive (``NonPositiveTimestepError``), as :func:`hisim.hydronics.circuit_power_w`
                raises them for one step.
        """
        mass_flow = np.asarray(mass_flow_in_kg_per_second, dtype=float)
        refusal = LoopColumns.refusal(mass_flow, seconds_per_timestep)
        if refusal is not None:
            raise refusal
        supply = np.asarray(supply_temperature_in_celsius, dtype=float)
        returned = np.asarray(return_temperature_in_celsius, dtype=float)
        power_in_watt = mass_flow * specific_heat_in_joule_per_kilogram_kelvin * (supply - returned)
        return np.asarray(
            power_in_watt * seconds_per_timestep / hydronics.UnitConversion.JOULES_PER_KILOWATT_HOUR, dtype=float
        )

    @staticmethod
    def refusal(mass_flow_in_kg_per_second: np.ndarray, seconds_per_timestep: float) -> Optional[hydronics.HydronicsError]:
        """Return the error a loop's heat cannot be derived for, without raising it, or ``None`` when it can.

        A step that is not positive gives a ``NonPositiveTimestepError``, a negative flow in some step a
        ``NegativeMassFlowError`` naming the first such step, as :func:`hisim.hydronics.circuit_power_w` refuses them
        for one step. For example, flows of ``[0.1, -0.1]`` kg/s give "mass_flow_kg_per_s must be >= 0 kg/s, got -0.1
        in step 1."; the balance reports it as a fault of the setup rule ``AR-07`` and leaves the loop out.

        Args:
            mass_flow_in_kg_per_second: The loop's mass flow per step, in kg/s.
            seconds_per_timestep: The step duration, in s.

        Returns:
            The error, or ``None``.
        """
        if seconds_per_timestep <= 0.0:
            return hydronics.NonPositiveTimestepError(f"seconds_per_timestep must be > 0 s, got {seconds_per_timestep!r}.")
        mass_flow = np.asarray(mass_flow_in_kg_per_second, dtype=float)
        negative = mass_flow < 0.0
        if negative.any():
            first = int(np.argmax(negative))
            return hydronics.NegativeMassFlowError(f"mass_flow_kg_per_s must be >= 0 kg/s, got {float(mass_flow[first])!r} in step {first}.")
        return None

    @property
    def label(self) -> str:
        """How a port name shows the loop: by its mass flow.

        For example "loop CondensingGasBoiler.WaterOutputMassFlowSh".
        """
        return f"loop {'.'.join(self.mass_flow)}"


@dataclass
class PortSeries:
    """One declared port of one component: its declaration, its peer and its kWh per step.

    A port is declared on one of the component's channels, an output or an input (``reads`` set: the output connected
    to the input, whose column the series is). A loop end (``loop_columns`` set) is a port too: its ``port`` states its
    role and carrier, its ``channel`` is the loop's mass flow, and its kWh per step are the loop's heat in the
    steps its direction is the port's, the same series at both ends (:meth:`DeclaredPorts.add_loops`).
    """

    component: str
    #: The output or input the port is declared on; a loop end's mass flow.
    channel: str
    #: The unit of the column the series is read from.
    unit: lt.Units
    port: EnergyPort
    peer: Peer
    kilowatt_hours: np.ndarray
    #: The loop's three outputs, for a loop end; None for a port of a channel.
    loop_columns: Optional[LoopColumns] = None
    #: For an input port, the output connected to the input as ``(component, output)``; None for any other port.
    reads: Optional[Tuple[str, str]] = None

    @property
    def annual_kilowatt_hours(self) -> float:
        """The port's total over the run."""
        return float(self.kilowatt_hours.sum())

    @property
    def is_idle(self) -> bool:
        """Whether the port moves no energy in any step of the run, so that it books no transfer and needs no peer.

        For example, a cooling port read by a heater that declares no cooling, in a run that never cools: a transfer
        of nothing is no one-sided booking. A port that is NaN in a step is not idle (the check reports it as not
        finite).
        """
        return not np.any(self.kilowatt_hours)

    @property
    def port_name(self) -> str:
        """The channel a port is declared on, or the loop's label for a loop end."""
        return self.channel if self.loop_columns is None else self.loop_columns.label

    def as_dict(self) -> Dict[str, object]:
        """The port as the balance report lists it; a loop end in kWh, with its three outputs."""
        entry: Dict[str, object] = {
            "output": self.port_name,
            "unit": self.unit.value if self.loop_columns is None else lt.Units.KWH.value,
            **self.port.as_dict(),
            "peer": self.peer.name,
            "peer_kind": self.peer.kind.value,
            "total_kwh": self.annual_kilowatt_hours,
            "reads": None if self.reads is None else ".".join(self.reads),
        }
        if self.loop_columns is not None:
            entry["loop"] = self.loop_columns.as_dict()
        return entry

    @property
    def name(self) -> str:
        """``component.output``, as the report and the error messages name a port."""
        return f"{self.component}.{self.port_name}"


@dataclass
class MeterReading:
    """What one meter publishes of one carrier per step, in kWh: its import from and export to the boundary.

    Attributes:
        meter: The meter's component name.
        building: The meter's building (:attr:`hisim.config.ComponentID.building`), None in a run without buildings.
        exchange: What the meter declares it publishes.
        import_kilowatt_hours: The import per step, in kWh.
        export_kilowatt_hours: The export per step, in kWh; zeros for a carrier the meter counts only bought.
    """

    meter: str
    building: Optional[str]
    exchange: MeteredExchange
    import_kilowatt_hours: np.ndarray
    export_kilowatt_hours: np.ndarray


@dataclass(frozen=True)
class ReadingFault:
    """A port, a loop or a meter reading the balance cannot take from the run: the sentence, and where it is found.

    For example, ``ReadingFault("EMS", "EMS declares the energy port Power, but it is exempt from the energy balance:
    it decides.", "Power")``. ``component`` is ``None`` for a fault of the whole table, ``port`` ``None`` for one of a
    whole component. The balance leaves out what it cannot read; the setup rules ``AR-07`` and ``AR-08`` refuse it.
    """

    component: Optional[str]
    message: str
    port: Optional[str] = None


@dataclass
class DeclaredPorts:
    """Every declared port of a run, by component; the meters; the components that declare none, exempt or not."""

    by_component: Dict[str, List[PortSeries]] = field(default_factory=dict)
    class_names: Dict[str, str] = field(default_factory=dict)
    undeclared: List[str] = field(default_factory=list)
    #: Every exempt component with its reason, in the order of the run.
    exempt: Dict[str, str] = field(default_factory=dict)
    #: What each meter of the run publishes, one reading per metered exchange.
    meters: List[MeterReading] = field(default_factory=list)
    #: Every breach of the loop rules (:mod:`hisim.energy_port`), at its component; the setup rule AR-06 refuses each.
    loop_breaches: List["LoopFault"] = field(default_factory=list)
    #: Every port, loop or meter reading whose column the run does not have or whose flow or step cannot be read;
    #: the setup rule AR-07 refuses each.
    column_faults: List[ReadingFault] = field(default_factory=list)
    #: Every energy port an exempt component or a meter declares, which moves no energy of its own; AR-08 refuses each.
    node_port_faults: List[ReadingFault] = field(default_factory=list)
    index: Optional[pd.Index] = None

    @property
    def loop_faults(self) -> List[str]:
        """Every breach of the loop rules as its sentence, in the order found, as the balance report lists them."""
        return [breach.message for breach in self.loop_breaches]

    @staticmethod
    def exchanges_of(component: Any) -> Tuple[MeteredExchange, ...]:
        """What ``component`` declares it meters (:meth:`hisim.component.Component.metered_exchanges`); empty when not.

        A stand-in without the method, as the tests build them, meters nothing.
        """
        declare = getattr(component, "metered_exchanges", None)
        return tuple(declare()) if callable(declare) else ()

    @classmethod
    def collect(
        cls,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        components: Sequence[Any],
        seconds_per_timestep: float,
    ) -> "DeclaredPorts":
        """Read the declared ports and the meters from the result table, whose columns are ``all_outputs`` in order.

        An output port's series is its own column; an input port's the column of the output connected to it, and an
        input port whose input is not connected is left out (:meth:`add_input_ports`). Nothing the table cannot give
        raises: it is left out and recorded, for the setup rules after the run to refuse. A loop end an exempt
        component or a meter declares is a breach (:attr:`loop_breaches`, rule ``AR-06``, as the build's ``EP-03``
        refuses it before the run); a port an exempt component or a meter declares is a :attr:`node_port_faults` entry
        (``AR-08``); a table without one column per output, a port, loop or meter reading an output the run does not
        have, and a loop whose flow is negative in some step or whose step is not positive are
        :attr:`column_faults` entries (``AR-07``). A table without one column per output is read no further.

        Args:
            results: The result table, one column per output and one row per step.
            all_outputs: The run's outputs, in the order of the table's columns.
            components: The components of the run, in the order they were added, with their inputs connected.
            seconds_per_timestep: The length of one step.

        Returns:
            The declared ports, the meters and what could not be read.
        """
        if results.shape[1] != len(all_outputs):
            return cls(index=results.index, column_faults=[ReadingFault(None, f"The result table has {results.shape[1]} columns for {len(all_outputs)} outputs.")])
        metered_carriers, exempt = cls.classify(components)
        wiring = Wiring(components, exempt, metered_carriers)
        declared = cls(index=results.index, exempt=exempt)
        columns = [column for column, output in enumerate(all_outputs) if output.energy_port is not None]
        # only the declared columns are copied: the check costs what the ports are, not the whole table
        values = results.iloc[:, columns].to_numpy(dtype=float) if columns else np.empty((len(results), 0))
        for position, column in enumerate(columns):
            output = all_outputs[column]
            port = output.energy_port
            if port is None:  # pragma: no cover - the columns are the declared ones
                continue
            node_fault = cls.node_port_fault(output.component_name, output.field_name, exempt, metered_carriers)
            if node_fault is not None:
                declared.node_port_faults.append(ReadingFault(output.component_name, node_fault, output.field_name))
                continue
            factor = EnergyPort.kilowatt_hours_per_step(output.unit, seconds_per_timestep, port.carrier)
            sign = -1.0 if port.negated else 1.0
            series = PortSeries(
                component=output.component_name,
                channel=output.field_name,
                unit=output.unit,
                port=port,
                peer=wiring.peer_of(output.component_name, output.field_name, port),
                kilowatt_hours=values[:, position] * (factor * sign),
            )
            declared.by_component.setdefault(output.component_name, []).append(series)
        declared.add_input_ports(results, all_outputs, components, wiring, seconds_per_timestep)
        loops = wiring.loops(components)
        declared.add_loops(results, all_outputs, loops, seconds_per_timestep)
        for component in components:
            declared.class_names[component.component_name] = type(component).__name__
            name = component.component_name
            if name not in declared.by_component and name not in exempt and name not in metered_carriers:
                declared.undeclared.append(name)
        declared.meters, meter_faults = cls.read_meters(results, all_outputs, components, seconds_per_timestep)
        declared.column_faults.extend(meter_faults)
        return declared

    @staticmethod
    def node_port_fault(
        component_name: str,
        channel_name: str,
        exempt: Mapping[str, str],
        metered_carriers: Mapping[str, FrozenSet[lt.EnergyBalanceCarrier]],
    ) -> Optional[str]:
        """Return why an energy port cannot be declared where it is, by an exempt component or a meter, or ``None``.

        For example, an L1 controller declaring a port on its output, or the electricity meter on an input, contradicts
        its kind: a controller decides, a meter publishes what it meters (:meth:`classify`). The balance leaves such a
        port out, and the setup rule ``AR-08`` refuses it.

        Args:
            component_name: The declaring component.
            channel_name: The output or input that carries the port.
            exempt: The exempt components with their reasons.
            metered_carriers: The meters with the carriers each meters.

        Returns:
            The sentence naming the channel and the reason, or ``None`` for a component that moves energy.
        """
        if component_name in exempt:
            return (
                f"{component_name} declares the energy port {channel_name}, but it is exempt from the "
                f"energy balance: {exempt[component_name]}."
            )
        if component_name in metered_carriers:
            return (
                f"{component_name} declares the energy port {channel_name}, but it is a meter: it "
                f"publishes what it meters (metered_exchanges) and moves no energy of its own."
            )
        return None

    def add_input_ports(
        self,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        components: Sequence[Any],
        wiring: "Wiring",
        seconds_per_timestep: float,
    ) -> None:
        """Add every input that declares an energy port, booking the column of the output connected to it, in kWh.

        The output's unit converts the column (a fuel's mass flow by the port's carrier's lower heating value), the
        port's ``negated`` turns it round, and the output's component is the peer (:meth:`Wiring.input_peer`). An
        input that is not connected moves nothing in this run and is left out, as an optional input without a source
        is. For example, a building's input ``HeatReceivedInWatt`` with
        ``EnergyPort(CONSUMPTION, SPACE_HEATING_HEAT)``, connected to a distribution system's ``HeatDeliveredInWatt``,
        books that column as space heating received from the distribution system. An input port an exempt component
        or a meter declares (:attr:`node_port_faults`) and one reading an output the run does not have
        (:attr:`column_faults`) are left out and recorded.
        """
        position_of = {(output.component_name, output.field_name): column for column, output in enumerate(all_outputs)}
        for component in components:
            name = component.component_name
            for component_input in component.inputs:
                port = component_input.energy_port
                if port is None:
                    continue
                node_fault = self.node_port_fault(name, component_input.field_name, wiring.exempt, wiring.metered_carriers)
                if node_fault is not None:
                    self.node_port_faults.append(ReadingFault(name, node_fault, component_input.field_name))
                    continue
                source = wiring.source_outputs.get((name, component_input.field_name))
                if source is None:
                    continue
                if source not in position_of:
                    self.column_faults.append(
                        ReadingFault(
                            name,
                            f"{name}.{component_input.field_name} declares an energy port and reads {'.'.join(source)}, "
                            "an output the run does not have.",
                            component_input.field_name,
                        )
                    )
                    continue
                output = all_outputs[position_of[source]]
                factor = EnergyPort.kilowatt_hours_per_step(output.unit, seconds_per_timestep, port.carrier)
                sign = -1.0 if port.negated else 1.0
                column = results.iloc[:, position_of[source]].to_numpy(dtype=float)
                series = PortSeries(
                    component=name,
                    channel=component_input.field_name,
                    unit=output.unit,
                    port=port,
                    peer=wiring.input_peer(source[0], port),
                    kilowatt_hours=column * (factor * sign),
                    reads=source,
                )
                self.by_component.setdefault(name, []).append(series)

    @staticmethod
    def classify(
        components: Sequence[Any],
    ) -> Tuple[Dict[str, FrozenSet[lt.EnergyBalanceCarrier]], Dict[str, str]]:
        """The meters of a run with the carriers each meters, and the exempt components with their reasons.

        A component that declares a metered exchange is a meter (a node), never exempt; any other is exempt when
        :meth:`Exemptions.reason` gives a reason.

        Args:
            components: The components of the run, in the order of the run.

        Returns:
            The meters by name with their carriers, and the exempt components by name with their reasons.
        """
        metered_carriers = {
            component.component_name: frozenset(exchange.carrier for exchange in exchanges)
            for component in components
            if (exchanges := DeclaredPorts.exchanges_of(component))
        }
        exempt = {
            component.component_name: reason
            for component in components
            if component.component_name not in metered_carriers and (reason := Exemptions.reason(component)) is not None
        }
        return metered_carriers, exempt

    @staticmethod
    def port_sets_of(component: Any) -> Tuple[FluidLoopPortSet, ...]:
        """The loop ends ``component`` declares to the balance (:meth:`hisim.component.Component.port_sets_in_the_energy_balance`).

        A stand-in without that method books its port sets (``port_sets``) if it has them, else none, as the tests
        build them.

        Raises:
            FluidLoopError: If the component tags a loop by half, which the build refuses first (setup rule ``FL-01``).
        """
        declare = getattr(component, "port_sets_in_the_energy_balance", None) or getattr(component, "port_sets", None)
        return tuple(declare()) if callable(declare) else ()

    def add_loops(
        self,
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        loops: "LoopWiring",
        seconds_per_timestep: float,
    ) -> None:
        """Add every declared loop end as a port, with the loop's heat per step and the other end as its peer.

        The heat of each loop is derived once from its three columns (:meth:`LoopColumns.heat_in_kilowatt_hours_per_step`,
        with the specific heat of the loop's fluid), and both ends book that series by the supplier's sign
        (:meth:`book_by_the_supplier_s_sign`). The wiring's faults (:meth:`Wiring.loops`) become
        :attr:`loop_breaches`, and so does an end that pumps water into a loop it cannot follow: one whose own flow
        is not 0 in some step while one of the quantities it reads is not connected. An open end that moves no water is
        left out without a fault: a buffer's loop to a second generator the house does not have, a boiler's
        hot-water loop in a house without a tank. An end naming an output the run does not have, and a loop whose
        flow is negative in some step or whose step is not positive (:meth:`LoopColumns.refusal`, recorded once per
        loop), are left out and recorded in :attr:`column_faults`.
        """
        position_of = {(output.component_name, output.field_name): column for column, output in enumerate(all_outputs)}
        self.loop_breaches.extend(loops.breaches)
        for name, port_set, open_quantity in loops.open_ends:
            if not port_set.loop.pumps:
                continue
            flow = port_set.channels[LoopQuantity.FLOW_OUT].field_name
            column = position_of.get((name, flow))
            if column is not None and np.any(results.iloc[:, column].to_numpy(dtype=float) != 0.0):
                self.loop_breaches.append(
                    LoopFault(
                        name,
                        f"{name} pumps water through its loop {port_set.loop.name!r} ({flow}, "
                        f"{port_set.loop.carrier.value}), but its {open_quantity.value} input "
                        f"{port_set.channels[open_quantity].field_name} is not connected: the heat of that water goes "
                        "nowhere the balance can see",
                        flow,
                    )
                )
        # The heat of each loop, once for both ends; None for a loop whose flow or step cannot be read
        heat_of: Dict[Tuple[LoopColumns, float], Optional[np.ndarray]] = {}
        for name, port_set, columns, peer_name in loops.ends:
            missing = [leg for leg in LoopColumns.LEGS if getattr(columns, leg) not in position_of]
            if missing:
                self.column_faults.extend(
                    ReadingFault(
                        name,
                        f"{name} declares a loop whose {leg} is {'.'.join(getattr(columns, leg))}, an output the run does not have.",
                        port_set.loop.name,
                    )
                    for leg in missing
                )
                continue
            specific_heat = port_set.loop.fluid.specific_heat_j_per_kg_k
            if (columns, specific_heat) not in heat_of:
                mass_flow, supply, returned = (
                    results.iloc[:, position_of[getattr(columns, leg)]].to_numpy(dtype=float)
                    for leg in LoopColumns.LEGS
                )
                refusal = LoopColumns.refusal(mass_flow, seconds_per_timestep)
                if refusal is not None:
                    self.column_faults.append(ReadingFault(name, f"the {columns.label} of {name}: {refusal}", columns.mass_flow[1]))
                heat_of[(columns, specific_heat)] = None if refusal is not None else LoopColumns.heat_in_kilowatt_hours_per_step(
                    mass_flow_in_kg_per_second=mass_flow,
                    supply_temperature_in_celsius=supply,
                    return_temperature_in_celsius=returned,
                    seconds_per_timestep=seconds_per_timestep,
                    specific_heat_in_joule_per_kilogram_kelvin=specific_heat,
                )
            heat = heat_of[(columns, specific_heat)]
            if heat is None:
                continue
            peer = Peer.unlinked() if peer_name is None else Peer(PeerKind.COMPONENT, peer_name)
            for port, kilowatt_hours in self.book_by_the_supplier_s_sign(heat, port_set.loop.carrier, supplies=port_set.loop.supplies):
                flow_output = all_outputs[position_of[columns.mass_flow]]
                series = PortSeries(
                    component=name,
                    channel=flow_output.field_name,
                    unit=flow_output.unit,
                    port=port,
                    peer=peer,
                    kilowatt_hours=kilowatt_hours,
                    loop_columns=columns,
                )
                self.by_component.setdefault(name, []).append(series)

    @staticmethod
    def book_by_the_supplier_s_sign(
        heat_in_kilowatt_hours: np.ndarray, carrier: lt.EnergyBalanceCarrier, *, supplies: bool
    ) -> List[Tuple[EnergyPort, np.ndarray]]:
        """The ports one end of a loop books, with their kWh per step, by the sign of the supplier's heat (§3.4).

        ``heat_in_kilowatt_hours`` is ``m c (T_sup - T_ret) dt``, the heat the supplier adds to the fluid. Where it is
        positive the loop heats: the loop's ``carrier`` goes from the supplier (``PRODUCTION``) to the receiver (``CONSUMPTION``).
        Where it is negative the supplier cools the fluid: ``COOLING`` goes from the receiver (``PRODUCTION``) to the supplier
        (``CONSUMPTION``), the minus of the heat. A run in which no step cools books the heat as it is under the carrier alone,
        so a heating-only loop has one port per end, as it always had; a run with a cooling step books both
        directions, each 0 in the other's steps, and each end's balance is the same. For example, a heat pump's
        space-heating loop that carries 2 kWh in one step and -1 kWh in the next books, at the heat pump, ``PRODUCTION`` of
        space-heating heat ``[2, 0]`` and ``CONSUMPTION`` of cooling ``[0, 1]``.

        Args:
            heat_in_kilowatt_hours: The loop's heat per step, in kWh, from the supplier's side.
            carrier: The heat the loop carries while it heats.
            supplies: Whether this end is the loop's supplier.

        Returns:
            The end's ports with their kWh per step: the heating face, and the cooling face where a step cools.
        """
        heating_role, cooling_role = (lt.EnergyRole.PRODUCTION, lt.EnergyRole.CONSUMPTION) if supplies else (lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION)
        cools = heat_in_kilowatt_hours < 0.0
        if not cools.any():
            return [(EnergyPort(heating_role, carrier), heat_in_kilowatt_hours)]
        return [
            (EnergyPort(heating_role, carrier), np.where(cools, 0.0, heat_in_kilowatt_hours)),
            (EnergyPort(cooling_role, lt.EnergyBalanceCarrier.COOLING), np.where(cools, -heat_in_kilowatt_hours, 0.0)),
        ]

    @staticmethod
    def read_meters(
        results: pd.DataFrame,
        all_outputs: Sequence[ComponentOutput],
        components: Sequence[Any],
        seconds_per_timestep: float,
    ) -> Tuple[List[MeterReading], List[ReadingFault]]:
        """What every component that declares :meth:`~hisim.component.Component.metered_exchanges` publishes, per step.

        An exchange naming an output the component does not have is left out and returned as a fault, for the setup
        rule ``AR-07``. For example, a meter declaring its import on ``FromGridInWatt`` without that output gives no
        reading and the fault "Meter declares that it meters electricity on its output FromGridInWatt, which it does
        not have.".

        Args:
            results: The result table, one column per output and one row per step.
            all_outputs: The run's outputs, in the order of the table's columns.
            components: The components of the run.
            seconds_per_timestep: The length of one step.

        Returns:
            The readings, one per metered exchange that can be read, and the faults of those that cannot.
        """
        position_of = {
            (output.component_name, output.field_name): column for column, output in enumerate(all_outputs)
        }
        readings: List[MeterReading] = []
        faults: List[ReadingFault] = []
        for component in components:
            for exchange in DeclaredPorts.exchanges_of(component):
                missing = [
                    field_name
                    for field_name in (exchange.import_output, exchange.export_output)
                    if field_name is not None and (component.component_name, field_name) not in position_of
                ]
                faults.extend(
                    ReadingFault(
                        component.component_name,
                        f"{component.component_name} declares that it meters {exchange.carrier.value} on its "
                        f"output {field_name}, which it does not have.",
                        field_name,
                    )
                    for field_name in missing
                )
                if missing:
                    continue
                series: List[np.ndarray] = []
                building: Optional[str] = None
                for field_name in (exchange.import_output, exchange.export_output):
                    if field_name is None:
                        series.append(np.zeros(len(results)))
                        continue
                    column = position_of[(component.component_name, field_name)]
                    output = all_outputs[column]
                    building = output.component_id.building
                    factor = EnergyPort.kilowatt_hours_per_step(output.unit, seconds_per_timestep, exchange.carrier)
                    series.append(results.iloc[:, column].to_numpy(dtype=float) * factor)
                readings.append(MeterReading(component.component_name, building, exchange, series[0], series[1]))
        return readings, faults

    def is_declared(self, component_name: str) -> bool:
        """Whether the component declares at least one port."""
        return component_name in self.by_component

    def all_series(self) -> List[PortSeries]:
        """Every declared port of the run, component by component."""
        return [series for ports in self.by_component.values() for series in ports]

    def unlinked(self) -> List[PortSeries]:
        """Every port without a peer that moves energy: its wiring names no meter or component moving energy, its carrier no default.

        An idle port (:attr:`PortSeries.is_idle`) is not listed: it moves nothing that needs a peer, for example a
        distribution system's cooling direction in a house that never cools, while no input reads it. The setup rule
        ``AR-09`` (``hisim/setup_rules/results.py``) fails every run but a demo's that lists one (§4.3).
        """
        return [
            series for series in self.all_series() if series.peer.kind == PeerKind.UNLINKED and not series.is_idle
        ]

    def unmetered(self) -> List[PortSeries]:
        """Every metered port (:attr:`~hisim.energy_port.EnergyPort.is_metered`) whose flow passes no meter.

        A port of electricity or a bought carrier whose peer is neither a meter nor a component: a boiler's gas port
        that only the supply node feeds, or an electricity port nothing reads (owner decision Q-J: every bought
        carrier's flow passes a meter). A port whose peer is a component hands its flow on, and that component's
        ports are metered in turn.
        """
        return [
            series
            for series in self.all_series()
            if series.port.is_metered and series.peer.kind not in (PeerKind.METER, PeerKind.COMPONENT)
        ]

    def declared_links(self) -> Dict[Tuple[str, str, str], Tuple[Optional[float], Optional[float]]]:
        """Every transfer between two declared components: ``(sender, receiver, carrier) -> (sent, received)``.

        A receiver's ``CONSUMPTION`` port whose peer is a declared component pairs with that sender's ``PRODUCTION`` ports of the
        same carrier whose peer is the receiver. A side that books nothing of the transfer is None: a receiver's
        ``CONSUMPTION`` port with no such ``PRODUCTION`` port has ``sent`` None, a sender's ``PRODUCTION`` port whose declared peer has no
        ``CONSUMPTION`` port of the same carrier from it has ``received`` None. Both are one-sided bookings, which the
        check fails (owner, 2026-10-02: always fail hard). A booking toward a component that declares no ports,
        a meter, a boundary node, a store or nobody is not a link between declared components and is not listed, and
        neither is an idle port (:attr:`PortSeries.is_idle`), which books nothing: a cooling port read by a heater
        that never cools and declares no cooling.
        """
        received: Dict[Tuple[str, str, str], float] = {}
        sent: Dict[Tuple[str, str, str], float] = {}
        for component, ports in self.by_component.items():
            for series in ports:
                if series.peer.kind != PeerKind.COMPONENT or not self.is_declared(series.peer.name) or series.is_idle:
                    continue
                if series.port.role == lt.EnergyRole.CONSUMPTION:
                    key = (series.peer.name, component, series.port.carrier.value)
                    received[key] = received.get(key, 0.0) + series.annual_kilowatt_hours
                elif series.port.role == lt.EnergyRole.PRODUCTION:
                    key = (component, series.peer.name, series.port.carrier.value)
                    sent[key] = sent.get(key, 0.0) + series.annual_kilowatt_hours
        links: Dict[Tuple[str, str, str], Tuple[Optional[float], Optional[float]]] = {
            key: (sent.get(key), total) for key, total in received.items()
        }
        for key, total in sent.items():
            if key not in received:
                links[key] = (total, None)
        return links

    def paired_links(self) -> Dict[Tuple[str, str, str], Tuple[float, float]]:
        """The transfers both sides declare: ``(sender, receiver, carrier) -> (sent kWh, received kWh)``.

        The subset of :meth:`declared_links` with both sides booked; the Sankey draws each of them once.
        """
        return {
            key: (sent, received)
            for key, (sent, received) in self.declared_links().items()
            if sent is not None and received is not None
        }


@dataclass(frozen=True)
class LoopFault:
    """One breach of a fluid-loop rule the wiring shows: the sentence, and the component (and output) it is found at.

    For example, ``LoopFault("Boiler", "the mass flow Boiler.Flow is read by 2 components ...", "Flow")`` is a flow
    two port sets of one loop read. ``port`` is the output the fault is found at, ``None`` when the fault is about a port set.
    """

    component: str
    message: str
    port: Optional[str] = None


@dataclass
class LoopWiring:
    """The declared loop ends of a run, its port sets, as the wiring resolves them, before any value is read.

    The faults are kept by the rule they break, so that each setup rule of ``hisim/setup_rules/energy_ports.py`` reports
    its own and the balance after the run lists the same ones (:attr:`breaches`).

    Attributes:
        ends: Every resolved end as ``(component, port set, its three outputs, the other end or None)``.
        open_ends: Every end with a quantity it reads through an input that is not connected, as
            ``(component, port set, the first such quantity)``; whether it moves water only the run's values tell.
        pairing_faults: Every end whose other end declares ports but not the same loop, and every loop with no
            or two suppliers (:meth:`Wiring.pairing_fault`).
        shared_flow_faults: Every mass flow that more than one component besides its owner declares a loop on.
        node_end_faults: Every loop end an exempt component or a meter declares; such an end is not resolved.
    """

    ends: List[Tuple[str, FluidLoopPortSet, LoopColumns, Optional[str]]] = field(default_factory=list)
    open_ends: List[Tuple[str, FluidLoopPortSet, LoopQuantity]] = field(default_factory=list)
    pairing_faults: List[LoopFault] = field(default_factory=list)
    shared_flow_faults: List[LoopFault] = field(default_factory=list)
    node_end_faults: List[LoopFault] = field(default_factory=list)

    @property
    def breaches(self) -> List[LoopFault]:
        """Every breach of the loop rules of :mod:`hisim.energy_port` the wiring shows, at its component.

        The pairing faults come first, then the shared flows, then the ends on an exempt component or a meter, the
        order the balance after the run lists them in. For example, a boiler and a tank that both state
        ``supplies=True`` give one pairing fault, found at the boiler.
        """
        return self.pairing_faults + self.shared_flow_faults + self.node_end_faults


class Wiring:
    """The connections of a run, read from the components' inputs, and the peer each port finds through them."""

    def __init__(
        self,
        components: Sequence[Component],
        exempt: Optional[Mapping[str, str]] = None,
        metered_carriers: Optional[Mapping[str, FrozenSet[lt.EnergyBalanceCarrier]]] = None,
    ) -> None:
        """Index every connected input by its own component and by the output it reads, and every output's load type.

        Args:
            components: The components of the run, in the order of the run.
            exempt: The exempt components by name (:class:`Exemptions`); the wiring never names one as a peer.
            metered_carriers: The meters by name, each with the carriers it meters; a meter is the peer only of a
                metered port of one of its carriers.
        """
        self.order: Dict[str, int] = {component.component_name: number for number, component in enumerate(components)}
        self.exempt: Mapping[str, str] = exempt or {}
        self.metered_carriers: Mapping[str, FrozenSet[lt.EnergyBalanceCarrier]] = metered_carriers or {}
        self.sources: Dict[Tuple[str, str], str] = {}
        #: The output each connected input reads, ``(component, input) -> (source component, source output)``.
        self.source_outputs: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.readers: Dict[Tuple[str, str], List[str]] = {}
        #: Every connected input that declares an energy port, by the output it reads: ``[(reader, input, port)]``.
        self.port_readers: Dict[Tuple[str, str], List[Tuple[str, str, EnergyPort]]] = {}
        self.load_types: Dict[Tuple[str, str], lt.LoadTypes] = {}
        self.outputs: Dict[str, List[str]] = {}
        #: The energy port of every output that declares one, ``(component, output) -> port``.
        self.output_ports: Dict[Tuple[str, str], EnergyPort] = {}
        self.declared: Dict[str, bool] = {
            component.component_name: any(output.energy_port is not None for output in component.outputs)
            or any(component_input.energy_port is not None for component_input in component.inputs)
            or bool(DeclaredPorts.port_sets_of(component))
            for component in components
        }
        for component in components:
            self.outputs[component.component_name] = [output.field_name for output in component.outputs]
            for output in component.outputs:
                self.load_types[(component.component_name, output.field_name)] = output.load_type
                if output.energy_port is not None:
                    self.output_ports[(component.component_name, output.field_name)] = output.energy_port
            for component_input in component.inputs:
                source = component_input.src_object_name
                if source is None or component_input.src_field_name is None:
                    continue
                key = (source, component_input.src_field_name)
                self.sources[(component.component_name, component_input.field_name)] = source
                self.source_outputs[(component.component_name, component_input.field_name)] = key
                readers = self.readers.setdefault(key, [])
                if component.component_name not in readers:
                    readers.append(component.component_name)
                if component_input.energy_port is not None:
                    self.port_readers.setdefault(key, []).append(
                        (component.component_name, component_input.field_name, component_input.energy_port)
                    )

    def meters(self, name: str, carrier: lt.EnergyBalanceCarrier) -> bool:
        """Whether the component ``name`` is a meter of ``carrier``."""
        return carrier in self.metered_carriers.get(name, frozenset())

    def moves_energy(self, name: str) -> bool:
        """Whether the component ``name`` can be the peer of a port that is not metered: neither exempt nor a meter."""
        return name not in self.exempt and name not in self.metered_carriers

    def paired_readers(self, component_name: str, field_name: str, port: EnergyPort) -> List[str]:
        """The components that read an output port through an input carrying a port it pairs with, in wiring order.

        For example, a building reading a distribution system's ``HeatDeliveredInWatt`` through its input
        ``HeatReceivedInWatt`` with ``EnergyPort(CONSUMPTION, SPACE_HEATING_HEAT)`` is the paired reader of the
        distribution system's ``EnergyPort(PRODUCTION, SPACE_HEATING_HEAT)`` on that output
        (:meth:`hisim.energy_port.EnergyPort.pairs_with`). Exempt components and meters are left out.

        Args:
            component_name: The output port's component.
            field_name: The output.
            port: The output's port.

        Returns:
            The readers' names, without repeats.
        """
        paired: List[str] = []
        for reader, _, reader_port in self.port_readers.get((component_name, field_name), []):
            if port.pairs_with(reader_port) and self.moves_energy(reader) and reader not in paired:
                paired.append(reader)
        return paired

    def component_peer(self, component_name: str, field_name: str, port: EnergyPort) -> Optional[str]:
        """The component the wiring names as the peer of an output port that is not metered; None when there is none.

        A port naming its ``environment`` has none. A component reading the output through an input carrying a port
        this port pairs with is the peer (:meth:`paired_readers`); else a ``PRODUCTION`` port's peer is a component
        that reads the output: one that declares ports first, then the first in the order of the run. Any other port
        names no component. Exempt components and meters are skipped.
        """
        if port.environment is not None:
            return None
        paired = self.paired_readers(component_name, field_name, port)
        if paired:
            return paired[0]
        if port.role != lt.EnergyRole.PRODUCTION:
            return None
        readers = [name for name in self.readers.get((component_name, field_name), []) if self.moves_energy(name)]
        readers.sort(key=lambda name: (not self.declared.get(name, False), self.order.get(name, 0)))
        return readers[0] if readers else None

    def input_peer(self, source_name: str, port: EnergyPort) -> Peer:
        """The peer of an input port: the component whose output the input reads, or the port's fallback.

        An input port books the energy exchanged with its source, so the source is the peer when it moves energy. A
        source that is exempt or a meter moves none, and the port then takes its carrier's boundary default or is
        unlinked, as an output port without a peer does. For example, a hydrogen tank's input that reads an
        electrolyzer's ``HydrogenOutput`` (a fuel's mass flow, the one place an input port of a metered carrier fits,
        :meth:`hisim.energy_port.EnergyPort.check_fits_an_input`) has the electrolyzer as its peer.

        Args:
            source_name: The component whose output the input reads.
            port: The input's port.

        Returns:
            The peer.
        """
        if self.moves_energy(source_name):
            return Peer(PeerKind.COMPONENT, source_name)
        boundary = port.default_boundary()
        return Peer.unlinked() if boundary is None else Peer(PeerKind.BOUNDARY, boundary)

    @staticmethod
    def load_types_agree(first: Optional[lt.LoadTypes], second: Optional[lt.LoadTypes]) -> bool:
        """Whether two outputs can carry the same flow: either is unknown, or the port types agree.

        The rule is HiSim's one spelling of it (:meth:`hisim.config.channels.PortTypeCompatibility.load_types_agree`),
        equal load types or the ``ANY`` wildcard; only an unknown load type, an output the wiring has no record of,
        is added here.
        """
        return first is None or second is None or PortTypeCompatibility.load_types_agree(first, second)

    def metered_peer(self, component_name: str, field_name: str, port: EnergyPort) -> Optional[Peer]:
        """The peer of a metered port (:attr:`~hisim.energy_port.EnergyPort.is_metered`); None when nothing reads it.

        The port watches its own output, whatever its role, and its peer is the first that reads it:

        1. a component that reads it directly and is not exempt: a meter of the port's carrier first, then a
           component that declares ports, then the first in the order of the run;
        2. else a meter of the port's carrier reached through exempt components, breadth first. An exempt component
           passes the flow on through its outputs of the same load type; for example, the energy manager reads a heat
           pump's electricity and gives its sum with the other devices' to the electricity meter. What an exempt
           component hands to anything but a meter is a decision, not the flow, so only a meter is taken there.

        Args:
            component_name: The port's component.
            field_name: The port's output.
            port: The metered port.

        Returns:
            The meter or the component, or None.
        """
        carrier = port.carrier
        watched = field_name
        direct = self.readers.get((component_name, watched), [])
        candidates = [name for name in direct if self.meters(name, carrier) or self.moves_energy(name)]
        candidates.sort(
            key=lambda name: (
                not self.meters(name, carrier),
                not self.declared.get(name, False),
                self.order.get(name, 0),
            )
        )
        if candidates:
            first = candidates[0]
            return Peer.meter(first) if self.meters(first, carrier) else Peer(PeerKind.COMPONENT, first)
        load_type = self.load_types.get((component_name, watched))
        queue = [name for name in direct if name in self.exempt]
        visited = set(queue)
        while queue:
            relay = queue.pop(0)
            for relay_output in self.outputs.get(relay, []):
                if not self.load_types_agree(load_type, self.load_types.get((relay, relay_output))):
                    continue
                for reader in self.readers.get((relay, relay_output), []):
                    if self.meters(reader, carrier):
                        return Peer.meter(reader)
                    if reader in self.exempt and reader not in visited:
                        visited.add(reader)
                        queue.append(reader)
        return None

    def resolve_loop(
        self, component_name: str, port_set: FluidLoopPortSet
    ) -> Tuple[Optional[LoopColumns], Optional[LoopQuantity]]:
        """The three outputs a port set's channels stand for, an input followed to the output connected to it.

        The flow is the one the pump owner moves (:attr:`hisim.component.FluidLoopPortSet.pumped_flow_quantity`): the
        pump owner's own ``FLOW_OUT``, the ``FLOW_IN`` of a member that passes it on; the supply is the supplier's
        ``TEMPERATURE_OUT`` and the receiver's ``TEMPERATURE_IN``, the return the other temperature. For example, a
        buffer's ``generator`` set (``supplies=False``, ``pumps=False``) resolves to the generator's flow and supply
        outputs, which its inputs read, and its own return output.

        Args:
            component_name: The end's component.
            port_set: The port set it declares.

        Returns:
            The loop's outputs and None; or None and the first quantity whose input is not connected.
        """
        supplies = port_set.loop.supplies
        quantity_of_leg = {
            "mass_flow": port_set.pumped_flow_quantity,
            "supply_temperature": LoopQuantity.TEMPERATURE_OUT if supplies else LoopQuantity.TEMPERATURE_IN,
            "return_temperature": LoopQuantity.TEMPERATURE_IN if supplies else LoopQuantity.TEMPERATURE_OUT,
        }
        resolved: Dict[str, Tuple[str, str]] = {}
        for leg in LoopColumns.LEGS:
            quantity = quantity_of_leg[leg]
            name = port_set.channels[quantity].field_name
            if not quantity.is_input:
                resolved[leg] = (component_name, name)
                continue
            source = self.source_outputs.get((component_name, name))
            if source is None:
                return None, quantity
            resolved[leg] = source
        return LoopColumns(**resolved), None

    def loop_peer(
        self,
        component_name: str,
        columns: LoopColumns,
        ends_at: Mapping[str, List[Tuple[FluidLoopPortSet, LoopColumns]]],
    ) -> Optional[str]:
        """The other end of a loop, found from the wiring alone; None when there is none.

        The component that reads the loop's mass flow is the other end of the flow's owner, and the owner the
        other end of the component that reads it (the hydronic coupling spec, §3.5). Among several readers, one that
        declares an end of this loop comes first, then one that declares ports, then the first in the order of
        the run; exempt components and meters are skipped. A flow nobody reads (a boiler whose distribution system
        pumps its own flow) falls back to the component that publishes or reads the loop's temperatures. For
        example, the buffer is the other end of the boiler's space-heating loop, and the boiler of the buffer's.

        Args:
            component_name: The end's component.
            columns: The loop's outputs.
            ends_at: Every declared loop end of the run, by component.

        Returns:
            The other end's component name, or None.
        """
        owner, flow = columns.mass_flow
        if owner != component_name:
            return owner if self.moves_energy(owner) else None

        def declares_this_loop(name: str) -> bool:
            return any(other.joins_the_same_outputs(columns) for _, other in ends_at.get(name, []))

        readers = [name for name in self.readers.get((owner, flow), []) if self.moves_energy(name)]
        if not readers:
            touching: List[str] = []
            for leg in ("supply_temperature", "return_temperature"):
                source, output = getattr(columns, leg)
                touching.append(source)
                touching.extend(self.readers.get((source, output), []))
            readers = [name for name in touching if name != component_name and self.moves_energy(name)]
        readers.sort(
            key=lambda name: (
                not declares_this_loop(name),
                not self.declared.get(name, False),
                self.order.get(name, 0),
            )
        )
        return readers[0] if readers else None

    def loops(self, components: Sequence[Any]) -> LoopWiring:
        """Resolve every declared loop end of the run, find its other end and check the loop rules.

        The rules of :mod:`hisim.energy_port`, each breach a fault: two components besides its owner declaring an end
        on one mass flow ("one reader"), an end whose other end declares ports but not the same loop ("every member
        or none"), a loop with no or two suppliers (rule 4 of §3.3 of ``roadmap/fluid_loops_spec.md``), and an end
        that an exempt component or a meter declares, which moves no water of its own and is not resolved. The build
        refuses them through the energy-port setup rules; the balance after the run reads them again.

        Args:
            components: The components of the run, in the order of the run.

        Returns:
            The resolved ends with their peers, the open ends and the faults by rule.

        Raises:
            FluidLoopError: If a component tags a loop by half, which the build refuses first (setup rule ``FL-01``).
        """
        wired = LoopWiring()
        resolved: List[Tuple[str, FluidLoopPortSet, LoopColumns]] = []
        for component in components:
            name = component.component_name
            for port_set in DeclaredPorts.port_sets_of(component):
                if not self.moves_energy(name):
                    wired.node_end_faults.append(
                        LoopFault(
                            name,
                            f"{name} declares the fluid loop {port_set.loop.name!r}, but it is exempt from the energy "
                            "balance or a meter: it moves no water of its own.",
                        )
                    )
                    continue
                columns, open_quantity = self.resolve_loop(name, port_set)
                if columns is None:
                    wired.open_ends.append((name, port_set, open_quantity or port_set.pumped_flow_quantity))
                else:
                    resolved.append((name, port_set, columns))
        ends_at: Dict[str, List[Tuple[FluidLoopPortSet, LoopColumns]]] = {}
        for name, end, columns in resolved:
            ends_at.setdefault(name, []).append((end, columns))
        for name, end, columns in resolved:
            peer_name = self.loop_peer(name, columns, ends_at)
            wired.ends.append((name, end, columns, peer_name))
            fault = self.pairing_fault(name, end, columns, peer_name, ends_at)
            if fault is not None:
                wired.pairing_faults.append(LoopFault(name, fault))
        declaring: Dict[Tuple[str, str], List[str]] = {}
        for name, _, columns in resolved:
            readers = declaring.setdefault(columns.mass_flow, [])
            if columns.mass_flow[0] != name and name not in readers:
                readers.append(name)
        wired.shared_flow_faults.extend(
            LoopFault(
                flow[0],
                f"the mass flow {'.'.join(flow)} is read by {len(names)} components that declare a loop on it "
                f"({', '.join(names)}): one flow delivers its heat once, a split needs a valve with one loop per branch",
                flow[1],
            )
            for flow, names in declaring.items()
            if len(names) > 1
        )
        return wired

    def pairing_fault(
        self,
        name: str,
        end: FluidLoopPortSet,
        columns: LoopColumns,
        peer_name: Optional[str],
        ends_at: Mapping[str, List[Tuple[FluidLoopPortSet, LoopColumns]]],
    ) -> Optional[str]:
        """Why a loop end does not pair with its other end, or None when it does or need not.

        The other end must tag a loop on the same three outputs, and exactly one of the two must supply it (rule 4 of
        §3.3 of ``roadmap/fluid_loops_spec.md``): no supplier is a loop of two receivers, a buffer's generator
        loop wired to a tank's coil; two are two generators on one loop with no store between them. One that declares
        no ports at all, or no other end, is no fault: the loop is drawn to an undeclared or an unlinked node
        instead.
        """
        if peer_name is None or not self.declared.get(peer_name, False):
            return None
        matching = [
            other for other, other_columns in ends_at.get(peer_name, []) if other_columns.joins_the_same_outputs(columns)
        ]
        if not matching:
            return (
                f"{name} tags its end (loop {end.loop.name!r}) of the loop {columns.as_dict()}, but its other "
                f"end, {peer_name}, declares ports and not this loop"
            )
        if all(other.loop.supplies == end.loop.supplies for other in matching):
            how_many = "two suppliers" if end.loop.supplies else "no supplier"
            return (
                f"the loop {columns.as_dict()} between {end.label} and "
                f"{', '.join(other.label for other in matching)} has {how_many}: exactly one member of a loop supplies "
                "it (supplies=True)"
            )
        return None

    def peer_of(self, component_name: str, field_name: str, port: EnergyPort) -> Peer:
        """The peer of one output port (complete Sankey spec, §4.3; :mod:`hisim.energy_port` states the rule).

        A storage change goes to the component's own store. A metered port goes to the meter or component that reads
        it (:meth:`metered_peer`), any other port to the component the wiring names (:meth:`component_peer`); an input
        port's peer is :meth:`input_peer`'s. A port
        with no such peer takes its carrier's boundary default, or nobody (unlinked). For example, a heat pump's
        electricity port, which the energy manager reads and passes on summed to the electricity meter, goes to the
        meter "ElectricityMeter"; a boiler's gas port that no meter reads comes from "natural gas supply".

        Args:
            component_name: The port's component.
            field_name: The port's output.
            port: The port.

        Returns:
            The peer.
        """
        if port.role == lt.EnergyRole.STORED_CHANGE:
            return Peer.store_of(component_name)
        if port.is_metered:
            metered = self.metered_peer(component_name, field_name, port)
            if metered is not None:
                return metered
        else:
            source = self.component_peer(component_name, field_name, port)
            if source is not None:
                return Peer(PeerKind.COMPONENT, source)
        boundary = port.default_boundary()
        if boundary is not None:
            return Peer(PeerKind.BOUNDARY, boundary)
        return Peer.unlinked()
