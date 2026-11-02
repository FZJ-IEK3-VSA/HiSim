# Documentation map

Where each kind of document belongs, so a reader knows where to look and a writer where to put a new one. A
document in the wrong place is moved with `git mv`, and every reference to it is updated in the same pull request;
closed beads and `obsolete/` keep their text.

## The root: five documents

| File | What it is |
|---|---|
| `README.md` | What HiSim is, how to install and run it, the funding notes. |
| `AGENTS.md` | The rules agents and contributors follow, each in one line with a link to its guide; the beads schema. |
| `CLAUDE.md` | Claude Code's entry point: commands, architecture, how to add a component. |
| `CONTRIBUTING.rst` | How to contribute: reporting bugs, proposing changes, the pull request guidelines. |
| `HISTORY.rst` | The release history, assembled at release, never in a pull request. |

Besides these, the root holds only what packaging, licensing and the tools need: `LICENSE`, `AUTHORS.rst`,
`CITATION.cff`, the configuration of the build and the checks (`pyproject.toml`, `setup.py`, `setup.cfg`,
`pytest.ini`, `mypy*.ini`, `.pylintrc`, ...), the Dockerfiles and the logos `README.md` shows. No spec, plan,
notes, log or todo list lives in the root. Three files that fit none of this are still there and wait for removal
(hisim-agent-rules-h2ib.10).

## `system_docs/`: binding guidelines

Rules every change follows, for people and agents alike; reviews check them. A guideline states rules in the
present tense, each with a one-line reason and an example, and stands on its own: it never cites a spec as its
authority. It changes by pull request, with the owner's agreement.

| File | What it rules |
|---|---|
| `coding_style.md` | Design, code rules, patterns, names, docstrings, commit messages and PR descriptions. |
| `component_design_principles.md` | The component kinds, black boxes, who decides what, what crosses a port. |
| `modelling_guide.md` | Energy at every port, signs, the 60 s reference, clamp or fail, parameter sources. |
| `results_and_interfaces.md` | How a result-changing change is made; which names are public interfaces. |
| `testing.md` | Markers, the `base` budget, what a test must prove, test only what changed. |
| `documentation_map.md` | This page. |

## `roadmap/`: specs, plans and records

- **Specs and plans**: a design with its owner decisions, written from `roadmap/templates/spec_template.md`
  before the work (AGENTS.md, "Specs and decisions"). An epic with several documents gets a subdirectory
  (`declarative_energy_systems/`, `renovisor/`, `json_development/`, `rust_ideas/`).
- **Records**: measured findings and closed issue logs, kept so that old pull requests and beads can still be
  followed (`roadmap/simulation_issues.md`, `roadmap/cost_module_issues.md`). A record whose items moved to beads
  says so in its first lines.
- **Templates**: `roadmap/templates/`.

A spec is a design record, not a rule for the code: docstrings never cite it (`coding_style.md` §6), and a guide
states its rules itself, naming a spec only as the source of an example or a decision. The spec citations that
docstrings and comments in `hisim/` still carry are removed under hisim-agent-rules-h2ib.26. A rule that a spec settles for
good is written into a guideline in `system_docs/`.

## Next to the code

- **A `README.md` in a directory describes that directory**: `energy_systems/README.md`,
  `system_setups/README.md`, `system_setups/economic_example/README.md`, `simulation_parameters/README.md`,
  `golden_references/README.md`, `hisim/economics/README.md`, `hisim/cost_database/spot_series/README.md`,
  `scripts/hpc_harness/README.md`, `roadmap/renovisor/README.md`, and the data provenance notes under
  `hisim/inputs/`.
- **Package usage pages** stay in their package: `hisim/renovisor/how_to_use.md`.
- **CI documentation** stays beside the workflows: `.github/ci-monitoring.md`.
- **`docs/`** is the Sphinx documentation published on Read the Docs, built by `.github/workflows/docs-build.yml`.
- **`paper/`** is the JOSS paper. **`obsolete/`** is frozen text, never edited.

## Todos and decisions

- **Every todo is a bead** (`br`, `.beads/issues.jsonl`), never a markdown list in any of the places above
  (AGENTS.md, "How HiSim uses beads"). A document may describe a problem and name its bead.
- **A decision is recorded where it applies**: in its spec as `D<n>` with the owner's date, and in its bead.
- **Findings between the RenoVisor packages** are issues in renovisorissues, linked from their beads.

## Moved on 2026-10-14

| Old path | New path |
|---|---|
| `TESTING.md` | `system_docs/testing.md` |
| `golden_ref_spec.md` | `roadmap/golden_ref_spec.md` |
| `cost_spec.md` | `roadmap/cost_spec.md` |
| `cost_module_issues.md` | `roadmap/cost_module_issues.md` |
| `simulation_issues.md` | `roadmap/simulation_issues.md` |
| `hpc_harnes_spec.md` | `roadmap/hpc_harness_spec.md` |
| `scripts/hisim_spec.md` | `roadmap/renovisor/hisim_input_spec_v1.md` |
| `system_docs/config_defaults_spec.md` | `roadmap/declarative_energy_systems/config_defaults_spec.md` |
| `LEARNINGS.md` | retired: agent notes of 2026-06 on the code base of then, superseded by `coding_style.md` and `testing.md` |
| `tests/README.md` | retired: run instructions of 2021, superseded by `testing.md` |
