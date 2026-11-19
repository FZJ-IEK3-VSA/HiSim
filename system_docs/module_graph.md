# Module graph

Which part of `hisim/` may import which. The graph itself is data, `system_docs/module_graph.yaml`; this page
explains it. `tests/test_module_graph.py` (marker `base`) checks every import against it, with a ratchet baseline of
today's violations, `tests/module_graph_baseline.yaml`.

Status: a first draft of the seams design for hisim-ylxf (drawing v5, 2026-11-10). The groups and edges are not yet
approved by the owner; changing an edge is a change of one `may_import` line in the YAML.

## Groups

A module belongs to the group of the longest prefix in the YAML that is the module itself or a package above it, so
a file that moves to another package changes group with it. `hisim.components.shared` (shared code) lies inside
`hisim.components` and wins over it. A module that matches no prefix fails the check, with the remedy in the message.

| group | holds | may import |
|---|---|---|
| leaves | loadtypes, units, log, utils, hydronics | nothing |
| physics_constants | `hisim.physics_constants` (empty: PhysicsConfig moves there) | nothing |
| infrastructure | caching, write guard, result paths, calculation scope and progress | leaves |
| parameters | simulation parameters, post-processing options | leaves, infrastructure |
| config | `hisim.config` | leaves |
| contracts | `hisim.contracts` (empty: the types that cross the seams) | leaves, config, parameters |
| shared_code | `hisim.components.shared` (empty: types several components use) | leaves, config |
| subcomponents | `hisim.subcomponents` | leaves, config, shared_code |
| component_model | component, dynamic_component, energy_port, sim_repository, part_load, explicit_state_controller, fluid_loops | the foundation |
| components | `hisim.components` | the foundation, component_model, subcomponents, shared_code |
| postprocessing, economics | their packages | leaves, parameters, contracts, infrastructure |
| simulator | simulator, component_wrapper, evaluation_order | the foundation, component_model, postprocessing, economics |
| setup_rules | `hisim.setup_rules` | the foundation but infrastructure, component_model, postprocessing |
| energy_system | `hisim.energy_system` | the foundation, component_model, simulator, setup_rules |
| building_sizer_utils | building_sizer_utils, system_setup_configuration | leaves, config, parameters, components |
| cli | cli, cli_*, hisim_main, hisim_with_profiler, project_code_overview_generator | the foundation, component_model, simulator, energy_system, setup_rules, postprocessing, economics |
| renovisor | `hisim.renovisor` | the foundation, component_model, components, energy_system, economics, postprocessing |

"The foundation" is leaves, physics_constants, config, parameters, contracts and infrastructure; every group but
leaves may import physics_constants. The YAML is the authority; the table is a reading aid. The allowed edges must be
acyclic.

## The rules

1. **Edges.** A module may import its own group and the groups its group's `may_import` lists.
2. **Private names.** A name with a leading underscore (not a dunder) is never imported from another group: a group
   that needs it gets a public spelling.
3. **Imports by string.** `importlib.import_module`, `__import__`, or a module's dotted name passed to a call, are
   allowed only in a *registry*: a module that loads by a name known only at run time (the class loader of
   energy-system files, the rules registry, `describe <class>`, `MAIN_CLASS`, ...). The YAML lists each registry with
   its reason and the groups it may load. Anywhere else an import by string is a violation, also inside one group:
   it hides an import, usually one that dodges a circular import (coding_style.md, code rule 7).

What counts as an import: every `import` and `from ... import` anywhere in the file (module level, in a function, in
a class), relative ones resolved; `import_module` / `__import__` with a literal, or a name bound to a literal in the
same function; a dotted module name passed to any call. `hisim/inputs` is not checked.

`TYPE_CHECKING` imports count: they tie the importer's code to the other group's types just the same, and the fix is
the same. They are listed in a section of their own, because they load nothing at run time and cause no import cycle.

## The ratchet

The baseline lists today's violations, one per line, sorted, as (importing module, imported module, imported name or
`*`) with a count of statements and the bead that will remove it. Never a line number: moving code does not touch it.

- A violation not in the baseline fails, and so does one with more statements than its entry's count. The message
  names the importer, the imported module, the edge, and the two remedies: fix the import, or add the printed entry
  with a bead id, which needs the owner's approval in review.
- An entry whose violation is gone, or whose count went down, fails as stale until it is deleted or lowered, so a fix
  cannot regress silently. `python scripts/module_graph.py prune` does that; it never adds or raises an entry.
- Every entry names an existing bead. A pull request that fixes a violation deletes its entry; the last one closes
  the bead.

```bash
python scripts/module_graph.py check     # the test's check, readable
python scripts/module_graph.py report    # entries per bead, per edge and per section, as Markdown
python scripts/module_graph.py prune     # delete or lower stale entries
```

The ratchet core, `scripts/ratchet.py`, knows nothing about imports; the code-size and escape-hatch ratchets
(hisim-repo-cleanup-o6a.12, o6a.13) are meant to reuse it with their own baseline layout.

## Older import lints

`tests/test_renovisor_import_lint.py`, `test_economics_import_lint.py`, `test_energy_system_layering.py` and
`test_subcomponent_import_lint.py` predate the graph and still run unchanged beside it.
