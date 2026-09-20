# Step 14 — `measures.yaml`'s material names are authoritative; `materials.yaml` gains the class field

**Status:** implementation specification, 2026-09-20. **Owner decision (2026-09-20):** "keep the
names from `measures.yaml` as authoritative and fix `materials.yaml` in our copy for now while we
wait for the owners to clean up `materials.yaml`." This reverses the direction the note to the
contract owner (§1) and the frontend's local respelling took (todos C1, H20, F5): the `material`
option values of the 2026-09-17 catalogue (`EPS`, `EPS Foam`, `XPS`, `PIR`, `Mineral wool`,
`Mineral Wool`, `mineral wool`, `glass wool`, `wood fiber`, `open cell spray foam`, `Liquid
Insulation`) stay as they are, and every one of them resolves to exactly one `materials.yaml` row
through a new field on that row — the note's own second option ("offer material classes; the class
is a field on each row").

Ground rules as in step 10 §0 (worktree `/home/noah/hisim/HiSim-renovisor`, branch
`material-classes` off current `origin/main`, no commits, class-scope constants, plain complete
docstrings, 120 columns; flake8/mypy/pylint-critical/prospector clean on every touched file; the
three CI mypy runs; new tests `@pytest.mark.base`, run once from `tests/` as CI does). **Invent no
numbers**: the mapping of names to rows is the table of `note-to-contract-owner-2026-09-19.md` §1,
nothing else.

---

## 1. Where the fixed file lives

The shared folder is the single home of every spec the three agents share (its README). The fixed
`materials.yaml` goes there as `/home/contract-proposals/materials.yaml`, produced from the
contract repository's `origin/main` (`5181aa5452e5`) file by adding one field, so a diff against
the original shows nothing but the addition. HiSim's vendored copy
`hisim/renovisor/contract/materials.yaml` is refreshed **from the shared folder** from now on
(`ContractSources` in `hisim/renovisor/contract/refresh.py` gains the file, with the same
`source: /home/contract-proposals` pin the request schema has; the contract repository stays the
source of `measures.yaml`). `PINNED.yaml` carries a `note` on the entry: "local deviation from
`renovisor-api-contract@5181aa5`: rows carry `measure_material_values` so the `material` option
values of `measures.yaml` resolve to a row (owner decision 2026-09-20, pending the contract owner's
cleanup, todo C1)". The drift test in `tests/renovisor/test_contract.py` covers it like the others.

## 2. The field

On each `materials` row: `measure_material_values: [<exact strings as measures.yaml spells
them>]`, empty list on rows no measure names. The eight rows and their values, from the note:

| `asp_id` | `measure_material_values` |
|---|---|
| `polystyrene_eps_rigid_board` | `EPS`, `EPS Foam` |
| `extruded_polystyrene_xps` | `XPS` |
| `pir` | `PIR` |
| `stone_wool_flexible_insulation_blankets` | `Mineral wool`, `Mineral Wool`, `mineral wool` |
| `metac_glasswool` | `glass wool` |
| `wood_fiber_rigid_board` | `wood fiber` |
| `open_cell_spray_foam` | `open cell spray foam` |
| `liquid_insulation` | `Liquid Insulation` |

Spelling variants are kept as given: `measures.yaml` is authoritative, and folding them is the
contract owner's job (C1 stays open with that wording). A `vocabularies` entry
`measure_material_values` is **not** added (the vocabulary is `measures.yaml`'s option values).

## 3. HiSim code

- `hisim/renovisor/request.py` `CatalogueTable` (or wherever the frozen catalogue table lives)
  gains `material_row_for(value) -> str` returning the `asp_id` for a `material` option value,
  built from the vendored `materials.yaml`; refuse at import/build time (a translator build
  failure, exit 3 class) if any `material` value of any measure resolves to zero or to several
  rows. Nothing in the request path changes: the frontend keeps copying the row's properties and
  the `asp_id` stays provenance (rule 5). The mapping report's material note may name the
  resolved row beside the value — optional, say what you did.
- The capability document keeps announcing the `measures.yaml` values under `values`; no change.
  `ProbeSet.MATERIAL` keeps its `asp_id`/properties object.
- Tests: every `material` value of `measures.yaml` resolves to exactly one row (`base`); the
  vendored `materials.yaml` equals the shared one (drift test); a value the table does not know is
  refused by name; the eight rows carry exactly the values of §2.

## 4. Shared folder bookkeeping (re-read immediately before each edit; one edit per file)

- `todos.md`: H20 is **superseded** — rewrite its heading to `[x]` with a dated note stating the
  owner decision and what landed; C1 gets a dated note: the request to the owner is now "add
  `measure_material_values` (or an equivalent class key) to `materials.yaml` rows and fold the
  spelling variants; `measures.yaml`'s names stay"; F5 gets a note; add **F12** for the frontend
  agent: resolve a `material` option value to its row through `measure_material_values` in the
  shared `materials.yaml`, drop the local `asp_id` respelling of the catalogue (rule V5 becomes
  "resolves to exactly one row by `measure_material_values`"), and re-run the coverage check
  against the served document, which already announces these names.
- `note-to-contract-owner-2026-09-19.md` §1: replace the "Requested change" with the class-field
  request and keep the table as the mapping the owner should confirm.
- `calculation-request.md`: where it says material values are `asp_id`s (search `asp_id` and
  rule V5 / §4.3), one sentence correcting it.

## 5. Done means

`tests/renovisor` green (base), the three CI mypy runs, pylint critical-only and prospector clean on
every touched file; `python -m hisim.renovisor capabilities --out /tmp/x.json` still writes the same
`material` values; the final report lists the rows changed, the refresh-script change, every shared
edit (quote the new sentences) and anything left undone.
