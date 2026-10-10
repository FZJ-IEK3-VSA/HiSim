"""Acceleration of the simulator's fixed-point iteration on the outputs that ask for it.

The simulator iterates every time step until no output changes by more than its tolerance. Where an output is the
image of a fixed-point map, such as the step mean temperature a water node publishes in answer to the supply its
generators send back, the plain iteration can need more passes than the simulator allows before it forces
convergence. A component declares such an output with ``is_accelerated=True`` (:meth:`hisim.component.Component.add_output`),
and the simulator then replaces, after the component has run, the value it computed by the next iterate of
:func:`accelerated_iterate`: the plain value for the first iterations of a step, a secant extrapolation of the
output's own fixed point after them, and an under-relaxed value when the iteration oscillates. Once the simulator
forces convergence, which freezes the controllers, it stops accelerating: the history before spans the controllers'
switching, so a secant step from it would aim at a fixed point that no longer exists.

The iteration history belongs to the simulator, which runs the iteration, so the components stay functions of their
inputs and their saved state. Every rule here is generic: it knows nothing about the component or the quantity, and
it changes only how fast the fixed point is reached, never which one.

Terms. The **published** value of an output is the value the other components reacted to: the value it held in the
step values when its component ran. The **computed** value is what the component wrote in that pass. The
**residual** of a pass is computed minus published; it is zero at the fixed point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List, Sequence

from hisim import component as cp


class AccelerationHistoryError(ValueError):

    """An iteration history that cannot be accelerated: empty, unpaired or not finite.

    It is a ``ValueError``, as every refusal of invalid input in HiSim is.
    """


class SecantAcceleration:

    """The constants of the fixed-point acceleration, a namespace.

    The values were chosen on the hot-water tank of a gas boiler, whose firing steps need about eleven plain
    iterations at 900 s steps; the acceleration brings them under the simulator's ten.
    """

    #: The number of iterations on one step after which the output is accelerated: six plain iterations come first,
    #: because a well-contracting iteration converges in fewer and an extrapolation from a short history is noisy.
    AFTER_ITERATIONS: ClassVar[int] = 6

    #: The weight of the new value when an oscillating iteration is under-relaxed, dimensionless.
    UNDER_RELAXATION_WEIGHT: ClassVar[float] = 0.5

    #: The largest estimated contraction factor the secant extrapolates at, dimensionless. The secant step is the
    #: residual over ``1 - theta``, so at 0.9 it is at most ten times the residual; closer to 1 an estimate from two
    #: noisy passes would turn a residual of 0.01 K into a jump of 10 K, and the plain iterate is used instead.
    MAXIMAL_CONTRACTION_FACTOR: ClassVar[float] = 0.9


def _finite(name: str, value: float) -> float:
    """Return ``value`` as a float, refused when it is NaN or infinite.

    Raises:
        AccelerationHistoryError: If ``value`` is NaN or infinite; the message names it ``name``.
    """
    number = float(value)
    if not math.isfinite(number):
        raise AccelerationHistoryError(f"{name} must be finite, got {value!r}.")
    return number


def accelerated_iterate(*, published_values: Sequence[float], computed_values: Sequence[float]) -> float:
    """Return the value an accelerated output takes next, from its iteration history on the current step.

    ``computed_values[j]`` is what the output's component computed in pass ``j`` while the output stood at
    ``published_values[j]``, oldest first. The residual of a pass is ``r_j = computed_values[j] - published_values[j]``.

    * Up to ``SecantAcceleration.AFTER_ITERATIONS`` passes: the plain iterate, the last computed value.
    * When the last two residuals have opposite signs (an oscillation): the under-relaxed ``x + w r`` with
      ``w = SecantAcceleration.UNDER_RELAXATION_WEIGHT``.
    * Otherwise the secant step on the residual, ``x - r (x - x_prev) / (r - r_prev)``, which is Aitken's
      delta-squared extrapolation of a plain iteration. It is taken only when the secant's estimate of the
      contraction factor ``theta = 1 + (r - r_prev) / (x - x_prev)`` lies in
      [0, ``SecantAcceleration.MAXIMAL_CONTRACTION_FACTOR``], a monotone contraction whose step ``r / (1 - theta)``
      is at most ten times the residual; outside it, and when two iterates coincide, the plain iterate is returned.

    A zero residual returns the published value in every branch, so a fixed point is never moved. For example, the
    contraction ``F(x) = 0.5 x + 20``, iterated plainly from 10, is at 39.84 after seven passes; the secant step
    from its last two passes lands on its fixed point, 40. Two passes at 55.000 and 55.001 °C with residuals of
    0.0100 and 0.009999 K estimate ``theta = 0.999``; the secant would jump to 65 °C, so the plain iterate, 55.010999 °C,
    is returned.

    Args:
        published_values: The value the output stood at in each pass of the step, oldest first.
        computed_values: The value the component computed in each of those passes.

    Returns:
        The next value of the output.

    Raises:
        AccelerationHistoryError: If the history is empty, the two sequences differ in length, or one of the last
            two pairs is not finite.
    """
    if len(published_values) != len(computed_values):
        raise AccelerationHistoryError(
            f"Every computed value pairs with the value published before it: got {len(published_values)} published "
            f"and {len(computed_values)} computed."
        )
    if not published_values:
        raise AccelerationHistoryError("The iteration history is empty.")
    count = len(published_values)
    last = count - 1
    computed_last = _finite(f"computed_values[{last}]", computed_values[last])
    published_last = _finite(f"published_values[{last}]", published_values[last])
    if count <= SecantAcceleration.AFTER_ITERATIONS:
        return computed_last
    published_previous = _finite(f"published_values[{last - 1}]", published_values[last - 1])
    computed_previous = _finite(f"computed_values[{last - 1}]", computed_values[last - 1])
    residual_previous = _finite("The previous residual", computed_previous - published_previous)
    residual = _finite("The residual", computed_last - published_last)
    if residual * residual_previous < 0.0:
        return _finite(
            "The under-relaxed value", published_last + SecantAcceleration.UNDER_RELAXATION_WEIGHT * residual
        )
    if published_last == published_previous or residual == residual_previous:
        return computed_last
    slope = _finite("The secant slope", (residual - residual_previous) / (published_last - published_previous))
    contraction_factor = 1.0 + slope
    if not 0.0 <= contraction_factor <= SecantAcceleration.MAXIMAL_CONTRACTION_FACTOR:
        return computed_last
    return _finite("The secant extrapolation", published_last - residual / slope)


@dataclass
class StepAcceleration:

    """The iteration history of every accelerated output on one time step, and the acceleration it drives.

    The simulator creates one per time step: before a component runs it records the published values of the
    component's accelerated outputs (:meth:`published_values_of`), and after it runs it replaces what the component
    computed by the next accelerated value (:meth:`accelerate`), until it forces convergence. The history is reset
    with every step because a new object is made.
    """

    #: For each accelerated output, by its index in the step values: the published values, oldest first.
    published_by_index: Dict[int, List[float]] = field(default_factory=dict)
    #: For each accelerated output, by its index in the step values: the computed values, oldest first.
    computed_by_index: Dict[int, List[float]] = field(default_factory=dict)

    @staticmethod
    def published_values_of(indices: Sequence[int], stsv: cp.SingleTimeStepValues) -> List[float]:
        """Return the values the accelerated outputs at these indices stand at before their component runs."""
        return [stsv.values[index] for index in indices]

    def accelerate(self, indices: Sequence[int], stsv: cp.SingleTimeStepValues, published: Sequence[float]) -> None:
        """Replace the computed values of the accelerated outputs at these indices by their next accelerated values.

        Args:
            indices: The indices of one component's accelerated outputs in the step values.
            stsv: The step values the component has just written.
            published: The values those outputs stood at before the component ran, in the order of ``indices``.
        """
        for index, published_value in zip(indices, published):
            published_history = self.published_by_index.setdefault(index, [])
            computed_history = self.computed_by_index.setdefault(index, [])
            published_history.append(published_value)
            computed_history.append(stsv.values[index])
            stsv.values[index] = accelerated_iterate(published_values=published_history, computed_values=computed_history)
