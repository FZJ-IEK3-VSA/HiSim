"""Tests for the environment the scenario-JSON regeneration hands its children.

The regeneration driver rebuilds every ``system_setups/*.py`` in a subprocess and then reports
whether the committed ``.scenario.json`` files still match. That report is only worth having if
each child ran *this* checkout's HiSim, and nothing about the way the child is started makes that
true by itself: the converter is invoked as a script path, so Python seeds ``sys.path`` with the
script's own directory (``scripts/``) rather than with the working directory, and the editable
install's finder is then free to answer ``import hisim`` from whichever checkout happens to be
installed. In a second checkout the driver would regenerate everything against the wrong tree and
announce no drift -- a false all-clear, which is worse than a failure because nobody investigates
it.

The first test pins the mechanism, so that the reasoning above stays checkable rather than being a
comment nobody can verify. The second pins the fix. Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import ClassVar, Dict, List

import pytest

from scripts.regenerate_scenario_jsons import REPO_ROOT, regenerate_one


class PathProbe:
    """A throwaway two-directory tree used to observe how Python seeds ``sys.path``.

    The tree mimics the shape the driver runs in: a root holding an importable package and a
    ``scripts/`` subdirectory holding the program that imports it. The package name is deliberate
    nonsense so that the probe can never accidentally succeed by finding something real, which
    makes a successful import proof that the root was on the path and an ``ImportError`` proof that
    it was not.
    """

    #: The package the probe tries to import. Nothing by this name exists on any real path.
    PACKAGE: ClassVar[str] = "sentinel_package_for_the_path_probe"

    #: What the probe prints when the import worked.
    FOUND: ClassVar[str] = "found"

    @classmethod
    def build(cls, root: Path) -> Path:
        """Lay out the probe tree and return the path of the program to run.

        Args:
            root: An empty directory to build in.

        Returns:
            The path of the probe script, inside ``root / "scripts"``.
        """
        (root / cls.PACKAGE).mkdir()
        (root / cls.PACKAGE / "__init__.py").write_text("", encoding="utf-8")
        scripts = root / "scripts"
        scripts.mkdir()
        probe = scripts / "probe.py"
        probe.write_text(
            f"import {cls.PACKAGE}\nprint({cls.FOUND!r})\n",
            encoding="utf-8",
        )
        return probe

    @classmethod
    def run(cls, probe: Path, root: Path, on_the_path: bool) -> subprocess.CompletedProcess:
        """Run the probe the way the driver runs the converter, with or without the fix.

        Args:
            probe: The probe script, as returned by :meth:`build`.
            root: The directory that is both the working directory and the package's home.
            on_the_path: Whether to name ``root`` in ``PYTHONPATH``.

        Returns:
            The finished process, whose return code says whether the import succeeded.
        """
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        if on_the_path:
            environment["PYTHONPATH"] = str(root)
        return subprocess.run(  # nosec B603 - fixed argument vector, no shell
            [sys.executable, str(probe)],
            cwd=str(root),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.mark.base
def test_a_script_started_by_path_cannot_import_from_its_working_directory(tmp_path: Path) -> None:
    """The premise: ``cwd`` is not what a script invocation puts on ``sys.path``.

    This is the whole reason the driver has to name ``PYTHONPATH`` explicitly. If a future Python
    ever did seed ``sys.path`` with the working directory for a script path, this test fails, the
    argument above stops applying, and the extra variable can go.

    Catches: somebody deleting the ``PYTHONPATH`` line on the grounds that ``cwd=REPO_ROOT``
    already covers it.
    """
    probe = PathProbe.build(tmp_path)

    without_the_fix = PathProbe.run(probe, tmp_path, on_the_path=False)
    with_the_fix = PathProbe.run(probe, tmp_path, on_the_path=True)

    assert without_the_fix.returncode != 0, (
        "a script started by path imported a package from its working directory, so the working "
        "directory is on sys.path after all and this script's PYTHONPATH handling is redundant"
    )
    assert PathProbe.PACKAGE in without_the_fix.stderr
    assert with_the_fix.returncode == 0, with_the_fix.stderr
    assert PathProbe.FOUND in with_the_fix.stdout


class RecordedChild:
    """A stand-in for :func:`subprocess.run` that keeps the environment it was handed.

    The driver's contract with its children is entirely in that environment, so capturing it is
    enough to test the contract, and it means the test never pays for a simulation.
    """

    def __init__(self) -> None:
        """Start out having seen nothing."""
        self.environments: List[Dict[str, str]] = []

    def __call__(self, *args, **kwargs) -> subprocess.CompletedProcess:
        """Record the environment and report success without running anything.

        Args:
            *args: The argument vector the driver built; ignored.
            **kwargs: The keyword arguments, of which ``env`` is what this test is about.

        Returns:
            A finished process with return code zero.
        """
        self.environments.append(dict(kwargs["env"]))
        return subprocess.CompletedProcess(args=args[0] if args else [], returncode=0)


@pytest.mark.base
def test_the_child_is_told_to_import_hisim_from_this_checkout(tmp_path: Path, monkeypatch) -> None:
    """Every child's ``PYTHONPATH`` must begin with the checkout the driver was started from.

    Prepending rather than replacing matters too: a caller who set ``PYTHONPATH`` for reasons of
    their own keeps it, they just stop outranking this checkout.

    Catches: a regeneration run in a git worktree quietly rebuilding every setup against the
    installed checkout instead, and reporting that nothing drifted.
    """
    recorded = RecordedChild()
    monkeypatch.setattr("scripts.regenerate_scenario_jsons.subprocess.run", recorded)
    monkeypatch.setenv("PYTHONPATH", "/somewhere/the/caller/cares/about")
    import queue

    indices: "queue.Queue[int]" = queue.Queue()
    indices.put(1)

    regenerate_one(
        setup_path=REPO_ROOT / "system_setups" / "simple_system_setup_one.py",
        python=sys.executable,
        index_pool=indices,
        log_dir=tmp_path,
        keep_simulation_json=True,
    )

    assert len(recorded.environments) == 1
    entries = recorded.environments[0]["PYTHONPATH"].split(os.pathsep)
    assert entries[0] == str(REPO_ROOT), f"PYTHONPATH does not lead with the checkout: {entries}"
    assert "/somewhere/the/caller/cares/about" in entries, f"the caller's own entry was dropped: {entries}"
