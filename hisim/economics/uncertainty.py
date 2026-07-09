"""Uncertainty band type for the lifecycle cost engine (cost_spec.md §3.9).

Every monetary input is a (minimum, average, maximum) triplet. The engine evaluates every
timeline in three coherent *slots* (LOW / AVERAGE / HIGH worlds); within signed cash-flow
amounts the invariant ``minimum <= average <= maximum`` still holds because revenue-type
parameters enter with their band mirrored (see :func:`UncertainValue.as_revenue`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Union


class Slot(str, Enum):
    """The three coherent evaluation worlds of cost_spec.md §3.9."""

    LOW = "low"
    AVERAGE = "average"
    HIGH = "high"


@dataclass(frozen=True)
class UncertainValue:
    """A monetary figure with an uncertainty band. Invariant: minimum <= average <= maximum."""

    average: float
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        """Validates finiteness and band ordering (with float-noise snapping)."""
        for value in (self.average, self.minimum, self.maximum):
            if not math.isfinite(value):
                raise ValueError(f"UncertainValue must be finite, got {self!r}.")
        if not self.minimum <= self.average <= self.maximum:
            # Slot-wise arithmetic accumulates float noise; snap violations within epsilon
            # instead of failing (real ordering violations are far above this threshold).
            scale = max(1.0, abs(self.average), abs(self.minimum), abs(self.maximum))
            tolerance = 1e-9 * scale
            if self.minimum - self.average <= tolerance and self.average - self.maximum <= tolerance:
                object.__setattr__(self, "minimum", min(self.minimum, self.average))
                object.__setattr__(self, "maximum", max(self.maximum, self.average))
            else:
                raise ValueError(f"UncertainValue band violated (min <= avg <= max): {self!r}.")

    @staticmethod
    def exact(value: float) -> "UncertainValue":
        """Degenerate band for values that are actually certain (statutory amounts, contracts)."""
        return UncertainValue(value, value, value)

    @staticmethod
    def from_json(value: Any, context: str = "") -> "UncertainValue":
        """Parses a JSON value: a bare number means exact, an object declares a band."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return UncertainValue.exact(float(value))
        if isinstance(value, dict):
            try:
                return UncertainValue(
                    average=float(value["avg"]),
                    minimum=float(value["min"]),
                    maximum=float(value["max"]),
                )
            except KeyError as err:
                raise ValueError(
                    f"Uncertainty band {context or value} must have 'min', 'avg' and 'max' keys."
                ) from err
        raise ValueError(f"Cannot parse uncertainty value {value!r} ({context}).")

    def to_json(self) -> Union[float, dict]:
        """Serializes: degenerate bands as bare numbers, real bands as objects."""
        if self.minimum == self.average == self.maximum:
            return self.average
        return {"min": self.minimum, "avg": self.average, "max": self.maximum}

    def is_exact(self) -> bool:
        """True when the band is degenerate (min = avg = max)."""
        return self.minimum == self.average == self.maximum

    def slot(self, slot: Slot) -> float:
        """Value in the given evaluation world."""
        if slot == Slot.LOW:
            return self.minimum
        if slot == Slot.HIGH:
            return self.maximum
        return self.average

    def as_revenue(self) -> "UncertainValue":
        """Mirrors the band for revenue-type parameters (§3.9).

        In the optimistic LOW world a revenue comes in at its *maximum*. Returned value is the
        signed (negative) cash-flow band ordered LOW <= AVERAGE <= HIGH again.
        """
        return UncertainValue(average=-self.average, minimum=-self.maximum, maximum=-self.minimum)

    def __add__(self, other: "UncertainValue") -> "UncertainValue":
        """Slot-wise addition."""
        return UncertainValue(
            average=self.average + other.average,
            minimum=self.minimum + other.minimum,
            maximum=self.maximum + other.maximum,
        )

    def __sub__(self, other: "UncertainValue") -> "UncertainValue":
        """Slot-wise difference (same-world comparison, NOT interval arithmetic; §3.9).

        When the subtrahend's band is wider than the minuend's, the LOW-world delta can
        exceed the HIGH-world delta (e.g. dropping a very uncertain gas bill); the result is
        therefore the *envelope* of the three slot deltas — "minimum" reads "best-case
        delta", not "LOW-world delta".
        """
        low_world = self.minimum - other.minimum
        high_world = self.maximum - other.maximum
        average = self.average - other.average
        return UncertainValue(
            average=average,
            minimum=min(low_world, average, high_world),
            maximum=max(low_world, average, high_world),
        )

    def scale(self, factor: float) -> "UncertainValue":
        """Multiplies all slots by a non-negative scalar (kWh, discount factor, escalation)."""
        if factor < 0:
            raise ValueError(
                "scale() only supports non-negative factors to preserve slot ordering; "
                "use as_revenue() for sign flips."
            )
        return UncertainValue(self.average * factor, self.minimum * factor, self.maximum * factor)

    def multiply_band(self, other: "UncertainValue") -> "UncertainValue":
        """Slot-wise product of two coherent cost-type bands (e.g. maintenance rate x investment).

        Both operands must be non-negative in every slot so slot ordering is preserved.
        """
        if self.minimum < 0 or other.minimum < 0:
            raise ValueError("multiply_band() requires non-negative bands in every slot.")
        return UncertainValue(
            average=self.average * other.average,
            minimum=self.minimum * other.minimum,
            maximum=self.maximum * other.maximum,
        )

    def clamp_upper(self, cap: "UncertainValue") -> "UncertainValue":
        """Applies a cap per slot (subsidy caps are checked per slot; §3.9, §5.4)."""
        return UncertainValue(
            average=min(self.average, cap.average),
            minimum=min(self.minimum, cap.minimum),
            maximum=min(self.maximum, cap.maximum),
        )

    @staticmethod
    def sum(values: Iterable["UncertainValue"]) -> "UncertainValue":
        """Slot-wise sum; empty input yields the exact zero band."""
        total = ZERO
        for value in values:
            total = total + value
        return total


#: The exact zero band, reused everywhere as the neutral element.
ZERO = UncertainValue.exact(0.0)
