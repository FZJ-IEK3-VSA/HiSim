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
``tests/test_renovisor_kpis.py`` greps them out of ``kpi_preparation.py``: a rename becomes a
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
from hisim.renovisor.materials import InsulationMaterials
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

    #: Operational CO2 of the simulated period (decision Q22). It is the sum of the grid
    #: electricity, grid gas and other-fuel footprints plus the *equipment* footprint, and that
    #: last term is zero unless ``COMPUTE_CAPEX`` was among the run's options -- which a RenoVisor
    #: calculation does not ask for, so what this reads is operational carbon alone.
    EMISSIONS_NAME: ClassVar[str] = "Total CO2 emissions for simulated period"

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
        return {
            KpiField.ENERGY_DEMAND: cls.ENERGY_DEMAND_NAME,
            KpiField.EMISSIONS: cls.EMISSIONS_NAME,
        }

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


class KpiBuilder:
    """Builds the ``kpis`` block of one calculation's ``result.json``.

    The builder reads nothing itself beyond what it is handed: the run's KPI document, the period
    it covered, the insulation layers the package added and the material table. That is what lets
    the unit tests drive it with a synthetic KPI document and a two-layer package instead of a
    simulation.

    Args:
        document: The run's ``all_kpis.json``, or ``None`` when it wrote none.
        period: The simulated period, which decides scaling and provenance.
        layers: The package's insulation layers, for the embodied-carbon figure.
        materials: The insulation-material table the carbon figures come from.
        emission_factor_country: The country whose emission and price factors the legacy KPI path
            used, named in the emissions field's ``source`` because it is the one input of that
            figure the RenoVisor request does not choose.
        dwelling_country: The country the dwelling is in. When it differs from
            *emission_factor_country* the operational CO2 figure rests on another country's grid,
            which is an unreviewed input and therefore makes the field ``PARTIAL`` however long
            the run was (decision Q21's own definition of the label).
    """

    #: The prefix every missing KPI field carries in ``result.json["missing"]``.
    MISSING_PREFIX: ClassVar[str] = "kpis"

    #: Why there is no energy label. HiSim implements no BER or DEAP procedure, and a letter
    #: derived from a kilowatt-hour figure by some other rule would be a rating nobody issued.
    ENERGY_LABEL_REASON: ClassVar[str] = (
        f"no BER/DEAP procedure in HiSim; no letter is invented ({ContractExamples.DECISION})"
    )

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
        materials: InsulationMaterials,
        emission_factor_country: str,
        dwelling_country: str,
    ) -> None:
        """Store the inputs; nothing is read until :meth:`build`."""
        self._document = document
        self._period = period
        self._layers = layers
        self._materials = materials
        self._country = emission_factor_country
        self._dwelling_country = dwelling_country
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
        if field is KpiField.EMISSIONS:
            source += (
                f"; operational CO2 (Q22), computed with the '{self._country}' emission factors of "
                "hisim/components/configuration.py"
            )
            if self._country != self._dwelling_country:
                source += (
                    f", which are not the '{self._dwelling_country}' grid's: that table has no "
                    f"'{self._dwelling_country}' row"
                )
                reviewed = False
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

    def _embodied_co2(self) -> Optional[Dict[str, Any]]:
        """Return the embodied carbon of the package's insulation layers.

        Decision Q22 made embodied carbon a field of its own rather than a term inside the
        operational figure, and the materials table is where it comes from: cradle-to-gate CO2
        equivalent per cubic metre times the volume each layer installs. A material whose dump row
        carries no per-cubic-metre figure, or a layer whose element area neither the realized
        record nor the inventory states, makes the whole field absent -- a partial sum over some
        of a building's insulation would read as the whole building's carbon.

        Returns:
            The provenance object, or ``None`` when the field is absent.
        """
        if self._layers.is_empty():
            self._absent(KpiField.EMBODIED_CO2, "the package adds no insulation layer (R8)")
            return None
        unresolved = self._layers.unresolved()
        if unresolved:
            self._absent(
                KpiField.EMBODIED_CO2,
                "no element area for " + ", ".join(layer.describe() for layer in unresolved),
            )
            return None
        total = 0.0
        terms: List[str] = []
        for layer in self._layers.all():
            if not self._materials.contains(layer.material_asp_id):
                self._absent(
                    KpiField.EMBODIED_CO2,
                    f"the material table has no row for '{layer.material_asp_id}'",
                )
                return None
            footprint = self._materials.by_asp_id(layer.material_asp_id).co2_footprint_in_kg_per_m3
            if footprint is None:
                self._absent(
                    KpiField.EMBODIED_CO2,
                    f"the material dump states no CO2 footprint per m3 for "
                    f"'{layer.material_asp_id}', so the package's embodied carbon is incomplete",
                )
                return None
            volume = layer.volume_in_m3()
            assert volume is not None  # guarded by the unresolved() check above
            total += footprint * volume
            terms.append(f"{layer.describe()} = {volume:g} m3 x {footprint:g} kg/m3")
        return ProvenancedValue(
            value=total,
            provenance=Provenance.SIMULATED,
            source="insulation_materials.json co2_footprint_in_kg_per_m3 x layer volume (Q22): "
            + "; ".join(terms),
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
    """One row of the payload's published shape: a field, its source and its expected provenance.

    The translation map is generated offline, with no simulation and therefore no ``result.json``
    to show. What it can show is the *shape* the frontend will receive: every field the payload
    can carry, where its number comes from, and whether that number will be a simulation result, a
    constant or a partly-estimated figure. That is what a frontend team needs before the first
    real payload exists, and what a reviewer needs to see the mocked fields without reading code.

    Args:
        field: The field's name in the payload block.
        source: Where its value comes from, in one phrase.
        provenance: The provenance it is published with, or the phrase saying it is absent.
    """

    field: str
    source: str
    provenance: str


class KpiSchema:
    """The published shape of the ``kpis`` block, without running anything.

    The rows restate, in one phrase each, what :class:`KpiBuilder` does -- deliberately, because
    the map's audience is a frontend developer reading a page rather than a Python method. The
    unit test asserts that every :class:`KpiField` has a row, so a field added to the payload
    without a row on the map is a failing build.
    """

    #: The provenance an annual figure carries when the run covers less than a full year, which is
    #: every run of the one-day pair the examples use.
    SCALED_PROVENANCE: ClassVar[str] = "SIMULATED over a full year, PARTIAL and scaled otherwise"

    @classmethod
    def rows(cls) -> Tuple[PayloadFieldRow, ...]:
        """Return one row per field of the ``kpis`` block, in payload order."""
        mocked = f"{ContractFiles.OPENAPI_FILENAME} Kpis.{{}} examples[0]"
        return (
            PayloadFieldRow(
                KpiField.ENERGY_DEMAND.value,
                f"all_kpis.json '{KpiSources.ENERGY_DEMAND_NAME}' (energy bought, Q22)",
                cls.SCALED_PROVENANCE,
            ),
            PayloadFieldRow(
                KpiField.EMISSIONS.value,
                f"all_kpis.json '{KpiSources.EMISSIONS_NAME}' (operational CO2, Q22)",
                cls.SCALED_PROVENANCE,
            ),
            PayloadFieldRow(
                KpiField.SELF_SUFFICIENCY.value,
                f"all_kpis.json '{KpiSources.SELF_SUFFICIENCY_NAME}' (a rate, never scaled)",
                Provenance.SIMULATED.value,
            ),
            PayloadFieldRow(
                KpiField.EMBODIED_CO2.value,
                "insulation_materials.json co2_footprint_in_kg_per_m3 x thickness x element area",
                f"{Provenance.SIMULATED.value}; absent when the package insulates nothing",
            ),
            PayloadFieldRow(
                KpiField.ENERGY_LABEL.value,
                "no BER/DEAP procedure in HiSim",
                f"{Provenance.MOCKED.value}, value null",
            ),
            PayloadFieldRow(
                KpiField.DISRUPTION_DAYS.value,
                mocked.format(KpiField.DISRUPTION_DAYS.value),
                Provenance.MOCKED.value,
            ),
            PayloadFieldRow(
                KpiField.INDOOR_AIR_QUALITY.value,
                mocked.format(KpiField.INDOOR_AIR_QUALITY.value),
                Provenance.MOCKED.value,
            ),
            PayloadFieldRow(
                KpiField.THERMAL_INSULATION_EFFECT.value,
                mocked.format(KpiField.THERMAL_INSULATION_EFFECT.value),
                Provenance.MOCKED.value,
            ),
            PayloadFieldRow(
                KpiField.SUMMER_HEAT_PROTECTION.value,
                mocked.format(KpiField.SUMMER_HEAT_PROTECTION.value),
                Provenance.MOCKED.value,
            ),
            PayloadFieldRow(
                KpiField.COMFORT.value,
                mocked.format("comfort.heating") + " and comfort.cooling",
                Provenance.MOCKED.value,
            ),
        )
