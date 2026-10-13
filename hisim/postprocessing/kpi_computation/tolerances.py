"""The golden gate's numeric tolerance, in the one place every comparer of KPI values reads it.

Same-machine output is byte-exact, so a very tight relative tolerance still passes while absorbing
sub-ULP cross-platform drift (golden-KPI spec §7). The gate's scripts (``scripts/golden_kpis.py``
and the scripts importing it) read the two values from here. The assembly test harness's ``monotone``
comparison (:mod:`hisim.energy_system.assemblies.testing.checks`) reads :data:`REL_TOL` from here; its
absolute floor is ``MONOTONE_ABS_FLOOR`` in that module, not :data:`ABS_TOL`.

Two values are equal when ``math.isclose(got, ref, rel_tol=REL_TOL, abs_tol=ABS_TOL)`` holds, that is
when ``|got - ref| <= max(REL_TOL * max(|got|, |ref|), ABS_TOL)``: the relative tolerance scales with the
larger of the two magnitudes, and the absolute tolerance is a floor below it.
"""

#: The relative tolerance two KPI values are equal within.
REL_TOL = 1e-9

#: The absolute tolerance two KPI values are equal within, in the KPI's own unit.
#:
#: A KPI that is physically zero still comes out as accumulated rounding noise, for example the
#: electric-heating house's one-week temperature deviation below 20 °C of about 1.4e-9 K·h. Its last
#: bits depend on the environment and the cache state, not on the code (hisim-b3b.6): the bless wrote
#: 1.3982390948778326e-09, the check computed 1.398239035665938e-09, 6e-17 apart. Relative to a zero,
#: one last-bit difference is a relative error of 4e-8, forty times ``REL_TOL``. The floor makes such
#: values comparable. At 1e-12 it lies far above that noise and far below any value a KPI states
#: physically.
ABS_TOL = 1e-12
