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
from typing import Dict

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
    ResultsSection,
    unlisted_lines,
)
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

    def test_the_section_validates_against_the_hisim_side_extension_schema(
        self, document: CapabilityDocument
    ) -> None:
        """A section nobody has agreed to yet is still a shape somebody wrote down."""
        ResultsSection.validate(document.body["results"])

    def test_a_section_with_an_unknown_key_is_refused(self) -> None:
        """``additionalProperties: false`` on the entry is what keeps the proposal a proposal."""
        from jsonschema import ValidationError

        section = ResultsSection.build()
        section["kpis"] = [{**section["kpis"][0], "invented": 1}]

        with pytest.raises(ValidationError):
            ResultsSection.validate(section)
