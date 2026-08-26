# Template-workflow spike — agent workplan

Mission (approved 2026-08-20): drive the **household heat pump example** through the entire
intended v2 workflow — template JSON (grouped connections, `"AUTO"`, entry-level presets)
→ v2 executor sizes and builds → simulation → fully realized audit JSON in the results
directory → the audit JSON re-loads and builds the identical system. **No repo-wide
switchover**: only the format features and the example's own components are touched.
The point is to put the two artifacts (template + realized record) in front of the team
before deciding anything repo-wide.

Branch: `json_v2`. Each step below is its own commit (or few) — these are the PR seams.
Never push; the user pushes from another machine.

## Steps (sequential; one agent each)

1. **Grouped connections (spec decision 20).** Parse-time normalization of the mapping
   spelling into the flat entries in `hisim/scenario_v2/schema.py`; hard errors per the
   decision; checked-in v2 fixtures converted to grouped spelling; spec status updated.
2. **Design-B conversion of the example's thermal chain.** Sized fields + presets +
   `SIZING_CONTRIBUTIONS` for the example's sizable components (HDS controller, heat pump
   + its two controllers, hot-water storages; PV/battery only if the legacy scaled
   factories already encode a defensible rule — never invent physics). Straight cutover of
   the touched classes' old factories, same policy as the boiler/HDS pilots.
3. **Decision 21 — entry-level `preset` field (executor half).** Spec text + schema +
   executor: `"preset": "<name>"` next to `"class"`, sparse `config` overrides laid over a
   fresh `Catalog` instance, identity injected, remaining AUTO flows to the sizing engine.
4. **Decision 21 — creator half.** `Catalog` stamps preset provenance; the template
   creator diffs against the preset baseline and emits `preset` + sparse overrides +
   `"AUTO"`; generate the canonical heat pump template with it and check it in.
5. **§8.3 audit dump.** Fully realized scenario JSON written into the results directory
   on v2 runs (HiSim version + git commit embedded, paths symbolized, per-field sizing
   provenance; values all concrete — presets expanded).
6. **Closing-the-loop test.** Template → resolve → one-day simulation → audit dump →
   reload the dump → identical system. This test *is* the workflow's promise.

## Canon and reference files (read before coding)

- `system_docs/json_scenario_v2_spec.md` — format spec; §3.3 connection shapes,
  decisions 18/20 and the decision list style for adding 21.
- `system_docs/config_defaults_spec.md` — design B: `sized_field`, laws, `SizingContext`,
  engine §8.4, template workflow §8.1/§8.3.
- Pilots to imitate: `hisim/components/generic_boiler.py` (presets Catalog, per-preset
  law, contributions), `hisim/components/heat_distribution_system.py`,
  `hisim/components/building_config.py` (contributions), `hisim/sizing.py`,
  `hisim/sizing_engine.py`, `hisim/scenario_v2/` (schema, executor, templating,
  serialization), `tests/test_sizing*.py`, `tests/test_scenario_v2_templates.py`.
- The target look of the grouped format: `~/heatpump_house_v2_review.json`.

## Conventions (non-negotiable)

- **Docstrings:** every class and function gets a real docstring, ≥2–3 sentences of full
  purpose. Match the pilots' density.
- **No module-level constants or mutable module state** — class-scope constants; Enums
  for string sets.
- **Commits:** imperative subject, body explaining the why, ending with
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Never any session links.
- **Findings log:** append accidental discoveries to `roadmap/random_findings.md` using
  its existing `[bug]/[friction]/[spec]/[elegance]` legend. Full capture, not curation.
- **Spec upkeep:** the step that implements a decision updates the spec's status text in
  the same commit.
- Do not spawn subagents of your own; do not touch v1 executor/generator paths; do not
  convert components outside the example; never commit `.gitignore`'s local
  `local_python_env/` line or files under `roadmap/` unless your step says so.

## Gates (run before committing; all must be clean)

```bash
cd /home/noah/hisim/HiSim
local_python_env/bin/python -m pytest tests/test_scenario_v2_schema.py tests/test_scenario_v2_contract.py \
    tests/test_scenario_v2_templates.py tests/test_sizing.py tests/test_sizing_engine.py -q
# when component configs / setups changed, additionally:
local_python_env/bin/python scripts/check_config_attrs.py
local_python_env/bin/python -m pytest tests/test_component.py <affected component tests> -q
# quality, exactly as CI runs it (see .github/workflows/quality.yml for the invocations):
#   prospector on touched files, mypy per mypy.ini on touched files
# final broad check for the step:
local_python_env/bin/python -m pytest tests/ -q -m base
```

## Status

- [x] 1 grouped connections (commit 1bb93c6d)
- [x] 2 design-B thermal chain
- [x] 3 preset entries (executor) (commits a7bd51b3, cf641a30)
- [x] 4 preset entries (creator) + canonical template
- [x] 5 audit dump (commits 053b6ef2, ab2923b0)
- [x] 6 closing-the-loop test
