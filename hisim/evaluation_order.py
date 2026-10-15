"""The order of the components within one kind of a pass: the L2 controllers and the physics.

The simulator evaluates a pass kind by kind (data sources, L2 controllers, L1 controllers, physics, meters). Within two
kinds the order matters too. An L2 controller that reads another L2 controller's signal, such as the heating curve
applying the energy manager's raise, gets the signal of the same pass only if the other is evaluated first:
:class:`L2Order` orders the L2 kind so. An L1 controller that decides from what its device reports and from the answer
of a store it reads, such as a hot-water tank's end-of-step temperature, pairs two values of the same run only if, in
every pass, the device is evaluated before the store: :class:`PhysicsOrder` orders the physics kind so. Both rules use
the kinds and the wiring alone, and both place their cycles the same way (:meth:`ConstraintGraph.ordered`). A kind is
a kind base class of :mod:`hisim.component`, such as :class:`~hisim.component.PhysicsComponent`.
"""

from dataclasses import dataclass
from typing import AbstractSet, Dict, FrozenSet, List, Sequence, Set, Tuple, Type

from hisim.component import Component, L1ControllerComponent, L2ControllerComponent, PhysicsComponent


@dataclass(frozen=True)
class EvaluationOrder:
    """The order a pass evaluates components in, and the components whose ordering constraints form a cycle.

    A position is a component's index in the order the components were added. For example, a boiler added at
    position 1 and a tank added at position 0, with the boiler's controller at position 2, give the positions
    (2, 1, 0): the controller, the boiler, the tank. The same type holds the order of all components of a pass
    (:meth:`hisim.simulator.Simulator.evaluation_positions`) and the order of one kind alone
    (:meth:`PhysicsOrder.ordered`, :meth:`L2Order.ordered`).
    """

    #: The positions of the components the order covers, in the order a pass evaluates them: all components for a
    #: pass, the physics or L2 positions alone for the order of one kind.
    positions: Tuple[int, ...]

    #: Each group of physics positions whose ordering constraints require one another first, in the order added. The
    #: components of a group keep the order they were added in. Empty for the order of the L2 kind.
    physics_cycles: Tuple[Tuple[int, ...], ...]

    #: Each group of L2 controller positions that read one another in a cycle, in the order added. The controllers of
    #: a group keep the order they were added in, so a member that reads a later member applies its signal of the pass
    #: before; no other L2 controller reads a later one. Empty for the order of the physics kind.
    l2_cycles: Tuple[Tuple[int, ...], ...] = ()


class ConstraintGraph:
    """The order and the cycles of a graph of ordering constraints, which every kind's order rule shares.

    A constraint graph gives, for every position, the positions that must be evaluated before it. The physics rule
    (:class:`PhysicsOrder`) and the L2 rule (:class:`L2Order`) build their own graphs and order them, cycles
    included, here, so both place a cycle the same way.
    """

    @staticmethod
    def cycles(predecessors: Dict[int, FrozenSet[int]]) -> Tuple[Tuple[int, ...], ...]:
        """Return the groups of positions whose constraints require one another first, each in the order added.

        A group is a set of two or more positions each of which must, through the constraints, come before every
        other (a strongly connected component of the constraint graph). For example,
        ``{0: frozenset({1}), 1: frozenset({0}), 2: frozenset({0})}`` gives ``((0, 1),)``.

        Args:
            predecessors: For every position, the positions that must come before it.

        Returns:
            The groups, ordered by their first position.
        """
        reachable: Dict[int, Set[int]] = {}
        for start in predecessors:
            seen: Set[int] = set()
            stack = list(predecessors[start])
            while stack:
                position = stack.pop()
                if position not in seen:
                    seen.add(position)
                    stack.extend(predecessors[position])
            reachable[start] = seen
        groups = {
            tuple(sorted({start} | {other for other in reachable[start] if start in reachable[other]}))
            for start in predecessors
            if start in reachable[start]
        }
        return tuple(sorted(groups))

    @staticmethod
    def ordered(
        predecessors: Dict[int, FrozenSet[int]],
    ) -> Tuple[Tuple[int, ...], Tuple[Tuple[int, ...], ...]]:
        """Return the positions in an order that keeps every constraint outside a cycle, and the cycles.

        Each cycle (:meth:`cycles`) becomes one block, and every other position a block of its own. The next block is
        always the first one, by its first position, whose predecessors outside it have all been placed; its
        positions are then evaluated one after another in the order added. So without constraints the order is the
        order added, a block goes where its first member would go, and only a member of a cycle waits for a position
        evaluated after it. For example, ``{0: frozenset({1}), 1: frozenset({2}), 2: frozenset({1})}`` (0 waits for
        1, and 1 and 2 for each other) gives the positions (1, 2, 0) and the cycle (1, 2): 0 goes after the cycle,
        not before it.

        Args:
            predecessors: For every position, the positions that must come before it; every one of them is a key.

        Returns:
            The positions in evaluation order, and the cycles ordered by their first position.
        """
        cycles = ConstraintGraph.cycles(predecessors)
        block_of: Dict[int, Tuple[int, ...]] = {position: (position,) for position in predecessors}
        for group in cycles:
            for position in group:
                block_of[position] = group
        blocks = sorted(set(block_of.values()))
        waiting_for = {
            block: {block_of[before] for position in block for before in predecessors[position]} - {block}
            for block in blocks
        }
        order: List[int] = []
        placed: Set[Tuple[int, ...]] = set()
        while len(placed) < len(blocks):
            block = min(block for block in blocks if block not in placed and waiting_for[block] <= placed)
            placed.add(block)
            order.extend(block)
        return tuple(order), cycles


class PhysicsOrder:
    """The rule that orders the physics kind within a pass: every device an L1 controller commands goes first.

    Terms: an L1 controller *commands* a physics component when one of the controller's outputs feeds one of that
    component's inputs; that component is one of its *devices*. The controller *reads* a physics component when one of
    that component's outputs feeds one of the controller's inputs.

    The rule: for every L1 controller, each of its devices is evaluated before every physics component the controller
    reads that is not one of its own devices. Everything else keeps the order the components were added in (a stable
    topological sort). For example, a hot-water tank added before the boiler and the collector that charge it is
    evaluated after both, because the boiler's and the collector's controllers read the tank.

    Why: a controller that pairs what its device reports with the store's answer reads the store's answer of the same
    pass only if the device runs first. With the store evaluated before the device the store's answer is one pass
    older than the device's report, and an iteration that pairs them can circle its fixed point instead of settling.
    The rule uses only the kinds and the wiring, so no component depends on how another is modelled.
    """

    @staticmethod
    def predecessors(
        kinds: Sequence[Type[Component]], sources: Sequence[AbstractSet[int]]
    ) -> Dict[int, FrozenSet[int]]:
        """Return, for every physics position, the physics positions the rule requires to be evaluated before it.

        For example, a tank at 0 read by a controller at 2 that commands a boiler at 1 gives
        ``{0: frozenset({1}), 1: frozenset()}``: the tank waits for the boiler, the boiler for nothing.

        Args:
            kinds: The kind base class of every component (:meth:`hisim.component.ComponentKinds.of`), in the order
                the components were added.
            sources: For every component, the positions of the components whose outputs feed its inputs.

        Returns:
            For every physics position, the devices that must come before it.

        Raises:
            ValueError: If ``kinds`` and ``sources`` differ in length.
        """
        if len(kinds) != len(sources):
            raise ValueError(f"Every component needs its sources: {len(kinds)} kinds, but {len(sources)} source sets.")
        physics = [position for position, kind in enumerate(kinds) if kind is PhysicsComponent]
        before: Dict[int, Set[int]] = {position: set() for position in physics}
        for controller, kind in enumerate(kinds):
            if kind is not L1ControllerComponent:
                continue
            devices = {position for position in physics if controller in sources[position]}
            read = {position for position in sources[controller] if position in before} - devices
            for store in read:
                before[store] |= devices
        return {position: frozenset(devices) for position, devices in before.items()}

    @staticmethod
    def ordered(kinds: Sequence[Type[Component]], sources: Sequence[AbstractSet[int]]) -> EvaluationOrder:
        """Return the physics positions in the order the rule gives, and the groups that form a cycle.

        Among the components that may be evaluated next, the one added first goes first, so without constraints the
        order is the order added. The components of a cycle keep the order they were added in and are evaluated one
        after another, at the place of the first of them that may go; the constraints between them are dropped. For
        example, a tank at 0, an L1 controller at 1 that reads the tank and commands a boiler at 2 give the physics
        positions (2, 0).

        Args:
            kinds: The kind base class of every component, in the order the components were added.
            sources: For every component, the positions of the components whose outputs feed its inputs.

        Returns:
            The physics positions only, in evaluation order, and the cycles among them.
        """
        positions, cycles = ConstraintGraph.ordered(PhysicsOrder.predecessors(kinds, sources))
        return EvaluationOrder(positions=positions, physics_cycles=cycles)


class L2Order:
    """The rule that orders the L2 controllers within a pass: every L2 controller after the L2 controllers it reads.

    An L2 controller *reads* another when one of the other's outputs feeds one of its inputs. The order added is kept
    wherever the reads allow it. For example, the heating curve added before the energy manager whose raise it
    applies is evaluated after it.

    Why: an L2 signal read by another L2 controller then reaches the reader, and through it the L1 controllers, in
    the same pass; evaluated the other way round the reader applies the signal of the pass before, a pass late.
    """

    @staticmethod
    def ordered(kinds: Sequence[Type[Component]], sources: Sequence[AbstractSet[int]]) -> EvaluationOrder:
        """Return the L2 positions reordered so that each comes after the L2 controllers it reads, and their cycles.

        The next controller is always the first one, in the order added, whose L2 sources have all been placed.
        Controllers that read one another in a cycle are placed as one block, as the physics rule places its cycles
        (:meth:`ConstraintGraph.ordered`): the block goes where its first member would go, once every controller its
        members read outside it has been placed, and its members keep the order added. So only a member of a cycle
        reads a controller evaluated after it, and the cycle is reported. For example, with the L2 controllers at 3
        and 7, 3 reading 7 gives the positions (7, 3); neither reading the other keeps (3, 7); each reading the other
        keeps (3, 7), with the cycle (3, 7). With the L2 controllers 0, 1 and 2, where 0 reads 1 and 1 and 2 read each
        other, the positions are (1, 2, 0), with the cycle (1, 2): 0 goes after the cycle it reads into.

        Args:
            kinds: The kind base class of every component (:meth:`hisim.component.ComponentKinds.of`), in the order
                the components were added.
            sources: For every component, the positions of the components whose outputs feed its inputs.

        Returns:
            The L2 positions only, in evaluation order, with the groups of them that read one another in a cycle as
            :attr:`EvaluationOrder.l2_cycles`; :attr:`EvaluationOrder.physics_cycles` is empty.

        Raises:
            ValueError: If ``kinds`` and ``sources`` differ in length.
        """
        if len(kinds) != len(sources):
            raise ValueError(f"Every component needs its sources: {len(kinds)} kinds, but {len(sources)} source sets.")
        l2_positions = {position for position, kind in enumerate(kinds) if kind is L2ControllerComponent}
        predecessors = {
            position: frozenset((set(sources[position]) & l2_positions) - {position})
            for position in sorted(l2_positions)
        }
        positions, cycles = ConstraintGraph.ordered(predecessors)
        return EvaluationOrder(positions=positions, physics_cycles=(), l2_cycles=cycles)
