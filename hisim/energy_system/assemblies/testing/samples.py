"""The parameter samples an assembly's declarations imply (``assemblies_spec.md`` §9.4, D24).

**Deterministic samples** (both tiers): the defaults, every preset, every ``range`` boundary (the
``min`` and the ``max`` of each numeric parameter, the others at their defaults), every allowed
value of each parameter with a closed set of values (an ``enum``'s ``values``, both booleans), and
every option of every internal variant (its selector set to the first value its ``when:`` lists).
A boundary or a value that would state a parameter an ``exactly_one_of``/``at_most_one_of`` keeps
unstated at the defaults moves the sample into that parameter's branch: the other parameters of
the constraint become unstated (``none``, or ``false`` for a boolean). A sample the declarations
still do not admit — a ``requires`` whose dependency has no stated default — is refused by name
(:class:`~.errors.SampleConstructionError`), never dropped.

**The Latin hypercube sample** (the nightly tier): ``scipy.stats.qmc.LatinHypercube`` over the
parameter box, scrambled, seeded. A constraint splits the box into **branches**, one hypercube
each: every parameter a constraint names is either stated or unstated, and every assignment of
those states that satisfies all constraints is a branch — ``exactly_one_of: [a, b]`` gives the
branches "a stated, b unstated" and "a unstated, b stated", ``at_most_one_of`` adds "both
unstated", and ``requires: {a: [b]}`` removes the corner "a stated, b unstated". In a branch an
unstated parameter is fixed, and every other parameter is a dimension: a numeric one over its
``range``, scaled from the unit interval; a boolean, an enum or any parameter with a closed set of
values a **stratified discrete** dimension, the unit interval cut into equal bins, one per value.
The bins are cut over the hypercube's strata rather than the raw coordinate, so with ``N``
samples each value is drawn ``floor(N/m)`` or ``ceil(N/m)`` times. An internal variant is selected
by a parameter with a closed set of values, so its selector's dimension is the variant's. Each
branch draws ``size`` samples from its own generator, seeded by ``(seed, branch index)``; no
sample is thrown away, and every sample is checked against every constraint — one that violates
one is a bug of the sampler (:class:`~.errors.SamplerError`).

**Sweeps** (``tests.monotone``): from a base sample, one parameter moves across its range in
``steps`` equidistant values, everything else fixed. A base whose constraint branch leaves the
parameter unstated admits no sweep of it — moving it would change two things at once — and the
report says so for that base.
"""

from __future__ import annotations

import enum
import itertools
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import qmc

from hisim.energy_system.assemblies.model import AssemblyFile, ParameterDeclaration, ParameterType
from hisim.energy_system.assemblies.parameters import ParameterChecks, ParameterResolver
from hisim.energy_system.assemblies.resolver import ResolvedAssembly
from hisim.energy_system.assemblies.testing.errors import (
    HarnessUsageError,
    SampleConstructionError,
    SamplerError,
)
from hisim.energy_system.errors import EnergySystemAssemblyError


class SampleKind(enum.Enum):
    """Where a sample comes from, which decides the tier it runs in."""

    DETERMINISTIC = "deterministic"
    HYPERCUBE = "hypercube"
    SWEEP = "sweep"


@dataclass
class Sample:
    """One complete parameter set of one assembly.

    Attributes:
        sample_id: ``s000``, ``s001``, … in the order the samples were made; stable for one
            assembly, tier, size and seed.
        values: Every declared parameter's value.
        kind: Where the sample first came from.
        origins: Every reason the sample exists (``preset south``, ``max of power_in_watt``,
            ``hypercube power_in_watt stated, share_of_roof none #3``); a parameter set reached
            twice is one sample with two origins.
        presets: The presets that resolve to exactly these values; the import names the first.
    """

    sample_id: str
    values: Dict[str, Any]
    kind: SampleKind
    origins: List[str] = field(default_factory=list)
    presets: List[str] = field(default_factory=list)

    @property
    def preset(self) -> Optional[str]:
        """The preset the import is written with, or ``None``."""
        return self.presets[0] if self.presets else None

    def to_document(self) -> Dict[str, Any]:
        """The sample as plain data, for the report."""
        return {
            "id": self.sample_id,
            "kind": self.kind.value,
            "origins": list(self.origins),
            "presets": list(self.presets),
            "parameters": dict(self.values),
        }


class ParameterSpace:
    """An assembly's parameters as a box: defaults, presets, placement within the constraints."""

    def __init__(self, assembly: ResolvedAssembly) -> None:
        """Prepares the space of one resolved assembly."""
        self.assembly = assembly
        self.model: AssemblyFile = assembly.model
        self.declarations: Mapping[str, ParameterDeclaration] = self.model.parameters

    # ------------------------------------------------------------------------------------- values

    def normalised(self, values: Mapping[str, Any]) -> Dict[str, Any]:
        """The values in declaration order, a float parameter's integer written as a float."""
        result: Dict[str, Any] = {}
        for name, declaration in self.declarations.items():
            value = values[name]
            if (
                declaration.type == ParameterType.FLOAT
                and isinstance(value, int)
                and not isinstance(value, bool)
            ):
                value = float(value)
            result[name] = value
        return result

    @staticmethod
    def key(values: Mapping[str, Any]) -> str:
        """The canonical text of a parameter set, by which two samples are the same."""
        return json.dumps(values, sort_keys=True, default=repr)

    def defaults(self) -> Dict[str, Any]:
        """Every parameter at its default: the base point of the boundary and value samples.

        Raises:
            SampleConstructionError: When a parameter declares no default; the samples "the others
                at their defaults" of §9.4 need one for every parameter.
        """
        missing = [name for name, declaration in self.declarations.items() if not declaration.has_default]
        if missing:
            raise SampleConstructionError(
                f"'{self.assembly.path}' declares {', '.join(missing)} without a default; the boundary and value "
                "samples hold every other parameter at its default (assemblies_spec.md §9.4), so every parameter "
                "of a tested assembly needs one."
            )
        return self.normalised({name: declaration.default for name, declaration in self.declarations.items()})

    def preset_values(self, preset: str) -> Dict[str, Any]:
        """The values a preset resolves to, through the expansion's own resolver."""
        resolved = ParameterResolver(
            self.model, self.assembly.path, f"preset '{preset}'", lambda: f"({self.assembly.label})"
        ).resolve(preset, {})
        return self.normalised(resolved.resolved)

    @staticmethod
    def unstated_value(declaration: ParameterDeclaration) -> Any:
        """The value that leaves a parameter unstated: ``false`` for a boolean, ``none`` otherwise."""
        return False if declaration.type == ParameterType.BOOL else None

    def problems(self, values: Mapping[str, Any]) -> List[str]:
        """Every way a complete parameter set breaks a declaration or a constraint; empty when it fits."""
        found: List[str] = []
        for name, declaration in self.declarations.items():
            if name not in values:
                found.append(f"'{name}' has no value")
                continue
            problem = ParameterChecks.problem(declaration, values[name])
            if problem is not None:
                found.append(f"'{name}' is {problem}")
        for name in values:
            if name not in self.declarations:
                found.append(f"'{name}' is no parameter")
        if not found:
            for constraint in self.model.constraints:
                violation = ParameterChecks.violation(constraint, values)
                if violation is not None:
                    found.append(f"'{constraint.text()}': {violation}")
        return found

    def exclusive_with(self, parameter: str) -> List[str]:
        """The parameters an ``exactly_one_of``/``at_most_one_of`` keeps unstated when this one is stated."""
        others: List[str] = []
        for constraint in self.model.constraints:
            if parameter in constraint.parameters:
                others.extend(name for name in constraint.parameters if name != parameter and name not in others)
        return others

    def place(self, base: Mapping[str, Any], parameter: str, value: Any, origin: str) -> Dict[str, Any]:
        """The base with one parameter set, moved into that parameter's constraint branch.

        Raises:
            SampleConstructionError: When the declarations still do not admit the sample.
        """
        values = dict(base)
        values[parameter] = value
        if ParameterChecks.is_stated(value):
            for other in self.exclusive_with(parameter):
                values[other] = self.unstated_value(self.declarations[other])
        values = self.normalised(values)
        problems = self.problems(values)
        if problems:
            raise SampleConstructionError(
                f"the sample '{origin}' of '{self.assembly.path}' is not admitted by its declarations: "
                + "; ".join(problems)
                + ". Give the parameters it depends on stated defaults, or restate the constraint."
            )
        return values


class SampleBook:
    """The samples of one assembly, each parameter set once, with every reason it was drawn."""

    def __init__(self, space: ParameterSpace) -> None:
        """Starts an empty book."""
        self.space = space
        self.samples: List[Sample] = []
        self._by_key: Dict[str, Sample] = {}

    def add(self, values: Mapping[str, Any], origin: str, kind: SampleKind, preset: Optional[str] = None) -> Sample:
        """Adds a parameter set, or the origin to the sample that already holds it.

        Raises:
            SamplerError: When the values break a declaration or a constraint; every caller has
                placed them within the constraints already, so this is a bug of the harness.
        """
        normalised = self.space.normalised(values)
        problems = self.space.problems(normalised)
        if problems:
            raise SamplerError(
                f"the harness built the sample '{origin}' of '{self.space.assembly.path}', which breaks its "
                "declarations: " + "; ".join(problems) + "."
            )
        key = ParameterSpace.key(normalised)
        sample = self._by_key.get(key)
        if sample is None:
            sample = Sample(sample_id=f"s{len(self.samples):03d}", values=normalised, kind=kind)
            self._by_key[key] = sample
            self.samples.append(sample)
        if origin not in sample.origins:
            sample.origins.append(origin)
        if preset is not None and preset not in sample.presets:
            sample.presets.append(preset)
        return sample

    def of_kind(self, *kinds: SampleKind) -> List[Sample]:
        """The samples first drawn as one of the kinds, in order."""
        return [sample for sample in self.samples if sample.kind in kinds]

    def with_preset(self, preset: str) -> Optional[Sample]:
        """The sample a preset resolved to."""
        return next((sample for sample in self.samples if preset in sample.presets), None)


class DeterministicSamples:
    """The samples both tiers run: defaults, presets, boundaries, allowed values, variant options."""

    @classmethod
    def add_to(cls, book: SampleBook) -> None:
        """Adds every deterministic sample of the book's assembly.

        Raises:
            SampleConstructionError: For a sample the assembly's declarations do not admit.
        """
        space = book.space
        model = space.model
        base = space.defaults()
        book.add(base, "defaults", SampleKind.DETERMINISTIC)
        for preset in model.presets:
            try:
                values = space.preset_values(preset)
            except EnergySystemAssemblyError as error:
                raise SampleConstructionError(f"the preset '{preset}' does not resolve: {error}") from error
            book.add(values, f"preset {preset}", SampleKind.DETERMINISTIC, preset=preset)
        for name, declaration in space.declarations.items():
            if declaration.type.is_numeric and declaration.range is not None:
                low, high = declaration.range
                for label, bound in (("min", low), ("max", high)):
                    value = int(bound) if declaration.type == ParameterType.INT else float(bound)
                    origin = f"{label} of {name}"
                    book.add(space.place(base, name, value, origin), origin, SampleKind.DETERMINISTIC)
        for name, declaration in space.declarations.items():
            for value in declaration.allowed_values or ():
                origin = f"{name} = {value!r}"
                book.add(space.place(base, name, value, origin), origin, SampleKind.DETERMINISTIC)
        for variant in model.variants.values():
            for option in variant.options.values():
                if not option.when:
                    continue
                origin = f"variant {variant.name}: {option.name}"
                book.add(
                    space.place(base, variant.selected_by, option.when[0], origin), origin, SampleKind.DETERMINISTIC
                )


@dataclass(frozen=True)
class Dimension:
    """One dimension of a constraint branch's hypercube.

    Attributes:
        parameter: The parameter it draws.
        low: A numeric dimension's lower bound.
        high: Its upper bound.
        integer: Whether the parameter is an integer.
        choices: A discrete dimension's values, in declared order; empty for a numeric one.
    """

    parameter: str
    low: float = 0.0
    high: float = 0.0
    integer: bool = False
    choices: Tuple[Any, ...] = ()

    @property
    def is_discrete(self) -> bool:
        """Whether the dimension draws from a closed set of values."""
        return bool(self.choices)

    def value(self, unit: float, size: int) -> Any:
        """The parameter value at a coordinate of the unit interval, for a hypercube of ``size`` points.

        A numeric dimension scales the coordinate into its range (an integer one cuts the range
        into equal integer bins); a discrete one takes the coordinate's stratum ``floor(u * size)``
        and cuts the strata into equal bins, one per value.
        """
        if self.is_discrete:
            stratum = min(int(math.floor(unit * size)), size - 1)
            return self.choices[stratum * len(self.choices) // size]
        if self.integer:
            low, high = int(self.low), int(self.high)
            return min(high, low + int(math.floor(unit * (high - low + 1))))
        return float(self.low + unit * (self.high - self.low))


@dataclass(frozen=True)
class ConstraintBranch:
    """One feasible assignment of stated and unstated to the parameters the constraints name.

    Attributes:
        index: Its position among the assembly's branches.
        stated: Every constrained parameter, stated or not.
        fixed: The parameters the branch fixes, with their values.
        dimensions: The parameters it draws.
    """

    index: int
    stated: Mapping[str, bool]
    fixed: Mapping[str, Any]
    dimensions: Tuple[Dimension, ...]

    @property
    def label(self) -> str:
        """How the report names the branch."""
        if not self.stated:
            return "the whole box"
        return ", ".join(f"{name} {'stated' if is_stated else 'none'}" for name, is_stated in self.stated.items())


class HypercubeSampler:
    """The seeded Latin hypercube sample of an assembly's parameter box, one hypercube per branch."""

    #: How many constrained parameters the branch enumeration takes; beyond, it is refused.
    MAXIMUM_CONSTRAINED = 12

    def __init__(self, space: ParameterSpace, size: int, seed: int) -> None:
        """Prepares the sampler.

        Args:
            space: The assembly's parameter space.
            size: The number of samples per branch.
            seed: The seed; branch ``i`` draws from ``numpy.random.default_rng((seed, i))``.

        Raises:
            HarnessUsageError: For a size below one.
        """
        if size < 1:
            raise HarnessUsageError(f"the hypercube sample size is {size}; it must be at least 1.")
        self.space = space
        self.size = size
        self.seed = seed

    def constrained(self) -> List[str]:
        """The parameters any constraint names, in declaration order."""
        named = {name for constraint in self.space.model.constraints for name in constraint.named_parameters()}
        return [name for name in self.space.declarations if name in named]

    def _stated_representative(self, declaration: ParameterDeclaration) -> Tuple[bool, Any]:
        """Whether a parameter can be stated, and a stated value of it, for the branch check."""
        if declaration.type.is_numeric:
            return (declaration.range is not None, declaration.range[0] if declaration.range else None)
        if declaration.type == ParameterType.BOOL:
            return True, True
        stated = [value for value in declaration.allowed_values or () if ParameterChecks.is_stated(value)]
        if stated:
            return True, stated[0]
        if declaration.has_default and ParameterChecks.is_stated(declaration.default):
            return True, declaration.default
        return False, None

    def branches(self) -> List[ConstraintBranch]:
        """Every feasible branch of the box, in the order of the stated/unstated product.

        Raises:
            SampleConstructionError: When more than :attr:`MAXIMUM_CONSTRAINED` parameters are
                constrained, or no branch is feasible.
        """
        constrained = self.constrained()
        declarations = self.space.declarations
        if len(constrained) > self.MAXIMUM_CONSTRAINED:
            raise SampleConstructionError(
                f"'{self.space.assembly.path}' constrains {len(constrained)} parameters; the branch enumeration "
                f"takes at most {self.MAXIMUM_CONSTRAINED}."
            )
        assignments: List[Dict[str, bool]] = []
        for states in itertools.product((True, False), repeat=len(constrained)):
            assignment = dict(zip(constrained, states))
            values: Dict[str, Any] = {}
            feasible = True
            for name, is_stated in assignment.items():
                if is_stated:
                    possible, representative = self._stated_representative(declarations[name])
                    feasible = feasible and possible
                    values[name] = representative
                else:
                    values[name] = self.space.unstated_value(declarations[name])
            if feasible and all(
                ParameterChecks.violation(constraint, values) is None for constraint in self.space.model.constraints
            ):
                assignments.append(assignment)
        if not assignments:
            raise SampleConstructionError(
                f"no assignment of stated and unstated to {', '.join(constrained)} satisfies the constraints of "
                f"'{self.space.assembly.path}'."
            )
        defaults = {name: declaration.default for name, declaration in declarations.items()}
        branches: List[ConstraintBranch] = []
        for index, assignment in enumerate(assignments):
            fixed: Dict[str, Any] = {}
            dimensions: List[Dimension] = []
            for name, declaration in declarations.items():
                if assignment.get(name) is False:
                    fixed[name] = self.space.unstated_value(declaration)
                elif declaration.type.is_numeric and declaration.range is not None:
                    dimensions.append(
                        Dimension(
                            parameter=name,
                            low=float(declaration.range[0]),
                            high=float(declaration.range[1]),
                            integer=declaration.type == ParameterType.INT,
                        )
                    )
                elif declaration.type == ParameterType.BOOL:
                    if assignment.get(name) is True:
                        fixed[name] = True
                    else:
                        dimensions.append(Dimension(parameter=name, choices=(True, False)))
                elif declaration.allowed_values is not None:
                    choices = tuple(
                        value
                        for value in declaration.allowed_values
                        if assignment.get(name) is not True or ParameterChecks.is_stated(value)
                    )
                    dimensions.append(Dimension(parameter=name, choices=choices))
                else:
                    fixed[name] = defaults[name]
            branches.append(
                ConstraintBranch(index=index, stated=assignment, fixed=fixed, dimensions=tuple(dimensions))
            )
        return branches

    def unit_points(self, branch: ConstraintBranch) -> np.ndarray:
        """The branch's points in the unit hypercube, ``size`` rows by one column per dimension."""
        if not branch.dimensions:
            return np.zeros((1, 0))
        generator = np.random.default_rng((self.seed, branch.index))
        sampler = qmc.LatinHypercube(d=len(branch.dimensions), scramble=True, seed=generator)
        return np.asarray(sampler.random(self.size), dtype=float)

    def values(self, branch: ConstraintBranch) -> List[Dict[str, Any]]:
        """The branch's parameter sets: one per hypercube point (one in all for a branch without dimensions)."""
        result: List[Dict[str, Any]] = []
        for point in self.unit_points(branch):
            values: Dict[str, Any] = dict(branch.fixed)
            for dimension, unit in zip(branch.dimensions, point):
                values[dimension.parameter] = dimension.value(float(unit), self.size)
            result.append(self.space.normalised(values))
        return result

    def add_to(self, book: SampleBook) -> List[ConstraintBranch]:
        """Adds every branch's hypercube to the book; returns the branches.

        Raises:
            SamplerError: When a drawn sample breaks a constraint (a bug of the sampler).
        """
        branches = self.branches()
        for branch in branches:
            for number, values in enumerate(self.values(branch)):
                book.add(values, f"hypercube {branch.label} #{number}", SampleKind.HYPERCUBE)
        return branches


def sweep_values(declaration: ParameterDeclaration, steps: int) -> List[Any]:
    """The values a monotone sweep moves a parameter through: ``steps`` equidistant points of its range.

    Raises:
        HarnessUsageError: For fewer than two steps, or a parameter without a numeric range.
    """
    if steps < 2:
        raise HarnessUsageError(f"a monotone sweep takes at least 2 steps, not {steps}.")
    if not declaration.type.is_numeric or declaration.range is None:
        raise HarnessUsageError(f"the parameter '{declaration.name}' has no numeric range to sweep.")
    low, high = declaration.range
    points = np.linspace(float(low), float(high), steps)
    if declaration.type == ParameterType.INT:
        return list(dict.fromkeys(int(round(point)) for point in points))
    return [float(point) for point in points]


def sweep_samples(
    space: ParameterSpace, base: Sample, parameter: str, steps: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """The parameter sets of one sweep from one base, or why the base admits none.

    Returns:
        The swept parameter sets in rising order of the parameter, and ``None``; or an empty list
        and the reason the base's constraint branch admits no sweep of the parameter.
    """
    declaration = space.declarations[parameter]
    swept: List[Dict[str, Any]] = []
    for value in sweep_values(declaration, steps):
        values = dict(base.values)
        values[parameter] = value
        problems = space.problems(space.normalised(values))
        if problems:
            return [], (
                f"the base {base.sample_id} keeps '{parameter}' unstated by its constraint branch "
                f"({'; '.join(problems)}), so moving it would change two things at once"
            )
        swept.append(space.normalised(values))
    return swept, None


def sample_summary(samples: Sequence[Sample]) -> Dict[str, int]:
    """How many samples of each kind a book holds, for the report."""
    counts: Dict[str, int] = {kind.value: 0 for kind in SampleKind}
    for sample in samples:
        counts[sample.kind.value] += 1
    return counts
