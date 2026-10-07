"""The assembly test harness: every assembly's test contract, executed (``assemblies_spec.md`` §9.4, D24).

Every assembly carries its test contract in its own file — a ``range`` on every numeric parameter,
``tests.bounds``, ``tests.monotone`` and optional ``tests.expect`` — and this package executes it
generically; nothing is written per assembly in Python. The harness runs on pytest (lean v1, §13.1):
``tests/assemblies/test_library_contracts.py`` parametrizes over the assemblies of a library and
their samples, the PR tier under the ``base`` marker and the Latin hypercube under ``nightly``, and
pytest does the reporting and the sharding. The package holds what is not pytest:

    - :mod:`.samples` — the parameter samples the declarations imply, the hypercube per constraint
      branch, and the monotone sweeps;
    - :mod:`.partners` — the test partners of a library, read from its ``test_partners.yaml``;
    - :mod:`.isolation` — the isolation system of one sample and its one-day run;
    - :mod:`.checks` — the checks of a run, of a sweep and of the member contract, each failing with
      :class:`~.checks.AssemblyCheckFailure` named by assembly, sample, check and subject.
"""
