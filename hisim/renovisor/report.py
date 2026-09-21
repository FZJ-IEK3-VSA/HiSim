"""``mapping_report.json``: what the translator did with every leaf of the request.

A calculation is only as trustworthy as its account of itself, so the report accounts for the
whole request: every leaf of ``location`` and ``house`` appears exactly once under ``fields``,
every measure appears exactly once under ``measures`` with every option it carried, every
default the translator applied is a line with the value it used, and every
``not_implemented_yet`` line carries the note of its ``not_implemented_yet.yaml`` entry
verbatim.

Its shape is §6 of the calculation-request specification::

    {
      "translator": {"version", "commit", "request_schema_version", "hisim_commit"},
      "base_file": "household_gas_building_sizer.grouped.energy_system.yaml",
      "energy_system_file": "renovisor_<hash>.energy_system.yaml",
      "fields": [{"path", "status", "target?", "value?", "note?"}],
      "measures": [{"id", "status", "options": [{"name", "status", "note?"}], "targets"}],
      "subjects": {"<cost subject>": "<measure id or null>"},
      "unpriced_subjects": ["<cost subject>"]
    }

:meth:`MappingReport.assert_complete` is the invariant as a check rather than as a promise: it
walks the request and raises when a leaf has no line or has two. That is what makes "nothing is
approximated or defaulted silently" (rule 6) a property of the build rather than of anyone's
discipline.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.vocabulary import ReportStatus


class ReportError(Exception):
    """The report does not account for the request, which is a bug in the translator.

    Raised by :meth:`MappingReport.assert_complete`, named after the leaf it is about, and
    turned into exit 3 like every other translator error: a report with a hole in it must not
    reach a user.
    """


@dataclass(frozen=True)
class FieldLine:
    """One line of ``fields``: what became of one request leaf.

    Args:
        path: The leaf's dotted path in the request, e.g.
            ``house.building.facade.u_value_in_watt_per_m2_per_kelvin``.
        status: What the translator did with it.
        target: The HiSim component and field it landed on, when it landed on one.
        value: The value used, which a ``defaulted`` line always carries and a ``used`` line
            carries when the value the request sent is not the value that was written.
        note: The sentence a person reads; for a ``not_implemented_yet`` line it is the
            whitelist entry's note, verbatim and nothing else.
    """

    path: str
    status: ReportStatus
    target: Optional[str] = None
    value: Any = None
    note: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the line as the report writes it, omitting the keys it does not carry."""
        row: Dict[str, Any] = {"path": self.path, "status": self.status.value}
        if self.target is not None:
            row["target"] = self.target
        if self.value is not None:
            row["value"] = self.value
        if self.note is not None:
            row["note"] = self.note
        return row


class HiSimCommit:
    """The commit of the code the translator is running out of, however it was shipped.

    It goes into the mapping report, into the capability document and into
    ``economics_result.json`` so that a stored result can be traced back to the code that produced
    it. The problem it solves is that a container image is not a git checkout: the Dockerfile
    copies the source and leaves ``.git`` behind, so asking git inside the image answers nothing
    and every result the image produced used to claim no provenance at all.

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
    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[2]

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
        """Return the short commit hash of the checkout, or ``None`` when there is no git."""
        try:
            completed = subprocess.run(
                ["git", "-C", str(cls.ROOT), "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return completed.stdout.strip() or None

    @classmethod
    def _shortened(cls, commit: str) -> str:
        """One spelling for all three sources: a full hash is truncated, anything else is kept."""
        stripped = commit.strip()
        if len(stripped) == 40 and all(character in "0123456789abcdef" for character in stripped.lower()):
            return stripped[: cls.SHORT_LENGTH]
        return stripped


class MappingReport:
    """The account of one translation, built as it runs and written beside the file it produced.

    Lines are appended in the order the translator visits the request, and a second line for a
    path replaces the first, because the last word on a field is what happened to it: a measure
    that overwrites a request value writes the line that describes the value simulated.

    Args:
        request_schema_version: The schema version the request declared.
        country: The dwelling's country, which decides whether the header warns about the
            legacy factor tables.
    """

    #: The file this report is written to.
    FILE_NAME: ClassVar[str] = "mapping_report.json"

    #: The two economics keys of the document, named here because a second process reads them:
    #: ``python -m hisim.economics staged`` stamps ``measure_id`` and the ``unpriced`` flag on
    #: every row of ``economics_result.json`` from them, and imports these constants rather than
    #: repeating the spellings, so a rename cannot silently empty the document's two maps.
    SUBJECTS_FIELD: ClassVar[str] = "subjects"
    UNPRICED_SUBJECTS_FIELD: ClassVar[str] = "unpriced_subjects"

    #: The key of the measure half, one entry per measure of the package in package order.
    MEASURES_FIELD: ClassVar[str] = "measures"

    #: What the header says about the legacy per-year fuel-price, emission-factor and device-cost
    #: tables of ``hisim/components/configuration.py``. ``reviewed`` is a country with sourced
    #: rows; ``placeholder`` is one registered with the sentinel ``-1e9`` in every number, which
    #: is why the cost and CO2 entries of that run's ``all_kpis.json`` read as visible nonsense.
    #: The payload publishes none of those leaves -- its operational CO2 comes from the lifecycle
    #: cost engine, which has its own per-country data files -- but a reader of the raw KPI
    #: document needs to know before they read one.
    LEGACY_REVIEWED: ClassVar[str] = "reviewed"
    LEGACY_PLACEHOLDER: ClassVar[str] = "placeholder"

    def __init__(self, request_schema_version: int = 1, country: str = "DE") -> None:
        """Create an empty report for one request."""
        self._schema_version = request_schema_version
        self._country = country
        self._fields: Dict[str, FieldLine] = {}
        self._measures: List[Dict[str, Any]] = []
        self._subjects: Dict[str, Optional[str]] = {}
        self._unpriced_subjects: List[str] = []
        self.base_file: Optional[str] = None
        self.energy_system_file: Optional[str] = None

    def field(
        self,
        path: str,
        status: ReportStatus,
        target: Optional[str] = None,
        value: Any = None,
        note: Optional[str] = None,
    ) -> None:
        """Record (or replace) the line for one request leaf.

        Args:
            path: The leaf's dotted path.
            status: What the translator did with it.
            target: The HiSim component and field it reached, when it reached one.
            value: The value written, which a default always names.
            note: The sentence a person reads.
        """
        self._fields[path] = FieldLine(path=path, status=status, target=target, value=value, note=note)

    def used(self, path: str, target: str, value: Any = None, note: Optional[str] = None) -> None:
        """Record a leaf that was written to a HiSim target exactly as the request stated it."""
        self.field(path, ReportStatus.USED, target=target, value=value, note=note)

    def approximated(self, path: str, note: str, target: Optional[str] = None, value: Any = None) -> None:
        """Record a leaf that was represented by something close but not equal."""
        self.field(path, ReportStatus.APPROXIMATED, target=target, value=value, note=note)

    def defaulted(self, path: str, value: Any, note: str, target: Optional[str] = None) -> None:
        """Record a leaf the request did not carry, with the value the translator used for it."""
        self.field(path, ReportStatus.DEFAULTED, target=target, value=value, note=note)

    def not_implemented_yet(self, path: str, note: str, value: Any = None) -> None:
        """Record a leaf that was accepted and acted on by nothing, with its whitelist note."""
        self.field(path, ReportStatus.NOT_IMPLEMENTED_YET, value=value, note=note)

    def has(self, path: str) -> bool:
        """Return whether a line has already been recorded for one path."""
        return path in self._fields

    def line(self, path: str) -> Optional[FieldLine]:
        """Return the line recorded for one path, or ``None``."""
        return self._fields.get(path)

    def lines(self) -> Tuple[FieldLine, ...]:
        """Return every field line, sorted by path."""
        return tuple(sorted(self._fields.values(), key=lambda entry: entry.path))

    def set_measures(self, measures: List[Dict[str, Any]]) -> None:
        """Store the measure half of the report, as :mod:`hisim.renovisor.apply` produced it."""
        self._measures = list(measures)

    def measures(self) -> Tuple[Dict[str, Any], ...]:
        """Return the measure entries, in package order."""
        return tuple(self._measures)

    def set_subjects(self, subjects: Mapping[str, Optional[str]]) -> None:
        """Store which catalogue measure created which cost subject.

        The economics half of the report, and the reason it exists: the lifecycle cost engine
        names a cost subject after the HiSim component or the envelope measure it prices, and
        knows nothing about the catalogue. ``economics_result.json`` has to stamp a ``measure_id``
        on every row of its investment build-up, so the correspondence is recorded here, where
        the translator that decided it can still see both halves.

        Args:
            subjects: Cost subject -> the measure that created it, or ``None`` for a subject that
                was already in the building.
        """
        self._subjects = dict(subjects)

    def subjects(self) -> Dict[str, Optional[str]]:
        """Return the cost subject to measure map, sorted by subject."""
        return {subject: self._subjects[subject] for subject in sorted(self._subjects)}

    def set_unpriced_subjects(self, subjects: Sequence[str]) -> None:
        """Store the cost subjects the request carried no price for.

        A measure with no price behind it is not left out of the economics -- it is in the plan
        and its cost is unknown, which is a different statement and the honest one. The list
        travels to the staged evaluator, which flags those rows in the result document instead of
        showing them at zero.

        Args:
            subjects: The subject names, in the order the package added them.
        """
        self._unpriced_subjects = list(subjects)

    def unpriced_subjects(self) -> Tuple[str, ...]:
        """Return the cost subjects with no price behind them, in package order."""
        return tuple(self._unpriced_subjects)

    def to_json(self) -> Dict[str, Any]:
        """Return the whole document, ready to be written."""
        return {
            "translator": {
                "version": TRANSLATOR_VERSION,
                "commit": HiSimCommit.of(),
                "request_schema_version": self._schema_version,
                "hisim_commit": HiSimCommit.of(),
                "legacy_factors": self.legacy_factors(),
            },
            "base_file": self.base_file,
            "energy_system_file": self.energy_system_file,
            "fields": [line.to_json() for line in self.lines()],
            self.MEASURES_FIELD: list(self._measures),
            self.SUBJECTS_FIELD: self.subjects(),
            self.UNPRICED_SUBJECTS_FIELD: list(self._unpriced_subjects),
        }

    def legacy_factors(self) -> str:
        """Return whether this country's legacy factor tables are data or sentinel placeholders.

        Returns:
            ``"reviewed"`` or ``"placeholder"``. Ireland is a placeholder until sourced Irish
            fuel prices, emission factors and device costs exist (finding F2), so every legacy
            cost and CO2 entry of an Irish run's ``all_kpis.json`` is ``-1e9``-scaled on
            purpose. Nothing the payload publishes reads one of them.
        """
        from hisim.components.configuration import PlaceholderCountryFactors

        return (
            self.LEGACY_PLACEHOLDER
            if PlaceholderCountryFactors.is_placeholder(self._country)
            else self.LEGACY_REVIEWED
        )

    def assert_complete(self, document: Mapping[str, Any]) -> None:
        """Raise when the report does not account for exactly the leaves the request carries.

        Args:
            document: The validated request, as it arrived.

        Raises:
            ReportError: Naming the leaves with no line. A line for a path the request does not
                carry is allowed and expected -- that is what a ``defaulted`` line is -- so only
                the missing direction is a fault.
        """
        missing = [path for path in self.request_leaves(document) if not self._covers(path)]
        if missing:
            raise ReportError(
                "the mapping report does not account for " + ", ".join(sorted(missing))
            )

    @classmethod
    def request_leaves(cls, document: Mapping[str, Any]) -> Tuple[str, ...]:
        """Return the dotted path of every leaf of ``schema_version``, ``location`` and ``house``.

        The measures are not leaves here: they are accounted for one entry each under
        ``measures``, which is a different invariant and a different test.
        """
        leaves: List[str] = []
        for key in ("schema_version", "location", "house"):
            if key in document:
                leaves.extend(cls._leaves(document[key], key))
        return tuple(leaves)

    @classmethod
    def _leaves(cls, value: Any, prefix: str) -> Iterator[str]:
        """Yield the dotted path of every leaf of a nested JSON-like structure."""
        if isinstance(value, Mapping):
            for key, child in value.items():
                yield from cls._leaves(child, f"{prefix}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from cls._leaves(item, f"{prefix}[{index}]")
        else:
            yield prefix

    def _covers(self, leaf: str) -> bool:
        """Return whether a leaf has a line of its own or lies under a line for its block."""
        if leaf in self._fields:
            return True
        return any(
            leaf.startswith(recorded) and leaf[len(recorded)] in ".["
            for recorded in self._fields
        )
