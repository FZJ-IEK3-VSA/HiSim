# P3 — what is still open before and after the stack merges

**Date:** 2026-09-05 · **Owner:** Noah Pflugradt
**Context:** #598 was split into seven stacked PRs (`p3_identifier_names` → `p3_declarative_fixes` →
`p3_recordable_components` → `p3_recorder_core` → `p3_recorded_fleet` → `p3_parity_rig` →
`p3_grouping_pass`), each based on the previous branch and merged in that order. This file collects
everything the split, the spec check of 2026-09-05 and the golden-coverage work left open, so nothing
survives only in a conversation. Items are removed when done, not ticked and kept.

## Missing deliverables (code)

- [ ] **The golden-YAML gate (AC-P3.2, R3.1).** A blocking workflow `golden-yaml-check.yml` that runs
  the golden setups *from their recorded twins* through `hisim energy-system run` against the unchanged
  `golden_references/`. The stack ships the freshness gate and the hand-dispatched parity rig, but not
  the blocking golden gate on the YAML path — and "golden suites green on recorded files" is P3's exit
  criterion in the plan. Builds on `p3_recorded_fleet` (needs the twins); the week references for the
  whole fleet come from the `golden_week_coverage` branch, so the gate covers 15 setups from day one.
- [ ] **Golden coverage stage 2: the KPI repair list becomes coverage work.** Each fix joins the setup
  to the golden week gate with a fresh blessing in the same PR: `household_gas_solar_thermal` (grid
  import 21.72 kWh above total consumption 10.9 kWh — an energy-balance defect worth fixing regardless;
  fix BEFORE blessing, never bless the wrong numbers), `basic_household_only_heating` (`NoneType *
  float` in KPI computation), `dynamic_components` and `electrolyzer_with_renewables` (CHP1 and
  transformer/rectifier have no KPI method), `simple_air_conditioner_household_building_sizer`
  (division by zero on a January window). Decide the two toys (`simple_system_setup_one`/`_two`):
  trivial KPI methods so all 22 are gated, or the plan's "exclude rather than implement" and 20.

## Documentation drift (fix on the stack branches before their PRs merge)

- [ ] **`plan.md` §P3 checkboxes are stale.** The work is done but unchecked: rename+enforce, the
  parity port, the recorder, the fleet recorded, parameter dedup (R8), `one_week_july`, the rig, the
  structural verdicts, the heat-pump grouping. Tick them with dates; tick "implementation spec accepted
  at review" when the stack review concludes.
- [ ] **`p3_recording_requirements.md` R5.2 / AC-P3.1 / AC-P3.11 contradict reality.** The plan carries
  the 2026-09-02 amendment (two setups retired independently by #596, `air_conditioned_house` stays
  because #605 removed the cache wipe, the fleet is twenty-two) but the requirements doc still says all
  three are deleted and counts 21. Amend the three places.
- [ ] **The rig's own files contradict R11.8.** `p3-parity.yml`'s header and `p3_parity_verdicts.py`'s
  docstring still say "deleted in P3's last PR" and cite AC-P3.20; R11.8 was amended 2026-08-31 to move
  the teardown to P6 and commit `403c8a34` updated the roadmap but not these two headers. Two-line fix
  on `p3_parity_rig`.

## Decision needed (owner)

- [ ] **Grouping coverage: 1 of 12 sizers is grouped.** G5/R10 read as one human judgement per
  module-config setup; the stack ships the pass proven on the heat-pump sizer only. Either group the
  remaining 11 inside P3 (11 probe lists plus table judgements), or amend the spec: the pass ships in
  P3, each remaining sizer is grouped when P5 prepares its base file. Leaning: the amendment — P5 is
  the consumer, so the judgement stays fresh and reviewable per file.

## Operational, after the stack merges

- [ ] **Dispatch the parity rig once over the whole fleet** (AC-P3.17) and keep the verdict table as
  the baseline — the "known state" for the seven KPI-broken setups.
- [ ] **When #625 (weather identity) merges: one fleet re-record plus one scenario regeneration.** Both
  freshness gates will name exactly what moved; commit the artifacts.
- [ ] **Retarget each stacked PR to `main` as its predecessor squash-merges** (`gh pr edit <n> --base
  main`), or delete merged branches so GitHub retargets automatically.
- [ ] **Close #598** with a comment pointing at the stack; **delete the `json_v2` spike branch** (local
  and origin) — the parity/templating halves are ported.
- [ ] AC-P3.4's cross-machine byte-identity is proven the first time CI's `energy-system-freshness`
  job goes green on twins recorded on the development box; note the date here and drop the item.

## Deferred by design (not P3's debt, listed so it is findable)

- P6 tears down the parity rig (R11.8 amended; AC-P3.20 moved there) and decides which setups the rig's
  evidence promotes into the permanent full-year gate.
- v1 scenario JSONs, `json_executor.py` and `scenario-json-freshness.yml` retire in P5 (Q-P3.4).
- The `cars` field on `UtspLpgConnectorConfig` is dead (declared, read by nothing) — removal is a small
  serialization change with a scenario regeneration, noted 2026-09-05 during the #625 review.
