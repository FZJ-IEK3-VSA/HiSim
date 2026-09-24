"""``economics_result.json``: one staged plan written in the shape the frontend draws from.

The engine's own outputs (``lifecycle_costs.json``, ``component_costs.json``,
``cash_flow_timeline.csv``) are shaped for the engine's report and for re-pricing. The RenoVisor
frontend needs a different cut of the same money: nine charts, every one of them a selection out
of a document rather than arithmetic of its own. This module builds that document out of a
:class:`~hisim.economics.staged.StagedResult` and nothing else, and validates every document it
writes against ``economics_result.schema.json``, which ships beside it.

Four conventions hold throughout, and the schema enforces the first three:

* **Bands.** Every amount is ``{"min": …, "best": …, "max": …}`` — the engine's LOW /
  BEST_ESTIMATE / HIGH slots — so nothing downstream can collapse a band silently.
* **Signs.** Cost is positive, money arriving is negative: a subsidy, a feed-in revenue, a
  residual value and an anyway-cost credit are all negative, which is the engine's own rule
  (``cost_spec.md`` §3.6) carried through unchanged.
* **Relative years.** Years are ``0..T`` with ``calendar_year`` given on every row, so a reader
  never has to add ``simulation_year`` themselves.
* **Ids, not labels.** Subjects, asset classes, measure ids, scheme ids and perspective ids are
  written as they are; the frontend labels them from its own catalogue and country pack.

Example::

    document = StagedDocument(result, parameters, perspective, measure_ids={"HeatPump": "heating_system"})
    document.write(Path("economics_result.json"))

Specification: ``/home/contract-proposals/economics-hisim-spec.md`` §3 (the document) and §5 (what
the charts need from it), with the engine-side decisions of
``roadmap/renovisor/implementation/step10_staged_economics.md`` §3.
"""

from __future__ import annotations

import enum
import json
import os
from pathlib import Path
from typing import Any, ClassVar, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from hisim.economics.calculators.financing_application import FinancingConstants
from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective
from hisim.economics.results import LifecycleCostResult, VariantComparison
from hisim.economics.staged import StagedEvaluator, StagedResult
from hisim.economics.staged_parameters import StagedParameters
from hisim.economics.subsidies import PayoutKind, SubsidyDecision
from hisim.economics.timeline import CashFlowEntry, CategoryRules, CostCategory
from hisim.economics.uncertainty import UncertainValue


#: How an awarded amount is found on a timeline: ``(scheme id, subject, stage)``, where the stage
#: is the one whose evaluation booked it and ``None`` on the reference.
AwardKey = Tuple[str, str, Optional[int]]


class SchemaValidationUnavailableError(RuntimeError):
    """``jsonschema`` is not installed, so a written document cannot be validated.

    The document is a contract with a frontend that has no way of checking it, so writing one
    unvalidated would ship whatever shape the code happened to produce. Refusing before the file
    exists is the same choice ``__main__.AuditLayerProbe`` makes for the audit exports: a
    half-kept promise is worse than a named refusal.
    """


class BandOrderError(ValueError):
    """A band in the document is not ``min <= best <= max``.

    Raised by :meth:`StagedDocument.assert_bands_ordered` before the file is written. The schema
    can say that a band is three numbers but not that they are ordered, so the one invariant the
    frontend relies on when it draws a range — the low world is the low end — is checked here
    instead. It is always a bug in this module: every band it publishes comes from an
    :class:`~hisim.economics.uncertainty.UncertainValue`, which cannot be unordered, unless the
    document builds one out of separate slot values and gets the ends wrong.
    """


class SubsidyReconciliationError(ValueError):
    """An evaluation books a subsidy its own ``subsidies[]`` rows do not award.

    Raised by :meth:`StagedDocument.assert_subsidies_reconciled` before the file is written. The
    document states support twice — as the ``Subsidies`` group of every year's stack and as the
    awarded rows of ``subsidies[]`` — and a frontend draws both, so a grant in one that is absent
    from the other is a chart contradicting the table beside it (hisim-cyc.5). Like
    :class:`BandOrderError` it is always a bug in the engine or in this module, never in the input.
    """


class CostGroup(str, enum.Enum):
    """The eight stacks every chart of E-spec §5 draws, in the order they are drawn in.

    A closed set with a fixed order, because the frontend's stacked columns (V1) and stacked bars
    (V2) must show the same eight segments in the same sequence for the baseline and for the plan,
    and the document states that order once under its ``groups`` key rather than leaving two
    renderers to agree on it.

    The names are the E-spec's own and are *ids* like everything else in the document: the
    frontend translates them, and nothing in HiSim parses them back.
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
    """The one table mapping the engine's cost categories onto the document's eight groups.

    Every :class:`~hisim.economics.timeline.CostCategory` appears exactly once, which is what
    makes "the eight groups sum to the total" true by construction rather than by a test that
    happens to pass: a category added to the engine and forgotten here fails
    :meth:`assert_total`, not a chart.

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
        """The group one category is shown under.

        Args:
            category: An engine cost category.

        Returns:
            Its group.

        Raises:
            KeyError: If the category is not in the table, which means a category was added to the
                engine without deciding which stack it belongs in.
        """
        return cls.BY_CATEGORY[category]

    @classmethod
    def assert_total(cls) -> None:
        """Raise unless every cost category has a group.

        Called once when the module is imported, so an engine that grows a category fails at
        import rather than by writing a document whose stacks do not add up.

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
    """What the document says about one subsidy scheme (E-spec §3, §5.7).

    Three states and no fourth: a scheme was ``AWARDED``, was refused on an answered condition
    (``INELIGIBLE``), or could not be decided because a question the applicant has not answered
    stands in the way (``UNDETERMINED``). The last one exists so that an unanswered question is
    shown as a question; publishing it as a zero is the mistake §5.7 forbids, and is why an
    undetermined entry carries no amount at all rather than an amount of nothing.
    """

    AWARDED = "awarded"
    INELIGIBLE = "ineligible"
    UNDETERMINED = "undetermined"


class EventKind(str, enum.Enum):
    """The markers a year of the annual series can carry (E-spec §3, chart V1 and V9).

    Closed on purpose: the frontend draws one glyph per kind, so a new kind is a frontend change
    and not a HiSim decision. ``REPLACEMENT`` is the one addition to the E-spec's three examples,
    without which chart V9 ("which measure starts when, replacements") would have to re-derive
    replacement years from ``by_subject`` while the other markers come ready-made.
    """

    INVESTMENT = "investment"
    SUBSIDY = "subsidy"
    STAGE_START = "stage_start"
    REPLACEMENT = "replacement"


class SubjectKindNames:
    """How ``by_subject[].kind`` spells the two kinds of cost subject.

    The engine's :class:`~hisim.economics.timeline.SubjectKind` is ``COMPONENT`` / ``CARRIER``;
    the document writes them lower-case, which is what E-spec §3's example shows and what the
    rest of the document's own enumerations (statuses, event kinds) use.
    """

    #: A device or an envelope measure — something that was bought.
    COMPONENT: ClassVar[str] = "component"

    #: An energy carrier — something that was billed.
    CARRIER: ClassVar[str] = "carrier"

    @classmethod
    def of(cls, subject_kind: Any) -> str:
        """The document's spelling of one engine subject kind."""
        return str(getattr(subject_kind, "value", subject_kind)).lower()


class StagedDocument:
    """Builds and writes ``economics_result.json`` for one priced plan.

    The builder holds the priced plan and the context the plan cannot know about itself — which
    catalogue measure created which cost subject, which subjects are in the document without a
    price behind them, where the provenance file is — and turns them into one JSON document.
    Nothing is computed here that the plan does not already carry: every figure is a filter, a
    pivot or a sum of the two :class:`~hisim.economics.results.LifecycleCostResult` objects on the
    :class:`~hisim.economics.staged.StagedResult`, which is what makes the document and the
    engine's own report incapable of disagreeing.

    Args:
        result: The priced plan.
        parameters: The assumptions it was priced under; written into ``parameters`` so a stored
            document states its own basis.
        perspective: The accounting frame, for its id and its financing plan.
        measure_ids: Cost subject -> the catalogue measure that created it, from the translator's
            ``mapping_report.json`` ``subjects`` map. A subject absent from the mapping belongs to
            the baseline and is stamped ``null``.
        unpriced_subjects: Subjects present in the plan with no price behind them — an envelope
            measure whose request carried no ``cost`` block. They are flagged rather than hidden,
            so the document says what it does not know (step 10 §1).
        cost_provenance: Name of the provenance file beside the document, for ``provenance``.
    """

    #: Version of this document format. Bumped when a consumer would have to change.
    SCHEMA_VERSION: ClassVar[int] = 2

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
    NO_CATALOGUE_NOTE: ClassVar[str] = "no subsidy catalogue for {country}"

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
    ) -> None:
        """Store the plan and its context; nothing is built until :meth:`to_json`.

        Which catalogue the plan was priced under is read off the result
        (:attr:`~hisim.economics.staged.StagedResult.subsidy_catalog_id`) and not taken from the
        caller, so the ``parameters`` block and the ``subsidies[]`` rows cannot name a catalogue
        the figures were not priced with. A plan with no catalogue was priced with
        ``subsidy_mode: NONE`` (:meth:`~hisim.economics.staged.StagedEvaluator.priced_under`), so
        the parameters and the perspective are resolved the same way here, and the ``parameters``
        block says what ran whichever perspective the caller passed.
        """
        subsidy_catalog_id = result.subsidy_catalog_id
        if subsidy_catalog_id is None:
            parameters, perspective = StagedEvaluator.priced_under(parameters, perspective, None)
        self._result = result
        self._parameters = parameters
        self._perspective = perspective
        self._measure_ids: Dict[str, Optional[str]] = dict(measure_ids or {})
        self._unpriced: Set[str] = set(unpriced_subjects)
        self._catalog_id = subsidy_catalog_id
        self._cost_provenance = cost_provenance

    # ------------------------------------------------------------------ the document

    def to_json(self) -> Dict[str, Any]:
        """Return the whole document as JSON-ready data.

        Returns:
            The document of E-spec §3: ``schema_version``, ``engine``, ``parameters``,
            ``currency``, ``groups``, ``stages``, ``reference``, ``plan``, ``comparison`` and
            ``provenance``, in that order, so two runs of one plan produce the same bytes.
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

        Validation happens before the first byte is written, so a document that does not match its
        own schema never reaches a consumer. The file is written with a trailing newline and
        stable key order, which is what lets two runs of one plan be compared byte for byte.

        Args:
            path: Where the document goes; parent directories are created.

        Returns:
            The document that was written, so a caller need not read it back.

        Raises:
            SchemaValidationUnavailableError: If ``jsonschema`` cannot be imported.
            jsonschema.ValidationError: If the document does not match the schema, which is a bug
                in this module rather than in its inputs.
            BandOrderError: If any band in the document is not ``min <= best <= max``.
            SubsidyReconciliationError: If an evaluation books support that its ``subsidies[]``
                rows do not award.
        """
        document = self.to_json()
        self.validate(document)
        self.assert_bands_ordered(document)
        self.assert_subsidies_reconciled(document)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return document

    @classmethod
    def schema_path(cls) -> Path:
        """Where the shipped JSON Schema is, beside this module."""
        return Path(os.path.dirname(os.path.abspath(__file__))) / cls.SCHEMA_FILE_NAME

    @classmethod
    def schema(cls) -> Dict[str, Any]:
        """The shipped JSON Schema, parsed."""
        with cls.schema_path().open(encoding="utf-8") as handle:
            loaded: Dict[str, Any] = json.load(handle)
        return loaded

    @classmethod
    def validate(cls, document: Mapping[str, Any]) -> None:
        """Check one document against the shipped schema.

        Args:
            document: The document to check.

        Raises:
            SchemaValidationUnavailableError: If ``jsonschema`` is not installed.
            jsonschema.ValidationError: Naming the first place the document departs from the
                schema.
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

    @classmethod
    def assert_bands_ordered(cls, document: Any, path: str = "") -> None:
        """Raise unless every band in the document reads ``min <= best <= max``.

        The schema types a band as three numbers and cannot express their order, so a band that
        got its ends the wrong way round — a sign flip applied slot by slot, a difference not
        re-enveloped — validates and then draws backwards in the frontend. Checking it once over
        the finished document is cheap and catches every such mistake at the place the document is
        written rather than in a chart.

        Args:
            document: The document, or any part of it while recursing.
            path: The dotted path of ``document`` inside the whole, for the message.

        Raises:
            BandOrderError: Naming the first band that is out of order and where it is.
        """
        if isinstance(document, Mapping):
            if set(document) == set(cls.BAND_KEYS) and all(
                isinstance(document[key], (int, float)) and not isinstance(document[key], bool)
                for key in cls.BAND_KEYS
            ):
                low, best, high = (document[key] for key in cls.BAND_KEYS)
                if not low <= best <= high:
                    raise BandOrderError(
                        f"the band at {path or '<document>'} is {low}/{best}/{high}, which is not "
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
        """Raise unless each evaluation's ``Subsidies`` stack is exactly its awarded ``subsidies[]``.

        Three statements are checked for the reference and for the plan. Every ``subsidy`` event
        of the ``annual`` series names a scheme that ``subsidies[]`` reports as awarded, so no year
        carries support from a scheme the table does not know. Every awarded row's
        ``amount_by_year_in_euro`` sums to its ``amount_in_euro``. And in every year the
        ``Subsidies`` group equals the awarded rows' amounts booked in that year, slot by slot, so
        the chart and the table state one figure year by year and not only over the horizon: a
        grant moved to another year with its total conserved is refused. An evaluation with no
        awarded row therefore books no support in any year, which is the no-catalogue case
        hisim-cyc.5 was about.

        Args:
            document: The finished document.

        Raises:
            SubsidyReconciliationError: Naming the evaluation, the year where there is one, and the
                first disagreement found.
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

    # ------------------------------------------------------------------ header blocks

    def _engine(self) -> Dict[str, Any]:
        """Which code produced the document: the repository commit and the package version.

        The commit is asked of :class:`hisim.renovisor.report.HiSimCommit`, which reads a baked
        ``hisim/COMMIT`` file or the ``HISIM_COMMIT`` environment variable before falling back to
        git, so an image built without a ``.git`` directory still says what it is. The import is
        function-local: ``hisim.renovisor`` depends on ``hisim.economics`` and not the other way
        round, and this one provenance string is not worth inverting that.
        """
        from hisim.renovisor.report import HiSimCommit  # pylint: disable=import-outside-toplevel

        return {"hisim_commit": HiSimCommit.of(), "economics_version": self.ECONOMICS_VERSION}

    def _parameters_block(self) -> Dict[str, Any]:
        """The assumptions the plan was priced under, as the document states them.

        Built by :meth:`~hisim.economics.staged_parameters.StagedParameters.to_document_block`,
        which is also the table of keys ``--parameters`` reads. One table for both directions is
        what makes this block a legal input file: a reader can copy it out of a document, hand it
        back over the same stages and get the same run.
        """
        return StagedParameters.to_document_block(
            parameters=self._parameters,
            perspective=self._perspective,
            simulation_year=self._result.plan.simulation_year,
            subsidy_catalog=self._catalog_id,
        )

    def _stages(self) -> List[Dict[str, Any]]:
        """One row per stage: its index, label, year, job and the measures it added."""
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
        """The ``Evaluation`` block of E-spec §3, for the reference or for the plan.

        The same shape serves both, which is what lets the frontend draw the two side by side with
        one renderer. ``staged`` decides only where the per-row stage index comes from: the plan's
        rows carry the stage that paid for or is active in them; the reference — one state held
        over the whole horizon — carries ``0`` in its ``annual`` series and ``null`` in its
        ``by_subject`` rows and its ``subsidies`` rows, because no stage bought its subjects.

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
        """The seven headline figures of an evaluation.

        Two of them are monthly, and they answer different questions.
        ``monthly_equivalent_cost_in_euro`` is the equivalent annual cost over twelve: the level
        monthly payment worth the whole horizon, and the headline (hisim-cyc.6). The result
        carries it, from :class:`~hisim.economics.calculators.aggregation.TimelineAggregation`,
        so the document divides nothing itself. ``monthly_cost_year1_in_euro`` is year 1's cash
        over twelve, replacements included: true for that year, and misleading as a running cost
        when the reference replaces its boiler in year 1.
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
        """The eight stacks of the NPV, every one of them present even at zero.

        A group with nothing in it is written as a zero band rather than omitted, because chart V1
        stacks the baseline and the plan side by side and a missing segment would shift the colours
        of everything above it.
        """
        totals: Dict[CostGroup, UncertainValue] = {group: UncertainValue.exact(0.0) for group in CostGroup}
        for category, band in result.npv_by_category.items():
            group = CostGroups.of(category)
            totals[group] = totals[group] + band
        return {group.value: self._band(totals[group]) for group in CostGroup}

    def _by_subject(self, result: LifecycleCostResult, staged: bool) -> List[Dict[str, Any]]:
        """One row per cost subject, with the measure and the stage that produced it.

        Chart V4 (the investment build-up per stage) filters these rows by ``stage``, so every row
        carries one even when it is ``null`` — a subject of the baseline that no stage bought.
        ``unpriced`` says the opposite of what an absent row would: the subject is part of the
        plan and its price is not known, which is the honest answer for an envelope measure whose
        request carried no ``cost`` block.
        """
        rows: List[Dict[str, Any]] = []
        replacements = self._replacement_years(result)
        for subject in sorted(result.component_breakdowns):
            breakdown = result.component_breakdowns[subject]
            categories = breakdown.npv_by_category
            rows.append(
                {
                    "subject": subject,
                    "kind": SubjectKindNames.of(breakdown.subject_kind),
                    "asset_class": breakdown.asset_class.value if breakdown.asset_class else None,
                    "measure_id": self._measure_ids.get(subject),
                    "stage": self._result.stage_of_subject(subject) if staged else None,
                    "unpriced": subject in self._unpriced,
                    "npv_in_euro": self._band(breakdown.total_npv_in_euro),
                    "investment_in_euro": self._band(breakdown.investment_gross_in_euro),
                    "subsidy_in_euro": self._band(
                        categories.get(CostCategory.SUBSIDY, UncertainValue.exact(0.0))
                    ),
                    "replacements_in_euro": self._band(
                        categories.get(CostCategory.REPLACEMENT, UncertainValue.exact(0.0))
                    ),
                    "maintenance_in_euro": self._band(
                        categories.get(CostCategory.MAINTENANCE, UncertainValue.exact(0.0))
                    ),
                    "residual_value_in_euro": self._band(
                        categories.get(CostCategory.RESIDUAL_VALUE, UncertainValue.exact(0.0))
                    ),
                    "service_life_years": self._service_life(subject),
                    "replacement_years": replacements.get(subject, []),
                }
            )
        return rows

    def _service_life(self, subject: str) -> Optional[float]:
        """The service life declared for one subject, or ``None`` when the database decided it.

        Only an explicit ``lifetime_override_in_years`` is published: the database's own lifetime
        is a property of the price data rather than of this plan, and the document has no way of
        saying which entry it came from. A reader who needs it runs ``explain``.
        """
        for stage in self._result.stages:
            for facts in stage.inputs.cost_facts:
                if facts.subject == subject and facts.facts.lifetime_override_in_years is not None:
                    return float(facts.facts.lifetime_override_in_years)
        return None

    @staticmethod
    def _replacement_years(result: LifecycleCostResult) -> Dict[str, List[int]]:
        """Which years each subject is replaced in, read off the timeline."""
        years: Dict[str, List[int]] = {}
        for entry in result.timeline.entries:
            if entry.category != CostCategory.REPLACEMENT:
                continue
            found = years.setdefault(entry.subject, [])
            if entry.year not in found:
                found.append(entry.year)
        return {subject: sorted(found) for subject, found in years.items()}

    def _annual(self, result: LifecycleCostResult, staged: bool, horizon: int) -> List[Dict[str, Any]]:
        """The year-by-year series chart V1 draws, with its group stack and its markers."""
        simulation_year = result.simulation_year
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
                    "calendar_year": (simulation_year + year) if simulation_year is not None else None,
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
        """The markers of one year: what was bought, what was granted, what stage began."""
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
        """The running total chart V3 draws, nominal and discounted."""
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
            for entry in scoped.entries:
                if entry.year != 1 or entry.subject != carrier:
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
        """The emission masses, as degenerate bands.

        The engine's CO2 accounting is a mass and carries no uncertainty band of its own
        (:class:`~hisim.economics.results.LifecycleCo2Result`), while the document's rule is that
        every quantity is a band. The three slots are therefore equal here, which says "this
        figure has no band" in the document's own vocabulary rather than by a special shape the
        frontend would have to branch on.
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
        """One row per scheme the solver considered, or the "no catalogue" question.

        With no catalogue configured the engine runs ``subsidy_mode: NONE``, so nothing is awarded
        and nothing is refused; the honest statement is then one undetermined row per stage that
        buys something, saying that the country has no catalogue (step 10 §1). Such a plan books
        no support at all — the engine's §10.1 flat shim is retired — and
        :meth:`assert_subsidies_reconciled` refuses a document whose stacks book support these rows
        do not award.

        On the plan, each decision's rows carry the stage whose evaluation made the decision, so a
        subject bought in one stage and grown in another states each stage's grant on its own row.
        """
        if self._catalog_id is None:
            return self._no_catalogue_rows(staged)
        rows: List[Dict[str, Any]] = []
        awarded = self._awarded_amounts(result, staged)
        claimed: Set[AwardKey] = set()
        for stage, decision in self._decisions(result, staged):
            measure_id = self._measure_ids.get(decision.measure_subject)
            rows.extend(self._decision_rows(decision, stage, measure_id, awarded, claimed))
        return rows

    def _decisions(
        self, result: LifecycleCostResult, staged: bool
    ) -> List[Tuple[Optional[int], SubsidyDecision]]:
        """Every subsidy decision of an evaluation, with the stage that made it.

        The plan's decisions are its stages' decisions in stage order
        (:meth:`~hisim.economics.staged.StagedEvaluator.evaluate` concatenates them), so they are
        read stage by stage here, which is what tells each decision's stage. The reference is one
        state and its decisions carry no stage.
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
        PayoutKind.LOAN_TERMS: "loan terms: the benefit is in the financing costs, not a grant",
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
    ) -> List[Dict[str, Any]]:
        """The rows of one measure's subsidy decision: awarded, refused and undecided.

        The awarded amount is read off the plan's own timeline rather than off the award record,
        so what the document publishes as support is exactly what the NPV was computed with — a
        staged award is escalated and moved into its stage's year, and re-reading the award would
        state the unmoved figure. An awarded row always carries an amount: a benefit that books no
        cash states a zero band and says where its money is instead.

        A soft loan's repayment grant is booked under the financing subject rather than under any
        measure, because the loan is taken out against the stage's investment as a whole. The
        first loan-terms row of that scheme in the stage states it; a further row of the same
        scheme states zero and says where the loan is (``claimed`` remembers which grants are
        already stated, across the decisions of one evaluation).

        Args:
            decision: The solver's decision for one measure.
            stage: The stage that made the decision, or ``None`` on the reference.
            measure_id: The catalogue measure behind the decision's subject.
            awarded: What :meth:`_awarded_amounts` read off the timeline.
            claimed: The financing keys whose grant a row already states; updated in place.

        Returns:
            The rows, awarded first.
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
                    "binding_cap": ", ".join(binding) if binding else None,
                    "open_questions": [],
                    "note": "; ".join(notes) or None,
                }
            )
        for rejected in decision.rejected:
            rows.append(
                {
                    "scheme": rejected.get("scheme_id"),
                    "measure_id": measure_id,
                    "stage": stage,
                    "status": SubsidyStatus.INELIGIBLE.value,
                    "amount_in_euro": None,
                    "amount_by_year_in_euro": None,
                    "binding_cap": None,
                    "open_questions": [],
                    "note": str(rejected.get("reason")) if rejected.get("reason") else None,
                }
            )
        for undetermined in decision.undetermined:
            rows.append(
                {
                    "scheme": undetermined.get("scheme_id"),
                    "measure_id": measure_id,
                    "stage": stage,
                    "status": SubsidyStatus.UNDETERMINED.value,
                    "amount_in_euro": None,
                    "amount_by_year_in_euro": None,
                    "binding_cap": None,
                    "open_questions": [str(field) for field in undetermined.get("missing_fields", [])],
                    "note": None,
                }
            )
        return rows

    def _awarded_amounts(
        self, result: LifecycleCostResult, staged: bool
    ) -> Dict[AwardKey, Dict[int, UncertainValue]]:
        """What each scheme paid for each subject in each stage, year by year, signed as a credit.

        Keyed by ``(scheme id, subject, stage)`` because one scheme routinely funds two measures
        of a plan, and one subject can be bought in one stage and grown in another: each row states
        its own grant, not the scheme's total. The stage is the one the entry came from
        (:meth:`~hisim.economics.staged.StagedResult.stage_of_timeline_entry`), which is the stage
        whose decision awarded it even for a payout that runs on into a later stage's years; a
        result without that map falls back to the stage active in the entry's year. On the
        reference the stage is ``None``. A soft loan's repayment grant is keyed under
        :attr:`~hisim.economics.calculators.financing_application.FinancingConstants.FINANCING_SUBJECT`.

        Read off the scoped timeline, the one every other figure of the evaluation is a pivot of;
        the entries are walked on the full timeline only because the stage map is indexed by it.
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
        """The loans of the plan, one per stage that borrowed, or ``None`` for a cash purchase.

        Every debt-service entry is attributed to the loan it repays rather than to the loan of
        whichever stage is active in the payment year. The two differ whenever a later stage
        borrows before an earlier loan's term is up: the first loan's remaining instalments would
        otherwise move onto the second loan's schedule, and the block would show a ten-year loan
        repaid in two years (step 12 §2.2). Plan totals are unaffected — the same entries are
        reported either way — but the per-loan series is what the financing chart draws.

        Args:
            result: The evaluated variant.
            horizon: The last year index of the horizon; every loan's schedule spans 0..T.
            staged: Whether this is the staged plan, whose entries carry the stage they came from.

        Returns:
            The financing block, or ``None`` when the perspective buys for cash or nothing was
            borrowed.
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
        """The disbursement year of the loan a debt-service entry belongs to.

        On the plan the answer is carried rather than derived: the splice records which stage
        every entry came from (:meth:`~hisim.economics.staged.StagedResult.stage_of_timeline_entry`)
        and a stage's loan disburses in that stage's own year, so the attribution is exact even
        when two loans are being repaid in the same year. On the reference — one state, at most one
        loan — and for a result whose entries carry no stage, the fallback is the last
        disbursement at or before the payment year, which is the same answer wherever the map
        exists and never blames a loan that had not been taken out yet.

        Args:
            position: The entry's index in the timeline it was read from.
            year: The payment year.
            disbursement_years: The years a loan was disbursed in, ascending.
            staged: Whether the timeline is the staged plan's.

        Returns:
            The year the loan being repaid was disbursed in.
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
            "discounted_payback_year": {
                "min": comparison.discounted_payback_years.get("low"),
                "best": comparison.discounted_payback_years.get("best_estimate"),
                "max": comparison.discounted_payback_years.get("high"),
            },
            "cumulative_delta": cumulative,
            "lifecycle_co2_delta_in_kg": self._scalar_band(
                plan.lifecycle_co2_result.total_co2_in_kg
                - reference.lifecycle_co2_result.total_co2_in_kg
            ),
            "warm_rent_change_in_euro_per_m2_month": self._warm_rent(comparison),
        }

    def _warm_rent(self, comparison: VariantComparison) -> Optional[Dict[str, float]]:
        """The warm-rent change per square metre and month, or ``None``.

        The engine reports the change per month for the whole dwelling and only for a tenant-scope
        perspective; the document's unit is per square metre, so the figure exists only when both
        the change and the living area are known. Neither is invented: an unknown area makes the
        field ``null`` rather than a per-dwelling number labelled per square metre.
        """
        change = comparison.warm_rent_change_per_month_in_euro
        area = self._result.plan.reference_areas.living_area_in_m2
        if change is None or not area:
            return None
        return self._band(change.scale(1.0 / area))

    # ------------------------------------------------------------------ bands

    @staticmethod
    def _band(value: UncertainValue) -> Dict[str, float]:
        """One band as the document writes it: ``{"min", "best", "max"}``.

        Deliberately not :meth:`hisim.economics.uncertainty.UncertainValue.to_json`, which
        collapses a degenerate band to a bare float: the document's contract is that every amount
        has the same three keys, so a consumer never has to branch on the shape of a number.
        """
        return {"min": value.minimum, "best": value.best_estimate, "max": value.maximum}

    @staticmethod
    def _negated_band(value: UncertainValue) -> Dict[str, float]:
        """A band with its sign flipped and its ends swapped, so it stays ordered.

        Used for the one figure the document reports with the opposite sign to the timeline: a
        loan principal. On the timeline a disbursement is money arriving and therefore negative;
        ``financing.loans[].principal_in_euro`` is "how much was borrowed", which is a positive
        quantity in every chart that shows it. Negating a band swaps which end is the low world,
        which is why the ends are exchanged rather than negated in place.
        """
        return {"min": -value.maximum, "best": -value.best_estimate, "max": -value.minimum}

    @staticmethod
    def _negated_slots(low: float, best: float, high: float) -> Dict[str, float]:
        """Three per-slot values negated into one ordered band.

        The document's cumulative difference is the sign flip of the engine's cumulative *savings*
        curves, which are stored slot by slot (``results.compare``) and are not an envelope: the
        low-world saving can exceed the high-world one when the reference's band is wider than the
        plan's. Negating the three slots in place would then leave ``min > max`` and the chart
        would draw the range backwards, which is why the ends are chosen by value rather than by
        position — the same thing :meth:`_negated_band` does for an already-ordered band, and what
        :meth:`UncertainValue.__sub__` does when it re-sorts a difference into an envelope.

        Args:
            low: The value in the LOW world.
            best: The best estimate.
            high: The value in the HIGH world.

        Returns:
            The band with the sign flipped and ``min <= best <= max`` restored.
        """
        negated = (-low, -best, -high)
        return {"min": min(negated), "best": -best, "max": max(negated)}

    @staticmethod
    def _scalar_band(value: float) -> Dict[str, float]:
        """A quantity with no band of its own, written as three equal slots."""
        return {"min": value, "best": value, "max": value}
