"""The ``kpis`` block of ``result.json``: three simulated figures, one derived, and six constants.

The contract asks for thirteen key performance indicators; HiSim computes three of them, derives a
fourth from the material table, and has no model at all behind the rest (challenge C22). Decision
A12 settled what to do about that: the constants are published with ``provenance: MOCKED`` and are
the *contract's own* examples rather than numbers invented here, so the frontend that asked for the
field gets the value it documented, clearly marked as not a simulation result. Decision R8 settled
the remaining case: a field with neither a computation nor a mocked constant is absent from the
payload and is listed under ``result.json["missing"]`` with the reason.

The three simulated figures and where they come from (decision Q22 fixed the boundaries)::

    energy_demand_in_kilowatt_hour_per_year  <- "Purchased energy consumption for simulated period"
    emissions_in_kg_co2_per_year             <- "Total CO2 emissions for simulated period"
    self_sufficiency_in_percent              <- "Self-sufficiency rate of electricity"

Those names are HiSim internals that a later refactor may rename, which is exactly the drift the
bindings table is guarded against, so they are gathered in :class:`KpiSources` and
``tests/renovisor/test_kpis.py`` greps them out of ``kpi_preparation.py``: a rename becomes a
failing build rather than a field that quietly disappears from every result.

Two properties of the period matter for every annual figure (requirement A14). A caller may run
one January day, so an annual figure from such a run is the simulated figure divided by the share
of a year it covered -- an extrapolation, published as ``PARTIAL`` with the period stated beside
it. A *rate* is not extrapolated: forty per cent self-sufficiency over a day is forty per cent
over that day, and dividing it by the period fraction would produce a nonsense above a hundred.
"""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.layers import EnvelopeLayers
from hisim.renovisor.provenance import MissingField, Period, ProvenancedValue
from hisim.renovisor.vocabulary import Provenance


class KpiField(str, Enum):
    """The names the payload's ``kpis`` block uses, in HiSim spelling (decision C3).

    They differ from the vendored contract's current spelling -- ``energy_demand_kwh`` there,
    ``energy_demand_in_kilowatt_hour_per_year`` here -- because C3 made HiSim's unit-spelling
    convention the contract's, and the contract PR of step 7 adopts these names. Writing them as
    an enum rather than as string literals means the payload builder, the missing-field list and
    the translation map all address the same field by the same constant.
    """

    ENERGY_DEMAND = "energy_demand_in_kilowatt_hour_per_year"
    EMISSIONS = "emissions_in_kg_co2_per_year"
    SELF_SUFFICIENCY = "self_sufficiency_in_percent"
    EMBODIED_CO2 = "embodied_co2_in_kg"
    ENERGY_LABEL = "energy_label"
    DISRUPTION_DAYS = "disruption_days_by_level"
    INDOOR_AIR_QUALITY = "indoor_air_quality"
    THERMAL_INSULATION_EFFECT = "thermal_insulation_effect"
    SUMMER_HEAT_PROTECTION = "summer_heat_protection"
    COMFORT = "comfort"


class KpiDocument:
    """The run's ``all_kpis.json``, addressed by KPI name rather than by key.

    ``WRITE_KPIS_TO_JSON`` writes a three-level document -- building object, then KPI group, then
    one entry per KPI -- whose innermost keys are *qualified with the source component* as soon as
    two components of one building report a KPI of the same name. The entries themselves always
    carry their plain ``name``, so that is what this class looks values up by, exactly as
    ``kpi_preparation.py`` does internally when it sums the meters.

    A RenoVisor calculation is one dwelling, so the first building object that carries a name is
    the answer; the district case the collection also serves does not arise here.

    Args:
        document: The parsed ``all_kpis.json``.
    """

    #: The file ``WRITE_KPIS_TO_JSON`` writes into the simulation's result directory.
    FILE_NAME: ClassVar[str] = "all_kpis.json"

    #: The key every entry carries its plain, unqualified KPI name under.
    NAME_KEY: ClassVar[str] = "name"

    #: The key every entry carries its value under.
    VALUE_KEY: ClassVar[str] = "value"

    def __init__(self, document: Mapping[str, Any]) -> None:
        """Index every entry of the document by its plain KPI name."""
        self._by_name: Dict[str, Any] = {}
        for groups in document.values():
            if not isinstance(groups, Mapping):
                continue
            for entries in groups.values():
                if not isinstance(entries, Mapping):
                    continue
                for entry in entries.values():
                    if isinstance(entry, Mapping) and self.NAME_KEY in entry:
                        self._by_name.setdefault(str(entry[self.NAME_KEY]), entry.get(self.VALUE_KEY))

    @classmethod
    def load(cls, results_directory: Path) -> Optional["KpiDocument"]:
        """Read ``all_kpis.json`` out of a run's result directory.

        Args:
            results_directory: The simulation's own output directory.

        Returns:
            The document, or ``None`` when the run wrote none -- which happens when
            ``WRITE_KPIS_TO_JSON`` was not among the options, and makes every KPI field absent
            rather than zero.
        """
        path = results_directory / cls.FILE_NAME
        if not path.is_file():
            return None
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def number(self, name: str) -> Optional[float]:
        """Return one KPI's value as a number, or ``None`` when it is absent or not numeric.

        Args:
            name: The KPI's plain name, e.g. ``"Total CO2 emissions for simulated period"``.

        Returns:
            The value, or ``None``.
        """
        value = self._by_name.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def has(self, name: str) -> bool:
        """Return whether the document carries a KPI of that name."""
        return name in self._by_name

    def names(self) -> Tuple[str, ...]:
        """Return every KPI name the document carries, sorted."""
        return tuple(sorted(self._by_name))


class KpiSources:
    """Which HiSim KPI each contract field reads, and what its boundary is.

    One table, so that the mapping is a thing a test can check rather than a set of string
    literals scattered through a builder. Every name here has to appear verbatim in
    ``hisim/postprocessing/kpi_computation/kpi_preparation.py``; the unit test greps the source
    file for each of them, which is the same drift guard the bindings table carries (challenge
    C22).
    """

    #: Delivered energy, summed over carriers by HiSim itself: grid electricity plus grid gas plus
    #: every other heating fuel a meter reported. The boundary is "energy bought" (decision Q22),
    #: which is what this KPI is: ``kpi_preparation.py`` builds it from the three meter KPIs of
    #: :attr:`ENERGY_DEMAND_CARRIER_NAMES`, summing each tag over every meter of that tag, so a
    #: dwelling with an oil meter and a pellet meter is counted once per meter and not once per
    #: fuel. Reading the aggregate rather than re-summing the three is deliberate: the summation
    #: has meter-collision rules this module would otherwise have to copy.
    ENERGY_DEMAND_NAME: ClassVar[str] = "Purchased energy consumption for simulated period"

    #: The three per-carrier meter KPIs the aggregate above is built from, named so the payload's
    #: ``source`` can state the boundary and so the drift test covers them too.
    ENERGY_DEMAND_CARRIER_NAMES: ClassVar[Tuple[str, ...]] = (
        "Total energy from grid",
        "Total gas demand from grid",
        "Total energy consumption",
    )

    #: There is deliberately no entry here for any KPI computed from the legacy per-year tables
    #: of ``hisim/components/configuration.py`` -- no cost KPI and, above all, not
    #: ``"Total CO2 emissions for simulated period"``. Those tables carry reviewed rows for
    #: Germany and Austria and sentinel placeholders (``-1e9``) for every other country HiSim
    #: registers, Ireland included, so reading one of them would publish either the wrong
    #: country's grid or visible nonsense. The two names below are energy and a rate, which no
    #: price or emission factor touches; the operational CO2 comes from the lifecycle cost
    #: engine instead (:class:`LifecycleCo2`), which prices and weighs the run against
    #: ``energy_prices_<country>.json`` for the country the economic parameters name.

    #: The share of the dwelling's electricity that came from its own generation, in per cent
    #: (decision A10: a number, not a grade). A rate, so it is never scaled to a year.
    SELF_SUFFICIENCY_NAME: ClassVar[str] = "Self-sufficiency rate of electricity"

    @classmethod
    def annual_names(cls) -> Dict[KpiField, str]:
        """Return the fields that are annual totals, and the KPI each one reads.

        Returns:
            Field -> HiSim KPI name. A value read through this table is divided by the period's
            share of a year and is ``PARTIAL`` whenever that share is not one.
        """
        return {KpiField.ENERGY_DEMAND: cls.ENERGY_DEMAND_NAME}

    @classmethod
    def rate_names(cls) -> Dict[KpiField, str]:
        """Return the fields that are rates, and the KPI each one reads.

        Returns:
            Field -> HiSim KPI name. A value read through this table is published as it stands.
        """
        return {KpiField.SELF_SUFFICIENCY: cls.SELF_SUFFICIENCY_NAME}

    @classmethod
    def all_names(cls) -> Tuple[str, ...]:
        """Return every HiSim KPI name this module depends on, sorted.

        Returns:
            The names the drift test looks for in ``kpi_preparation.py``.
        """
        names = set(cls.annual_names().values()) | set(cls.rate_names().values())
        names |= set(cls.ENERGY_DEMAND_CARRIER_NAMES)
        return tuple(sorted(names))


class ContractExamples:
    """The contract schema's own ``examples``, read at build time rather than typed out here.

    Decision A12 allows a mocked KPI; it does not allow an invented one. The difference is where
    the constant comes from: a number the frontend wrote into ``openapi.yaml`` as the example of
    its own field is the frontend's number, and a number typed into HiSim would be HiSim inventing
    a comfort grade. So every mocked value is read out of the vendored contract at the moment the
    payload is built, and its ``source`` names the schema path it was read from::

        ContractExamples.of("indoor_air_quality")        # ('high', 'openapi.yaml Kpis…')

    A field whose schema carries no example yields ``None`` with a source saying so, which makes
    it a missing field rather than a guess.
    """

    #: The document path of the KPI schema's property map inside ``openapi.yaml``.
    SCHEMA_PATH: ClassVar[Tuple[str, ...]] = ("components", "schemas", "Kpis", "properties")

    #: The key an OpenAPI 3.1 schema carries its examples list under.
    EXAMPLES_KEY: ClassVar[str] = "examples"

    #: The key a nested object schema carries its own properties under.
    PROPERTIES_KEY: ClassVar[str] = "properties"

    #: The decision that allows a mocked value at all, quoted in every mocked source string.
    DECISION: ClassVar[str] = "A12"

    @classmethod
    def schema(cls, *names: str) -> Mapping[str, Any]:
        """Return the schema of one KPI property, or of a property nested inside one.

        Args:
            *names: The property names from ``Kpis`` downwards, e.g. ``("comfort", "heating")``.

        Returns:
            The schema object, or an empty mapping when the contract has no such property.
        """
        node: Any = ContractFiles.openapi()
        for key in cls.SCHEMA_PATH:
            node = node.get(key) if isinstance(node, Mapping) else None
        for index, name in enumerate(names):
            if not isinstance(node, Mapping):
                return {}
            if index:
                node = node.get(cls.PROPERTIES_KEY)
                node = node.get(name) if isinstance(node, Mapping) else None
            else:
                node = node.get(name)
        return node if isinstance(node, Mapping) else {}

    @classmethod
    def of(cls, *names: str) -> Tuple[Any, str]:
        """Return the first example of one KPI property and the sentence naming where it came from.

        Args:
            *names: The property names from ``Kpis`` downwards.

        Returns:
            ``(value, source)``. ``value`` is ``None`` when the property has no examples, and the
            source then says so instead of naming an example that does not exist.
        """
        path = f"Kpis.{'.'.join(names)}"
        examples = cls.schema(*names).get(cls.EXAMPLES_KEY)
        if not isinstance(examples, list) or not examples:
            return None, f"{ContractFiles.OPENAPI_FILENAME} {path} carries no examples; {cls.DECISION}"
        return examples[0], f"{ContractFiles.OPENAPI_FILENAME} {path} examples[0]; {cls.DECISION}"


@dataclass(frozen=True)
class KpiBlock:
    """The finished ``kpis`` block and the fields that could not be filled.

    Args:
        values: The JSON-ready block, field name -> a provenance object, or -> a map of them for
            the one nested field (``comfort``).
        missing: One entry per contract field this run could not produce, already prefixed with
            ``kpis.``.
    """

    values: Dict[str, Any]
    missing: Tuple[MissingField, ...]


class LifecycleCo2:
    """The cost engine's parallel CO2 accounting, read out of one run's result directory.

    The engine runs a mass balance alongside the money: operational carbon per carrier and per
    year from the run's own energy flows, with the emission factors of the country the
    ``EconomicParameters`` named. That is why the payload's operational CO2 comes from here and
    not from the legacy KPI path, whose factor table has rows for Germany and Austria only
    (``hisim/components/configuration.py``) and which therefore cannot answer the question for
    an Irish dwelling at all.

    Args:
        block: The ``lifecycle_co2`` object of the perspective the payload reads.
    """

    #: The engine's primary export, in the run's result directory.
    FILE_NAME: ClassVar[str] = "lifecycle_costs.json"

    #: The perspective the payload reads: the RenoVisor default of step 10 §1 -- the existing
    #: building, subsidies applied where a catalogue says so, cash financing. It is the same
    #: perspective ``economics_result.json`` is written under, so the KPI half and the money half
    #: of a calculation cannot describe different worlds.
    PERSPECTIVE_ID: ClassVar[str] = "brownfield_net"

    #: The keys inside it.
    BLOCK: ClassVar[str] = "lifecycle_co2"
    OPERATIONAL_BY_YEAR: ClassVar[str] = "operational_co2_by_year_in_kg"
    OPERATIONAL_BY_CARRIER: ClassVar[str] = "operational_co2_by_carrier_in_kg"
    FACTORS: ClassVar[str] = "emission_factor_by_carrier_in_kg_per_kwh"

    #: The index of the first simulated year in the by-year array; index 0 is the investment
    #: year and is always zero.
    FIRST_YEAR: ClassVar[int] = 1

    def __init__(self, block: Mapping[str, Any]) -> None:
        """Store the parsed ``lifecycle_co2`` object."""
        self._block = dict(block)

    @classmethod
    def load(cls, results_directory: Path) -> Optional["LifecycleCo2"]:
        """Read the engine's CO2 accounting out of one run's result directory.

        Args:
            results_directory: The simulation's own output directory.

        Returns:
            The accounting, or ``None`` when the run wrote no lifecycle result -- which is what
            a run without ``COMPUTE_LIFECYCLE_COSTS`` leaves behind, and makes the operational
            CO2 absent with the reason rather than fall back to the German factors.
        """
        path = results_directory / cls.FILE_NAME
        if not path.is_file():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        perspective = cls._perspective(document)
        if perspective is None:
            return None
        block = perspective.get(cls.BLOCK)
        return cls(block) if isinstance(block, Mapping) else None

    @classmethod
    def _perspective(cls, document: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        """The perspective the operational CO2 is read from: the default one, or any of them.

        The engine evaluates the greenfield perspectives for a run with no existing-asset
        register and the brownfield ones for a run that has one, so no single id is present in
        every document. Falling back to the first perspective the document carries is safe for
        *this* figure and only for this figure: operational CO2 per year is the emission factor
        of a carrier times the kilowatt-hours that crossed the meter, and every perspective of
        one run prices the same meter, so the by-year array is identical in all of them. Nothing
        monetary is read here, and the embodied half of the payload's carbon comes from the
        insulation layers rather than from this file.

        Args:
            document: The parsed ``lifecycle_costs.json``.

        Returns:
            The perspective object, or ``None`` when the document carries none at all.
        """
        preferred = document.get(cls.PERSPECTIVE_ID)
        if isinstance(preferred, Mapping):
            return preferred
        for value in document.values():
            if isinstance(value, Mapping) and isinstance(value.get(cls.BLOCK), Mapping):
                return value
        return None

    def annual_operational_in_kg(self) -> Optional[float]:
        """Return the operational CO2 of one year, in kilograms.

        Returns:
            The first simulated year of the engine's by-year array, which the engine has
            already extrapolated from the simulated period; ``None`` when the array is absent
            or too short.
        """
        series = self._block.get(self.OPERATIONAL_BY_YEAR)
        if not isinstance(series, list) or len(series) <= self.FIRST_YEAR:
            return None
        value = series[self.FIRST_YEAR]
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def carriers(self) -> Dict[str, float]:
        """Return the operational CO2 over the whole horizon, per energy carrier."""
        raw = self._block.get(self.OPERATIONAL_BY_CARRIER)
        if not isinstance(raw, Mapping):
            return {}
        return {str(name): float(value) for name, value in raw.items()}

    def factors(self) -> Dict[str, float]:
        """Return the emission factor the engine used for each carrier, in kg CO2 per kWh."""
        raw = self._block.get(self.FACTORS)
        if not isinstance(raw, Mapping):
            return {}
        return {str(name): float(value) for name, value in raw.items()}

    def describe_carriers(self) -> str:
        """Return the carriers and their factors as one phrase for the payload's ``source``.

        Returns:
            E.g. ``"carriers: ELECTRICITY at 0.28 kg/kWh"`` -- the multiplication behind the
            mass, so a reader can check it rather than take it.
        """
        factors = self.factors()
        names = sorted(self.carriers())
        if not names:
            return "the engine names no carrier"
        described = ", ".join(
            f"{name} at {factors[name]:g} kg/kWh" if name in factors else name for name in names
        )
        return f"carriers: {described}"


class KpiBuilder:
    """Builds the ``kpis`` block of one calculation's ``result.json``.

    The builder reads nothing itself beyond what it is handed: the run's KPI document, the period
    it covered, the insulation layers the package added and the material table. That is what lets
    the unit tests drive it with a synthetic KPI document and a two-layer package instead of a
    simulation.

    Args:
        document: The run's ``all_kpis.json``, or ``None`` when it wrote none.
        period: The simulated period, which decides scaling and provenance.
        layers: The package's insulation layers, for the embodied-carbon figure. Each carries
            the material's own CO2 footprint, because the request sends the material's
            properties rather than its id (rule 5 of the contract).
        lifecycle_co2: The cost engine's parallel CO2 accounting, which is where the
            operational carbon comes from; ``None`` when the run wrote none, in which case the
            field is absent with the reason.
        country: The country the dwelling is in, which the economic parameters named and whose
            emission factors the engine therefore used. It appears in the field's ``source``.
    """

    #: The prefix every missing KPI field carries in ``result.json["missing"]``.
    MISSING_PREFIX: ClassVar[str] = "kpis"

    #: Why there is no energy label. HiSim implements no BER or DEAP procedure, and a letter
    #: derived from a kilowatt-hour figure by some other rule would be a rating nobody issued.
    ENERGY_LABEL_REASON: ClassVar[str] = (
        f"no BER/DEAP procedure in HiSim; no letter is invented ({ContractExamples.DECISION})"
    )

    #: Why there is no embodied-carbon figure when the package adds no insulation. Decision R8
    #: forbids the plausible zero, and a building that was not insulated has no embodied carbon
    #: to report rather than none of it.
    EMBODIED_CO2_ABSENT_REASON: ClassVar[str] = "the package adds no insulation layer (R8)"

    #: The two leaves of the contract's nested ``comfort`` object.
    COMFORT_LEAVES: ClassVar[Tuple[str, ...]] = ("heating", "cooling")

    #: The mocked scalar fields, each read from its own property's examples.
    MOCKED_SCALAR_FIELDS: ClassVar[Tuple[KpiField, ...]] = (
        KpiField.DISRUPTION_DAYS,
        KpiField.INDOOR_AIR_QUALITY,
        KpiField.THERMAL_INSULATION_EFFECT,
        KpiField.SUMMER_HEAT_PROTECTION,
    )

    def __init__(
        self,
        document: Optional[KpiDocument],
        period: Period,
        layers: EnvelopeLayers,
        lifecycle_co2: Optional["LifecycleCo2"],
        country: str,
    ) -> None:
        """Store the inputs; nothing is read until :meth:`build`."""
        self._document = document
        self._period = period
        self._layers = layers
        self._lifecycle_co2 = lifecycle_co2
        self._country = country
        self._missing: List[MissingField] = []

    def build(self) -> KpiBlock:
        """Return the ``kpis`` block and the fields that could not be filled.

        Returns:
            The :class:`KpiBlock`. Fields appear in the order of :class:`KpiField`, which is the
            order the contract's schema lists them in, so two runs of one request produce the same
            bytes (requirement R10).
        """
        self._missing = []
        values: Dict[str, Any] = {}
        for field, name in KpiSources.annual_names().items():
            self._put(values, field, self._annual(field, name))
        self._put(values, KpiField.EMISSIONS, self._emissions())
        for field, name in KpiSources.rate_names().items():
            self._put(values, field, self._rate(name))
        self._put(values, KpiField.EMBODIED_CO2, self._embodied_co2())
        values[KpiField.ENERGY_LABEL.value] = ProvenancedValue(
            value=None, provenance=Provenance.MOCKED, source=self.ENERGY_LABEL_REASON
        ).to_json()
        for field in self.MOCKED_SCALAR_FIELDS:
            self._put(values, field, self._mocked(field.value))
        self._put(values, KpiField.COMFORT, self._comfort())
        return KpiBlock(values=values, missing=tuple(self._missing))

    def _put(self, values: Dict[str, Any], field: KpiField, value: Optional[Dict[str, Any]]) -> None:
        """Write one field into the block, or leave it out when the builder produced nothing.

        A ``None`` here means the field's own builder already recorded why it is absent, so this
        method only has to respect that decision rather than repeat it.

        Args:
            values: The block being assembled.
            field: The field.
            value: Its JSON-ready value, or ``None`` when it is absent.
        """
        if value is not None:
            values[field.value] = value

    def _absent(self, field: KpiField, reason: str) -> None:
        """Record that one field is absent from the payload, with the reason.

        Args:
            field: The field.
            reason: One sentence saying what is missing.
        """
        self._missing.append(MissingField(field=f"{self.MISSING_PREFIX}.{field.value}", reason=reason))

    def _annual(self, field: KpiField, name: str) -> Optional[Dict[str, Any]]:
        """Return one annual total, scaled from the simulated period to a year.

        Args:
            field: The contract field, for the absence record.
            name: The HiSim KPI to read.

        Returns:
            The provenance object, or ``None`` when the KPI is absent from the run.
        """
        value = self._document.number(name) if self._document is not None else None
        if value is None:
            self._absent(field, f"the run's {KpiDocument.FILE_NAME} has no KPI '{name}'")
            return None
        fraction = self._period.fraction_of_year
        if fraction <= 0.0:
            self._absent(field, "the simulated period is empty, so no annual figure can be derived from it")
            return None
        reviewed = True
        source = f"{KpiDocument.FILE_NAME}: '{name}'"
        if field is KpiField.ENERGY_DEMAND:
            source += (
                "; delivered energy bought over every carrier (Q22), summed by HiSim from "
                + ", ".join(f"'{carrier}'" for carrier in KpiSources.ENERGY_DEMAND_CARRIER_NAMES)
            )
        if not self._period.is_full_year():
            source += f"; scaled to a year by 1 / {fraction:.6g}"
            reviewed = False
        return ProvenancedValue(
            value=value / fraction,
            provenance=Provenance.SIMULATED if reviewed else Provenance.PARTIAL,
            source=source,
            period=self._period,
        ).to_json()

    def _rate(self, name: str) -> Optional[Dict[str, Any]]:
        """Return one rate, published as the run computed it.

        Args:
            name: The HiSim KPI to read.

        Returns:
            The provenance object, or ``None`` when the KPI is absent from the run.
        """
        value = self._document.number(name) if self._document is not None else None
        if value is None:
            self._absent(
                KpiField.SELF_SUFFICIENCY, f"the run's {KpiDocument.FILE_NAME} has no KPI '{name}'"
            )
            return None
        return ProvenancedValue(
            value=value,
            provenance=Provenance.SIMULATED,
            source=f"{KpiDocument.FILE_NAME}: '{name}'; a rate, so it is not scaled to a year",
            period=self._period,
        ).to_json()

    def _emissions(self) -> Optional[Dict[str, Any]]:
        """Return the operational CO2 of one year, from the lifecycle cost engine.

        Decision Q22 defines the figure as operational carbon per year, and the engine is the
        only place in HiSim that computes it with the dwelling's own country factors: it reads
        ``energy_prices_<country>.json`` through the ``EconomicParameters`` the run attached,
        while the legacy KPI path reads a table with rows for Germany and Austria only. The
        engine has already extrapolated a part-year run to a year, which is exactly what makes
        such a run ``PARTIAL``: the number is a year of the weather and the occupancy those few
        days happened to contain.

        Returns:
            The provenance object, or ``None`` when the run wrote no lifecycle result.
        """
        if self._lifecycle_co2 is None:
            self._absent(
                KpiField.EMISSIONS,
                f"the run wrote no {LifecycleCo2.FILE_NAME}, so the cost engine's operational "
                "CO2 accounting is not there; the legacy KPI is not read, because its emission "
                "factors have rows for DE and AT only",
            )
            return None
        value = self._lifecycle_co2.annual_operational_in_kg()
        if value is None:
            self._absent(
                KpiField.EMISSIONS,
                f"{LifecycleCo2.FILE_NAME} carries no year-1 operational CO2 under "
                f"'{LifecycleCo2.PERSPECTIVE_ID}'",
            )
            return None
        source = (
            f"{LifecycleCo2.FILE_NAME}: {LifecycleCo2.PERSPECTIVE_ID}."
            f"{LifecycleCo2.BLOCK}.{LifecycleCo2.OPERATIONAL_BY_YEAR}[{LifecycleCo2.FIRST_YEAR}], "
            f"operational CO2 per year (Q22) with the '{self._country}' emission factors the "
            f"cost engine was given; {self._lifecycle_co2.describe_carriers()}"
        )
        if not self._period.is_full_year():
            source += (
                f"; the engine extrapolated it from the simulated period "
                f"({self._period.fraction_of_year:.6g} of a year)"
            )
        return ProvenancedValue(
            value=value,
            provenance=Provenance.SIMULATED if self._period.is_full_year() else Provenance.PARTIAL,
            source=source,
            period=self._period,
        ).to_json()

    def _embodied_co2(self) -> Optional[Dict[str, Any]]:
        """Return the embodied carbon of the package's insulation layers.

        Decision Q22 made embodied carbon a field of its own rather than a term inside the
        operational figure, and the request's own material objects are where it comes from:
        ``co2_footprint_a1_a3_c3_c4_kg_m2`` is an EN 15804+A2 figure per square metre at the
        thickness for U = 0.3, so the product is the footprint times the element's area. A
        material whose request object carries no footprint, or a layer whose element area
        neither the realized record nor the request states, makes the whole field absent -- a
        partial sum over some of a building's insulation would read as the whole building's
        carbon.

        Returns:
            The provenance object, or ``None`` when the field is absent.
        """
        if self._layers.is_empty():
            self._absent(KpiField.EMBODIED_CO2, self.EMBODIED_CO2_ABSENT_REASON)
            return None
        unresolved = self._layers.unresolved()
        if unresolved:
            self._absent(
                KpiField.EMBODIED_CO2,
                "no element area for " + ", ".join(layer.describe() for layer in unresolved),
            )
            return None
        without = self._layers.without_footprint()
        if without:
            self._absent(
                KpiField.EMBODIED_CO2,
                "the request states no co2_footprint_a1_a3_c3_c4_kg_m2 for "
                + ", ".join(layer.describe() for layer in without),
            )
            return None
        total = self._layers.total_embodied_co2_in_kg()
        if total is None:
            self._absent(KpiField.EMBODIED_CO2, "the package's embodied carbon is incomplete")
            return None
        return ProvenancedValue(
            value=total,
            provenance=Provenance.SIMULATED,
            source=(
                "the request's own material properties, co2_footprint_a1_a3_c3_c4_kg_m2 x element "
                "area (Q22): " + self._layers.describe_terms()
            ),
        ).to_json()

    def _mocked(self, *names: str) -> Optional[Dict[str, Any]]:
        """Return one mocked field, valued with the contract schema's own example.

        Args:
            *names: The property names from ``Kpis`` downwards.

        Returns:
            The provenance object, or ``None`` when the schema carries no example, in which case
            the field is recorded as absent.
        """
        value, source = ContractExamples.of(*names)
        if value is None:
            self._absent(KpiField(names[0]), source)
            return None
        return ProvenancedValue(value=value, provenance=Provenance.MOCKED, source=source).to_json()

    def _comfort(self) -> Optional[Dict[str, Any]]:
        """Return the nested ``comfort`` object, each of whose two leaves is a mocked value.

        Returns:
            ``{"heating": {...}, "cooling": {...}}``, or ``None`` when neither leaf has an example.
        """
        leaves: Dict[str, Any] = {}
        for leaf in self.COMFORT_LEAVES:
            value, source = ContractExamples.of(KpiField.COMFORT.value, leaf)
            if value is None:
                self._missing.append(
                    MissingField(
                        field=f"{self.MISSING_PREFIX}.{KpiField.COMFORT.value}.{leaf}", reason=source
                    )
                )
                continue
            leaves[leaf] = ProvenancedValue(
                value=value, provenance=Provenance.MOCKED, source=source
            ).to_json()
        return leaves or None


@dataclass(frozen=True)
class PayloadFieldRow:
    """One field ``result.json`` can carry: where its number comes from and what it will be.

    Two readers need this before the first real payload exists. The translation map is generated
    offline, with no simulation and therefore no ``result.json`` to show, and the capability
    document's ``results`` section is the same statement served to the frontend over
    ``GET /measures``: every field the payload can carry, the source its number is read from, and
    whether that number will be a simulation result, a constant, a partly-estimated figure, or
    nothing at all. Both are generated from these rows rather than from a second hand-kept list,
    so a field added to the payload without a row here is a failing build.

    A row says one of two things. Either the field is published, and then it carries the
    provenance it is published with and the conditions that change it; or it is never published,
    and then it carries the sentence ``result.json["missing"]`` states instead. A field that is
    published *sometimes* carries both: its provenance, and the reason and the condition of its
    absence (embodied carbon is the one such field today).

    Args:
        block: Which block of ``result.json`` the field sits in -- ``kpis`` or ``costs``, the
            same prefix its ``missing`` entry carries.
        field: The field's name inside that block.
        source: Where its value comes from, in one phrase; empty for a field that is never
            published and therefore has no source to name.
        provenance: The provenance it is published with, or ``None`` when no run fills it in.
        conditions: The circumstances that change the published provenance, one sentence each.
        reason: The sentence ``result.json["missing"]`` carries when the field is absent, or
            ``None`` when it never is.
        when: When the field is absent, in one phrase, or ``None`` when it never is.
    """

    #: The word the ``provenance`` key carries for a field no run publishes.
    ABSENT: ClassVar[str] = "absent"

    #: What joins the provenance and its conditions in :meth:`describe`.
    DESCRIPTION_SEPARATOR: ClassVar[str] = "; "

    block: str
    field: str
    source: str
    provenance: Optional[Provenance] = None
    conditions: Tuple[str, ...] = ()
    reason: Optional[str] = None
    when: Optional[str] = None

    def path(self) -> str:
        """Return the field's dotted path in the payload, e.g. ``kpis.energy_label``."""
        return f"{self.block}.{self.field}"

    def published(self) -> str:
        """Return the provenance word: one of the three, or :attr:`ABSENT`."""
        return self.provenance.value if self.provenance is not None else self.ABSENT

    def describe(self) -> str:
        """Return the provenance with its conditions as one phrase, for a page a person reads.

        Returns:
            The provenance word, then every condition and, when the field can be absent, the
            circumstance it is absent under, joined by :attr:`DESCRIPTION_SEPARATOR`.
        """
        parts = [self.published(), *self.conditions]
        if self.when is not None:
            parts.append(self.when)
        return self.DESCRIPTION_SEPARATOR.join(parts)

    def to_json(self) -> Dict[str, Any]:
        """Return one entry of the capability document's ``results`` section.

        Returns:
            ``{"field", "provenance"}`` always, plus ``"source"``, ``"conditions"``, ``"reason"``
            and ``"when"`` where this row has them. The shape is the one
            ``measure-capabilities.results-extension.yaml`` declares.
        """
        entry: Dict[str, Any] = {"field": self.path(), "provenance": self.published()}
        if self.source:
            entry["source"] = self.source
        if self.conditions:
            entry["conditions"] = list(self.conditions)
        if self.reason is not None:
            entry["reason"] = self.reason
        if self.when is not None:
            entry["when"] = self.when
        return entry


class KpiSchema:
    """The published shape of the ``kpis`` block, without running anything.

    The rows restate, in one phrase each, what :class:`KpiBuilder` does -- deliberately, because
    the audience is a frontend developer reading a page or a JSON document rather than a Python
    method. Every phrase is built from :class:`KpiSources` and from the builder's own reason
    constants, so the two cannot drift apart silently, and the unit test asserts that every
    :class:`KpiField` has a row.
    """

    #: Which block of ``result.json`` these rows describe; the prefix their ``missing`` entries
    #: carry, so the two documents address one field by one path.
    BLOCK: ClassVar[str] = KpiBuilder.MISSING_PREFIX

    #: What an annual figure does when the run is shorter than a year, which is every run of the
    #: one-day pair the examples use.
    SCALED_CONDITION: ClassVar[str] = (
        "PARTIAL when the period is shorter than a year, and the annual figure is scaled from it"
    )

    #: When the embodied-carbon figure is absent instead of published. A partial sum over some of
    #: a building's insulation would read as the whole building's carbon, so it is all or nothing.
    EMBODIED_CO2_WHEN: ClassVar[str] = (
        "absent when the package insulates nothing, or when a layer's element area or its "
        "material's CO2 footprint is unknown"
    )

    #: The energy label's one condition: the field exists and its value is always null.
    ENERGY_LABEL_CONDITION: ClassVar[str] = "the value is always null; no letter is invented"

    @classmethod
    def rows(cls) -> Tuple[PayloadFieldRow, ...]:
        """Return one row per field of the ``kpis`` block, in payload order."""
        mocked = f"{ContractFiles.OPENAPI_FILENAME} Kpis.{{}} examples[0]"
        return (
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.ENERGY_DEMAND.value,
                f"all_kpis.json '{KpiSources.ENERGY_DEMAND_NAME}' (energy bought, Q22)",
                Provenance.SIMULATED,
                conditions=(cls.SCALED_CONDITION,),
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.EMISSIONS.value,
                f"{LifecycleCo2.FILE_NAME} {LifecycleCo2.PERSPECTIVE_ID}.{LifecycleCo2.BLOCK} "
                "(operational CO2 per year, the cost engine's own country factors, Q22)",
                Provenance.SIMULATED,
                conditions=(cls.SCALED_CONDITION,),
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.SELF_SUFFICIENCY.value,
                f"all_kpis.json '{KpiSources.SELF_SUFFICIENCY_NAME}' (a rate, never scaled)",
                Provenance.SIMULATED,
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.EMBODIED_CO2.value,
                "the request material's co2_footprint_a1_a3_c3_c4_kg_m2 x element area",
                Provenance.SIMULATED,
                reason=KpiBuilder.EMBODIED_CO2_ABSENT_REASON,
                when=cls.EMBODIED_CO2_WHEN,
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.ENERGY_LABEL.value,
                KpiBuilder.ENERGY_LABEL_REASON,
                Provenance.MOCKED,
                conditions=(cls.ENERGY_LABEL_CONDITION,),
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.DISRUPTION_DAYS.value,
                mocked.format(KpiField.DISRUPTION_DAYS.value),
                Provenance.MOCKED,
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.INDOOR_AIR_QUALITY.value,
                mocked.format(KpiField.INDOOR_AIR_QUALITY.value),
                Provenance.MOCKED,
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.THERMAL_INSULATION_EFFECT.value,
                mocked.format(KpiField.THERMAL_INSULATION_EFFECT.value),
                Provenance.MOCKED,
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.SUMMER_HEAT_PROTECTION.value,
                mocked.format(KpiField.SUMMER_HEAT_PROTECTION.value),
                Provenance.MOCKED,
            ),
            PayloadFieldRow(
                cls.BLOCK,
                KpiField.COMFORT.value,
                mocked.format("comfort.heating") + " and comfort.cooling",
                Provenance.MOCKED,
            ),
        )
