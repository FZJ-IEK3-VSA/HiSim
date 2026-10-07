"""The golden gate's numeric tolerance, in the one place every comparer of KPI values reads it.

Same-machine output is byte-exact, so a very tight relative tolerance still passes while absorbing
sub-ULP cross-platform drift (golden-KPI spec §7). The gate's scripts (``scripts/golden_kpis.py``
and the scripts importing it) and the assembly test harness's ``monotone`` comparison
(:mod:`hisim.energy_system.assemblies.testing.checks`) read the two values from here.
"""

#: The relative tolerance two KPI values are equal within.
REL_TOL = 1e-9

#: The absolute tolerance two KPI values are equal within.
ABS_TOL = 0.0
