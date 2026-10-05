"""The JSON Schema of the assembly file, and the definitions schema version 4 adds to an energy system.

``hisim energy-system schema`` writes two files side by side: the energy-system schema
(``energy_system_v3.schema.json``, which reads versions 3 and 4) and, next to it, the assembly
schema (:attr:`AssemblySchemaBuilder.FILENAME`). The assembly schema is format-only — an assembly
names its members' classes as plain dotted strings, and whether they exist is the library check's
question — and it states the library contract of D24 as schema rules: every parameter carries a
description, every numeric parameter a ``range``, and the file a ``tests`` block with at least one
``monotone`` entry. Validating a draft against it therefore lists exactly what the draft still owes.

The blocks both files share — an import, its instances and verbs, a port, the ``{$port: …}``
placeholder — are defined once here, in :class:`AssemblyFormatDefinitions`, and spliced into both;
the names, references and input shapes both files share with a version-3 file are
:class:`~hisim.energy_system.schema_definitions.SharedSchemaDefinitions`, which both builders import,
so neither builder imports the other.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from hisim import loadtypes as lt
from hisim.energy_system.assemblies.model import AssemblyFile, ConstraintKind, ParameterType, MonotoneDirection
from hisim.energy_system.imports_model import BindingVerbs, InstanceEntry
from hisim.energy_system.imports_reader import ImportsReader
from hisim.energy_system.model import ComponentEntry
from hisim.energy_system.schema_definitions import SharedSchemaDefinitions


class AssemblyFormatDefinitions:
    """The ``$defs`` of the blocks schema version 4 adds, shared by both schemas."""

    #: Pattern of a dotted class path.
    CLASS_PATTERN: ClassVar[str] = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$"

    #: Pattern of an assembly's library path.
    ASSEMBLY_PATTERN: ClassVar[str] = AssemblyFile.LIBRARY_PATH_PATTERN.pattern

    #: Pattern of a verb's partner reference: a name, optionally followed by an instance and a port.
    PARTNER_PATTERN: ClassVar[str] = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$"

    @classmethod
    def names(cls) -> Dict[str, Any]:
        """One name or a non-empty list of names."""
        return {
            "oneOf": [
                {"$ref": "#/$defs/name"},
                {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/name"}},
            ]
        }

    @classmethod
    def conditions(cls) -> Dict[str, Any]:
        """``required_when``/``active_when``: parameter to a non-empty list of values."""
        return {
            "type": "object",
            "propertyNames": {"$ref": "#/$defs/name"},
            "additionalProperties": {"type": "array", "minItems": 1},
        }

    @classmethod
    def entry_extensions(cls) -> Dict[str, Any]:
        """The keys schema version 4 adds to a top-level component entry."""
        return {
            "order": {"$ref": "#/$defs/order"},
            "ports": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/name"},
                "additionalProperties": {
                    "$ref": "#/$defs/port",
                    "propertyNames": {"not": {"enum": list(ImportsReader.CONDITION_KEYS)}},
                    "description": "A site entry has no parameters, so its ports carry no required_when/active_when.",
                },
            },
            **cls.verb_properties(),
        }

    @classmethod
    def verb_properties(cls) -> Dict[str, Any]:
        """The three binding verbs (§3.1)."""
        partner_map = {
            "type": "object",
            "propertyNames": {"$ref": "#/$defs/name"},
            "additionalProperties": {"type": "string", "pattern": cls.PARTNER_PATTERN},
        }
        return {
            BindingVerbs.KEYS[0]: partner_map,
            BindingVerbs.KEYS[1]: partner_map,
            BindingVerbs.KEYS[2]: {"type": "array", "items": {"$ref": "#/$defs/name"}},
        }

    @classmethod
    def definitions(cls) -> Dict[str, Any]:
        """The shared ``$defs``."""
        parameter_values = {"type": "object", "propertyNames": {"$ref": "#/$defs/name"}}
        return {
            "order": {"type": "integer", "minimum": 0, "description": "Evaluation order (assemblies_spec.md §2.3)."},
            "port_placeholder": {
                "type": "object",
                "additionalProperties": False,
                "required": ["$port"],
                "properties": {
                    "$port": {"$ref": "#/$defs/name"},
                    "wires": {"type": "object", "additionalProperties": {"$ref": "#/$defs/name"}},
                },
            },
            "observes_placeholder": {
                "type": "object",
                "additionalProperties": False,
                "required": ["$observes"],
                "properties": {"$observes": {"$ref": "#/$defs/name"}},
            },
            "port": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "into": cls.names(),
                    "partner": cls.names(),
                    "wires": {"type": "object", "additionalProperties": {"$ref": "#/$defs/name"}},
                    "output": {"$ref": "#/$defs/reference"},
                    "from": {"$ref": "#/$defs/reference"},
                    "circuit": {"$ref": "#/$defs/name"},
                    "member": cls.names(),
                    "carrier": {"type": ["string", "object"]},
                    "outputs": {"type": "array", "items": {"$ref": "#/$defs/name"}},
                    "fact": {"type": ["string", "object"]},
                    "many": {"type": "boolean"},
                    "export": {"type": "boolean"},
                    "controllable": {"type": "object"},
                    "optional": {"type": "boolean"},
                    "required_when": cls.conditions(),
                    "active_when": cls.conditions(),
                },
            },
            "internal_port": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bind"],
                "properties": {"bind": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}}},
            },
            "observer_port": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"into": cls.names(), "default": {"type": "string"}},
            },
            "instance": {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["parameters"],
                        "additionalProperties": False,
                        "properties": {
                            "preset": {"type": ["string", "object"]},
                            "parameters": parameter_values,
                            "installation_year": {"type": "integer"},
                            "quote": {"type": "object"},
                        },
                    },
                    {
                        "type": "object",
                        "propertyNames": {"not": {"enum": list(InstanceEntry.LONG_FORM_KEYS)}},
                    },
                    {
                        "type": "object",
                        "minProperties": 1,
                        "additionalProperties": False,
                        "properties": {
                            "installation_year": {"type": "integer"},
                            "quote": {"type": "object"},
                        },
                    },
                ]
            },
            "import": {
                "type": "object",
                "additionalProperties": False,
                "required": ["assembly"],
                "properties": {
                    "order": {"$ref": "#/$defs/order"},
                    "assembly": {"type": "string", "pattern": cls.ASSEMBLY_PATTERN},
                    "preset": {"type": ["string", "object"]},
                    "parameters": parameter_values,
                    "instances": {
                        "type": "object",
                        "minProperties": 1,
                        "propertyNames": {"$ref": "#/$defs/name"},
                        "additionalProperties": {"$ref": "#/$defs/instance"},
                    },
                    **cls.verb_properties(),
                    "observes": {"type": "array"},
                    "actuates": {},
                    "installation_year": {"type": "integer"},
                    "quote": {"type": "object"},
                },
                "not": {"required": ["instances", "parameters"]},
            },
        }


class AssemblySchemaBuilder:
    """Assembles the JSON Schema of the assembly file (schema version 4)."""

    #: The committed file, written next to the energy-system schema.
    FILENAME: ClassVar[str] = "assembly_v4.schema.json"

    #: The dialect, as the energy-system schema's.
    DIALECT: ClassVar[str] = "https://json-schema.org/draft/2020-12/schema"

    def build(self) -> Dict[str, Any]:
        """Builds the schema."""
        definitions = {**SharedSchemaDefinitions.names(), **SharedSchemaDefinitions.items()}
        definitions.update(AssemblyFormatDefinitions.definitions())
        definitions.update(self._own_definitions())
        return {
            "$schema": self.DIALECT,
            "$id": self.FILENAME,
            "title": f"HiSim assembly, schema version {AssemblyFile.SCHEMA_VERSION}",
            "description": (
                "A fragment of an energy system in a file of its own (assemblies_spec.md): members, inner "
                "imports, parameters, presets, internal variants, an interface of ports and its test contract."
            ),
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "kind", "name", "tests"],
            "properties": {
                "schema_version": {"const": AssemblyFile.SCHEMA_VERSION},
                "kind": {"const": AssemblyFile.KIND},
                "name": {"type": "string", "pattern": AssemblyFormatDefinitions.ASSEMBLY_PATTERN},
                "description": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "propertyNames": {
                        "allOf": [
                            {"$ref": "#/$defs/name"},
                            {"not": {"enum": list(AssemblyFile.RESERVED_PARAMETER_NAMES)}},
                        ]
                    },
                    "additionalProperties": {"$ref": "#/$defs/parameter"},
                },
                "constraints": {"type": "array", "items": {"$ref": "#/$defs/constraint"}},
                "presets": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/name"},
                    "additionalProperties": {"type": "object"},
                },
                "imports": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/name"},
                    "additionalProperties": {"$ref": "#/$defs/import"},
                },
                "components": {"$ref": "#/$defs/members"},
                "variants": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/name"},
                    "additionalProperties": {"$ref": "#/$defs/variant"},
                },
                "interface": {"$ref": "#/$defs/interface"},
                "tests": {"$ref": "#/$defs/tests"},
            },
            "$defs": definitions,
        }

    @classmethod
    def _own_definitions(cls) -> Dict[str, Any]:
        """The definitions only an assembly file has."""
        numeric = [kind.value for kind in ParameterType if kind.is_numeric]
        member_input: Dict[str, Any] = {
            "oneOf": [
                {"$ref": "#/$defs/default_inputs"},
                {"$ref": "#/$defs/explicit_wire"},
                {"$ref": "#/$defs/aggregator_feed"},
                {"$ref": "#/$defs/port_placeholder"},
                {"$ref": "#/$defs/observes_placeholder"},
            ]
        }
        member_keys: List[str] = list(ComponentEntry.ENTRY_KEYS) + ["order", "display"]
        sections = {
            "needs": {"$ref": "#/$defs/port"},
            "provides": {"$ref": "#/$defs/port"},
            "internal": {"$ref": "#/$defs/internal_port"},
            "observes": {"$ref": "#/$defs/observer_port"},
            "actuates": {},
        }
        return {
            "parameter": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "description"],
                "properties": {
                    "type": {"enum": [kind.value for kind in ParameterType]},
                    "unit": {"enum": list(lt.Units.__members__)},
                    "default": {},
                    "values": {"type": "array", "minItems": 1},
                    "range": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["min", "max"],
                        "properties": {"min": {"type": "number"}, "max": {"type": "number"}},
                    },
                    "description": {"type": "string", "minLength": 1},
                },
                "if": {"properties": {"type": {"enum": numeric}}},
                "then": {"required": ["range"]},
            },
            "constraint": {
                "type": "object",
                "minProperties": 1,
                "maxProperties": 1,
                "properties": {
                    ConstraintKind.EXACTLY_ONE_OF.value: {"type": "array", "items": {"$ref": "#/$defs/name"}},
                    ConstraintKind.AT_MOST_ONE_OF.value: {"type": "array", "items": {"$ref": "#/$defs/name"}},
                    ConstraintKind.REQUIRES.value: {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"$ref": "#/$defs/name"}},
                    },
                },
                "additionalProperties": False,
            },
            "member": {
                "type": "object",
                "additionalProperties": False,
                "required": [ComponentEntry.CLASS_KEY],
                "propertyNames": {"enum": member_keys},
                "properties": {
                    ComponentEntry.CLASS_KEY: {"type": "string", "pattern": AssemblyFormatDefinitions.CLASS_PATTERN},
                    "preset": {"type": ["string", "object"]},
                    "constructor": {"type": "object", "minProperties": 1, "maxProperties": 1},
                    "config": {"type": "object"},
                    "inputs": {"type": "array", "items": member_input},
                    "sizing_sources": {"$ref": "#/$defs/sizing_sources"},
                    "order": {"$ref": "#/$defs/order"},
                    "display": {"type": "string"},
                },
            },
            "members": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/name"},
                "additionalProperties": {"$ref": "#/$defs/member"},
            },
            "variant": {
                "type": "object",
                "additionalProperties": False,
                "required": ["selected_by", "options"],
                "properties": {
                    "selected_by": {"$ref": "#/$defs/name"},
                    "options": {
                        "type": "object",
                        "minProperties": 1,
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["when"],
                            "properties": {
                                "when": {"type": "array", "minItems": 1},
                                "components": {"$ref": "#/$defs/members"},
                            },
                        },
                    },
                },
            },
            "interface": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    section: {"type": "object", "additionalProperties": schema} for section, schema in sections.items()
                },
            },
            "tests": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bounds", "monotone"],
                "properties": {
                    "bounds": {"type": "array", "items": {"$ref": "#/$defs/bounds"}},
                    "monotone": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/monotone"}},
                    "expect": {"type": "array", "items": {"$ref": "#/$defs/expect"}},
                },
            },
            "bounds": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "output": {"$ref": "#/$defs/reference"},
                    "kpi": {"type": "string"},
                    "member": {"$ref": "#/$defs/name"},
                    "unit": {"enum": list(lt.Units.__members__)},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                },
                "oneOf": [{"required": ["output", "unit"]}, {"required": ["kpi", "member"]}],
                "anyOf": [{"required": ["min"]}, {"required": ["max"]}],
            },
            "monotone": {
                "type": "object",
                "additionalProperties": False,
                "required": ["parameter", "kpi", "member", "direction"],
                "properties": {
                    "parameter": {"$ref": "#/$defs/name"},
                    "kpi": {"type": "string"},
                    "member": {"$ref": "#/$defs/name"},
                    "direction": {"enum": [direction.value for direction in MonotoneDirection]},
                },
            },
            "expect": {
                "type": "object",
                "additionalProperties": False,
                "required": ["preset", "kpi", "member"],
                "properties": {
                    "preset": {"$ref": "#/$defs/name"},
                    "kpi": {"type": "string"},
                    "member": {"$ref": "#/$defs/name"},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                },
                "anyOf": [{"required": ["min"]}, {"required": ["max"]}],
            },
        }
