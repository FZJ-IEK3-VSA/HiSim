"""Where the commit in every stored document comes from, and what it does when nothing says.

:class:`hisim.renovisor.report.HiSimCommit` is the provenance of three documents — the mapping
report, the capability document and ``economics_result.json`` — and it has to work in a container
image, which is not a git checkout. These cases pin the order of its three sources and the one
property none of them may break: a run is never failed, and no document becomes invalid, when no
source can say what commit this is.
"""

import pytest

from hisim.renovisor.capabilities import CapabilityDocument
from hisim.renovisor.report import HiSimCommit

pytestmark = pytest.mark.base


class TestHiSimCommit:
    """The three sources, in order, and the ``None`` that is a legitimate fourth answer."""

    #: A full-length hash, to check that the three sources end up spelled the same way.
    FULL_HASH = "0123456789abcdef0123456789abcdef01234567"

    def test_the_baked_file_wins(self, tmp_path, monkeypatch):
        """``hisim/COMMIT`` is what the image was built from, so it answers before anything else."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.setenv(HiSimCommit.COMMIT_VARIABLE, "from-the-environment")
        (tmp_path / "hisim").mkdir()
        (tmp_path / HiSimCommit.COMMIT_FILE).write_text(self.FULL_HASH + "\n", encoding="utf-8")
        assert HiSimCommit.of() == self.FULL_HASH[: HiSimCommit.SHORT_LENGTH]

    def test_the_environment_is_read_when_the_file_is_absent(self, tmp_path, monkeypatch):
        """An image built without the file can still be told its revision by its orchestrator."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.setenv(HiSimCommit.COMMIT_VARIABLE, self.FULL_HASH)
        assert HiSimCommit.of() == self.FULL_HASH[: HiSimCommit.SHORT_LENGTH]

    def test_an_empty_file_does_not_shadow_the_environment(self, tmp_path, monkeypatch):
        """A marker file that says nothing is not an answer, and the next source is asked."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.setenv(HiSimCommit.COMMIT_VARIABLE, "abc1234")
        (tmp_path / "hisim").mkdir()
        (tmp_path / HiSimCommit.COMMIT_FILE).write_text("   \n", encoding="utf-8")
        assert HiSimCommit.of() == "abc1234"

    def test_no_source_at_all_is_none_rather_than_a_failure(self, tmp_path, monkeypatch):
        """Provenance never fails a calculation: an unknown commit is stated as unknown."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.delenv(HiSimCommit.COMMIT_VARIABLE, raising=False)
        assert HiSimCommit.of() is None

    def test_the_git_case_is_unchanged(self, monkeypatch):
        """In a developer checkout the answer is still git's own short hash, as before."""
        monkeypatch.delenv(HiSimCommit.COMMIT_VARIABLE, raising=False)
        commit = HiSimCommit.of()
        assert commit is None or commit.strip() == commit

    def test_the_capability_document_stays_valid_without_a_commit(self, tmp_path, monkeypatch):
        """A null commit must not make the document the frontend validates against invalid."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.delenv(HiSimCommit.COMMIT_VARIABLE, raising=False)
        document = CapabilityDocument.build()
        document.validate()
        assert document.body["engine_version"].endswith("unknown")
