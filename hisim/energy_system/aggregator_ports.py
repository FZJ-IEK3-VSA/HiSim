"""The three rules that keep an aggregator's grown port set consistent.

An aggregator does not declare a port per participant; it grows one per feed, with a name derived
from the participant and the port being measured. That makes three things checkable that an
ordinary component never has to worry about, and all three are checked here, before and after the
aggregator is asked to create anything.

A participant's ``(name, output)`` pair must be **unique** per aggregator, which is what makes the
derived names collision-free without an index counter — and the duplicate that matters is the one
that only appears after a bare item has been expanded, since the file itself then looks fine while
the flow is counted twice. A derived name must be **free**, because the templates never
disambiguate: a counter would make a name depend on how many participants happened to be declared
before it, and these names are quoted in stored analyses. And afterwards the promised ports must
**exist**, because an aggregator with a hand-written creation hook is otherwise free to invent its
own names and turn a frozen part of the format into whatever it likes.

The checks are classmethods over explicit arguments and reach into components only through the
port lists every component has, so this module imports no component code. They raise nothing: a
broken rule is appended to the caller's list of
:class:`~hisim.energy_system.errors.WiringRefusal` records, every one of a batch, and the wiring
rules of the setup rules refuse them (``hisim/setup_rules/wiring.py``: ``EF-25``, ``EF-32``,
``EF-21`` and ``EF-22``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from hisim.energy_system.errors import EnergySystemErrorId, WiringRefusal
from hisim.energy_system.resolution import ResolvedDynamicConnection


class AggregatorPortChecker:
    """The uniqueness, freedom and existence rules for the ports an aggregator grows.

    Grouped into one class rather than left as free functions because they are one rule set with
    one subject — the port names a set of resolved feeds implies — and because two of them run
    either side of the single call that asks the aggregator to create anything, so a reader has to
    see them together to see that the aggregator is left untouched when one of them fails.
    """

    @classmethod
    def check_participant_ports_are_unique(
        cls, target_name: str, resolved: Sequence[ResolvedDynamicConnection], refusals: List[WiringRefusal]
    ) -> None:
        """Records every participant ``(name, output)`` pair an aggregator measures more than once.

        This is what makes the derived port names collision-free without an index counter, and
        it covers the pairs that only appear once bare items have been expanded. Example: a meter
        fed ``occupancy.ElectricalPowerConsumption`` by a bare item and by a written feed records
        one ``EF-25`` refusal naming both.

        Args:
            target_name: Name of the aggregator.
            resolved: Its resolved connections.
            refusals: Where each repetition is recorded (``EF-25``).
        """
        seen: Dict[Tuple[str, str], ResolvedDynamicConnection] = {}
        for connection in resolved:
            key = (connection.source_name, connection.source_output)
            previous = seen.get(key)
            if previous is not None:
                refusals.append(
                    WiringRefusal(
                        EnergySystemErrorId.DUPLICATE_FEED,
                        f"components.{target_name}.inputs",
                        f"'{target_name}' measures '{key[0]}.{key[1]}' twice: via "
                        f"{previous.describe()} and via {connection.describe()}.",
                        remedy="A participant's output feeds one aggregator at most once.",
                    )
                )
                continue
            seen[key] = connection

    @classmethod
    def check_port_names_are_free(
        cls, target_name: str, target: Any, resolved: Sequence[ResolvedDynamicConnection], refusals: List[WiringRefusal]
    ) -> List[str]:
        """Records every derived port name the aggregator already uses, and returns the names to create.

        Resolution never disambiguates by appending a counter — that is the point of the derived
        names — so a collision with a declared port or within the batch refuses the build. The
        check runs before the aggregator creates anything, and a target with a refusal creates
        nothing, so a failing file leaves the component untouched.

        Args:
            target_name: Name of the aggregator.
            target: The aggregator component.
            resolved: Its resolved connections, already sorted.
            refusals: Where each collision is recorded (``EF-32``).

        Returns:
            The names resolution is about to create, inputs and dispatch outputs together; a
            colliding name is not among them.
        """
        existing = {port.field_name for port in target.inputs}
        existing |= {port.field_name for port in target.outputs}
        created: List[str] = []
        for connection in resolved:
            for name in (connection.aggregator_input_name, connection.dispatch_output_name):
                if name is None:
                    continue
                if name in existing:
                    refusals.append(
                        WiringRefusal(
                            EnergySystemErrorId.PORT_NAME_COLLISION,
                            f"components.{target_name}.inputs",
                            f"resolving {connection.describe()} would create the port '{name}' on "
                            f"'{target_name}', which already has a port of that name.",
                            remedy=(
                                "Derived port names carry no counter; rename the participant or "
                                "the output it is measured on."
                            ),
                        )
                    )
                    continue
                existing.add(name)
                created.append(name)
        return created

    @classmethod
    def check_ports_were_created(
        cls, target_name: str, target: Any, resolved: Sequence[ResolvedDynamicConnection], refusals: List[WiringRefusal]
    ) -> None:
        """Records every port the aggregator did not create although a derived name promises it.

        The naming templates are part of the format because the names end up in result files and
        lookups, so an aggregator with a hand-written resolution hook must not be free to invent
        its own. Recording it here turns such a mistake into a precise refusal instead of a
        missing column noticed weeks later.

        Args:
            target_name: Name of the aggregator.
            target: The aggregator component.
            resolved: Its resolved connections.
            refusals: Where a missing input (``EF-22``) or dispatch output (``EF-21``) is recorded.
        """
        inputs = {port.field_name for port in target.inputs}
        outputs = {port.field_name for port in target.outputs}
        for connection in resolved:
            if connection.aggregator_input_name not in inputs:
                refusals.append(
                    WiringRefusal(
                        EnergySystemErrorId.UNKNOWN_INPUT_PORT,
                        f"components.{target_name}.inputs",
                        f"'{target_name}' ({target.get_full_classname()}) did not create the input "
                        f"'{connection.aggregator_input_name}' while resolving "
                        f"{connection.describe()}.",
                        remedy=(
                            "An aggregator creates one input per feed, named by the derived "
                            "template."
                        ),
                    )
                )
            dispatch_name = connection.dispatch_output_name
            if dispatch_name is not None and dispatch_name not in outputs:
                refusals.append(
                    WiringRefusal(
                        EnergySystemErrorId.UNKNOWN_OUTPUT_PORT,
                        f"components.{target_name}.inputs",
                        f"'{target_name}' ({target.get_full_classname()}) did not create the "
                        f"dispatch output '{dispatch_name}' while resolving "
                        f"{connection.describe()}.",
                        remedy=(
                            "An aggregator creates one output per dispatch block, named by the "
                            "derived template."
                        ),
                    )
                )
