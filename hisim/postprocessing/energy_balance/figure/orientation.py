"""The orientation table of the Sankey figure: on which side of its trunk every flow of a node attaches.

The side of a flow follows from its node's role and from the flow's direction, kind, carrier and the role of its
other end, never from a class or component name. Sides are given relative to the role's *nominal axis* (east for
most roles, south for the batteries): "tail", "head" or a compass letter. A node turned by its tree connection keeps
its arrangement, since the letters are turned into sides relative to the nominal axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Dict, List, Mapping, Optional, Set, Tuple

from hisim import loadtypes as lt
from hisim.postprocessing.energy_balance.figure.geometry import Direction, Side
from hisim.postprocessing.energy_balance.figure.graph import DrawnGraph, DrawnNode, Flow, FlowKind, Role


@dataclass(frozen=True)
class RoleSides:
    """One row of the orientation table: the side of each kind of flow of a role ("tail", "head" or a compass letter).

    Attributes:
        link_in: A link into the node (other than its tree connection and the chain).
        terminal_in: A boundary input or a residual gain.
        link_out: A link out of the node (other than to the chain's next node).
        boundary_out: An output to a boundary node (the taps, grid export, the ambient sink).
        loss_out: A loss or a residual loss.
        storage: A storage change in either direction: the side stays when the sign flips between runs.
    """

    link_in: str
    terminal_in: str
    link_out: str
    boundary_out: str
    loss_out: str
    storage: str


class Orientation:
    """The orientation table: the side of every flow of a node, from its role, the flow's kind, carrier and peer."""

    #: The axis each role is drawn along when it is a root or when nothing else decides it.
    NOMINAL_AXIS: ClassVar[Mapping[Role, Direction]] = {
        Role.ELECTRIC_STORAGE: Direction.S,
        Role.VEHICLE_STORAGE: Direction.S,
    }
    #: The compass side a role's tree connection enters by, where it is not the tail (relative to the nominal axis).
    TREE_SIDE: ClassVar[Mapping[Role, str]] = {Role.HOT_WATER_TANK: "S", Role.HOUSEHOLD: "N", Role.VEHICLE: "N"}
    #: The compass side the losses of a building take, by rank of their value (largest first), repeating.
    BUILDING_LOSS_SIDES: ClassVar[Tuple[str, ...]] = ("head", "N", "S")
    #: The table's rows; the electricity meter, the building and the battery losses have rules of their own.
    TABLE: ClassVar[Mapping[Role, RoleSides]] = {
        Role.FUEL_METER: RoleSides("tail", "tail", "head", "head", "S", "S"),
        Role.PV: RoleSides("tail", "tail", "head", "head", "S", "S"),
        Role.GENERATOR: RoleSides("tail", "S", "N", "head", "S", "S"),
        Role.BUFFER: RoleSides("S", "S", "head", "head", "N", "S"),
        Role.DISTRIBUTION: RoleSides("S", "S", "head", "head", "N", "S"),
        Role.HOT_WATER_TANK: RoleSides("tail", "N", "head", "head", "N", "N"),
        Role.SOLAR_COLLECTOR: RoleSides("S", "tail", "head", "head", "N", "N"),
        Role.HOUSEHOLD: RoleSides("tail", "S", "head", "head", "S", "S"),
        Role.ELECTRIC_STORAGE: RoleSides("tail", "W", "W", "W", "head", "W"),
        Role.VEHICLE_STORAGE: RoleSides("tail", "W", "head", "head", "E", "W"),
        Role.VEHICLE: RoleSides("tail", "N", "head", "head", "S", "S"),
        Role.COOLER: RoleSides("N", "N", "head", "head", "S", "S"),
        Role.CONVERTER: RoleSides("tail", "N", "head", "head", "S", "S"),
        Role.ELECTRICITY_METER: RoleSides("S", "N", "S", "head", "S", "S"),
        Role.BUILDING: RoleSides("S", "S", "head", "head", "S", "S"),
    }

    @staticmethod
    def nominal_axis(role: Role) -> Direction:
        """Return the axis a role is drawn along unless its tree connection decides otherwise (east for most)."""
        return Orientation.NOMINAL_AXIS.get(role, Direction.E)

    @staticmethod
    def sides(graph: DrawnGraph, node: DrawnNode, tree_flow: Optional[str]) -> Dict[str, Side]:
        """Return the side of every flow of a node, by flow id.

        The node's own tree connection takes the side its role names for it (the tail for most, the south side of a
        hot-water tank); a link to the chain's next node takes the head, one from the previous the tail; every other
        flow follows the role's row of the table. Compass sides are turned into sides relative to the role's
        nominal axis, so a node rotated by its tree connection keeps its arrangement.

        Args:
            graph: The drawn graph (roles and chain).
            node: The node.
            tree_flow: The id of the node's flow by which it hangs from its anchor, None for a root.
        """
        role = graph.roles[node.name]
        axis = Orientation.nominal_axis(role)
        loss_rank = {f.flow_id: rank for rank, f in enumerate(sorted(node.flows_where(is_input=False, kind=FlowKind.LOSS), key=lambda f: (-f.magnitude_kwh, f.flow_id)))}
        result: Dict[str, Side] = {}
        seconds = Orientation.parallel_seconds(graph)
        tree = next((f for f in node.flows if f.flow_id == tree_flow), None)
        tail_taken = tree is not None and tree.is_input and (node.name in graph.chain or Orientation.tree_side(graph, role, tree) == "tail")
        for flow in node.flows:
            if flow.link_id in seconds:
                # A second link to the same node runs as an arrow of its own below the first: out of the source's
                # frame bottom, into the target's frame bottom.
                result[flow.flow_id] = Side.RIGHT
                continue
            if flow.flow_id == tree_flow:
                # On the chain every node enters at its tail, so the chain stays one row.
                entry = "tail" if node.name in graph.chain else Orientation.tree_side(graph, role, flow)
                where = entry if flow.is_input else "head"
            else:
                where = Orientation.chain_side(graph, node, flow) or Orientation.rule(graph, role, flow, loss_rank.get(flow.flow_id, 0))
                if role is Role.HOT_WATER_TANK and tail_taken and where == "tail" and flow.kind is FlowKind.LINK:
                    # The tree connection holds the tail; a second heat source stacked beside it would be enclosed
                    # by the tree connection's arrow, so it comes in from the north, where the collector sits.
                    where = "N"
            result[flow.flow_id] = Orientation.to_side(where, axis)
        return result

    @staticmethod
    def tree_side(graph: DrawnGraph, role: Role, flow: Flow) -> str:
        """Return the side a node's tree connection enters by: the role's ``TREE_SIDE``, else the tail.

        A hot-water tank hangs above its generator and takes the generator's heat from the south; when its largest
        heat input is a solar collector (a summer week, the collector covering the hot water), it hangs from the
        collector, which then continues into its tail, and the generator's heat keeps the south side
        (:meth:`tank_rule`).
        """
        if role is Role.HOT_WATER_TANK and graph.roles.get(flow.other_end) is Role.SOLAR_COLLECTOR:
            return "tail"
        return Orientation.TREE_SIDE.get(role, "tail")

    @staticmethod
    def tank_rule(graph: DrawnGraph, flow: Flow) -> Optional[str]:
        """Return "S" for a hot-water tank's heat from a generator that is not its tree parent, else None.

        Example: in a July week the collector feeds the tank's tail; the heat pump's hot-water heat comes in from
        below, as it does when the heat pump is the tank's parent, instead of joining the collector at the tail.
        """
        if flow.is_input and flow.kind is FlowKind.LINK and graph.roles.get(flow.other_end) is Role.GENERATOR:
            return "S"
        return None

    @staticmethod
    def parallel_seconds(graph: DrawnGraph) -> Set[int]:
        """Return the links that run beside a tree connection between the same source and target.

        Example: a gas meter feeds its boiler fuel for space heating (the tree connection) and fuel for hot water; the
        hot-water link is a second. A second is drawn as an arrow of its own, below the first with a visible gap,
        instead of a stub stacked under the tree connection (which reads as a misaligned edge).
        """
        tree_links = {edge.link_id for tree in graph.trees for edge in tree.edges}
        pairs: Dict[Tuple[str, str], List[int]] = {}
        for link in graph.links:
            pairs.setdefault((link.source, link.target), []).append(link.link_id)
        seconds: Set[int] = set()
        for link_ids in pairs.values():
            # Two cross-links between the same nodes are already two routed arrows (the household's heat flows).
            if len(link_ids) > 1 and any(k in tree_links for k in link_ids):
                seconds.update(k for k in link_ids if k not in tree_links)
        return seconds

    @staticmethod
    def to_side(where: str, axis: Direction) -> Side:
        """Turn "tail", "head" or a compass letter into a side of a trunk with the given axis."""
        if where in ("tail", "head"):
            return Side(where)
        return Side.facing(Direction(where), axis)

    @staticmethod
    def chain_side(graph: DrawnGraph, node: DrawnNode, flow: Flow) -> Optional[str]:
        """Return "head" for the link to the chain's next node, "tail" for the link from the previous one, else None."""
        if flow.kind is not FlowKind.LINK or node.name not in graph.chain:
            return None
        index = graph.chain.index(node.name)
        if not flow.is_input and index + 1 < len(graph.chain) and flow.other_end == graph.chain[index + 1]:
            return "head"
        if flow.is_input and index > 0 and flow.other_end == graph.chain[index - 1]:
            return "tail"
        return None

    @staticmethod
    def rule(graph: DrawnGraph, role: Role, flow: Flow, loss_rank: int) -> str:
        """Return the side of one flow by its role's row: "tail", "head" or a compass letter (relative to the nominal axis).

        The few context rules come first (the solar cut, the electricity meter, the space-heating output of a
        generator, losses ranked by value); everything else is a lookup of the role's row by the flow's direction and
        kind. Example: a buffer's standby loss goes north, its storage change south.
        """
        if flow.kind is FlowKind.CUT:
            return "tail"
        if role is Role.ELECTRICITY_METER:
            special = Orientation.meter_rule(graph, flow)
        elif role is Role.HOT_WATER_TANK:
            special = Orientation.tank_rule(graph, flow)
        else:
            special = Orientation.context_rule(role, flow, loss_rank)
        if special is not None:
            return special
        row = Orientation.TABLE[role]
        if flow.kind is FlowKind.STORAGE:
            return row.storage
        if flow.is_input:
            return row.link_in if flow.kind is FlowKind.LINK else row.terminal_in
        if flow.kind is FlowKind.LINK:
            return row.link_out
        return row.boundary_out if flow.kind is FlowKind.BOUNDARY else row.loss_out

    @staticmethod
    def context_rule(role: Role, flow: Flow, loss_rank: int) -> Optional[str]:
        """Return the side of a flow whose side depends on more than its kind, else None.

        A generator sends space heating along the chain (head) and other heat north; a building's sun comes from the
        north and its space heating into its tail, its losses go head, north, south by rank; a battery's largest
        loss goes east, the others to its head.
        """
        carrier = lt.EnergyBalanceCarrier
        if role is Role.GENERATOR and not flow.is_input and flow.kind is FlowKind.LINK and flow.carrier == carrier.SPACE_HEATING_HEAT.value:
            return "head"
        if role is Role.BUILDING:
            if flow.kind is FlowKind.LOSS:
                return Orientation.BUILDING_LOSS_SIDES[loss_rank % len(Orientation.BUILDING_LOSS_SIDES)]
            if flow.is_input and flow.carrier == carrier.SOLAR.value:
                return "N"
            if flow.is_input and flow.kind is FlowKind.LINK and flow.carrier == carrier.SPACE_HEATING_HEAT.value:
                return "tail"
        if role is Role.ELECTRIC_STORAGE and flow.kind is FlowKind.LOSS:
            return "E" if loss_rank == 0 else "head"
        return None

    @staticmethod
    def meter_rule(graph: DrawnGraph, flow: Flow) -> Optional[str]:
        """Return the side of an electricity meter's flow where it depends on the system, else None.

        Electricity from a PV or another source without inputs comes into the tail; grid import comes from the north
        when a PV feeds the tail, else into the tail; grid export goes north from the chain's root, else to the head;
        electricity to a collector or a cooler (both above the chain) goes north, to a generator or converter on the
        chain to the head.
        """
        other_role = graph.roles.get(flow.other_end)
        is_link = flow.kind is FlowKind.LINK
        if flow.is_input and is_link:
            source = graph.node(flow.other_end)
            feeds_alone = not source.flows_where(is_input=True, kind=FlowKind.LINK) and other_role is not Role.SOLAR_COLLECTOR
            return "tail" if other_role is Role.PV or feeds_alone else None
        if flow.is_input and flow.kind is FlowKind.BOUNDARY:
            has_pv = any(f.kind is FlowKind.LINK and graph.roles.get(f.other_end) is Role.PV for f in graph.node(flow.node).flows)
            return "N" if has_pv else "tail"
        if not flow.is_input and flow.kind is FlowKind.BOUNDARY:
            return "N" if graph.chain and graph.chain[0] == flow.node else "head"
        if not flow.is_input and is_link and other_role in (Role.SOLAR_COLLECTOR, Role.COOLER):
            return "N"
        if not flow.is_input and is_link and other_role in (Role.GENERATOR, Role.CONVERTER) and flow.other_end in graph.chain:
            return "head"
        return None
