"""The commit of the HiSim code that is running, for the provenance block of every document that names one.

The mapping report, the capability document and ``economics_result.json`` each record which code produced them.
:class:`HiSimCommit` answers that question in one place, below both the translator (``hisim.renovisor``) and the cost
engine (``hisim.economics``), so neither has to import the other for it.

Example::

    from hisim.hisim_commit import HiSimCommit

    HiSimCommit.of()  # "8f307a5", or None when no source states a commit
"""

import functools
import os
import subprocess
from pathlib import Path
from typing import ClassVar, Optional


class HiSimCommit:
    """The commit of the HiSim code that is running, however it was shipped.

    It goes into the mapping report, into the capability document and into
    ``economics_result.json`` so that a stored result can be traced back to the code that produced
    it. A container image is not a git checkout: the Dockerfile copies the source and leaves
    ``.git`` behind, so asking git inside the image answers nothing, and the image states its
    commit another way.

    Three sources are tried, in the order of how much they are worth trusting:

    1. ``hisim/COMMIT`` — a one-line file the image build writes (``RUN printf '%s'
       "$HISIM_COMMIT" > hisim/COMMIT``, from the Dockerfile's ``ARG HISIM_COMMIT``), which is the
       commit the image was *built from* and travels with it. ``printf '%s'`` rather than ``echo``
       on purpose: the file carries the hash and no trailing newline;
    2. the ``HISIM_COMMIT`` environment variable, for a container run whose orchestrator knows
       the revision but whose image was built without the file;
    3. ``git rev-parse --short HEAD`` in the checkout, which is the developer case and is
       unchanged in behaviour, including the ``None`` it returns when there is no git.

    ``None`` stays a legitimate answer throughout: a checkout without git, an installed package
    outside a repository, an image built without either marker. A calculation is never failed over
    provenance, and the capability document stays valid with a null commit.

    Example::

        HISIM_COMMIT=8f307a53 python -m hisim.renovisor translate request.json --out jobs/abc
    """

    #: Where the repository is, relative to this module.
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    #: The baked marker file, relative to the repository root. Written by the image build.
    COMMIT_FILE: ClassVar[str] = "hisim/COMMIT"

    #: The environment variable read when the file is absent.
    COMMIT_VARIABLE: ClassVar[str] = "HISIM_COMMIT"

    #: What a document whose schema demands a string says when no source knows the commit. The
    #: capability document is validated against the vendored contract schema, which types
    #: ``translator.commit`` as a string, so a null there would make the document invalid rather
    #: than merely uninformative; :meth:`or_unknown` is what such a caller uses.
    UNKNOWN: ClassVar[str] = "unknown"

    #: How many characters of a full hash the short form keeps, so the three sources agree on
    #: one spelling. Git's own ``--short`` default is seven; a baked file is normally already
    #: short and is truncated only when it carries a full hash.
    SHORT_LENGTH: ClassVar[int] = 7

    @classmethod
    def of(cls) -> Optional[str]:
        """Return the short commit of this HiSim, or ``None`` when nothing states one.

        Returns:
            The commit as the baked file, the environment or git gives it, shortened to
            :attr:`SHORT_LENGTH` when it is a full 40-character hash; ``None`` when no source
            answers.
        """
        for candidate in (cls._baked(), cls._environment(), cls._git()):
            if candidate:
                return cls._shortened(candidate)
        return None

    @classmethod
    def or_unknown(cls) -> str:
        """The short commit, or :attr:`UNKNOWN` — for the documents that cannot carry a null.

        Returns:
            :meth:`of`, or ``"unknown"`` when it answered ``None``.
        """
        return cls.of() or cls.UNKNOWN

    @classmethod
    def _baked(cls) -> Optional[str]:
        """The commit the image build wrote into ``hisim/COMMIT``, or ``None``."""
        path = cls.ROOT / cls.COMMIT_FILE
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        return text.strip() or None

    @classmethod
    def _environment(cls) -> Optional[str]:
        """The commit the run's environment states in ``HISIM_COMMIT``, or ``None``."""
        return (os.environ.get(cls.COMMIT_VARIABLE) or "").strip() or None

    @classmethod
    def _git(cls) -> Optional[str]:
        """Return the short commit hash of the checkout, or ``None`` when there is no git.

        Asked once per process and per :attr:`ROOT` (:meth:`_git_at`): the lookup is a ``git``
        subprocess, and a probe run reaches it twice per probe through
        :meth:`MappingReport.to_json`.
        """
        return cls._git_at(cls.ROOT)

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _git_at(root: Path) -> Optional[str]:
        """Return ``git rev-parse --short HEAD`` in *root*, remembered for the life of the process.

        The memo is safe because the answer is a process-lifetime constant in every case that
        matters: the code a process runs is the code it imported when it started, so a checkout
        moved to another commit underneath a running process would make a fresh answer *less*
        true of that process, not more; and where there is no git (an image, an installed package)
        the ``None`` does not change either. Keyed on the root, so a caller that points
        :attr:`ROOT` somewhere else is asked afresh. :meth:`forget` clears it.
        """
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return completed.stdout.strip() or None

    @classmethod
    def forget(cls) -> None:
        """Clear the remembered git answer, so the next :meth:`of` asks git again (tests do)."""
        cls._git_at.cache_clear()

    @classmethod
    def _shortened(cls, commit: str) -> str:
        """One spelling for all three sources: a full hash is truncated, anything else is kept."""
        stripped = commit.strip()
        if len(stripped) == 40 and all(character in "0123456789abcdef" for character in stripped.lower()):
            return stripped[: cls.SHORT_LENGTH]
        return stripped
