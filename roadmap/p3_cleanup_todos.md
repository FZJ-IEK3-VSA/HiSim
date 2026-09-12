# P3 — what is still open before and after the stack merges

**Date:** 2026-09-05 · **Updated:** 2026-09-07 · **Owner:** Noah Pflugradt
**Context:** #598 was split into seven stacked PRs (`p3_identifier_names` → `p3_declarative_fixes` →
`p3_recordable_components` → `p3_recorder_core` → `p3_recorded_fleet` → `p3_parity_rig` →
`p3_grouping_pass`), each based on the previous branch and merged in that order — all seven are on
main as of 2026-09-07, with the follow-up rounds #644–#647 merged and #648/#649 in review. This
file collects everything the split, the spec check of 2026-09-05 and the golden-coverage work left
open, so nothing survives only in a conversation. Items are removed when done, not ticked and kept.

## Missing deliverables (code)

- [x] **Multi-instance KPI collision — fixed 2026-09-07 on `kpi_multi_instance`.**
  `Component.component_kpi_entries` is now the method the collector calls: it asks the overridable
  `get_component_kpi_entries` and stamps every entry that names no source with the component's own
  name, so no component has to remember the field. `KpiPreparation.keyed_component_entries` then
  keys one building's entries: where several components share an entry name, each keys as
  `"<name> (<source component>)"`, a collision whose colliders do not all name a source is refused
  rather than silently overwritten, and one component emitting a name twice is refused too.
  `Building`'s duplicate emission is hoisted out of its per-output loop, the diesel car's two
  entries got distinct names, and the meter lookup in `read_opex_and_capex_costs_from_results`
  matches an entry's own `name` instead of the collection key and sums across the meters of a
  building, so qualification neither zeroes a general KPI nor lets one of two meters stand for
  both. Seven goldens re-blessed (`dynamic_components` plus the six setups whose collisions had
  been hiding a component). Remove this entry once it is on main.
- [ ] **Stable KPI addresses.** Keys are still volatile (bare unless a collision exists) and
  consumers rebuild key strings by hand. Spec: `roadmap/kpi_address_spec.md`; its own PR after #653
  is on main.


## Decision needed (owner)

*Nothing open.*

## Operational, after the stack merges

- [ ] **Dispatch the parity rig once over the whole fleet** (AC-P3.17) and keep the verdict table as
  the baseline — the "known state" for the seven KPI-broken setups. The dispatch covers the January
  window only while July is fenced (R11.5 as amended 2026-09-06; `roadmap/midyear_start_epic.md`).
- [ ] **Retarget #649 to `main` once #648 squash-merges** (`gh pr edit 649 --base main`), or delete
  the merged branch so GitHub retargets automatically.
- [ ] **Delete the `json_v2` spike branch** (local and origin) — the parity/templating halves are
  ported. (#598 itself was closed 2026-09-07.)
- [ ] **Give the Electrolyzer and the Transformer cost models** (noted 2026-09-05, #641 review
  round). A bare `hisim_main.py electrolyzer_with_renewables.py` falls back to
  `full_year_all_options`, and COMPUTE_OPEX/COMPUTE_CAPEX run before COMPUTE_KPIS — both
  components are real devices (MODELS_NO_DEVICE would be a lie) with no `get_cost_opex`/
  `get_cost_capex`, so the stock all-options run still dies before the new KPIs compute.
  Pre-existing, and the golden gate now runs COMPUTE_OPEX and COMPUTE_CAPEX as well (from
  `golden_gate_costs` on), so the building-level cost KPIs are pinned by the references; fixing it
  means real cost data for both devices, its own small PR.
- [ ] **Extract the shared child-recorder helper** (decided 2026-09-05, #638 review round; both
  #636 and #638 are on main, so this is unblocked). `scripts/record_all_setups.py::Recorder` and
  `hisim/energy_system/recording/probe_session.py::ProbeRunner` both build the identical child
  command (`-m hisim.cli energy-system record`), strip the same `HISIM_LOCAL_LPG_CALC_INDEX`
  variable and run the same subprocess shape — deliberate duplication while the two lived on
  different stack branches. The helper's home is the recording package, with the script importing
  it.

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
