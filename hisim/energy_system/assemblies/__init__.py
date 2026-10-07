"""Assemblies: composing an energy system from tested, parameterised fragments (lean v1).

``roadmap/declarative_energy_systems/assemblies_spec.md``, §13.1 (D26). An assembly is a fragment
of an energy system in a file of its own, ``<family>/<name>.assembly.yaml``; an energy-system file of
schema version 4 imports assemblies under ``imports``, and :func:`~.expansion.expand_imports` turns
every import into ordinary components of one flat file before anything else sees it. Assemblies are
flat in v1; the constructs §13.1 cuts are refused by name (``EF-73``).

The modules follow the stages of §2.3:

    - :mod:`.model` and :mod:`.reader` — the assembly file and its reader;
    - :mod:`.resolver` — library paths along the search path, and each file's sha256;
    - :mod:`.library` — the library check, listing every problem of a file at once;
    - :mod:`.parameters` — an import's parameters checked and the internal variants selected;
    - :mod:`.addresses` — every member's structured address and its substituted entry;
    - :mod:`.binding` — the ports bound by the verbs and the default rule, and lowered;
    - :mod:`.record` — the import record and the source map;
    - :mod:`.expansion` — :func:`~.expansion.expand_imports`, which drives the stages;
    - :mod:`.describe` and :mod:`.schema` — ``describe <family>/<name>`` and the JSON Schema.

The blocks an energy-system file shares with an assembly — imports, verbs, ports and the
placeholders — live one level up, in :mod:`hisim.energy_system.imports_model` and
:mod:`hisim.energy_system.imports_reader`, because the file model needs them.
"""
