"""The reproduction block a generated run record carries and an authored file never does.

A record alone does not reproduce a run. What does is the tuple of the record, the simulation
parameters it was run with, the version of HiSim that read them and the state of the input data
tree. Two of those four can be written down cheaply, and this module writes them down: the version
and, when the running package genuinely lives in a HiSim checkout, the commit. A record found in a
results directory a year later then says which HiSim produced it and from which file, instead of
being a set of numbers with no provenance at all.

Everything here degrades rather than fails, because metadata must never be the reason a simulation
does not start. An uninstalled package reports an unknown version; the commit is absent whenever
git is unavailable, the package does not sit in a checkout, or the checkout it sits in is not
HiSim's own — an installed package inside somebody else's working tree must not report that tree's
commit as HiSim's.

There is deliberately no timestamp. A record regenerated from an unchanged system must be an
unchanged file, and a clock in it would make every regeneration a diff, which is the fastest way to
teach readers to ignore the file.
"""

# clean

from __future__ import annotations

import os
import subprocess  # nosec B404 - one fixed-argv git query for the metadata block, never a shell
from importlib import metadata as importlib_metadata
from typing import Any, ClassVar, Dict, Mapping, Optional

import hisim
from hisim import log


class RunMetadata:
    """The reproduction block a generated record carries and an authored file never does.

    A record alone does not reproduce a run: the tuple that does is the record, the simulation
    parameters, the version of HiSim that read them and the state of the input data tree. Two of
    those four can be written down cheaply and are, so that a record found in a results
    directory a year later says which HiSim produced it and from which file.

    Everything here degrades rather than fails. An uninstalled package reports its version as
    :attr:`UNKNOWN_VERSION`, and the commit is ``None`` whenever git is unavailable, the package
    does not sit in a checkout, or the checkout it sits in is not HiSim's own — an installed
    package inside somebody else's working tree must never report that tree's commit as HiSim's.
    There is deliberately no timestamp: it would make every regeneration of an otherwise
    unchanged record a diff, which is the fastest way to teach readers to ignore the file.
    """

    #: The distribution whose installed version is reported; the name ``setup.py`` registers.
    DISTRIBUTION_NAME: ClassVar[str] = "hisim"

    #: Version reported when the package metadata cannot be read at all, which happens when
    #: HiSim runs from a plain source checkout that was never installed.
    UNKNOWN_VERSION: ClassVar[str] = "unknown"

    #: Seconds after which the git query is abandoned. Collecting metadata must never stall a
    #: simulation, so a timeout simply means "no commit".
    GIT_TIMEOUT_SECONDS: ClassVar[int] = 10

    #: Key holding the version of HiSim that wrote the record.
    HISIM_VERSION_KEY: ClassVar[str] = "hisim_version"

    #: Key holding the commit of the checkout that wrote it, or ``None``.
    GIT_COMMIT_KEY: ClassVar[str] = "git_commit"

    #: Key naming the energy-system file the run started from.
    SOURCE_ENERGY_SYSTEM_KEY: ClassVar[str] = "source_energy_system"

    #: Key naming the simulation-parameters file the run used.
    SOURCE_SIMULATION_PARAMETERS_KEY: ClassVar[str] = "source_simulation_parameters"

    #: Prefix shared by the two keys that name the run's input files. Comparing two records for
    #: equality drops them, because re-running a record legitimately changes which file it came
    #: from while changing nothing about the system the record describes.
    SOURCE_KEY_PREFIX: ClassVar[str] = "source_"

    @classmethod
    def hisim_version(cls) -> str:
        """Returns the installed HiSim version, or :attr:`UNKNOWN_VERSION` when unreadable.

        Returns:
            The version string, for example ``"1.2.4"``.
        """
        try:
            return importlib_metadata.version(cls.DISTRIBUTION_NAME)
        except importlib_metadata.PackageNotFoundError:
            return cls.UNKNOWN_VERSION

    @classmethod
    def git_commit(cls) -> Optional[str]:
        """Returns the commit of the HiSim checkout the running package comes from.

        Two guards keep the answer honest: git must report a repository at all, and the
        repository it reports must *be* the directory holding the ``hisim`` package, so an
        installation that happens to sit inside an unrelated working tree answers ``None``
        rather than that tree's commit.

        Returns:
            The full commit hash, or ``None`` when it cannot be determined honestly.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(hisim.__file__)))
        toplevel = cls._run_git(root, "rev-parse", "--show-toplevel")
        if toplevel is None or os.path.normpath(toplevel) != os.path.normpath(root):
            return None
        return cls._run_git(root, "rev-parse", "HEAD")

    @classmethod
    def _run_git(cls, repository_directory: str, *arguments: str) -> Optional[str]:
        """Runs one git query and returns its stripped output, or ``None`` on any failure.

        Every failure mode — git missing, no repository, a timeout, a non-zero exit — collapses
        to ``None``, because the caller treats "no answer" and "no repository" identically.

        Args:
            repository_directory: Directory to run git in.
            *arguments: The git arguments after the executable name.

        Returns:
            The stripped standard output, or ``None``.
        """
        try:
            completed = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, short timeout
                ["git", *arguments],
                cwd=repository_directory,
                capture_output=True,
                text=True,
                timeout=cls.GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            log.debug(f"git {' '.join(arguments)} failed while collecting run metadata: {error!r}")
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    @classmethod
    def collect(cls, source_energy_system: str, source_simulation_parameters: str) -> Dict[str, Any]:
        """Builds the metadata block of one record.

        Args:
            source_energy_system: The energy-system file the run started from, as a path
                relative to the working directory or, failing that, its bare file name.
            source_simulation_parameters: The parameters file of the run, spelled the same way.

        Returns:
            The block, with the keys in the order a record writes them.
        """
        return {
            cls.HISIM_VERSION_KEY: cls.hisim_version(),
            cls.GIT_COMMIT_KEY: cls.git_commit(),
            cls.SOURCE_ENERGY_SYSTEM_KEY: source_energy_system,
            cls.SOURCE_SIMULATION_PARAMETERS_KEY: source_simulation_parameters,
        }

    @classmethod
    def without_sources(cls, metadata: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Drops the two keys naming the run's input files from a metadata block.

        Re-running a record produces a record of the same system from a different file, so the
        two ``source_`` keys are the one part of a record that legitimately differs between a
        run and its re-execution. Every comparison that asks whether two records describe the
        same system therefore compares the blocks without them.

        Args:
            metadata: The block, or ``None`` for a file that carries none.

        Returns:
            The remaining keys and values.
        """
        if metadata is None:
            return {}
        return {
            key: value
            for key, value in metadata.items()
            if not key.startswith(cls.SOURCE_KEY_PREFIX)
        }

    @classmethod
    def describe_source(cls, path: Any) -> str:
        """Renders one input path the way a record names it.

        A record is read by people who did not run it, often from a results directory copied
        somewhere else, so an absolute path from the machine that produced it is noise. A path
        below the working directory is written relative to it and anything else by its bare
        file name, which is the part that identifies the file.

        Args:
            path: The path of one input file; may be empty.

        Returns:
            The relative path or the bare file name, and an empty string for no path at all.
        """
        if not path:
            return ""
        text = str(os.fspath(path))
        try:
            relative = os.path.relpath(os.path.abspath(text), os.getcwd())
        except ValueError:
            return os.path.basename(text)
        return relative if not relative.startswith(os.pardir) else os.path.basename(text)
