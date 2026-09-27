"""One calculation's lifetime: a fresh result directory, and a write guard around everything it does.

Every entry point that runs a calculation -- ``hisim_main`` in both of its modes, ``run_energy_system``
(and with it ``hisim energy-system run``), the RenoVisor ``Calculation`` and the economics CLI's
writing commands -- opens exactly one :meth:`CalculationScope.open` around the whole of it. The
scope does two things (bead hisim-epc.23, owner decisions of 2026-09-26):

1. **A fresh result directory.** :class:`~hisim.result_path_provider.ResultPathProviderSingleton` is
   process-global, and a worker that runs several calculations in one process must never hand the
   second one the first one's directory. At the start of the scope the provider is reset unless a
   caller configured it *for this calculation* and nothing has used that configuration yet (a test
   choosing its directory, a building-sizer harness choosing a hash layout); a configuration a
   previous simulator derived, or a directory a previous calculation claimed, is dropped. At the end
   of the scope -- however it ends -- the provider is reset again, so a configuration is used by
   exactly one calculation. The logger, module-global as well, is reset with it. A directory the
   caller names (a RenoVisor job directory) is adopted into the provider, so the provider answers
   with it for the whole calculation.

2. **The write guard.** :class:`~hisim.write_guard.WriteGuard` refuses every write outside the result
   directory and the cache directories for the length of the scope; see that module for the rules.

A scope opened inside another is the same calculation: it adds its directory to the running guard
and changes nothing else.

**The contract the caller relies on:** everything a calculation writes lands in its result directory
or in the cache directories, and nothing a later calculation needs is in the result directory -- the
caches, which a later calculation does need, live in the cache directories. A caller may therefore
copy what it wants out of the result directory and delete the directory whole.
"""

import contextlib
from pathlib import Path
from typing import Iterator, Optional, Sequence, Union

from hisim import log
from hisim.result_path_provider import ResultPathProviderSingleton
from hisim.write_guard import GuardMode, WriteGuard


class CalculationScope:

    """Opens and closes one calculation; see the module docstring."""

    @classmethod
    def _start_fresh(cls) -> None:
        """Drop whatever the provider and the logger carry over from an earlier calculation.

        The logger is module-global too, and an energy-system run used to leave it pointing at its
        result directory, so the next calculation in the process logged its first lines there.
        """
        provider = ResultPathProviderSingleton()
        if provider.configured_by_simulator or provider.claimed_directory is not None:
            ResultPathProviderSingleton.reset()
        if not log.logger.before_result_dir_created:
            log.logger.reset()

    @classmethod
    @contextlib.contextmanager
    def open(
        cls,
        label: str,
        run_directory: Optional[Union[str, Path]] = None,
        cache_directories: Sequence[Optional[str]] = (),
        mode: Optional[GuardMode] = None,
    ) -> Iterator[WriteGuard]:
        """Run the body as one calculation.

        Args:
            label: What the calculation is, for the guard's messages.
            run_directory: The directory the caller chose for the calculation (a RenoVisor job
                directory). It is created, adopted into the result path provider and allowed.
                Without it the simulator obtains the directory from the provider as it always did,
                and tells the guard.
            cache_directories: Cache directories the caller already knows.
            mode: The guard's mode; ``HISIM_WRITE_GUARD`` when omitted.

        Yields:
            The active write guard.

        Raises:
            StrayWriteError: When the calculation wrote outside its directories.
        """
        running = WriteGuard.active()
        if running is not None:
            # Already inside a calculation (an entry point calling another): this is the same
            # calculation, which keeps its provider, its logger and its guard.
            if run_directory is not None:
                WriteGuard.admit_result_directory(str(run_directory))
            yield running
            return
        cls._start_fresh()
        try:
            directories = []
            if run_directory is not None:
                directories.append(ResultPathProviderSingleton().adopt_directory(run_directory))
            # contextlib drives this generator to its end on exit and on error alike, so the guard's
            # own exit always runs.
            with WriteGuard.calculation(  # pylint: disable=contextmanager-generator-missing-cleanup
                label=label,
                result_directories=directories,
                cache_directories=cache_directories,
                mode=mode,
            ) as guard:
                yield guard
        finally:
            ResultPathProviderSingleton.reset()
            log.logger.reset()
