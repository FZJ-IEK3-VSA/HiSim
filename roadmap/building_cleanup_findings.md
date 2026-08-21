# Building cleanup — findings log

Observations collected while building the phase-1 test harness for
`hisim/components/building.py` (plan of record: `roadmap/building_cleanup_spec.md`).
Nothing here was fixed: ground rule 1 of the cleanup is "no value changes, ever", so
current behaviour — including the crashes — was pinned in
`tests/goldens/building_information.json` and `tests/goldens/building_one_day.json` and is
listed here for the design review's physics section.

## BuildingInformation, from the full TABULA sweep (2974 codes)

1. **124 of the 2974 TABULA building codes crash `BuildingInformation`** with
   `ZeroDivisionError: float division by zero` in `set_window_area_parameter`:
   `window_scaling_factor = window_area_in_m2 / (A_Window_1 + A_Window_2)` and both TABULA
   window areas are zero for those rows. Affected: 121 `DE.DistrictMZLerch.G.EFH.SyAv.*`
   district variants and `ES.ME.MFH.05.Gen.ReEx.001.001/.002/.003`. All 124 are pinned as
   `"raises: ZeroDivisionError: ..."` snapshot entries.
2. **A configured window area cannot rescue such a code.** The divisor is the TABULA
   reference area, not the configured one, so the same 124 codes still raise with an
   explicit `window_area_in_m2`. The override sweep confirms this for
   `DE.DistrictMZLerch.G.EFH.SyAv.001.011`, which raises under all five override variants.
3. **121 codes have `A_C_Ref == 0`.** With no floor area in the config the class silently
   substitutes 500 m² (with a warning) and writes that value back into the TABULA reference
   row (`buildingdata_ref["A_C_Ref"] = ...`). The magic default and the write-back are both
   invisible in the resulting object.
4. **Scaling round-trips a value through its own ratio.** Configuring
   `absolute_conditioned_floor_area_in_m2 = 250.0` produces
   `scaled_conditioned_floor_area_in_m2 = 249.99999999999997`, because the code computes
   `ref * (target / ref)` instead of using `target`. Phase 3's deduplication of the twin
   branches must preserve the artefact or the golden change has to be justified explicitly.
5. **`number_of_apartments` is truncated, not rounded.** `int(...)` is applied to
   `conditioned_floor_area / 92.1`, so the default 121.2 m² single-family home reports 1
   apartment (1.32 truncated), and a configured `number_of_apartments = 0` takes the same
   path. A 183 m² dwelling would still report 1.
6. **Reference-data mutation.** `delta_U_ThermalBridging == 0` is patched to 0.1 in the
   reference row before use, so the object's own view of the TABULA data differs from the
   file. Already known to the spec (phase 3, item 4); the sweep confirms it fires for a
   large share of the catalogue.
7. **The door U-value is `(u * area) / area`.** Guarded against a zero area, so the
   guard's `else` branch is the only one that ever produces a different value — the
   computation is a no-op with a division-by-zero hazard attached.
8. **Near-colliding attribute names around the 11-tuple.**
   `tabula_ref_heat_transfer_coeff_by_ventilation_reference_in_watt_per_kelvin` (position 8
   of the tuple) and
   `tabula_ref_heat_transfer_coeff_by_ventilation_ref_in_watt_per_m2_per_kelvin`
   (position 10) differ only in `ref`/`reference` and the unit suffix, and the transmission
   coefficient sits between them. The assignment is currently correct; the naming is what
   makes the positional unpacking dangerous.
9. **62 public attributes, one uniform shape.** All 2850 non-crashing codes produce exactly
   the same attribute set, which is why the golden can store the attribute names once and
   one aligned value row per code.

## Building component, from the one-day snapshot

10. **The solar-gain disk cache makes the component order-dependent.** `Building.__init__`
    asks `utils.get_cache_file` whether a cached solar-gain series exists for its config and
    simulation parameters; if one does, `i_simulate` reads `solar_heat_gain_through_windows`
    from it and never evaluates the window model at all. Two runs of the same setup
    therefore take different code paths, and a cache written by a *different* test sharing
    the key supplies its values. Layer 2 has to patch `utils.get_cache_file` to be
    reproducible at all. Worth an explicit decision in phase 6.
11. **Two output branches are unreachable at default settings.** The cooling side of
    `TheoreticalThermalBuildingDemand` and the window-opening indoor-air override
    (`enable_opening_windows`, `OpenWindow`) require the building to overheat, which no
    plausible winter day at the default setpoints achieves. Layer 2 reaches them with a
    deliberately oversized final heating block plus a scaled variant with lowered
    setpoints; anyone changing those branches should know the coverage depends on that
    choice.
12. **`i_simulate` writes the cache file on the last timestep** (`timestep + 1 ==
    timesteps`), i.e. a component method performs a file write as a side effect of a
    simulation step. Relevant for phase 6's split of thermal model from I/O glue.

## Phase 2 (the mechanical split), notes from execution

13. **One import cycle: `BuildingConfig.get_main_classname` needs `Building`.** The config
    returns `Building.get_full_classname()`, giving the only backward edge in the package
    (`config -> building` while `building -> config` for the constructor signature). Resolved
    with a runtime-local import inside the method (sanctioned by the spec as "the minimal
    local import"), plus a module-level `# pylint: disable=cyclic-import` in `config.py` and
    an inline `import-outside-toplevel` disable — pylint counts even function-level imports
    in its cycle graph, and both messages would otherwise fail the prospector/critical-only
    gates. Module import order stays acyclic.
14. **No scanner needed fixing for the package layout.** `scripts/check_config_attrs.py`
    walks with `rglob("*.py")`, `json_executor.import_from_string` resolves classnames via
    `importlib.import_module` + `getattr` (the re-exporting `__init__` keeps the old
    `hisim.components.building.Building` strings working), and
    `project_code_overview_generator.py` uses `os.walk` — all recurse into packages. The
    `# clean` tag the overview generator reads was carried onto every new submodule.
15. **Docs reference the package path and still resolve, but autodoc member visibility is
    untested.** `docs/api/hisim.components.building.rst` uses `automodule` on the package;
    the classes are now imported names in `__init__` (listed in `__all__`). The docs build is
    not a CI gate, so this was left unchanged — worth a look whenever the Sphinx docs are
    next built.
16. **The phase-1 goldens contain no module paths** (verified by grep before the split), so
    both golden files are untouched by phase 2 — no regeneration, no justification needed.

## Phase 3 (BuildingInformation stage 1), notes from execution

17. **Finding 7 was wrong: the door `(u * area) / area` round trip is NOT a float no-op.**
    Simplifying it to the bare U-value turned 164 of the 3,000 layer-1 snapshots red: the
    multiply-then-divide shifts the last mantissa bit for those codes (e.g. TABULA's stored
    2.2 becomes 2.2000000000000006 for `SI.N.SFH-TH.03-04.Gen.SyAv.001.001`, and the
    conductance derived from it moves with it). Per ground rules 1 and 2 the exact
    expression was kept (with a guarding comment in `set_door_heat_transfer_parameter`);
    removing the artifact is a deliberate golden-regeneration decision for the design
    review, not a cleanup. The zero-area guard's `else` branch remains the only path that
    skips the round trip.
18. **The TABULA-row mutations were not load-bearing.** The phase-3 trace confirmed
    `A_C_Ref` is read exactly once (before the write-back site can run) and never again,
    inside or outside the class, and the `delta_U_ThermalBridging` patch was re-read only
    by the very next statement; `building.py`/`window.py` never touch `buildingdata_ref`,
    and `tests/test_building_manual_calculation_thermal_conductances.py` (the one external
    reader) touches only `H_Transmission_*`/`U_Actual_*`/`A_*`/`b_Transmission_*` cells.
    The row is now read-only after lookup.
19. **Dead None-checks in the reference-data reader.** `set_reference_data_from_tabula`
    (formerly the 11-tuple method) checks `is None` on values that just came out of
    `float(...)`, which can never be None (a NaN cell yields `nan`, not None). The checks
    were kept verbatim during the de-tupling to stay strictly behavior-identical; they are
    a candidate for removal in phase 4's element-table rewrite.
20. **The dead bare annotations were load-bearing for pylint.** Deleting them (phase 3,
    hazard 3) woke up 44 `attribute-defined-outside-init` messages: pylint follows
    attribute assignments only one call level below `__init__`, and the `set_*` leaf
    methods sit two levels deep (behind `get_building_area_parameters` /
    `get_building_heat_transfer_parameters` / `get_constants`). Resolved with one explicit
    class-level disable naming the temporal coupling instead of 40 misleading annotations;
    phase 4's pipeline rewrite should make the disable removable.

## Phase 4 (BuildingInformation stage 2, the element table), notes from execution

21. **Python's built-in `sum()` is NOT behavior-identical to chained `+` on floats.**
    Since Python 3.12, `sum()` uses Neumaier compensated summation (CPython gh-100425),
    which can differ from left-associated addition in the last mantissa bit: TABULA's
    wall areas `255.95 + 38.48 + 52.92` give `347.35` chained but `347.34999999999997`
    via `sum()`. Replacing the per-element `+` chains with `sum()` in the shared helpers
    turned 56 of the 3,006 layer-1 snapshots red (areas, weighted U-values and every
    conductance derived from them). The element helpers therefore sum through an explicit
    left-associated loop (`_left_associated_float_sum`), with a docstring pinning the
    reason. Anyone "simplifying" a float `+` chain to `sum()`/`math.fsum()` anywhere in
    the golden's reach will hit the same wall.
22. **Entries 19 and 20 are resolved.** The dead `is None` checks on `float(...)` results
    in `set_reference_data_from_tabula` are removed, and the class-level
    `attribute-defined-outside-init` disable came out: after the phase-4 restructure every
    attribute assignment sits at most one call level below `__init__` (the value-only
    helpers `_scaled_element_area_in_m2`, `_element_u_value_and_adjustment_factor`,
    `_thermal_bridging_conductance_in_watt_per_kelvin` and
    `_ventilation_conductance_in_watt_per_kelvin` assign nothing), which is exactly the
    depth pylint follows. Prospector reports 0 messages without any suppression.
23. **The spec's "~330 lines become ~70" excluded the documentation conventions.** The
    ten-method block (283 lines post-phase-3) became ~55 lines of explicit pipeline
    assignments plus ~110 lines of table and helpers whose majority is docstrings and
    field documentation (the descriptor rules, the entry-17 round trip, the entry-21 sum
    pitfall all need saying). The file went 789 -> 754 lines; the code shrank as promised,
    the prose that pins the float hazards is what remains.
24. **The `get_building_params` mis-unpack (spec 6a.4) was born dead AND born broken.**
    Repo-history check (2026-08-21): the method, the ten component-side attribute copies
    and the return/unpack order mismatch were all introduced together in commit
    `2f97b5f2` ("Refactoring building", PR #399, 2025-08-01) — the mismatch existed from
    the very first version, and `git log -S` shows no commit ever touched the method
    again. Before #399 the component's `build()` had no such copy block under any name
    (it went straight to `get_conductances()`), and at #399 every real read of those
    attribute names sits inside `BuildingInformation`, whose own attributes they shadow.
    So the copies were never consumed by anyone at any point in history: the refactor
    added a mirror block the component never needed, swapped the tuple order in the
    same commit, and no test or reviewer could ever notice because the values were
    write-only. Strengthens the 6a.4 deletion (no historical consumer exists) and is
    the cleanest possible specimen of the tuple-unpack hazard class: the bug survived
    ~13 months precisely because it was harmless.
25. **Layer 2's bitwise comparison did not survive contact with CI (fixed 2026-08-21).**
    The day after the phase-1 merge, the one-day snapshot failed on the ubuntu-latest
    runner: 1-ULP differences on 6–8 daylight timesteps of the trig-derived columns
    (SolarGainThroughWindows and the heat fluxes computed from it), while OpenWindow,
    the temperatures and every pure-arithmetic column matched exactly. Root cause:
    `math.cos` (window model) and pvlib's numpy trigonometry bind to the platform's
    libm, and this box's glibc 2.43 rounds those transcendentals' last bit differently
    than the runner's older glibc — package versions were verified identical (pvlib
    0.15.2 is the newest; numpy is forced <2 by oemof-solph on both sides). Fix: layer 2
    compares floats up to `PLATFORM_ULP_TOLERANCE` (4 ULPs of the larger operand) —
    verified to accept all eight CI-observed pairs and still reject sign flips and
    1e-9-relative changes. Layer 1 (pure IEEE arithmetic, bit-exact on every platform)
    stays strictly bitwise and remains the referee for ULP-level arithmetic changes;
    the `sum()`-vs-chained-`+` and door-round-trip catches were layer-1 catches and are
    unaffected. Lesson recorded: bitwise goldens are portable exactly as far as the
    computation avoids transcendentals; the moment libm enters, the honest cross-platform
    contract is a stated ULP band.
