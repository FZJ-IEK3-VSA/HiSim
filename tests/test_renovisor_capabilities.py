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

from hisim.renovisor.capabilities import (
    Aggregation,
    CapabilityDocument,
    MeasureStatus,
    ProbeKind,
    ProbeRunner,
    ProbeSet,
    unlisted_lines,
)
from hisim.renovisor.request import CatalogueTable
from hisim.renovisor.vocabulary import ReportStatus

#: The tally of §4.2 as decision D-D leaves it. The frontend side's spec counted 16 / 9 / 7;
#: `air_conditioners`, `temperature_control_system` and
#: `optimize_behaviour_for_self_consumption_of_pv` move to the list because the twins have no
#: group for them, which is three measures off `supported` and one off `approximated`.
EXPECTED_TALLY: Dict[str, int] = {"supported": 15, "approximated": 7, "not_implemented_yet": 10}

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


@pytest.fixture(scope="module")
def document() -> CapabilityDocument:
    """Build the capability document once; every test in this module reads the same probe run."""
    return CapabilityDocument.build(generated_at="2026-09-19T00:00:00+00:00")


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
                if option.name == CatalogueTable.MATERIAL:
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
        assert unlisted_lines(document.results) == ()

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

    def test_the_only_non_deterministic_field_can_be_overridden(self, tmp_path: Path) -> None:
        """So that a test, and a reproducible build, can compare two documents byte for byte."""
        first = CapabilityDocument.build(generated_at="2026-09-19T00:00:00+00:00")
        path = tmp_path / "capabilities.json"

        assert first.write(path) == 0
        assert first.body["translator"]["generated_at"] == "2026-09-19T00:00:00+00:00"
        assert path.read_text(encoding="utf-8").endswith("\n")


@pytest.mark.base
class TestTheRunner:
    """Every probe goes through validate, apply and translate, and nothing else."""

    def test_a_refused_probe_is_a_result_rather_than_an_exception(self) -> None:
        """``ES`` has no TABULA typology; that is a refusal the document records, not a crash."""
        from hisim.renovisor.capabilities import Probe

        probe = Probe(name="field:location.country=ES", kind=ProbeKind.FIELD)
        probe = Probe(
            name=probe.name, kind=ProbeKind.FIELD, location={"country": "ES"}, subject="location.country"
        )

        results = ProbeRunner().run([probe])

        assert results[0].refused == ("location.country.unsupported",)
