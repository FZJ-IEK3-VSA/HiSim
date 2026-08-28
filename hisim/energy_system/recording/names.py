"""The two identity rules a component has to satisfy before it can be written into a file.

Everything else the recorder produces is a translation of something the runtime already holds. The
two rules here are the only places where the recorder refuses instead: a component whose runtime
name is not an identifier cannot become a key of an energy-system file, and a component whose
identity carries a building or a unit cannot be written at all, because the format rebuilds the
identity from the key alone and would silently drop the other two halves.

The second rule is a guard rather than a translation. ``ComponentID.key`` joins building, unit and
name with underscores and is never parsed back, so a district file recorded today would come back
with ``building=None`` and a name equal to the old key — and the electricity meter branches on
exactly that field. No setup in ``system_setups/`` sets one, so the guard costs nothing now and
turns the hazard into a refusal the day one does, instead of into a number that quietly changed.

Port names get the same treatment for one narrow reason: an explicit wire writes the source's port
as the dotted half of a reference, and the format's reference grammar accepts only identifiers
there. A target port is written under its own key and is free of the rule, which is why the two
directions are checked separately rather than by one sweep over every port a component has.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Tuple

from hisim.config import NameSyntax
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemRecordingError


class RecordedNames:
    """The name and identity checks the recorder runs before it writes anything.

    Grouped into one class because they answer one question — can this component be addressed by
    name in an energy-system file — and because a caller that runs one of them always runs the
    others for the same component. Every method either returns the validated string or raises, so
    a call site never has to branch on a boolean and then invent its own message.

    All methods are classmethods over explicit arguments; the class holds no state and is never
    instantiated. The setup module is passed through into every message, because the person reading
    a recording failure is looking at a Python file and needs to be told which one.
    """

    #: What a component name stands for in the format's own vocabulary, used in the message so
    #: that a rejection reads the same whether it came from a file key or from a runtime name.
    COMPONENT_ROLE: ClassVar[str] = "component"

    #: The two halves of ``ComponentID`` a recorded file cannot carry, because the format rebuilds
    #: the identity from the entry's key and the key alone.
    QUALIFYING_FIELDS: ClassVar[Tuple[str, ...]] = ("building", "unit")

    @classmethod
    def check_component_name(cls, name: Any, setup: str) -> str:
        """Returns the runtime name if it can be a file key, and raises otherwise.

        Args:
            name: The component's runtime name, of any type.
            setup: The setup module being recorded, for the message.

        Returns:
            The validated name.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` naming the component, the rule and the setup.
        """
        problem = NameSyntax.explain_violation(name)
        if problem is not None:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_NAME_INVALID,
                f"{setup}:{name}",
                f"'{name}' cannot be the key of an energy-system file: {problem}.",
                remedy=(
                    "Rename the component in the setup. The runtime name is also the file key and "
                    "the result-column prefix, so the recorder must not sanitize it here."
                ),
            )
        return str(name)

    @classmethod
    def check_source_port(cls, component_name: str, port_name: str, setup: str) -> str:
        """Returns a source port name if a reference can address it, and raises otherwise.

        An input item writes the producing port as the second half of ``from: <component>.<port>``,
        and the format's reference grammar accepts an identifier there and nothing else. A port
        that breaks the rule is therefore unwritable rather than merely ugly, and it is the
        producing component's defect, which is what the message says.

        Args:
            component_name: Runtime name of the component declaring the port.
            port_name: The output's field name.
            setup: The setup module being recorded, for the message.

        Returns:
            The validated port name.

        Raises:
            EnergySystemRecordingError: ``EF-R1`` naming the component, the port and the setup.
        """
        problem = NameSyntax.explain_violation(port_name)
        if problem is not None:
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_NAME_INVALID,
                f"{setup}:{component_name}.{port_name}",
                f"the output '{port_name}' of '{component_name}' cannot be referenced: {problem}.",
                remedy="Rename the output on the component class; a reference addresses a port by name.",
            )
        return port_name

    @classmethod
    def check_identity(cls, name: str, config: Any, setup: str) -> None:
        """Refuses a component whose identity carries a building or a unit.

        Args:
            name: The component's runtime name, for the message.
            config: Its live configuration object, whose ``component_id`` is inspected.
            setup: The setup module being recorded, for the message.

        Raises:
            EnergySystemRecordingError: ``EF-R2`` naming the component, the field and the setup.
        """
        identity = getattr(config, "component_id", None)
        for field_name in cls.QUALIFYING_FIELDS:
            value = getattr(identity, field_name, None)
            if value is None:
                continue
            raise EnergySystemRecordingError(
                EnergySystemErrorId.RECORDED_QUALIFIED_IDENTITY,
                f"{setup}:{name}",
                f"'{name}' carries the {field_name} '{value}' in its identity, which an "
                "energy-system file cannot express: an entry's key is its whole name.",
                remedy=(
                    "Recording a district system needs the format to carry the qualified identity "
                    "first; until then such a setup is out of scope rather than recorded lossily."
                ),
            )
