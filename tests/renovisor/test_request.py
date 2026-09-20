"""T-SCHEMA, T-VAL and T-CAT: what a valid calculation request is, and how it is refused.

Three things are proved here. **T-SCHEMA**: the vendored mockup validates against the vendored
schema, and a generated corpus of one-key mutations of it -- each key deleted, each enum swapped
for a wrong value, each number pushed past each bound, an unknown key added at each object -- is
refused with the expected code at the expected path. The frontend side's spec expected a
pydantic mirror and a test proving the mirror agrees with the schema; validating against the
schema itself leaves nothing to prove, so the corpus is what the test became.

**T-VAL**: every code of §7 of the contract has a request producing it, all problems are
reported at once, and ``validate`` and ``run`` agree. **T-CAT**: the frozen catalogue table in
:mod:`hisim.renovisor.request` equals the vendored ``measures.yaml``.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from hisim.renovisor.capabilities import assert_catalogue_matches
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.economics import EconomicContextBuilder
from hisim.renovisor.request import (
    AccessLevel,
    CatalogueTable,
    ProblemCode,
    Request,
    RequestError,
    SemanticChecks,
    ValueType,
)


def mockup() -> Dict[str, Any]:
    """Return a fresh copy of the vendored mockup."""
    return copy.deepcopy(ContractFiles.request_mockup())


def codes_of(document: Any) -> List[Tuple[str, str]]:
    """Return ``(path, code)`` for every problem of one document, or an empty list when valid."""
    try:
        Request.parse(document)
    except RequestError as error:
        return [(problem.path, problem.code.value) for problem in error.problems]
    return []


def objects_of(document: Any, prefix: str = "") -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield every object of a document with its dotted path, for the mutation corpus."""
    if isinstance(document, dict):
        yield prefix, document
        for key, value in document.items():
            yield from objects_of(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(document, list):
        for index, item in enumerate(document):
            yield from objects_of(item, f"{prefix}[{index}]")


@pytest.mark.base
class TestTheSchemaIsTheTruth:
    """T-SCHEMA: the mockup validates, and one-key mutations of it do not."""

    def test_the_vendored_mockup_is_a_valid_request(self) -> None:
        """The one example both sides of the contract point at has to be one."""
        request = Request.parse(mockup())

        assert request.schema_version == 1
        assert request.country.value == "IE"
        assert len(request.measures) == 5

    def test_deleting_any_required_key_is_refused_by_name(self) -> None:
        """Every required leaf of the mockup, removed one at a time, is ``required.missing``."""
        required = [
            ("house.building", "building_type"),
            ("house.building", "construction_year"),
            ("house.building", "absolute_conditioned_floor_area_in_m2"),
            ("house.building", "set_heating_temperature_in_celsius"),
            ("house.building.facade", "u_value_in_watt_per_m2_per_kelvin"),
            ("house.occupancy", "number_of_residents"),
            ("house.heating", "type_of_system"),
            ("house.heat_distribution", "type_of_system"),
        ]
        for parent, key in required:
            document = mockup()
            node: Any = document
            for part in parent.split("."):
                node = node[part]
            del node[key]

            problems = codes_of(document)

            assert (f"{parent}.{key}", ProblemCode.REQUIRED_MISSING.value) in problems

    def test_an_unknown_key_at_any_object_is_refused_at_that_object(self) -> None:
        """``additionalProperties: false`` throughout; the path names the key, not its parent."""
        for path, _ in objects_of(mockup()):
            if path.startswith("measures"):
                continue  # a measure's options are an open map by design
            document = mockup()
            node: Any = document
            for part in [item for item in path.split(".") if item]:
                node = node[part]
            node["nonsense_key"] = 1

            problems = codes_of(document)

            expected = f"{path}.nonsense_key" if path else "nonsense_key"
            assert (expected, ProblemCode.KEY_UNKNOWN.value) in problems, path

    @pytest.mark.parametrize(
        "path, value",
        [
            ("house.heating.type_of_system", "coal_fired_dragon"),
            ("house.heat_distribution.type_of_system", "steel_panel_radiators"),
            ("house.building.building_type", "castle"),
            ("house.building.roof.shape", "domed"),
        ],
    )
    def test_an_unknown_enum_value_is_refused_with_the_accepted_list(
        self, path: str, value: str
    ) -> None:
        """A frontend that sends a stale vocabulary is told which words this build knows."""
        document = mockup()
        node: Any = document
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

        try:
            Request.parse(document)
            raise AssertionError("expected a refusal")
        except RequestError as error:
            problem = next(item for item in error.problems if item.path == path)
            assert problem.code is ProblemCode.ENUM_UNKNOWN
            assert problem.accepted

    @pytest.mark.parametrize(
        "path, value",
        [
            ("house.building.construction_year", 1600),
            ("house.building.construction_year", 2200),
            ("house.building.set_heating_temperature_in_celsius", 11),
            ("house.building.set_heating_temperature_in_celsius", 29),
            ("house.building.absolute_conditioned_floor_area_in_m2", 0),
            ("house.building.facade.u_value_in_watt_per_m2_per_kelvin", 11),
            ("house.occupancy.number_of_residents", 13),
        ],
    )
    def test_a_number_past_a_bound_is_refused_at_that_bound(self, path: str, value: Any) -> None:
        """Every ``minimum``/``maximum`` of the schema is a refusal, not a clamp."""
        document = mockup()
        node: Any = document
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

        assert (path, ProblemCode.RANGE_EXCEEDED.value) in codes_of(document)

    def test_a_wrong_type_is_refused_as_a_type(self) -> None:
        """A string where a number belongs is a type fault, not an out-of-range number."""
        document = mockup()
        document["house"]["building"]["construction_year"] = "nineteen seventy five"

        assert (
            "house.building.construction_year",
            ProblemCode.TYPE_INVALID.value,
        ) in codes_of(document)

    def test_an_unsupported_schema_version_is_named_as_such(self) -> None:
        """The release implements one version, and says which when it is handed another."""
        document = mockup()
        document["schema_version"] = 2

        assert ("schema_version", ProblemCode.SCHEMA_VERSION_UNSUPPORTED.value) in codes_of(document)

    def test_a_device_sized_two_ways_at_once_violates_its_choice(self) -> None:
        """``pv_system`` is sized by exactly one of power and roof share, never both."""
        document = mockup()
        document["house"]["pv_system"] = {"power_in_watt": 4000, "size_in_percent_of_roof_area": 60}

        assert ("house.pv_system", ProblemCode.ONEOF_VIOLATED.value) in codes_of(document)


@pytest.mark.base
class TestTheSemanticChecks:
    """T-VAL: every code of §7 is reachable, and all of them are reported at once."""

    def test_an_unknown_measure_is_refused_with_the_catalogue(self) -> None:
        """A measure id the catalogue does not have is a refusal naming the 32 that it does."""
        document = mockup()
        document["measures"] = [{"id": "install_a_wind_turbine"}]

        try:
            Request.parse(document)
            raise AssertionError("expected a refusal")
        except RequestError as error:
            problem = error.problems[0]
            assert problem.code is ProblemCode.MEASURE_UNKNOWN
            assert problem.accepted is not None and len(problem.accepted) == 32

    def test_one_measure_twice_is_refused_naming_both_positions(self) -> None:
        """A package applies each measure at most once; two of them is a contradiction."""
        document = mockup()
        document["measures"] = [
            {"id": "outside_shading"},
            {"id": "outside_shading"},
        ]

        problems = codes_of(document)

        assert ("measures[1].id", ProblemCode.MEASURE_DUPLICATE.value) in problems

    def test_an_option_the_measure_does_not_have_is_refused(self) -> None:
        """The frozen table knows every option of every measure, so a typo is named."""
        document = mockup()
        document["measures"] = [{"id": "outside_shading", "options": {"colour": "blue"}}]

        assert (
            "measures[0].options.colour",
            ProblemCode.MEASURE_OPTION_UNKNOWN.value,
        ) in codes_of(document)

    def test_a_missing_everyone_option_is_refused(self) -> None:
        """An ``experts`` option may be absent; an ``everyone`` option may not."""
        document = mockup()
        document["measures"] = [{"id": "heating_system", "options": {}}]

        assert (
            "measures[0].options.type_of_system",
            ProblemCode.MEASURE_OPTION_MISSING.value,
        ) in codes_of(document)

    def test_an_option_value_outside_the_catalogue_is_refused_with_the_list(self) -> None:
        """The catalogue's ``values`` are the accepted set, and the answer says what they are."""
        document = mockup()
        document["measures"] = [
            {"id": "heating_installation", "options": {"type_of_system": "cast_iron"}}
        ]

        try:
            Request.parse(document)
            raise AssertionError("expected a refusal")
        except RequestError as error:
            problem = error.problems[0]
            assert problem.code is ProblemCode.MEASURE_OPTION_VALUE_UNKNOWN
            assert problem.accepted == ("surface_heating", "low_temperature_radiator", "conventional_radiator")

    def test_added_insulation_in_a_request_is_refused(self) -> None:
        """``added_insulation`` is written by ``apply``; a request states the U-value instead."""
        document = mockup()
        document["house"]["building"]["facade"]["added_insulation"] = {
            "placement": "external_wall_external",
            "thickness_in_mm": 100,
            "material": {"asp_id": "x", "thermal_conductivity_w_mk": 0.04},
        }

        assert (
            "house.building.facade.added_insulation",
            ProblemCode.ADDED_INSULATION_NOT_ALLOWED.value,
        ) in codes_of(document)

    def test_a_country_with_no_tabula_typology_is_refused(self) -> None:
        """Spain is a data gap, not a feature gap, and stays a refusal until a typology is chosen."""
        document = mockup()
        document["location"]["country"] = "ES"

        assert (
            "location.country",
            ProblemCode.LOCATION_COUNTRY_UNSUPPORTED.value,
        ) in codes_of(document)

    def test_two_volumes_for_one_storage_are_refused(self) -> None:
        """The house has one hot-water storage; two different volumes describe two houses."""
        document = mockup()
        document["house"]["hot_water"] = {
            "supply": "together_with_heating_system",
            "volume_heating_water_storage_in_liter": 300,
        }
        document["house"]["solar_thermal_system"] = {"supplies": "dhw_only", "storage_volume_in_liter": 500}

        assert (
            "house.solar_thermal_system.storage_volume_in_liter",
            ProblemCode.HOT_WATER_CONFLICTING_VOLUMES.value,
        ) in codes_of(document)

    def test_the_same_two_volumes_are_accepted(self) -> None:
        """Agreeing twice is not a contradiction."""
        document = mockup()
        document["house"]["hot_water"] = {
            "supply": "together_with_heating_system",
            "volume_heating_water_storage_in_liter": 300,
        }
        document["house"]["solar_thermal_system"] = {"supplies": "dhw_only", "storage_volume_in_liter": 300}

        assert codes_of(document) == []

    def test_a_dwelling_the_index_cannot_place_is_refused(self) -> None:
        """An apartment built in 1700 has no Irish typology row and no usable neighbour."""
        document = mockup()
        document["house"]["building"]["tabula_building_code"] = "IE.N.SFH.05.Gen.ReEx.001.001"

        assert (
            "house.building.tabula_building_code",
            ProblemCode.TABULA_UNRESOLVABLE.value,
        ) in codes_of(document)

    def test_a_measure_pushing_a_value_past_its_range_is_refused(self) -> None:
        """Two ranges a measure can break that the schema cannot see: cars and the set point."""
        document = mockup()
        document["measures"] = [
            {"id": "electric_vehicle", "options": {"number": 5}},
            {"id": "change_room_temperature", "options": {"new_room_temperature": 40}},
        ]

        problems = codes_of(document)

        assert ("measures[0].options.number", ProblemCode.RANGE_EXCEEDED.value) in problems
        assert (
            "measures[1].options.new_room_temperature",
            ProblemCode.RANGE_EXCEEDED.value,
        ) in problems

    def test_a_material_without_a_positive_conductivity_is_refused(self) -> None:
        """The one thing checked about a material is the one thing the physics needs."""
        document = mockup()
        document["measures"] = [
            {
                "id": "external_insulation",
                "options": {"material": {"asp_id": "stub", "thermal_conductivity_w_mk": 0}},
            }
        ]

        problems = codes_of(document)

        assert any(code == ProblemCode.TYPE_INVALID.value for _, code in problems)

    def test_a_material_the_database_has_never_heard_of_is_accepted(self) -> None:
        """Rule 5: ``asp_id`` is provenance, and the backend reads no material catalogue."""
        document = mockup()
        document["measures"] = [
            {
                "id": "external_insulation",
                "options": {
                    "material": {"asp_id": "unicorn_wool", "thermal_conductivity_w_mk": 0.031}
                },
            }
        ]

        assert codes_of(document) == []

    def test_every_problem_is_reported_at_once(self) -> None:
        """A frontend fixing one field per round trip is the slow way to learn a form is wrong."""
        document = mockup()
        document["location"]["country"] = "ES"
        document["measures"] = [{"id": "no_such_measure"}, {"id": "outside_shading"}, {"id": "outside_shading"}]

        problems = codes_of(document)
        codes = {code for _, code in problems}

        assert ProblemCode.LOCATION_COUNTRY_UNSUPPORTED.value in codes
        assert ProblemCode.MEASURE_UNKNOWN.value in codes
        assert ProblemCode.MEASURE_DUPLICATE.value in codes

    def test_the_problems_document_is_the_shape_the_contract_names(self) -> None:
        """``{"problems": [{"path", "code", "message", "accepted"?}]}`` and nothing else."""
        document = mockup()
        document["house"]["heating"]["type_of_system"] = "coal_fired_dragon"

        try:
            Request.parse(document)
            raise AssertionError("expected a refusal")
        except RequestError as error:
            body = error.to_json()
            assert set(body) == {"problems"}
            first = body["problems"][0]
            assert set(first) >= {"path", "code", "message"}
            assert json.dumps(body)


@pytest.mark.base
class TestTheFrozenCatalogue:
    """T-CAT: the table in code equals the vendored file, or the build fails by name."""

    def test_the_frozen_table_equals_the_vendored_measures_file(self) -> None:
        """Ids, option names, access levels and value lists, all four."""
        assert_catalogue_matches()

    def test_a_catalogue_that_drifted_fails_by_name(self, tmp_path: Path) -> None:
        """The check is the whole reason the table is frozen rather than read at request time."""
        import yaml

        catalogue = copy.deepcopy(ContractFiles.measures())
        catalogue["measures"].append({"id": "a_new_measure", "options": []})
        path = tmp_path / "measures.yaml"
        path.write_text(yaml.safe_dump(catalogue), encoding="utf-8")

        with pytest.raises(AssertionError) as raised:
            assert_catalogue_matches(path)
        assert "a_new_measure" in str(raised.value)

    def test_every_catalogue_id_has_options_of_the_two_access_levels_only(self) -> None:
        """``everyone`` and ``experts`` are the whole vocabulary; a third would be silent."""
        for measure_id in CatalogueTable.ids():
            for option in CatalogueTable.options_of(measure_id):
                assert option.access_level in (AccessLevel.EVERYONE, AccessLevel.EXPERTS)
                assert option.value_type in tuple(ValueType)


@pytest.mark.base
class TestTheContentHash:
    """The file name is the content, so two identical requests are one file."""

    def test_key_order_does_not_change_the_hash(self) -> None:
        """The canonical body sorts keys recursively, so formatting cannot split one job in two."""
        first = Request.parse(mockup())
        reordered = json.loads(json.dumps(mockup(), sort_keys=True))
        second = Request.parse(reordered)

        assert first.content_hash() == second.content_hash()

    def test_a_different_house_is_a_different_hash(self) -> None:
        """One changed number is one different calculation."""
        first = Request.parse(mockup())
        other = mockup()
        other["house"]["building"]["absolute_conditioned_floor_area_in_m2"] = 141

        assert Request.parse(other).content_hash() != first.content_hash()

    def test_the_hash_is_sixteen_hexadecimal_characters(self) -> None:
        """The first 16 hex of the SHA-256, which is what the backend's job id is built from."""
        content_hash = Request.parse(mockup()).content_hash()

        assert len(content_hash) == 16
        assert all(character in "0123456789abcdef" for character in content_hash)


class TestTheMeasurePriceBand:
    """T-VAL: a ``measures[i].cost`` block that is not a price band is a request problem.

    The block is the E-spec §7 proposal the vendored schema has not adopted yet (findings
    F7/F10), so a request carrying one does not get past the schema; the semantic checks are
    therefore driven directly here, which is the same way the economic-context tests exercise the
    other unadopted field. What the checks decide is what happens the day the schema grows the
    block: an inverted band raises out of the middle of a translation and a negative one prices a
    renovation that pays the owner, so both are refused before anything is translated, with exit 2
    and a ``problems.json``.
    """

    @staticmethod
    def _with_cost(block: Dict[str, Any]) -> Dict[str, Any]:
        """The mockup with one price block on its first measure."""
        document = mockup()
        document["measures"] = [dict(document["measures"][0], cost=block)]
        return document

    def test_a_valid_band_is_accepted(self) -> None:
        """The ordinary case: a cheap end, an expensive end, and nothing to say about them."""
        problems = SemanticChecks.of(
            self._with_cost({"min_in_euro_per_m2": 100.0, "max_in_euro_per_m2": 200.0})
        )

        assert [problem for problem in problems if problem.code is ProblemCode.MEASURE_COST_BAND_INVALID] == []

    def test_a_band_whose_cheap_end_is_above_its_expensive_end_is_refused(self) -> None:
        """``UncertainValue`` refuses it mid-translation; the request says so first."""
        problems = SemanticChecks.of(
            self._with_cost({"min_in_euro_per_m2": 200.0, "max_in_euro_per_m2": 100.0})
        )

        assert len(problems) == 1
        assert problems[0].code is ProblemCode.MEASURE_COST_BAND_INVALID
        assert problems[0].path == "measures[0].cost"
        assert "cheap end" in problems[0].message

    @pytest.mark.parametrize(
        "block",
        [
            {"min_in_euro_per_m2": -10.0, "max_in_euro_per_m2": 200.0},
            {"min_in_euro_per_m2": 100.0, "max_in_euro_per_m2": -200.0},
        ],
        ids=["negative minimum", "negative maximum"],
    )
    def test_a_negative_price_is_refused(self, block: Dict[str, Any]) -> None:
        """A renovation that pays the owner per square metre is not a price band."""
        problems = SemanticChecks.of(self._with_cost(block))

        assert [problem.code for problem in problems] == [ProblemCode.MEASURE_COST_BAND_INVALID]
        assert "negative" in problems[0].message

    def test_a_non_finite_price_is_refused(self) -> None:
        """``inf`` survives JSON in Python and would make every total infinite."""
        problems = SemanticChecks.of(
            self._with_cost({"min_in_euro_per_m2": 0.0, "max_in_euro_per_m2": float("inf")})
        )

        assert [problem.code for problem in problems] == [ProblemCode.MEASURE_COST_BAND_INVALID]
        assert "finite" in problems[0].message

    def test_every_bad_band_is_reported_at_once(self) -> None:
        """A frontend fixing one measure per round trip is the slow way to fix a form."""
        document = mockup()
        document["measures"] = [
            dict(document["measures"][0], cost={"min_in_euro_per_m2": 5.0, "max_in_euro_per_m2": 1.0}),
            dict(document["measures"][1], cost={"min_in_euro_per_m2": -1.0, "max_in_euro_per_m2": 1.0}),
        ]
        problems = SemanticChecks.of(document)

        assert [problem.path for problem in problems] == ["measures[0].cost", "measures[1].cost"]

    def test_a_measure_without_a_block_is_not_a_problem(self) -> None:
        """An absent price makes the measure unpriced, which the document states rather than hides."""
        assert not SemanticChecks.of(mockup())

    def test_the_two_sides_spell_the_block_the_same_way(self) -> None:
        """The check and the reader of the block are in two modules and must agree on three keys."""
        assert SemanticChecks.COST_KEY == EconomicContextBuilder.COST_KEY
        assert SemanticChecks.COST_MINIMUM_KEY == EconomicContextBuilder.COST_MINIMUM_KEY
        assert SemanticChecks.COST_MAXIMUM_KEY == EconomicContextBuilder.COST_MAXIMUM_KEY
