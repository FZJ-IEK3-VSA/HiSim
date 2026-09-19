"""The vocabularies carry exactly the strings the catalogue and the request schema spell.

Decision D-B made the measure catalogue's lowercase ``snake_case`` the wire format and the
*value* of every enum in :mod:`hisim.renovisor.vocabulary`. That is only worth anything while
the two stay identical, so these tests read the vendored ``measures.yaml`` and
``calculation-request.schema.json`` and compare them member for member. A catalogue value added
tomorrow is a failing test today, which is what "invent none" means in practice.
"""

from typing import Any, Dict, List, Tuple

import pytest

from hisim.components.heat_distribution_system import HeatDistributionSystemType
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.vocabulary import (
    AirTightness,
    BuildingType,
    CollectorType,
    Country,
    FrameMaterial,
    HeatDistributionType,
    HeatGenerator,
    HotWaterSupply,
    ReportStatus,
    RoofShape,
    SolarThermalSupplies,
    TemperatureControl,
    ThermalElement,
    VentilationType,
    WhiteAppliances,
)


def catalogue_values(measure_id: str, option_name: str) -> List[Any]:
    """Return the accepted values of one catalogue option, from the vendored file."""
    for measure in ContractFiles.measures()["measures"]:
        if measure["id"] != measure_id:
            continue
        for option in measure["options"] or []:
            if option["name"] == option_name:
                return list(option["values"])
    raise AssertionError(f"measures.yaml has no {measure_id}.{option_name}")


def schema_enum(*path: str) -> List[Any]:
    """Return the ``enum`` array of one definition of the vendored request schema."""
    node: Dict[str, Any] = ContractFiles.request_schema()["$defs"]
    for part in path[:-1]:
        node = node[part]
    return list(node[path[-1]]["enum"])


#: Every vocabulary that mirrors one catalogue option, with the option it mirrors.
FROM_CATALOGUE: Tuple[Tuple[Any, str, str], ...] = (
    (HeatDistributionType, "heating_installation", "type_of_system"),
    (HotWaterSupply, "hot_water_system", "supply"),
    (TemperatureControl, "temperature_control_system", "type_of_system"),
    (SolarThermalSupplies, "solar_thermal_system", "supplies"),
    (FrameMaterial, "window_replacement", "frame_material"),
)


@pytest.mark.base
class TestTheCatalogueIsTheVocabulary:
    """Every enum value is a string the catalogue or the request schema spells."""

    @pytest.mark.parametrize("vocabulary, measure_id, option_name", FROM_CATALOGUE)
    def test_a_vocabulary_equals_its_catalogue_option(
        self, vocabulary: Any, measure_id: str, option_name: str
    ) -> None:
        """The enum's values are the option's values, in the option's own order."""
        assert [member.value for member in vocabulary] == catalogue_values(measure_id, option_name)

    def test_the_heat_generators_are_the_catalogue_plus_the_one_inventory_only_value(self) -> None:
        """Sixteen values a measure can install, plus ``solid_fuel_heating`` (F-spec §3.6)."""
        catalogue = catalogue_values("heating_system", "type_of_system")
        assert [member.value for member in HeatGenerator] == catalogue + ["solid_fuel_heating"]

    def test_the_ventilation_types_are_the_catalogue_plus_natural(self) -> None:
        """Only an existing system can be naturally ventilated, so no measure installs it."""
        catalogue = catalogue_values("ventilation_system", "type_of_system")
        assert set(member.value for member in VentilationType) == set(catalogue) | {"natural"}

    @pytest.mark.parametrize(
        "vocabulary, path",
        [
            (Country, ("location", "properties", "country")),
            (BuildingType, ("building", "properties", "building_type")),
            (RoofShape, ("roof", "properties", "shape")),
            (AirTightness, ("ventilation", "properties", "air_tightness")),
            (WhiteAppliances, ("appliances", "properties", "white_appliances")),
            (CollectorType, ("solar_thermal_system", "properties", "collector_type")),
            (HotWaterSupply, ("hot_water", "properties", "supply")),
            (VentilationType, ("ventilation", "properties", "type_of_system")),
            (HeatGenerator, ("heating", "properties", "type_of_system")),
            (HeatDistributionType, ("heat_distribution", "properties", "type_of_system")),
            (SolarThermalSupplies, ("solar_thermal_system", "properties", "supplies")),
            (FrameMaterial, ("window", "properties", "frame_material")),
        ],
    )
    def test_a_vocabulary_equals_its_request_schema_enum(self, vocabulary: Any, path: Any) -> None:
        """The enum's values are the schema's, in the schema's own order."""
        assert [member.value for member in vocabulary] == schema_enum(*path)


@pytest.mark.base
class TestTheMappingsThatAreEnumDefinitions:
    """The one place a request value becomes a HiSim member is the member itself."""

    def test_every_heat_distribution_value_names_a_hisim_member(self) -> None:
        """§3.7 of the contract, held as a property rather than as a lookup table."""
        assert HeatDistributionType.SURFACE_HEATING.hisim_member is HeatDistributionSystemType.FLOORHEATING
        assert (
            HeatDistributionType.LOW_TEMPERATURE_RADIATOR.hisim_member
            is HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR
        )
        assert HeatDistributionType.CONVENTIONAL_RADIATOR.hisim_member is HeatDistributionSystemType.RADIATOR

    def test_every_member_name_is_hisims_own_spelling(self) -> None:
        """Decision D-B: the catalogue's case on the wire, HiSim's case in the code."""
        for vocabulary in (HeatGenerator, HeatDistributionType, HotWaterSupply, VentilationType):
            for member in vocabulary:
                assert member.name == member.name.upper()

    def test_the_five_elements_are_the_request_blocks_own_names(self) -> None:
        """An element is its own path segment, so nothing has to translate between the two."""
        building = ContractFiles.request_schema()["$defs"]["building"]["properties"]
        assert {element.value for element in ThermalElement} <= set(building)


@pytest.mark.base
class TestReportStatus:
    """The four statuses, and the order the capability document aggregates them in."""

    def test_the_worst_of_several_statuses_is_the_one_that_kept_least_of_the_request(self) -> None:
        """``used`` < ``defaulted`` < ``approximated`` < ``not_implemented_yet``."""
        assert ReportStatus.worst_of(ReportStatus.USED, ReportStatus.DEFAULTED) is ReportStatus.DEFAULTED
        assert (
            ReportStatus.worst_of(ReportStatus.DEFAULTED, ReportStatus.APPROXIMATED)
            is ReportStatus.APPROXIMATED
        )
        assert (
            ReportStatus.worst_of(ReportStatus.APPROXIMATED, ReportStatus.NOT_IMPLEMENTED_YET)
            is ReportStatus.NOT_IMPLEMENTED_YET
        )
        assert ReportStatus.worst_of(ReportStatus.USED) is ReportStatus.USED

    def test_the_worst_of_nothing_is_a_question_with_no_answer(self) -> None:
        """There is no neutral status, so an empty aggregation is a bug rather than ``used``."""
        with pytest.raises(ValueError):
            ReportStatus.worst_of()
