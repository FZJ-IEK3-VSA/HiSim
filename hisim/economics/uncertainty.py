"""Uncertainty band type for the lifecycle cost engine (cost_spec.md §3.9).

Every monetary input is a (minimum, average, maximum) triplet. The engine evaluates every
timeline in three coherent *slots* (LOW / AVERAGE / HIGH worlds); within signed cash-flow
amounts the invariant ``minimum <= average <= maximum`` still holds because revenue-type
parameters enter with their band mirrored (see :func:`UncertainValue.as_revenue`).

This module is the arithmetic kernel of the package: it is the leaf every other economics module
imports (facts, catalog entries, timeline, calculators, results, exports), and it imports nothing
from the package itself. No cost input in this domain is known to the euro — device prices vary by
installer and region, maintenance rates are rules of thumb, energy prices vary by supplier — so the
engine carries the band from input to KPI rather than estimating an error bar after the fact. Because
discounting and aggregation are linear and act slot-wise, all three valuations fall out of a single
evaluation pass at negligible cost.

**Semantics: envelope, not confidence interval.** LOW and HIGH mean "if *everything* comes in at the
favorable respectively unfavorable end" — full correlation, not independent draws. Treating the
inputs as independent random variables would produce a narrower and, given the data actually
available, unjustifiable band. Distribution sampling is a deliberate non-goal of the spec (§2) and
would be layered on the scenario hook (§4.6) rather than here.

What this module deliberately does NOT own: *which* parameters count as revenue-type. That is a
property of a cash flow's cost category and lives in `timeline.CategoryRules.REVENUE_CATEGORIES`;
this module only provides the mirroring operation such a caller needs. It also owns no rates,
horizons or discounting — those live in `parameters.py` and `timeline.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar, Iterable, Union


class Slot(str, Enum):
    """The three coherent evaluation worlds of cost_spec.md §3.9.

    LOW is the optimistic world (every cost-type parameter at its minimum, every revenue-type
    parameter at its maximum), HIGH the pessimistic mirror image, and AVERAGE the headline world
    reported everywhere as *the* number. The enum exists so that code which must name a slot —
    subsidy caps that bind in one world but not another, plausibility checks, per-slot columns in
    exports — can do so explicitly instead of passing "min"/"max" strings around.

    Note that a `Slot` is a property of the whole evaluation, not of a single value: within one slot
    every parameter is set consistently, which is what makes the LOW and HIGH totals defensible
    envelopes of a *plan* rather than an arbitrary mix of best and worst cases.
    """

    LOW = "low"
    AVERAGE = "average"
    HIGH = "high"


@dataclass(frozen=True)
class UncertainValue:
    """A monetary figure with an uncertainty band. Invariant: minimum <= average <= maximum.

    This is the universal value type of the cost engine: every price, rate-derived amount, cash-flow
    entry, NPV, KPI and export cell is one of these rather than a bare float, so uncertainty can
    never be silently dropped somewhere in the middle of the pipeline. Frozen and hashable so that
    identical values can be interned in the provenance ledger (§3.10) and used as dictionary keys.

    The three fields are the values the same quantity takes in the three worlds of `Slot`, *after*
    any revenue mirroring: `minimum` is the value in the LOW (optimistic) world, not "the smallest
    number this parameter could be". For a cost the two coincide; for a revenue they are opposites,
    which is exactly what `as_revenue` is for. Arithmetic is slot-wise throughout (`__add__`,
    `scale`, `multiply_band`, `clamp_upper`), which keeps each world internally consistent; the one
    exception is `__sub__`, which is documented separately because a difference of two bands is not
    monotone in the same way.

    Note the field order: `average` comes first, so the positional constructor reads
    ``UncertainValue(avg, min, max)``. Loaders and callers generally use keywords or `exact`.
    """

    #: The exact zero band, reused everywhere as the neutral element. Assigned right after the
    #: class body, because it is an instance of the class it hangs on.
    ZERO: ClassVar["UncertainValue"]

    average: float
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        """Validates finiteness and band ordering (with float-noise snapping).

        Enforcing the invariant at construction is what makes every downstream consumer able to read
        `minimum`/`maximum` as "LOW world"/"HIGH world" without re-checking, and it turns a mirroring
        mistake (a revenue booked without `as_revenue`) into an immediate exception rather than a
        quietly inverted band in a published report.
        """
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
        """Degenerate band for values that are actually certain (statutory amounts, contracts).

        Legally or contractually fixed figures — lump-sum grants, statutory levy caps, tax rates, an
        EEG feed-in tariff — genuinely have no band, and forcing them through the same type keeps the
        engine free of a second "certain value" code path. It is also how physical quantities from
        the simulation (kWh, peaks) and engine-internal zeros enter the arithmetic.
        """
        return UncertainValue(value, value, value)

    @staticmethod
    def from_json(value: Any, context: str = "") -> "UncertainValue":
        """Parses a JSON value: a bare number means exact, an object declares a band.

        This is the single reader behind the universal data-file value syntax of §3.1/§3.9: every
        monetary field of the cost database, tariff contracts, subsidy catalog and stored result
        files accepts either `0.30` or `{"min": .., "avg": .., "max": ..}`, so data authors never
        need a second schema for certain values. Ordering and finiteness are validated by the
        constructor, so a malformed band fails at load time rather than mid-evaluation.

        Args:
            value: A JSON scalar (int/float, but not bool) or a dict with "min", "avg" and "max".
            context: Dotted path of the field being parsed, used only to make errors locatable.

        Returns:
            The parsed band; a bare number yields min = avg = max.

        Raises:
            ValueError: If the dict misses a key, or the value is neither number nor dict.
        """
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
        """Serializes: degenerate bands as bare numbers, real bands as objects.

        The inverse of `from_json`, and the reason exported JSON stays readable: a run whose inputs
        are all certain produces plain numbers instead of thousands of identical triplets. Used by
        every export (§7.2), by the provenance ledger and by `economic_inputs.json` round-trips.
        """
        if self.minimum == self.average == self.maximum:
            return self.average
        return {"min": self.minimum, "avg": self.average, "max": self.maximum}

    def is_exact(self) -> bool:
        """True when the band is degenerate (min = avg = max).

        Reports and plausibility checks use this to decide whether showing an uncertainty band adds
        information — a whisker around a statutory lump sum is noise, not honesty.
        """
        return self.minimum == self.average == self.maximum

    def slot(self, slot: Slot) -> float:
        """Value in the given evaluation world.

        The accessor for code that iterates over the three worlds — per-slot cap checks, per-slot
        exports, the zero-sum assertions of §6.5 — so no caller has to map `Slot.LOW` onto the
        `minimum` attribute itself. Any slot other than LOW/HIGH (i.e. AVERAGE) returns `average`.
        """
        if slot == Slot.LOW:
            return self.minimum
        if slot == Slot.HIGH:
            return self.maximum
        return self.average

    def as_revenue(self) -> "UncertainValue":
        """Mirrors the band for revenue-type parameters (§3.9).

        In the optimistic LOW world a revenue comes in at its *maximum*. Returned value is the
        signed (negative) cash-flow band ordered LOW <= AVERAGE <= HIGH again.

        **Why mirroring is not optional.** Two conventions meet here. A revenue parameter is
        *stated* in "how much money arrives" terms, where the band's minimum is the stingiest
        outcome; a cash-flow entry is *signed*, cost positive and money arriving negative (§3.6).
        Slot-wise addition then only produces meaningful totals if every summand's `minimum` field
        already means "this entry's value in the LOW world". For a cost that is its cheapest value;
        for a revenue the LOW world is the one where the *most* money arrives, i.e. the most
        negative cash flow. Plain negation would put -min (the least favorable revenue) into the
        `minimum` slot, which both breaks the min <= avg <= max invariant and, worse, would silently
        sum an optimistic-cost world with a pessimistic-revenue world — the resulting "minimum" NPV
        would be no envelope of anything. Reversing the ends while negating fixes both at once.

        Callers are the places that book a negative-signed entry from a positively-stated parameter:
        feed-in remuneration and controllability discounts (`tariffs.py`), subsidy awards
        (`calculators/subsidy_application.py`), residual values (`calculators/investment.py`),
        anyway-cost credits (`calculators/context_resolution.py`), loan disbursement and repayment
        grants (`calculators/financing_application.py`) and the landlord leg of the modernization
        levy (`actors.py`). Which categories these are is fixed by role, never guessed per entry —
        see `timeline.CategoryRules.REVENUE_CATEGORIES`, and note the W3.7 warning there that band
        mirroring and entry sign are two distinct properties that do not have the same membership.
        """
        return UncertainValue(average=-self.average, minimum=-self.maximum, maximum=-self.minimum)

    def __add__(self, other: "UncertainValue") -> "UncertainValue":
        """Slot-wise addition — the workhorse of every aggregation in the package.

        Adds the two operands within each world separately, which is correct precisely because the
        slots are coherent scenarios: the LOW total is the sum of all LOW-world values, not an
        interval-arithmetic lower bound. Every NPV, pivot and per-category total is built by folding
        this operator over entries, so mirroring mistakes upstream surface as broken bands here.
        """
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

        Comparing variants within the same world is the point (§3.7): a heat-pump price band that
        appears in both variants cancels instead of inflating the delta, which is why variant
        deltas, payback and warm-rent neutrality are all computed slot-wise. The re-sorting into an
        envelope is the price of keeping the class invariant while the subtraction is not monotone;
        the caveat it embodies — an extremal delta may occur in a *mixed* world that no slot
        describes — is what the scenario axes and break-even search of §4.6 exist to explore.
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
        """Multiplies all slots by a non-negative scalar (kWh, discount factor, escalation).

        The multiplication used whenever a band meets an *exact* quantity: metered energy, a
        discount or escalation factor, a component count, an allocation share. Non-negativity is
        required rather than assumed, because a negative factor would swap the ends of the band and
        break the LOW/HIGH reading of the slots; sign flips must go through `as_revenue`, which
        reverses the ends deliberately. Note this keeps sign-carrying entries intact — scaling an
        already-negative revenue entry by a discount factor stays a revenue.

        Raises:
            ValueError: If `factor` is negative.
        """
        if factor < 0:
            raise ValueError(
                "scale() only supports non-negative factors to preserve slot ordering; "
                "use as_revenue() for sign flips."
            )
        return UncertainValue(self.average * factor, self.minimum * factor, self.maximum * factor)

    def multiply_band(self, other: "UncertainValue") -> "UncertainValue":
        """Slot-wise product of two coherent cost-type bands (e.g. maintenance rate x investment).

        Both operands must be non-negative in every slot so slot ordering is preserved.

        Multiplying band by band is only defensible when the two are *coherent* — the same world
        drives both — which is exactly the §3.9 slot model: the expensive world has both the higher
        maintenance rate and the higher investment it applies to, so their product is the
        HIGH-world maintenance cost. This is deliberately not interval multiplication, which would
        also consider cheap-rate-times-expensive-investment corners that no coherent world realizes.
        Used by `calculators/maintenance.py` (rate x gross investment) and by the coupled-cost share
        of `calculators/context_resolution.py`.

        Raises:
            ValueError: If either band dips below zero in any slot, since the ordering guarantee
                the method relies on would no longer hold.
        """
        if self.minimum < 0 or other.minimum < 0:
            raise ValueError("multiply_band() requires non-negative bands in every slot.")
        return UncertainValue(
            average=self.average * other.average,
            minimum=self.minimum * other.minimum,
            maximum=self.maximum * other.maximum,
        )

    def clamp_upper(self, cap: "UncertainValue") -> "UncertainValue":
        """Applies a cap per slot (subsidy caps are checked per slot; §3.9, §5.4).

        Caps are the main nonlinearity in the engine — an eligible-cost ceiling or a modernization
        levy limit may bind in the HIGH world and not in LOW — and applying them within each world
        is what keeps that behavior visible instead of averaging it away. The per-slot outcome is
        intended and is reported: the subsidy audit records which slots a cap bound in, and the
        input audit surfaces it per scheme (`input_audit.ResolvedInputRow.caps_binding_by_scheme`).
        Callers are the subsidy engine (`subsidies.py`) and the German levy rules (`actors.py`).
        """
        return UncertainValue(
            average=min(self.average, cap.average),
            minimum=min(self.minimum, cap.minimum),
            maximum=min(self.maximum, cap.maximum),
        )

    @staticmethod
    def sum(values: Iterable["UncertainValue"]) -> "UncertainValue":
        """Slot-wise sum; empty input yields the exact zero band.

        The band-aware counterpart of the builtin `sum`, which cannot be used because it would start
        the fold at the integer 0. Folding left in iteration order is kept deliberately, so a
        reported total is reproducible bit for bit across runs and matches the order the audit and
        reconciliation checks (§7.4) walk the same entries in.
        """
        total = UncertainValue.ZERO
        for value in values:
            total = total + value
        return total


UncertainValue.ZERO = UncertainValue.exact(0.0)
