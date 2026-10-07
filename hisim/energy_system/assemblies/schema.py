"""The JSON Schema of the assembly file (lean v1), committed beside the energy-system schema.

The schema matches the v1 reader (:mod:`.reader`) exactly: every key it reads and nothing a cut
construct of §13.1 would add — no ``imports``, ``presets``, ``from:``, ``internal:``, ``order:``,
``$switch``, ``at_most_one_of``/``requires``, ``export``, ``priorities`` or ``actuates``. It also
states the library contract of D24 as schema rules: every parameter carries a description and a
default, every numeric one a unit and a ``range``, and the file a ``tests`` block, with at least one
``monotone`` entry when it has a numeric parameter. The blocks it shares with the energy-system file —
names, input items, sizing sources, a port and the placeholders — are taken from that file's schema
builder (:class:`~hisim.energy_system.schema_export.SchemaBuilder`), so the two never drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict

from hisim import loadtypes as lt
from hisim.energy_system.assemblies.model import AssemblyFile, MonotoneDirection, ParameterType
from hisim.energy_system.model import ComponentEntry
from hisim.energy_system.schema_export import SchemaBuilder, default_schema_path, render_schema


class AssemblySchemaBuilder:
    """Assembles the JSON Schema of the assembly file."""

    #: The committed file, written next to the energy-system schema.
    FILENAME: ClassVar[str] = "assembly_v4.schema.json"

    #: The definitions taken from the energy-system schema.
    SHARED: ClassVar[tuple] = (
        "name",
        "reference",
        "source",
        "default_inputs",
        "explicit_wire",
        "aggregator_feed",
        "input_item",
        "string_or_param",
        "sizing_sources",
        "port",
        "port_placeholder",
        "observes_placeholder",
    )

    #: The parameter types that are numbers, which carry a unit and a range.
    NUMERIC: ClassVar[list] = [kind.value for kind in ParameterType if kind.is_numeric]

    def build(self) -> Dict[str, Any]:
        """Builds the schema."""
        shared = SchemaBuilder(()).build()["$defs"]
        definitions = {key: shared[key] for key in self.SHARED}
        definitions.update(self._own_definitions())
        return {
            "$schema": SchemaBuilder.DIALECT,
            "$id": self.FILENAME,
            "title": f"HiSim assembly, schema version {AssemblyFile.SCHEMA_VERSION} (lean v1)",
            "description": (
                "A fragment of an energy system in a file of its own (assemblies_spec.md §13.1): members, "
                "parameters, exactly_one_of constraints, internal variants, an interface of ports and its "
                "test contract."
            ),
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "kind", "name", "tests"],
            "properties": {
                "schema_version": {"const": AssemblyFile.SCHEMA_VERSION},
                "kind": {"const": AssemblyFile.KIND},
                "name": {"type": "string", "pattern": AssemblyFile.LIBRARY_PATH_PATTERN.pattern},
                "description": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "propertyNames": {"allOf": [{"$ref": "#/$defs/name"}, {"not": {"enum": ["priorities"]}}]},
                    "additionalProperties": {"$ref": "#/$defs/parameter"},
                },
                "constraints": {"type": "array", "items": {"$ref": "#/$defs/constraint"}},
                "components": {"$ref": "#/$defs/members"},
                "variants": {
                    "type": "object",
                    "propertyNames": {"$ref": "#/$defs/name"},
                    "additionalProperties": {"$ref": "#/$defs/variant"},
                },
                "interface": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        section: {"type": "object", "additionalProperties": {"$ref": "#/$defs/port"}}
                        for section in AssemblyFile.INTERFACE_SECTIONS
                    },
                },
                "tests": {"$ref": "#/$defs/tests"},
            },
            "if": {
                "required": ["parameters"],
                "properties": {
                    "parameters": {
                        "not": {"additionalProperties": {"not": {"properties": {"type": {"enum": self.NUMERIC}}}}}
                    }
                },
            },
            "then": {"properties": {"tests": {"properties": {"monotone": {"minItems": 1}}}}},
            "$defs": definitions,
        }

    @classmethod
    def _own_definitions(cls) -> Dict[str, Any]:
        """The definitions only an assembly file has."""
        band = {"min": {"type": "number"}, "max": {"type": "number"}}
        one_end = {"anyOf": [{"required": ["min"]}, {"required": ["max"]}]}
        return {
            "parameter": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "default", "description"],
                "properties": {
                    "type": {"enum": [kind.value for kind in ParameterType]},
                    "unit": {"enum": list(lt.Units.__members__)},
                    "default": {},
                    "values": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "range": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["min", "max"],
                        "properties": band,
                    },
                    "description": {"type": "string", "minLength": 1},
                },
                "allOf": [
                    {"if": {"properties": {"type": {"enum": cls.NUMERIC}}}, "then": {"required": ["unit", "range"]}},
                    {"if": {"properties": {"type": {"const": "enum"}}}, "then": {"required": ["values"]}},
                ],
            },
            "constraint": {
                "type": "object",
                "additionalProperties": False,
                "required": ["exactly_one_of"],
                "properties": {"exactly_one_of": {"type": "array", "minItems": 2, "items": {"$ref": "#/$defs/name"}}},
            },
            "member": {
                "type": "object",
                "additionalProperties": False,
                "required": [ComponentEntry.CLASS_KEY],
                "properties": {
                    ComponentEntry.CLASS_KEY: {"type": "string"},
                    "preset": {"$ref": "#/$defs/string_or_param"},
                    "constructor": {"type": "object", "minProperties": 1, "maxProperties": 1},
                    "config": {"type": "object"},
                    "inputs": {"type": "array", "items": {"$ref": "#/$defs/input_item"}},
                    "sizing_sources": {"$ref": "#/$defs/sizing_sources"},
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
            "tests": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "bounds": {"type": "array", "items": {"$ref": "#/$defs/bounds"}},
                    "monotone": {"type": "array", "items": {"$ref": "#/$defs/monotone"}},
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
                    **band,
                },
                "oneOf": [{"required": ["output", "unit"], "not": {"required": ["member"]}}, {"required": ["kpi"]}],
                **one_end,
            },
            "monotone": {
                "type": "object",
                "additionalProperties": False,
                "required": ["parameter", "kpi", "direction"],
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
                "required": ["kpi"],
                "properties": {"kpi": {"type": "string"}, "member": {"$ref": "#/$defs/name"}, **band},
                **one_end,
            },
        }


def assembly_schema_path() -> Path:
    """The committed assembly schema, beside the energy-system schema."""
    return default_schema_path().parent / AssemblySchemaBuilder.FILENAME


def export_assembly_schema(directory: Path) -> Path:
    """Writes the assembly schema into a directory and returns its path."""
    target = directory / AssemblySchemaBuilder.FILENAME
    target.write_text(render_schema(AssemblySchemaBuilder().build()), encoding="utf-8")
    return target


def assembly_schema_is_current() -> bool:
    """Whether the committed assembly schema is byte-identical to a fresh export."""
    path = assembly_schema_path()
    return path.exists() and path.read_text(encoding="utf-8") == render_schema(AssemblySchemaBuilder().build())
