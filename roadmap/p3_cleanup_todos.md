# P3 — what is still open before and after the stack merges

**Date:** 2026-09-05 · **Updated:** 2026-09-19 · **Owner:** Noah Pflugradt
**Context:** #598 was split into seven stacked PRs (`p3_identifier_names` → `p3_declarative_fixes` →
`p3_recordable_components` → `p3_recorder_core` → `p3_recorded_fleet` → `p3_parity_rig` →
`p3_grouping_pass`), each based on the previous branch and merged in that order — all seven are on
main as of 2026-09-07, with the follow-up rounds #644–#649 merged. This file collects everything
the split, the spec check of 2026-09-05 and the golden-coverage work left open, so nothing survives
only in a conversation. Items are removed when done, not ticked and kept.

## Missing deliverables (code)

- [ ] **Stable KPI addresses.** Keys are still volatile (bare unless a collision exists) and
  consumers rebuild key strings by hand. Spec: `roadmap/kpi_address_spec.md`; its own PR after #653
  is on main.


## Decision needed (owner)

*Nothing open.*

## Operational, after the stack merges

- [ ] **Delete the `json_v2` spike branch** (local and origin) — the parity/templating halves are
  ported. (#598 itself was closed 2026-09-07.)
## Deferred by design (not P3's debt, listed so it is findable)

- P6 tears down the parity rig (R11.8 amended; AC-P3.20 moved there) and decides which setups the rig's
  evidence promotes into the permanent full-year gate.
- ~~v1 scenario JSONs, `json_executor.py` and `scenario-json-freshness.yml` retire in P5 (Q-P3.4).~~
  **Done 2026-09-12**, in P4 rather than P5 (owner): retired together with `hisim_convert_to_json.py`,
  `scripts/regenerate_scenario_jsons.py` and `golden-json-check.yml`, because every setup has a
  recorded twin and the YAML gates cover what the JSON pair covered, at ~48 CI minutes per PR less.
- The `cars` field on `UtspLpgConnectorConfig` is dead (declared, read by nothing) — removal is a small
  serialization change with a scenario regeneration, noted 2026-09-05 during the #625 review.

## Done and removed (dates only, so the removals are auditable)

Golden-YAML gate and full-fleet golden coverage: on main with #636/#642. plan.md §P3 ticks: #644.
Rig headers say P6: #637. Channel migration: #645. Requirements doc R5.1/R5.2/R5.3, AC-P3.1,
AC-P3.10, AC-P3.11 and Q-P3.5 amended to the twenty-two-setup reality: 2026-09-07, this commit.
The #625 fleet re-record: satisfied by #636 recording all twenty-two after #625, first freshness
run green on main 2026-09-06. AC-P3.4 evidence: same run. #598 closed: 2026-09-07.
Grouping coverage, the one decision this file was holding for the owner: decided 2026-09-07 to
group inside P3 rather than amend the spec, and done the same day. All thirteen
configuration-driven setups now carry a probe list, a grouping table and a grouped file, and every
probe column of every one of them reproduces its flat recording byte for byte; the nine
single-configuration setups stay flat by decision. State page:
`roadmap/declarative_energy_systems/grouping_overview.md`; what each setup cost and what it
turned up: `roadmap/declarative_energy_systems/grouping_worklist.md`.

The freshness gate flipped to blocking: 2026-09-08, #654 — eleven green runs on main
across 2026-09-06..08 (one cancelled by a newer push, none failed), which the owner judged
sufficient to grant the bit ahead of the nominal week.

Four operational items removed 2026-09-19, each verified rather than assumed. The **fleet-wide
parity dispatch** (AC-P3.17) ran on 2026-09-19 against main at `dd6cb78b`: twenty-two triples,
twenty-two green, exact equality with no slack, and the run before it on 2026-09-18 was green too.
The entry expected the table to record "the known state for the seven KPI-broken setups" -- there
are none left, and the baseline is full parity across the fleet. The evidence is recorded where P6
will use it, in `plan.md`. The **Electrolyzer and Transformer cost models** both exist
(`get_cost_opex`/`get_cost_capex` on `generic_electrolyzer_h2.Electrolyzer` and
`transformer_rectifier.Transformer`), and `electrolyzer_with_renewables` runs `COMPUTE_OPEX`,
`COMPUTE_CAPEX` and `COMPUTE_KPIS` to completion, which is the failure that entry described. The
**shared child-recorder helper** is `hisim/energy_system/recording/child_recorder.py`, used by the
fleet driver and the probe runner alike. The **#649 retarget** is moot: #648 and #649 are both
merged and closed.
