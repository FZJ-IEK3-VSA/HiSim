"""The parameter samples an assembly's declarations imply (``assemblies_spec.md`` §9.4, D24, lean v1 §13.1).

**Deterministic samples** (the PR tier): the defaults, the ``min`` and the ``max`` of every numeric
parameter's ``range`` and every allowed value of every parameter with a closed set (an enum's
``values``, both booleans), each with the other parameters at their defaults, and every option of
every internal variant. A value that states a parameter of an ``exactly_one_of`` moves the sample
into that parameter's branch: the other parameters of the constraint become unstated (their
default when it is unstated, else ``none``, or ``false`` for a boolean); a value that leaves the
constraint without a stated parameter states the next one that can be.

**The Latin hypercube sample** (the nightly tier): ``scipy.stats.qmc.LatinHypercube`` over the
parameter box, scrambled and seeded. Every assignment of one stated parameter per
``exactly_one_of`` that the constraints admit is a **branch** with a hypercube of its own; in a
branch an unstated parameter is fixed, and every other one is a dimension: a numeric parameter over
its ``range``, a boolean or an enum a stratified discrete dimension whose bins are cut over the
hypercube's strata, so with ``N`` samples each value is drawn ``floor(N/m)`` or ``ceil(N/m)`` times.
An internal variant is selected by a boolean or an enum, so its selector's dimension is the
variant's. Branch ``i`` draws from ``numpy.random.default_rng((seed, i))``; no sample is thrown away,
but two points of a branch whose dimensions are all discrete can draw the same values, which are one
sample, so ``N`` is an upper bound there. The constraints are disjoint (the library check refuses a
parameter in two), so every choice of one parameter per constraint is a branch.

**Sweeps** (``tests.monotone``): from a base sample one numeric parameter moves across its range in
:data:`MONOTONE_STEPS` equidistant values, everything else fixed; an int parameter takes the nearest
integer of each, so it sweeps up to that many distinct values. A base whose branch keeps the
parameter unstated admits no sweep of it, since moving it would change two things at once. Two bases
that differ only in the swept parameter reach the same points, which are one sweep.

**Int parameters.** Every value the sampler gives an int parameter — a boundary, a representative,
a hypercube coordinate, a sweep point — goes through :func:`range_value`: the nearest integer, half
rounding up, clamped into the range, whose bounds the library check holds to integers.

Every sample is checked against every declaration and constraint as it is made; one that breaks
one is a bug of the harness (:class:`SamplerError`).
"""

from __future__ import annotations

import enum
import itertools
import json
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import qmc

from hisim.energy_system.assemblies.model import AssemblyFile, MonotoneDeclaration, ParameterType
from hisim.energy_system.assemblies.parameters import ParameterChecks

#: The values a monotone sweep moves its parameter through.
MONOTONE_STEPS = 4


class SamplerError(Exception):
    """A sample the harness built breaks a declaration or a constraint of its assembly: a bug of the harness."""


class Tier(enum.Enum):
    """The tier a sample runs in: the deterministic samples in the PR gate, the hypercube nightly."""

    BASE = "base"
    NIGHTLY = "nightly"


def range_value(value: float, low: float, high: float, integer: bool) -> Any:
    """A point of a numeric range ``[low, high]``, clamped into it; for an int parameter the nearest integer.

    Half rounds up. The one conversion every boundary, representative, hypercube point and sweep point
    of a numeric parameter takes, so an int parameter's value never leaves its (integer) range.
    """
    if integer:
        return min(int(high), max(int(low), math.floor(value + 0.5)))
    return float(min(high, max(low, value)))


@dataclass
class Sample:
    """One complete parameter set of one assembly.

    Attributes:
        sample_id: ``s000`` … for the deterministic samples, ``h000`` … for the hypercube, in the order made.
        values: Every declared parameter's value, in declaration order.
        tier: The tier it runs in, set by the sampler that drew it.
        origins: Every reason the sample exists (``defaults``, ``max of power_in_watt``,
            ``hypercube power_in_watt stated #3``); a parameter set reached twice is one sample.
    """

    sample_id: str
    values: Dict[str, Any]
    tier: Tier
    origins: List[str] = field(default_factory=list)

    @property
    def nightly(self) -> bool:
        """Whether the sample belongs to the nightly tier (the hypercube)."""
        return self.tier == Tier.NIGHTLY


class ParameterSpace:
    """An assembly's parameters as a box: defaults, branches of the constraints, placement within them."""

    def __init__(self, model: AssemblyFile) -> None:
        """Prepares the space of one assembly the library check has accepted."""
        self.model = model
        self.declarations = model.parameters

    def normalised(self, values: Mapping[str, Any]) -> Dict[str, Any]:
        """The values in declaration order, a float parameter's integer written as a float."""
        result: Dict[str, Any] = {}
        for name, declaration in self.declarations.items():
            value = values[name]
            numeric = isinstance(value, int) and not isinstance(value, bool)
            result[name] = float(value) if declaration.type == ParameterType.FLOAT and numeric else value
        return result

    @staticmethod
    def key(values: Any) -> str:
        """The canonical text of a parameter set (or a list of them), by which two samples (or sweeps) are the same."""
        return json.dumps(values, sort_keys=True, default=repr)

    def defaults(self) -> Dict[str, Any]:
        """Every parameter at its default (the library check guarantees one)."""
        return self.normalised({name: declaration.default for name, declaration in self.declarations.items()})

    def unstated(self, name: str) -> Any:
        """The value leaving a parameter unstated: its default when that is unstated, else ``false`` or ``none``."""
        declaration = self.declarations[name]
        if not ParameterChecks.is_stated(declaration.default):
            return declaration.default
        return False if declaration.type == ParameterType.BOOL else None

    def representative(self, name: str) -> Optional[Any]:
        """A value stating a parameter: its default when stated, else the first one it allows; ``None`` if none."""
        declaration = self.declarations[name]
        if ParameterChecks.is_stated(declaration.default):
            return declaration.default
        if declaration.range is not None:
            low, high = declaration.range
            return range_value(low, low, high, declaration.type == ParameterType.INT)
        stated = [value for value in declaration.allowed_values or () if ParameterChecks.is_stated(value)]
        return stated[0] if stated else None

    def problems(self, values: Mapping[str, Any]) -> List[str]:
        """Every way a complete parameter set breaks a declaration or a constraint; empty when it fits."""
        found = [
            f"'{name}' is {ParameterChecks.problem(declaration, values.get(name))}"
            for name, declaration in self.declarations.items()
            if ParameterChecks.problem(declaration, values.get(name)) is not None
        ]
        for names in self.model.exactly_one_of:
            violation = ParameterChecks.violation(names, values)
            if violation is not None:
                found.append(violation)
        return found

    def place(self, base: Mapping[str, Any], name: str, value: Any) -> Dict[str, Any]:
        """The base with one parameter set, moved into the constraint branch that value implies."""
        values = dict(base)
        values[name] = value
        for names in self.model.exactly_one_of:
            if name not in names:
                continue
            others = [other for other in names if other != name]
            if ParameterChecks.is_stated(value):
                values.update({other: self.unstated(other) for other in others})
            elif not any(ParameterChecks.is_stated(values[other]) for other in others):
                other = next((other for other in others if self.representative(other) is not None), None)
                if other is not None:
                    values[other] = self.representative(other)
        return self.normalised(values)


class SampleSet:
    """The samples of one assembly and tier, each parameter set once, with every reason it was drawn."""

    #: The identifier prefix of each tier's samples.
    PREFIXES: ClassVar[Mapping[Tier, str]] = {Tier.BASE: "s", Tier.NIGHTLY: "h"}

    def __init__(self, space: ParameterSpace, tier: Tier, known: Sequence[Sample] = ()) -> None:
        """Starts an empty set; parameter sets the ``known`` samples hold are not drawn again."""
        self.space = space
        self.tier = tier
        self.samples: List[Sample] = []
        self._by_key: Dict[str, Sample] = {ParameterSpace.key(sample.values): sample for sample in known}

    def add(self, values: Mapping[str, Any], origin: str) -> None:
        """Adds a parameter set, or the origin to the sample that holds it already.

        Raises:
            SamplerError: When the values break a declaration or a constraint of the assembly.
        """
        normalised = self.space.normalised(values)
        problems = self.space.problems(normalised)
        if problems:
            raise SamplerError(
                f"the harness built the sample '{origin}' of '{self.space.model.name}', which breaks its declarations: "
                + "; ".join(problems)
                + "."
            )
        key = ParameterSpace.key(normalised)
        if key not in self._by_key:
            self._by_key[key] = Sample(f"{self.PREFIXES[self.tier]}{len(self.samples):03d}", normalised, self.tier)
            self.samples.append(self._by_key[key])
        self._by_key[key].origins.append(origin)


def deterministic_samples(space: ParameterSpace) -> List[Sample]:
    """The defaults, every range boundary, every allowed value and every variant option (§9.4)."""
    samples = SampleSet(space, Tier.BASE)
    base = space.defaults()
    samples.add(base, "defaults")
    for name, declaration in space.declarations.items():
        if declaration.range is not None:
            low, high = declaration.range
            for label, bound in zip(("min", "max"), declaration.range):
                value = range_value(bound, low, high, declaration.type == ParameterType.INT)
                samples.add(space.place(base, name, value), f"{label} of {name}")
    for name, declaration in space.declarations.items():
        for value in declaration.allowed_values or ():
            samples.add(space.place(base, name, value), f"{name} = {value!r}")
    for variant in space.model.variants.values():
        for option in variant.options.values():
            samples.add(
                space.place(base, variant.selected_by, option.when[0]), f"variant {variant.name}: {option.name}"
            )
    return samples.samples


@dataclass(frozen=True)
class Dimension:
    """One dimension of a branch's hypercube: a numeric range, or a closed set of values (``choices``)."""

    parameter: str
    range: Optional[Tuple[float, float]] = None
    integer: bool = False
    choices: Tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        """A dimension is a range with ``low <= high`` or a set of choices: exactly one of them.

        Raises:
            SamplerError: For both, neither, or a range running backwards (a bug of the harness).
        """
        if (self.range is None) == (not self.choices):
            raise SamplerError(f"the dimension '{self.parameter}' needs a range or choices, exactly one of them.")
        if self.range is not None and self.range[0] > self.range[1]:
            raise SamplerError(f"the dimension '{self.parameter}' runs from {self.range[0]} down to {self.range[1]}.")

    def value(self, unit: float, size: int) -> Any:
        """The value at a coordinate of the unit interval, for a hypercube of ``size`` points.

        A numeric dimension scales the coordinate into its range (an integer one cuts it into equal
        integer bins); a discrete one takes the coordinate's stratum ``floor(u * size)`` and cuts the
        strata into equal bins, one per value.
        """
        if self.range is None:
            stratum = min(int(math.floor(unit * size)), size - 1)
            return self.choices[stratum * len(self.choices) // size]
        low, high = self.range
        if self.integer:
            # Each of the high - low + 1 integers gets an equal share of the unit interval.
            return range_value(low - 0.5 + unit * (high - low + 1), low, high, True)
        return range_value(low + unit * (high - low), low, high, False)


@dataclass(frozen=True)
class Branch:
    """One admitted assignment of stated and unstated to the constrained parameters, and its hypercube."""

    index: int
    stated: Mapping[str, bool]
    fixed: Mapping[str, Any]
    dimensions: Tuple[Dimension, ...]

    @property
    def label(self) -> str:
        """How an origin names the branch."""
        stated = [name for name, is_stated in self.stated.items() if is_stated]
        return f"{', '.join(stated)} stated" if stated else "the whole box"


def branches(space: ParameterSpace) -> List[Branch]:
    """Every branch of the box: one stated parameter per ``exactly_one_of``, where the constraints admit it.

    Raises:
        SamplerError: When no assignment is admitted (the library check accepted the defaults, so
            this is a bug of the harness).
    """
    constraints = space.model.exactly_one_of  # disjoint: the library check refuses a parameter in two
    assignments = [
        {name: name == chosen for names, chosen in zip(constraints, choice) for name in names}
        for choice in itertools.product(*constraints)
        if all(space.representative(name) is not None for name in choice)
    ]
    if not assignments:
        raise SamplerError(f"no branch of the constraints of '{space.model.name}' can be stated.")
    result: List[Branch] = []
    for index, stated in enumerate(assignments):
        fixed: Dict[str, Any] = {}
        dimensions: List[Dimension] = []
        for name, declaration in space.declarations.items():
            choices = tuple(
                value
                for value in declaration.allowed_values or ()
                if not stated.get(name) or ParameterChecks.is_stated(value)
            )
            if stated.get(name) is False:
                fixed[name] = space.unstated(name)
            elif declaration.range is not None:
                dimensions.append(Dimension(name, declaration.range, integer=declaration.type == ParameterType.INT))
            elif len(choices) > 1:
                dimensions.append(Dimension(name, choices=choices))
            else:
                fixed[name] = choices[0] if choices else declaration.default
        result.append(Branch(index, stated, fixed, tuple(dimensions)))
    return result


def hypercube_samples(space: ParameterSpace, size: int, seed: int, known: Sequence[Sample] = ()) -> List[Sample]:
    """The seeded Latin hypercube sample, ``size`` points per branch; sets the ``known`` samples hold are skipped.

    ``size`` is an upper bound for a branch whose dimensions are all discrete (enums, booleans, int
    parameters): two points drawing the same values are one sample.

    Raises:
        SamplerError: For a size below one, or a drawn sample that breaks a constraint.
    """
    if size < 1:
        raise SamplerError(f"the hypercube sample size is {size}; it must be at least 1.")
    samples = SampleSet(space, Tier.NIGHTLY, known)
    for branch in branches(space):
        points = np.zeros((1, 0))
        if branch.dimensions:
            generator = np.random.default_rng((seed, branch.index))
            points = qmc.LatinHypercube(d=len(branch.dimensions), scramble=True, seed=generator).random(size)
        for number, point in enumerate(points):
            values = dict(branch.fixed)
            values.update(
                {
                    dimension.parameter: dimension.value(float(unit), size)
                    for dimension, unit in zip(branch.dimensions, point)
                }
            )
            samples.add(values, f"hypercube {branch.label} #{number}")
    return samples.samples


def sweep(
    space: ParameterSpace, base: Sample, parameter: str, steps: int = MONOTONE_STEPS
) -> Optional[List[Dict[str, Any]]]:
    """The parameter sets of one monotone sweep from one base, rising; ``None`` when the base's branch admits none.

    Raises:
        SamplerError: When a swept point breaks a declaration (a bug of the harness).
    """
    declaration = space.declarations[parameter]
    constrained = any(parameter in names for names in space.model.exactly_one_of)
    if declaration.range is None or (constrained and not ParameterChecks.is_stated(base.values[parameter])):
        return None
    low, high = declaration.range
    integer = declaration.type == ParameterType.INT
    points = list(
        dict.fromkeys(range_value(float(point), low, high, integer) for point in np.linspace(low, high, steps))
    )
    swept = [space.normalised({**base.values, parameter: point}) for point in points]
    for values in swept:
        problems = space.problems(values)
        if problems:
            raise SamplerError(
                f"the sweep of '{parameter}' from {base.sample_id} of '{space.model.name}' breaks its declarations: "
                + "; ".join(problems)
            )
    return swept


def sweeps(
    space: ParameterSpace, bases: Sequence[Sample], declaration: MonotoneDeclaration
) -> List[Tuple[Sample, List[Dict[str, Any]]]]:
    """Every distinct sweep of one monotone declaration from the bases that admit one, with its first base.

    Two bases that differ only in the swept parameter reach the same sweep, which is evaluated once.
    """
    found: Dict[str, Tuple[Sample, List[Dict[str, Any]]]] = {}
    for base in bases:
        points = sweep(space, base, declaration.parameter)
        if points is not None:
            found.setdefault(ParameterSpace.key(points), (base, points))
    return list(found.values())
