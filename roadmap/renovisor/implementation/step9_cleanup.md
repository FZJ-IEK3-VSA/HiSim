# Step 9 — make PR #778 green and tidy

**Status:** implementation specification, 2026-09-19
**Context:** PR #778 (`renovisor-backend` → `main`, HEAD `a9c983c8`). The golden gates pass; three
quality jobs fail: `mypy` (the `tests/` run with `mypy_moderate.ini`), `pylint`
(`pylintrc-critical-only` over `git ls-files '*.py'`) and `prospector` (`.prospector.yml`,
strictness veryhigh, doc warnings on). The owner also asked for the PR to be tidied: the
RenoVisor tests into their own directory, and whatever else a reviewer would trip over.

Ground rules as in step 3 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, interpreter and
`PYTHONPATH`, no commits, class-scope constants, docstrings, 120 columns). Nothing in
`hisim/renovisor/` changes behaviour in this step: no test may change what it asserts, only
where it lives and how it is spelled. If a lint fix would require a behaviour change, stop and
report it instead.

---

## 1. The three quality jobs, reproduced locally

Run exactly these and make each clean for the files this PR touches:

```
mypy -p hisim --config-file mypy.ini                         # already clean
mypy --config-file mypy_moderate.ini system_setups/          # already clean
mypy --config-file mypy_moderate.ini tests/                  # 4 errors: occupancy:92, tabula:31, map:109, cli:204
pylint --rcfile pylintrc-critical-only $(git ls-files '*.py')
prospector --profile .prospector.yml <every .py file this PR adds or changes>
```

Known findings and the fix each wants:

- **`apply.py` W0212 protected-access** (21 lines): the registry's dispatch table names the
  measure functions with a leading underscore and then reads them through the class. Make the
  functions public (drop the underscore; they *are* the registry's public surface, one per
  catalogue measure) or build the table inside the class body so no external access happens.
  Keep the docstrings.
- **W0621 redefined-outer-name** in `test_renovisor_capabilities.py` (`document`),
  `test_renovisor_map.py` (`page`): the repository's convention is
  `@pytest.fixture(name="document")` on a function called `fixture_document`, so the test
  argument does not shadow a module-level name. Apply it.
- **W0404 reimport `Probe`**, **W0613 unused arguments** (`test_renovisor_cli.py:31`,
  `test_renovisor_costs.py:223, 272`; drop the argument or name it `_`), **C1803**
  (`== ()` → `not …`), **W0212 `_actions`** in `test_renovisor_cli.py:203` (use argparse's public
  surface: `parser.parse_args` on each command or `parser.format_help()`).
- **mypy on tests**: `occupancy:92` (`str | None` in `in`), `tabula:31` (`requested_code: str =
  None` → `Optional[str]`), `map:109` (a `KpiField`/`CostField` union variable; annotate the
  variable), `cli:204` (`set(x or ())`).
- **prospector pydocstyle** D205/D209/D400/D415 on multi-line test docstrings: summary line ending
  in a period, blank line, closing quotes on their own line. Fix every message prospector reports
  on the PR's files; run it file by file if the whole-tree run is slow.

## 2. The tests move to `tests/renovisor/`

The directory exists and holds two orphaned fixtures (`example_inventory_ie_1988_detached.json`,
`example_package_gas_to_heat_pump.json`; nothing references them since the vendored mockup became
the fixture — verified by grep). Do:

- Delete the two JSON files.
- Add `tests/renovisor/__init__.py` (the `tests/components/` precedent; `tests/__init__.py`
  exists, so module names stay unique as `tests.renovisor.test_*`).
- `git mv tests/test_renovisor_<name>.py tests/renovisor/test_<name>.py` for all fifteen
  (`apply, capabilities, cli, contract, costs, envelope, kpis, map, occupancy, provenance,
  request, run, tabula, translate, vocabulary`). `tests/test_placeholder_country_factors.py` is a
  component test, not a RenoVisor one: `git mv` it to
  `tests/components/test_configuration_placeholder_country.py`.
- Fix every path the moved files compute (`Path(__file__).resolve().parent.parent` becomes
  `.parent.parent.parent`, and so on; grep for `__file__` in the moved tests).
- Update every reference to the old names: `CLAUDE.md` (three lines), `simulation_issues.md`
  (two), the docstrings in `hisim/renovisor/vocabulary.py`, `occupancy.py`, `map.py`, `kpis.py`,
  `contract/__init__.py`, `contract/refresh.py` (its `PINNED.yaml` header string — then re-run
  `python -m hisim.renovisor.contract.refresh ~/renovisor-api-contract` so `PINNED.yaml` is
  regenerated rather than hand-edited; the hashes must not change, only the header comment), and
  anything else `grep -rn test_renovisor` finds outside `roadmap/`.
- `pytest tests/renovisor -q` and `pytest -m base tests/renovisor -q` both collect and pass; the
  `system_setups`-marked run tests still carry their marker (CI runs one job per marker).

## 3. Documentation and packaging

- `docs/modules/renovisor.rst` describes the deleted steps-3–6 modules (`catalogue`, `options`,
  `registry`, `effects`, `materials`, `inventory`, `application`, `base_files`, `tabula_ie`,
  `materials_import`). Rewrite it for v2: the pipeline in one paragraph (validate → apply →
  translate → run → results, plus capabilities), then one `automodule` block per current module
  (`request, apply, envelope, constants, tabula, translate, whitelist, report, simulation, run,
  kpis, costs, provenance, result, layers, capabilities, map, occupancy, vocabulary, contract,
  contract.refresh`). Build the docs locally, `python -m sphinx -b html docs <scratch>`, and make
  sure no warning mentions `renovisor` (the job does not run with `-W`, but a stale reference is
  exactly what a reviewer notices).
- `setup.py` `package_data`: `renovisor/data/*.json` names a directory that no longer exists;
  replace by what the package now ships: `renovisor/contract/*.yaml`, `renovisor/contract/*.json`,
  `renovisor/not_implemented_yet.yaml`, `renovisor/how_to_use.md`. Check each glob matches at
  least one file.
- `hisim/renovisor/how_to_use.md`: read it once against the current CLI (`run | translate |
  validate | capabilities`, exit codes, the whitelist rule) and fix anything stale.

## 4. `roadmap/renovisor/` — what a reviewer should not have to wade through

- Delete `contract_rewrite.diff` (2 094 lines, the diff of a superseded contract branch) and the
  `mockups/` directory (`heat_pump_household.energy_system.yaml` and `measures.schema.yaml` are the
  August design that the catalogue superseded; their footer decisions are recorded in
  `challenges.md`). Git history keeps both.
- Add a one-line "**Superseded** by the adoption of the frontend side's spec (challenges.md §13);
  kept for the reasoning" note at the top of `contract_rewrite_proposal.md` and of
  `implementation/step7_contract_pr.md`.
- Add `roadmap/renovisor/README.md`: a reading order for a reviewer. *Current:* `challenges.md`
  (§13 first, then §12, §9), `review_translator_spec_v2_2026-09-19.md`, `implementation/
  step8_reconciliation.md` (+ its addendum), `translation_map.html`, the vendored F-spec files under
  `hisim/renovisor/contract/`. *History, in order:* `requirements.md`, `requirements.discussion.md`,
  `field_inventory.md`, `measures_v2_requirements.md`, `implementation/step3…step7`,
  `contract_rewrite_proposal.md`. One sentence each on what the document is for.
- Fix any link in the remaining documents that pointed at a deleted file (grep for `mockups/`,
  `contract_rewrite.diff`, `tests/test_renovisor`, `tests/renovisor/example_`).

## 5. Done means

`pytest tests/renovisor tests/components/test_configuration_placeholder_country.py -q` green (the
`system_setups` runs included); the three mypy commands clean; `pylint --rcfile
pylintrc-critical-only $(git ls-files '*.py')` reports nothing for the PR's files; prospector
reports nothing for the PR's files; the docs build without a renovisor warning; the map is
current (`pytest tests/renovisor/test_map.py`); `git status --porcelain` shows only intended
changes and no result directories. Final report: every command with its result, the list of moved
and deleted files, and anything you could not make clean without a behaviour change.
