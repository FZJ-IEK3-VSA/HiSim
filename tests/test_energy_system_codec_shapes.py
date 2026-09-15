"""Tests that the decodability gate and the value decoder state the same set of shapes.

A named constructor is refused at import time when one of its parameters asks for something
no written value can become, and a written value is decoded against the annotation of the
field or parameter it lands on. Those are two readings of one question, and they used to be
two implementations of it: ``is_decodable_annotation`` admitted a dataclass only if it had a
``from_dict`` while the decoder happily rebuilt one through its constructor, it admitted
``Union[A, B]`` of two dataclasses that the decoder then refused to disambiguate, and it said
nothing at all about ``None`` although the decoder had a rule for it.

So the predicate is now the single statement, the codec imports and consults it, and this
file is the matrix that keeps the two honest: every shape the predicate names is decoded here
and its result inspected — by type, not only by value, because HiSim's enums derive from
``str`` and an undecoded member compares equal to the string that spells it — and every shape
it refuses is listed with the reason it cannot be written.

Two decoder bugs the matrix pins by construction: a ``null`` written for an ``Optional`` enum
used to be handed to the enum lookup, which refused the very absence the ``Optional`` exists
to admit, and a ``Union[str, Enum]`` refused every string that was not a member instead of
letting the free-string half of the union have it.
"""

# clean

import enum
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import pytest
from dataclasses_json import dataclass_json

from hisim.components.air_conditioner import AirConditionerConfig
from hisim.config.presets import is_decodable_annotation
from hisim.energy_system.codec import ConfigValueCodec
from hisim.energy_system.errors import EnergySystemBindingError


class Fuel(str, enum.Enum):
    """An enum whose member values differ from its member names, as HiSim's often do.

    The difference is the whole point: a decoder that passes the written string through
    produces a value that compares equal to the member and is not the member, which is the
    failure the codec exists to prevent and the one a test comparing with ``==`` misses.
    """

    DIESEL = "Diesel"
    PETROL = "Petrol"


@dataclass_json
@dataclass
class Station:
    """A nested object that can rebuild itself from a mapping, through ``from_dict``."""

    code: str
    altitude_in_m: float = 0.0


@dataclass
class Plain:
    """A nested object with no ``from_dict``, rebuilt through its own constructor.

    The predicate used to refuse this shape while the decoder rebuilt it anyway, which is one
    half of the divergence this file pins.
    """

    size: int


class NotADataclass:
    """A plain class: no fields to write, nothing to rebuild it from."""


#: Every shape the predicate admits, as ``(annotation, written value, decoded value)``. The
#: decoded value is compared by value *and* by type, which is what distinguishes an enum
#: member from the string that spells it and a float from the integer that was written.
DECODABLE: Tuple[Tuple[str, Any, Any, Any], ...] = (
    ("bool", bool, True, True),
    ("int", int, 5, 5),
    ("float from an int", float, 5, 5.0),
    ("str", str, "profile", "profile"),
    ("enum by member name", Fuel, "DIESEL", Fuel.DIESEL),
    ("enum by member value", Fuel, "Diesel", Fuel.DIESEL),
    ("dataclass with from_dict", Station, {"code": "DE.01", "altitude_in_m": 202.0},
     Station(code="DE.01", altitude_in_m=202.0)),
    ("dataclass without from_dict", Plain, {"size": 2}, Plain(size=2)),
    ("list of floats", List[float], [1, 2.5], [1.0, 2.5]),
    ("list of dataclasses", List[Plain], [{"size": 1}, {"size": 2}], [Plain(size=1), Plain(size=2)]),
    ("optional enum, written", Optional[Fuel], "PETROL", Fuel.PETROL),
    ("optional enum, written null", Optional[Fuel], None, None),
    ("one or several, one", Union[Plain, List[Plain]], {"size": 3}, Plain(size=3)),
    ("one or several, several", Union[Plain, List[Plain]], [{"size": 3}], [Plain(size=3)]),
    ("enum or free string, a member", Union[str, Fuel], "DIESEL", Fuel.DIESEL),
    ("enum or free string, a string", Union[str, Fuel], "anything else", "anything else"),
)

#: Every shape the predicate refuses, with what makes it unwritable. A file carries scalars,
#: mappings and lists; these are the annotations none of those three can become without the
#: decoder guessing.
REFUSED: Tuple[Tuple[str, Any], ...] = (
    ("anything at all", Any),
    ("two dataclasses to choose between", Union[Plain, Station]),
    ("two lists to choose between", Union[List[Plain], List[Station]]),
    ("a callable", Callable[[], None]),
    ("a plain class", NotADataclass),
    ("a mapping of its own", Dict[str, float]),
    ("a tuple", Tuple[float, float]),
    ("a list of anything", list),
)


def kinds_of(value: Any) -> Any:
    """Returns the type of a value, or the types of a list's items.

    Args:
        value: A decoded value.

    Returns:
        The type, or the list of item types, which is what an equality comparison hides.
    """
    if isinstance(value, list):
        return [type(item) for item in value]
    return type(value)


def codec() -> ConfigValueCodec:
    """Builds a codec on a real configuration class.

    The class matters only for the field path; ``decode_argument`` is asked about an
    annotation the caller hands it, so any real class serves — and this one carries the
    ``List[float]`` fields the override test below needs.

    Returns:
        A codec over :class:`AirConditionerConfig`.
    """
    return ConfigValueCodec(AirConditionerConfig)


@pytest.mark.base
@pytest.mark.parametrize(
    "annotation, written, expected", [case[1:] for case in DECODABLE], ids=[case[0] for case in DECODABLE]
)
def test_every_shape_the_gate_admits_is_one_the_decoder_produces(
    annotation: Any, written: Any, expected: Any
) -> None:
    """Catches the gate and the decoder disagreeing about one written shape.

    Failure mode caught: a constructor parameter accepted at import time whose value no file
    can actually supply, or refused at import time although the decoder would have read it —
    both of which were true before the predicate became the only statement of the set.
    """
    assert is_decodable_annotation(annotation), f"the gate refuses {annotation}"

    decoded = codec().decode_argument(annotation, written, "components.Probe.x", "Probe", "x")

    assert decoded == expected
    assert kinds_of(decoded) == kinds_of(expected)


@pytest.mark.base
@pytest.mark.parametrize("annotation", [case[1] for case in REFUSED], ids=[case[0] for case in REFUSED])
def test_a_shape_no_written_value_can_become_is_refused_by_the_gate(annotation: Any) -> None:
    """Catches the gate widening to a shape the decoder could only guess at.

    Failure mode caught: a parameter typed ``Any`` or ``Union[A, B]`` over two dataclasses
    passing the declaration check, which would leave the schema permissive and the decoder
    picking one of two classes at random.
    """
    assert not is_decodable_annotation(annotation)


@pytest.mark.base
@pytest.mark.parametrize("annotation", [case[1] for case in REFUSED], ids=[case[0] for case in REFUSED])
def test_a_shape_the_gate_refuses_is_passed_through_untouched(annotation: Any) -> None:
    """Catches the decoder inventing a reading for a shape it has no rule for.

    A field may be annotated in a way the codec cannot act on — the class deserializes it
    itself — and the standing concession is that such a value is handed on as written. It has
    to be a *pass-through*, though, not a half-decoding: the value that comes back is the
    value that went in.
    """
    written = {"whatever": [1, 2]}

    assert codec().decode_argument(annotation, written, "components.Probe.x", "Probe", "x") is written


@pytest.mark.base
def test_a_null_on_a_parameter_that_admits_none_is_not_looked_up_in_the_enum() -> None:
    """Catches ``null`` being refused as a misspelled enum member.

    The defect this pins: the enum lookup ran before the ``None`` check, so a written ``null``
    on an ``Optional`` enum was reported as naming no member — the one value an ``Optional``
    exists to accept.
    """
    assert codec().decode_argument(Optional[Fuel], None, "components.Probe.x", "Probe", "x") is None


@pytest.mark.base
def test_a_null_where_none_is_not_admitted_is_refused_rather_than_written_through() -> None:
    """Catches a written ``null`` silently becoming the value of a field that cannot hold it.

    The defect this pins: ``None`` was returned unchecked, so a mandatory float ended up
    holding ``None`` and failed much later, in arithmetic, with nothing left to name the line
    that wrote it.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        codec().decode_argument(float, None, "components.Probe.x", "Probe", "x")

    assert "must not be null" in str(raised.value)


@pytest.mark.base
def test_a_string_that_names_no_member_is_refused_when_only_the_enum_is_admitted() -> None:
    """Catches the free-string fallback leaking into a parameter that admits only the enum.

    The fallback belongs to ``Union[str, Enum]`` alone. Where the enum is the only thing
    admitted, a string naming no member is still the mistake it always was, and the message
    still has to list the members.
    """
    with pytest.raises(EnergySystemBindingError) as raised:
        codec().decode_argument(Fuel, "Kerosene", "components.Probe.x", "Probe", "x")

    assert "no member of Fuel" in str(raised.value)
    assert "DIESEL" in str(raised.value)


@pytest.mark.base
def test_a_list_written_over_a_list_field_is_decoded_item_by_item() -> None:
    """Catches a list override of a ``List[float]`` field being refused as a single number.

    The field path reduced ``List[float]`` to its item type and then complained that the list
    was not a float, so a real configuration's reference curve could not be overridden at all.
    Reading the list case first fixes that, and the integers in it arrive as floats.
    """
    decoded = codec().decode("eer_ref", [3, 3.5], "components.AC.config.eer_ref", "AC")

    assert decoded == [3.0, 3.5]
    assert kinds_of(decoded) == [float, float]
