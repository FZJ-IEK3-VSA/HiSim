"""The package's one root finder, shared by the views and the scenario break-even (cost_spec.md §4.6).

`scenarios.find_break_even` uses it to find where two variants' KPI difference crosses zero, and
`views._effective_annual_rate` to find a loan's effective rate. It bisects because both functions are only piecewise
smooth (subsidy caps and tariff tiers make kinks). The module imports nothing from `hisim.economics`.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple


def bisect_root(
    function: Callable[[float], float],
    window: Tuple[float, float],
    max_iterations: int,
    tolerance: Optional[float] = None,
) -> Optional[float]:
    """Return a root of `function` inside `window` by bisection, or None when the window's ends share a sign.

    Example: `bisect_root(lambda x: x - 2.0, (0.0, 10.0), 60)` returns about 2.0. A window whose ends have the same
    sign holds no crossing bisection can find, so the caller gets None ("no root in range") instead of an arbitrary
    end.

    Args:
        function: The function to solve. It is evaluated once at each end and once per halving.
        window: `(low, high)` bounds of the search, inclusive.
        max_iterations: Maximum number of halvings; reaching it returns the midpoint of the remaining interval.
        tolerance: Interval width at which to stop early, or None to always spend `max_iterations` halvings.

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
