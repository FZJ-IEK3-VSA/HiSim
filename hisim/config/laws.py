"""Sizing laws: how a config field's value derives from the surrounding system.

This module holds the *law* half of the declarative sizing design: the
:class:`SizingLaw` base class, the expression-tree terms built by operator overloading
(so a linear law reads like the formula it is, describes itself in error messages and
names what it reads), the sibling term :func:`Self`, the cardinality hook :func:`Many`,
the wrapper for genuinely computational function laws, and the :func:`law` normalizer
that turns any of the three allowed spellings — expression, function, constant — into a
law object. The sizing errors live here too, at the bottom of the package's import
chain, because the laws and everything above them raise them.

What laws *read* — the :class:`~hisim.config.context.SizingContext` and its ``Size.*``
term vocabulary — lives next door in :mod:`hisim.config.context`; how fields *declare*
laws and how configs *resolve* them lives in :mod:`hisim.config.sizing`. Per the
``hisim.config`` layering rule this module imports nothing from the rest of HiSim.
"""

# clean

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from hisim.config.context import SizingContext


class SizingError(Exception):
    """Base class of every error the sizing machinery raises.

    Split from ``ValueError`` so callers (tests, the future v2 executor) can catch sizing
    problems precisely without swallowing unrelated failures.
    """


class ConfigSizingError(SizingError):
    """A config still requires sizing, or a law could not be evaluated.

    Raised centrally by ``Component.__init__`` when a config carrying ``AUTO`` reaches a
    component (the "forgot to size" case), and by ``resolve_config`` when a law needs an
    absent context fact, names a sibling field that does not exist, or sits in a cycle of
    sibling reads. The fact engine raises it for every binding failure as well.
    """


class NothingToSizeError(SizingError):
    """``resolve`` was called on a config class that declares no sizable field at all.

    Passing a sizing context to a component that can never use one is a setup bug — the
    author believed something got sized that never could be — so it fails loudly instead
    of doing nothing. A class whose sizable fields are all currently concrete is the
    legitimate no-op case and does not raise.
    """


class Cardinality(enum.Enum):
    """How many providers of a fact one law input expects: exactly one, or all of them.

    Every fact a law reads carries a cardinality so that the fact engine knows whether a
    bare fact name must resolve to a single provider (``ONE``, what every term produces
    unless the author wraps it in :func:`Many`) or collects every declared provider into
    a tuple (``MANY``, declared-but-unimplemented: it raises on evaluation, so a law can
    be written for the multi-provider case while the aggregation is still open).
    """

    ONE = "one"
    MANY = "many"


class OwnFieldsView:
    """Read-only access to the *other* fields of the config a law is being evaluated for.

    A sibling term (:func:`Self`) needs the final value of another field of the same
    config — pinned by the author or already computed by its own law — and asks for it
    here. The implementation lives in :mod:`hisim.config.sizing` next to the resolver
    that fills it; the base class lives here so the law algebra can type its second
    evaluation argument without importing the layer above it.
    """

    def value_of(self, field_name: str) -> Any:
        """Returns the final value of the named field of the config under resolution.

        Raises:
            ConfigSizingError: If the field does not exist on the config, or if it is a
                sizable field whose own law has not been evaluated yet.
        """
        raise NotImplementedError


class SizingLaw:
    """Base class of every sizing law: something that computes a value from the context.

    Laws are attached to fields by ``sized_field`` (under :attr:`METADATA_KEY`) and
    evaluated by ``resolve_config``, both in :mod:`hisim.config.sizing`. The expression
    subclasses build small trees via operator overloading so a linear law reads like the
    formula it is, describes itself in error messages, and names both the context facts
    and the sibling fields it reads. Computational laws are plain functions wrapped in
    :class:`_FunctionLaw`.
    """

    #: Field-metadata key under which ``sized_field`` stores the law.
    METADATA_KEY: ClassVar[str] = "hisim_sizing_law"

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Computes the law's value from the context facts and the config's own fields.

        The ``own`` view is supplied by ``resolve_config`` whenever a config is being
        resolved and stays ``None`` for a standalone evaluation, in which case a sibling
        term fails precisely instead of silently reading nothing.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """Renders the law as a short human-readable formula for errors and the audit."""
        raise NotImplementedError

    def facts_read(self) -> Tuple[Tuple[str, Cardinality], ...]:
        """Names the context facts this law reads, each with its cardinality.

        Empty when the law reads no fact at all (a constant law) or when the reads are
        not statically knowable — which is why a function law must declare them.
        """
        return ()

    def fields_read(self) -> Tuple[str, ...]:
        """Names the sibling fields of the same config this law reads, in read order.

        Empty for every law that only reads context facts. ``resolve_config`` uses these
        names to order a config's sizable fields so that a sibling is always final by the
        time it is read.
        """
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

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Reads the fact, raising precisely when the context does not carry it."""
        del own
        value = getattr(ctx, self.field_name)
        if value is None:
            raise ConfigSizingError(
                f"the SizingContext carries no '{self.field_name}', which this law reads"
            )
        return value

    def describe(self) -> str:
        """Renders as the Size-registry spelling."""
        return f"Size.{self.field_name.upper()}"

    def facts_read(self) -> Tuple[Tuple[str, Cardinality], ...]:
        """Names exactly the one fact this term reads, at cardinality one."""
        return ((self.field_name, Cardinality.ONE),)


class _UnaryLaw(SizingLaw):
    """Base class of every law that wraps exactly one other law and post-processes it.

    Scaling, clamping, rounding and the many-cardinality hook all have the same shape:
    one inner law plus a little extra behaviour. Collecting the wrapping here forwards a
    composite law's reads — context facts and sibling fields — in exactly one place, so a
    new operator cannot forget them and silently drop an edge out of the graph.
    """

    def __init__(self, inner: SizingLaw) -> None:
        """Stores the wrapped law."""
        self.inner = inner

    def facts_read(self) -> Tuple[Tuple[str, Cardinality], ...]:
        """Delegates to the wrapped law."""
        return self.inner.facts_read()

    def fields_read(self) -> Tuple[str, ...]:
        """Delegates to the wrapped law."""
        return self.inner.fields_read()


class _ManyTerm(_UnaryLaw):
    """A declared-but-unimplemented law input reading *every* provider of one fact.

    The term exists so that a law for the multi-provider case (several PV arrays feeding
    one battery) can already be written and introspected, and so the fact engine can tell
    a many-read from a one-read when it reports an ambiguity. Evaluating it raises: the
    aggregation is deliberately undecided, and summing or picking the first value would
    be exactly the guessing the binding rule forbids.
    """

    #: The message the term raises when a resolution actually reaches it.
    NOT_IMPLEMENTED_MESSAGE: ClassVar[str] = (
        "many-cardinality is declared but not implemented; see plan parking lot"
    )

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Always raises: the many-cardinality aggregation is not implemented."""
        del ctx, own
        raise NotImplementedError(self.NOT_IMPLEMENTED_MESSAGE)

    def describe(self) -> str:
        """Renders as ``Many(inner)``."""
        return f"Many({self.inner.describe()})"

    def facts_read(self) -> Tuple[Tuple[str, Cardinality], ...]:
        """Re-labels the wrapped term's facts as many-cardinality reads."""
        return tuple((fact, Cardinality.MANY) for fact, _ in self.inner.facts_read())


class _SelfTerm(SizingLaw):
    """A law that reads the final value of another field of the *same* config.

    Written as ``Self("maximal_thermal_power_in_watt")``, it lets a field derive from a
    sibling — a pellet boiler's minimal power is a twelfth of its own maximal power — and
    always sees the sibling's *final* value, pinned or sized. ``resolve_config`` orders a
    config's sizable fields by these reads, so the sibling is final by evaluation time; a
    cycle of such reads is a hard error naming the fields involved.
    """

    def __init__(self, field_name: str) -> None:
        """Stores the name of the sibling field this term reads."""
        self.field_name = field_name

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Reads the sibling's final value through the resolver's own-fields view."""
        del ctx
        if own is None:
            raise ConfigSizingError(
                f"the law reads the sibling field '{self.field_name}', which is only "
                "available while a config is being resolved; use resolve_config."
            )
        return own.value_of(self.field_name)

    def describe(self) -> str:
        """Renders as the ``Self("field")`` spelling the author wrote."""
        return f'Self("{self.field_name}")'

    def fields_read(self) -> Tuple[str, ...]:
        """Names exactly the one sibling field this term reads."""
        return (self.field_name,)


class _ScaledLaw(_UnaryLaw):
    """A law multiplied by a constant factor."""

    def __init__(self, inner: SizingLaw, factor: float) -> None:
        """Stores the wrapped law and the factor."""
        super().__init__(inner)
        self.factor = factor

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Evaluates the inner law and scales the result."""
        return self.inner.evaluate(ctx, own) * self.factor

    def describe(self) -> str:
        """Renders as ``factor * inner``."""
        return f"{self.factor} * {self.inner.describe()}"


class _ClampedLaw(_UnaryLaw):
    """A law clamped from below and/or above."""

    def __init__(self, inner: SizingLaw, minimum: Optional[float] = None, maximum: Optional[float] = None) -> None:
        """Stores the wrapped law and the bounds."""
        super().__init__(inner)
        self.minimum = minimum
        self.maximum = maximum

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Evaluates the inner law and applies the bounds."""
        value = self.inner.evaluate(ctx, own)
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


class _RoundedLaw(_UnaryLaw):
    """A law whose value is rounded to a fixed number of digits."""

    def __init__(self, inner: SizingLaw, digits: int) -> None:
        """Stores the wrapped law and the digit count."""
        super().__init__(inner)
        self.digits = digits

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Evaluates the inner law and rounds the result."""
        return round(self.inner.evaluate(ctx, own), self.digits)

    def describe(self) -> str:
        """Renders as a ``.rounded(n)`` suffix."""
        return f"{self.inner.describe()}.rounded({self.digits})"


class _ConstantLaw(SizingLaw):
    """A law whose sized value is a plain constant (``rule=0.0``).

    Several real scaled factories set a field to a fixed value in the sized variant (the
    scaled boiler's minimal power is 0.0); a constant law expresses that directly instead
    of contorting it into a zero-factor expression.
    """

    def __init__(self, value: Any) -> None:
        """Stores the constant."""
        self.value = value

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Returns the constant; context and own fields are deliberately ignored."""
        del ctx, own
        return self.value

    def describe(self) -> str:
        """Renders the constant itself."""
        return repr(self.value)


class _FunctionLaw(SizingLaw):
    """A genuinely computational law: a plain function of the context and, optionally, siblings.

    Functions cannot be introspected, so the facts they read must be **declared**:
    without the declaration the dependency graph would be a guess, degrading the engine's
    precise up-front errors into "no progress" mysteries. Enforcement happens at
    declaration time in :func:`law`, so a forgetful author fails on import. A function
    that also reads sibling fields declares them as ``fields=(...)`` and is then called
    as ``fn(ctx, own)``; one declaring no fields keeps the one-argument ``fn(ctx)``
    protocol, so the declaration — not the signature — decides how it is invoked.
    """

    def __init__(
        self,
        function: Callable[..., Any],
        reads: Tuple[Tuple[str, Cardinality], ...],
        fields: Tuple[str, ...] = (),
    ) -> None:
        """Stores the callable, its declared context facts and its declared sibling fields."""
        self.function = function
        self.reads = reads
        self.fields = fields

    def evaluate(self, ctx: "SizingContext", own: Optional[OwnFieldsView] = None) -> Any:
        """Calls the function, passing the own-fields view only if sibling reads were declared."""
        if not self.fields:
            return self.function(ctx)
        if own is None:
            raise ConfigSizingError(
                f"the law reads the sibling field(s) {list(self.fields)}, which are only "
                "available while a config is being resolved; use resolve_config."
            )
        return self.function(ctx, own)

    def describe(self) -> str:
        """Renders the function by its qualified name where available."""
        return getattr(self.function, "__qualname__", repr(self.function))

    def facts_read(self) -> Tuple[Tuple[str, Cardinality], ...]:
        """Returns the declared facts, which registration uses as graph edges."""
        return self.reads

    def fields_read(self) -> Tuple[str, ...]:
        """Returns the declared sibling fields, which order a config's resolution."""
        return self.fields


def Self(field_name: str) -> SizingLaw:  # noqa: N802  # pylint: disable=invalid-name
    """Builds a term reading the final value of the named sibling field of the same config.

    Written in title case because it reads as a keyword in a formula —
    ``Self("maximal_thermal_power_in_watt") * (1 / 12)`` — matching the ``Size.*`` terms
    it sits next to. The field name is a plain string because sibling vocabularies are
    per class and cannot live in a central registry; ``resolve_config`` checks every name
    against the class's dataclass fields, so a typo fails naming the known fields.
    """
    return _SelfTerm(field_name)


def Many(term: SizingLaw) -> SizingLaw:  # noqa: N802  # pylint: disable=invalid-name
    """Wraps a fact term so the law declares it as a read of *every* provider of that fact.

    The result is accepted everywhere a term is — in an expression, in a function law's
    ``reads`` — and reports its facts at :attr:`Cardinality.MANY`, which makes the engine
    demand a list rather than a single reference when the binding is ambiguous.
    Evaluating it raises: the term is a declaration hook, not a working aggregation.
    """
    return _ManyTerm(term)


def _read_pairs(reads: Tuple[Any, ...]) -> Tuple[Tuple[str, Cardinality], ...]:
    """Turns a declared ``reads`` tuple into ``(fact, cardinality)`` pairs.

    Entries may be ``Size.*`` terms, :func:`Many`-wrapped terms, or plain fact-name
    strings (meaning cardinality one). Keeping the coercion in one place lets
    ``sized_field``, :func:`law` and the introspection helpers agree on what a law reads.
    """
    pairs: List[Tuple[str, Cardinality]] = []
    for entry in reads:
        if isinstance(entry, SizingLaw):
            pairs.extend(entry.facts_read())
        else:
            pairs.append((str(entry), Cardinality.ONE))
    return tuple(pairs)


def _reject_mixed_cardinality(pairs: Tuple[Tuple[str, Cardinality], ...], described: str) -> None:
    """Rejects a law that reads the same fact both as one provider and as many.

    Such a law is contradictory: the engine would have to bind the fact to one provider
    and to the whole provider set at once. The check runs at declaration time so the
    contradiction never reaches a resolution.
    """
    seen: Dict[str, Cardinality] = {}
    for fact, cardinality in pairs:
        previous = seen.setdefault(fact, cardinality)
        if previous is not cardinality:
            raise SizingError(
                f"the law {described} reads '{fact}' both as one provider and as many; "
                "a fact has one cardinality per law."
            )


def normalize_law(
    rule: Any, reads: Optional[Tuple[Any, ...]] = None, fields: Optional[Tuple[str, ...]] = None
) -> SizingLaw:
    """Turns whatever ``sized_field(rule=...)`` or :func:`law` received into a law object.

    Accepts a ready law, a callable (which **must** declare its reads), or any other
    value (wrapped as a constant law). The ``reads`` entries may be ``Size.*`` terms,
    :func:`Many`-wrapped terms or plain fact names; ``fields`` names sibling fields and
    switches the callable to the two-argument ``fn(ctx, own)`` protocol. The result is
    checked for the one-and-many contradiction before it is returned.
    """
    if isinstance(rule, SizingLaw):
        _reject_mixed_cardinality(rule.facts_read(), rule.describe())
        return rule
    if callable(rule):
        if reads is None:
            raise SizingError(
                "a function law must declare the facts it reads, e.g. "
                "law(fn, reads=(Size.HEATING_LOAD_IN_WATT, ...)) - expression laws "
                "derive theirs automatically; without the declaration the sizing "
                "dependency graph is a guess."
            )
        pairs = _read_pairs(reads)
        _reject_mixed_cardinality(pairs, getattr(rule, "__qualname__", repr(rule)))
        return _FunctionLaw(rule, pairs, tuple(fields or ()))
    return _ConstantLaw(rule)


def law(
    rule: Any, reads: Optional[Tuple[Any, ...]] = None, fields: Optional[Tuple[str, ...]] = None
) -> SizingLaw:
    """Public spelling of law normalization, for laws declared outside ``sized_field``.

    Config classes use this to name a law once as a ``ClassVar`` and reuse it — in the
    field declaration and in presets that override the class law for one field (a preset
    may assign a ``SizingLaw`` as a field *value*, which the resolver treats like AUTO
    but computes with that law). For callables ``reads`` is mandatory (Size terms,
    ``Many`` terms or plain fact names) and ``fields`` optionally names sibling fields
    the callable then receives as a second argument; see :class:`_FunctionLaw`.
    """
    return normalize_law(rule, reads, fields)
