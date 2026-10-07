"""Builds `economics_result.json`: one staged plan in the shape the RenoVisor frontend draws its charts from.

The document is built from a `StagedResult` alone and validated against the shipped `economics_result.schema.json`
before it is written. Every amount is a band `{"min", "best", "max"}` (the engine's LOW, BEST_ESTIMATE and HIGH slots);
cost is positive and money arriving is negative (cost_spec.md §3.6). Years are relative (`0..T`), with `calendar_year`
on every row, `null` when the plan names no start year. Subjects, asset classes, measures, schemes and perspectives are
written as ids; the frontend labels them.

Example::

    document = StagedDocument(result, parameters, perspective, measure_ids={"HeatPump": "heating_system"})
    document.write(Path("economics_result.json"))
"""

from __future__ import annotations

import enum
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from hisim.economics.calculators.financing_application import FinancingConstants
from hisim.economics.carriers import bill_subjects
from hisim.economics.facts import ComponentCostFacts
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective
from hisim.economics.results import LifecycleCostResult, VariantComparison
from hisim.economics.staged import IncrementSubjects, InvestmentOverride, StagedEvaluator, StagedResult
from hisim.economics.staged_parameters import StagedParameters, StatedQuote
from hisim.economics.subsidies import PayoutKind, SchemeMaximum, SchemeMaximumNotes, SubsidyDecision
from hisim.economics.timeline import CashFlowEntry, CategoryRules, CostCategory
from hisim.economics.uncertainty import UncertainValue
from hisim.hisim_commit import HiSimCommit
from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource


#: How an awarded amount is found on a timeline: ``(scheme id, subject, stage)``, where the stage
#: is the one whose evaluation booked it and ``None`` on the reference.
AwardKey = Tuple[str, str, Optional[int]]


class SchemaValidationUnavailableError(RuntimeError):
    """Raised when `jsonschema` is not installed, so a document cannot be validated before it is written.

    The frontend cannot check the document itself, so writing it unvalidated is refused.
    """


class BandOrderError(ValueError):
    """Raised when a band in the document is not `min <= best <= max`.

    The schema cannot express the order of a band's three numbers, so `StagedDocument.assert_bands_ordered` checks it
    before writing. It always indicates a bug in this module, since an `UncertainValue` is ordered by construction.
    """


class SubsidyReconciliationError(ValueError):
    """Raised when an evaluation books a subsidy that its own `subsidies[]` rows do not award.

    The document states support twice, as the `Subsidies` stack of each year and as the awarded `subsidies[]` rows, and
    the frontend draws both. `StagedDocument.assert_subsidies_reconciled` raises this before writing; it always
    indicates a bug in the engine or this module, never in the input.
    """


class MeasureWithoutRowError(ValueError):
    """Raised when a measure a stage carries out has no `by_subject` row in the document.

    `by_subject` is what the frontend draws the cost of the work from, so a missing row hides a bought measure. Every
    measure the translator acts on creates a cost subject or is declared costless or unpriced in its mapping report;
    one that is neither is a translator or engine bug, or a stale mapping report that re-translating the stage fixes.
    """


class CostGroup(str, enum.Enum):
    """The eight cost groups every stacked chart draws, in drawing order.

    The order is fixed and published under the document's `groups` key, so the baseline and the plan show the same
    segments in the same sequence. The names are ids the frontend translates.
    """

    INVESTMENT_AND_FINANCING = "Investment & financing"
    RESIDUAL_VALUE_AND_ANYWAY_CREDIT = "Residual value & anyway credit"
    SUBSIDIES = "Subsidies"
    REPLACEMENTS = "Replacements"
    ENERGY = "Energy"
    CO2 = "CO2"
    MAINTENANCE_AND_OPERATION = "Maintenance & operation"
    LEVY_AND_TRANSFERS = "Levy & transfers"


class CostGroups:
    """The table mapping every engine cost category onto one of the document's eight groups.

    Each `CostCategory` appears exactly once, so the groups sum to the total by construction; `assert_total` fails at
    import if a category is missing.

    Example::

        CostGroups.of(CostCategory.FEED_IN_REVENUE) is CostGroup.ENERGY
    """

    #: Category -> the group its money is shown under. Exhaustive over ``CostCategory``.
    BY_CATEGORY: ClassVar[Dict[CostCategory, CostGroup]] = {
        CostCategory.INVESTMENT: CostGroup.INVESTMENT_AND_FINANCING,
        CostCategory.PLANNING: CostGroup.INVESTMENT_AND_FINANCING,
        CostCategory.REMOVAL: CostGroup.INVESTMENT_AND_FINANCING,
        CostCategory.LOAN_INTEREST: CostGroup.INVESTMENT_AND_FINANCING,
        CostCategory.LOAN_PRINCIPAL: CostGroup.INVESTMENT_AND_FINANCING,
        CostCategory.LOAN_DISBURSEMENT: CostGroup.INVESTMENT_AND_FINANCING,
        CostCategory.RESIDUAL_VALUE: CostGroup.RESIDUAL_VALUE_AND_ANYWAY_CREDIT,
        CostCategory.ANYWAY_COST_CREDIT: CostGroup.RESIDUAL_VALUE_AND_ANYWAY_CREDIT,
        CostCategory.SUBSIDY: CostGroup.SUBSIDIES,
        CostCategory.REPLACEMENT: CostGroup.REPLACEMENTS,
        CostCategory.REPLACEMENT_RESERVE: CostGroup.REPLACEMENTS,
        CostCategory.ENERGY_WORKING: CostGroup.ENERGY,
        CostCategory.ENERGY_STANDING: CostGroup.ENERGY,
        CostCategory.ENERGY_CAPACITY_CHARGE: CostGroup.ENERGY,
        CostCategory.FEED_IN_REVENUE: CostGroup.ENERGY,
        CostCategory.ENERGY_CO2_PRICE: CostGroup.CO2,
        CostCategory.CO2_DAMAGE: CostGroup.CO2,
        CostCategory.MAINTENANCE: CostGroup.MAINTENANCE_AND_OPERATION,
        CostCategory.FIXED_OPERATION: CostGroup.MAINTENANCE_AND_OPERATION,
        CostCategory.MODERNIZATION_LEVY: CostGroup.LEVY_AND_TRANSFERS,
    }

    @classmethod
    def of(cls, category: CostCategory) -> CostGroup:
        """Return the group one cost category is shown under.

        Args:
            category: An engine cost category.

        Returns:
            Its group.

        Raises:
            KeyError: If the category is not in the table.
        """
        return cls.BY_CATEGORY[category]

    @classmethod
    def assert_total(cls) -> None:
        """Raise unless every cost category has a group; called once at import.

        Raises:
            ValueError: Naming the categories with no group.
        """
        missing = sorted(category.value for category in CostCategory if category not in cls.BY_CATEGORY)
        if missing:
            raise ValueError(
                f"cost categories {missing} have no document group; every category must be in "
                "exactly one of the eight stacks or the groups no longer sum to the total."
            )


CostGroups.assert_total()


class SubsidyStatus(str, enum.Enum):
    """What the document says about one subsidy scheme: `AWARDED`, `INELIGIBLE` or `UNDETERMINED`.

    `INELIGIBLE` means refused on an answered condition; `UNDETERMINED` means an unanswered applicant question decides
    it. An undetermined row carries no amount at all, so the question is shown as a question rather than as a zero.
    """

    AWARDED = "awarded"
    INELIGIBLE = "ineligible"
    UNDETERMINED = "undetermined"


class EventKind(str, enum.Enum):
    """The markers a year of the annual series can carry.

    The set is closed because the frontend draws one glyph per kind. `REPLACEMENT` marks the years a subject is
    replaced, so the timeline chart need not derive them from `by_subject`.
    """

    INVESTMENT = "investment"
    SUBSIDY = "subsidy"
    STAGE_START = "stage_start"
    REPLACEMENT = "replacement"


class SubjectKindNames:
    """How `by_subject[].kind` spells the two kinds of cost subject: `component` and `carrier`, in lower case."""

    #: A device or an envelope measure — something that was bought — and also the zero row of a
    #: measure the engine prices no subject for (a setting, a measure HiSim holds no price for),
    #: which is carried out rather than billed.
    COMPONENT: ClassVar[str] = "component"

    #: An energy carrier — something that was billed.
    CARRIER: ClassVar[str] = "carrier"

    @classmethod
    def of(cls, subject_kind: Any) -> str:
        """Return the document's spelling of one engine `SubjectKind`."""
        return str(getattr(subject_kind, "value", subject_kind)).lower()


class StagedDocument:
    """Builds and writes `economics_result.json` for one priced plan.

    It holds the priced plan plus what the plan does not know about itself: which catalogue measure created which cost
    subject, which subjects have no price, and where the provenance file is. Every figure is a filter, pivot or sum of
    the two `LifecycleCostResult` objects (reference and plan) on the `StagedResult`, so the document cannot disagree
    with the engine's own report. A cost subject is one costed thing on the timeline, such as a component, an envelope
    element or an energy carrier.

    Args:
        result: The priced plan.
        parameters: The assumptions it was priced under, written into `parameters`.
        perspective: The accounting frame, for its id and financing plan.
        measure_ids: Cost subject -> the catalogue measure that created it, from the translator's
            `mapping_report.json`; a subject absent from it belongs to the baseline and gets `null`. None (a document
            built without the map) stamps no measure on any row and skips `assert_every_measure_has_row`.
        unpriced_subjects: Subjects in the plan with no price (an envelope measure without a `cost` block, or a measure
            HiSim holds no price for); flagged, not hidden.
        cost_provenance: Name of the provenance file beside the document.
        costless_subjects: Subjects of a measure that costs nothing (a setting, not a purchase), and subjects bought as
            part of another whose facts were zeroed (the battery's energy-management controller); each gets a zero row
            of its own where it booked no flow.
        subject_notes: Subject -> the sentence its row's `note` carries (why it has no price, or costs nothing).
        replaces_subjects: Measure subject -> the reference subjects it replaces, from the mapping report; rows without
            a measure state an empty list.
    """

    #: Version of this document format, bumped whenever a consumer would have to change.
    SCHEMA_VERSION: ClassVar[int] = 8

    #: The one currency the engine prices in.
    CURRENCY: ClassVar[str] = "EUR"

    #: What the document is called when the CLI is not told otherwise.
    FILE_NAME: ClassVar[str] = "economics_result.json"

    #: The JSON Schema this module validates every written document against.
    SCHEMA_FILE_NAME: ClassVar[str] = "economics_result.schema.json"

    #: Categories whose year-0 entries make up the headline "what does it cost to build" figure.
    INVESTMENT_TOTAL_CATEGORIES: ClassVar[FrozenSet[CostCategory]] = frozenset(
        {CostCategory.INVESTMENT, CostCategory.PLANNING, CostCategory.REMOVAL}
    )

    #: Which revision of the engine's own specification produced the numbers, echoed into
    #: ``engine.economics_version`` so a stored document names the rules behind it. Source:
    #: ``roadmap/cost-spec-v2.md``, the specification this package implements; it is a document
    #: name and not an estimate of anything.
    ECONOMICS_VERSION: ClassVar[str] = "cost-spec-v2"

    #: The note an undetermined subsidy row carries when the country has no catalogue at all.
    NO_CATALOGUE_NOTE: ClassVar[str] = (
        "no subsidy catalogue for {country}; no maximum: there is no scheme to state one"
    )

    #: The note of a subsidy row whose decision carries no maximum for its scheme (a stored decision
    #: from an older version); the solver states one for every scheme it assesses.
    NO_MAXIMUM_NOTE: ClassVar[str] = "no maximum: the decision states none for this scheme"

    #: The note of a row the reader's quote prices, where HiSim holds no price of its own for the
    #: subject: it replaces the note that said the row was unpriced.
    QUOTED_UNPRICED_NOTE: ClassVar[str] = (
        "priced by the reader's quote; HiSim holds no price of its own for it"
    )

    #: The note of a quoted measure HiSim holds neither a price nor an asset class for.
    QUOTED_PURCHASE_NOTE: ClassVar[str] = (
        "priced by the reader's quote and bought once: HiSim holds no lifetime for it, so it is "
        "never replaced, maintained or written down"
    )

    #: The note of a further subject of a quoted measure, bought within the quote.
    INCLUDED_NOTE: ClassVar[str] = (
        "bought within the reader's quote for {measure_id} in stage {stage}: its investment there is "
        "zero, its lifetime and its later replacements are the cost database's"
    )

    #: The question such a row puts to whoever can answer it.
    NO_CATALOGUE_QUESTION: ClassVar[str] = (
        "is there a support scheme for this measure in {country}? The engine ran with "
        "subsidy_mode NONE, so nothing was awarded and nothing was refused."
    )

    def __init__(
        self,
        result: StagedResult,
        parameters: EconomicParameters,
        perspective: Perspective,
        measure_ids: Optional[Mapping[str, Optional[str]]] = None,
        unpriced_subjects: Iterable[str] = (),
        cost_provenance: str = "cost_provenance.json",
        costless_subjects: Iterable[str] = (),
        subject_notes: Optional[Mapping[str, str]] = None,
        replaces_subjects: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> None:
        """Store the plan and its context; nothing is built until `to_json`.

        The subsidy catalogue is read off the result (`StagedResult.subsidy_catalog_id`), not taken from the caller, so
        the document cannot name a catalogue the figures were not priced with. A plan without a catalogue was priced
        with subsidy mode NONE, and the parameters and perspective are resolved the same way here.
        """
        subsidy_catalog_id = result.subsidy_catalog_id
        if subsidy_catalog_id is None:
            parameters, perspective = StagedEvaluator.priced_under(parameters, perspective, None)
        self._result = result
        self._parameters = parameters
        self._perspective = perspective
        self._measure_ids: Dict[str, Optional[str]] = dict(measure_ids or {})
        # An increment a later stage bought for a kept subject is that subject's measure too:
        # its row sits beside the subject it enlarges, under the same measure.
        for stage in result.stages:
            for subject_facts in stage.inputs.cost_facts:
                base = IncrementSubjects.base_of(subject_facts.subject)
                if base is not None and base in self._measure_ids:
                    self._measure_ids.setdefault(subject_facts.subject, self._measure_ids[base])
        self._has_measure_map = measure_ids is not None
        self._unpriced: Set[str] = set(unpriced_subjects)
        self._costless: Set[str] = set(costless_subjects)
        self._notes: Dict[str, str] = dict(subject_notes or {})
        self._replaces: Dict[str, List[str]] = {
            subject: sorted(names) for subject, names in (replaces_subjects or {}).items()
        }
        self._catalog_id = subsidy_catalog_id
        self._cost_provenance = cost_provenance
        self._component_sources = self._sources_of_components(result)

    @staticmethod
    def _sources_of_components(result: StagedResult) -> Dict[str, KpiSource]:
        """Return subject -> KPI source of every component any stage simulated.

        A KPI source is the stable address of a HiSim component (import path, instance, assembly member, name). The
        first stage's `display_name` and `label` stand when a later stage renames them.

        Raises:
            ValueError: If a stage's inputs carry no sources (written by an older version), or if two stages give one
                subject sources that differ in an identity field (`KpiSource.IDENTITY_FIELDS`).
        """
        sources: Dict[str, KpiSource] = {}
        for index, stage in enumerate(result.stages):
            stage_sources = stage.inputs.component_sources
            if stage_sources is None:
                raise ValueError(
                    f"Stage {index} was read from an economic_inputs.json written before it recorded "
                    "component_sources, so no by_subject row could say whether its subject is a HiSim "
                    "component. Re-run that stage's simulation."
                )
            for subject, source in stage_sources.items():
                known = sources.setdefault(subject, source)
                if known.identity() != source.identity():
                    raise ValueError(
                        f"The subject '{subject}' is two components across the stages: {known} and {source}."
                    )
        return sources

    def _source_of(self, subject: str) -> Optional[Dict[str, Any]]:
        """Return a row's `source`: the KPI source of the component its subject is, or None.

        A subject is a component when a stage simulated a component of that name; the increment a later stage bought
        for a kept component counts as that component. Envelope elements, measures, carriers and synthetic subjects
        such as financing have none.
        """
        source = self._component_sources.get(subject)
        if source is None:
            base = IncrementSubjects.base_of(subject)
            source = self._component_sources.get(base) if base is not None else None
        return None if source is None else source.to_dict()

    # ------------------------------------------------------------------ the document

    def to_json(self) -> Dict[str, Any]:
        """Return the whole document as JSON-ready data.

        Returns:
            The document with `schema_version`, `engine`, `parameters`, `currency`, `groups`, `stages`, `reference`,
                `plan`, `comparison` and `provenance`, in that order, so two runs of one plan produce the same bytes.
        """
        return {
            "schema_version": self.SCHEMA_VERSION,
            "engine": self._engine(),
            "parameters": self._parameters_block(),
            "currency": self.CURRENCY,
            "groups": [group.value for group in CostGroup],
            "stages": self._stages(),
            "reference": self._evaluation(self._result.reference, staged=False),
            "plan": self._evaluation(self._result.plan, staged=True),
            "comparison": self._comparison(self._result.comparison),
            "provenance": {
                "cost_provenance": self._cost_provenance,
                "explain_command": (
                    "python -m hisim.economics explain <stage directory> --value "
                    f"{self._perspective.id}/total_npv_in_euro"
                ),
            },
        }

    def write(self, path: Path) -> Dict[str, Any]:
        """Validate the document and write it, or write nothing.

        Validation runs before the first byte is written. The file has stable key order and a trailing newline, so two
        runs of one plan compare byte for byte.

        Args:
            path: Where the document goes; parent directories are created.

        Returns:
            The document that was written.

        Raises:
            SchemaValidationUnavailableError: If `jsonschema` cannot be imported.
            jsonschema.ValidationError: If the document does not match the schema (a bug in this module).
            BandOrderError: If a band is not `min <= best <= max`.
            SubsidyReconciliationError: If an evaluation books support its `subsidies[]` rows do not award.
            MeasureWithoutRowError: If a measure a stage carries out has no `by_subject` row.
        """
        document = self.to_json()
        self.validate(document)
        self.assert_bands_ordered(document)
        self.assert_subsidies_reconciled(document)
        if self._has_measure_map:
            self.assert_every_measure_has_row(document)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return document

    @classmethod
    def schema_path(cls) -> Path:
        """Return the path of the shipped JSON Schema, beside this module."""
        return Path(os.path.dirname(os.path.abspath(__file__))) / cls.SCHEMA_FILE_NAME

    @classmethod
    def schema(cls) -> Dict[str, Any]:
        """Return the shipped JSON Schema, parsed."""
        with cls.schema_path().open(encoding="utf-8") as handle:
            loaded: Dict[str, Any] = json.load(handle)
        return loaded

    @classmethod
    def validate(cls, document: Mapping[str, Any]) -> None:
        """Check one document against the shipped schema.

        Args:
            document: The document to check.

        Raises:
            SchemaValidationUnavailableError: If `jsonschema` is not installed.
            jsonschema.ValidationError: Naming the first place the document departs from the schema.
        """
        try:
            import jsonschema  # pylint: disable=import-outside-toplevel  # optional at import time
        except ImportError as error:  # pragma: no cover - the dependency ships with HiSim
            raise SchemaValidationUnavailableError(
                "jsonschema is not importable, so economics_result.json cannot be validated "
                "against economics_result.schema.json and is not written."
            ) from error
        jsonschema.validate(instance=document, schema=cls.schema())

    #: The three keys that make a mapping in the document a band, and their order.
    BAND_KEYS: ClassVar[Tuple[str, str, str]] = ("min", "best", "max")

    #: The bands whose ends may be ``null`` with a stated meaning, by the key they sit under, and
    #: the value a ``null`` end is ordered as. A payback year is ``null`` when that end never pays
    #: back within the horizon, which is later than any year. Every other
    #: band of the schema has numeric ends (or is ``null`` whole), so a ``null`` end anywhere else
    #: is left to the schema, which refuses it.
    NULL_END_MEANS: ClassVar[Dict[str, float]] = {"discounted_payback_year": math.inf}

    @classmethod
    def assert_bands_ordered(cls, document: Any, path: str = "") -> None:
        """Raise unless every band in the document reads `min <= best <= max`.

        A band whose end may be `null` with a meaning (`NULL_END_MEANS`) is checked with that meaning; for example a
        payback year that never comes counts as later than every year.

        Args:
            document: The document, or any part of it while recursing.
            path: The dotted path of `document` inside the whole, for the message.

        Raises:
            BandOrderError: Naming the first out-of-order band and where it is.
        """
        if isinstance(document, Mapping):
            null_end = cls.NULL_END_MEANS.get(path.rsplit(".", 1)[-1])
            if set(document) == set(cls.BAND_KEYS) and all(
                (isinstance(document[key], (int, float)) and not isinstance(document[key], bool))
                or (document[key] is None and null_end is not None)
                for key in cls.BAND_KEYS
            ):
                ends = [document[key] for key in cls.BAND_KEYS]
                low, best, high = (float(end) if end is not None else float(null_end or 0.0) for end in ends)
                if not low <= best <= high:
                    stated = "/".join("null" if document[key] is None else str(document[key]) for key in cls.BAND_KEYS)
                    raise BandOrderError(
                        f"the band at {path or '<document>'} is {stated}, which is not "
                        "min <= best <= max. Every amount in economics_result.json is an ordered "
                        "band; a sign flip applied slot by slot is the usual cause."
                    )
                return
            for key, value in document.items():
                cls.assert_bands_ordered(value, f"{path}.{key}" if path else str(key))
            return
        if isinstance(document, list):
            for index, value in enumerate(document):
                cls.assert_bands_ordered(value, f"{path}[{index}]")

    #: How far the two statements of support may drift apart, in euro, per band slot: the cent the
    #: document's other sum checks are held to, for float sums over up to a horizon of years.
    RECONCILIATION_TOLERANCE_IN_EURO: ClassVar[float] = 0.01

    @classmethod
    def assert_subsidies_reconciled(cls, document: Mapping[str, Any]) -> None:
        """Raise unless each evaluation's `Subsidies` stack matches its awarded `subsidies[]` rows.

        Checked for the reference and the plan: every `subsidy` event names a scheme awarded in `subsidies[]`; every
        awarded row's `amount_by_year_in_euro` sums to its `amount_in_euro`; and every year's `Subsidies` group equals
        the awarded amounts booked in that year, slot by slot. An evaluation with no awarded row therefore books no
        support in any year.

        Args:
            document: The finished document.

        Raises:
            SubsidyReconciliationError: Naming the evaluation, the year if any, and the first disagreement.
        """
        tolerance = cls.RECONCILIATION_TOLERANCE_IN_EURO
        for variant in ("reference", "plan"):
            evaluation = document[variant]
            awarded = [
                row for row in evaluation["subsidies"] if row["status"] == SubsidyStatus.AWARDED.value
            ]
            awarded_schemes = {row["scheme"] for row in awarded}
            stated: Dict[int, Dict[str, float]] = {}
            for row in awarded:
                row_total = dict.fromkeys(cls.BAND_KEYS, 0.0)
                for booking in row["amount_by_year_in_euro"]:
                    in_year = stated.setdefault(booking["year"], dict.fromkeys(cls.BAND_KEYS, 0.0))
                    for key in cls.BAND_KEYS:
                        in_year[key] += booking["amount_in_euro"][key]
                        row_total[key] += booking["amount_in_euro"][key]
                for key in cls.BAND_KEYS:
                    if abs(row_total[key] - row["amount_in_euro"][key]) > tolerance:
                        raise SubsidyReconciliationError(
                            f"{variant}.subsidies row of scheme {row['scheme']!r} (stage {row['stage']}) "
                            f"states {row['amount_in_euro'][key]} in slot {key!r}, but its "
                            f"amount_by_year_in_euro sums to {row_total[key]}."
                        )
            booked: Dict[int, Dict[str, float]] = {}
            for year in evaluation["annual"]:
                for event in year["events"]:
                    if event["kind"] == EventKind.SUBSIDY.value and event["scheme"] not in awarded_schemes:
                        raise SubsidyReconciliationError(
                            f"{variant}.annual[{year['year']}] books support from scheme "
                            f"{event['scheme']!r}, which {variant}.subsidies does not award "
                            f"(awarded: {sorted(map(str, awarded_schemes)) or 'none'})."
                        )
                booked[year["year"]] = {
                    key: year["by_group"][CostGroup.SUBSIDIES.value][key] for key in cls.BAND_KEYS
                }
            zero = dict.fromkeys(cls.BAND_KEYS, 0.0)
            for year_index in sorted(set(booked) | set(stated)):
                for key in cls.BAND_KEYS:
                    in_stack = booked.get(year_index, zero)[key]
                    in_rows = stated.get(year_index, zero)[key]
                    if abs(in_stack - in_rows) > tolerance:
                        raise SubsidyReconciliationError(
                            f"{variant}: in year {year_index} the Subsidies group of the annual series "
                            f"is {in_stack} in slot {key!r}, but the awarded {variant}.subsidies rows "
                            f"book {in_rows} in that year; the document would draw a credit its table "
                            "does not grant."
                        )

    @staticmethod
    def assert_every_measure_has_row(document: Mapping[str, Any]) -> None:
        """Raise unless every measure a stage carries out has a `by_subject` row naming it.

        The plan carries out the measures of every stage, the reference those of `stages[0]`; each must be the
        `measure_id` of at least one row of that evaluation. `write` checks this only for a document built with the
        translator's measure map.

        Args:
            document: The finished document.

        Raises:
            MeasureWithoutRowError: Naming the evaluation and the measures without a row.
        """
        stages = document["stages"]
        carried_out = {
            "reference": [measure for stage in stages[:1] for measure in stage["measures"]],
            "plan": [measure for stage in stages for measure in stage["measures"]],
        }
        for variant, measures in carried_out.items():
            named = {row["measure_id"] for row in document[variant]["by_subject"]}
            missing = sorted({measure for measure in measures if measure not in named})
            if missing:
                raise MeasureWithoutRowError(
                    f"{variant}.by_subject has no row for {missing}, which a stage carries out. Every "
                    "measure the translator acts on creates a cost subject or is declared costless or "
                    "unpriced in its mapping report (costless_subjects, unpriced_subjects); a report "
                    "that does neither is a translator bug, or was written before the declaration "
                    "existed and the stage must be translated again."
                )

    # ------------------------------------------------------------------ header blocks

    def _engine(self) -> Dict[str, Any]:
        """Return which code produced the document: the repository commit and the package version.

        The commit comes from `hisim.hisim_commit.HiSimCommit`, which reads a baked `hisim/COMMIT` file or the
        `HISIM_COMMIT` variable before falling back to git.
        """
        return {"hisim_commit": HiSimCommit.of(), "economics_version": self.ECONOMICS_VERSION}

    def _parameters_block(self) -> Dict[str, Any]:
        """Return the assumptions the plan was priced under, as the document's `parameters` block.

        Built by `StagedParameters.to_document_block`, the same key table `--parameters` reads, so the block fed back
        over the same stages gives the same run. `price_basis_year` is the year the evaluator resolved
        (`StagedResult.price_basis_year`), not the caller's record, which may leave it unset.
        """
        parameters = self._parameters
        if self._result.price_basis_year is not None:
            parameters = replace(parameters, price_basis_year=self._result.price_basis_year)
        return StagedParameters.to_document_block(
            parameters=parameters,
            perspective=self._perspective,
            weather_year=self._result.plan.simulation_year,
            subsidy_catalog=self._catalog_id,
            energy=self._result.energy_echo,
            plan_start_year=self._result.plan_start_year,
            price_basis_year_origin=self._result.price_basis_year_origin,
            investment_overrides=[
                StatedQuote(
                    stage=override.stage,
                    measure_id=override.measure_id,
                    amount_in_euro=override.amount_in_euro,
                    source=override.source,
                ).to_json()
                for override in self._result.investment_overrides
            ],
        )

    def _stages(self) -> List[Dict[str, Any]]:
        """Return one row per stage: its index, label, year, job and the measures it added."""
        return [
            {
                "index": index,
                "label": stage.label,
                "from_year": stage.from_year,
                "job_id": stage.job_id,
                "measures": list(stage.measures),
            }
            for index, stage in enumerate(self._result.stages)
        ]

    # ------------------------------------------------------------------ one evaluation

    def _evaluation(self, result: LifecycleCostResult, staged: bool) -> Dict[str, Any]:
        """Return the evaluation block for the reference or for the plan.

        Both share one shape so the frontend draws them side by side. `staged` only decides each row's stage: plan rows
        carry the stage that paid for or is active in them; the reference (one state held over the horizon) carries `0`
        in `annual` and `null` in `by_subject` and `subsidies`.

        Args:
            result: The evaluated variant.
            staged: Whether this is the staged plan rather than the reference.

        Returns:
            The evaluation block.
        """
        horizon = self._parameters.observation_period_in_years
        return {
            "totals": self._totals(result),
            "by_group": self._by_group(result),
            "by_category": {
                category.value: self._band(band) for category, band in sorted(
                    result.npv_by_category.items(), key=lambda item: item[0].value
                )
            },
            "by_payer": {
                payer.name: self._band(band)
                for payer, band in sorted(result.npv_by_payer.items(), key=lambda item: item[0].name)
            },
            "by_subject": self._by_subject(result, staged),
            "annual": self._annual(result, staged, horizon),
            "cumulative": self._cumulative(result, horizon),
            "energy_year1": self._energy_year1(result),
            "co2": self._co2(result),
            "subsidies": self._subsidies(result, staged),
            "financing": self._financing(result, horizon, staged),
        }

    def _totals(self, result: LifecycleCostResult) -> Dict[str, Any]:
        """Return the seven headline figures of an evaluation.

        `monthly_equivalent_cost_in_euro` is the equivalent annual cost divided by twelve (an even monthly spread, not
        a monthly annuity) and is the headline; it comes from the result. `monthly_cost_year1_in_euro` is year 1's cash
        over twelve, replacements included, so it is high when the reference replaces its boiler in year 1.
        """
        investment = UncertainValue.exact(0.0)
        for entry in result.timeline.entries:
            if entry.year == 0 and entry.category in self.INVESTMENT_TOTAL_CATEGORIES:
                investment = investment + entry.amount_in_euro
        levelized = result.levelized_cost_of_heat_in_euro_per_kwh
        monthly = result.monthly_cost_year1_in_euro
        return {
            "npv_in_euro": self._band(result.total_npv_in_euro),
            "equivalent_annual_cost_in_euro": self._band(result.equivalent_annual_cost_in_euro),
            "monthly_equivalent_cost_in_euro": self._band(result.monthly_equivalent_cost_in_euro),
            "monthly_cost_year1_in_euro": self._band(monthly) if monthly is not None else None,
            "investment_year0_in_euro": self._band(investment),
            "sunk_cost_written_off_in_euro": self._band(result.sunk_cost_written_off_in_euro),
            "levelized_cost_of_heat_in_euro_per_kwh": (
                self._band(levelized) if levelized is not None else None
            ),
        }

    def _by_group(self, result: LifecycleCostResult) -> Dict[str, Any]:
        """Return the eight group stacks of the NPV, each present even at zero.

        An empty group is a zero band rather than omitted, so the stacked charts keep their segment colours aligned.
        """
        totals: Dict[CostGroup, UncertainValue] = {group: UncertainValue.exact(0.0) for group in CostGroup}
        for category, band in result.npv_by_category.items():
            group = CostGroups.of(category)
            totals[group] = totals[group] + band
        return {group.value: self._band(totals[group]) for group in CostGroup}

    def _by_subject(self, result: LifecycleCostResult, staged: bool) -> List[Dict[str, Any]]:
        """Return one `by_subject` row per cost subject, with the measure and stage that produced it.

        - `stage`: the last stage that paid for the subject; `null` for a baseline subject no stage bought.
        - `unpriced`: the subject is in the plan but its price is unknown (e.g. an envelope measure with no `cost`
          block).
        - `investment_in_euro`: the investment, planning and removal booked in year 0 by every stage starting then. On
          the reference it is the whole purchase.
        - `investment_by_stage`: one `{"stage": k, "investment_in_euro": band}` per stage that booked investment,
          planning or removal for the subject, ascending, each in nominal euros of the stage's start year; a stage that
          carried the subject over or kept it as existing equipment has none. For a plan whose stages all start in year
          0 the entries sum to `investment_in_euro`. Empty on the reference.
        - `measure_id`: on the reference, only for a measure `stages[0]` carries out.
        - `service_life_years`, `service_life_origin`, `installation_year` (a kept asset's register year, or plan year
          0 plus the buying stage's `from_year`; plan year 0 is `plan_start_year`, else the price basis year) and
          `installation_year_origin`: `null` on carrier rows, synthetic subjects (financing, replacement reserve, CO2
          damage) and measure-only rows.
        - `investment_origin` and `investment_source`: where the row's purchase was priced from (`reader_quote`,
          `included_in_reader_quote`, `request` or `cost_database`) and the quote's or request's source sentence;
          `null` on carrier, unpriced and measure-only rows.
        - `note`: why a row has no price or costs nothing, or what a reader's quote made of it.
        - `replaces_subjects`: the reference subjects the measure's subject replaces (e.g. external insulation names
          `envelope_facade`); empty without a `measure_id`.

        A measure with no priced subject still gets a zero row (`_measure_only_rows`), so every measure appears.
        """
        rows: List[Dict[str, Any]] = []
        replacements = self._replacement_years(result)
        by_stage = self._investment_by_stage(result) if staged else {}
        reference_measures = set(self._result.stages[0].measures) if self._result.stages else set()
        for subject in sorted(result.component_breakdowns):
            breakdown = result.component_breakdowns[subject]
            categories = breakdown.npv_by_category
            measure_id = self._measure_ids.get(subject)
            if not staged and measure_id not in reference_measures:
                measure_id = None
            quote = self._result.quote_of(subject, staged)
            origin, source = self._result.investment_origin(subject, staged)
            unpriced = subject in self._unpriced and quote is None
            if unpriced:
                origin, source = None, None
            rows.append(
                self._row(
                    subject,
                    kind=SubjectKindNames.of(breakdown.subject_kind),
                    asset_class=breakdown.asset_class.value if breakdown.asset_class else None,
                    measure_id=measure_id,
                    stage=self._row_stage(subject, staged, by_stage),
                    unpriced=unpriced,
                    npv=breakdown.total_npv_in_euro,
                    investment=breakdown.investment_gross_in_euro,
                    investment_origin=origin.value if origin is not None else None,
                    investment_source=source,
                    investment_by_stage=sorted(by_stage.get(subject, {}).items()),
                    categories=categories,
                    life=self._life_fields(subject, staged),
                    replacement_years=replacements.get(subject, []),
                    note=self._note(subject, quote),
                    replaces_subjects=self._replaces.get(subject, []) if measure_id is not None else [],
                    source=self._source_of(subject),
                )
            )
        rows.extend(self._measure_only_rows(result, staged))
        rows.extend(self._no_flow_rows(result, staged, by_stage))
        return sorted(rows, key=lambda row: row["subject"])

    def _row_stage(self, subject: str, staged: bool, by_stage: Mapping[str, Mapping[int, Any]]) -> Optional[int]:
        """Return the `stage` of one `by_subject` row: the last stage that paid for the subject, else None.

        Stage 0 holds every reference subject but pays for none it merely keeps (a cylinder, a meter, a kept envelope
        element), so such a row is None unless stage 0 booked a purchase for it.

        Args:
            subject: The row's subject.
            staged: Whether the row is the plan's.
            by_stage: `_investment_by_stage` of the plan.

        Returns:
            The stage index, or None.
        """
        if not staged:
            return None
        stage = self._result.stage_of_subject(subject)
        if stage == 0 and 0 not in by_stage.get(subject, {}):
            return None
        return stage

    def _no_flow_rows(
        self, result: LifecycleCostResult, staged: bool, by_stage: Mapping[str, Mapping[int, Any]]
    ) -> List[Dict[str, Any]]:
        """Return one zero row per unpriced or costless subject that booked no flow at all.

        An unpriced subject is priced at zero, so it books money only where the timeline dates an event for it; a kept
        envelope element whose renewal falls after the horizon books nothing and has no pivot row. Its row here states
        its lifetime and installation year with `unpriced: true`. A costless subject with cost facts (the battery's
        energy-management controller) gets one the same way with `unpriced: false`. All bands are exact zeros, so no
        sum changes. The subjects are those of `stages[0]` on the reference and of every active stage on the plan.

        Args:
            result: The evaluation the rows go into.
            staged: Whether it is the plan.
            by_stage: `_investment_by_stage` of the plan, for `_row_stage`.

        Returns:
            The rows, unsorted.
        """
        zero = UncertainValue.exact(0.0)
        indices = sorted(set(self._result.active_stage_by_year)) if staged else [0]
        facts_by_subject: Dict[str, ComponentCostFacts] = {}
        for index in indices:
            if index < len(self._result.stages):
                facts_by_subject.update(
                    {facts.subject: facts.facts for facts in self._result.stages[index].inputs.cost_facts}
                )
        reference_measures = set(self._result.stages[0].measures) if self._result.stages else set()
        rows: List[Dict[str, Any]] = []
        for subject, facts in sorted(facts_by_subject.items()):
            if subject in result.component_breakdowns or subject not in self._unpriced | self._costless:
                continue
            measure_id = self._measure_ids.get(subject)
            if not staged and measure_id not in reference_measures:
                measure_id = None
            rows.append(
                self._row(
                    subject,
                    kind=SubjectKindNames.COMPONENT,
                    asset_class=facts.asset_class.value,
                    measure_id=measure_id,
                    stage=self._row_stage(subject, staged, by_stage),
                    unpriced=subject in self._unpriced,
                    npv=zero,
                    investment=zero,
                    investment_origin=None,
                    investment_source=None,
                    investment_by_stage=[],
                    categories={},
                    life=self._life_fields(subject, staged),
                    replacement_years=[],
                    note=self._notes.get(subject),
                    replaces_subjects=self._replaces.get(subject, []) if measure_id is not None else [],
                    source=self._source_of(subject),
                )
            )
        return rows

    def _note(self, subject: str, quote: Optional[Tuple[InvestmentOverride, bool]]) -> Optional[str]:
        """Return one row's `note`: the mapping report's, or what a reader's quote made of the row.

        A quote replaces an "unpriced" note; a quoted measure with no asset class says it is bought once; a further
        subject of a quoted measure says it was bought within the quote.

        Args:
            subject: The row's subject.
            quote: The quote that priced its purchase and whether this is the main subject, or None.

        Returns:
            The note, or None.
        """
        stated = self._notes.get(subject)
        if quote is None:
            return stated
        override, main = quote
        if not main:
            return self.INCLUDED_NOTE.format(measure_id=override.measure_id, stage=override.stage)
        if subject in self._unpriced:
            has_facts = any(
                facts.subject == subject for facts in self._result.stages[override.stage].inputs.cost_facts
            )
            return self.QUOTED_UNPRICED_NOTE if has_facts else self.QUOTED_PURCHASE_NOTE
        return stated

    #: The four lifetime and age fields of a row whose subject has no lifetime.
    NO_LIFE: ClassVar[Dict[str, None]] = {
        "service_life_years": None,
        "service_life_origin": None,
        "installation_year": None,
        "installation_year_origin": None,
    }

    def _life_fields(self, subject: str, staged: bool) -> Dict[str, Any]:
        """Return the four lifetime and age fields of one row, `null` where the subject has no lifetime."""
        life = self._result.life_of(subject, staged)
        if life is None:
            return dict(self.NO_LIFE)
        return {
            "service_life_years": life.service_life_years,
            "service_life_origin": life.service_life_origin.value,
            "installation_year": life.installation_year,
            "installation_year_origin": (
                life.installation_year_origin.value if life.installation_year_origin is not None else None
            ),
        }

    def _measure_only_rows(self, result: LifecycleCostResult, staged: bool) -> List[Dict[str, Any]]:
        """Return one zero row per measure the engine prices no subject for, in the stage that carries it out.

        The subject is the measure id, taken from the mapping report's `costless_subjects` (a setting, priced zero) or
        `unpriced_subjects` (HiSim holds no price). A subject that has a breakdown row or cost facts in any stage gets
        none here. Bands are exact zeros and lifetime fields `null`. On the plan the row's `stage` is the first stage
        carrying the measure out, with that stage's zero in `investment_by_stage`; on the reference a row exists only
        for a measure of `stages[0]`.

        Args:
            result: The evaluation the rows go into.
            staged: Whether it is the plan.

        Returns:
            The rows, unsorted.
        """
        zero = UncertainValue.exact(0.0)
        rows: List[Dict[str, Any]] = []
        stages = self._result.stages if staged else self._result.stages[:1]
        priced = {facts.subject for stage in self._result.stages for facts in stage.inputs.cost_facts}
        for subject in sorted(self._costless | self._unpriced):
            measure_id = self._measure_ids.get(subject)
            if measure_id is None or subject in priced or subject in result.component_breakdowns:
                continue
            carried_out = [index for index, stage in enumerate(stages) if measure_id in stage.measures]
            if not carried_out:
                continue
            stage = carried_out[0] if staged else None
            rows.append(
                self._row(
                    subject,
                    kind=SubjectKindNames.COMPONENT,
                    asset_class=None,
                    measure_id=measure_id,
                    stage=stage,
                    unpriced=subject in self._unpriced,
                    npv=zero,
                    investment=zero,
                    investment_origin=None,
                    investment_source=None,
                    investment_by_stage=[(stage, zero)] if stage is not None else [],
                    categories={},
                    life=self.NO_LIFE,
                    replacement_years=[],
                    note=self._notes.get(subject),
                    replaces_subjects=self._replaces.get(subject, []),
                    source=self._source_of(subject),
                )
            )
        return rows

    def _row(
        self,
        subject: str,
        kind: str,
        asset_class: Optional[str],
        measure_id: Optional[str],
        stage: Optional[int],
        unpriced: bool,
        npv: UncertainValue,
        investment: UncertainValue,
        investment_origin: Optional[str],
        investment_source: Optional[str],
        investment_by_stage: Iterable[Tuple[int, UncertainValue]],
        categories: Mapping[CostCategory, UncertainValue],
        life: Mapping[str, Any],
        replacement_years: List[int],
        note: Optional[str],
        source: Optional[Dict[str, Any]],
        replaces_subjects: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """Return one `by_subject` row; the only place its key set is written.

        Args:
            subject: The cost subject.
            kind: Its `SubjectKindNames` word.
            asset_class: Its asset class value, or None.
            measure_id: The measure that created it, or None.
            stage: The stage it is attributed to, or None.
            unpriced: Whether HiSim holds no price for it and no quote priced it.
            npv: Its net present value.
            investment: Its gross investment.
            investment_origin: Where that purchase was priced from (an `InvestmentOrigin` value), or None.
            investment_source: The quote's or request's source sentence, or None.
            investment_by_stage: `(stage, amount)` per stage that paid into it, ascending.
            categories: Its NPV by cost category; a missing category is an exact zero.
            life: The four lifetime and age fields (`_life_fields`, `NO_LIFE`).
            replacement_years: The years its replacements fall in.
            note: Why it has no price or costs nothing, or what a quote made of it; None otherwise.
            source: The KPI source of the component it is, or None.
            replaces_subjects: The reference subjects it replaces; empty when no measure created it.

        Returns:
            The row, in the schema's key order.
        """
        zero = UncertainValue.exact(0.0)
        return {
            "subject": subject,
            "source": source,
            "kind": kind,
            "asset_class": asset_class,
            "measure_id": measure_id,
            "stage": stage,
            "unpriced": unpriced,
            "npv_in_euro": self._band(npv),
            "investment_in_euro": self._band(investment),
            "investment_origin": investment_origin,
            "investment_source": investment_source,
            "investment_by_stage": [
                {"stage": index, "investment_in_euro": self._band(amount)} for index, amount in investment_by_stage
            ],
            "subsidy_in_euro": self._band(categories.get(CostCategory.SUBSIDY, zero)),
            "replacements_in_euro": self._band(categories.get(CostCategory.REPLACEMENT, zero)),
            "maintenance_in_euro": self._band(categories.get(CostCategory.MAINTENANCE, zero)),
            "residual_value_in_euro": self._band(categories.get(CostCategory.RESIDUAL_VALUE, zero)),
            **life,
            "replacement_years": replacement_years,
            "note": note,
            "replaces_subjects": list(replaces_subjects),
        }

    def _investment_by_stage(self, result: LifecycleCostResult) -> Dict[str, Dict[int, UncertainValue]]:
        """Return each subject's gross purchase on the plan's timeline, split by the stage that made it.

        The categories are `INVESTMENT_TOTAL_CATEGORIES` (investment, planning, removal) in whatever year the stage
        books them. An entry's stage is the stage that bought it (`StagedResult.stage_of_timeline_entry`), not the
        stage active in its year.

        Args:
            result: The plan's evaluation.

        Returns:
            Subject -> stage index -> the band that stage booked.
        """
        amounts: Dict[str, Dict[int, UncertainValue]] = {}
        for position, entry in enumerate(result.timeline.entries):
            if entry.category not in self.INVESTMENT_TOTAL_CATEGORIES:
                continue
            stage = self._result.stage_of_timeline_entry(position)
            if stage is None:
                continue
            per_stage = amounts.setdefault(entry.subject, {})
            per_stage[stage] = per_stage.get(stage, UncertainValue.exact(0.0)) + entry.amount_in_euro
        return amounts

    @staticmethod
    def _replacement_years(result: LifecycleCostResult) -> Dict[str, List[int]]:
        """Return the years each subject is replaced in, read off the timeline."""
        years: Dict[str, List[int]] = {}
        for entry in result.timeline.entries:
            if entry.category != CostCategory.REPLACEMENT:
                continue
            found = years.setdefault(entry.subject, [])
            if entry.year not in found:
                found.append(entry.year)
        return {subject: sorted(found) for subject, found in years.items()}

    def _annual(self, result: LifecycleCostResult, staged: bool, horizon: int) -> List[Dict[str, Any]]:
        """Return the year-by-year series with its group stack and markers.

        `calendar_year` is the plan's start year plus the relative year, and `null` when the plan names no start year;
        the stages' simulation (weather) year dates nothing.
        """
        start = self._result.plan_start_year
        by_year_group: Dict[int, Dict[CostGroup, UncertainValue]] = {
            year: {group: UncertainValue.exact(0.0) for group in CostGroup}
            for year in range(horizon + 1)
        }
        scoped = result.scoped_timeline()
        for entry in scoped.entries:
            if 0 <= entry.year <= horizon:
                group = CostGroups.of(entry.category)
                by_year_group[entry.year][group] = by_year_group[entry.year][group] + entry.amount_in_euro
        series = result.annual_cost_series_nominal_in_euro
        rows = []
        for year in range(horizon + 1):
            nominal = series[year] if year < len(series) else UncertainValue.exact(0.0)
            rows.append(
                {
                    "year": year,
                    "calendar_year": (start + year) if start is not None else None,
                    "stage": self._result.stage_of_year(year) if staged else 0,
                    "total_nominal_in_euro": self._band(nominal),
                    "total_discounted_in_euro": self._band(
                        nominal.scale(self._parameters.discount_factor(year))
                    ),
                    "by_group": {
                        group.value: self._band(by_year_group[year][group]) for group in CostGroup
                    },
                    "events": self._events(scoped.entries, year, staged),
                }
            )
        return rows

    def _events(self, entries: Iterable[CashFlowEntry], year: int, staged: bool) -> List[Dict[str, Any]]:
        """Return the markers of one year: what was bought, what was granted, which stage began."""
        events: List[Dict[str, Any]] = []
        if staged:
            for index, stage in enumerate(self._result.stages):
                if index > 0 and stage.from_year == year:
                    events.append({"kind": EventKind.STAGE_START.value, "stage": index})
        for entry in entries:
            if entry.year != year:
                continue
            if entry.category == CostCategory.INVESTMENT:
                events.append(
                    {
                        "kind": EventKind.INVESTMENT.value,
                        "subject": entry.subject,
                        "measure_id": self._measure_ids.get(entry.subject),
                    }
                )
            elif entry.category == CostCategory.SUBSIDY:
                events.append({"kind": EventKind.SUBSIDY.value, "scheme": entry.subsidy_scheme_id})
            elif entry.category == CostCategory.REPLACEMENT:
                events.append({"kind": EventKind.REPLACEMENT.value, "subject": entry.subject})
        return events

    def _cumulative(self, result: LifecycleCostResult, horizon: int) -> List[Dict[str, Any]]:
        """Return the running cumulative total, nominal and discounted."""
        series = result.annual_cost_series_nominal_in_euro
        nominal = UncertainValue.exact(0.0)
        discounted = UncertainValue.exact(0.0)
        rows = []
        for year in range(horizon + 1):
            amount = series[year] if year < len(series) else UncertainValue.exact(0.0)
            nominal = nominal + amount
            discounted = discounted + amount.scale(self._parameters.discount_factor(year))
            rows.append(
                {
                    "year": year,
                    "nominal_in_euro": self._band(nominal),
                    "discounted_in_euro": self._band(discounted),
                }
            )
        return rows

    def _energy_year1(self, result: LifecycleCostResult) -> List[Dict[str, Any]]:
        """The year-1 bill per carrier: volumes, money and the effective price between them."""
        rows = []
        scoped = result.scoped_timeline()
        for carrier in sorted(result.annual_energy_quantities_by_carrier):
            quantities = result.annual_energy_quantities_by_carrier[carrier]
            cost = UncertainValue.exact(0.0)
            revenue = UncertainValue.exact(0.0)
            subjects = bill_subjects(carrier)
            for entry in scoped.entries:
                if entry.year != 1 or entry.subject not in subjects:
                    continue
                if entry.category in CategoryRules.BILL_CATEGORIES:
                    cost = cost + entry.amount_in_euro
                elif entry.category == CostCategory.FEED_IN_REVENUE:
                    revenue = revenue + entry.amount_in_euro
            rows.append(
                {
                    "carrier": carrier,
                    "bought_in_kwh": quantities.bought_in_kwh,
                    "sold_in_kwh": quantities.sold_in_kwh,
                    "cost_in_euro": self._band(cost),
                    "revenue_in_euro": self._band(revenue),
                    "effective_price_in_euro_per_kwh": (
                        self._band(cost.scale(1.0 / quantities.bought_in_kwh))
                        if quantities.bought_in_kwh > 0
                        else None
                    ),
                }
            )
        return rows

    def _co2(self, result: LifecycleCostResult) -> Dict[str, Any]:
        """Return the emission masses as degenerate bands (three equal slots).

        The engine's CO2 accounting carries no band, but the document writes every quantity as a band; equal slots say
        "no band" without a special shape.
        """
        masses = result.lifecycle_co2_result
        by_year = list(masses.operational_co2_by_year_in_kg)
        operational_year1 = by_year[1] if len(by_year) > 1 else 0.0
        return {
            "embodied_in_kg": self._scalar_band(masses.embodied_co2_in_kg),
            "operational_year1_in_kg": self._scalar_band(operational_year1),
            "lifecycle_in_kg": self._scalar_band(masses.total_co2_in_kg),
            "by_year_in_kg": [self._scalar_band(mass) for mass in by_year],
        }

    def _subsidies(self, result: LifecycleCostResult, staged: bool) -> List[Dict[str, Any]]:
        """Return one row per scheme the solver considered, or the "no catalogue" rows.

        Without a catalogue the engine runs subsidy mode NONE, so nothing is awarded or refused; the document then
        states one undetermined row per buying stage saying the country has no catalogue, and books no support. On the
        plan, each row carries the stage whose evaluation made the decision.
        """
        if self._catalog_id is None:
            return self._no_catalogue_rows(staged)
        rows: List[Dict[str, Any]] = []
        awarded = self._awarded_amounts(result, staged)
        claimed: Set[AwardKey] = set()
        for stage, decision in self._decisions(result, staged):
            measure_id = self._measure_ids.get(decision.measure_subject)
            rows.extend(
                self._decision_rows(
                    decision,
                    stage,
                    measure_id,
                    awarded,
                    claimed,
                    self._result.subsidy_scale(stage, decision.measure_subject),
                )
            )
        return self._with_measure_maxima(rows)

    @classmethod
    def _with_measure_maxima(cls, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stamp every row with `max_amount_for_measure_in_euro`, the most a scheme can pay for the whole measure.

        A scheme funding a measure makes one decision (one row) per subject of it. `max_amount_in_euro` is the row's
        own subject's maximum; the measure's is the slot-wise sum over the rows of the same scheme, measure and stage,
        stated on each. It is None on a row without a measure, and on all rows of the group when any of them is None
        (an unknown term makes the sum unknown).
        """
        totals: Dict[Tuple[Any, Any, Any], Optional[Dict[str, float]]] = {}
        for row in rows:
            if row["measure_id"] is None:
                continue
            key = (row["scheme"], row["measure_id"], row["stage"])
            maximum = row["max_amount_in_euro"]
            if maximum is None:
                totals[key] = None
                continue
            total = totals.setdefault(key, {"min": 0.0, "best": 0.0, "max": 0.0})
            if total is None:
                continue
            for slot in total:
                total[slot] += maximum[slot]
        for row in rows:
            measure_maximum = (
                totals.get((row["scheme"], row["measure_id"], row["stage"])) if row["measure_id"] is not None else None
            )
            row["max_amount_for_measure_in_euro"] = dict(measure_maximum) if measure_maximum is not None else None
        return rows

    @classmethod
    def _maximum(cls, decision: SubsidyDecision, scheme_id: Optional[str], scale: float) -> Tuple[Any, Optional[str]]:
        """Return one row's `max_amount_in_euro` and the note it needs, if any.

        The maximum comes from `subsidies.scheme_maximum`, moved into the plan exactly as the stage's year-0 award is
        (valued on the cost as booked in the stage's year) and signed as a credit, like `amount_in_euro`.

        Args:
            decision: The decision the row belongs to.
            scheme_id: The row's scheme.
            scale: The factor the stage's year-0 figures of the subject are booked with.

        Returns:
            `(band or None, the note a None needs or None)`.
        """
        maximum: Optional[SchemeMaximum] = decision.maximum_by_scheme.get(str(scheme_id))
        if maximum is None:
            return None, cls.NO_MAXIMUM_NOTE
        if maximum.amount_in_euro is None:
            return None, maximum.note
        # `+ 0.0` turns the -0.0 a mirrored zero (a loan with no repayment grant) would carry into 0.0.
        band = cls._band(maximum.amount_in_euro.scale(scale).as_revenue())
        return {key: value + 0.0 for key, value in band.items()}, None

    @staticmethod
    def _joined(*notes: Optional[str]) -> Optional[str]:
        """Return the notes joined with `; `, or None when there are none."""
        present = [note for note in notes if note]
        return "; ".join(present) or None

    def _decisions(
        self, result: LifecycleCostResult, staged: bool
    ) -> List[Tuple[Optional[int], SubsidyDecision]]:
        """Return every subsidy decision of an evaluation, with the stage that made it.

        The plan's decisions are its stages' decisions in stage order, so they are read stage by stage. The reference's
        decisions carry no stage.
        """
        if not staged:
            return [(None, decision) for decision in result.subsidy_decisions]
        return [
            (index, decision)
            for index, stage_result in enumerate(self._result.per_stage)
            for decision in stage_result.subsidy_decisions
        ]

    def _no_catalogue_rows(self, staged: bool) -> List[Dict[str, Any]]:
        """The undetermined rows a plan priced without a catalogue publishes."""
        country = self._parameters.country
        stages = range(1, len(self._result.stages)) if staged else range(0, 1)
        return [
            {
                "scheme": None,
                "measure_id": None,
                "stage": index if staged else None,
                "status": SubsidyStatus.UNDETERMINED.value,
                "amount_in_euro": None,
                "amount_by_year_in_euro": None,
                "max_amount_in_euro": None,
                "max_amount_for_measure_in_euro": None,
                "binding_cap": None,
                "open_questions": [self.NO_CATALOGUE_QUESTION.format(country=country)],
                "note": self.NO_CATALOGUE_NOTE.format(country=country),
            }
            for index in stages
        ]

    #: What an awarded row of a benefit that books no cash says instead of a grant. Its amount is
    #: a zero band: the scheme was awarded, so the row is not a question, and its money is in
    #: another group of the stack.
    NON_CASH_NOTES: ClassVar[Dict[PayoutKind, str]] = {
        PayoutKind.LOAN_TERMS: SchemeMaximumNotes.SOFT_LOAN,
        PayoutKind.VAT_REDUCTION: "VAT reduction: the benefit is in the investment price, not a grant",
    }

    #: The note of the loan-terms row that carries the loan's repayment grant.
    REPAYMENT_GRANT_NOTE: ClassVar[str] = (
        "loan terms: the benefit is in the financing costs; the amount is the loan's repayment "
        "grant, which is plan-wide and stated on this row only"
    )

    #: The note of a further loan-terms row of the same scheme, whose loan is stated elsewhere.
    LOAN_STATED_ONCE_NOTE: ClassVar[str] = (
        "loan terms: the loan and its repayment grant are plan-wide and stated on the first row "
        "of this scheme"
    )

    @classmethod
    def _decision_rows(
        cls,
        decision: SubsidyDecision,
        stage: Optional[int],
        measure_id: Optional[str],
        awarded: Mapping[AwardKey, Mapping[int, UncertainValue]],
        claimed: Set[AwardKey],
        scale: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Return the rows of one measure's subsidy decision: awarded, refused and undecided.

        The awarded amount is read off the plan's timeline, not the award record, so the published support is what the
        NPV used (a staged award is moved into and valued in its stage's year). An awarded row always carries an
        amount; a benefit that books no cash states a zero band and says where its money is. A soft loan's repayment
        grant is booked under the financing subject, since the loan covers the stage's whole investment: the first
        loan-terms row of that scheme in the stage states it, later rows state zero.

        Args:
            decision: The solver's decision for one measure.
            stage: The stage that made the decision, or None on the reference.
            measure_id: The catalogue measure behind the decision's subject.
            awarded: What `_awarded_amounts` read off the timeline.
            claimed: The financing keys whose grant a row already states; updated in place.
            scale: The factor the stage books its year-0 awards with, applied to `max_amount_in_euro`; 1.0 on the
                reference.

        Returns:
            The rows, awarded first; every row states `max_amount_in_euro`.
        """
        rows: List[Dict[str, Any]] = []
        for award in decision.applied:
            by_year: Dict[int, UncertainValue] = dict(
                awarded.get((award.scheme_id, decision.measure_subject, stage), {})
            )
            notes = [award.display_name] if award.display_name else []
            if award.payout_kind == PayoutKind.LOAN_TERMS:
                financing_key = (award.scheme_id, FinancingConstants.FINANCING_SUBJECT, stage)
                if financing_key in claimed:
                    notes.append(cls.LOAN_STATED_ONCE_NOTE)
                else:
                    claimed.add(financing_key)
                    grant = awarded.get(financing_key, {})
                    for year, amount in grant.items():
                        by_year[year] = by_year.get(year, UncertainValue.exact(0.0)) + amount
                    notes.append(cls.REPAYMENT_GRANT_NOTE if grant else cls.NON_CASH_NOTES[award.payout_kind])
            elif award.payout_kind in cls.NON_CASH_NOTES and not by_year:
                notes.append(cls.NON_CASH_NOTES[award.payout_kind])
            binding = sorted(slot for slot, bound in award.caps_binding_per_slot.items() if bound)
            maximum, maximum_note = cls._maximum(decision, award.scheme_id, scale)
            rows.append(
                {
                    "scheme": award.scheme_id,
                    "measure_id": measure_id,
                    "stage": stage,
                    "status": SubsidyStatus.AWARDED.value,
                    "amount_in_euro": cls._band(UncertainValue.sum(by_year.values())),
                    "amount_by_year_in_euro": [
                        {"year": year, "amount_in_euro": cls._band(by_year[year])} for year in sorted(by_year)
                    ],
                    "max_amount_in_euro": maximum,
                    "binding_cap": ", ".join(binding) if binding else None,
                    "open_questions": [],
                    "note": cls._joined("; ".join(notes), maximum_note if maximum_note not in notes else None),
                }
            )
        for rejected in decision.rejected:
            maximum, maximum_note = cls._maximum(decision, rejected.get("scheme_id"), scale)
            rows.append(
                {
                    "scheme": rejected.get("scheme_id"),
                    "measure_id": measure_id,
                    "stage": stage,
                    "status": SubsidyStatus.INELIGIBLE.value,
                    "amount_in_euro": None,
                    "amount_by_year_in_euro": None,
                    "max_amount_in_euro": maximum,
                    "binding_cap": None,
                    "open_questions": [],
                    "note": cls._joined(
                        str(rejected.get("reason")) if rejected.get("reason") else None, maximum_note
                    ),
                }
            )
        for undetermined in decision.undetermined:
            scheme_id = undetermined.get("scheme_id")
            maximum, maximum_note = cls._maximum(decision, scheme_id, scale)
            rows.append(
                {
                    "scheme": undetermined.get("scheme_id"),
                    "measure_id": measure_id,
                    "stage": stage,
                    "status": SubsidyStatus.UNDETERMINED.value,
                    "amount_in_euro": None,
                    "amount_by_year_in_euro": None,
                    "max_amount_in_euro": maximum,
                    "binding_cap": None,
                    "open_questions": [str(field) for field in undetermined.get("missing_fields", [])],
                    "note": maximum_note,
                }
            )
        return rows

    def _awarded_amounts(
        self, result: LifecycleCostResult, staged: bool
    ) -> Dict[AwardKey, Dict[int, UncertainValue]]:
        """Return what each scheme paid for each subject in each stage, year by year, signed as a credit.

        Keyed by `(scheme id, subject, stage)`, since one scheme can fund two measures and one subject can be bought in
        one stage and enlarged in another. The stage is the one whose decision awarded the entry
        (`StagedResult.stage_of_timeline_entry`), falling back to the stage active in the entry's year; None on the
        reference. A soft loan's repayment grant is keyed under `FinancingConstants.FINANCING_SUBJECT`. Amounts come
        from the scoped timeline; the full timeline is walked only because the stage map is indexed by it.
        """
        scoped = {id(entry) for entry in result.scoped_timeline().entries}
        amounts: Dict[AwardKey, Dict[int, UncertainValue]] = {}
        for position, entry in enumerate(result.timeline.entries):
            if (
                entry.category != CostCategory.SUBSIDY
                or entry.subsidy_scheme_id is None
                or id(entry) not in scoped
            ):
                continue
            stage: Optional[int] = None
            if staged:
                stage = self._result.stage_of_timeline_entry(position)
                if stage is None:
                    stage = self._result.stage_of_year(entry.year)
            by_year = amounts.setdefault((entry.subsidy_scheme_id, entry.subject, stage), {})
            by_year[entry.year] = by_year.get(entry.year, UncertainValue.exact(0.0)) + entry.amount_in_euro
        return amounts

    def _financing(
        self, result: LifecycleCostResult, horizon: int, staged: bool
    ) -> Optional[Dict[str, Any]]:
        """Return the plan's loans, one per stage that borrowed, or None for a cash purchase.

        Each debt-service entry is attributed to the loan it repays, not to the loan of the stage active in the payment
        year, so a later stage borrowing before an earlier loan is repaid does not move instalments between loans.
        Totals are the same either way; the per-loan series is what the financing chart draws.

        Args:
            result: The evaluated variant.
            horizon: The last year index; every loan's schedule spans 0..T.
            staged: Whether this is the staged plan, whose entries carry their stage.

        Returns:
            The financing block, or None when the perspective buys for cash or nothing was borrowed.
        """
        plan = self._perspective.financing
        disbursed: Dict[int, UncertainValue] = {}
        service: Dict[int, Dict[int, UncertainValue]] = {}
        entries = list(result.timeline.entries)
        disbursement_years = sorted(
            entry.year for entry in entries if entry.category == CostCategory.LOAN_DISBURSEMENT
        )
        for position, entry in enumerate(entries):
            if entry.category == CostCategory.LOAN_DISBURSEMENT:
                previous = disbursed.get(entry.year, UncertainValue.exact(0.0))
                disbursed[entry.year] = previous + entry.amount_in_euro
            elif entry.category in (CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL):
                borrowed = self._borrowing_year(position, entry.year, disbursement_years, staged)
                by_year = service.setdefault(borrowed, {})
                previous = by_year.get(entry.year, UncertainValue.exact(0.0))
                by_year[entry.year] = previous + entry.amount_in_euro
        if plan is None or not disbursed:
            return None
        loans = []
        for year in sorted(disbursed):
            by_year = service.get(year, {})
            loans.append(
                {
                    "stage": self._result.stage_of_year(year) if staged else 0,
                    "principal_in_euro": self._negated_band(disbursed[year]),
                    "rate": plan.nominal_interest_rate,
                    "term_years": plan.term_in_years,
                    "debt_service_by_year_in_euro": [
                        self._band(by_year.get(index, UncertainValue.exact(0.0)))
                        for index in range(horizon + 1)
                    ],
                }
            )
        return {"loans": loans}

    def _borrowing_year(
        self, position: int, year: int, disbursement_years: List[int], staged: bool
    ) -> int:
        """Return the disbursement year of the loan a debt-service entry belongs to.

        On the plan the splice (the assembly of the plan's timeline from its stages' timelines) records each entry's
        stage, and a stage's loan disburses in that stage's year, so the answer is exact. On the reference, or without
        that record, it is the last disbursement at or before the payment year.

        Args:
            position: The entry's index in the timeline.
            year: The payment year.
            disbursement_years: The years a loan was disbursed in, ascending.
            staged: Whether the timeline is the staged plan's.

        Returns:
            The year the repaid loan was disbursed in.
        """
        if staged:
            stage_index = self._result.stage_of_timeline_entry(position)
            if stage_index is not None:
                return self._result.stages[stage_index].from_year
        earlier = [candidate for candidate in disbursement_years if candidate <= year]
        return earlier[-1] if earlier else 0

    # ------------------------------------------------------------------ comparison

    def _comparison(self, comparison: VariantComparison) -> Dict[str, Any]:
        """Plan minus reference: the deltas, the payback and the cumulative difference."""
        horizon = self._parameters.observation_period_in_years
        reference = self._result.reference
        plan = self._result.plan
        monthly_delta = None
        if plan.monthly_cost_year1_in_euro is not None and reference.monthly_cost_year1_in_euro is not None:
            monthly_delta = self._band(
                plan.monthly_cost_year1_in_euro - reference.monthly_cost_year1_in_euro
            )
        savings = comparison.cumulative_discounted_savings_in_euro
        cumulative = []
        reference_series = reference.annual_cost_series_nominal_in_euro
        plan_series = plan.annual_cost_series_nominal_in_euro
        nominal = UncertainValue.exact(0.0)
        for year in range(horizon + 1):
            reference_amount = (
                reference_series[year] if year < len(reference_series) else UncertainValue.exact(0.0)
            )
            plan_amount = plan_series[year] if year < len(plan_series) else UncertainValue.exact(0.0)
            nominal = nominal + (plan_amount - reference_amount)
            cumulative.append(
                {
                    "year": year,
                    "nominal_in_euro": self._band(nominal),
                    # `savings` is reference - plan; the document reports plan - reference.
                    "discounted_in_euro": self._negated_slots(
                        savings["low"][year], savings["best_estimate"][year], savings["high"][year]
                    ),
                }
            )
        return {
            "npv_delta_in_euro": self._band(comparison.npv_delta_in_euro),
            "equivalent_annual_cost_delta_in_euro": self._band(
                comparison.equivalent_annual_cost_delta_in_euro
            ),
            "monthly_equivalent_cost_delta_in_euro": self._band(
                comparison.monthly_equivalent_cost_delta_in_euro
            ),
            "monthly_cost_year1_delta_in_euro": monthly_delta,
            # The range by value, not by slot: which world pays back first depends on which
            # uncertainty dominates the savings.
            "discounted_payback_year": comparison.discounted_payback_envelope.to_band(),
            "cumulative_delta": cumulative,
            "lifecycle_co2_delta_in_kg": self._scalar_band(
                plan.lifecycle_co2_result.total_co2_in_kg
                - reference.lifecycle_co2_result.total_co2_in_kg
            ),
            "warm_rent_change_in_euro_per_m2_month": self._warm_rent(comparison),
        }

    def _warm_rent(self, comparison: VariantComparison) -> Optional[Dict[str, float]]:
        """Return the warm-rent change per square metre and month, or None.

        The engine reports the monthly change for the whole dwelling, and only for a tenant-scope perspective; without
        a known living area the field is None rather than a per-dwelling number.
        """
        change = comparison.warm_rent_change_per_month_in_euro
        area = self._result.plan.reference_areas.living_area_in_m2
        if change is None or not area:
            return None
        return self._band(change.scale(1.0 / area))

    # ------------------------------------------------------------------ bands

    @staticmethod
    def _band(value: UncertainValue) -> Dict[str, float]:
        """Return one band as the document writes it: `{"min", "best", "max"}`.

        Unlike `UncertainValue.to_json`, a degenerate band is not collapsed to a float, so every amount has the same
        three keys.
        """
        return {"min": value.minimum, "best": value.best_estimate, "max": value.maximum}

    @staticmethod
    def _negated_band(value: UncertainValue) -> Dict[str, float]:
        """Return a band with its sign flipped and its ends swapped, so it stays ordered.

        Used for a loan principal: on the timeline a disbursement is negative (money arriving), while
        `financing.loans[].principal_in_euro` states the amount borrowed as positive.
        """
        return {"min": -value.maximum, "best": -value.best_estimate, "max": -value.minimum}

    @staticmethod
    def _negated_slots(low: float, best: float, high: float) -> Dict[str, float]:
        """Negate three per-slot values into one ordered band.

        The cumulative difference is the negated cumulative savings, stored per slot and not an envelope: the low-world
        saving can exceed the high-world one. The ends are therefore chosen by value, not by position.

        Args:
            low: The value in the LOW world.
            best: The best estimate.
            high: The value in the HIGH world.

        Returns:
            The negated band with `min <= best <= max`.
        """
        negated = (-low, -best, -high)
        return {"min": min(negated), "best": -best, "max": max(negated)}

    @staticmethod
    def _scalar_band(value: float) -> Dict[str, float]:
        """Return a quantity with no band of its own as three equal slots."""
        return {"min": value, "best": value, "max": value}
