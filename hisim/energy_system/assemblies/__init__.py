"""Assemblies: composing an energy system from tested, parameterised fragments.

``roadmap/declarative_energy_systems/assemblies_spec.md`` (PR #881), step 1a (bead hisim-lt0b.1). An
assembly is a fragment of an energy system in a file of its own, ``<family>/<name>.assembly.yaml``;
an energy-system file of schema version 4 imports assemblies under ``imports``, and the expansion
turns every import into ordinary components of one flat file before anything else sees it.

The package is a module set rather than one module because each stage has its own failure mode
and its own reader: the format, the search path, the parameters, the checks, the expansion and its
record are separate questions, and the expansion is the only module that needs all of them.

    - :mod:`.model` — the in-memory form of an assembly file: parameters, constraints, presets,
      members, internal variants, ports, the test contract.
    - :mod:`.reader` — the YAML reader, which keeps a line index for the source maps.
    - :mod:`.resolver` — library paths along the search path (``energy_systems/assemblies/``,
      then ``HISIM_ASSEMBLY_PATH``), the content hash of each file.
    - :mod:`.parameters` — presets, value checks, constraints and ``{$param: …}`` substitution.
    - :mod:`.library` — the library check, listing every problem of a file at once.
    - :mod:`.expansion` — :func:`~.expansion.expand_imports`, the stage in front of the group
      expansion: addresses, order paths, port binding and lowering.
    - :mod:`.record` — the import record and the source maps.
    - :mod:`.describe` — ``hisim energy-system describe <family>/<name>``.
    - :mod:`.schema` — the JSON Schema of the assembly file.

The blocks an energy-system file shares with an assembly — imports, binding verbs, ports and the
``{$port: …}`` placeholder — live one level up, in :mod:`hisim.energy_system.imports_model` and
:mod:`hisim.energy_system.imports_reader`, because the file model needs them and this package
imports the file model. Nothing here is imported by ``hisim.energy_system`` itself, so importing the
format never loads the expansion.
"""
