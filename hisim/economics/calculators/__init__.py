"""The calculators of the lifecycle cost engine, one module per financial mechanism (cost_spec.md §2.3).

`evaluator.EconomicEvaluator` composes them: `annualization` (scaling a partial simulated year to a full one),
`escalation` (`(1+r)**n` and the rate fallbacks), `categories` (cost category sets), `context_resolution` (new, kept or
replaced asset), `investment` (capex, replacements, residual value), `maintenance`, `energy` (the bill over the
horizon), `co2`, `subsidy_application`, `financing_application`, `reserve` (the OPERATING_ONLY sinking fund) and
`aggregation` (NPV, EAC and pivots).

Amounts are cost-positive and revenue-negative (§3.6). Every timeline entry is in nominal euros of its own year,
undiscounted; year 0 is the investment year and recurring flows run over years 1..T. The order in which entries reach
the timeline matters, because the NPV is summed in entry order.
"""
