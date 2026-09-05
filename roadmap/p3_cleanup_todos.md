# P3 — what is still open before and after the stack merges

**Date:** 2026-09-05 · **Owner:** Noah Pflugradt
**Context:** #598 was split into seven stacked PRs (`p3_identifier_names` → `p3_declarative_fixes` →
`p3_recordable_components` → `p3_recorder_core` → `p3_recorded_fleet` → `p3_parity_rig` →
`p3_grouping_pass`), each based on the previous branch and merged in that order. This file collects
everything the split, the spec check of 2026-09-05 and the golden-coverage work left open, so nothing
survives only in a conversation. Items are removed when done, not ticked and kept.

## Missing deliverables (code)

- [x] **The golden-YAML gate (AC-P3.2, R3.1) — shipped 2026-09-05 on `p3_recorded_fleet`.**
  `golden-yaml-check.yml` runs the golden setups from their recorded twins through the executor
  against the unchanged `golden_references/`, with the port-named EMS priority KPIs excluded on
  both comparison sides by a declared pattern (`PORT_NAMED_KPIS`, unit-tested as load-bearing).
  Merges with the stack; remove this entry once it is on main.
- [x] **Golden coverage stage 2: the last two setups — complete, 22 of 22 (2026-09-05, pending
  merges).** A fresh scan showed five of the seven 2026-08-28 repair items healed themselves — the
  toys via #616's models-no-device answers, the heating-only and air-conditioner crashes gone, and
  `household_gas_solar_thermal`'s doubled grid import fixed by #617 (verified: grid import equals
  consumption to the watt-hour) — so all five are in the week gate with fresh blessings. The last
  two followed: `dynamic_components` via the CHP KPIs (`chp_kpis` branch; its CHPs idle through the
  probe window, so their KPIs are honest zeros) and `electrolyzer_with_renewables` via the
  transformer/rectifier and electrolyzer KPIs (`electrolyzer_setup_kpis` branch). Both are blessed
  and gated on `golden_gate_full_fleet`, which merges after #639 and the two KPI PRs. Every setup
  in the repository is now golden-gated at week resolution or better.
- [ ] **Multi-instance KPI collision (found 2026-09-05 while implementing the CHP KPIs).** Two
  components of one class in one building collapse into a single flattened KPI group: the two
  batteries of `dynamic_components` report one `BUI1.Battery.*` set, one instance silently
  overwriting the other, and the two CHPs now do the same. Pre-existing and systemic — the flatten
  key is building.tag.name and ignores the entry's source component. Fixing it renames KPIs and
  therefore re-blesses references; its own PR, after the gate has settled.

## Documentation drift (fix on the stack branches before their PRs merge)

- [ ] **`plan.md` §P3 checkboxes are stale.** The work is done but unchecked: rename+enforce, the
  parity port, the recorder, the fleet recorded, parameter dedup (R8), `one_week_july`, the rig, the
  structural verdicts, the heat-pump grouping. Tick them with dates; tick "implementation spec accepted
  at review" when the stack review concludes.
- [ ] **`p3_recording_requirements.md` R5.2 / AC-P3.1 / AC-P3.11 contradict reality.** The plan carries
  the 2026-09-02 amendment (two setups retired independently by #596, `air_conditioned_house` stays
  because #605 removed the cache wipe, the fleet is twenty-two) but the requirements doc still says all
  three are deleted and counts 21. Amend the three places.
- [x] **The rig's own files contradict R11.8 — fixed 2026-09-05 on `p3_parity_rig`** (the #637
  review round found all seven stale headers, not two): every rig file and the workflow now say
  phase P6, the workflow's deletion list gained `p3_parity_verdicts.py`, and the coverage counts
  read 22 setups / 44 triples. Remove this entry once #637 is on main.

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
- [ ] **Flip `energy-system-freshness` from advisory to blocking after its burn-in** (decided
  2026-09-05, #636 review round). The gate merges with `continue-on-error: true` because
  byte-identical re-recording is proven by test for one setup and only by design for the other
  twenty-one; after a week of green runs on main, delete that one line (the workflow header
  carries the same instruction) and the gate blocks. This subsumes the AC-P3.4 item above — the
  first green run is the evidence, the week of them is the confidence.

## Deferred by design (not P3's debt, listed so it is findable)

- P6 tears down the parity rig (R11.8 amended; AC-P3.20 moved there) and decides which setups the rig's
  evidence promotes into the permanent full-year gate.
- v1 scenario JSONs, `json_executor.py` and `scenario-json-freshness.yml` retire in P5 (Q-P3.4).
- The `cars` field on `UtspLpgConnectorConfig` is dead (declared, read by nothing) — removal is a small
  serialization change with a scenario regeneration, noted 2026-09-05 during the #625 review.
