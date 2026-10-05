"""``hisim energy-system test-assemblies``: the harness over a whole library, as a command (§9.4).

::

    hisim energy-system test-assemblies --out DIR [--tier pr|nightly] [--library DIR]... [--samples N]
                                        [--seed S] [--steps K] [--shard i/n]

The library is every assembly in the directories ``--library`` names, in that search order, or the
machine's search path (``energy_systems/assemblies/``, then ``HISIM_ASSEMBLY_PATH``) when none is
named; their ``test_partners.yaml`` files are the test-partner registry. ``--shard i/n`` takes
every ``n``-th assembly of the sorted library starting at the ``i``-th. ``--samples`` and ``--seed``
set the nightly hypercube and are refused with the ``pr`` tier, which draws none. The runs, the
JSON report and the summary go to ``--out``, which is required: the harness never writes where it
was not told to, and never leaves a temporary directory behind. The summary is printed; a failed
check makes the command fail after the report is written. The exit codes are listed on
:meth:`hisim.cli.EnergySystemCommands.test_assemblies`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, TextIO

from hisim.energy_system.assemblies.resolver import AssemblyResolver
from hisim.energy_system.errors import EnergySystemAssemblyError
from hisim.energy_system.assemblies.testing.errors import HarnessUsageError
from hisim.energy_system.assemblies.testing.harness import (
    DEFAULT_MONOTONE_STEPS,
    DEFAULT_SAMPLE_SIZE,
    DEFAULT_SEED,
    AssemblyHarness,
    HarnessSettings,
    Tier,
    library_paths,
    parse_shard,
    require_passed,
    shard_of,
)
from hisim.energy_system.assemblies.testing.report import HarnessReport


def test_assemblies(
    *,
    tier: str = Tier.PR.value,
    libraries: Optional[Sequence[str]] = None,
    samples: Optional[int] = None,
    seed: Optional[int] = None,
    steps: int = DEFAULT_MONOTONE_STEPS,
    out: str,
    shard: str = "1/1",
    stream: Optional[TextIO] = None,
) -> HarnessReport:
    """Runs the harness over a library, prints the summary and fails on a failed check.

    Returns:
        The report, when every check held.

    Raises:
        HarnessUsageError: For an unknown tier, ``--samples``/``--seed`` with the ``pr`` tier, a
            library directory that does not exist, an empty library or shard, or an output
            directory that already holds runs.
        AssemblyTestFailure: When a check failed; the report is written and printed first.
    """
    try:
        chosen = Tier(tier)
    except ValueError as error:
        raise HarnessUsageError(f"the tier is 'pr' or 'nightly', not {tier!r}.") from error
    if chosen == Tier.PR and (samples is not None or seed is not None):
        raise HarnessUsageError("--samples and --seed set the nightly hypercube; the pr tier draws none.")
    settings = HarnessSettings(
        tier=chosen,
        sample_size=samples if samples is not None else DEFAULT_SAMPLE_SIZE,
        seed=seed if seed is not None else DEFAULT_SEED,
        monotone_steps=steps,
    )
    try:
        resolver = AssemblyResolver([Path(item) for item in libraries]) if libraries else AssemblyResolver.default()
    except EnergySystemAssemblyError as error:
        raise HarnessUsageError(f"the library cannot be searched: {error}") from error
    index, count = parse_shard(shard)
    paths = shard_of(library_paths(resolver), index, count)
    harness = AssemblyHarness(resolver, Path(out), settings)
    report = harness.test_library(paths, shard=f"{index}/{count}")
    if stream is not None:
        print(report.summary(), file=stream)
    require_passed(report)
    return report
