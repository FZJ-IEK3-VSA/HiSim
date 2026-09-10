"""The package's one root finder, shared by the views and the scenario break-even (§4.6, V5).

Two places in the cost package solve a scalar equation by bisection: `scenarios.find_break_even`
looks for the parameter value at which two variants' KPI difference crosses zero, and
`views._effective_annual_rate` looks for the discount rate at which a loan's own flow sequence has
present value zero. They had one implementation each, with the same three decisions — the search
window, what to do when the window does not bracket a sign change, and how many halvings to spend —
made twice and worded differently. This module makes them once.

Bisection rather than a faster root finder in both cases, and for the same reason: the functions
are only piecewise smooth. A KPI difference has kinks wherever a subsidy cap binds, a replacement
falls in or out of the horizon or a tariff tier changes; a loan's present value is smooth but is
evaluated on a schedule that may be truncated. Bisection cannot be thrown by a kink as long as the
interval brackets a sign change, and a bracket that does not is *reported* rather than solved
around — a root reported outside the searched window would be a number nobody asked for.

This module is a leaf: it imports nothing from `hisim.economics`, so both the engine-side scenario
machinery and the view layer can use it without either reaching for the other.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple


def bisect_root(
    function: Callable[[float], float],
    window: Tuple[float, float],
    max_iterations: int,
    tolerance: Optional[float] = None,
) -> Optional[float]:
    """Bisects `function` inside `window`, or None when the window's ends share a sign.

    The same-sign refusal is the point of the helper as much as the halving is: a window whose
    ends have the same sign contains no crossing that bisection can find, and returning None says
    "no root in the searched range" instead of returning whichever end the iteration drifted to.
    Callers turn that None into their own statement — "no crossing in range" for a break-even, "no
    non-negative effective rate" for a loan.

    Args:
        function: The function to root. It is evaluated once at each end and once per iteration,
            and the value at an end is reused rather than recomputed, so an expensive function
            (the break-even's is two full evaluations) costs one call per halving.
        window: `(low, high)` bounds of the search, inclusive.
        max_iterations: Hard cap on halvings. Reaching it returns the midpoint of the remaining
            interval rather than raising: the interval still brackets the root, it is merely
            wider than the caller hoped.
        tolerance: Absolute interval width at which to stop early, or None to always spend
            `max_iterations` halvings. A caller whose function is cheap passes None and buys
            precision with iterations instead.

    Returns:
        The root, or None when `function(low)` and `function(high)` have the same sign.
    """
    low, high = window
    value_low, value_high = function(low), function(high)
    if value_low * value_high > 0:
        return None
    for _iteration in range(max_iterations):
        middle = (low + high) / 2.0
        value_middle = function(middle)
        if tolerance is not None and abs(high - low) < tolerance:
            return middle
        if value_low * value_middle <= 0:
            high, value_high = middle, value_middle
        else:
            low, value_low = middle, value_middle
    return (low + high) / 2.0
