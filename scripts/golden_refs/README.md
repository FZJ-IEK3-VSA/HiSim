# Golden Reference Regression Gate

## Overview

The golden-reference system is a regression gate for HiSim simulation outputs.
It captures a known-good **snapshot** of a curated set of
`(setup, parameter_set)` runs and re-runs the same pairs later, asserting that
every observable artifact (CSV, JSON, image, and binary file) is unchanged.

It is **not** part of the fast `pytest -m base` gate. Run it locally or as a
separate CI job when you want to detect unintended changes to simulation
results (for example, after a numerical refactor or a post-processing change).

Two scripts drive it:

- `scripts/golden_update.py` — (re)generates the snapshot. Run deliberately,
  after reviewing the resulting diff.
- `scripts/golden_check.py` (via `scripts/golden_check.sh`) — re-runs the same
  pairs and compares every artifact against the snapshot, writing a deviation
  report and exiting non-zero on any difference.

The snapshot lives under `results/golden_references/` and is **not** committed
to git — `results/` is gitignored. The snapshot is uploaded to GitLab as a CI
artifact and retrieved on demand.

## Prerequisites

- `Pillow` (the `PIL` import) — used by `scripts/compare.py` for pixel-exact
  image comparison (`compare_image` loads both images with `PIL.Image.open`,
  converts them to numpy arrays, and compares the pixel data).
- `numpy` — already a declared HiSim dependency.

Both are listed in `requirements.txt`.

## Config file

`scripts/golden_config.json` is the single source of truth for what runs. It
lists the curated **setups** and **parameter sets**:

- Each **setup** has an `id` (stable identifier) and a `path` (repo-relative
  path to a `system_setups/*.py` file exposing `setup_function`).
- Each **parameter set** has an `id`, a `factory` (the name of a
  `SimulationParameters` classmethod such as `one_day_only`), a `year`, a
  `seconds_per_timestep`, and a list of `post_processing_options` — each an
  `PostProcessingOptions` member **name** (e.g. `"PLOT_CARPET"`,
  `"COMPUTE_KPIS"`, `"EXPORT_TO_CSV"`), never an invented boolean flag.
- An optional `nondeterministic: true` flag on a parameter set marks its
  result as "advisory" — it is still compared, but a mismatch does not fail
  the gate (for setups that depend on wall-clock time or non-deterministic
  ordering).

The checker pairs every setup with every parameter set (a Cartesian product).
To add or change a setup or parameter set, edit the JSON; no Python changes
are needed. If the config changes, the snapshot must be regenerated (see
"Config drift" below).

## Generating a snapshot

```bash
python scripts/golden_update.py
```

This runs every `(setup, parameter_set)` pair and writes the full artifact set
plus a `manifest.json` into `results/golden_references/`:

```
results/golden_references/
    manifest.json
    <setup_id>/<param_id>/   # one directory per pair, containing all run output
```

The `manifest.json` records the HiSim git commit, Python version, platform, a
SHA-256 of `golden_config.json`, an ISO-8601 timestamp, and a per-pair
artifact inventory (relative path, SHA-256, size, kind). `golden_update.py`
**overwrites** any previous snapshot and never reads it — it is the "bless new
golden" button, run deliberately.

Optional flags:

- `--config PATH` — path to an alternative config file (defaults to
  `scripts/golden_config.json`).
- `--results-root PATH` — override the `results/` root.
- `--repo-root PATH` — override the repository root used to resolve setup
  paths.

## Checking against a snapshot

```bash
bash scripts/golden_check.sh
# or equivalently:
python scripts/golden_check.py
```

This:

1. Loads `golden_config.json` and the snapshot's `manifest.json` (fails hard
   if either is missing).
2. Hard-fails if `manifest.json`'s `config_sha256` does not match
   `sha256(golden_config.json)` — the snapshot was generated with a different
   config and the comparison would be meaningless. Regenerate the snapshot with
   `golden_update.py` after changing the config.
3. Warns (but proceeds) if `manifest.json`'s `hisim_commit` differs from the
   current commit.
4. Re-runs every `(setup, parameter_set)` pair into
   `results/golden-ref-check/<setup_id>/<param_id>/` (ephemeral; never writes
   to the snapshot directory).
5. Compares every artifact recursively against the snapshot:
   - **CSV** — row count, column set, and cell values (numeric cells use
     `math.isclose` with `REL_TOL=1e-6, ABS_TOL=0.0`; non-numeric cells use
     exact string equality).
   - **JSON** — deep equality (key order invariant).
   - **Image** (`.png`/`.jpg`/`.jpeg`) — identical mode and size, then
     pixel-array comparison via Pillow + numpy.
   - **Other** (`.pkl`, `.log`, `.txt`, `finished.flag`, …) — byte-identical
     SHA-256.
   Missing-in-snapshot files are reported as `missing`; fresh-only files as
   `unexpected`.
6. Writes `results/golden-ref-check/report.txt` (human-readable) and
   `report.json` (machine-readable).
7. Exits `0` if every pair is identical, `1` if any artifact diverged, is
   missing, or is unexpected.

Flags:

- `--clean` — remove `results/golden-ref-check/` after a successful (passing)
  run. By default the directory is left in place for inspection.
- `--image-tolerance PCT` — allow a fraction of differing pixels per image
  (default `0.0` = strict pixel-exact). Useful on heterogeneous CI runners
  where font/FreeType differences cause sub-pixel drift. A value of `1.0`
  allows up to 1% of pixels to differ.
- `--config PATH` — path to an alternative config file.
- `--golden-root PATH` — override the snapshot root
  (defaults to `results/golden_references`).
- `--check-root PATH` — override the ephemeral fresh-run root
  (defaults to `results/golden-ref-check`).

The snapshot directory (`results/golden_references/`) is **never** written to
by the checker.

## Interpreting the report

`report.txt` is human-readable; `report.json` is machine-readable (a
`dataclasses.asdict` dump of the `ComparisonReport`). Each
`(setup, parameter_set)` pair lists its artifacts and any deviations:

- **CSV** — differing cell count plus the first 10 differing cells (row index,
  column name, expected vs. actual).
- **Image** — differing pixel count, bounding box `(left, top, right, bottom)`,
  and the path to a side-by-side diff image written under
  `results/golden-ref-check/diffs/`.
- **JSON** — the JSON path of the first divergence.
- **Other** — expected vs. actual SHA-256.
- **missing** — the file exists in the snapshot but not in the fresh run.
- **unexpected** — the file exists in the fresh run but not in the snapshot.

The final summary line reads `PASS` or
`FAIL: N artifacts diverged across M setups; K missing/unexpected`.

A pair whose fresh run raised an exception is reported with the traceback and
treated as a failure (the snapshot had artifacts, the fresh run produced none).

## When to regenerate

Run `golden_update.py` after an **intentional** change to simulation outputs —
for example a refactor that changes numerical results, a new post-processing
option, or an updated input data set. Review the resulting diff before
blessing the new snapshot. **Never** edit input CSVs to make comparisons pass;
fix the Python side or update the snapshot deliberately.

## Config drift

`golden_config.json` is hashed into `manifest.json` as `config_sha256`. If the
config changes (a setup or parameter set is added, removed, or altered),
`golden_check.py` will hard-fail with a config-hash mismatch because the
existing snapshot no longer matches the config. Regenerate the snapshot with
`golden_update.py` after changing the config.
