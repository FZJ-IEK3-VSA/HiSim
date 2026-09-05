"""Tests for the environment the scenario-JSON regeneration passes to its subprocesses.

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
comment nobody can verify. The second pins the fix. The third pins the other half of the child's
contract, the borrowed local-LPG base index. Each test states the failure mode it catches.
"""

# clean

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import ClassVar, Dict, List

import pytest

from hisim.components.pylpg_workspace import LpgBaseIndexPool, PylpgWorkspace
from scripts.regenerate_scenario_jsons import REPO_ROOT, regenerate_one


class PathProbe:
    """A throwaway package plus a throwaway script, used to observe what Python puts on ``sys.path``.

    The package name is nonsense so the probe cannot succeed by finding something real: a successful
    import proves the package's directory was on the path, an ``ImportError`` proves it was not.
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
        """Run the probe the way the driver runs the converter, with or without ``PYTHONPATH``.

        Args:
            probe: the probe script from :meth:`build`.
            root: the working directory, which is also the package's parent.
            on_the_path: whether to set ``PYTHONPATH`` to ``root``.

        Returns:
            subprocess.CompletedProcess: the finished process; its return code says whether the import worked.
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
    """A script run by path cannot import a package from its working directory unless ``PYTHONPATH`` names it.

    This is the reason the driver sets the variable. If a future Python put the working directory on
    ``sys.path`` for scripts, this test would fail and the variable could go.
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

    The environment is the part of the driver's contract with its children that this test file is
    about (the argument vector, working directory and log routing are the rest), and it is captured
    whole -- so recording it is enough here, and the test never pays for a simulation.
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
    """Every child's ``PYTHONPATH`` starts with this checkout and keeps the caller's own entries after it."""
    recorded = RecordedChild()
    monkeypatch.setattr("scripts.regenerate_scenario_jsons.subprocess.run", recorded)
    monkeypatch.setenv("PYTHONPATH", "/somewhere/the/caller/cares/about")

    regenerate_one(
        setup_path=REPO_ROOT / "system_setups" / "simple_system_setup_one.py",
        python=sys.executable,
        index_pool=LpgBaseIndexPool(slots=1),
        log_dir=tmp_path,
        keep_simulation_json=True,
    )

    assert len(recorded.environments) == 1
    entries = recorded.environments[0]["PYTHONPATH"].split(os.pathsep)
    assert entries[0] == str(REPO_ROOT), f"PYTHONPATH does not lead with the checkout: {entries}"
    assert "/somewhere/the/caller/cares/about" in entries, f"the caller's own entry was dropped: {entries}"


@pytest.mark.base
def test_the_child_is_handed_the_borrowed_base_index(tmp_path: Path, monkeypatch) -> None:
    """The base index borrowed from the pool must arrive in the child's environment.

    The pool and its ``child_environment`` are unit-tested on their own, but ``regenerate_one`` is
    the only place that composes them, and only this test observes that composition: without it the
    driver could stop borrowing, or hand every child the same constant, and no test would fail until
    concurrent workers corrupted each other's ``pylpg/C<index>`` directories again.

    Catches: a refactor of ``regenerate_one`` that drops or bypasses the borrow.
    """
    recorded = RecordedChild()
    monkeypatch.setattr("scripts.regenerate_scenario_jsons.subprocess.run", recorded)

    regenerate_one(
        setup_path=REPO_ROOT / "system_setups" / "simple_system_setup_one.py",
        python=sys.executable,
        index_pool=LpgBaseIndexPool(slots=1),
        log_dir=tmp_path,
        keep_simulation_json=True,
    )

    assert len(recorded.environments) == 1
    handed = recorded.environments[0].get(PylpgWorkspace.INDEX_ENVIRONMENT_VARIABLE)
    assert handed == "1", f"the borrowed base index did not reach the child: {handed!r}"
