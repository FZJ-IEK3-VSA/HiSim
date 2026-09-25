"""T-NIY and T-CAP: the list is honest in both directions, and the document says what runs do.

**T-NIY** is the fail-loud rule of §1.6 in test form. Over the whole probe set: every
``not_implemented_yet`` report line carries a note (which can only come from an entry, because
the only way to write such a line is to ask the list for one), and every entry of
``not_implemented_yet.yaml`` is reached by at least one probe. An entry nothing reaches is
either an omission somebody has since implemented -- delete it -- or a sentence about a request
nobody can send, which is a promise no run can keep.

**T-CAP** is the tally: the document's measure-level counts, computed from the probes rather
than declared, compared against the frontend side's §4.2 table adjusted for decision D-D. D-D
moves five measures the spec counted as implemented onto the list, because the recorded twins
have no night-setback group, no air conditioner and no energy management system without a
battery.
"""

from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, Mapping, Optional

import copy
import pytest

from hisim.renovisor.apply import MeasureRegistry
from hisim.renovisor.capabilities import (
    Aggregation,
    CapabilityDocument,
    MeasureStatus,
    NoteAggregation,
    Observation,
    Probe,
    ProbeKind,
    ProbeResult,
    ProbeRunner,
    ProbeSet,
    RequestSchemaBounds,
    ResultsSection,
    unlisted_lines,
)
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.costs import CostField, CostSchema
from hisim.renovisor.kpis import KpiField, KpiSchema
from hisim.renovisor.request import CatalogueTable, ValueType
from hisim.renovisor.vocabulary import Provenance, ReportStatus

#: The tally of §4.2 as decision D-D leaves it. The frontend side's spec counted 16 / 9 / 7;
#: `air_conditioners`, `temperature_control_system` and
#: `optimize_behaviour_for_self_consumption_of_pv` move to the list because the twins have no
#: group for them, which is three measures off `supported` and one off `approximated`. Contract
#: PR #10 (2026-09-22) then gave `cavity_wall_insulation`, `basement_internal_insulation` and
#: `top_floor_ceiling_insulation` a `material` option: the translator stopped picking their
#: material itself, and the three move from `approximated` to `supported` (15 / 7 became 18 / 4).
EXPECTED_TALLY: Dict[str, int] = {"supported": 18, "approximated": 4, "not_implemented_yet": 10}

#: The ten measures the list carries at measure level, which is what the tally above counts.
EXPECTED_NOT_IMPLEMENTED = {
    "outside_shading",
    "ventilation_system",
    "shallow_air_tightness_measures",
    "replace_white_appliances",
    "electric_vehicle",
    "diy_sealing_of_air_leaks",
    "thermocover_for_the_windows",
    "air_conditioners",
    "temperature_control_system",
    "optimize_behaviour_for_self_consumption_of_pv",
}


@pytest.fixture(scope="module", name="document")
def fixture_document() -> CapabilityDocument:
    """Build the capability document once; every test in this module reads the same probe run."""
    return CapabilityDocument.build()


@pytest.mark.base
class TestTheProbeSet:
    """The set is data, and it covers the two ends of the sparseness rule."""

    def test_the_anchor_is_the_vendored_mockup_with_an_empty_package(self) -> None:
        """One example, pointed at by both sides of the contract."""
        anchor = ProbeSet.anchor()

        assert anchor["measures"] == []
        assert anchor["house"]["heating"]["type_of_system"] == "conventional_gas_heating"

    def test_the_probe_material_is_the_mockups_external_insulation_row(self) -> None:
        """No material is typed twice: a re-vendored mockup row is the row every probe sends."""
        mockup = ContractFiles.request_mockup()
        row = next(measure for measure in mockup["measures"] if measure["id"] == "external_insulation")
        expected = row["options"][CatalogueTable.MATERIAL]

        assert ProbeSet.material() == expected
        for measure_id in CatalogueTable.ids():
            options = ProbeSet.package(measure_id).get("options", {})
            if CatalogueTable.MATERIAL in options:
                assert options[CatalogueTable.MATERIAL] == expected

    def test_a_mockup_without_that_row_is_refused_by_name(self) -> None:
        """The probe set has no fallback material; losing the mockup's row is a loud failure."""
        mockup = ContractFiles.request_mockup()
        mockup["measures"] = [measure for measure in mockup["measures"] if measure["id"] != "external_insulation"]

        with pytest.raises(ValueError, match="calculation-request.mockup-1.yaml carries no 'external_insulation'"):
            ProbeSet.material(mockup)

    def test_the_bare_baseline_carries_no_optional_block_at_all(self) -> None:
        """The request the defect of c02bc801 was never exercised by, now probed every time."""
        bare = next(probe for probe in ProbeSet.build() if probe.kind is ProbeKind.BARE)
        house = bare.document(ProbeSet.anchor())["house"]

        for block in ProbeSet.BLOCKS:
            assert block not in house
        assert "shape" not in house["building"]["roof"]

    def test_every_catalogue_measure_and_option_value_is_probed(self) -> None:
        """A value added to the catalogue tomorrow is probed tomorrow, with no probe written."""
        names = {probe.name for probe in ProbeSet.build()}

        for measure_id in CatalogueTable.ids():
            assert f"measure:{measure_id}" in names
            for option in CatalogueTable.options_of(measure_id):
                if option.value_type is ValueType.MATERIAL:
                    continue  # a material travels as an object, so its probe is named after it
                for value in option.values or ():
                    assert f"option:{measure_id}.{option.name}={value}" in names

    def test_the_set_is_bigger_than_the_catalogue_and_smaller_than_a_test_suite(self) -> None:
        """A few hundred probes is what a pure translator buys: the whole set runs in a second."""
        assert 200 < len(ProbeSet.build()) < 600


@pytest.mark.base
class TestTheListIsHonestInBothDirections:
    """T-NIY, which runs on every pull request."""

    def test_every_not_implemented_line_carries_a_note(self, document: CapabilityDocument) -> None:
        """Only the list may produce that status, so a line without a note cannot exist."""
        assert not unlisted_lines(document.results)

    def test_every_entry_of_the_list_is_reached_by_a_probe(self, document: CapabilityDocument) -> None:
        """An entry nothing reaches is a promise no run can keep; delete it or reach it."""
        assert document.unhit_entries() == (), (
            "these entries of not_implemented_yet.yaml are unreachable: "
            + ", ".join(document.unhit_entries())
        )

    def test_no_probe_ends_in_a_translator_error(self, document: CapabilityDocument) -> None:
        """A probe that raised would have failed the build already; this says what that proves."""
        assert len(document.results) == document.body["translator"]["probes"]

    def test_an_excepted_value_is_not_noted(self, document: CapabilityDocument) -> None:
        """A value in ``except`` is implemented, so it must not carry the entry's sentence."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}
        ventilation = fields["house.ventilation.type_of_system"]
        natural = next(value for value in ventilation["values"] if value["value"] == "natural")

        assert natural["status"] == ReportStatus.USED.value
        assert "not configurable" not in str(natural.get("note", ""))
        mechanical = next(
            value for value in ventilation["values"] if value["value"] == "mechanical_extract"
        )
        assert mechanical["status"] == ReportStatus.NOT_IMPLEMENTED_YET.value
        assert "not configurable" in mechanical["note"]


@pytest.mark.base
class TestTheDocument:
    """T-CAP: the shape, the tally and the notes."""

    def test_it_validates_against_the_vendored_openapi_schema(self, document: CapabilityDocument) -> None:
        """The document is what ``GET /measures`` returns, so it has to be that shape."""
        document.validate()

    def test_every_catalogue_measure_appears_exactly_once(self, document: CapabilityDocument) -> None:
        """A consumer that finds one missing treats the whole answer as invalid."""
        ids = [entry["measure_id"] for entry in document.body["measures"]]

        assert ids == list(CatalogueTable.ids())
        assert len(ids) == len(set(ids)) == 32

    def test_every_option_of_every_measure_appears_exactly_once(self, document: CapabilityDocument) -> None:
        """Option names and, for an enum, every one of its values."""
        for entry in document.body["measures"]:
            options = CatalogueTable.options_of(str(entry["measure_id"]))
            assert [option["name"] for option in entry["options"]] == [option.name for option in options]
            for option, spec in zip(entry["options"], options):
                if spec.values is not None:
                    assert option["accepted_values"] == list(spec.values)
                    assert [value["value"] for value in option["values"]] == list(spec.values)

    def test_a_material_option_entry_has_exactly_its_name_status_and_note(
        self, document: CapabilityDocument
    ) -> None:
        """A material travels as an object, so its entry lists no values and no bounds.

        Pinned for every insulation measure: a frontend reads the material list from
        ``materials.yaml``, and an ``accepted_values`` or a ``minimum`` appearing here would be a
        second, disagreeing source for it.
        """
        entries = {str(entry["measure_id"]): entry for entry in document.body["measures"]}
        for measure_id in MeasureRegistry.INSULATION:
            spec = CatalogueTable.material_option(measure_id)
            assert spec is not None, measure_id
            option = next(option for option in entries[measure_id]["options"] if option["name"] == spec.name)
            assert option == {
                "name": CatalogueTable.MATERIAL,
                "status": ReportStatus.USED.value,
                "note": MeasureRegistry.MATERIAL_NOTE,
            }, measure_id

    def test_the_measure_tally_is_the_contracts_table_as_decision_dd_leaves_it(
        self, document: CapabilityDocument
    ) -> None:
        """Computed from the probes, never declared; this is what it comes to."""
        assert document.tally() == EXPECTED_TALLY

    def test_the_measures_on_the_list_are_the_ones_the_list_names(
        self, document: CapabilityDocument
    ) -> None:
        """The tally is a count; this is the count's content."""
        listed = {
            str(entry["measure_id"])
            for entry in document.body["measures"]
            if entry["status"] == MeasureStatus.NOT_IMPLEMENTED_YET.value
        }

        assert listed == EXPECTED_NOT_IMPLEMENTED

    def test_a_substitution_is_told_apart_from_a_gap(self, document: CapabilityDocument) -> None:
        """The review's D-E: the frontend hides one class and may offer the other with its note."""
        measures = {entry["measure_id"]: entry for entry in document.body["measures"]}

        assert measures["outside_shading"]["substitution"] is False
        heating = measures["heating_system"]
        hybrid = next(
            value
            for option in heating["options"]
            if option["name"] == "type_of_system"
            for value in option["values"]
            if value["value"] == "hybrid_heat_pump"
        )
        assert "modelled as" in hybrid["note"]
        assert Aggregation.is_substitution(hybrid["note"])
        assert hybrid["substitution"] is True

    def test_every_value_entry_announces_whether_it_is_a_substitution(
        self, document: CapabilityDocument
    ) -> None:
        """hisim-epc.10: the per-value flag the frontend's F8 wording keys on.

        Every ``values`` entry of every option and every inventory field carries the flag, and
        it is true exactly when the entry's own note says "modelled as" or "stands in for".
        """
        for entry in document.body["measures"]:
            for option in entry["options"]:
                for value in option.get("values", ()):
                    assert isinstance(value["substitution"], bool)
                    assert value["substitution"] is Aggregation.is_substitution(value.get("note"))
        for entry in document.body["fields"]:
            for value in entry.get("values", ()):
                assert isinstance(value["substitution"], bool)
                assert value["substitution"] is Aggregation.is_substitution(value.get("note"))

    def test_the_heat_pump_values_carry_their_own_flags(self, document: CapabilityDocument) -> None:
        """The concrete values F8 reads: stand-ins say so, implemented values do not."""
        heating = next(
            entry for entry in document.body["measures"] if entry["measure_id"] == "heating_system"
        )
        statuses = {
            value["value"]: value
            for option in heating["options"]
            if option["name"] == "type_of_system"
            for value in option["values"]
        }

        assert statuses["air_source_heat_pump"]["substitution"] is False
        assert statuses["hybrid_heat_pump"]["substitution"] is True
        assert statuses["ground_source_heat_pump"]["substitution"] is True
        assert statuses["conventional_lpg_heating"]["substitution"] is True
        assert statuses["condensing_lpg_heating"]["substitution"] is True

    def test_a_value_that_is_not_implemented_does_not_change_its_measures_status(
        self, document: CapabilityDocument
    ) -> None:
        """§4.2: values carry their own status and never change the measure's."""
        heating = next(
            entry for entry in document.body["measures"] if entry["measure_id"] == "heating_system"
        )

        assert heating["status"] == MeasureStatus.SUPPORTED.value
        statuses = {
            value["value"]: value["status"]
            for option in heating["options"]
            if option["name"] == "type_of_system"
            for value in option["values"]
        }
        assert statuses["air_source_heat_pump"] == ReportStatus.USED.value
        assert statuses["hybrid_heat_pump"] == ReportStatus.NOT_IMPLEMENTED_YET.value

    def test_the_document_names_the_build_it_came_from(self, document: CapabilityDocument) -> None:
        """Immutable per engine version, so it has to say which version it is of."""
        translator = document.body["translator"]

        assert document.body["engine"] == "hisim"
        assert translator["request_schema_version"] == 1
        assert translator["catalogue_revision"].startswith("renovisor-api-contract@")
        assert translator["probes"] > 0

    def test_the_inventory_fields_are_aggregated_the_same_way(self, document: CapabilityDocument) -> None:
        """``fields`` is the same report, taken over the house instead of over the package."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}

        assert fields["house.occupancy.number_of_residents"]["status"] == (
            ReportStatus.NOT_IMPLEMENTED_YET.value
        )
        assert fields["house.building.facade.u_value_in_watt_per_m2_per_kelvin"]["status"] == (
            ReportStatus.USED.value
        )

    def test_two_builds_of_one_state_are_byte_identical(
        self, document: CapabilityDocument, tmp_path: Path
    ) -> None:
        """The backend hashes the file for a strong, immutable ETag; a clock in it would break that (H18)."""
        second = CapabilityDocument.build()
        first_path = tmp_path / "first.json"
        second_path = tmp_path / "second.json"

        assert document.write(first_path) == 0
        assert second.write(second_path) == 0
        assert first_path.read_bytes() == second_path.read_bytes()
        assert "generated_at" not in document.body["translator"]
        assert first_path.read_text(encoding="utf-8").endswith("\n")


@pytest.mark.base
class TestTheRunner:
    """Every probe goes through validate, apply and translate, and nothing else."""

    def test_a_refused_probe_is_a_result_rather_than_an_exception(self) -> None:
        """``ES`` has no TABULA typology; that is a refusal the document records, not a crash."""
        probe = Probe(name="field:location.country=ES", kind=ProbeKind.FIELD)
        probe = Probe(
            name=probe.name, kind=ProbeKind.FIELD, location={"country": "ES"}, subject="location.country"
        )

        results = ProbeRunner().run([probe])

        assert results[0].refused == ("location.country.unsupported",)


@pytest.mark.base
class TestANoteFollowsTheStatusItExplains:
    """The step 8 addendum A: an entry is explained by the probe that set its status.

    The defect this pins: ``house.solar_thermal_system.supplies`` was published as
    ``not_implemented_yet`` -- because one probe asked for ``dhw_and_space_heating`` -- while
    carrying the sentence of ``dhw_only``, the value that works. A reader of the document was
    told an item was missing and handed the explanation of the item that is not.
    """

    @staticmethod
    def _result(name: str, path: str, status: ReportStatus, note: str) -> ProbeResult:
        """Return one probe result carrying one field line, for the aggregation to reduce."""
        return ProbeResult(
            probe=Probe(name=name, kind=ProbeKind.BLOCK), fields={path: (status, note)}
        )

    def test_a_two_probe_field_carries_the_note_of_the_probe_that_set_the_status(self) -> None:
        """Two probes, two statuses, two sentences: the published one follows the status."""
        path = "house.example.field"
        entries = Aggregation.fields(
            (
                self._result("first", path, ReportStatus.USED, "the value that works"),
                self._result("second", path, ReportStatus.NOT_IMPLEMENTED_YET, "no model for it"),
            )
        )

        assert entries[0]["status"] == ReportStatus.NOT_IMPLEMENTED_YET.value
        assert entries[0]["note"] == "no model for it"

    def test_probes_that_tie_at_the_worst_status_carry_all_their_sentences(self) -> None:
        """Each is true of a different request, so picking one of them would hide the other."""
        path = "house.example.field"
        entries = Aggregation.fields(
            (
                self._result("first", path, ReportStatus.USED, "the value that works"),
                self._result("second", path, ReportStatus.NOT_IMPLEMENTED_YET, "no heat pump"),
                self._result("third", path, ReportStatus.NOT_IMPLEMENTED_YET, "no immersion"),
            )
        )

        assert entries[0]["note"] == f"no heat pump{NoteAggregation.SEPARATOR}no immersion"

    def test_two_probes_that_tie_with_the_same_sentence_carry_it_once(self) -> None:
        """A sentence repeated by every probe of a kind is still one sentence."""
        observations = (
            Observation(ReportStatus.NOT_IMPLEMENTED_YET, "one sentence"),
            Observation(ReportStatus.NOT_IMPLEMENTED_YET, "one sentence"),
        )

        assert NoteAggregation.note_of(observations, ReportStatus.NOT_IMPLEMENTED_YET) == "one sentence"

    def test_a_status_with_no_sentence_behind_it_carries_none(self) -> None:
        """A note belonging to another status is not borrowed to fill the gap."""
        observations = (
            Observation(ReportStatus.USED, "a used sentence"),
            Observation(ReportStatus.APPROXIMATED, None),
        )

        assert NoteAggregation.note_of(observations, ReportStatus.APPROXIMATED) is None

    def test_the_document_explains_the_field_that_the_defect_was_found_on(
        self, document: CapabilityDocument
    ) -> None:
        """The real entry, over the real probe set: the collector's two supply values."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}
        supplies = fields["house.solar_thermal_system.supplies"]

        assert supplies["status"] == ReportStatus.NOT_IMPLEMENTED_YET.value
        assert "modelled as dhw_only" in supplies["note"]
        assert supplies["substitution"] is True

    def test_the_two_unimplemented_hot_water_supplies_are_both_named(
        self, document: CapabilityDocument
    ) -> None:
        """Two probes tie at the worst status with different sentences, so both are carried."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}
        supply = fields["house.hot_water.supply"]

        assert NoteAggregation.SEPARATOR in supply["note"]
        assert "domestic-hot-water heat pump" in supply["note"]
        assert "immersion-heater" in supply["note"]

    def test_a_used_option_carries_no_sentence_from_one_of_its_values(
        self, document: CapabilityDocument
    ) -> None:
        """The values keep their own notes; the option says nothing it cannot account for."""
        heating = next(
            entry for entry in document.body["measures"] if entry["measure_id"] == "heating_system"
        )
        option = next(row for row in heating["options"] if row["name"] == "type_of_system")

        assert option["status"] == ReportStatus.USED.value
        assert "note" not in option
        hybrid = next(value for value in option["values"] if value["value"] == "hybrid_heat_pump")
        assert "modelled as air_source_heat_pump" in hybrid["note"]


@pytest.mark.base
class TestTheResultsSection:
    """The step 8 addendum B: what the caller gets back, beside what the translator accepts."""

    def test_every_payload_field_has_an_entry(self, document: CapabilityDocument) -> None:
        """Generated from the tables the payload is built from, so it cannot miss one."""
        results = document.body["results"]
        kpis = {entry["field"] for entry in results["kpis"]}
        costs = {entry["field"] for entry in results["costs"]}

        assert kpis == {f"kpis.{field.value}" for field in KpiField}
        assert {f"costs.{field.value}" for field in CostField} <= costs

    def test_it_is_generated_from_the_payload_tables_and_not_from_a_second_list(self) -> None:
        """The section is the two schema tables serialized, entry for entry."""
        section = ResultsSection.build()

        assert section["kpis"] == [row.to_json() for row in KpiSchema.rows()]
        assert section["costs"] == [row.to_json() for row in CostSchema.rows()]

    def test_a_published_field_names_its_source_and_its_provenance(
        self, document: CapabilityDocument
    ) -> None:
        """A frontend has to be able to tell a simulated figure from a constant in advance."""
        entries = {entry["field"]: entry for entry in document.body["results"]["kpis"]}
        demand = entries["kpis.energy_demand_in_kilowatt_hour_per_year"]

        assert demand["provenance"] == Provenance.SIMULATED.value
        assert "Purchased energy consumption" in demand["source"]
        assert any("PARTIAL when the period" in condition for condition in demand["conditions"])

    def test_every_cost_field_is_absent_with_the_reason_the_payload_writes(
        self, document: CapabilityDocument
    ) -> None:
        """Since step 10 the money is in ``economics_result.json``, and the document says so.

        Decision R8 forbids the plausible zero, so a field ``result.json`` does not carry has to
        announce its absence. Every cost field is now such a field: the staged evaluator prices
        the whole plan, and the reason names the document, the key inside it and the command that
        writes it -- except the property-value figure, which moved nowhere because no model
        produces it (A13).
        """
        entries = {entry["field"]: entry for entry in document.body["results"]["costs"]}

        for field in CostField:
            entry = entries[f"costs.{field.value}"]
            assert entry["provenance"] == "absent"
            assert entry["reason"]
            assert entry["when"]
        assert "economics_result.json" in entries["costs.grant_in_euro"]["reason"]
        assert "A13" in entries["costs.property_value_increase_in_percent"]["reason"]

    def test_a_field_that_is_published_but_can_be_absent_says_both(
        self, document: CapabilityDocument
    ) -> None:
        """Embodied carbon is the one such field: simulated, or absent with its own sentence."""
        entries = {entry["field"]: entry for entry in document.body["results"]["kpis"]}
        embodied = entries["kpis.embodied_co2_in_kg"]

        assert embodied["provenance"] == Provenance.SIMULATED.value
        assert "no insulation layer" in embodied["reason"]
        assert "element area" in embodied["when"]

    def test_the_section_is_what_the_shared_schema_declares(self, document: CapabilityDocument) -> None:
        """``results`` is validated with the rest of the document against the shared schema."""
        document.validate()


@pytest.mark.base
class TestTheSchemaIsStrict:
    """The shared schema admits exactly what the document carries (decision of 2026-09-23).

    A key the translator starts to emit fails validation until the spec declares it, so the spec
    cannot fall behind the served document again.
    """

    @pytest.mark.parametrize(
        "where",
        ["top", "translator", "measure", "option", "value", "field", "result"],
    )
    def test_an_undeclared_key_anywhere_is_refused(self, document: CapabilityDocument, where: str) -> None:
        """One invented key at each level of the document fails validation."""
        from jsonschema import ValidationError

        body = copy.deepcopy(document.body)
        valued_options = [
            option for measure in body["measures"] for option in measure["options"] if option.get("values")
        ]
        assert valued_options, "no option of the document carries values"
        valued = valued_options[0]
        targets = {
            "top": body,
            "translator": body["translator"],
            "measure": body["measures"][0],
            "option": valued,
            "value": valued["values"][0],
            "field": body["fields"][0],
            "result": body["results"]["kpis"][0],
        }
        targets[where]["invented"] = 1
        with pytest.raises(ValidationError):
            CapabilityDocument(body=body, results=document.results, whitelist=document.whitelist).validate()

    @pytest.mark.parametrize("key", ["fields", "results", "translator"])
    def test_a_missing_section_is_refused(self, document: CapabilityDocument, key: str) -> None:
        """What the document always carries is required, so a consumer can rely on it."""
        from jsonschema import ValidationError

        body = copy.deepcopy(document.body)
        del body[key]
        with pytest.raises(ValidationError):
            CapabilityDocument(body=body, results=document.results, whitelist=document.whitelist).validate()


#: The keywords the request schema bounds or enumerates a leaf with.
BOUND_KEYWORDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "enum")


def _request_schema_leaf(path: str) -> Optional[Dict[str, Any]]:
    """Return what the request schema declares about one dotted path, over refs and alternatives.

    Deliberately a second, smaller walk than the translator's own
    :class:`hisim.renovisor.capabilities.RequestSchemaBounds`, so the test does not grade the
    production walker with itself. Every reference in the vendored schema is ``#/$defs/<name>``.
    ``None`` when the schema has no such path: the document also lists fields the translator
    derives (``house.heating.installation_year``) that a request cannot carry.
    """
    schema = ContractFiles.request_schema()

    def expand(node: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
        yield node
        if "$ref" in node:
            prefix, name = str(node["$ref"]).rsplit("/", 1)
            assert prefix == "#/$defs", node["$ref"]
            yield from expand(schema["$defs"][name])
        for keyword in ("oneOf", "anyOf", "allOf"):
            for alternative in node.get(keyword, []):
                yield from expand(alternative)
        if isinstance(node.get("items"), Mapping):
            yield from expand(node["items"])

    node: Mapping[str, Any] = schema
    for part in path.split("."):
        child = next(
            (candidate["properties"][part] for candidate in expand(node) if part in candidate.get("properties", {})),
            None,
        )
        if child is None:
            return None
        node = child
    return {key: value for candidate in expand(node) for key, value in candidate.items() if key in BOUND_KEYWORDS}


@pytest.mark.base
class TestNumericOptions:
    """A numeric option publishes bounds only where the request schema bounds its field inclusively."""

    #: ``measure_id.option`` -> the request field the option writes, for the options of contract
    #: 882a8c1 whose field the request schema declares.
    OPTION_FIELDS: ClassVar[Dict[str, str]] = {
        "air_conditioners.power_in_watt": "house.air_conditioning.power_in_watt",
        "photovoltaic_system.power_in_watt": "house.pv_system.power_in_watt",
        "photovoltaic_system.azimuth_in_degree": "house.pv_system.azimuth",
        "photovoltaic_system.tilt_in_degree": "house.pv_system.tilt",
        "photovoltaic_system.shading_losses_in_percent": "house.pv_system.shading_losses_in_percent",
        "battery_system.capacity_in_kwh": "house.battery.custom_battery_capacity_generic_in_kilowatt_hour",
        "battery_system.power_in_watt": "house.battery.power_in_watt",
    }

    def test_every_free_numeric_option_has_two_probe_points(self) -> None:
        """A numeric option the catalogue adds without points fails here by name, not in a probe."""
        for measure_id in CatalogueTable.ids():
            for option in CatalogueTable.options_of(measure_id):
                if option.values is None and option.value_type in (ValueType.INTEGER, ValueType.NUMBER):
                    (low, high), _published = ProbeSet.option_points(measure_id, option.name)
                    assert low < high, (measure_id, option.name)

    def test_an_option_with_a_schema_field_is_probed_inside_it_and_publishes_only_inclusive_bounds(self) -> None:
        """Published bounds are the schema's own; an exclusive or missing bound makes them private points."""
        for qualified, path in self.OPTION_FIELDS.items():
            measure_id, name = qualified.split(".")
            (low, high), published = ProbeSet.option_points(measure_id, name)
            declared = _request_schema_leaf(path)
            assert declared is not None, path
            for point in (low, high):
                assert point >= declared.get("minimum", point), (qualified, point)
                assert point <= declared.get("maximum", point), (qualified, point)
                assert point > declared.get("exclusiveMinimum", point - 1), (qualified, point)
            inclusive = "minimum" in declared and "maximum" in declared
            assert published == inclusive, qualified
            if published:
                assert (low, high) == (declared["minimum"], declared["maximum"]), qualified

    def test_a_private_probe_point_never_reaches_the_document(self, document: CapabilityDocument) -> None:
        """The battery's two numbers and the array's power and shading publish no minimum or maximum."""
        measures = {entry["measure_id"]: entry for entry in document.body["measures"]}
        for qualified in ProbeSet.OPTION_PROBE_POINTS:
            measure_id, name = qualified.split(".")
            option = next(entry for entry in measures[measure_id]["options"] if entry["name"] == name)
            assert "minimum" not in option and "maximum" not in option, qualified


@pytest.mark.base
class TestNumericFields:
    """A numeric field publishes the request schema's bounds, never its probe points (PR #807)."""

    def test_a_numeric_field_publishes_exactly_the_schemas_bounds(self, document: CapabilityDocument) -> None:
        """Each of the four bound keywords the schema declares, under its own name; nothing invented, no ``values``."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}
        for path in ProbeSet.FIELD_PROBE_POINTS:
            full = f"{ProbeSet.HOUSE_PREFIX}{path}"
            entry, declared = fields[full], _request_schema_leaf(full)
            assert declared is not None, path
            assert "values" not in entry, path
            assert "enum" not in declared, path
            for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                if key in declared:
                    assert entry[key] == declared[key], (path, key)
                else:
                    assert key not in entry, (path, key)

    def test_the_exclusive_bounds_of_the_request_schema_are_published(self, document: CapabilityDocument) -> None:
        """hisim-9h7t: floor area, roof U-value, SCOP, collector area and the two device powers are > 0 or > 1."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}

        assert fields["house.building.absolute_conditioned_floor_area_in_m2"]["exclusiveMinimum"] == 0
        assert fields["house.building.roof.u_value_in_watt_per_m2_per_kelvin"]["exclusiveMinimum"] == 0
        assert fields["house.heating.heatpump_scop_en14825_w35"]["exclusiveMinimum"] == 1
        assert fields["house.solar_thermal_system.area_m2"]["exclusiveMinimum"] == 0
        assert fields["house.pv_system.power_in_watt"]["exclusiveMinimum"] == 0
        assert fields["house.pv_system.power_in_watt"]["maximum"] == 100000
        assert fields["house.battery.power_in_watt"]["exclusiveMinimum"] == 0
        assert "minimum" not in fields["house.battery.power_in_watt"]

    def test_only_a_probed_numeric_field_carries_bounds(self, document: CapabilityDocument) -> None:
        """A bound on any other entry would be one nobody derived from the schema."""
        numeric = {f"{ProbeSet.HOUSE_PREFIX}{path}" for path in ProbeSet.FIELD_PROBE_POINTS}
        for entry in document.body["fields"]:
            if set(entry) & {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}:
                assert entry["path"] in numeric, entry["path"]

    def test_an_enum_declared_field_publishes_the_schemas_enum(self, document: CapabilityDocument) -> None:
        """The three ``electric_vehicles`` fields are enums, not ranges, and read as such."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}
        for path in ProbeSet.FIELD_VALUES:
            full = f"{ProbeSet.HOUSE_PREFIX}{path}"
            declared = _request_schema_leaf(full)
            assert declared is not None, path
            if "enum" not in declared:
                continue  # a boolean, which lists (True, False) without the schema enumerating them
            assert [value["value"] for value in fields[full]["values"]] == declared["enum"], path
            assert "minimum" not in fields[full] and "maximum" not in fields[full], path

    def test_every_enum_declared_inventory_field_is_probed_per_value(self, document: CapabilityDocument) -> None:
        """An inventory enum the document lists without ``values`` would hide which values run."""
        for entry in document.body["fields"]:
            path = entry["path"]
            declared = _request_schema_leaf(path) if path.startswith(ProbeSet.HOUSE_PREFIX) else None
            if declared is not None and "enum" in declared:
                assert path.removeprefix(ProbeSet.HOUSE_PREFIX) in ProbeSet.FIELD_VALUES, path

    def test_every_probe_point_lies_inside_the_schema(self) -> None:
        """A probe the schema refuses would report a refusal rather than a capability."""
        for path, points in ProbeSet.FIELD_PROBE_POINTS.items():
            declared = _request_schema_leaf(f"{ProbeSet.HOUSE_PREFIX}{path}")
            assert declared is not None, path
            for point in points:
                assert point >= declared.get("minimum", point), (path, point)
                assert point <= declared.get("maximum", point), (path, point)
                assert point > declared.get("exclusiveMinimum", point - 1), (path, point)
                assert point < declared.get("exclusiveMaximum", point + 1), (path, point)

    def test_the_walker_reads_through_references_and_nullable_alternatives(self) -> None:
        """A bound behind ``$ref`` and a nullable ``oneOf`` is found; an exclusive one keeps its own keyword."""
        schema = {
            "properties": {"house": {"$ref": "#/$defs/house"}},
            "$defs": {
                "house": {"properties": {"block": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/block"}]}}},
                "block": {
                    "properties": {
                        "closed": {"oneOf": [{"type": "null"}, {"type": "number", "minimum": 2, "maximum": 9}]},
                        "open": {"type": "number", "exclusiveMinimum": 0},
                    }
                },
            },
        }

        assert RequestSchemaBounds.of("house.block.closed", schema) == {"minimum": 2, "maximum": 9}
        assert RequestSchemaBounds.of("house.block.open", schema) == {"exclusiveMinimum": 0}
        with pytest.raises(KeyError):
            RequestSchemaBounds.of("house.block.missing", schema)

    def test_a_path_is_either_enumerated_or_numeric(self) -> None:
        """The two tables are disjoint, so neither silently shadows the other in the probe set."""
        assert not set(ProbeSet.FIELD_VALUES) & set(ProbeSet.FIELD_PROBE_POINTS)
        assert len(ProbeSet.varied_fields()) == len(ProbeSet.FIELD_VALUES) + len(ProbeSet.FIELD_PROBE_POINTS)

    def test_an_enumerated_field_keeps_one_entry_per_value(self, document: CapabilityDocument) -> None:
        """Enumerations, booleans and the glazing-pane counts still list their values."""
        fields = {entry["path"]: entry for entry in document.body["fields"]}
        for path, values in ProbeSet.FIELD_VALUES.items():
            entry = fields[f"{ProbeSet.HOUSE_PREFIX}{path}"]
            assert [value["value"] for value in entry["values"]] == list(values), path
            assert "minimum" not in entry, path
