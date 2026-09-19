"""The post-processing option list is taken as the simulation parameters give it.

Until September 2026 ``postprocessing_main.py`` read a ``HISIM_IN_DOCKER_CONTAINER`` environment
variable, set by the Dockerfile, and narrowed the option list to an allow-list written in 2022 for
the UTSP batch workers (CSV export and KPIs, later the legacy cost and KPI-JSON writers). Every
option added since was silently dropped inside a container until someone noticed; the lifecycle
cost stage was the last victim, which left the RenoVisor backend with an exit code 0 and three
missing artifacts. The owner decided on 2026-09-19 to remove the override rather than widen it:
the caller's simulation file is authoritative, inside a container as anywhere else.

These tests pin that decision at the two places the override lived, so it cannot creep back in
as a "small container safety" change.
"""

import inspect
from pathlib import Path
from typing import ClassVar

import pytest

from hisim.postprocessing import postprocessing_main


class OverrideTraces:
    """Where the removed override lived and the strings that would betray its return."""

    #: Any environment read in the post-processing module is suspect: nothing in it should depend
    #: on where the process runs.
    ENVIRONMENT_READS: ClassVar[tuple[str, ...]] = ("os.getenv(", "os.environ")
    #: The variable the Dockerfile used to set and the module used to read.
    FLAG_NAME: ClassVar[str] = "HISIM_IN_DOCKER_CONTAINER"
    #: The repository's Dockerfile, relative to this test file.
    DOCKERFILE: ClassVar[Path] = Path(__file__).resolve().parents[1] / "Dockerfile"


@pytest.mark.base
def test_the_post_processing_module_reads_no_environment_variable() -> None:
    """The option list comes from the parameters alone; the module never consults the environment."""
    source = inspect.getsource(postprocessing_main)
    for trace in OverrideTraces.ENVIRONMENT_READS:
        assert trace not in source, (
            f"postprocessing_main reads the environment ({trace!r}); the option list is meant to be "
            "taken as the simulation parameters give it, in a container as anywhere else."
        )
    assert OverrideTraces.FLAG_NAME not in source


@pytest.mark.base
def test_the_dockerfile_sets_no_container_flag() -> None:
    """The image no longer announces itself to HiSim; there is nothing left that would listen."""
    dockerfile = OverrideTraces.DOCKERFILE.read_text(encoding="utf-8")
    assert OverrideTraces.FLAG_NAME not in dockerfile
