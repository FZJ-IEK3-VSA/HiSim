"""Where the commit in every stored document comes from, and what it does when nothing says.

:class:`hisim.renovisor.report.HiSimCommit` is the provenance of three documents — the mapping
report, the capability document and ``economics_result.json`` — and it has to work in a container
image, which is not a git checkout. These cases pin the order of its three sources and the one
property none of them may break: a run is never failed, and no document becomes invalid, when no
source can say what commit this is.
"""

import re

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

    def test_a_checkout_answers_with_a_short_hexadecimal_hash(self, monkeypatch):
        """In a developer checkout the answer is git's own short hash, and is shaped like one.

        The earlier version of this case asserted ``commit.strip() == commit``, which is true of
        every string, and branched on whether the machine happened to be a checkout — so no
        production mistake could fail it. What is checkable without a clock and without a network
        is the *shape*: seven to twelve hexadecimal digits, which is what ``git rev-parse
        --short`` produces and what :attr:`HiSimCommit.SHORT_LENGTH` trims a full hash to. The
        case skips where there is no checkout to ask, which is stated rather than tolerated.
        """
        monkeypatch.delenv(HiSimCommit.COMMIT_VARIABLE, raising=False)
        monkeypatch.setattr(HiSimCommit, "COMMIT_FILE", "hisim/COMMIT.absent")
        commit = HiSimCommit.of()
        if commit is None:
            pytest.skip("not a git checkout, so there is no hash to check the shape of")
        assert re.fullmatch(r"[0-9a-f]{7,12}", commit), commit

    def test_the_file_wins_over_the_environment_and_the_environment_over_git(
        self, tmp_path, monkeypatch
    ):
        """The order of the three sources, decided without depending on the machine at all.

        Each source is put in place by the test, so the answer is the one the order prescribes and
        not the one the environment happens to offer: the baked file first because it travels with
        the image it describes, then the variable an orchestrator sets, then git.
        """
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        (tmp_path / "hisim").mkdir()
        (tmp_path / HiSimCommit.COMMIT_FILE).write_text("aaaaaaa", encoding="utf-8")
        monkeypatch.setenv(HiSimCommit.COMMIT_VARIABLE, "bbbbbbb")
        assert HiSimCommit.of() == "aaaaaaa"

        (tmp_path / HiSimCommit.COMMIT_FILE).unlink()
        assert HiSimCommit.of() == "bbbbbbb"

        monkeypatch.delenv(HiSimCommit.COMMIT_VARIABLE)
        assert HiSimCommit.of() is None, "an empty directory is not a checkout"

    def test_a_full_hash_from_any_source_is_shortened_to_one_spelling(self, tmp_path, monkeypatch):
        """Forty hexadecimal characters become seven, so the three sources agree on one form."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.setenv(HiSimCommit.COMMIT_VARIABLE, "0" * 39 + "f")
        assert HiSimCommit.of() == "0" * 7

    def test_the_capability_document_stays_valid_without_a_commit(self, tmp_path, monkeypatch):
        """A null commit must not make the document the frontend validates against invalid."""
        monkeypatch.setattr(HiSimCommit, "ROOT", tmp_path)
        monkeypatch.delenv(HiSimCommit.COMMIT_VARIABLE, raising=False)
        document = CapabilityDocument.build()
        document.validate()
        assert document.body["engine_version"].endswith("unknown")
