"""The uncertainty band type of the lifecycle cost engine (cost_spec.md §3.9).

Every monetary value is an `UncertainValue`, a (minimum, best_estimate, maximum) band. The engine evaluates three
coherent worlds, called slots (LOW, BEST_ESTIMATE, HIGH), in one pass; arithmetic is slot-wise. LOW and HIGH are
envelopes, "everything comes in cheap / expensive", not confidence intervals. This is the package's leaf module and
imports nothing from it; which categories are revenue-type is decided in `timeline.CategoryRules`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Iterable, Optional, Union


class Slot(str, Enum):
    """The three coherent evaluation worlds of §3.9.

    LOW is the optimistic world (every cost-type parameter at its minimum, every revenue-type parameter at its
    maximum), HIGH its mirror image, and BEST_ESTIMATE the headline world. A slot belongs to the whole evaluation:
    within one slot every parameter is set consistently, so the LOW and HIGH totals are envelopes of a plan.
    """

    LOW = "low"
    BEST_ESTIMATE = "best_estimate"
    HIGH = "high"


@dataclass(frozen=True)
class UncertainValue:
    """A monetary figure with an uncertainty band; invariant minimum <= best_estimate <= maximum.

    The universal value type of the engine: prices, cash-flow entries, NPVs, KPIs and export cells are all bands, so
    uncertainty is never dropped. Frozen and hashable, so the provenance ledger can intern values (§3.10).

    The fields are the values in the three worlds after any revenue mirroring: `minimum` is the LOW-world value, which
    for a revenue is the largest income (see `as_revenue`). Arithmetic is slot-wise (`__add__`, `scale`,
    `multiply_band`, `clamp_upper`), except `__sub__`. The positional order is `UncertainValue(best_estimate, minimum,
    maximum)`; prefer keywords or `exact`.
    """

    #: The exact zero band, reused everywhere as the neutral element. Assigned right after the
    #: class body, because it is an instance of the class it hangs on.
    ZERO: ClassVar["UncertainValue"]

    best_estimate: float
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        """Validate finiteness and band ordering, snapping float-noise violations within epsilon.

        A revenue booked without `as_revenue` therefore fails here instead of producing an inverted band.
        """
        for value in (self.best_estimate, self.minimum, self.maximum):
            if not math.isfinite(value):
                raise ValueError(f"UncertainValue must be finite, got {self!r}.")
        if not self.minimum <= self.best_estimate <= self.maximum:
            # Slot-wise arithmetic accumulates float noise; snap violations within epsilon
            # instead of failing (real ordering violations are far above this threshold).
            scale = max(1.0, abs(self.best_estimate), abs(self.minimum), abs(self.maximum))
            tolerance = 1e-9 * scale
            if self.minimum - self.best_estimate <= tolerance and self.best_estimate - self.maximum <= tolerance:
                object.__setattr__(self, "minimum", min(self.minimum, self.best_estimate))
                object.__setattr__(self, "maximum", max(self.maximum, self.best_estimate))
            else:
                raise ValueError(f"UncertainValue band violated (min <= best_estimate <= max): {self!r}.")

    @staticmethod
    def exact(value: float) -> "UncertainValue":
        """Return a degenerate band for a certain value (statutory amounts, contracts, simulated kWh)."""
        return UncertainValue(value, value, value)

    @staticmethod
    def from_json(value: Any, context: str = "") -> "UncertainValue":
        """Parse a JSON value: a bare number is exact, an object declares a band (§3.9).

        Every monetary field of the data files and stored results accepts `0.30` or `{"min": .., "best_estimate": ..,
        "max": ..}`. The constructor validates ordering, so a malformed band fails at load time.

        Args:
            value: A JSON number (not bool) or a dict with "min", "best_estimate" and "max".
            context: Dotted path of the field, for error messages.

        Returns:
            The parsed band; a bare number yields min = best_estimate = max.

        Raises:
            ValueError: If the dict misses a key, or the value is neither number nor dict.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return UncertainValue.exact(float(value))
        if isinstance(value, dict):
            try:
                return UncertainValue(
                    best_estimate=float(value["best_estimate"]),
                    minimum=float(value["min"]),
                    maximum=float(value["max"]),
                )
            except KeyError as err:
                raise ValueError(
                    f"Uncertainty band {context or value} must have 'min', 'best_estimate' and 'max' keys."
                ) from err
        raise ValueError(f"Cannot parse uncertainty value {value!r} ({context}).")

    def to_json(self) -> Union[float, dict]:
        """Serialize: an exact band as a bare number, a real band as an object; the inverse of `from_json`."""
        if self.minimum == self.best_estimate == self.maximum:
            return self.best_estimate
        return {"min": self.minimum, "best_estimate": self.best_estimate, "max": self.maximum}

    @staticmethod
    def optional_to_json(value: Optional["UncertainValue"]) -> Any:
        """Serialize an optional band, keeping None as JSON null.

        "No value" (never resolved) differs from zero euro (resolved and free), so None does not collapse to a zero
        band. Used by `serialization.py` and `input_audit.py`.

        Args:
            value: The band to serialize, or None.

        Returns:
            What `to_json` returns for a band, or None.
        """
        return value.to_json() if value is not None else None

    @staticmethod
    def optional_from_json(value: Any) -> Optional["UncertainValue"]:
        """Parse an optional band: JSON null stays None, anything else goes through `from_json`.

        Args:
            value: The parsed JSON value, or None for an absent or null field.

        Returns:
            The parsed band, or None.

        Raises:
            ValueError: If a non-null value is neither a number nor a well-formed band object.
        """
        return UncertainValue.from_json(value) if value is not None else None

    def is_exact(self) -> bool:
        """Return True when the band is degenerate (min = best_estimate = max); reports then draw no whisker."""
        return self.minimum == self.best_estimate == self.maximum

    def slot(self, slot: Slot) -> float:
        """Return the value in the given world: `minimum` for LOW, `maximum` for HIGH, else `best_estimate`."""
        if slot == Slot.LOW:
            return self.minimum
        if slot == Slot.HIGH:
            return self.maximum
        return self.best_estimate

    def as_revenue(self) -> "UncertainValue":
        """Mirror the band for a revenue-type parameter: negate it and swap the ends (§3.9).

        Example: a feed-in revenue stated as {min 100, best 120, max 150} becomes the entry band {min -150, best -120,
        max -100}.

        A revenue parameter is stated as money arriving, where the minimum is the stingiest outcome; a cash-flow entry
        is signed, and its `minimum` must be the LOW-world (optimistic) value, which for a revenue is the most money
        arriving. Plain negation would put the least favourable revenue into the LOW slot, breaking the ordering
        invariant and mixing worlds. Used wherever a negative entry is booked from a positively stated parameter:
        feed-in and controllability discounts, subsidy awards, residual values, anyway credits, loan disbursements and
        repayment grants, and the landlord leg of the modernization levy. Mirroring and entry sign are different
        properties; see `timeline.CategoryRules`.
        """
        return UncertainValue(best_estimate=-self.best_estimate, minimum=-self.maximum, maximum=-self.minimum)

    def __add__(self, other: "UncertainValue") -> "UncertainValue":
        """Add slot-wise: the LOW total is the sum of LOW-world values, not an interval bound."""
        return UncertainValue(
            best_estimate=self.best_estimate + other.best_estimate,
            minimum=self.minimum + other.minimum,
            maximum=self.maximum + other.maximum,
        )

    def __sub__(self, other: "UncertainValue") -> "UncertainValue":
        """Return the slot-wise difference, re-sorted into an envelope (§3.9).

        Variants are compared within the same world (§3.7), so a price band present in both cancels. When the
        subtrahend's band is wider than the minuend's, the LOW-world delta can exceed the HIGH-world delta (e.g.
        dropping a very uncertain gas bill), so the three deltas are sorted: `minimum` means the best-case delta, not
        the LOW-world delta.
        """
        low_world = self.minimum - other.minimum
        high_world = self.maximum - other.maximum
        best_estimate = self.best_estimate - other.best_estimate
        return UncertainValue(
            best_estimate=best_estimate,
            minimum=min(low_world, best_estimate, high_world),
            maximum=max(low_world, best_estimate, high_world),
        )

    def scale(self, factor: float) -> "UncertainValue":
        """Multiply all slots by a non-negative scalar (kWh, a discount or escalation factor, a count, a share).

        A negative factor would swap the band's ends; sign flips go through `as_revenue`. Scaling a negative revenue
        entry keeps it a revenue.

        Raises:
            ValueError: If `factor` is negative.
        """
        if factor < 0:
            raise ValueError(
                "scale() only supports non-negative factors to preserve slot ordering; "
                "use as_revenue() for sign flips."
            )
        return UncertainValue(self.best_estimate * factor, self.minimum * factor, self.maximum * factor)

    def multiply_band(self, other: "UncertainValue") -> "UncertainValue":
        """Return the slot-wise product of two coherent cost-type bands, e.g. maintenance rate times investment.

        The same world drives both operands, so the HIGH-world product is the higher rate times the higher investment;
        this is not interval multiplication.

        Raises:
            ValueError: If either band is negative in any slot, since the product's ordering would then not hold.
        """
        if self.minimum < 0 or other.minimum < 0:
            raise ValueError("multiply_band() requires non-negative bands in every slot.")
        return UncertainValue(
            best_estimate=self.best_estimate * other.best_estimate,
            minimum=self.minimum * other.minimum,
            maximum=self.maximum * other.maximum,
        )

    def clamp_upper(self, cap: "UncertainValue") -> "UncertainValue":
        """Cap each slot separately (§3.9, §5.4).

        A cap such as an eligible-cost ceiling may bind in the HIGH world and not in LOW; the subsidy audit and the
        input audit report which slots it bound in. Used by the subsidy engine and the German levy rules (`actors.py`).
        """
        return UncertainValue(
            best_estimate=min(self.best_estimate, cap.best_estimate),
            minimum=min(self.minimum, cap.minimum),
            maximum=min(self.maximum, cap.maximum),
        )

    @staticmethod
    def sum(values: Iterable["UncertainValue"]) -> "UncertainValue":
        """Return the slot-wise sum; an empty input yields the exact zero band.

        Folds left in iteration order, so totals are reproducible bit for bit and match the order the audit and
        reconciliation checks (§7.4) use.
        """
        total = UncertainValue.ZERO
        for value in values:
            total = total + value
        return total


UncertainValue.ZERO = UncertainValue.exact(0.0)
