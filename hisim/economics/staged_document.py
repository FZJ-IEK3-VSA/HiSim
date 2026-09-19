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
from typing import Any, ClassVar, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set

from hisim.economics.parameters import EconomicParameters
from hisim.economics.perspectives import Perspective
from hisim.economics.results import LifecycleCostResult, VariantComparison
from hisim.economics.staged import StagedResult
from hisim.economics.subsidies import SubsidyDecision
from hisim.economics.timeline import CashFlowEntry, CategoryRules, CostCategory
from hisim.economics.uncertainty import UncertainValue


class SchemaValidationUnavailableError(RuntimeError):
    """``jsonschema`` is not installed, so a written document cannot be validated.

    The document is a contract with a frontend that has no way of checking it, so writing one
    unvalidated would ship whatever shape the code happened to produce. Refusing before the file
    exists is the same choice ``__main__.AuditLayerProbe`` makes for the audit exports: a
    half-kept promise is worse than a named refusal.
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
        subsidy_catalog_id: What ``parameters.subsidy_catalog`` says: the catalogue in force, or
            ``None`` when the plan ran with none, in which case every subsidy row is undetermined.
        cost_provenance: Name of the provenance file beside the document, for ``provenance``.
    """

    #: Version of this document format. Bumped when a consumer would have to change.
    SCHEMA_VERSION: ClassVar[int] = 1

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
        subsidy_catalog_id: Optional[str] = None,
        cost_provenance: str = "cost_provenance.json",
    ) -> None:
        """Store the plan and its context; nothing is built until :meth:`to_json`."""
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
        """
        document = self.to_json()
        self.validate(document)
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
        """The assumptions the plan was priced under, as the document states them."""
        parameters = self._parameters
        simulation_year = self._result.plan.simulation_year
        return {
            "horizon_years": parameters.observation_period_in_years,
            "interest_rate": parameters.interest_rate,
            "country": parameters.country,
            "price_basis_year": parameters.price_basis_year,
            "simulation_year": simulation_year,
            "perspective_id": self._perspective.id,
            "escalation": {
                "general": parameters.general_price_escalation_rate,
                "investment": parameters.investment_price_escalation_rate,
                "feed_in": parameters.feed_in_escalation_rate,
                "energy": {
                    carrier.value: rate
                    for carrier, rate in sorted(
                        parameters.energy_price_escalation_rates.items(), key=lambda item: item[0].value
                    )
                },
            },
            "subsidy_catalog": self._catalog_id,
        }

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
        rows carry the stage that paid for or is active in them, and the reference — one state held
        over the whole horizon — carries stage 0 throughout.

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
            "financing": self._financing(result, horizon),
        }

    def _totals(self, result: LifecycleCostResult) -> Dict[str, Any]:
        """The six headline figures of an evaluation."""
        investment = UncertainValue.exact(0.0)
        for entry in result.timeline.entries:
            if entry.year == 0 and entry.category in self.INVESTMENT_TOTAL_CATEGORIES:
                investment = investment + entry.amount_in_euro
        levelized = result.levelized_cost_of_heat_in_euro_per_kwh
        monthly = result.monthly_cost_year1_in_euro
        return {
            "npv_in_euro": self._band(result.total_npv_in_euro),
            "equivalent_annual_cost_in_euro": self._band(result.equivalent_annual_cost_in_euro),
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
        buys something, saying that the country has no catalogue (step 10 §1). The engine's flat
        shim is never published as a grant.
        """
        if self._catalog_id is None:
            return self._no_catalogue_rows(staged)
        rows: List[Dict[str, Any]] = []
        awarded = self._awarded_amounts(result)
        for decision in result.subsidy_decisions:
            stage = self._result.stage_of_subject(decision.measure_subject) if staged else None
            measure_id = self._measure_ids.get(decision.measure_subject)
            rows.extend(self._decision_rows(decision, stage, measure_id, awarded))
        return rows

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
                "binding_cap": None,
                "open_questions": [self.NO_CATALOGUE_QUESTION.format(country=country)],
                "note": self.NO_CATALOGUE_NOTE.format(country=country),
            }
            for index in stages
        ]

    @classmethod
    def _decision_rows(
        cls,
        decision: SubsidyDecision,
        stage: Optional[int],
        measure_id: Optional[str],
        awarded: Mapping[str, UncertainValue],
    ) -> List[Dict[str, Any]]:
        """The rows of one measure's subsidy decision: awarded, refused and undecided.

        The awarded amount is read off the plan's own timeline rather than off the award record,
        so what the document publishes as support is exactly what the NPV was computed with — a
        staged award is escalated and moved into its stage's year, and re-reading the award would
        state the unmoved figure.
        """
        rows: List[Dict[str, Any]] = []
        for award in decision.applied:
            amount = awarded.get(award.scheme_id)
            binding = sorted(slot for slot, bound in award.caps_binding_per_slot.items() if bound)
            rows.append(
                {
                    "scheme": award.scheme_id,
                    "measure_id": measure_id,
                    "stage": stage,
                    "status": SubsidyStatus.AWARDED.value,
                    "amount_in_euro": cls._band(amount) if amount is not None else None,
                    "binding_cap": ", ".join(binding) if binding else None,
                    "open_questions": [],
                    "note": award.display_name or None,
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
                    "binding_cap": None,
                    "open_questions": [str(field) for field in undetermined.get("missing_fields", [])],
                    "note": None,
                }
            )
        return rows

    @staticmethod
    def _awarded_amounts(result: LifecycleCostResult) -> Dict[str, UncertainValue]:
        """What each scheme actually paid on this timeline, by scheme id, signed as a credit."""
        amounts: Dict[str, UncertainValue] = {}
        for entry in result.timeline.entries:
            if entry.category != CostCategory.SUBSIDY or entry.subsidy_scheme_id is None:
                continue
            previous = amounts.get(entry.subsidy_scheme_id, UncertainValue.exact(0.0))
            amounts[entry.subsidy_scheme_id] = previous + entry.amount_in_euro
        return amounts

    def _financing(self, result: LifecycleCostResult, horizon: int) -> Optional[Dict[str, Any]]:
        """The loans of the plan, one per stage that borrowed, or ``None`` for a cash purchase."""
        plan = self._perspective.financing
        disbursed: Dict[int, UncertainValue] = {}
        service: Dict[int, Dict[int, UncertainValue]] = {}
        for entry in result.timeline.entries:
            if entry.category == CostCategory.LOAN_DISBURSEMENT:
                previous = disbursed.get(entry.year, UncertainValue.exact(0.0))
                disbursed[entry.year] = previous + entry.amount_in_euro
            elif entry.category in (CostCategory.LOAN_INTEREST, CostCategory.LOAN_PRINCIPAL):
                by_year = service.setdefault(self._borrowing_year(entry.year), {})
                previous = by_year.get(entry.year, UncertainValue.exact(0.0))
                by_year[entry.year] = previous + entry.amount_in_euro
        if plan is None or not disbursed:
            return None
        loans = []
        for year in sorted(disbursed):
            by_year = service.get(year, {})
            loans.append(
                {
                    "stage": self._result.stage_of_year(year),
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

    def _borrowing_year(self, year: int) -> int:
        """The disbursement year of the loan a debt-service entry in ``year`` belongs to.

        One loan per stage, each starting in its stage's year, so a payment in year ``y`` belongs
        to the last stage that started at or before ``y``. With one loan the answer is 0 and the
        attribution is trivially right; with several it is the same rule the operating splice uses.
        """
        stage = self._result.stages[self._result.stage_of_year(year)]
        return stage.from_year

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
                    "discounted_in_euro": {
                        "min": -savings["low"][year],
                        "best": -savings["best_estimate"][year],
                        "max": -savings["high"][year],
                    },
                }
            )
        return {
            "npv_delta_in_euro": self._band(comparison.npv_delta_in_euro),
            "equivalent_annual_cost_delta_in_euro": self._band(
                comparison.equivalent_annual_cost_delta_in_euro
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
    def _scalar_band(value: float) -> Dict[str, float]:
        """A quantity with no band of its own, written as three equal slots."""
        return {"min": value, "best": value, "max": value}
