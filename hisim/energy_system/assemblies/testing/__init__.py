"""The assembly test harness: every assembly's test contract, executed (``assemblies_spec.md`` §9.4, D24).

"Tested fragments" means more than one run at the defaults. Every assembly carries its test
contract in its own file — a ``range`` on every numeric parameter, ``tests.bounds``,
``tests.monotone`` and optional ``tests.expect`` — and this package executes it, generically:
nothing is written per assembly in Python. It lives beside the assemblies rather than under
``tests/`` because the command line (``hisim energy-system test-assemblies``) runs it as well as
the test suite, and it imports nothing of pytest.

    - :mod:`.samples` — the parameter samples an assembly's declarations imply: every preset,
      every ``range`` boundary, every allowed value, every internal variant, and the seeded Latin
      hypercube sample of the parameter box, one hypercube per constraint branch.
    - :mod:`.partners` — the registry of test partners, read from the ``test_partners.yaml`` of
      each library directory: which site component stands in for a need's partner class, the
      other end of a circuit, a carrier's provider or consumer, a fact's provider.
    - :mod:`.isolation` — the isolation system of one assembly and one sample (the assembly as one
      import, a test partner for every port whose binding changes what it computes), and its
      one-day run with the energy-balance check and ``i_doublecheck`` on.
    - :mod:`.contract` — the member contract, checked on the constructed members of the base runs
      before any declaration: a ``bounds`` entry for every energy-carrying or temperature output,
      each bounds unit its output's, and every named KPI one the member reports.
    - :mod:`.checks` — what is checked on a run: exceptions, non-finite values, the energy balance,
      ``bounds`` on outputs and KPIs, ``expect`` per preset, and the ``monotone`` evaluation.
    - :mod:`.report` — the per-assembly report, its JSON form and its one-screen summary, and
      :class:`~.report.AssemblyTestFailure`, raised once everything has run.
    - :mod:`.harness` — the tiers, the library walk and its shards, and the run of one assembly.
    - :mod:`.errors` — the named errors of the harness itself.

The harness fails hard: a contract the library check or the member contract refuses, a missing
test partner and a failed check are all errors with names. Failed checks are collected over every sample and every
assembly first, so that one report shows all of them, and then raised together.
"""
