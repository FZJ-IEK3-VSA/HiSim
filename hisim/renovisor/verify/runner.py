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
``failures``: a settable thing no probe changes, a stage-1 diff that is not the probe's change, a
changed leaf whose status is ``defaulted`` or ``not_implemented_yet`` although the capability
document announces ``used`` or ``approximated`` for it, and a translation that raised. What does
not fail it is a finding: "no effect", and -- the bridge of :data:`CONDITIONAL_PROBE_KINDS`, until
``hisim-5dfc`` -- a pair probe whose status is below the announced one, because a combination's
status is conditional and the capability document cannot say so yet.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

from hisim.energy_system.emitter import EnergySystemEmitter
from hisim.renovisor import TRANSLATOR_VERSION
from hisim.renovisor.apply import apply
from hisim.renovisor.capabilities import Aggregation, Probe, ProbeKind, ProbeResult, ProbeRunner
from hisim.renovisor.report import HiSimCommit
from hisim.renovisor.request import Request, RequestError
from hisim.renovisor.translate import Translator
from hisim.renovisor.verify.leaves import ABSENT, Change, RequestLeaves, SystemLeaves, edit_sources, request_hash
from hisim.renovisor.verify.probes import Completeness, MissingProbe, ProbeBases, VerificationProbe
from hisim.renovisor.vocabulary import ReportStatus
from hisim.renovisor.whitelist import Whitelist

#: The bead that retires :data:`CONDITIONAL_PROBE_KINDS`: conditional statuses in the capability document.
CONDITIONAL_STATUS_BEAD = "hisim-5dfc"

#: A bridge, by owner decision (Noah, 2026-09-26), until :data:`CONDITIONAL_STATUS_BEAD` lands.
#:
#: A probe of these kinds -- a pair, one of the capability probe set's combinations (spec §7) --
#: changes two things at once, and a leaf's status *in that combination* can be below the one the
#: capability document announces for the leaf: solar thermal's ``supplies`` beside an oil boiler,
#: a seasonal efficiency beside a heat pump. The document has no way yet to state a status that
#: holds only under a condition, so for these probes a status below the announced one is a finding
#: (``conditional_status``) and its ``map`` cell is ◐, not ✖. Every other probe keeps the failure
#: rule. When hisim-5dfc lands, this becomes ``()`` -- the one edit that retires the bridge, since
#: :meth:`VerificationRunner._conditional`, the finding and the legend's remark all read it.
CONDITIONAL_PROBE_KINDS: Tuple[ProbeKind, ...] = (ProbeKind.PAIR,)


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
            CellState.AS_LISTED: "approximated / not_implemented_yet / defaulted, as listed" + (
                f"; or a combination's conditional status below the announced one ({CONDITIONAL_STATUS_BEAD})"
                if CONDITIONAL_PROBE_KINDS else ""
            ),
            CellState.NO_EFFECT: "no effect",
            CellState.FAILED: "failed",
            CellState.NOT_RUN: "not run",
        }[self]


class Stage(str, Enum):
    """The three columns of tier 1."""

    REQUEST = "req"
    MAPPING = "map"
    SYSTEM = "sys"


@dataclass(frozen=True)
class Issue:
    """One failure or finding, named.

    Args:
        code: What kind: ``missing_probe``, ``request_diff``, ``status_below_announced``,
            ``translation_error`` (failures), ``no_effect`` or ``conditional_status`` (findings).
        message: One sentence a person reads.
        probe: The probe it is about, when it is about one.
    """

    code: str
    message: str
    probe: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        """Return the issue as ``report.json`` carries it."""
        row: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.probe is not None:
            row["probe"] = self.probe
        return row


@dataclass(frozen=True)
class Artefacts:
    """What the translation layer produced for one request.

    Args:
        request_hash: The cache key.
        refused: The problem codes of a request the validation refused; empty otherwise.
        error: ``Type: message`` of a translation that raised; ``None`` otherwise.
        report: ``mapping_report.json``, when the translation finished.
        system: The translated file and its economic context, flattened.
        sources: Edit location -> the request path that asked for it.
        hits: The whitelist entries the translation matched.
    """

    request_hash: str
    refused: Tuple[str, ...] = ()
    error: Optional[str] = None
    report: Optional[Dict[str, Any]] = None
    system: Optional[SystemLeaves] = None
    sources: Dict[str, str] = field(default_factory=dict)
    hits: Tuple[str, ...] = ()

    @property
    def finished(self) -> bool:
        """Return whether the translation produced a file and a report."""
        return self.report is not None and self.system is not None


class ArtefactCache:
    """Translates requests, one translation per distinct request hash.

    Args:
        base_files_directory: Where the recorded twins live.
        whitelist: The list the translations run against; one instance, its hit record cleared
            before every translation.
    """

    def __init__(self, base_files_directory: Optional[Path] = None, whitelist: Optional[Whitelist] = None) -> None:
        """Create an empty cache; nothing is translated until :meth:`get`."""
        self._whitelist = whitelist if whitelist is not None else Whitelist.load()
        self._translator = Translator(base_files_directory or ProbeRunner.DEFAULT_BASE_FILES, self._whitelist)
        self._artefacts: Dict[str, Artefacts] = {}
        self.lookups = 0

    @property
    def translations(self) -> int:
        """Return how many distinct requests were translated."""
        return len(self._artefacts)

    def get(self, document: Mapping[str, Any]) -> Artefacts:
        """Return the artefacts of one request, translating it the first time it is asked for."""
        self.lookups += 1
        key = request_hash(document)
        if key not in self._artefacts:
            self._artefacts[key] = self._translate(key, document)
        return self._artefacts[key]

    def _translate(self, key: str, document: Mapping[str, Any]) -> Artefacts:
        """Run ``validate`` + ``apply`` + ``translate`` on one request."""
        self._whitelist.forget_hits()
        try:
            request = Request.parse(document)
        except RequestError as error:
            return Artefacts(request_hash=key, refused=tuple(problem.code.value for problem in error.problems))
        try:
            applied = apply(request.document["house"], request.measures, self._whitelist)
            translated = self._translator.translate(request, applied)
            rendered = EnergySystemEmitter.to_document(translated.model)
        except Exception as error:  # pylint: disable=broad-except  # every failure is reported by name
            return Artefacts(request_hash=key, error=f"{type(error).__name__}: {error}")
        return Artefacts(
            request_hash=key,
            report=translated.report.to_json(),
            system=SystemLeaves.of(rendered, translated.economic_context, translated.base_file_name),
            sources=edit_sources(translated.edits),
            hits=self._whitelist.hits(),
        )


class Announcements:
    """What the capability document announces, per measure, option, value and inventory field.

    Built with :class:`~hisim.renovisor.capabilities.Aggregation` from the same probe run, so it is
    the document ``python -m hisim.renovisor capabilities`` writes for this build. The measure-level
    word ``supported`` is read back as the report's ``used``.
    """

    def __init__(self, results: Sequence[ProbeResult]) -> None:
        """Aggregate the probe results the way the capability document does."""
        self._measures: Dict[str, str] = {}
        self._options: Dict[Tuple[str, str], Tuple[str, Dict[str, str]]] = {}
        for entry in Aggregation.measures(results):
            status = str(entry["status"])
            self._measures[entry["measure_id"]] = ReportStatus.USED.value if status == "supported" else status
            for option in entry["options"]:
                values = {_value_key(value["value"]): str(value["status"]) for value in option.get("values", [])}
                self._options[(entry["measure_id"], option["name"])] = (str(option["status"]), values)
        self._fields: Dict[str, Tuple[str, Dict[str, str]]] = {}
        for entry in Aggregation.fields(results):
            values = {_value_key(value["value"]): str(value["status"]) for value in entry.get("values", [])}
            self._fields[entry["path"]] = (str(entry["status"]), values)

    def measure(self, measure_id: str) -> Optional[str]:
        """Return the status announced for one measure."""
        return self._measures.get(measure_id)

    def option(self, measure_id: str, name: str, value: Any) -> Optional[str]:
        """Return the status announced for one option at one value: the value's own, else the option's."""
        entry = self._options.get((measure_id, name))
        if entry is None:
            return None
        return entry[1].get(_value_key(value), entry[0])

    def field(self, path: str, value: Any) -> Optional[str]:
        """Return the status announced for one inventory path at one value: the value's own, else the field's."""
        entry = self._fields.get(path)
        if entry is None:
            return None
        return entry[1].get(_value_key(value), entry[0])


def _value_key(value: Any) -> str:
    """Return a hashable key for one probed value; a material object is keyed by its JSON."""
    return json.dumps(value, sort_keys=True)


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
        announced: The status the capability document announces for the leaf at this value.
        removed: Whether the probe removes the leaf rather than setting it; a removed leaf is
            expected to be ``defaulted`` and is not held to the announcement.
    """

    path: str
    line: str
    status: Optional[str]
    target: Optional[str] = None
    value: Any = None
    note: Optional[str] = None
    announced: Optional[str] = None
    removed: bool = False

    #: The statuses that fail a changed leaf the document announces better for.
    SILENT: ClassVar[Tuple[str, ...]] = (ReportStatus.DEFAULTED.value, ReportStatus.NOT_IMPLEMENTED_YET.value)

    #: The announcements a silent status contradicts.
    PROMISED: ClassVar[Tuple[str, ...]] = (ReportStatus.USED.value, ReportStatus.APPROXIMATED.value)

    @property
    def below_announcement(self) -> bool:
        """Return whether the report is silent about a leaf the document promises to act on."""
        return not self.removed and self.status in self.SILENT and self.announced in self.PROMISED

    def conditional_message(self) -> str:
        """Return what the finding of a combination's status below the announced one says."""
        return (
            f"{self.path} is {self.status} in this combination although the capability document announces "
            f"{self.announced} for the leaf: a combination whose status is conditional, which the document "
            f"cannot state until {CONDITIONAL_STATUS_BEAD}"
        )

    def to_json(self) -> Dict[str, Any]:
        """Return the line as ``report.json`` carries it."""
        row: Dict[str, Any] = {"path": self.path, "line": self.line, "status": self.status}
        for key in ("target", "value", "note", "announced"):
            if getattr(self, key) is not None:
                row[key] = getattr(self, key)
        if self.removed:
            row["removed"] = True
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
            option's line.
        """
        fields = {row["path"]: row for row in report.get("fields", [])}
        measures = {row["id"]: row for row in report.get("measures", [])}
        positions = {
            str(entry.get("id")): index for index, entry in enumerate(document.get("measures") or [])
        }
        lines: Dict[str, StatusLine] = {}
        for change in changes:
            line = cls._line(change, fields, measures, positions, announcements)
            lines.setdefault(line.line, line)
        return tuple(lines.values())

    @classmethod
    def _line(
        cls,
        change: Change,
        fields: Mapping[str, Mapping[str, Any]],
        measures: Mapping[str, Mapping[str, Any]],
        positions: Mapping[str, int],
        announcements: Announcements,
    ) -> StatusLine:
        """Return the report line one changed leaf reaches."""
        removed = change.after is ABSENT
        path = change.path
        if not path.startswith(f"{RequestLeaves.MEASURES}[id="):
            row = cls._nearest(path, fields)
            if row is None:
                return StatusLine(path=path, line=path, status=None, removed=removed)
            return StatusLine(
                path=path,
                line=str(row["path"]),
                status=str(row["status"]),
                target=row.get("target"),
                value=row.get("value"),
                note=row.get("note"),
                announced=None if removed else announcements.field(path, change.after),
                removed=removed,
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
            return StatusLine(
                path=option_path,
                line=option_path,
                status=None if option is None else str(option["status"]),
                note=None if option is None else option.get("note"),
                announced=None if removed else announcements.option(measure_id, name, cls._option_value(change)),
                removed=removed,
            )
        return StatusLine(
            path=prefix,
            line=prefix,
            status=None if measure is None else str(measure["status"]),
            target=(", ".join(measure.get("targets", [])) or None) if measure is not None else None,
            note=None if measure is None else measure.get("note"),
            announced=None if removed else announcements.measure(measure_id),
            removed=removed,
        )

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
        conditional: The stage-2 lines below the announced status that are a finding rather than a
            failure, because the probe is a combination (:data:`CONDITIONAL_PROBE_KINDS`).
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
    conditional: Tuple[StatusLine, ...] = ()

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
        if self.tested.refused:
            row["refused"] = list(self.tested.refused)
        if self.tested.error:
            row["error"] = self.tested.error
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
    """

    verdicts: Tuple[ProbeVerdict, ...]
    missing: Tuple[MissingProbe, ...]
    completeness_checked: bool
    translations: int
    lookups: int

    #: The tier this report is.
    TIER: ClassVar[int] = 1

    def failures(self) -> Tuple[Issue, ...]:
        """Return every failure: completeness first, then per probe in probe order."""
        issues: List[Issue] = [Issue("missing_probe", gap.message()) for gap in self.missing]
        for verdict in self.verdicts:
            name = verdict.probe.name
            if verdict.cells[Stage.REQUEST] is CellState.FAILED:
                issues.append(Issue("request_diff", verdict.remarks[Stage.REQUEST], name))
            if verdict.tested.error:
                issues.append(Issue("translation_error", verdict.tested.error, name))
            elif verdict.cells[Stage.MAPPING] is CellState.FAILED:
                issues.append(Issue("status_below_announced", verdict.remarks[Stage.MAPPING], name))
        return tuple(issues)

    def findings(self) -> Tuple[Issue, ...]:
        """Return every finding, per probe in probe order.

        Two kinds: a combination whose status is below the announced one
        (:data:`CONDITIONAL_PROBE_KINDS`), and a used leaf that left the energy system unchanged.
        """
        issues: List[Issue] = []
        for verdict in self.verdicts:
            name = verdict.probe.name
            if verdict.conditional:
                message = "; ".join(line.conditional_message() for line in verdict.conditional)
                issues.append(Issue("conditional_status", message, name))
            if verdict.cells[Stage.SYSTEM] is CellState.NO_EFFECT:
                issues.append(Issue("no_effect", verdict.remarks[Stage.SYSTEM], name))
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
            artefacts = cache.get(probe.document)
            if artefacts.refused:
                results.append(ProbeResult(probe=probe.probe, refused=artefacts.refused))
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
        tested = cache.get(probe.document)
        base = cache.get(probe.base_document) if probe.base_document is not None else None
        stage_one = probe.stage_one()
        stray = probe.stray_changes(stage_one)
        cells: Dict[Stage, CellState] = {}
        remarks: Dict[Stage, str] = {}
        cells[Stage.REQUEST], remarks[Stage.REQUEST] = cls._request_cell(probe, stage_one, stray)
        stage_two: Tuple[StatusLine, ...] = ()
        if tested.report is not None:
            stage_two = StatusLines.of(stage_one, tested.report, probe.document, announcements)
        conditional = cls._conditional(probe, stage_two)
        cells[Stage.MAPPING], remarks[Stage.MAPPING] = cls._mapping_cell(tested, stage_two, conditional)
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
            conditional = ()
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
            conditional=conditional,
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
    def _unfinished(tested: Artefacts) -> Optional[Tuple[CellState, str]]:
        """Return the state of a column whose probe was refused or raised, or ``None`` when it translated."""
        if tested.refused:
            return CellState.NOT_RUN, f"refused by validation: {', '.join(tested.refused)}"
        if tested.error:
            return CellState.FAILED, f"the translation raised {tested.error}"
        return None

    @staticmethod
    def _conditional(probe: VerificationProbe, lines: Sequence[StatusLine]) -> Tuple[StatusLine, ...]:
        """Return the lines below the announced status that the bridge of :data:`CONDITIONAL_PROBE_KINDS` excuses.

        A probe of one of those kinds is a combination, whose status the capability document cannot
        yet announce as conditional (``hisim-5dfc``); for any other probe nothing is excused.
        """
        if probe.probe.kind not in CONDITIONAL_PROBE_KINDS:
            return ()
        return tuple(line for line in lines if line.below_announcement)

    @classmethod
    def _mapping_cell(
        cls, tested: Artefacts, lines: Sequence[StatusLine], conditional: Sequence[StatusLine] = ()
    ) -> Tuple[CellState, str]:
        """Stage 2: the report's status for every changed leaf, against the announcement.

        A line below the announcement fails the cell, unless it is one of *conditional*: then the
        cell is ◐ and its remark says why (:data:`CONDITIONAL_PROBE_KINDS`).
        """
        unfinished = cls._unfinished(tested)
        if unfinished is not None:
            return unfinished
        problems = [f"the mapping report carries no line for {line.path}" for line in lines if line.status is None]
        problems.extend(
            f"{line.path} is {line.status} although the capability document announces {line.announced}"
            for line in lines
            if line.below_announcement and line not in conditional
        )
        excused = [line.conditional_message() for line in conditional]
        if problems:
            return CellState.FAILED, "; ".join(problems + excused)
        if excused:
            return CellState.AS_LISTED, "; ".join(excused)
        statuses = sorted({str(line.status) for line in lines if not line.removed})
        if not lines:
            return CellState.AS_EXPECTED, "the anchor's report accounts for every leaf"
        if statuses and statuses != [ReportStatus.USED.value]:
            return CellState.AS_LISTED, f"status {', '.join(statuses)}"
        return CellState.AS_EXPECTED, "used" if statuses else "removed leaves, defaulted as documented"

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
        unfinished = cls._unfinished(tested)
        if unfinished is not None:
            return unfinished
        if not base.finished:
            return CellState.NOT_RUN, f"the base did not translate ({base.error or ', '.join(base.refused)})"
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
