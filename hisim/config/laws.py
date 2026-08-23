"""Sizing laws: how a config field's value derives from the surrounding system.

This module holds the *law* half of the declarative sizing design: the
:class:`SizingLaw` base class, the expression-tree terms built by operator
overloading (so a linear law reads like the formula it is, can describe itself in error
messages, and can name the context facts it reads), the wrapper for genuinely
computational function laws, and the :func:`law` normalizer that turns any of the three
allowed spellings — expression, function, constant — into a law object. The sizing
errors live here too, at the bottom of the package's import chain, because both the
laws and everything above them (the field machinery in :mod:`hisim.config.sizing`, the
fact engine in :mod:`hisim.config.engine`) raise them.

What laws *read* — the :class:`~hisim.config.context.SizingContext` and its ``Size.*``
term vocabulary — lives next door in :mod:`hisim.config.context`; how fields *declare*
laws (``sized_field``) and how configs *resolve* them lives in
:mod:`hisim.config.sizing`. Per the ``hisim.config`` layering rule this module imports
nothing from the rest of HiSim.
"""

# clean

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional, Tuple

if TYPE_CHECKING:
    from hisim.config.context import SizingContext


class SizingError(Exception):
    """Base class of every error the sizing machinery raises.

    Split from ``ValueError`` so that callers (tests, the future v2 executor) can catch
    sizing problems precisely without accidentally swallowing unrelated failures.
    """


class ConfigSizingError(SizingError):
    """A config still requires sizing, or a law could not be evaluated.

    Raised centrally by ``Component.__init__`` when a config carrying ``AUTO`` reaches a
    component (the "forgot to size" case), and by ``resolve_config`` when a law needs a
    context fact that the given :class:`~hisim.config.context.SizingContext` does not
    carry.
    """


class NothingToSizeError(SizingError):
    """``resolve`` was called on a config class that declares no sizable field at all.

    Passing a sizing context to a component that can never use one is a setup bug — the
    author believed something got sized that never could be — so it fails loudly instead
    of silently doing nothing. A class that *has* sizable fields which are
    all currently concrete is the legitimate no-op case and does not raise.
    """


class SizingLaw:
    """Base class of every sizing law: something that computes a value from the context.

    Laws are attached to fields by ``sized_field`` (under :attr:`METADATA_KEY`) and
    evaluated by ``resolve_config``, both in :mod:`hisim.config.sizing`. The expression
    subclasses build small trees via operator overloading so that a linear law reads
    like the formula it is, can be described in error messages, and can name the context
    facts it reads. Genuinely computational laws are plain functions wrapped in
    :class:`_FunctionLaw`.
    """

    #: Field-metadata key under which ``sized_field`` stores the law.
    METADATA_KEY: ClassVar[str] = "hisim_sizing_law"

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Computes the law's value against the given context."""
        raise NotImplementedError

    def describe(self) -> str:
        """Renders the law as a short human-readable formula for errors and the audit."""
        raise NotImplementedError

    def facts_read(self) -> Tuple[str, ...]:
        """Names the context fields this law reads; empty when not statically knowable."""
        return ()

    def __mul__(self, factor: float) -> "SizingLaw":
        """Scales the law by a constant factor, e.g. ``Size.HEATING_LOAD_IN_WATT * 1.1``."""
        return _ScaledLaw(self, factor)

    def __rmul__(self, factor: float) -> "SizingLaw":
        """Scales the law by a constant factor written on the left, ``1.1 * Size...``."""
        return _ScaledLaw(self, factor)

    def at_least(self, minimum: float) -> "SizingLaw":
        """Clamps the law's value from below."""
        return _ClampedLaw(self, minimum=minimum)

    def at_most(self, maximum: float) -> "SizingLaw":
        """Clamps the law's value from above."""
        return _ClampedLaw(self, maximum=maximum)

    def rounded(self, digits: int) -> "SizingLaw":
        """Rounds the law's value to the given number of digits."""
        return _RoundedLaw(self, digits)


class _FactTerm(SizingLaw):
    """A law that passes one context fact through unchanged.

    These are the leaves of every expression law. The ``Size`` registry in
    :mod:`hisim.config.context` holds one per ``SizingContext`` field, so the vocabulary
    of terms and the vocabulary of facts are the same thing by construction and cannot
    drift apart.
    """

    def __init__(self, field_name: str) -> None:
        """Stores the context field this term reads."""
        self.field_name = field_name

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Reads the fact, raising precisely when the context does not carry it."""
        value = getattr(ctx, self.field_name)
        if value is None:
            raise ConfigSizingError(
                f"the SizingContext carries no '{self.field_name}', which this law reads"
            )
        return value

    def describe(self) -> str:
        """Renders as the Size-registry spelling."""
        return f"Size.{self.field_name.upper()}"

    def facts_read(self) -> Tuple[str, ...]:
        """Names exactly the one fact this term reads."""
        return (self.field_name,)


class _ScaledLaw(SizingLaw):
    """A law multiplied by a constant factor."""

    def __init__(self, inner: SizingLaw, factor: float) -> None:
        """Stores the wrapped law and the factor."""
        self.inner = inner
        self.factor = factor

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Evaluates the inner law and scales the result."""
        return self.inner.evaluate(ctx) * self.factor

    def describe(self) -> str:
        """Renders as ``factor * inner``."""
        return f"{self.factor} * {self.inner.describe()}"

    def facts_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()


class _ClampedLaw(SizingLaw):
    """A law clamped from below and/or above."""

    def __init__(self, inner: SizingLaw, minimum: Optional[float] = None, maximum: Optional[float] = None) -> None:
        """Stores the wrapped law and the bounds."""
        self.inner = inner
        self.minimum = minimum
        self.maximum = maximum

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Evaluates the inner law and applies the bounds."""
        value = self.inner.evaluate(ctx)
        if self.minimum is not None:
            value = max(value, self.minimum)
        if self.maximum is not None:
            value = min(value, self.maximum)
        return value

    def describe(self) -> str:
        """Renders the bounds as ``.at_least``/``.at_most`` suffixes."""
        rendered = self.inner.describe()
        if self.minimum is not None:
            rendered += f".at_least({self.minimum})"
        if self.maximum is not None:
            rendered += f".at_most({self.maximum})"
        return rendered

    def facts_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()


class _RoundedLaw(SizingLaw):
    """A law whose value is rounded to a fixed number of digits."""

    def __init__(self, inner: SizingLaw, digits: int) -> None:
        """Stores the wrapped law and the digit count."""
        self.inner = inner
        self.digits = digits

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Evaluates the inner law and rounds the result."""
        return round(self.inner.evaluate(ctx), self.digits)

    def describe(self) -> str:
        """Renders as a ``.rounded(n)`` suffix."""
        return f"{self.inner.describe()}.rounded({self.digits})"

    def facts_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()


class _ConstantLaw(SizingLaw):
    """A law whose sized value is a plain constant (``rule=0.0``).

    Several real scaled factories set a field to a fixed value in the sized variant (the
    scaled boiler's minimal power is 0.0); a constant law expresses that directly instead
    of contorting it into a zero-factor expression.
    """

    def __init__(self, value: Any) -> None:
        """Stores the constant."""
        self.value = value

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Returns the constant; the context is deliberately ignored."""
        del ctx
        return self.value

    def describe(self) -> str:
        """Renders the constant itself."""
        return repr(self.value)


class _FunctionLaw(SizingLaw):
    """A genuinely computational law: a plain function of the context.

    Functions cannot be introspected, so the facts they read must be **declared**:
    without the declaration the sizing dependency
    graph would be a guess, silently degrading the engine's precise up-front errors
    into "no progress" mysteries. Enforcement happens at declaration time in
    :func:`law`, so a forgetful author fails on import, not three resolution waves in.
    """

    def __init__(self, function: Callable[["SizingContext"], Any], reads: Tuple[str, ...]) -> None:
        """Stores the callable and its declared context facts."""
        self.function = function
        self.reads = reads

    def evaluate(self, ctx: "SizingContext") -> Any:
        """Calls the function on the context."""
        return self.function(ctx)

    def describe(self) -> str:
        """Renders the function by its qualified name where available."""
        return getattr(self.function, "__qualname__", repr(self.function))

    def facts_read(self) -> Tuple[str, ...]:
        """Returns the declared facts, which registration uses as graph edges."""
        return self.reads


def normalize_law(rule: Any, reads: Optional[Tuple[Any, ...]] = None) -> SizingLaw:
    """Turns whatever ``sized_field(rule=...)`` or :func:`law` received into a law object.

    Accepts a ready law, a callable (which **must** declare its reads), or any
    other value (wrapped as a constant law) — the three spellings the spec allows. The
    ``reads`` entries may be ``Size.*`` terms or plain fact names.
    """
    if isinstance(rule, SizingLaw):
        return rule
    if callable(rule):
        if reads is None:
            raise SizingError(
                "a function law must declare the facts it reads, e.g. "
                "law(fn, reads=(Size.HEATING_LOAD_IN_WATT, ...)) - expression laws "
                "derive theirs automatically; without the declaration the sizing "
                "dependency graph is a guess."
            )
        fact_names = tuple(
            entry.field_name if isinstance(entry, _FactTerm) else str(entry) for entry in reads
        )
        return _FunctionLaw(rule, fact_names)
    return _ConstantLaw(rule)


def law(rule: Any, reads: Optional[Tuple[Any, ...]] = None) -> SizingLaw:
    """Public spelling of law normalization, for laws declared outside ``sized_field``.

    Config classes use this to name a law once as a ``ClassVar`` and reuse it — in the
    field declaration and in presets that override the class law for one field (the
    per-preset escape hatch): a preset may assign a ``SizingLaw`` as a field
    *value*, which the resolver treats like AUTO but computes with that law instead of
    the field's declared one. For callables, ``reads`` is mandatory (Size terms or
    plain fact names); see :class:`_FunctionLaw`.
    """
    return normalize_law(rule, reads)
