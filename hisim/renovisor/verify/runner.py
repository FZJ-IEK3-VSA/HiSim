"""Tier 1 of the path verification: every probe through the translation layer, stages 1 to 3.

For each probe the runner obtains two sets of artefacts -- the base's and the probe's -- by running
``validate`` + ``apply`` + ``translate`` and no simulation, and compares them:

* **stage 1**, the request: the diff from the base request to the probe's, which must be exactly
  the probe's change -- non-empty, and every changed leaf under a path the probe declares;
* **stage 2**, the mapping report: the line the probe's report carries for every changed leaf,
  beside the status the capability document announces for it;
* **stage 3**, the energy system: the diff from the base's translated file (and economic
  context) to the probe's, at configuration-field level. It is *shown*, not judged: there are no
  expectation tables yet (spec §9 step 2 is a person's work). The one verdict tier 1 draws from it
  is "no effect": an empty diff for a probe whose changed leaf the report calls ``used``.

Artefacts are cached by request hash (:class:`ArtefactCache`), the recipe the backend keys its
jobs by, so a probe that is another probe's base is translated once.

What fails the run (spec §6, what applies without expectations) is an :class:`Issue` under
``failures`` (:class:`IssueCode`): a settable thing no probe changes, a stage-1 diff that is not the
probe's change, a changed leaf whose status is below the one the capability document announces for
it (``approximated``, ``defaulted`` or ``not_implemented_yet`` under an announced ``used``;
``defaulted`` or ``not_implemented_yet`` under an announced ``approximated``), a translation that
raised, a probe the request *schema* refuses (the probe is broken), and a base that did not
translate. The announced status is the one the document's ``conditions`` give for the probe's own
request, as the frontend reads it (measure-capabilities 0.5.0), so a pair probe is held to the
condition its combination announces and every other probe to the unconditional status. A probe a
*semantic* check refuses (``added_insulation.not_allowed``, ``location.country.unsupported``) is
refused on purpose: its ``map`` cell is ◐ with the problem code. What does not fail the run is a
finding: "no effect".
"""

import dataclasses
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.capabilities import Aggregation, Conditions, Probe, ProbeResult, ProbeRunner, value_key
from hisim.renovisor.report import HiSimCommit
from hisim.renovisor.request import Problem, Request, RequestError
from hisim.renovisor.verify.leaves import ABSENT, Change, RequestLeaves, SystemLeaves, edit_sources
from hisim.renovisor.verify.probes import Completeness, MissingProbe, ProbeBases, VerificationProbe
from hisim.renovisor.vocabulary import ReportStatus
from hisim.renovisor.whitelist import Whitelist


class CellState(str, Enum):
    """The state of one cell of the matrix, the spec's §5 legend."""

    AS_EXPECTED = "as_expected"
    AS_LISTED = "as_listed"
    NO_EFFECT = "no_effect"
    FAILED = "failed"
    NOT_RUN = "not_run"

    @property
    def glyph(self) -> str:
        """Return the symbol the matrix draws."""
        return {
            CellState.AS_EXPECTED: "●",
            CellState.AS_LISTED: "◐",
            CellState.NO_EFFECT: "○",
            CellState.FAILED: "✖",
            CellState.NOT_RUN: "–",
        }[self]

    @property
    def legend(self) -> str:
        """Return what the legend says the symbol means."""
        return {
            CellState.AS_EXPECTED: "as expected",
            CellState.AS_LISTED: "approximated / not_implemented_yet / defaulted, as listed (a combination's "
            "as its condition announces); a request a semantic check refuses, as intended",
            CellState.NO_EFFECT: "no effect",
            CellState.FAILED: "failed",
            CellState.NOT_RUN: "not run: the anchor's system, a request identical to its base, or a system a "
            "semantic check left untranslated",
        }[self]


class Stage(str, Enum):
    """The three columns of tier 1."""

    REQUEST = "req"
    MAPPING = "map"
    SYSTEM = "sys"


class IssueCode(str, Enum):
    """What kind of failure or finding an :class:`Issue` is; the value is what ``report.json`` carries."""

    MISSING_PROBE = "missing_probe"
    REQUEST_DIFF = "request_diff"
    STATUS_BELOW_ANNOUNCED = "status_below_announced"
    TRANSLATION_ERROR = "translation_error"
    PROBE_REFUSED_BY_SCHEMA = "probe_refused_by_schema"
    BASE_NOT_TRANSLATED = "base_not_translated"
    NO_EFFECT = "no_effect"

    @property
    def is_failure(self) -> bool:
        """Return whether an issue of this kind fails the run; the other kinds are findings."""
        return self is not IssueCode.NO_EFFECT


@dataclass(frozen=True)
class Issue:
    """One failure or finding, named.

    Args:
        code: What kind (:class:`IssueCode`).
        message: One sentence a person reads.
        probe: The probe it is about, when it is about one.
    """

    code: IssueCode
    message: str
    probe: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the issue as ``report.json`` carries it."""
        row: Dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.probe is not None:
            row["probe"] = self.probe
        return row


@dataclass(frozen=True)
class Refusal:
    """Why the request validation refused a request.

    Args:
        problems: The problems, as ``problems.json`` would carry them.
        structural: Whether the JSON Schema refused it (:attr:`RequestError.structural`) -- a
            broken probe -- rather than a semantic check, which refuses a request on purpose.
    """

    problems: Tuple[Problem, ...]
    structural: bool

    @property
    def codes(self) -> Tuple[str, ...]:
        """Return the problem codes, in order."""
        return tuple(problem.code.value for problem in self.problems)

    @property
    def by(self) -> str:
        """Return who refused: ``schema`` or ``semantic_check``."""
        return "schema" if self.structural else "semantic_check"

    def describe(self) -> str:
        """Return every problem as ``code at path: message``, joined."""
        return "; ".join(f"{problem.code.value} at {problem.path}: {problem.message}" for problem in self.problems)

    def to_json(self) -> Dict[str, Any]:
        """Return the refusal as ``report.json`` carries it."""
        return {"by": self.by, "problems": [problem.to_json() for problem in self.problems]}


@dataclass(frozen=True)
class Artefacts:
    """What the translation layer produced for one request: a refusal, an error, or a translation.

    Args:
        request_hash: The cache key.
        refusal: Why the validation refused the request; ``None`` otherwise.
        error: ``Type: message`` of a translation that raised; ``None`` otherwise.
        traceback: The whole traceback of that error.
        report: ``mapping_report.json``, when the translation finished.
        system: The translated file and its economic context, flattened.
        sources: Edit location -> the request path that asked for it.
        hits: The whitelist entries the translation matched.

    Raises:
        ValueError: When more than one of the three outcomes is set, or a translation is half there.
    """

    request_hash: str
    refusal: Optional[Refusal] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    report: Optional[Dict[str, Any]] = None
    system: Optional[SystemLeaves] = None
    sources: Dict[str, str] = field(default_factory=dict)
    hits: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse artefacts that are refused and raised, or refused (or raised) and translated."""
        translated = self.report is not None or self.system is not None
        outcomes = [self.refusal is not None, self.error is not None, translated]
        if sum(outcomes) != 1:
            raise ValueError(f"artefacts of {self.request_hash} must be exactly one of refused, raised, translated")
        if translated and not self.finished:
            raise ValueError(f"artefacts of {self.request_hash} carry a report or a system but not both")
        if self.traceback is not None and self.error is None:
            raise ValueError(f"artefacts of {self.request_hash} carry a traceback without an error")

    @property
    def finished(self) -> bool:
        """Return whether the translation produced a file and a report."""
        return self.report is not None and self.system is not None

    def outcome(self) -> str:
        """Return what happened to the request in a few words, for a remark."""
        if self.refusal is not None:
            return f"refused by the {self.refusal.by.replace('_', ' ')}: {', '.join(self.refusal.codes)}"
        if self.error is not None:
            return f"raised {self.error}"
        return "translated"


class ArtefactCache:
    """Translates requests, one translation per distinct request hash.

    Each translation is :meth:`ProbeRunner.translate`, the capability run's own pipeline.

    Args:
        base_files_directory: Where the recorded twins live.
        whitelist: The list the translations run against; one instance, its hit record cleared
            before every translation.
    """

    def __init__(self, base_files_directory: Optional[Path] = None, whitelist: Optional[Whitelist] = None) -> None:
        """Create an empty cache; nothing is translated until :meth:`get`."""
        self._runner = ProbeRunner(base_files_directory, whitelist)
        self._artefacts: Dict[str, Artefacts] = {}
        self.lookups = 0

    @property
    def translations(self) -> int:
        """Return how many distinct requests were translated."""
        return len(self._artefacts)

    def get(self, document: Mapping[str, Any], key: Optional[str] = None) -> Artefacts:
        """Return the artefacts of one request, translating it the first time it is asked for.

        Args:
            document: The request body.
            key: Its hash (:meth:`Request.hash_of`) when the caller holds it already, as a
                :class:`~hisim.renovisor.verify.probes.VerificationProbe` does, so a hit costs no
                hashing; computed from *document* when omitted.
        """
        self.lookups += 1
        if key is None:
            key = Request.hash_of(document)
        if key not in self._artefacts:
            self._artefacts[key] = self._translate(key, document)
        return self._artefacts[key]

    def _translate(self, key: str, document: Mapping[str, Any]) -> Artefacts:
        """Run the capability pipeline on one request and keep what the harness compares."""
        try:
            translated = self._runner.translate(document)
            rendered = EnergySystemEmitter.to_document(translated.model)
        except RequestError as error:
            return Artefacts(request_hash=key, refusal=Refusal(problems=error.problems, structural=error.structural))
        except Exception as error:  # pylint: disable=broad-except  # every failure is reported by name
            return Artefacts(
                request_hash=key, error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc()
            )
        return Artefacts(
            request_hash=key,
            report=translated.report.to_json(),
            system=SystemLeaves.of(rendered, translated.economic_context, translated.base_file_name),
            sources=edit_sources(translated.edits),
            hits=self._runner.whitelist.hits(),
        )


@dataclass(frozen=True)
class Announced:
    """What the capability document announces for one leaf of one request.

    Args:
        status: The status, in the report's words (a measure's ``supported`` read back as ``used``):
            the worst of the ``conditions`` that hold for the request, else the unconditional one.
        conditions: The conditions that hold, as the document carries them; empty when the
            unconditional status applies.
    """

    status: str
    conditions: Tuple[Mapping[str, Any], ...] = ()


class Announcements:
    """What the capability document announces, per measure, option, value and inventory field.

    Built with :meth:`~hisim.renovisor.capabilities.Aggregation.entries` from the same probe run, so
    it is the document ``python -m hisim.renovisor capabilities`` writes for this build, conditions
    included. Every lookup takes the request it is asked for and applies the entry's ``conditions``
    to it as the shared spec says a consumer does: when one or more hold, the worst of their
    statuses; otherwise the unconditional status. The measure-level word ``supported`` is read back
    as the report's ``used``.
    """

    def __init__(self, results: Sequence[ProbeResult]) -> None:
        """Aggregate the probe results the way the capability document does."""
        measures, fields = Aggregation.entries(results)
        self._measures: Dict[str, Mapping[str, Any]] = {}
        self._options: Dict[Tuple[str, str], Mapping[str, Any]] = {}
        for entry in measures:
            self._measures[entry["measure_id"]] = entry
            for option in entry["options"]:
                self._options[(entry["measure_id"], option["name"])] = option
        self._fields: Dict[str, Mapping[str, Any]] = {entry["path"]: entry for entry in fields}

    def measure(self, measure_id: str, document: Mapping[str, Any]) -> Optional[Announced]:
        """Return what is announced for one measure in one request."""
        entry = self._measures.get(measure_id)
        return None if entry is None else self._resolve(entry, document)

    def option(self, measure_id: str, name: str, value: Any, document: Mapping[str, Any]) -> Optional[Announced]:
        """Return what is announced for one option at one value: the value's entry, else the option's."""
        entry = self._options.get((measure_id, name))
        return None if entry is None else self._resolve(self._at(entry, value), document)

    def field(self, path: str, value: Any, document: Mapping[str, Any]) -> Optional[Announced]:
        """Return what is announced for one inventory path at one value: the value's entry, else the field's."""
        entry = self._fields.get(path)
        return None if entry is None else self._resolve(self._at(entry, value), document)

    @staticmethod
    def _at(entry: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
        """Return the ``values`` entry of *value*, or *entry* itself when it has none."""
        key = value_key(value)
        return next((item for item in entry.get("values", []) if value_key(item["value"]) == key), entry)

    @staticmethod
    def _resolve(entry: Mapping[str, Any], document: Mapping[str, Any]) -> Announced:
        """Apply an entry's conditions to one request."""
        holding = tuple(
            condition for condition in entry.get("conditions", []) if Conditions.holds(condition["when"], document)
        )
        words = [str(condition["status"]) for condition in holding] or [str(entry["status"])]
        statuses = [ReportStatus.USED if word == "supported" else ReportStatus(word) for word in words]
        return Announced(ReportStatus.worst_of(*statuses).value, holding)


@dataclass(frozen=True)
class StatusLine:
    """What the probe's mapping report says about one changed leaf (stage 2).

    Args:
        path: The changed leaf, or the option or measure it belongs to.
        line: The report entry it was read from: a ``fields`` path, ``measures[id=<id>]`` or
            ``measures[id=<id>].options.<name>``.
        status: The status the report carries; ``None`` when it carries no line at all.
        target: The HiSim target the line names.
        value: The value the line names.
        note: The line's sentence.
        announced: The status the capability document announces for the leaf at this value in this
            request, its ``conditions`` applied.
        removed: Whether the probe removes the leaf rather than setting it; a removed leaf is
            expected to be ``defaulted`` and is not held to the announcement.
        conditions: The document's conditions that hold for this request and gave *announced*;
            empty when the unconditional status did.

    A line is below its announcement (:attr:`below_announcement`) when its status ranks under the
    announced one in :attr:`RANK`: ``approximated``, ``defaulted`` or ``not_implemented_yet`` under
    an announced ``used`` (the approximated case by owner decision, 2026-09-26), ``defaulted`` or
    ``not_implemented_yet`` under an announced ``approximated``.
    """

    path: str
    line: str
    status: Optional[str]
    target: Optional[str] = None
    value: Any = None
    note: Optional[str] = None
    announced: Optional[str] = None
    removed: bool = False
    conditions: Tuple[Mapping[str, Any], ...] = ()

    #: How much of a leaf a status acts on; an announcement not ranked here promises nothing.
    RANK: ClassVar[Dict[str, int]] = {
        ReportStatus.USED.value: 2,
        ReportStatus.APPROXIMATED.value: 1,
        ReportStatus.DEFAULTED.value: 0,
        ReportStatus.NOT_IMPLEMENTED_YET.value: 0,
    }

    @property
    def below_announcement(self) -> bool:
        """Return whether the report acts on a leaf less than the document announces it does."""
        if self.removed or self.status is None or self.announced is None:
            return False
        promised = self.RANK.get(self.announced, 0)
        return promised > 0 and self.RANK.get(self.status, 0) < promised

    def announcement(self) -> str:
        """Return the announced status with the terms of the conditions that gave it, for a remark."""
        if not self.conditions:
            return str(self.announced)
        terms = " or ".join(Conditions.describe(condition["when"]) for condition in self.conditions)
        return f"{self.announced} (where {terms})"

    def to_json(self) -> Dict[str, Any]:
        """Return the line as ``report.json`` carries it."""
        row: Dict[str, Any] = {"path": self.path, "line": self.line, "status": self.status}
        for key in ("target", "value", "note", "announced"):
            if getattr(self, key) is not None:
                row[key] = getattr(self, key)
        if self.removed:
            row["removed"] = True
        if self.conditions:
            row["conditions"] = [dict(condition) for condition in self.conditions]
        return row


class StatusLines:
    """Reads the stage-2 lines of one probe out of its mapping report."""

    @classmethod
    def of(
        cls,
        changes: Sequence[Change],
        report: Mapping[str, Any],
        document: Mapping[str, Any],
        announcements: Announcements,
    ) -> Tuple[StatusLine, ...]:
        """Return one line per report entry the changed leaves reach, in change order.

        Args:
            changes: The stage-1 diff.
            report: The probe's mapping report.
            document: The probe's request, for a cost block's position in the package.
            announcements: What the capability document announces.

        Returns:
            The lines, one per report entry: the leaves of one material object share their
            option's line. An empty container that appears or disappears beside a leaf inside it
            -- ``measures[id=battery_system].options`` giving way to its first option -- is
            structure, not a setting, and reaches no line: it is not the measure removed.
        """
        fields = {row["path"]: row for row in report.get("fields", [])}
        measures = {row["id"]: row for row in report.get("measures", [])}
        positions = {
            str(entry.get("id")): index for index, entry in enumerate(document.get("measures") or [])
        }
        lines: Dict[str, StatusLine] = {}
        for change in changes:
            if change.is_empty_container and any(
                other.path != change.path and RequestLeaves.is_under(other.path, change.path) for other in changes
            ):
                continue
            line = cls._line(change, fields, measures, positions, (announcements, document))
            lines.setdefault(line.line, line)
        return tuple(lines.values())

    @classmethod
    def _line(
        cls,
        change: Change,
        fields: Mapping[str, Mapping[str, Any]],
        measures: Mapping[str, Mapping[str, Any]],
        positions: Mapping[str, int],
        announcing: Tuple[Announcements, Mapping[str, Any]],
    ) -> StatusLine:
        """Return the report line one changed leaf reaches, beside what is announced for it in this request."""
        announcements, document = announcing
        removed = change.after is ABSENT
        path = change.path
        if not path.startswith(f"{RequestLeaves.MEASURES}[id="):
            row = cls._nearest(path, fields)
            if row is None:
                return StatusLine(path=path, line=path, status=None, removed=removed)
            return cls._announced(
                StatusLine(
                    path=path,
                    line=str(row["path"]),
                    status=str(row["status"]),
                    target=row.get("target"),
                    value=row.get("value"),
                    note=row.get("note"),
                    removed=removed,
                ),
                None if removed else announcements.field(path, change.after, document),
            )
        measure_id = path[len(f"{RequestLeaves.MEASURES}[id="):path.index("]")]
        prefix = RequestLeaves.measure_path(measure_id)
        rest = path[len(prefix):]
        if rest.startswith(".cost"):
            key = f"{RequestLeaves.MEASURES}[{positions.get(measure_id, '?')}].cost"
            row = fields.get(key)
            return StatusLine(
                path=f"{prefix}.cost",
                line=key,
                status=None if row is None else str(row["status"]),
                note=None if row is None else row.get("note"),
                removed=removed,
            )
        measure = measures.get(measure_id)
        if rest.startswith(".options."):
            name = rest[len(".options."):].split(".")[0]
            option_path = f"{prefix}.options.{name}"
            option = next((row for row in (measure or {}).get("options", []) if row.get("name") == name), None)
            return cls._announced(
                StatusLine(
                    path=option_path,
                    line=option_path,
                    status=None if option is None else str(option["status"]),
                    note=None if option is None else option.get("note"),
                    removed=removed,
                ),
                None if removed else announcements.option(measure_id, name, cls._option_value(change), document),
            )
        return cls._announced(
            StatusLine(
                path=prefix,
                line=prefix,
                status=None if measure is None else str(measure["status"]),
                target=(", ".join(measure.get("targets", [])) or None) if measure is not None else None,
                note=None if measure is None else measure.get("note"),
                removed=removed,
            ),
            None if removed else announcements.measure(measure_id, document),
        )

    @staticmethod
    def _announced(line: StatusLine, announced: Optional[Announced]) -> StatusLine:
        """Return *line* with what the document announces for it in this request."""
        if announced is None:
            return line
        return dataclasses.replace(line, announced=announced.status, conditions=announced.conditions)

    @staticmethod
    def _nearest(path: str, fields: Mapping[str, Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        """Return the line of *path*, or of the nearest block above it that carries one."""
        candidate = path
        while candidate:
            if candidate in fields:
                return fields[candidate]
            if "." not in candidate:
                return None
            candidate = candidate.rsplit(".", 1)[0]
        return None

    @staticmethod
    def _option_value(change: Change) -> Any:
        """Return the value an option change sends; a material's leaf stands for the material."""
        return change.after


@dataclass(frozen=True)
class ProbeVerdict:
    """Everything tier 1 says about one probe.

    Args:
        probe: The probe with its base.
        tested: The probe's artefacts.
        base: The base's artefacts; ``None`` for the anchor.
        stage_one: The request diff.
        stray: The changed leaves under no declared path.
        stage_two: The report lines of the changed leaves.
        stage_three: The energy-system diff; empty when either side did not translate.
        cells: The state of each column.
        remarks: One sentence per column, saying why it has its state.
    """

    probe: VerificationProbe
    tested: Artefacts
    base: Optional[Artefacts]
    stage_one: Tuple[Change, ...]
    stray: Tuple[str, ...]
    stage_two: Tuple[StatusLine, ...]
    stage_three: Tuple[Change, ...]
    cells: Dict[Stage, CellState]
    remarks: Dict[Stage, str]

    @property
    def base_is_broken(self) -> bool:
        """Return whether the base failed to translate in a way that is not intended.

        A base that raised or that the request schema refuses is broken. A base a semantic check
        refuses is broken only under a probe that translates, since the probe then has nothing to
        be compared with; under a probe the semantic checks refuse as well -- an
        ``added_insulation`` leaf measured from another -- both refusals are the intended ones.
        """
        base = self.base
        if base is None or base.finished:
            return False
        if base.refusal is None or base.refusal.structural:
            return True
        return self.tested.refusal is None

    def to_json(self) -> Dict[str, Any]:
        """Return the probe as ``report.json`` carries it."""
        probe = self.probe
        base_file = {
            "before": None if self.base is None or self.base.system is None else self.base.system.base_file,
            "after": None if self.tested.system is None else self.tested.system.base_file,
        }
        row: Dict[str, Any] = {
            "name": probe.name,
            "kind": probe.probe.kind.value,
            "category": probe.category,
            "subject": probe.probe.subject,
            "value": probe.probe.value,
            "request_hash": self.tested.request_hash,
            "base": {
                "name": probe.base_name,
                "request_hash": None if self.base is None else self.base.request_hash,
                "reason": probe.base_reason,
            },
            "declared_changes": list(probe.changes),
            "cells": {stage.value: self.cells[stage].value for stage in Stage},
            "remarks": {stage.value: self.remarks[stage] for stage in Stage if self.remarks.get(stage)},
            "stage1": {"changes": [change.to_json() for change in self.stage_one], "stray": list(self.stray)},
            "stage2": [line.to_json() for line in self.stage_two],
            "stage3": {"base_file": base_file, "changes": [change.to_json() for change in self.stage_three]},
            "stage4": "not run (tier 2)",
            "stage5": "not run (tier 2)",
        }
        if self.tested.refusal is not None:
            row["refused"] = self.tested.refusal.to_json()
        if self.tested.error is not None:
            row["error"] = self.tested.error
            row["traceback"] = self.tested.traceback
        return row


@dataclass
class VerificationReport:
    """The whole tier-1 run: one verdict per probe, the failures and the findings.

    Args:
        verdicts: One per probe, in probe order.
        missing: The settable things no probe changes; empty when completeness was not checked.
        completeness_checked: Whether the run covered the whole probe set, which completeness
            is only meaningful for.
        translations: How many distinct requests were translated.
        lookups: How many artefact lookups the run made, bases included.

    Raises:
        ValueError: When *missing* lists gaps although completeness was not checked.
    """

    verdicts: Tuple[ProbeVerdict, ...]
    missing: Tuple[MissingProbe, ...]
    completeness_checked: bool
    translations: int
    lookups: int

    #: The tier this report is.
    TIER: ClassVar[int] = 1

    def __post_init__(self) -> None:
        """Refuse gaps that were never looked for."""
        if self.missing and not self.completeness_checked:
            raise ValueError("a report lists missing probes only when completeness was checked")

    def failures(self) -> Tuple[Issue, ...]:
        """Return every failure: completeness first, then per probe in probe order."""
        issues: List[Issue] = [Issue(IssueCode.MISSING_PROBE, gap.message()) for gap in self.missing]
        for verdict in self.verdicts:
            name = verdict.probe.name
            tested = verdict.tested
            if verdict.cells[Stage.REQUEST] is CellState.FAILED:
                issues.append(Issue(IssueCode.REQUEST_DIFF, verdict.remarks[Stage.REQUEST], name))
            if tested.error is not None:
                issues.append(Issue(IssueCode.TRANSLATION_ERROR, tested.error, name))
            elif tested.refusal is not None and tested.refusal.structural:
                issues.append(Issue(
                    IssueCode.PROBE_REFUSED_BY_SCHEMA,
                    f"the request schema refuses the probe: {tested.refusal.describe()}",
                    name,
                ))
            elif verdict.cells[Stage.MAPPING] is CellState.FAILED:
                issues.append(Issue(IssueCode.STATUS_BELOW_ANNOUNCED, verdict.remarks[Stage.MAPPING], name))
            if verdict.base is not None and verdict.base_is_broken:
                issues.append(Issue(
                    IssueCode.BASE_NOT_TRANSLATED,
                    f"its base {verdict.probe.base_name} did not translate: {verdict.base.outcome()}",
                    name,
                ))
        return tuple(issues)

    def findings(self) -> Tuple[Issue, ...]:
        """Return every finding, per probe in probe order: a used leaf that left the energy system unchanged."""
        issues: List[Issue] = []
        for verdict in self.verdicts:
            name = verdict.probe.name
            if verdict.cells[Stage.SYSTEM] is CellState.NO_EFFECT:
                issues.append(Issue(IssueCode.NO_EFFECT, verdict.remarks[Stage.SYSTEM], name))
        return tuple(issues)

    def tally(self) -> Dict[str, Dict[str, int]]:
        """Return how many cells of each column carry each state."""
        counts = {stage.value: {state.value: 0 for state in CellState} for stage in Stage}
        for verdict in self.verdicts:
            for stage, state in verdict.cells.items():
                counts[stage.value][state.value] += 1
        return counts

    def to_json(self) -> Dict[str, Any]:
        """Return ``report.json``: the matrix, the probes and the verdicts, with no clock reading in it."""
        failures, findings = self.failures(), self.findings()
        return {
            "tier": self.TIER,
            "stages": [1, 2, 3],
            "translator": {"version": TRANSLATOR_VERSION, "hisim_commit": HiSimCommit.or_unknown()},
            "legend": {state.value: {"glyph": state.glyph, "meaning": state.legend} for state in CellState},
            "summary": {
                "probes": len(self.verdicts),
                "translations": self.translations,
                "lookups": self.lookups,
                "completeness_checked": self.completeness_checked,
                "failures": len(failures),
                "findings": len(findings),
                "cells": self.tally(),
            },
            "failures": [issue.to_json() for issue in failures],
            "findings": [issue.to_json() for issue in findings],
            "probes": [verdict.to_json() for verdict in self.verdicts],
        }


class VerificationRunner:
    """Runs tier 1 over the probe set and returns the report.

    Args:
        base_files_directory: Where the recorded twins live.
        whitelist: The list the translations run against.

    Example::

        report = VerificationRunner().run()
        ReportWriter.write(report, Path("path-report"))
    """

    def __init__(self, base_files_directory: Optional[Path] = None, whitelist: Optional[Whitelist] = None) -> None:
        """Store the two inputs; nothing runs until :meth:`run`."""
        self._directory = base_files_directory
        self._whitelist = whitelist

    def run(self, probes: Optional[Sequence[Probe]] = None, completeness: Optional[bool] = None) -> VerificationReport:
        """Translate every probe and its base, and compare them.

        Args:
            probes: The probes to verify; the whole capability set when omitted.
            completeness: Whether to check that every settable thing has a probe; by default only
                when the whole set runs, because a subset is incomplete on purpose.

        Returns:
            The report.
        """
        verification_probes = ProbeBases.build(probes)
        cache = ArtefactCache(self._directory, self._whitelist)
        results: List[ProbeResult] = []
        for probe in verification_probes:
            artefacts = cache.get(probe.document, probe.request_hash)
            if artefacts.refusal is not None:
                results.append(ProbeResult(probe=probe.probe, refused=artefacts.refusal.codes))
            elif artefacts.report is not None:
                results.append(ProbeResult.of_report(probe.probe, artefacts.report, artefacts.hits))
        announcements = Announcements(results)
        verdicts = tuple(self._verdict(probe, cache, announcements) for probe in verification_probes)
        check = probes is None if completeness is None else completeness
        return VerificationReport(
            verdicts=verdicts,
            missing=Completeness.missing(verification_probes) if check else (),
            completeness_checked=check,
            translations=cache.translations,
            lookups=cache.lookups,
        )

    @classmethod
    def _verdict(cls, probe: VerificationProbe, cache: ArtefactCache, announcements: Announcements) -> ProbeVerdict:
        """Compare one probe with its base, stage by stage."""
        tested = cache.get(probe.document, probe.request_hash)
        base = cache.get(probe.base_document, probe.base_request_hash) if probe.base_document is not None else None
        stage_one = probe.stage_one
        stray = probe.stray_changes(stage_one)
        cells: Dict[Stage, CellState] = {}
        remarks: Dict[Stage, str] = {}
        cells[Stage.REQUEST], remarks[Stage.REQUEST] = cls._request_cell(probe, stage_one, stray)
        stage_two: Tuple[StatusLine, ...] = ()
        if tested.report is not None:
            stage_two = StatusLines.of(stage_one, tested.report, probe.document, announcements)
        cells[Stage.MAPPING], remarks[Stage.MAPPING] = cls._mapping_cell(tested, stage_two)
        stage_three: Tuple[Change, ...] = ()
        if base is not None and base.system is not None and tested.system is not None:
            stage_three = base.system.diff(tested.system, tested.sources)
        in_base: Dict[str, Optional[str]] = {}
        if base is not None and base.report is not None and probe.base_document is not None:
            in_base = {
                line.line: line.status
                for line in StatusLines.of(stage_one, base.report, probe.base_document, announcements)
            }
        cells[Stage.SYSTEM], remarks[Stage.SYSTEM] = cls._system_cell(
            tested, base, stage_two, stage_three, (probe.base_name or "", in_base)
        )
        if base is not None and not stage_one:
            # A request identical to its base has nothing to say in the later stages; its stage-1
            # failure is the verdict.
            for stage in (Stage.MAPPING, Stage.SYSTEM):
                cells[stage], remarks[stage] = CellState.NOT_RUN, "the request does not differ from its base"
        return ProbeVerdict(
            probe=probe,
            tested=tested,
            base=base,
            stage_one=stage_one,
            stray=stray,
            stage_two=stage_two,
            stage_three=stage_three,
            cells=cells,
            remarks=remarks,
        )

    @staticmethod
    def _request_cell(
        probe: VerificationProbe, stage_one: Sequence[Change], stray: Sequence[str]
    ) -> Tuple[CellState, str]:
        """Stage 1: exactly the probe's change, and for the anchor an empty package."""
        if probe.base_document is None:
            if probe.document.get(RequestLeaves.MEASURES):
                return CellState.FAILED, "the anchor carries measures; it must be the mockup with an empty package"
            return CellState.AS_EXPECTED, "the anchor: the vendored mockup with an empty measure list"
        if stray:
            return CellState.FAILED, (
                f"the request changes {', '.join(stray)} beside the declared {', '.join(probe.changes)}"
            )
        if not stage_one:
            return CellState.FAILED, (
                f"the request is identical to its base {probe.base_name}: {probe.base_reason}"
            )
        return CellState.AS_EXPECTED, f"{len(stage_one)} leaf/leaves changed, all under {', '.join(probe.changes)}"

    @staticmethod
    def _unfinished(tested: Artefacts, stage: Stage) -> Optional[Tuple[CellState, str]]:
        """Return the state of a column whose probe was refused or raised, or ``None`` when it translated.

        A refusal by the request schema is a broken probe, ✖ in both columns. A refusal by a
        semantic check is intended: the ``map`` column is ◐ with the problem code, and the ``sys``
        column, which has no system to show, is not run.
        """
        refusal = tested.refusal
        if refusal is not None and refusal.structural:
            return CellState.FAILED, f"refused by the request schema: {refusal.describe()}"
        if refusal is not None:
            if stage is Stage.MAPPING:
                return CellState.AS_LISTED, f"refused by a semantic check, as intended: {', '.join(refusal.codes)}"
            return CellState.NOT_RUN, f"nothing translated: refused by a semantic check ({', '.join(refusal.codes)})"
        if tested.error is not None:
            return CellState.FAILED, f"the translation raised {tested.error}"
        return None

    @classmethod
    def _mapping_cell(cls, tested: Artefacts, lines: Sequence[StatusLine]) -> Tuple[CellState, str]:
        """Stage 2: the report's status for every changed leaf, against the announcement.

        The announcement is the one the document's conditions give for this request
        (:class:`Announcements`), so a combination is held to its condition like any single change to
        the unconditional status: a line below it fails the cell. A remark names the conditions that
        applied, so a ◐ or ● that a condition decided says so.
        """
        unfinished = cls._unfinished(tested, Stage.MAPPING)
        if unfinished is not None:
            return unfinished
        problems = [f"the mapping report carries no line for {line.path}" for line in lines if line.status is None]
        problems.extend(
            f"{line.path} is {line.status} although the capability document announces {line.announcement()}"
            for line in lines
            if line.below_announcement
        )
        if problems:
            return CellState.FAILED, "; ".join(problems)
        conditioned = "".join(
            f"; {line.path} announced {line.announcement()}" for line in lines if line.conditions
        )
        statuses = sorted({str(line.status) for line in lines if not line.removed})
        if not lines:
            return CellState.AS_EXPECTED, "the anchor's report accounts for every leaf"
        if statuses and statuses != [ReportStatus.USED.value]:
            return CellState.AS_LISTED, f"status {', '.join(statuses)}{conditioned}"
        return CellState.AS_EXPECTED, ("used" if statuses else "removed leaves, defaulted as documented") + conditioned

    @classmethod
    def _system_cell(
        cls,
        tested: Artefacts,
        base: Optional[Artefacts],
        lines: Sequence[StatusLine],
        changes: Sequence[Change],
        in_base: Tuple[str, Mapping[str, Optional[str]]],
    ) -> Tuple[CellState, str]:
        """Stage 3: shown, not judged -- except an empty diff behind a used leaf.

        The remark of a "no effect" names the base and the status the same report line has there,
        because that is the first thing a person triaging it asks: a used value measured against a
        sibling that is itself only approximated, or against a default equal to it, explains an
        empty diff; two used values that translate to the same file do not.
        """
        if base is None:
            return CellState.NOT_RUN, "the anchor has no base to compare with"
        unfinished = cls._unfinished(tested, Stage.SYSTEM)
        if unfinished is not None:
            return unfinished
        if not base.finished:
            return CellState.FAILED, f"the base {in_base[0]} did not translate: {base.outcome()}"
        if changes:
            return CellState.AS_EXPECTED, f"{len(changes)} field(s) differ (shown, not judged)"
        used = [line for line in lines if line.status == ReportStatus.USED.value and not line.removed]
        if used:
            base_name, statuses = in_base
            there = ", ".join(f"{line.line}: {statuses.get(line.line) or 'no line'}" for line in used)
            return CellState.NO_EFFECT, (
                f"{', '.join(line.path for line in used)} is reported used, but the energy system and the "
                f"economic context are identical to those of the base {base_name} ({there} there)"
            )
        return CellState.AS_LISTED, "no field differs, and no changed leaf is reported used"
