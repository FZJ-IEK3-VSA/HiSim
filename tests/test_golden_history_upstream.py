"""The rename table checked against the upstream commits of ``golden_references/``.

``scripts/golden_kpi_renames.py`` records, for each rename, the commit that made it; these
two tests compare those claims with ``git log -- golden_references/``. That needs the
unsquashed upstream history: a shallow clone skips them (``requires_history``), and a
squash-synced mirror has history of another shape that makes them fail. So they carry
``upstream_history`` instead of ``base`` and run in the ``others`` CI job, whose checkout
fetches the full history. The rest of the rig's tests are in ``tests/test_golden_history.py``.
"""

from __future__ import annotations

from typing import Dict, List

import pytest

from scripts.golden_history import golden_commits
from scripts.golden_kpi_renames import FLEET_WIDE, KPI_RENAMES, PAIR_RENAMES, KpiRename
from tests import test_golden_history
from tests.test_golden_history import REPO_ROOT, requires_history

pytestmark = pytest.mark.upstream_history

#: The module-scoped fixture of every KPI key each golden ever carried, shared by assignment.
historical_keys = test_golden_history.historical_keys


@requires_history
def test_every_declared_old_kpi_name_really_existed(  # pylint: disable=redefined-outer-name
    historical_keys: Dict[str, set],
) -> None:
    """A rename claims a name the goldens once carried; a typo in it would go unnoticed."""
    for stem, entries in KPI_RENAMES.items():
        for entry in entries:
            if stem == FLEET_WIDE:
                assert any(entry.old in keys for keys in historical_keys.values()), (
                    f"no golden ever carried {entry.old!r}"
                )
            else:
                assert stem in historical_keys, f"no golden file was ever named {stem!r}"
                assert entry.old in historical_keys[stem], (
                    f"{stem!r} never carried {entry.old!r}"
                )


@requires_history
def test_every_rename_names_a_commit_that_touched_the_goldens() -> None:
    """The commit column is the evidence; it has to be a golden commit of this branch."""
    shas = {commit.sha for commit in golden_commits(REPO_ROOT)}
    claims: List[KpiRename] = [entry for entries in KPI_RENAMES.values() for entry in entries]
    for entry in claims:
        assert entry.commit in shas, f"{entry.commit} does not touch golden_references/"
    for rename in PAIR_RENAMES.values():
        assert rename.commit in shas, f"{rename.commit} does not touch golden_references/"
