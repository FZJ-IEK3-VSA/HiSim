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

Everything the codec cannot decode is a hard error naming the entry, the field, the value and
the type expected — never a silent pass-through of a wrong type, because a wrong number in a
configuration surfaces as a wrong simulation result rather than as a crash.
"""

# clean

from __future__ import annotations

import dataclasses
import enum
import typing
from typing import Any, Dict, Mapping, Optional, Tuple, Type

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
        return self._decode_by_annotation(field_name, value, location, name)

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

    def _decode_by_annotation(self, field_name: str, value: Any, location: str, name: str) -> Any:
        """Decodes a value against the field's resolved type annotation.

        Args:
            field_name: The field being overridden.
            value: The written value.
            location: The dotted key path, for the message.
            name: The component's name, for the message.

        Returns:
            The decoded value; the value unchanged when the annotation gives the codec
            nothing to go on, which leaves the final say to the configuration class itself.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when the value contradicts the annotation.
        """
        annotation = self.hints.get(field_name)
        if annotation is None:
            return value
        candidates = self._candidate_types(annotation)
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, enum.Enum):
                return self._decode_enum(candidate, value, location, name, field_name)
        if value is None:
            return None
        return self._decode_scalar(candidates, value, location, name, field_name)

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
        return tuple(argument for argument in arguments if argument is not type(None))

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
        if isinstance(value, enum_class):
            return value
        if isinstance(value, str):
            member = enum_class.__members__.get(value)
            if member is not None:
                return member
            try:
                return enum_class(value)
            except ValueError:
                pass
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
