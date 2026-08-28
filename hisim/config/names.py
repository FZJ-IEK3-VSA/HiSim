"""The one identifier rule every name in HiSim obeys, defined where both users can reach it.

A component's runtime name is not decoration. It is the prefix of every output name and
therefore of every result column, it is the key a declarative energy-system file addresses
that component by, and it is the string a reference in such a file resolves against. Those
three roles only stay the same string if the name is a plain identifier, so exactly one
grammar governs all of them: a letter or underscore followed by letters, digits or
underscores.

The rule lives in :mod:`hisim.config` rather than next to either of its users because it has
two of them on opposite sides of the dependency direction. ``hisim/energy_system/model.py``
enforces it on what an author writes in a file, ``hisim/component.py`` enforces it on what a
component is constructed with, and the energy-system package already imports the component
runtime, so the component runtime cannot import back. The configuration package is the
bottom layer both of them import, which makes it the only place a single definition can sit.
"""

# clean

from __future__ import annotations

import re
from typing import Any, ClassVar, Optional, Pattern, TypeGuard


class NameSyntax:
    """The identifier grammar shared by component names, file keys and references.

    The class carries the one pattern a name has to match plus the two character sets whose
    presence means the author wrote something other than a name at all — a glob pattern or a
    filesystem path. Both are called out separately when a name is rejected, because "this is
    not an identifier" is unhelpful feedback for someone who deliberately typed ``pv_*`` or
    ``../pv`` and needs to be told that patterns and paths are not part of this vocabulary.

    Everything here is a class-level constant or a classmethod; the class is never
    instantiated and holds no state. It exists to give the rule one name and one home, so
    that a change to the grammar is a change in a single place rather than in every module
    that happens to validate a name.
    """

    #: The one identifier syntax of HiSim names: a letter or underscore, then letters,
    #: digits or underscores. Used for component names, group names, fact names and port
    #: names alike, so that a reader never has to remember which allows what.
    IDENTIFIER_PATTERN: ClassVar[Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    #: Characters that only appear in wildcard or glob syntax. Their presence is always
    #: reported as its own problem rather than as a name failing the pattern: the author
    #: meant a pattern, and patterns are not part of this vocabulary.
    WILDCARD_CHARACTERS: ClassVar[str] = "*?[]"

    #: Characters that only appear in filesystem paths. A name identifies a component, never
    #: a location, so any of them means a path was written where a name belongs.
    PATH_CHARACTERS: ClassVar[str] = "/\\"

    @classmethod
    def is_identifier(cls, value: Any) -> TypeGuard[str]:
        """Reports whether ``value`` is a string that satisfies the identifier grammar.

        This is the plain predicate form of the rule, for callers that want to branch on a
        name rather than reject it — a schema exporter listing which of a file's keys are
        addressable, for instance. It is deliberately total: any object may be passed, and
        anything that is not a matching string simply answers ``False``. The return is a type
        guard, so a caller that has checked a raw YAML value may go on treating it as a
        ``str`` without a second ``isinstance`` for the type checker's benefit.

        Args:
            value: The candidate name. Values of any type are accepted, because names arrive
                from YAML documents where an unquoted token may have been typed as a number
                or a boolean before it ever reaches this check.

        Returns:
            ``True`` if ``value`` is a ``str`` matching :attr:`IDENTIFIER_PATTERN`.
        """
        return isinstance(value, str) and cls.IDENTIFIER_PATTERN.match(value) is not None

    @classmethod
    def explain_violation(cls, value: Any) -> Optional[str]:
        """Names the specific rule ``value`` breaks, or ``None`` if it breaks none.

        The wording is shared by every caller so that a component rejected at construction
        time and a file key rejected at load time tell the author the same thing about the
        same string. The wildcard and path cases are separated from the general case because
        they are the two mistakes with a distinct intent behind them, and saying "wildcards
        are not part of this format version" is far more actionable than repeating the
        character set an identifier may use.

        Args:
            value: The candidate name, of any type.

        Returns:
            A short phrase completing "``'x'`` is not a usable name: …", or ``None`` when the
            value is a well-formed identifier.
        """
        if not isinstance(value, str):
            return f"a name must be a string, not {type(value).__name__}"
        if not value:
            return "a name must not be empty"
        if any(character in value for character in cls.WILDCARD_CHARACTERS):
            return "wildcards are not part of this vocabulary"
        if any(character in value for character in cls.PATH_CHARACTERS):
            return "a name identifies a component, never a path"
        if cls.IDENTIFIER_PATTERN.match(value) is None:
            return "a name starts with a letter or underscore and continues with letters, digits or underscores"
        return None

    @classmethod
    def require_identifier(cls, value: Any, role: str) -> None:
        """Raises unless ``value`` is a well-formed name for the given role.

        This is the enforcing form used at the two points where a name becomes real: when a
        component is constructed with its runtime name, and when a declarative file's key is
        read. Rejecting there rather than downstream means an unusable name surfaces at the
        moment it is introduced, with the offending string in the message, instead of as a
        malformed result column or an unresolvable reference much later.

        Args:
            value: The candidate name, of any type.
            role: What the name stands for — ``"component"``, ``"group"``, ``"fact"`` — so the
                message says which of HiSim's names was rejected.

        Raises:
            ValueError: If ``value`` is not a string matching :attr:`IDENTIFIER_PATTERN`. The
                message names the offending string, the role and the rule it broke.
        """
        problem = cls.explain_violation(value)
        if problem is not None:
            raise ValueError(f"'{value}' is not a usable {role} name: {problem}.")
