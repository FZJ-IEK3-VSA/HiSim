# AGENTS.md

## What HiSim is

ETHOS.HiSim (Household Infrastructure and Building Simulator) is a Python package for time-step simulation of household energy systems — it models electricity consumption, heating demand, PV generation, heat pumps, batteries, EVs, hydrogen systems, and more. A simulation is a set of components wired together, iterated over every time step until each step's outputs converge, followed by post-processing (KPIs, plots, CSV/JSON/PDF reports). The top-level layout is: `hisim/` (the package — simulator core, the `Component` model, `components/`, `postprocessing/`, input data under `hisim/inputs/`); `system_setups/` (one `setup_function(sim, sim_params)` per `.py` file — the imperative input — plus the shared `.simulation.json` parameter files); `energy_systems/` (the declarative input: `*.energy_system.yaml`, one recorded twin per setup, plus `*.simulation.yaml`); `tests/` (pytest suite, organized by marker); and `hisim/hisim_main.py` (the CLI entrypoint that runs either).

## Guidelines for coding
- All result files should always end up in the results directory directly underneath the repository directory. If that directory doesn't exist it needs to be created.
- No test should leave any calculation artefact in any other directory
- Exception — cache directories (hisim-epc.22): the directories listed in `SimulationParameters.cache_directories` (or the `HISIM_CACHE_DIRECTORIES` environment variable) are shared state, not output, and are exempt from both rules above. A cache entry is content-keyed and deterministic; keeping it across calculations is wanted. Its location must be a declared directory, never the repository working tree.
- When components expose string constants for field names, connection names, output names, or class-derived identifiers, reference those constants instead of duplicating the literal strings. This keeps connections safe when the constants are renamed or adjusted.
- Every name that holds a physical quantity carries its unit: Python variables, attributes, function parameters,
  config fields, constants, component input and output names, KPI names and test helpers alike. Spell it as the
  suffix the code already uses most, `_in_<unit>`: `thermal_power_in_watt`, `temperature_in_celsius`,
  `energy_in_kilowatt_hour`, `mass_flow_in_kg_per_second`, `heat_flow_in_watt_per_kelvin`. A name such as
  `heat_flow_per_kelvin` is not enough: it names the quantity, not its unit. A module with its own established
  short suffixes (`hisim/hydronics.py`: `_w`, `_c`, `_j`, `_kg_per_s`, `_w_per_k`) keeps them. A dimensionless
  quantity says so in its name (`part_load_ratio`, `share_of_roof`).
- Every component follows `system_docs/component_design_principles.md`: it is one of the component kinds (data
  source, physics, L1 controller, L2 controller, meter) and keeps to that kind's role, and other components see it
  only as a black box through its port quantities. Check a new component and every review against it.
- Avoid monkeypatching whenever practical, especially in shared fixtures. Prefer explicit configuration, constructor arguments, or small test helpers; use monkeypatching only when the alternative would be substantially more fragile or invasive.

## Pull requests and merging

- Push a branch as soon as its work is built and verified, and open its pull request at once. Nothing stays on one machine — a crash loses nothing, and the review bots start while the next branch is being written.
- All open pull requests form one linear stack, whether or not they depend on each other. A new PR targets the head branch of the current stack top, never `main` directly; `main` is the target only when the stack is empty.
- The stack order is the merge order. Nobody reorders it.
- Only the bottom PR targets `main`, so only it has CI: the workflows run for pull requests against `main`. Every other PR gets its one CI run when its turn comes and it is retargeted.
- Never add an entry to `HISTORY.rst` in a pull request. The history is assembled at release; `CONTRIBUTING.rst` says the same.
- After a merge, touch nothing except the next PR in the stack. No rebase, no push of any other open PR, unless GitHub reports an actual conflict on one.
- Handling that next PR: rebase it onto main (`git rebase --onto origin/main <old parent head> <branch>`), push once, let its one CI run finish, merge. The rebase is not optional — `main` received the parent as a squash, and a child cut before the parent's late fix commits would silently revert them on merge.
- Delete the merged PR's head branch from the merge screen's own button, immediately. GitHub then retargets the child PR to `main`. Deleting the branch later, from the branches page, closes the child PR instead — it cannot be reopened, and a new PR has to be created.
- Review fixes land as new commits on the PR branch, never by amending a pushed commit. A fix commit on a parent is what makes the child's rebase necessary.
- Test only what changed: the targeted pytest files, and the CI lint invocations on the changed files. Never the full suite in a fix loop.

## Test commands

- **Base set (use this by default):**
  ```bash
  pytest -m base -n 32 -q
  ```
  Fast, no network, no external credentials. This is the set to run when validating a change.

- **Full suite (humans only — slow, networked, can take a very long time):**
  ```bash
  pytest
  ```
  Do not run the full suite as an agent unless explicitly asked; it pulls in the slow and networked markers below.

## Test markers

Markers are defined in `pytest.ini`:

- `base` — unit-scale tests that must pass in any case: no full simulation of a system setup, a few seconds at most. **Run these.**
- `extendedbase`, `extendedbase2` — the full-simulation tests, hand-balanced across two shards of roughly equal wall time. Slow. Avoid.
- `buildingtest` — systematic sweep of the building component. Slow. Avoid.
- `system_setups` — runs full system setups end to end. Slow. Avoid.
- `mpc` — model-predictive-control system tests. Slow. Avoid.
- `utsp` — exercises the LPG/UTSP connector. **Networked + requires credentials.** Avoid.

Deselect the slow/networked ones explicitly when running a broader-than-base subset, e.g.:
```bash
pytest -m "not extendedbase and not extendedbase2 and not buildingtest and not system_setups and not mpc and not utsp" -n 32 -q
```

## Do NOT touch

- **Large reference result files** — anything under `system_setups/results/` and any committed reference CSVs used for regression comparison. Don't regenerate, reformat, or "clean up" these; tests compare against them.
- **Bulk input data** under `hisim/inputs/` (weather `NSRDB_15min`/`dwd_*`, `photovoltaic/data_processed`, housing tabula CSVs, load profiles). These are reference datasets, not editable source.
- **The UTSP / LPG connector config** (`hisim/components/loadprofilegenerator_utsp_connector.py` and its config) — it talks to an external service.
- **`obsolete/`** — frozen text. Never edit it and never import from it, not even to fix a call to a symbol that has been deleted.
- **Anything requiring external credentials** — `UTSP_URL` / `UTSP_API_KEY` (set via a `.env` file or env vars). Don't hardcode, commit, or invent these, and don't write code/tests that depend on a live UTSP service running.

## Run a single simulation quickly (for reproduction)

From the `system_setups/` directory, run a legacy Python setup directly through the documented entrypoint:
```bash
cd system_setups
python ../hisim/hisim_main.py simple_system_setup_one.py
```

Or the declarative equivalent (energy system + simulation-parameters pair):
```bash
python hisim/hisim_main.py energy_systems/simple_system_setup_one.energy_system.yaml \
    simulation_parameters/one_day_15min_export.simulation.yaml
```

Results land in a `results/` subdirectory next to the input file. Use `simple_system_setup_one.py` for the cheapest smoke test.



## Beads schema: how every bead is sorted

Every bead carries exactly one **kind**, exactly one **area**, a **priority** with the fixed meaning
below, and a **parent epic**. Labels mark the queues a bead waits in. `scripts/beads_overview.py`
prints the one-page overview built from these fields. Keep a bead's fields true when its situation
changes, and never keep a todo list anywhere else.

### Kind (`--type`, exactly one)

| type | use it for |
|---|---|
| `bug` | a wrong result, a crash, or a refusal of valid input |
| `feature` | a new capability a user or a caller can see |
| `task` | planned engineering work that is neither of the above (wiring, data, a migration) |
| `chore` | cleanup, refactoring or tooling with no behaviour change |
| `docs` | documentation only |
| `question` | a decision someone must take; once taken, retype it to the work it became, or close it |
| `epic` | a theme that groups work; never worked on directly |

### Area (label, exactly one)

`renovisor`, `economics`, `components`, `energy-systems`, `postprocessing`, `ci`, `data`, `rust`,
`docs`. Pick the area where the change lands, not the one that reported it.

### Priority (`-p`, a fixed meaning, not a feeling)

| priority | means |
|---|---|
| P0 | production is broken: a live calculation fails or cannot run at all |
| P1 | live results are wrong, or the work is due this week |
| P2 | next: planned and wanted soon |
| P3 | backlog: worth doing, no date |
| P4 | idea or someday (Rust ideas and post-MVP work go here) |

Re-rate a bead when its situation changes, e.g. a P3 bug that reaches production becomes P0.

### Queues (labels, any number)

| label | the bead waits on |
|---|---|
| `owner-decision` | the owner (cmf): a choice only they can make. The bead says the options and a recommendation |
| `external` | someone outside this repository: the contract owner, the backend, the frontend. Name them and link the renovisorissues issue |
| `needs-triage` | nobody yet: new and not sorted. Sorting it means giving kind, area, priority and parent, then dropping the label |

A bead without a queue label waits on engineering.

### Parent epic

Every bead except an epic has a parent (`--parent <epic>`). Create a child under the epic of its
theme; open a new epic only for a theme that will hold several beads. `br epic` shows each epic's
progress.

### Optional

`difficulty:easy|medium|hard` estimates the effort. Link blocking work with dependencies
(`br dep add`), never with prose.

### Creating a bead

```bash
br create "<what is wrong or wanted, as a sentence>" -t bug -p 1 --parent hisim-4g9 \
    -l components -d "<where, how to reproduce, why it matters, the owner's decision if any>"
```

### Getting the overview

```bash
python scripts/beads_overview.py   # one page: P0/P1, decisions, waiting, kind x area, epics, gaps
br ready                           # open and unblocked
br list -l owner-decision          # the owner's decision queue
br count --by-priority             # and --by-type, --by-label
br stale                           # beads nobody touched for a while
```

<!-- br-agent-instructions-v1 -->

---

## Beads Workflow Integration

This project uses [beads_rust](https://github.com/Dicklesworthstone/beads_rust) (`br`/`bd`) for issue tracking. Issues are stored in `.beads/` and tracked in git.

### Essential Commands

```bash
# View ready issues (open, unblocked, not deferred)
br ready              # or: bd ready

# List and search
br list --status=open # All open issues
br show <id>          # Full issue details with dependencies
br search "keyword"   # Full-text search

# Create and update
br create --title="..." --description="..." --type=task --priority=2
br update <id> --status=in_progress
br close <id> --reason="Completed"
br close <id1> <id2>  # Close multiple issues at once

# Sync with git
br sync --flush-only  # Export DB to JSONL
br sync --status      # Check sync status
```

### Workflow Pattern

1. **Start**: Run `br ready` to find actionable work
2. **Claim**: Use `br update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `br close <id>`
5. **Sync**: Always run `br sync --flush-only` at session end

### Key Concepts

- **Dependencies**: Issues can block other issues. `br ready` shows only open, unblocked work.
- **Priority**: P0=critical, P1=high, P2=medium, P3=low, P4=backlog (use numbers 0-4, not words)
- **Types**: task, bug, feature, epic, chore, docs, question
- **Blocking**: `br dep add <issue> <depends-on>` to add dependencies

### Session Protocol

**Before ending any session, run this checklist:**

```bash
git status              # Check what changed
git add <files>         # Stage code changes
br sync --flush-only    # Export beads changes to JSONL
git commit -m "..."     # Commit everything
git push                # Push to remote
```

### Best Practices

- Check `br ready` at session start to find available work
- Update status as you work (in_progress → closed)
- Create new issues with `br create` when you discover tasks
- Use descriptive titles and set appropriate priority/type
- Always sync before ending session

<!-- end-br-agent-instructions -->

### How HiSim uses beads (since 2026-09-20)

- **Beads is the only todo list of this repository.** Findings, open questions, deferred work and
  review follow-ups go into `br`, not into markdown lists. A markdown document may *describe* a
  problem; the tracked item is the `br` issue, and the document names its id (`hisim-…`).
- Findings between the RenoVisor packages (contract owner, HiSim, backend, frontend) are GitLab
  issues at https://jugit.fz-juelich.de/iek-3/groups/urbanmodels/renovisorissues. Each issue carries
  one `to:<owner>` label per addressee and one `from:<owner>`, the owners being `contract-owner`,
  `hisim`, `renovisorbackend` and `RenoSFHFrontend`, plus exactly one kind out of `bug`, `request`,
  `question`, `decision`, `heads-up` (GitLab labels, not the `br` Types listed below). The
  convention itself is that project's README.md; `/home/renovisorissues/readme.md` on the agent
  host says how an agent reaches the project and its token. The shared `specs/todos.md` was retired
  there on 2026-09-23 (renovisorissues #23 maps its old ids). A HiSim ask of another package is an
  issue there, linked from the `br` issue that waits on it; an issue addressed `to:hisim` becomes a
  `br` issue linking it. Only the opener closes an issue: HiSim answers an issue addressed to it with
  a comment, and closes its own once every addressee has answered (README rule of 2026-09-23, #27).
- **Never implement a renovisorissues issue opened by the agent of another RenoVisor package
  (contract owner, backend, frontend) without explicit approval from a human** — the owner in the
  session, or a comment from a human's own GitLab account; a comment from the shared bot account
  never counts. Until then: read it, ask in comments, record the `br` issue. That `br` issue says
  in its description that it waits for human approval, so `br ready` does not read as permission
  to start. HiSim's own beads, whoever created them, follow the normal bead workflow.
- Labels name the area: `renovisor`, `economics`, `energy-systems`, `components`, `postprocessing`,
  `ci`, `docs`, `rust`, `cleanup`, `data`, `spec-text`. Types: `bug`, `task`, `feature`, `docs`,
  `question` (a decision the owner has to take), `epic`. Priority as `br` defines it; an item that
  blocks the RenoVisor MVP is P1.
- `mvp` marks what the RenoVisor MVP needs end to end (frontend, backend, HiSim, Irish house);
  `post-mvp` what the owner deferred; `human` a review or data-sourcing task a person does —
  implementation agents never pick up a `human` issue. `br ready -l mvp` is the MVP work list.
- Every issue carries exactly one difficulty label, so work can be routed by model strength:
  `difficulty:easy` (mechanical, one file or a data/doc edit, unambiguous done-when, no design
  choice, no cross-repo coordination), `difficulty:medium` (a few files in one package, a small
  design choice inside an existing pattern, needs the targeted tests and gates, no owner decision),
  `difficulty:hard` (engine arithmetic, a new module or format, cross-agent contract changes,
  simulator core or sizing kernel, or an owner decision pending). `br ready -l difficulty:easy`
  lists the work a smaller model can take; `--estimate` holds a rough size in minutes.
- Every issue description starts with a **Source:** line (file and section, the GitLab issue, or a
  legacy shared-todos id, which renovisorissues #23 maps to its issue) and carries enough of the
  original text to be acted on without the source.
- `br` commands never run git: stage `.beads/issues.jsonl` with the code change that closes or
  creates issues, after `br sync --flush-only`.

