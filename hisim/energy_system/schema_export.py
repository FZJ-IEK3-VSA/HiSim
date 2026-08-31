"""Exporting the energy-system format as a JSON Schema an editor and a validator can read.

An energy-system file is written by hand, and almost everything that can go wrong in one is
knowable before it is run: a preset name the class does not declare, a configuration key that is
not a field, a constructor argument spelled wrong, a sizing source naming a fact nothing reads.
The executor rejects all of that with a message naming the alternatives, but it does so after the
author has saved the file and started a run. A JSON Schema moves the same knowledge into the
editor, where a completion list and a red underline appear while the line is being typed — which
is why every mockup carries a ``# yaml-language-server: $schema=`` line pointing at the file this
module writes.

The schema is generated, never written by hand. Everything it knows comes from
:func:`hisim.config.introspection.describe_config`: the fields of each configuration class and
their annotations, the presets and named constructors it declares, which fields a law may size,
and which sizing facts the class reads. Generating it is what keeps the schema and the executor
from ever disagreeing — the two read the same declarations — and a test regenerates the committed
file and fails on any difference, so a conversion that adds a preset also updates the schema in
its own change.

Per-class knowledge is expressed with the conditional idiom: one ``allOf`` branch per component
class, each saying "if ``class`` is this one, then ``preset`` is one of these, ``constructor`` is
one of those with these parameters, ``config`` has these keys and nothing else, and
``sizing_sources`` names only facts this class reads". That is the shape editors understand, and
it is what makes an unknown preset or an unknown field a diagnostic on the offending line rather
than a complaint about the whole document.

One consequence of generating the class list from declarations is worth stating: a component
whose configuration class has neither a preset nor a named constructor cannot appear in the
schema at all, because there would be nothing to say about it. Such a class is not yet usable
from a file anyway — the class-bound validator rejects it for the same reason — so the schema's
component list is exactly the set of classes an energy-system file can name today, and it grows
as the conversion proceeds.
"""

# clean

from __future__ import annotations

import dataclasses
import json
import typing
from pathlib import Path
from typing import Any, Dict, Sequence

from hisim.config.introspection import ConfigDescription, describe_config
from hisim.config.presets import constructors_of
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.model import ComponentEntry, EnergySystemFile, Group, Variant, VariantOption
from hisim.energy_system.names import NameRules
from hisim.energy_system.schema_classes import ComponentClassScan, JsonTypes


class SchemaBuilder:
    """Assembles the whole JSON Schema of one schema version from a set of component classes.

    Built for one export and used once. The document it produces has two halves: a fixed part
    describing the format itself — the top level, the group and variant blocks, the three input
    shapes, the sizing-source references — which changes only when the format does, and a
    generated part describing what the classes of this repository allow, which changes with
    every conversion.

    The generated half is one conditional branch per component class. Branches are additive and
    independent: an entry naming a class matches exactly one of them, and an entry naming a class
    the schema does not know fails on the closed ``class`` list before any branch is consulted.
    """

    #: The JSON Schema dialect the document is written in.
    DIALECT: str = "https://json-schema.org/draft/2020-12/schema"

    #: Name of the committed schema file, which is what the mockups' editor line points at.
    FILENAME: str = "energy_system_v3.schema.json"

    #: Indentation of the written file, and a trailing newline, so that a diff of two exports
    #: reads line by line rather than as one changed line.
    INDENT: int = 2

    #: Pattern of a plain name — a component, a group, a fact or a port.
    NAME_PATTERN: str = NameRules.IDENTIFIER_PATTERN.pattern

    def __init__(self, classes: Sequence[type]) -> None:
        """Prepares a builder for one set of component classes.

        Args:
            classes: The component classes the schema will accept under ``class``, in the order
                they should appear in the completion list.
        """
        self.classes = tuple(classes)

    def build(self) -> Dict[str, Any]:
        """Builds the complete schema document.

        Returns:
            The schema as plain data, ready to be written as JSON.
        """
        return {
            "$schema": self.DIALECT,
            "$id": self.FILENAME,
            "title": f"HiSim energy system, schema version {EnergySystemFile.SUPPORTED_SCHEMA_VERSION}",
            "description": (
                "A declarative description of one simulated household: its components, how each "
                "is configured, where each takes its inputs from and, where that is ambiguous, "
                "where each takes its sizing facts from."
            ),
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "name"],
            "properties": self._top_level(),
            "$defs": self._definitions(),
        }

    def _top_level(self) -> Dict[str, Any]:
        """Builds the properties of the document's top level.

        Returns:
            One property per top-level key, in the canonical order the emitter writes them.
        """
        return {
            "schema_version": {
                "const": EnergySystemFile.SUPPORTED_SCHEMA_VERSION,
                "description": "The format version this file is written against.",
            },
            "name": {"type": "string", "description": "The name of the energy system."},
            "description": {"type": "string"},
            "components": {"$ref": "#/$defs/components"},
            "groups": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/name"},
                "additionalProperties": {"$ref": "#/$defs/group"},
            },
            "variants": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/name"},
                "additionalProperties": {"$ref": "#/$defs/variant"},
            },
            "metadata": {
                "type": "object",
                "description": (
                    "Written by a generated run record and never by hand; a file carrying it is "
                    "re-run explicitly rather than run as an authored energy system."
                ),
            },
        }

    def _definitions(self) -> Dict[str, Any]:
        """Builds the reusable definitions the top level refers to.

        Returns:
            The ``$defs`` block: names, references, the component map, the entry, the group,
            the variant with its options and the three input shapes.
        """
        return {
            "name": {"type": "string", "pattern": self.NAME_PATTERN},
            "reference": {"type": "string", "pattern": self._reference_pattern(dotted=True)},
            "source": {"type": "string", "pattern": self._reference_pattern(dotted=False)},
            "components": {
                "type": "object",
                "propertyNames": {"$ref": "#/$defs/name"},
                "additionalProperties": {"$ref": "#/$defs/entry"},
            },
            "group": {
                "type": "object",
                "additionalProperties": False,
                "required": list(Group.GROUP_KEYS),
                "properties": {
                    "enabled": {"type": "boolean"},
                    "components": {"$ref": "#/$defs/components"},
                },
            },
            "variant": {
                "type": "object",
                "additionalProperties": False,
                "required": list(Variant.VARIANT_KEYS),
                "properties": {
                    "selected": {"$ref": "#/$defs/name"},
                    "options": {
                        "type": "object",
                        "minProperties": 1,
                        "propertyNames": {"$ref": "#/$defs/name"},
                        "additionalProperties": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(VariantOption.OPTION_KEYS),
                            "properties": {"components": {"$ref": "#/$defs/components"}},
                        },
                    },
                },
            },
            "entry": self._entry(),
            "input_item": {
                "oneOf": [
                    {"$ref": "#/$defs/default_inputs"},
                    {"$ref": "#/$defs/explicit_wire"},
                    {"$ref": "#/$defs/aggregator_feed"},
                ]
            },
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

    def _entry(self) -> Dict[str, Any]:
        """Builds the schema of one component entry, class conditionals included.

        Returns:
            The entry schema: the keys every entry may carry, the closed list of classes, and
            one conditional branch per class.
        """
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [ComponentEntry.CLASS_KEY],
            "properties": {
                ComponentEntry.CLASS_KEY: {
                    "enum": [
                        path
                        for component in self.classes
                        for path in ComponentClassScan.paths_of(component)
                    ],
                    "description": "The component class, as a dotted path.",
                },
                "preset": {"type": "string"},
                "constructor": {"type": "object", "minProperties": 1, "maxProperties": 1},
                "config": {"type": "object"},
                "inputs": {"type": "array", "items": {"$ref": "#/$defs/input_item"}},
                "sizing_sources": {"$ref": "#/$defs/sizing_sources"},
            },
            "allOf": [self._class_branch(component) for component in self.classes],
        }

    def _class_branch(self, component_class: type) -> Dict[str, Any]:
        """Builds the conditional branch constraining every entry of one component class.

        Args:
            component_class: The class the branch is about.

        Returns:
            An ``if``/``then`` pair: matched on the ``class`` value, constraining the preset, the
            constructor, the configuration keys and the sizing sources.
        """
        config_class = ComponentClassScan.config_class_of(component_class)
        if config_class is None:
            raise ValueError(
                f"{ComponentClassScan.path_of(component_class)} declares no configuration class, "
                "so an energy-system file cannot configure it and the schema cannot describe it."
            )
        description = describe_config(config_class)
        return {
            "if": {
                "properties": {
                    ComponentEntry.CLASS_KEY: {
                        "enum": list(ComponentClassScan.paths_of(component_class))
                    }
                },
                "required": [ComponentEntry.CLASS_KEY],
            },
            "then": {
                "properties": {
                    "preset": self._presets(description),
                    "constructor": self._constructors(config_class, description),
                    "config": self._config(config_class, description),
                    "sizing_sources": self._sizing_sources(config_class),
                }
            },
        }

    @classmethod
    def _presets(cls, description: ConfigDescription) -> Any:
        """Builds the constraint on an entry's ``preset`` key.

        Args:
            description: The description of the entry's configuration class.

        Returns:
            The closed list of preset names, or ``False`` — the schema nothing satisfies — for a
            class that declares no preset at all, so that writing one is rejected in place.
        """
        if not description.presets:
            return False
        return {"enum": [preset.name for preset in description.presets]}

    @classmethod
    def _constructors(cls, config_class: type, description: ConfigDescription) -> Any:
        """Builds the constraint on an entry's ``constructor`` block.

        A constructor block is a one-entry mapping from the constructor's name to its arguments,
        so the name is constrained through ``propertyNames`` and each constructor's arguments
        through a property of the same name.

        Args:
            config_class: The configuration dataclass, for the resolved parameter annotations.
            description: The description of the entry's configuration class.

        Returns:
            The constructor schema, or ``False`` for a class that declares none.
        """
        if not description.constructors:
            return False
        annotations = cls._constructor_annotations(config_class)
        return {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 1,
            "propertyNames": {"enum": [entry.name for entry in description.constructors]},
            "properties": {
                entry.name: {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        parameter.name
                        for parameter in entry.parameters
                        if parameter.default is dataclasses.MISSING
                    ],
                    "properties": {
                        parameter.name: JsonTypes.of(
                            annotations.get(entry.name, {}).get(parameter.name, parameter.type_name),
                            sizable=False,
                        )
                        for parameter in entry.parameters
                    },
                }
                for entry in description.constructors
            },
        }

    @classmethod
    def _constructor_annotations(cls, config_class: type) -> Dict[str, Dict[str, Any]]:
        """Resolves the parameter annotations of every named constructor of one class.

        The description carries an annotation as the string it was written as, which is enough
        for a human reading a ``describe`` output but not enough to build a schema from: an
        enumeration only becomes a closed list of member names once the annotation is resolved to
        the class. So the builders are inspected once more here, and a constructor whose
        annotations do not resolve simply contributes nothing, leaving its parameters permissive.

        Args:
            config_class: The configuration dataclass.

        Returns:
            Constructor name to parameter name to resolved annotation.
        """
        resolved: Dict[str, Dict[str, Any]] = {}
        for name, builder in constructors_of(config_class).items():
            try:
                resolved[name] = dict(typing.get_type_hints(builder.function))
            except Exception:  # pylint: disable=broad-except  # an unresolvable annotation stays open
                resolved[name] = {}
        return resolved

    @classmethod
    def _config(cls, config_class: type, description: ConfigDescription) -> Dict[str, Any]:
        """Builds the constraint on an entry's ``config`` block: its own fields and nothing else.

        Field annotations are resolved against the defining module rather than read as the
        strings the description carries, because an annotation is only a type once it is
        resolved and the enumerations are what make a completion list worth having.

        Args:
            config_class: The configuration dataclass.
            description: Its description, for the set of sizable fields.

        Returns:
            The schema of the block.
        """
        try:
            hints = typing.get_type_hints(config_class)
        except Exception:  # pylint: disable=broad-except  # an unresolvable field stays permissive
            hints = {}
        sizable = {field.name for field in description.fields if field.sizable}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                field.name: JsonTypes.of(
                    hints.get(field.name, field.type_name), sizable=field.name in sizable
                )
                for field in description.fields
            },
        }

    @classmethod
    def _sizing_sources(cls, config_class: type) -> Dict[str, Any]:
        """Builds the constraint on an entry's ``sizing_sources`` keys.

        Args:
            config_class: The configuration dataclass.

        Returns:
            The sizing-source schema with its keys closed over the facts the class reads; a class
            that reads none accepts no key at all.
        """
        return {
            "$ref": "#/$defs/sizing_sources",
            "propertyNames": {"enum": list(facts_read_by(config_class))},
        }

    @classmethod
    def _reference_pattern(cls, *, dotted: bool) -> str:
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


def build_schema(classes: Sequence[type]) -> Dict[str, Any]:
    """Builds the JSON Schema of the energy-system format for one set of component classes.

    The generator behind the committed schema, exposed separately so that a test can build a
    schema over two classes and check the shape of what it produces without importing HiSim's
    whole component tree.

    Args:
        classes: The component classes the schema accepts under ``class``. Each one's
            configuration class is read for its presets, constructors, fields and facts.

    Returns:
        The schema as plain data.
    """
    return SchemaBuilder(classes).build()


def default_schema_path() -> Path:
    """Returns the path of the committed schema file inside this repository.

    Returns:
        ``hisim/energy_system_v3.schema.json``, which is what the mockups' editor line names.
    """
    return Path(__file__).resolve().parents[1] / SchemaBuilder.FILENAME


def export_schema(path: Any = None) -> Path:
    """Writes the schema of every component class this repository can configure from a file.

    Args:
        path: Where to write it; the committed location when omitted.

    Returns:
        The path written to.
    """
    target = Path(path) if path is not None else default_schema_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_schema(build_schema(ComponentClassScan.collect())), encoding="utf-8")
    return target


def render_schema(schema: Dict[str, Any]) -> str:
    """Renders a schema as the exact text the committed file holds.

    One rendering used by both the writer and the freshness test, so that a comparison can never
    fail on formatting alone.

    Args:
        schema: The schema as plain data.

    Returns:
        The JSON text, indented and newline-terminated.
    """
    return json.dumps(schema, indent=SchemaBuilder.INDENT, sort_keys=False) + "\n"


def schema_is_current() -> bool:
    """Reports whether the committed schema still matches what an export would write.

    The committed file is generated, so it can silently fall behind the declarations it was
    generated from — a conversion adds a preset and the schema keeps offering the old list. This
    answers that in one call, for the freshness test and for a reviewer at a shell alike.

    Returns:
        ``True`` when the committed file exists and is byte-identical to a fresh export.
    """
    path = default_schema_path()
    if not path.exists():
        return False
    return path.read_text(encoding="utf-8") == render_schema(build_schema(ComponentClassScan.collect()))
