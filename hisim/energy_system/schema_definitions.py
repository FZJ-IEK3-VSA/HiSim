"""The JSON Schema definitions the energy-system schema and the assembly schema share.

Both committed schemas (:mod:`hisim.energy_system.schema_export` and
:mod:`hisim.energy_system.assemblies.schema`) describe names, references and the three input shapes a
component entry and an assembly member write alike. They are defined once here, in a module that
imports neither builder, so each builder imports this one and neither imports the other.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict

from hisim.energy_system.names import NameRules


class SharedSchemaDefinitions:
    """The ``$defs`` both schemas carry: names and references, and the input items and sizing sources."""

    #: Pattern of a plain name — a component, a group, a fact or a port.
    NAME_PATTERN: ClassVar[str] = NameRules.IDENTIFIER_PATTERN.pattern

    @classmethod
    def reference_pattern(cls, *, dotted: bool) -> str:
        """Builds the regular expression of a reference to a component or to one of its members.

        Args:
            dotted: Whether the member half is required, as it is for a sizing source and for an
                explicit wire, or optional, as it is for an aggregator feed.

        Returns:
            The anchored pattern.
        """
        name = cls.NAME_PATTERN.strip("^$")
        member = f"\\.{name}" if dotted else f"(\\.{name})?"
        return f"^{name}{member}$"

    @classmethod
    def names(cls) -> Dict[str, Any]:
        """``name``, ``reference`` (``Component.member``) and ``source`` (the member optional)."""
        return {
            "name": {"type": "string", "pattern": cls.NAME_PATTERN},
            "reference": {"type": "string", "pattern": cls.reference_pattern(dotted=True)},
            "source": {"type": "string", "pattern": cls.reference_pattern(dotted=False)},
        }

    @classmethod
    def items(cls) -> Dict[str, Any]:
        """The three input shapes — a bare name, an explicit wire, an aggregator feed — and ``sizing_sources``."""
        return {
            "default_inputs": {"$ref": "#/$defs/name"},
            "explicit_wire": {
                "type": "object",
                "additionalProperties": False,
                "required": ["input", "from"],
                "properties": {
                    "input": {"$ref": "#/$defs/name"},
                    "from": {"$ref": "#/$defs/reference"},
                },
            },
            "aggregator_feed": {
                "type": "object",
                "additionalProperties": False,
                "required": ["from", "tags"],
                "properties": {
                    "from": {"$ref": "#/$defs/source"},
                    "component_type": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "weight": {"type": "integer"},
                    "dispatch": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "target_input": {"$ref": "#/$defs/name"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
            "sizing_sources": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/name"},
                "additionalProperties": {
                    "oneOf": [
                        {"$ref": "#/$defs/reference"},
                        {"type": "array", "items": {"$ref": "#/$defs/reference"}},
                    ]
                },
            },
        }
