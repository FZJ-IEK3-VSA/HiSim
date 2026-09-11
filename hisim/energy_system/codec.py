"""Decoding the plain values of a file into the typed values a configuration field holds.

A ``config`` block in an energy-system file is written in YAML, so everything in it arrives as
a string, a number, a boolean, a list or a mapping. A configuration field, by contrast, holds
an enum member, a float, a nested dataclass or the ``AUTO`` sentinel that says "a law fills
this in". This module is the one place that crosses between the two, so that a value written
in a file means the same thing no matter which stage reads it and no matter which class it
lands on.

The rule that matters most is the enum one. A field typed by an enum must end up holding the
*member*, never the string that spells it: HiSim's configuration enums derive from ``str``, so
a forgotten decode compares equal in tests and quietly fails an ``is`` comparison in the one
component that uses identity — the failure mode this module exists to make impossible. The
second rule is ``AUTO``: the bare word re-opens a field that a preset pinned, which is how an
author asks for a value to be sized rather than fixed, and it is legal only on a field that
has a law to close it again.

The same decoding serves the arguments of a named constructor, which a file writes as a
mapping under ``constructor:`` and which reach a Python classmethod expecting the same enum
members and nested objects a field holds. :meth:`ConfigValueCodec.decode_argument` is that one
implementation, and the two paths that go through it — the *sparse override*, which writes a
value onto one field of an already-built configuration, and the constructor argument — cannot
come to mean different things by the same written word. A *complete* ``config`` block is the
third path and goes elsewhere: the configuration class deserializes it whole through its own
``from_dict``, because only the class knows how to rebuild every nested object at once, and
this module's part in that is :meth:`ConfigValueCodec.to_deserializer_payload`, which only
rewrites the enum spellings the two formats disagree on.

What a written value may be decoded into at all is not stated here either: it is
:func:`hisim.config.presets.is_decodable_annotation`, in the bottom layer, which the
``@constructor`` decorator consults to refuse an undecodable parameter at import time and which
this module consults before decoding, so that the gate and the decoder cannot disagree. An
annotation the predicate refuses is passed through untouched on the field path, leaving the
last word to the configuration class itself; on the constructor path the decoded value is
additionally checked against the shape its parameter asks for, because a constructor argument
has no class behind it to catch what the codec let through.

Everything the codec cannot decode is a hard error naming the entry, the field, the value and
the type expected — never a silent pass-through of a wrong type, because a wrong number in a
configuration surfaces as a wrong simulation result rather than as a crash.
"""

# clean

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from types import NoneType
from typing import Any, Dict, Mapping, Optional, Tuple, Type

from hisim.config.presets import is_decodable_annotation
from hisim.config.sizing import AUTO, SizedFieldMetadata, _AutoSize
from hisim.energy_system.errors import EnergySystemBindingError, EnergySystemErrorId


class ConfigValueCodec:
    """Decodes the values of one configuration class from their wire form.

    Built for a single configuration class, whose field types and per-field decoders it
    resolves once, and then asked to decode individual values. Reusing one codec across the
    fields of one entry is what keeps the annotation resolution — which is the expensive part
    — out of the per-value path.

    The codec decides nothing about *which* fields may be written; that is the class-bound
    validator's job and it has already run by the time a codec is built. What arrives here is
    a key known to be a field, and the only question left is whether the value fits it.
    """

    def __init__(self, config_class: type) -> None:
        """Resolves the field types and per-field decoders of one configuration class.

        Args:
            config_class: The configuration dataclass whose values are to be decoded.
        """
        self.config_class = config_class
        self.fields: Dict[str, dataclasses.Field] = {
            field.name: field for field in dataclasses.fields(config_class)
        }
        try:
            self.hints: Mapping[str, Any] = typing.get_type_hints(config_class)
        except Exception:  # pylint: disable=broad-except
            # An annotation that cannot be resolved costs the type-directed decoding of that
            # class, not the load: the per-field decoders and the AUTO rule still apply, and
            # a value the codec cannot type-check is passed on for the class itself to reject.
            self.hints = {}

    def decode(self, field_name: str, value: Any, location: str, name: str) -> Any:
        """Decodes one written value into the form its configuration field holds.

        Args:
            field_name: The field being overridden; it is known to exist.
            value: The value as the YAML document carried it.
            location: The dotted key path of the value, for the message.
            name: The component's name, for the message.

        Returns:
            The decoded value, ready to be written onto the configuration.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when the value does not fit the field, naming
                the field, the value and the type expected, and listing the enum's members
                where the field is enum-typed.
        """
        if isinstance(value, str) and value == _AutoSize.WIRE_SPELLING:
            return AUTO
        decoder = self._declared_decoder(field_name)
        if decoder is not None:
            try:
                return decoder(value)
            except EnergySystemBindingError:
                raise
            except Exception as error:  # pylint: disable=broad-except
                raise self._decoder_failure(field_name, value, location, name, error) from error
        return self.decode_argument(self.hints.get(field_name), value, location, name, field_name)

    def to_deserializer_payload(
        self, block: Mapping[str, Any], location: str, name: str
    ) -> Dict[str, Any]:
        """Rewrites a complete ``config`` block into the spelling the class itself reads.

        A complete block is not applied field by field like a sparse override — the
        configuration class deserializes it as a whole, because only the class knows how to
        rebuild the nested objects some of its fields hold. That deserializer reads an
        enum-typed field by the member's *value*, while this format writes enums by the
        member's *name*, and the two differ for every enum whose values are not their names.
        Bridging that difference here, rather than in the deserializer, keeps one spelling in
        the file and leaves the class's own serialization untouched.

        Nothing else is touched: numbers, strings, nested mappings and lists are handed on
        exactly as written, and so is the bare word ``AUTO``, which a sizable field's own
        decoder understands.

        Args:
            block: The complete ``config`` block as the file carries it.
            location: The dotted key path of the block, for the message.
            name: The component's name, for the message.

        Returns:
            A fresh mapping ready for the configuration class's deserializer.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when a value on an enum-typed field names no
                member, listing the members the enum has.
        """
        payload: Dict[str, Any] = {}
        for key, value in block.items():
            enum_class = self._enum_type_of(key)
            if enum_class is None or not isinstance(value, str) or value == _AutoSize.WIRE_SPELLING:
                payload[key] = value
                continue
            member = self._decode_enum(enum_class, value, f"{location}.{key}", name, key)
            payload[key] = member.value
        return payload

    def wire_value(self, field_name: str, value: Any) -> Any:
        """Returns a plain value spelled the way the field's own type spells it.

        One mismatch is common enough to matter and invisible until it bites: a field annotated
        ``float`` whose default was written as a whole number holds an ``int``. Reading such a
        value back through the configuration class turns it into a ``float``, so a record that
        wrote it as an ``int`` would describe a system that differs from the one its own
        re-execution builds — not numerically, but textually, which is enough to break the
        promise that re-running a record reproduces it.

        Nothing else is converted. A value whose type the annotation admits is written as it is,
        and a field whose annotation the codec could not resolve is left alone entirely.

        Args:
            field_name: The field the value belongs to.
            value: The plain value about to be written.

        Returns:
            The value, widened to ``float`` where that is the only numeric type the field admits.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            return value
        candidates = self._candidate_types(self.hints.get(field_name))
        if float in candidates and int not in candidates:
            return float(value)
        return value

    def _enum_type_of(self, field_name: str) -> Optional[Type[enum.Enum]]:
        """Returns the enum a field is typed by, or ``None`` when it holds something else.

        Both declaration styles are read: a plain annotation, whose union members carry the
        enum, and a sizable field, whose declaration records the enum separately so that the
        ``AUTO`` decoder can still coerce it.

        Args:
            field_name: The field to look up; a key that is no field at all answers ``None``.

        Returns:
            The enum class, or ``None``.
        """
        field = self.fields.get(field_name)
        if field is None:
            return None
        recorded = field.metadata.get(SizedFieldMetadata.VALUE_TYPE)
        if isinstance(recorded, type) and issubclass(recorded, enum.Enum):
            return recorded
        for candidate in self._candidate_types(self.hints.get(field_name)):
            if isinstance(candidate, type) and issubclass(candidate, enum.Enum):
                return candidate
        return None

    def _decoder_failure(
        self, field_name: str, value: Any, location: str, name: str, error: Exception
    ) -> EnergySystemBindingError:
        """Turns a refusal by the field's own decoder into a message an author can act on.

        A declared decoder reports what it could not do, not what the author could have
        written instead, and for an enum-typed field that difference is the whole message: the
        members are a short closed set and printing them turns the rejection into the fix. Any
        other field keeps the decoder's own sentence, which is the best available account of
        why the value was refused.

        Args:
            field_name: The field being overridden.
            value: The written value.
            location: The dotted key path, for the message.
            name: The component's name, for the message.
            error: What the field's decoder raised.

        Returns:
            The exception to raise.
        """
        for candidate in self._candidate_types(self.hints.get(field_name)):
            if isinstance(candidate, type) and issubclass(candidate, enum.Enum):
                try:
                    self._decode_enum(candidate, value, location, name, field_name)
                except EnergySystemBindingError as enum_error:
                    return enum_error
        return self._undecodable(location, name, field_name, value, str(error))

    def _declared_decoder(self, field_name: str) -> Optional[Any]:
        """Returns the field's own wire decoder, when its declaration carries one.

        A sizable field declares an encoder/decoder pair together with its law, and other
        fields may carry one from the serialization layer. Where such a decoder exists it is
        authoritative — it is the class author's statement of how the wire form of that field
        is read — so the codec uses it instead of its own type-directed rules.

        Args:
            field_name: The field to look up.

        Returns:
            The decoder callable, or ``None`` when the field declares none.
        """
        metadata = self.fields[field_name].metadata.get("dataclasses_json")
        if metadata is None:
            return None
        decoder = getattr(metadata, "decoder", None)
        if decoder is None and isinstance(metadata, Mapping):
            decoder = metadata.get("decoder")
        return decoder

    def decode_argument(
        self, annotation: Any, value: Any, location: str, name: str, field_name: str
    ) -> Any:
        """Decodes one written value against a type annotation, whatever declared it.

        The annotation-driven half of the codec, factored out of the field path so that the
        arguments of a named constructor get exactly the treatment a ``config`` block's values
        get: an enum arrives as the member and not as the string that spells it, a mapping on
        a dataclass-typed parameter is rebuilt by that class, a list is decoded item by item,
        and a plain scalar is checked against the types the annotation admits.

        An annotation :func:`~hisim.config.presets.is_decodable_annotation` refuses gives the
        codec nothing to work with — ``Any``, a tuple, a plain class — and the value is passed
        on as written, which is the field path's standing concession to a class that
        deserializes such a field itself. A constructor parameter never has an annotation like
        that: the decorator refused it at import time, from the same predicate.

        Args:
            annotation: The resolved annotation to decode against; ``None`` when there is
                none, in which case the value is passed on untouched.
            value: The written value.
            location: The dotted key path of the value, for the message.
            name: The component's name, for the message.
            field_name: What the value is being written to — a field name or a parameter
                name — as the message spells it.

        Returns:
            The decoded value.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when the value contradicts the annotation.
        """
        if annotation is None or not is_decodable_annotation(annotation):
            return value
        item_annotation = self._list_item_annotation(annotation)
        if item_annotation is not None and isinstance(value, list):
            return [
                self.decode_argument(item_annotation, item, f"{location}[{index}]", name, field_name)
                for index, item in enumerate(value)
            ]
        if value is None:
            # Asked before the enum lookup, which would otherwise refuse the very null an
            # Optional exists to admit, and asked at all, because a null on a field that
            # admits none used to be written through as None.
            if self._admits_none(annotation):
                return None
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNDECODABLE_VALUE,
                location,
                f"'{name}' sets '{field_name}' to null, but it holds "
                f"{self._render_annotation(annotation)} and must not be null.",
            )
        candidates = self._candidate_types(annotation)
        member = self._decode_admitted_enum(candidates, value, location, name, field_name)
        if member is not None:
            return member
        rebuilt = self._rebuild_nested_dataclass(candidates, value, location, name, field_name)
        if rebuilt is not None:
            return rebuilt
        return self._decode_scalar(candidates, value, location, name, field_name)

    @classmethod
    def _decode_admitted_enum(
        cls, candidates: Tuple[Any, ...], value: Any, location: str, name: str, field_name: str
    ) -> Optional[Any]:
        """Decodes the value as an enum member where the annotation admits an enum.

        Where the enum is the only thing admitted, a value naming no member is refused here
        with the members listed. Where a plain ``str`` is admitted beside it — the shape of a
        parameter that takes either a catalogue key or a free name — a string that names no
        member is not a mistake but the other half of the union, so it is handed back for the
        scalar path to accept.

        Args:
            candidates: The types the annotation admits.
            value: The written value.
            location: The dotted key path, for the message.
            name: The component's name, for the message.
            field_name: The field or parameter being written, for the message.

        Returns:
            The member, or ``None`` when no enum is admitted or a free string was written.

        Raises:
            EnergySystemBindingError: ``EF-1A`` naming the value and listing the members.
        """
        for candidate in candidates:
            if not (isinstance(candidate, type) and issubclass(candidate, enum.Enum)):
                continue
            if str in candidates and isinstance(value, str):
                return cls._enum_member(candidate, value)
            return cls._decode_enum(candidate, value, location, name, field_name)
        return None

    def check_argument_shape(
        self, annotation: Any, value: Any, location: str, name: str, parameter: str
    ) -> None:
        """Refuses a decoded constructor argument whose shape its parameter cannot hold.

        The field path may pass a value it could not type-check on to the configuration class,
        which validates itself and would refuse it. A constructor argument has no such second
        reader: it goes straight into a Python call, where a mapping that stayed a mapping or a
        list where one object belongs fails deep inside the builder, or worse, does not fail at
        all. So the shape the annotation asks for is checked once here, after the decoding,
        which is the only point at which "still a mapping" means "nothing rebuilt it".

        Args:
            annotation: The parameter's resolved annotation.
            value: The value as the decoding left it.
            location: The dotted key path of the argument, for the message.
            name: The component's name, for the message.
            parameter: The parameter's name, for the message.

        Raises:
            EnergySystemBindingError: ``EF-1A`` naming the parameter, the written value's type
                and the shape the parameter asks for.
        """
        clause = self._unmet_shape(annotation, value)
        if clause is None:
            return
        raise EnergySystemBindingError(
            EnergySystemErrorId.UNDECODABLE_VALUE,
            location,
            f"'{name}' sets '{parameter}' to {value!r} ({type(value).__name__}), which must "
            f"{clause}; the parameter is annotated {self._render_annotation(annotation)}.",
        )

    @classmethod
    def _unmet_shape(cls, annotation: Any, value: Any) -> Optional[str]:
        """Names the shape an annotation asks for, when the decoded value is not of it.

        The four shapes a decoded value can still be wrong in, each of which reaches the
        builder as a Python error naming nothing the author wrote: a null where the parameter
        admits none, a list where no list is admitted, a single value where only a list is,
        and a mapping or a scalar where the parameter wants an object of its own class. An
        annotation the decodability predicate refuses states no shape and is not policed.

        Args:
            annotation: The parameter's resolved annotation.
            value: The value as the decoding left it.

        Returns:
            The clause an error message completes with ``which must …``, or ``None`` when the
            value fits.
        """
        if annotation is None or not is_decodable_annotation(annotation):
            return None
        if value is None:
            return None if cls._admits_none(annotation) else "not be null"
        unmet = cls._unmet_list_shape(annotation, value)
        if unmet is not None or isinstance(value, list):
            return unmet
        return cls._unmet_object_shape(annotation, value)

    @classmethod
    def _unmet_list_shape(cls, annotation: Any, value: Any) -> Optional[str]:
        """Names the list shape an annotation asks for, when the value is not of it.

        The two mistakes a list makes: several values written where the parameter admits only
        one, and one written where it admits only several. The second is the reason this is
        asked of a *decoded* value at all — a mapping that rebuilt itself into the item class
        is a perfectly good item and still not the list its parameter iterates over.

        Args:
            annotation: The parameter's resolved annotation.
            value: The value as the decoding left it.

        Returns:
            The clause, or ``None`` when nothing about the list shape is wrong.
        """
        item_annotation = cls._list_item_annotation(annotation)
        if isinstance(value, list):
            return None if item_annotation is not None else "not be a list"
        if item_annotation is not None and all(
            member is NoneType or typing.get_origin(member) is list
            for member in cls._union_members(annotation)
        ):
            return f"be a list of {cls._render_annotation(item_annotation)}"
        return None

    @classmethod
    def _unmet_object_shape(cls, annotation: Any, value: Any) -> Optional[str]:
        """Names the object an annotation asks for, when the value is not one.

        Reached for a value that is neither ``None`` nor a list, so the question left is
        whether it is one of the types the annotation admits. A mapping that is still a
        mapping was rebuilt by nothing, and a scalar where only an object belongs is the
        shorthand — a catalogue name, say — that the format does not have.

        Args:
            annotation: The parameter's resolved annotation.
            value: The value as the decoding left it.

        Returns:
            The clause, or ``None`` when the value is of an admitted type or the annotation
            names no object to compare it against.
        """
        candidates = cls._candidate_types(annotation)
        concrete = tuple(candidate for candidate in candidates if isinstance(candidate, type))
        if concrete and isinstance(value, concrete):
            return None
        if isinstance(value, Mapping):
            return "not be a mapping"
        nested = [candidate for candidate in concrete if dataclasses.is_dataclass(candidate)]
        return f"be a mapping of {nested[0].__name__}'s own fields" if nested else None

    @classmethod
    def _union_members(cls, annotation: Any) -> Tuple[Any, ...]:
        """Returns a union's members as written, ``NoneType`` included, or the annotation itself.

        The counterpart of :meth:`_candidate_types`, which drops ``NoneType`` because a value
        has to match one of the *other* members. Whether ``None`` is admitted at all is exactly
        what this one is asked.

        Args:
            annotation: The resolved annotation.

        Returns:
            The union's members, or a single-element tuple for anything else.
        """
        origin = typing.get_origin(annotation)
        if origin is typing.Union or origin is types.UnionType:
            return typing.get_args(annotation)
        return (annotation,)

    @classmethod
    def _admits_none(cls, annotation: Any) -> bool:
        """Whether a written ``null`` is a legal value of this annotation."""
        return any(member is NoneType for member in cls._union_members(annotation))

    @classmethod
    def _render_annotation(cls, annotation: Any) -> str:
        """Renders an annotation the way an error message prints it, without the ``typing.``."""
        name = getattr(annotation, "__name__", None)
        if isinstance(name, str) and typing.get_origin(annotation) is None:
            return name
        return str(annotation).replace("typing.", "")

    @classmethod
    def _list_item_annotation(cls, annotation: Any) -> Optional[Any]:
        """Returns the item type of a list-admitting annotation, or ``None`` for any other.

        Both spellings that occur in HiSim are read: a plain ``List[X]`` and the
        ``Union[X, List[X]]`` of a parameter that takes either one thing or several — the LPG
        occupancy's household, where a list means one reference per apartment. Reading the
        item type *before* the union is flattened is what keeps a list from being mistaken for
        a single value of its own item type.

        Args:
            annotation: The resolved annotation.

        Returns:
            The type each item is decoded against, or ``None`` when no list is admitted.
        """
        origin = typing.get_origin(annotation)
        if origin is list:
            arguments = typing.get_args(annotation)
            return arguments[0] if len(arguments) == 1 else None
        if origin is typing.Union or origin is types.UnionType:
            for member in typing.get_args(annotation):
                item = cls._list_item_annotation(member)
                if item is not None:
                    return item
        return None

    @classmethod
    def _rebuild_nested_dataclass(
        cls, candidates: Tuple[Any, ...], value: Any, location: str, name: str, field_name: str
    ) -> Optional[Any]:
        """Rebuilds a mapping written onto a dataclass-typed field through that class itself.

        A complete ``config`` block is deserialized by the configuration class as a whole, which
        is what keeps its nested objects objects. A sparse override took the other road — its
        values were written onto the instance one field at a time — so a nested mapping would
        land as a plain dict and flatten the very object the class had rebuilt. This gives the
        override the same treatment the block gets: the field's own class reads its mapping.

        Args:
            candidates: The types the field admits.
            value: The written value.
            location: The dotted key path, for the message.
            name: The component's name, for the message.
            field_name: The field being overridden, for the message.

        Returns:
            The rebuilt object, or ``None`` when the field is not a dataclass taking a mapping —
            the scalar path then decides.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when the mapping does not fit the nested class.
        """
        if not isinstance(value, Mapping):
            return None
        nested = [
            candidate
            for candidate in candidates
            if isinstance(candidate, type) and dataclasses.is_dataclass(candidate)
        ]
        if len(nested) != 1:
            return None
        try:
            from_dict = getattr(nested[0], "from_dict", None)
            if callable(from_dict):
                return from_dict(dict(value))
            return nested[0](**dict(value))
        except Exception as error:  # pylint: disable=broad-except
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNDECODABLE_VALUE,
                location,
                f"'{name}' sets '{field_name}' to a mapping that does not fit "
                f"{nested[0].__name__}: {error}",
            ) from error

    @classmethod
    def _candidate_types(cls, annotation: Any) -> Tuple[Any, ...]:
        """Flattens an annotation into the concrete types a value may take.

        ``Optional[X]`` and every other union is reduced to its members with ``None`` removed,
        because a written value is never ``None`` unless it was written as such, and the union
        members are what the value has to match one of.

        Args:
            annotation: The resolved type annotation of a field.

        Returns:
            The candidate types, in declaration order.
        """
        origin = typing.get_origin(annotation)
        if origin is None:
            return (annotation,)
        arguments = typing.get_args(annotation)
        if not arguments:
            return (annotation,)
        return tuple(argument for argument in arguments if argument is not NoneType)

    @classmethod
    def _decode_enum(
        cls, enum_class: Type[enum.Enum], value: Any, location: str, name: str, field_name: str
    ) -> Any:
        """Turns the written spelling of an enum-typed field into the member itself.

        Both spellings are accepted — the member name and the member value — because HiSim's
        configuration enums spell the two alike and an author cannot be expected to know which
        one a given enum uses. What is not accepted is passing the string through: the caller
        gets a member or an error.

        Args:
            enum_class: The enum the field is typed by.
            value: The written value.
            location: The dotted key path, for the message.
            name: The component's name, for the message.
            field_name: The field being overridden, for the message.

        Returns:
            The enum member.

        Raises:
            EnergySystemBindingError: ``EF-1A`` naming the value and listing the members.
        """
        member = cls._enum_member(enum_class, value)
        if member is not None:
            return member
        raise EnergySystemBindingError(
            EnergySystemErrorId.UNDECODABLE_VALUE,
            location,
            f"'{name}' sets '{field_name}' to {value!r}, which is no member of "
            f"{enum_class.__name__}.",
            alternatives=tuple(enum_class.__members__),
            alternatives_label=f"members of {enum_class.__name__}",
            offending_value=value if isinstance(value, str) else None,
        )

    @classmethod
    def _enum_member(cls, enum_class: Type[enum.Enum], value: Any) -> Optional[Any]:
        """Looks a written value up among an enum's members, by name and then by value.

        The lookup alone, without the refusal: a caller that admits something else beside the
        enum needs to know that the value names no member without being told it is an error.

        Args:
            enum_class: The enum to look the value up in.
            value: The written value.

        Returns:
            The member, or ``None`` when the value names none.
        """
        if isinstance(value, enum_class):
            return value
        if isinstance(value, str):
            member = enum_class.__members__.get(value)
            if member is not None:
                return member
            try:
                return enum_class(value)
            except ValueError:
                return None
        return None

    @classmethod
    def _decode_scalar(
        cls, candidates: Tuple[Any, ...], value: Any, location: str, name: str, field_name: str
    ) -> Any:
        """Checks a plain value against the simple types a field accepts.

        Only the four scalar types are policed, and only when the field admits nothing else:
        a wrong string where a number belongs is the mistake that produces a plausible-looking
        but wrong simulation, and it is cheap to catch. Anything richer — a nested dataclass, a
        mapping, a list — is handed on untouched, because guessing at its shape here would
        duplicate the configuration class's own deserialization.

        Args:
            candidates: The types the field admits.
            value: The written value.
            location: The dotted key path, for the message.
            name: The component's name, for the message.
            field_name: The field being overridden, for the message.

        Returns:
            The value, converted to ``float`` where the field wants one and an integer was
            written, and unchanged otherwise.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when no admitted scalar type fits the value.
        """
        scalars = tuple(candidate for candidate in candidates if candidate in (bool, int, float, str))
        if len(scalars) != len(candidates) or not scalars:
            return value
        if bool in scalars and isinstance(value, bool):
            return value
        if float in scalars and isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if int in scalars and isinstance(value, int) and not isinstance(value, bool):
            return value
        if str in scalars and isinstance(value, str):
            return value
        expected = ", ".join(candidate.__name__ for candidate in scalars)
        raise EnergySystemBindingError(
            EnergySystemErrorId.UNDECODABLE_VALUE,
            location,
            f"'{name}' sets '{field_name}' to {value!r} ({type(value).__name__}), but the "
            f"field holds {expected}.",
        )

    @classmethod
    def _undecodable(
        cls, location: str, name: str, field_name: str, value: Any, detail: str
    ) -> EnergySystemBindingError:
        """Builds the ``EF-1A`` rejection of a value the field's own decoder refused.

        Args:
            location: The dotted key path of the value.
            name: The component's name.
            field_name: The field being overridden.
            value: The written value.
            detail: What the field's decoder said, kept verbatim.

        Returns:
            The exception to raise.
        """
        return EnergySystemBindingError(
            EnergySystemErrorId.UNDECODABLE_VALUE,
            location,
            f"'{name}' sets '{field_name}' to {value!r}, which the field's own decoder "
            f"refused: {detail}.",
        )
