"""The ``costs`` block of ``result.json``: the cost engine's figures plus the envelope materials.

Decision Q23 made HiSim responsible for the whole ``Costs`` block of the contract, and split it in
two along the line of who owns the numbers. Everything about the *devices* and the *bills* comes
from HiSim's lifecycle cost engine, which prices an Irish run against ``devices_IE.json``,
``energy_prices_IE.json`` and ``escalation_defaults_IE.json``; those files are AI estimates that
nobody has reviewed yet, so every figure resting on them is ``PARTIAL`` and says so. Everything
about the *envelope* comes from the insulation-material database and from nowhere else: decisions
Q8/Q9 forbid HiSim from estimating envelope prices, and the engine's own thirteen envelope entries
are AI estimates of exactly that kind. The material database gives euro per cubic metre and no
labour, so the envelope figure is the material cost alone, published as ``PARTIAL`` with a source
saying what it leaves out until the materials team's total-cost column exists.

The two halves meet in one place, ``investment_costs_in_euro``, and the payload keeps the seam
visible: ``investment_breakdown`` carries the device half and the envelope half as separate
figures, so a reader can see which part of a retrofit's price tag is an engine estimate and which
is a material list::

    "investment_costs_in_euro":  {"value": {"low": …, "best_estimate": …, "high": …}, …}
    "investment_breakdown": {"devices": {…}, "envelope_material": {…}}

Which perspective the figures are read under is :attr:`CostSources.PERSPECTIVE_ID`, and the choice
matters: the bundle's greenfield rows are the ones a run with no existing-asset register
evaluates, and of those, the *gross* one is the honest source here. Its sibling ``greenfield_net``
applies the §10.1 flat subsidy shim whenever no country catalogue is configured, which for Ireland
today would put a German-shaped support percentage into an Irish result. The grant is therefore
its own field, read from the net view only when a real ``subsidy_catalog/IE.json`` is configured,
and absent until that file exists (decision Q24; the file itself is step 6b).
"""

import csv
import dataclasses
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.renovisor.kpis import PayloadFieldRow
from hisim.renovisor.layers import EnvelopeLayers
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.provenance import MissingField, ProvenancedValue, Range
from hisim.renovisor.vocabulary import Provenance


class CostField(str, Enum):
    """The names the payload's ``costs`` block uses, in HiSim spelling (decision C3).

    The vendored contract still spells them ``investment_costs_euro`` and
    ``monthly_net_cost_20y_euro``; C3 fixed HiSim's ``_in_euro`` convention as the contract's, and
    the contract PR of step 7 adopts these names. ``investment_breakdown`` is the one member that
    is not a contract field: it is the split decision Q23 asks HiSim to keep visible.
    """

    INVESTMENT = "investment_costs_in_euro"
    ENERGY = "energy_costs_in_euro_per_year"
    MAINTENANCE = "maintenance_costs_in_euro_per_year"
    NET_PRESENT_VALUE = "net_present_value_in_euro"
    MONTHLY_TWENTY_YEARS = "monthly_net_cost_20y_in_euro"
    MONTHLY_TEN_YEARS = "monthly_net_cost_10y_in_euro"
    GRANT = "grant_in_euro"
    PAYBACK = "payback_period_in_years"
    PROPERTY_VALUE = "property_value_increase_in_percent"
    INVESTMENT_BREAKDOWN = "investment_breakdown"


class CostSources:
    """Which engine figure each contract field reads, and under which perspective.

    A table for the same reason :class:`hisim.renovisor.kpis.KpiSources` is one: the mapping from
    a contract field to a cost-engine field is a claim a reviewer should be able to check in one
    place, and a reader of ``result.json`` should be able to follow a number back to the export it
    came from without reading the builder.
    """

    #: The perspective every cost figure but the grant is read under. ``greenfield_gross`` is the
    #: first row of the shipped bundle that a register-free RenoVisor run evaluates, and the right
    #: one of the two: it applies no subsidy at all, so the payload's investment and net present
    #: value are the price before support, and support is reported once, in its own field, from a
    #: catalogue rather than from the flat shim.
    PERSPECTIVE_ID: ClassVar[str] = "greenfield_gross"

    #: The perspective the grant is read under, when a country subsidy catalogue is configured:
    #: the same greenfield situation with the subsidy engine switched on.
    SUBSIDY_PERSPECTIVE_ID: ClassVar[str] = "greenfield_net"

    #: The timeline year a system's own investment falls in.
    INVESTMENT_YEAR: ClassVar[int] = 0

    #: The first fully billed year, which is what "per year" means for an energy or maintenance
    #: figure. Year 0 holds the investment and, under the engine's end-of-year convention, no full
    #: year of operation.
    FIRST_BILLED_YEAR: ClassVar[int] = 1

    #: The timeline category holding the year-0 device investments.
    INVESTMENT_CATEGORY: ClassVar[str] = "INVESTMENT"

    #: The timeline category holding maintenance.
    MAINTENANCE_CATEGORY: ClassVar[str] = "MAINTENANCE"

    #: The timeline categories that together are an energy bill: the working price, the standing
    #: charge, the carbon price and the capacity charge. Feed-in revenue is deliberately not among
    #: them -- it is income, and netting it into a "cost per year" would make a large enough PV
    #: array look like a negative energy bill.
    ENERGY_CATEGORIES: ClassVar[Tuple[str, ...]] = (
        "ENERGY_WORKING",
        "ENERGY_STANDING",
        "ENERGY_CO2_PRICE",
        "ENERGY_CAPACITY_CHARGE",
    )

    #: The engine field the net present value is read from.
    NET_PRESENT_VALUE_FIELD: ClassVar[str] = "total_npv_in_euro"

    #: The engine field the monthly figures are derived from.
    EQUIVALENT_ANNUAL_COST_FIELD: ClassVar[str] = "equivalent_annual_cost_in_euro"

    #: The engine field naming the horizon a result was evaluated over.
    HORIZON_FIELD: ClassVar[str] = "observation_period_in_years"

    #: The shorter horizon the contract also asks for, evaluated a second time from the stored
    #: inputs rather than by simulating again.
    SHORT_HORIZON_IN_YEARS: ClassVar[int] = 10

    #: Months in a year, the divisor behind both monthly figures.
    MONTHS_PER_YEAR: ClassVar[float] = 12.0

    #: The category whose NPV is the support the subsidy engine awarded. It is negative-signed on
    #: the timeline (support reduces cost), so the published grant is its magnitude.
    SUBSIDY_CATEGORY: ClassVar[str] = "SUBSIDY"

    #: Why the price basis of every engine figure is only partly reviewed, quoted in each source.
    PRICE_BASIS_NOTE: ClassVar[str] = (
        "Irish device and energy prices are AI estimates (src_ai_estimates) and are not reviewed"
    )


class CostDocuments:
    """The cost engine's exports of one run, read as files.

    The engine writes its result as ``lifecycle_costs.json`` (the per-perspective KPIs and NPV
    pivots), ``lifecycle_kpis.json`` (the same figures as a flat namespaced KPI list) and
    ``cash_flow_timeline.csv`` (every cash flow, one row per entry). This class reads the first
    and the third, which between them answer every question the payload asks: the JSON has the
    discounted totals and the timeline has the per-year, per-category detail the annual figures
    need.

    Args:
        perspectives: The parsed ``lifecycle_costs.json``, keyed by perspective id.
        timeline: The parsed ``cash_flow_timeline.csv`` rows.
    """

    #: The engine's primary machine-readable export.
    COSTS_FILE_NAME: ClassVar[str] = "lifecycle_costs.json"

    #: The engine's flat KPI export, read for nothing the JSON does not carry but named here so a
    #: reader of the payload knows both files are the engine's and not the translation layer's.
    KPIS_FILE_NAME: ClassVar[str] = "lifecycle_kpis.json"

    #: The unaggregated timeline every engine figure is a pivot of.
    TIMELINE_FILE_NAME: ClassVar[str] = "cash_flow_timeline.csv"

    #: The timeline CSV's delimiter, as ``exports.write_cash_flow_timeline`` writes it.
    TIMELINE_DELIMITER: ClassVar[str] = ";"

    #: The timeline columns this module reads.
    PERSPECTIVE_COLUMN: ClassVar[str] = "perspective"
    YEAR_COLUMN: ClassVar[str] = "year"
    CATEGORY_COLUMN: ClassVar[str] = "category"
    NOMINAL_LOW_COLUMN: ClassVar[str] = "nominal_min"
    NOMINAL_BEST_COLUMN: ClassVar[str] = "nominal_best_estimate"
    NOMINAL_HIGH_COLUMN: ClassVar[str] = "nominal_max"

    def __init__(self, perspectives: Mapping[str, Any], timeline: Tuple[Mapping[str, str], ...]) -> None:
        """Store the two parsed exports."""
        self._perspectives = dict(perspectives)
        self._timeline = timeline

    @classmethod
    def load(cls, results_directory: Path) -> Optional["CostDocuments"]:
        """Read the engine's exports out of a run's result directory.

        Args:
            results_directory: The simulation's own output directory.

        Returns:
            The documents, or ``None`` when ``lifecycle_costs.json`` is not there -- which is what
            a run without ``COMPUTE_LIFECYCLE_COSTS`` leaves behind, and makes every cost field
            absent rather than zero.
        """
        costs_path = results_directory / cls.COSTS_FILE_NAME
        if not costs_path.is_file():
            return None
        perspectives = json.loads(costs_path.read_text(encoding="utf-8"))
        rows: List[Mapping[str, str]] = []
        timeline_path = results_directory / cls.TIMELINE_FILE_NAME
        if timeline_path.is_file():
            with timeline_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter=cls.TIMELINE_DELIMITER))
        return cls(perspectives=perspectives, timeline=tuple(rows))

    def perspective_ids(self) -> Tuple[str, ...]:
        """Return every perspective the run evaluated, in bundle order."""
        return tuple(self._perspectives)

    def result(self, perspective_id: str) -> Optional[Mapping[str, Any]]:
        """Return one perspective's result object, or ``None`` when the run has no such row."""
        result = self._perspectives.get(perspective_id)
        return result if isinstance(result, Mapping) else None

    def banded_field(self, perspective_id: str, field: str) -> Optional[Range]:
        """Return one top-level banded figure of a perspective.

        Args:
            perspective_id: Which perspective to read.
            field: The engine field name, e.g. ``"total_npv_in_euro"``.

        Returns:
            The range, or ``None`` when the perspective or the field is absent.
        """
        result = self.result(perspective_id)
        if result is None:
            return None
        return Range.from_uncertain_value(result.get(field))

    def category_npv(self, perspective_id: str, category: str) -> Optional[Range]:
        """Return the net present value of one cost category of one perspective.

        Args:
            perspective_id: Which perspective to read.
            category: The ``CostCategory`` value, e.g. ``"SUBSIDY"``.

        Returns:
            The range, or ``None`` when the perspective has no entry in that category.
        """
        result = self.result(perspective_id)
        if result is None:
            return None
        by_category = result.get("npv_by_category")
        if not isinstance(by_category, Mapping):
            return None
        return Range.from_uncertain_value(by_category.get(category))

    def horizon_in_years(self, perspective_id: str) -> Optional[int]:
        """Return the observation period one perspective was evaluated over, in years."""
        result = self.result(perspective_id)
        parameters = result.get("parameters") if result is not None else None
        horizon = parameters.get(CostSources.HORIZON_FIELD) if isinstance(parameters, Mapping) else None
        return int(horizon) if isinstance(horizon, int) else None

    def nominal_total(
        self, perspective_id: str, year: int, categories: Tuple[str, ...]
    ) -> Optional[Range]:
        """Sum the nominal cash flows of one year and a set of categories, slot by slot.

        This is the one derivation this module performs on the timeline, and it is a filter and a
        sum rather than a new economic step: the engine's own exports are pivots of exactly these
        rows, so a figure computed here reconciles with the published totals by construction.

        Args:
            perspective_id: Which perspective's timeline to read.
            year: The timeline year, 0 for the investment and 1 for the first billed year.
            categories: The cost categories to include.

        Returns:
            The slot-wise sum, or ``None`` when the timeline holds no such row at all -- which is
            a different statement from a total of zero and has to stay one.
        """
        wanted = set(categories)
        total = Range.zero()
        found = False
        for row in self._timeline:
            if row.get(self.PERSPECTIVE_COLUMN) != perspective_id:
                continue
            if row.get(self.CATEGORY_COLUMN) not in wanted:
                continue
            try:
                if int(row[self.YEAR_COLUMN]) != year:
                    continue
                total = total.plus(
                    Range(
                        low=float(row[self.NOMINAL_LOW_COLUMN]),
                        best_estimate=float(row[self.NOMINAL_BEST_COLUMN]),
                        high=float(row[self.NOMINAL_HIGH_COLUMN]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
            found = True
        return total if found else None


class EnvelopeMaterialCost:
    """The material cost of a package's insulation layers, in euro, per country.

    Decisions Q8/Q9 are the whole content of this class: an envelope measure is priced from the
    materials database and from nothing else, and the database states a minimum and a maximum
    price per cubic metre per country. So the figure is ``price per m3 x thickness x area`` summed
    over the layers, with the low and high bounds carried through and the midpoint as the best
    estimate, and it is *material only* -- no labour, no scaffolding, no system parts -- until the
    materials team's total-cost column arrives.

    It is deliberately not fed to the cost engine as a ``SubjectCostFacts``: the engine would price
    it identically, since the number is handed in either way, and adding it here keeps the
    envelope half of the investment visible as its own figure in ``investment_breakdown``.
    """

    #: What the figure covers and what it does not, quoted in the payload's ``source``.
    SCOPE_NOTE: ClassVar[str] = (
        "material only, from insulation_materials.json investment_cost_in_euro_per_m3; no labour, "
        "scaffolding or system parts until the materials database gains its total-cost column "
        "(Q8/Q9)"
    )

    def __init__(self, layers: EnvelopeLayers, materials: InsulationMaterials, country: str) -> None:
        """Store the layers, the table and the country whose price column is read."""
        self._layers = layers
        self._materials = materials
        self._country = country

    def total(self) -> Tuple[Optional[Range], str]:
        """Return the material cost of every layer and the sentence describing it.

        Returns:
            ``(range, source)`` when every layer could be priced, and ``(None, reason)`` when one
            could not -- an unresolved area, a material with no row, or a country with no price
            column for it. A partial envelope cost would understate a retrofit's price, so the
            whole figure drops out and the reason is published instead.
        """
        if self._layers.is_empty():
            return None, "the package adds no insulation layer"
        total = Range.zero()
        terms: List[str] = []
        for layer in self._layers.all():
            volume = layer.volume_in_m3()
            if volume is None:
                return None, f"no element area for {layer.describe()}"
            if not self._materials.contains(layer.material_asp_id):
                return None, f"the material table has no row for '{layer.material_asp_id}'"
            prices = self._materials.by_asp_id(layer.material_asp_id).investment_cost_in_euro_per_m3
            price = prices.get(self._country)
            if price is None or price.low is None or price.high is None:
                return None, (
                    f"the material table states no {self._country} price per m3 for "
                    f"'{layer.material_asp_id}'"
                )
            total = total.plus(Range.from_bounds(price.low * volume, price.high * volume))
            terms.append(
                f"{layer.describe()} = {volume:g} m3 x {price.low:g}-{price.high:g} EUR/m3 "
                f"[{self._country}]"
            )
        return total, f"{self.SCOPE_NOTE}: " + "; ".join(terms)


class ShortHorizonEvaluation:
    """A second evaluation of the stored inputs over a shorter horizon.

    The contract asks for a monthly net cost over ten years beside the one over twenty, and the
    engine's horizon is a parameter of an *evaluation*, not of a simulation: ``economic_inputs.json``
    is the faithful extract of the run, and re-pricing it against the same database with a
    different ``observation_period_in_years`` is a matter of milliseconds and touches no
    simulation, no post-processing and no file the run already wrote. That is what this class does,
    and why the ten-year figure is a real evaluation rather than a twenty-year figure rescaled.

    Nothing about it may break a calculation: the imports are local to :meth:`equivalent_annual_cost`
    so that a HiSim without the cost engine's optional dependencies still produces a payload, and
    any failure returns ``None`` with the reason, which makes the field absent.
    """

    #: The file the evaluation reads, written by the engine before anything economic happens.
    INPUTS_FILE_NAME: ClassVar[str] = "economic_inputs.json"

    @classmethod
    def equivalent_annual_cost(
        cls, results_directory: Path, perspective_id: str, horizon_in_years: int
    ) -> Tuple[Optional[Range], str]:
        """Re-evaluate one perspective over *horizon_in_years* and return its annual cost.

        Args:
            results_directory: The run's result directory, holding ``economic_inputs.json``.
            perspective_id: The perspective to evaluate, one of the shipped bundle's.
            horizon_in_years: The observation period to evaluate over.

        Returns:
            ``(range, source)`` on success, ``(None, reason)`` otherwise.
        """
        if not (results_directory / cls.INPUTS_FILE_NAME).is_file():
            return None, f"the run wrote no {cls.INPUTS_FILE_NAME} to re-evaluate"
        try:
            from hisim.economics.database import CostDatabase
            from hisim.economics.evaluator import EconomicEvaluator
            from hisim.economics.parameters import EconomicParameters
            from hisim.economics.perspectives import load_default_bundle, select_applicable
            from hisim.economics.serialization import read_inputs, read_stored_parameters
            from hisim.economics.subsidies import SubsidyCatalog

            inputs = read_inputs(str(results_directory))
            stored = read_stored_parameters(str(results_directory)) or EconomicParameters()
            parameters = dataclasses.replace(stored, observation_period_in_years=horizon_in_years)
            perspectives = select_applicable(
                load_default_bundle(), has_register=inputs.existing_assets is not None
            )
            chosen = next((item for item in perspectives if item.id == perspective_id), None)
            if chosen is None:
                return None, f"the default perspective bundle has no applicable '{perspective_id}'"
            catalog = SubsidyCatalog.load_configured(parameters.country, parameters.subsidy_catalog_path)
            evaluator = EconomicEvaluator(CostDatabase(parameters.cost_database_path), parameters, catalog)
            result = evaluator.evaluate(inputs, chosen)
        except Exception as error:  # pylint: disable=broad-except  # an absent field, not a crash
            return None, (
                f"the {horizon_in_years}-year re-evaluation of {cls.INPUTS_FILE_NAME} failed "
                f"({type(error).__name__}: {error})"
            )
        band = Range.from_uncertain_value(result.equivalent_annual_cost_in_euro.to_json())
        if band is None:
            return None, f"the {horizon_in_years}-year evaluation produced no equivalent annual cost"
        return band, (
            f"{cls.INPUTS_FILE_NAME} re-evaluated over {horizon_in_years} years under "
            f"'{perspective_id}': equivalent_annual_cost_in_euro / 12"
        )


class SubsidyCatalogue:
    """Where the country subsidy catalogues live and whether this country has one.

    Decision Q24 asks for an Irish catalogue and step 6b builds it; this class is the wiring that
    picks it up the moment it appears, so that the day the file lands no code has to change for
    ``grant_in_euro`` to start being published. Until then the directory holds ``DE.json`` and
    ``AT.json`` only and an Irish calculation configures no catalogue at all -- which is a
    deliberate choice and not an oversight: with no catalogue the engine falls back to a flat
    shim whose support percentage comes from the device entries, and publishing that as an Irish
    grant would be inventing a scheme.
    """

    #: The directory the shipped catalogues live in, relative to the repository root.
    DIRECTORY_NAME: ClassVar[str] = "subsidy_catalog"

    #: The package directory holding it.
    PACKAGE_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    @classmethod
    def directory(cls) -> Path:
        """Return the directory the shipped country catalogues live in."""
        return cls.PACKAGE_ROOT / cls.DIRECTORY_NAME

    @classmethod
    def path_for(cls, country: str, directory: Optional[Path] = None) -> Optional[Path]:
        """Return the catalogue file of one country, or ``None`` when there is none.

        Args:
            country: The ISO country code, e.g. ``"IE"``.
            directory: Where to look; :meth:`directory` when omitted. A test passes a temporary
                directory holding a synthetic catalogue.

        Returns:
            The path of ``<COUNTRY>.json``, or ``None`` when the file does not exist.
        """
        base = cls.directory() if directory is None else directory
        candidate = base / f"{country.upper()}.json"
        return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class CostBlock:
    """The finished ``costs`` block and the fields that could not be filled.

    Args:
        values: The JSON-ready block, field name -> a provenance object, plus the nested
            ``investment_breakdown`` whose two leaves are provenance objects of their own.
        missing: One entry per contract field this run could not produce, already prefixed with
            ``costs.``.
    """

    values: Dict[str, Any]
    missing: Tuple[MissingField, ...]


class CostBuilder:
    """Builds the ``costs`` block of one calculation's ``result.json``.

    Like the KPI builder, it reads only what it is handed, so a unit test can drive it with a
    synthetic pair of engine exports and a two-layer package and never run a simulation.

    Args:
        documents: The run's cost-engine exports, or ``None`` when it produced none.
        layers: The package's insulation layers, for the envelope half of the investment.
        materials: The insulation-material table the envelope prices come from.
        country: The country code whose price column is read and whose subsidy catalogue is
            looked for.
        results_directory: Where the run's exports are, for the second evaluation over the shorter
            horizon; ``None`` disables that evaluation and makes the ten-year field absent.
        subsidy_catalogue_path: The catalogue this run was configured with, or ``None`` when the
            country has none yet.
    """

    #: The prefix every missing cost field carries in ``result.json["missing"]``.
    MISSING_PREFIX: ClassVar[str] = "costs"

    #: The key the device half of the investment sits under.
    DEVICES_KEY: ClassVar[str] = "devices"

    #: The key the envelope half sits under.
    ENVELOPE_KEY: ClassVar[str] = "envelope_material"

    #: Why there is no payback period: it is a difference between this calculation and the same
    #: dwelling without the package, and nothing here has run the second one.
    PAYBACK_REASON: ClassVar[str] = (
        "needs the base calculation as well; a compare entry point is a later step"
    )

    #: Why there is no property-value figure: decision A13 records that no model exists.
    PROPERTY_VALUE_REASON: ClassVar[str] = "no model for the value a renovation adds to a property (A13)"

    #: Why there is no grant until the Irish catalogue is written (decision Q24, step 6b).
    GRANT_REASON_TEMPLATE: ClassVar[str] = (
        "no subsidy catalogue for '{country}' (subsidy_catalog/{country}.json); the engine's flat "
        "shim is not an Irish scheme and is not published as one (Q24, step 6b)"
    )

    def __init__(
        self,
        documents: Optional[CostDocuments],
        layers: EnvelopeLayers,
        materials: InsulationMaterials,
        country: str,
        results_directory: Optional[Path] = None,
        subsidy_catalogue_path: Optional[Path] = None,
    ) -> None:
        """Store the inputs; nothing is read until :meth:`build`."""
        self._documents = documents
        self._layers = layers
        self._materials = materials
        self._country = country
        self._results_directory = results_directory
        self._catalogue = subsidy_catalogue_path
        self._missing: List[MissingField] = []

    def build(self) -> CostBlock:
        """Return the ``costs`` block and the fields that could not be filled.

        Returns:
            The :class:`CostBlock`. Fields appear in the order of :class:`CostField`, so two runs
            of one request produce the same bytes (requirement R10).
        """
        self._missing = []
        values: Dict[str, Any] = {}
        self._investment(values)
        self._annual(values, CostField.ENERGY, CostSources.ENERGY_CATEGORIES, "energy")
        self._annual(
            values, CostField.MAINTENANCE, (CostSources.MAINTENANCE_CATEGORY,), "maintenance"
        )
        self._net_present_value(values)
        self._monthly_over_the_full_horizon(values)
        self._monthly_over_the_short_horizon(values)
        self._grant(values)
        self._absent(CostField.PAYBACK, self.PAYBACK_REASON)
        self._absent(CostField.PROPERTY_VALUE, self.PROPERTY_VALUE_REASON)
        return CostBlock(values=values, missing=tuple(self._missing))

    def _absent(self, field: CostField, reason: str) -> None:
        """Record that one field is absent from the payload, with the reason."""
        self._missing.append(MissingField(field=f"{self.MISSING_PREFIX}.{field.value}", reason=reason))

    def _partial(self, value: Range, source: str) -> Dict[str, Any]:
        """Return one ``PARTIAL`` provenance object; every engine figure is one.

        Args:
            value: The band.
            source: Where it came from.

        Returns:
            The JSON-ready provenance object.
        """
        return ProvenancedValue(value=value, provenance=Provenance.PARTIAL, source=source).to_json()

    def _investment(self, values: Dict[str, Any]) -> None:
        """Fill the investment field and its two-part breakdown.

        The devices come from the engine's year-0 investment entries and the envelope from the
        material table; each half is published on its own as well as in the total, so the seam
        decision Q23 draws stays visible in the payload.

        Args:
            values: The block being assembled.
        """
        devices = (
            self._documents.nominal_total(
                CostSources.PERSPECTIVE_ID,
                CostSources.INVESTMENT_YEAR,
                (CostSources.INVESTMENT_CATEGORY,),
            )
            if self._documents is not None
            else None
        )
        envelope, envelope_note = EnvelopeMaterialCost(
            self._layers, self._materials, self._country
        ).total()
        breakdown: Dict[str, Any] = {}
        if devices is not None:
            breakdown[self.DEVICES_KEY] = self._partial(
                devices,
                f"{CostDocuments.TIMELINE_FILE_NAME}: year {CostSources.INVESTMENT_YEAR} "
                f"{CostSources.INVESTMENT_CATEGORY} entries under '{CostSources.PERSPECTIVE_ID}'; "
                f"{CostSources.PRICE_BASIS_NOTE}",
            )
        if envelope is not None:
            breakdown[self.ENVELOPE_KEY] = self._partial(envelope, envelope_note)
        if devices is None and envelope is None:
            self._absent(
                CostField.INVESTMENT,
                f"no engine investment entries ({self._engine_absence()}) and no envelope material "
                f"cost ({envelope_note})",
            )
        else:
            total = (devices or Range.zero()).plus(envelope or Range.zero())
            parts = []
            if devices is not None:
                parts.append(f"devices from the cost engine ({CostSources.PRICE_BASIS_NOTE})")
            else:
                parts.append(f"no device investment: {self._engine_absence()}")
            if envelope is not None:
                parts.append(f"envelope {envelope_note}")
            else:
                parts.append(f"no envelope material cost: {envelope_note}")
            values[CostField.INVESTMENT.value] = self._partial(total, "; ".join(parts))
        if breakdown:
            values[CostField.INVESTMENT_BREAKDOWN.value] = breakdown

    def _annual(
        self, values: Dict[str, Any], field: CostField, categories: Tuple[str, ...], what: str
    ) -> None:
        """Fill one per-year cost field from the first billed year of the timeline.

        Args:
            values: The block being assembled.
            field: The contract field.
            categories: The timeline categories that make up the figure.
            what: One word naming the figure, for the source sentence.
        """
        total = (
            self._documents.nominal_total(
                CostSources.PERSPECTIVE_ID, CostSources.FIRST_BILLED_YEAR, categories
            )
            if self._documents is not None
            else None
        )
        if total is None:
            self._absent(
                field,
                f"the run's timeline holds no year-{CostSources.FIRST_BILLED_YEAR} {what} entries "
                f"({self._engine_absence()})",
            )
            return
        values[field.value] = self._partial(
            total,
            f"{CostDocuments.TIMELINE_FILE_NAME}: year {CostSources.FIRST_BILLED_YEAR} nominal "
            f"{', '.join(categories)} entries under '{CostSources.PERSPECTIVE_ID}'; "
            f"{CostSources.PRICE_BASIS_NOTE}",
        )

    def _net_present_value(self, values: Dict[str, Any]) -> None:
        """Fill the net present value, stating the horizon it was computed over."""
        band = (
            self._documents.banded_field(
                CostSources.PERSPECTIVE_ID, CostSources.NET_PRESENT_VALUE_FIELD
            )
            if self._documents is not None
            else None
        )
        if band is None:
            self._absent(CostField.NET_PRESENT_VALUE, self._engine_absence())
            return
        values[CostField.NET_PRESENT_VALUE.value] = self._partial(
            band,
            f"{CostDocuments.COSTS_FILE_NAME}: {CostSources.PERSPECTIVE_ID}."
            f"{CostSources.NET_PRESENT_VALUE_FIELD} over {self._horizon_phrase()}; "
            f"{CostSources.PRICE_BASIS_NOTE}",
        )

    def _monthly_over_the_full_horizon(self, values: Dict[str, Any]) -> None:
        """Fill the monthly net cost over the engine's own horizon."""
        band = (
            self._documents.banded_field(
                CostSources.PERSPECTIVE_ID, CostSources.EQUIVALENT_ANNUAL_COST_FIELD
            )
            if self._documents is not None
            else None
        )
        if band is None:
            self._absent(CostField.MONTHLY_TWENTY_YEARS, self._engine_absence())
            return
        values[CostField.MONTHLY_TWENTY_YEARS.value] = self._partial(
            band.scaled(1.0 / CostSources.MONTHS_PER_YEAR),
            f"{CostDocuments.COSTS_FILE_NAME}: {CostSources.PERSPECTIVE_ID}."
            f"{CostSources.EQUIVALENT_ANNUAL_COST_FIELD} / 12 over {self._horizon_phrase()}; "
            f"{CostSources.PRICE_BASIS_NOTE}",
        )

    def _monthly_over_the_short_horizon(self, values: Dict[str, Any]) -> None:
        """Fill the ten-year monthly net cost by re-evaluating the stored inputs."""
        if self._results_directory is None:
            self._absent(
                CostField.MONTHLY_TEN_YEARS,
                f"no result directory to re-evaluate over "
                f"{CostSources.SHORT_HORIZON_IN_YEARS} years",
            )
            return
        band, note = ShortHorizonEvaluation.equivalent_annual_cost(
            self._results_directory, CostSources.PERSPECTIVE_ID, CostSources.SHORT_HORIZON_IN_YEARS
        )
        if band is None:
            self._absent(CostField.MONTHLY_TEN_YEARS, note)
            return
        values[CostField.MONTHLY_TEN_YEARS.value] = self._partial(
            band.scaled(1.0 / CostSources.MONTHS_PER_YEAR),
            f"{note}; {CostSources.PRICE_BASIS_NOTE}",
        )

    def _grant(self, values: Dict[str, Any]) -> None:
        """Fill the grant from the subsidy solver, or record why there is none.

        The figure is published only when this calculation was configured with a real country
        catalogue. Without one the engine still books a flat-shim support in its ``greenfield_net``
        view, and that number is a percentage of device prices rather than a scheme anybody
        administers, so it is not published as a grant.

        Args:
            values: The block being assembled.
        """
        if self._catalogue is None:
            self._absent(CostField.GRANT, self.GRANT_REASON_TEMPLATE.format(country=self._country))
            return
        band = (
            self._documents.category_npv(
                CostSources.SUBSIDY_PERSPECTIVE_ID, CostSources.SUBSIDY_CATEGORY
            )
            if self._documents is not None
            else None
        )
        if band is None:
            self._absent(
                CostField.GRANT,
                f"the subsidy solver awarded nothing under '{CostSources.SUBSIDY_PERSPECTIVE_ID}' "
                f"from {self._catalogue.name}",
            )
            return
        magnitude = Range(low=-band.high, best_estimate=-band.best_estimate, high=-band.low)
        values[CostField.GRANT.value] = self._partial(
            magnitude,
            f"{CostDocuments.COSTS_FILE_NAME}: {CostSources.SUBSIDY_PERSPECTIVE_ID}."
            f"npv_by_category.{CostSources.SUBSIDY_CATEGORY}, sign-flipped to the support received, "
            f"from the catalogue {self._catalogue}",
        )

    def _engine_absence(self) -> str:
        """Return the sentence saying why an engine figure is missing.

        Returns:
            One of two sentences: the run produced no cost exports at all, or it produced them
            without the perspective this module reads.
        """
        if self._documents is None:
            return (
                f"the run wrote no {CostDocuments.COSTS_FILE_NAME}; "
                "COMPUTE_LIFECYCLE_COSTS produced no result"
            )
        available = ", ".join(self._documents.perspective_ids()) or "none"
        return (
            f"{CostDocuments.COSTS_FILE_NAME} carries no usable "
            f"'{CostSources.PERSPECTIVE_ID}' figure (perspectives present: {available})"
        )

    def _horizon_phrase(self) -> str:
        """Return the horizon a perspective was evaluated over, as a phrase for a source string."""
        horizon = (
            self._documents.horizon_in_years(CostSources.PERSPECTIVE_ID)
            if self._documents is not None
            else None
        )
        return "an unstated horizon" if horizon is None else f"{horizon} years"


class CostSchema:
    """The published shape of the ``costs`` block, without running anything.

    The counterpart of :class:`hisim.renovisor.kpis.KpiSchema` for the money half of the payload,
    and the same contract: one row per :class:`CostField`, each saying where the figure comes from
    and what provenance it carries, so the translation map can show the frontend what will arrive
    before the first payload exists. The three fields that are always absent carry their reason in
    place of a provenance, because that is the honest answer to "what will I get here".
    """

    @classmethod
    def rows(cls) -> Tuple[PayloadFieldRow, ...]:
        """Return one row per field of the ``costs`` block, in payload order."""
        engine = f"lifecycle cost engine under '{CostSources.PERSPECTIVE_ID}'"
        return (
            PayloadFieldRow(
                CostField.INVESTMENT.value,
                f"{engine}: year-0 {CostSources.INVESTMENT_CATEGORY} entries, plus the envelope "
                "material cost from insulation_materials.json (Q9)",
                f"{Provenance.PARTIAL.value}: device prices are AI estimates, the envelope is "
                "material only",
            ),
            PayloadFieldRow(
                CostField.ENERGY.value,
                f"{engine}: year-1 nominal {', '.join(CostSources.ENERGY_CATEGORIES)}",
                f"{Provenance.PARTIAL.value}: Irish energy prices are AI estimates",
            ),
            PayloadFieldRow(
                CostField.MAINTENANCE.value,
                f"{engine}: year-1 nominal {CostSources.MAINTENANCE_CATEGORY}",
                f"{Provenance.PARTIAL.value}: maintenance rates are AI estimates",
            ),
            PayloadFieldRow(
                CostField.NET_PRESENT_VALUE.value,
                f"{engine}: {CostSources.NET_PRESENT_VALUE_FIELD} over the engine's horizon",
                Provenance.PARTIAL.value,
            ),
            PayloadFieldRow(
                CostField.MONTHLY_TWENTY_YEARS.value,
                f"{engine}: {CostSources.EQUIVALENT_ANNUAL_COST_FIELD} / 12",
                Provenance.PARTIAL.value,
            ),
            PayloadFieldRow(
                CostField.MONTHLY_TEN_YEARS.value,
                f"{CostDocuments.COSTS_FILE_NAME}'s stored inputs re-evaluated over "
                f"{CostSources.SHORT_HORIZON_IN_YEARS} years, / 12",
                Provenance.PARTIAL.value,
            ),
            PayloadFieldRow(
                CostField.GRANT.value,
                f"the subsidy solver under '{CostSources.SUBSIDY_PERSPECTIVE_ID}', when a country "
                "catalogue exists",
                "absent until subsidy_catalog/<COUNTRY>.json exists (Q24)",
            ),
            PayloadFieldRow(
                CostField.PAYBACK.value,
                "the difference against the same dwelling without the package",
                "absent: needs the base calculation (a compare entry point)",
            ),
            PayloadFieldRow(
                CostField.PROPERTY_VALUE.value,
                "no model",
                "absent (A13)",
            ),
            PayloadFieldRow(
                CostField.INVESTMENT_BREAKDOWN.value,
                "the two halves of the investment, separately: devices and envelope_material",
                Provenance.PARTIAL.value,
            ),
        )
