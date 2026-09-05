"""The name and reference grammar every part of the energy-system format shares.

One identifier syntax runs through the whole format — component names, group names, variant
and option names, fact names and port names all use it — and one compound form joins two of
them with a dot. Keeping that grammar in a module of its own says what it is: a property of
the wire format rather than of the models, which is why the loader, the schema exporter and
the models themselves all reach for the same class instead of each spelling a pattern.

The grammar matters most for what it refuses. Wildcards and path syntax are the vocabulary
of a preprocessor this format version does not have, and accepting them silently would let a
file written against that expectation resolve to something its author never meant. Both are
therefore rejected here, at the one place a name or a reference enters the model at all.
"""

# clean

from __future__ import annotations

from typing import Any, ClassVar, Optional, Pattern, Tuple

from hisim.config import NameSyntax
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError


class NameRules:
    """The identifier and reference grammar shared by every part of the format.

    Component names, group names, variant and option names, fact names and port names all use
    one identifier syntax — a letter or underscore followed by letters, digits or underscores —
    so a reader never has to remember which allows what. A reference joining two of them with a
    dot, such as ``pv_south.pv_peak_power_in_watt``, is the only compound form the format has.

    The class exists mainly for what it forbids. Wildcards (``pv_*``, ``[*]``) and relative
    or absolute path syntax (``../pv``, ``/etc/x``) are deliberately not part of this format
    version: they are the vocabulary of a future preprocessor, and accepting them silently
    would make files written against that expectation resolve to something unintended.
    """

    #: The one identifier syntax of the format, used for component names, group names,
    #: variant and option names, fact names and port names alike.
    IDENTIFIER_PATTERN: ClassVar[Pattern[str]] = NameSyntax.IDENTIFIER_PATTERN

    #: Characters that only appear in wildcard or glob syntax. Their presence is always a
    #: rejection rather than a name failing the identifier pattern: the author meant a
    #: pattern, and patterns are not part of this format version.
    WILDCARD_CHARACTERS: ClassVar[str] = NameSyntax.WILDCARD_CHARACTERS

    #: Characters that only appear in filesystem paths. A reference addresses a component
    #: by name, never by location, so any of them means a path was written instead.
    PATH_CHARACTERS: ClassVar[str] = NameSyntax.PATH_CHARACTERS

    #: The separator between the two halves of a reference.
    REFERENCE_SEPARATOR: ClassVar[str] = "."

    @classmethod
    def check_identifier(cls, value: Any, location: str, role: str) -> str:
        """Returns ``value`` unchanged if it is a well-formed name, and raises otherwise.

        A name has to be a string matching the one identifier pattern of the format.
        Numbers, booleans and nested blocks reach this check whenever an author writes an
        unquoted YAML value the parser typed differently, so the type is verified here.

        Args:
            value: The raw YAML value that should be a name.
            location: Dotted key path of the value, used in the message.
            role: What the name stands for ("component", "group", "fact"), used in the
                message so the author knows which rule was applied.

        Returns:
            The validated name.

        Raises:
            EnergySystemFormatError: ``EF-08`` if the value is not a string or does not
                match the identifier pattern.
        """
        if not NameSyntax.is_identifier(value):
            raise EnergySystemFormatError(
                EnergySystemErrorId.INVALID_NAME,
                location,
                f"'{value}' is not a usable {role} name.",
                remedy=(
                    "A name starts with a letter or underscore and continues with "
                    "letters, digits or underscores."
                ),
            )
        return value

    @classmethod
    def split_reference(cls, value: Any, location: str, *, require_member: bool) -> Tuple[str, Optional[str]]:
        """Splits ``component`` or ``component.member`` into its two halves.

        This is the single place the reference grammar is enforced, for the ``from`` key of
        an input item as well as for every value of a ``sizing_sources`` block. Both use the
        same syntax and differ only in whether the second half is required: a sizing source
        always names a fact, while an input may name a component alone and let the target's
        declared defaults pick the ports.

        Args:
            value: The raw YAML value that should be a reference.
            location: Dotted key path of the value, used in the message.
            require_member: Whether the dotted second half must be present.

        Returns:
            A pair of the component name and either the member name or ``None``.

        Raises:
            EnergySystemFormatError: ``EF-06`` if the value is not a string, contains
                wildcard or path characters, has the wrong number of dotted parts, or
                has a part that is not a well-formed identifier.
        """
        if not isinstance(value, str) or not value:
            raise cls._reference_error(location, value, "a reference must be a non-empty string")
        if any(character in value for character in cls.WILDCARD_CHARACTERS):
            raise cls._reference_error(location, value, "wildcards are not part of this format version")
        if any(character in value for character in cls.PATH_CHARACTERS):
            raise cls._reference_error(location, value, "a reference names a component, never a path")
        parts = value.split(cls.REFERENCE_SEPARATOR)
        if len(parts) > 2 or (require_member and len(parts) != 2):
            expected = "'<component>.<fact>'" if require_member else "'<component>' or '<component>.<Output>'"
            raise cls._reference_error(location, value, f"a reference is written {expected}")
        for part in parts:
            if not NameSyntax.is_identifier(part):
                raise cls._reference_error(location, value, f"'{part}' is not a usable name")
        return parts[0], parts[1] if len(parts) == 2 else None

    @classmethod
    def _reference_error(cls, location: str, value: Any, problem: str) -> EnergySystemFormatError:
        """Builds the single rejection used for every malformed reference.

        All reference problems share one identifier because they share one cause: the author
        wrote something that is not a plain name or dotted name. Funnelling them through one
        builder keeps the wording identical whichever rule tripped, and keeps the reason
        visible in the message rather than only in the identifier.

        Args:
            location: Dotted key path of the value.
            value: The reference the author wrote.
            problem: The specific rule that was broken.

        Returns:
            The exception to raise; the caller raises it so the traceback starts there.
        """
        return EnergySystemFormatError(
            EnergySystemErrorId.WILDCARD_OR_RELATIVE_REFERENCE,
            location,
            f"'{value}' is not a valid reference: {problem}.",
        )
