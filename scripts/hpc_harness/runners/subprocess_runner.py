"""Generic subprocess runner (spec §4.4): arbitrary command per job.

Payload contract: ``{"argv": ["prog", "--out", "{result_dir}", ...], "cwd"?: "..."}``.
Substitutions in every argv element: ``{result_dir}`` → the job's staging dir, and
``{python}`` → ``sys.executable`` (the worker's own interpreter), so Python-based
commands always run in the correct environment instead of whatever ``python`` is on
PATH. Forgoes the warm-start benefit, which non-Python programs cannot use anyway.
"""

import subprocess
import sys


class SubprocessRunner:
    """Runs one external command per job."""

    name = "subprocess"

    def warmup(self) -> None:
        """Nothing to warm up."""

    def on_fork(self) -> None:
        """Nothing to re-initialize."""

    def run(self, payload: dict, result_dir: str) -> None:
        """Run the payload argv; non-zero exit raises."""
        argv = [
            str(a).replace("{result_dir}", result_dir).replace("{python}", sys.executable)
            for a in payload["argv"]
        ]
        completed = subprocess.run(
            argv, cwd=payload.get("cwd") or result_dir, check=False
        )
        if completed.returncode != 0:
            raise RuntimeError(f"command {argv[0]} exited with {completed.returncode}")
