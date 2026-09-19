"""T-CTX: what the translator tells the lifecycle cost engine about the dwelling.

The economic context is half of the money's inputs and none of it comes from the simulation: what
stood in the cellar, how old it is, which measure replaces what, how many square metres a layer
covers, what it cost and who is applying. Each of those is asserted here on the vendored mockup,
because a wrong entry does not fail a run — it produces a plausible number for a different
building.

The builder is driven directly rather than through a run: it reads a validated request, an applied
package and the ``Building`` config the translator wrote, all three of which a test can produce in
milliseconds, and nothing else.
"""

import copy
import dataclasses
import os
from typing import Any, Dict, Optional

import pytest

from hisim.economics.carriers import EnergyCarrier
from hisim.economics.facts import ExistingAssetRegister
from hisim.loadtypes import ComponentType, Units
from hisim.simulationparameters import SimulationParameters
from hisim.renovisor.apply import apply
from hisim.renovisor.constants import AnywayShareByPlacement
from hisim.renovisor.contract import ContractFiles
from hisim.economics.subsidies import DwellingType, SubsidyCatalog
from hisim.renovisor.economics import (
    DwellingTypes,
    EconomicContextBuilder,
    EnvelopeAssets,
    GeneratorAssets,
)
from hisim.renovisor.request import Request
from hisim.renovisor.simulation import EconomicSetup, SubsidyCatalogue
from hisim.renovisor.vocabulary import BuildingType, HeatGenerator, ThermalElement
from hisim.renovisor.whitelist import Whitelist

pytestmark = pytest.mark.base


def _mockup() -> Dict[str, Any]:
    """The vendored mockup request, as a fresh document."""
    return copy.deepcopy(ContractFiles.request_mockup())


def _built(document: Dict[str, Any], building_config: Optional[Dict[str, Any]] = None) -> Any:
    """Run the builder over one request document.

    Args:
        document: The request, which is validated here exactly as a run validates it.
        building_config: The ``Building`` config the translator would have written; the mockup's
            own conditioned floor area and one facade area when omitted, which is enough to size
            the facade measure and to leave the other elements unsized.

    Returns:
        The :class:`~hisim.renovisor.economics.EconomicContextResult`.
    """
    request = Request.parse(document)
    applied = apply(request.document["house"], request.measures, Whitelist.load())
    config = building_config if building_config is not None else {
        "absolute_conditioned_floor_area_in_m2": 140.0,
        "facade_area_in_m2": 173.0,
    }
    return EconomicContextBuilder(request, applied, config).build()


def _builder(document: Dict[str, Any], with_cost_block: bool = False) -> EconomicContextBuilder:
    """The builder over one request, optionally with a ``cost`` block the schema does not allow.

    Two fields of E-spec §7 -- ``installation_year`` and ``measures[i].cost`` -- are additive
    proposals the vendored request schema has not adopted yet (findings F7/F10), so a request
    carrying either is refused by the validator. The builder is supposed to read them the moment
    they arrive, and this is how that behaviour is exercised in the meantime: validate the request
    as it stands, then hand the builder the document the frontend will send.

    Args:
        document: The request.
        with_cost_block: Whether to add a price band to the first envelope measure.

    Returns:
        The builder.
    """
    request = Request.parse(document)
    raw = dict(request.document)
    if with_cost_block:
        raw["measures"] = [
            dict(measure, cost={"min_in_euro_per_m2": 100.0, "max_in_euro_per_m2": 200.0})
            if measure.get("id") == "external_insulation"
            else measure
            for measure in raw["measures"]
        ]
        request = dataclasses.replace(request, document=raw)
    applied = apply(request.document["house"], request.measures, Whitelist.load())
    return EconomicContextBuilder(request, applied, {"facade_area_in_m2": 173.0})


class TestTheRegister:
    """What was already in the building, how old it is, and what supersedes it."""

    def test_the_gas_boiler_is_in_the_register_with_its_carrier(self) -> None:
        """The mockup's dwelling heats with conventional gas, which is what the register says.

        The carrier is not decoration: the subsidy conditions of a heat-pump grant ask whether the
        building has a functioning *fossil* heating system, and this entry is the only thing that
        can answer them.
        """
        register = _built(_mockup()).context.existing_assets
        assert isinstance(register, ExistingAssetRegister)
        boiler = register.find(ComponentType.GAS_HEATER)
        assert boiler is not None
        assert boiler.energy_carrier is EnergyCarrier.NATURAL_GAS
        assert boiler.is_functional is True

    def test_the_boilers_age_is_the_buildings_construction_year(self) -> None:
        """The request states no installation year, so the asset is as old as the building."""
        document = _mockup()
        register = _built(document).context.existing_assets
        boiler = register.find(ComponentType.GAS_HEATER)
        assert boiler.installation_year == document["house"]["building"]["construction_year"]

    def test_a_stated_installation_year_wins_over_the_construction_year(self) -> None:
        """E-spec §7's additive field: read when present, defaulted to the construction year.

        The field is not in the vendored request schema yet (findings F7/F10), so a request
        carrying it does not validate and the case is driven through the builder the translator
        holds rather than through a request the validator would reject. The moment the schema
        grows the field, this is the behaviour it gets.
        """
        builder = _builder(_mockup())
        # pylint: disable=protected-access
        assert builder._year_of({"installation_year": 2008}) == 2008
        assert builder._year_of({}) == _mockup()["house"]["building"]["construction_year"]

    def test_the_heating_measure_declares_what_replaces_the_boiler(self) -> None:
        """Without it the engine would keep the boiler and install the heat pump beside it."""
        register = _built(_mockup()).context.existing_assets
        boiler = register.find(ComponentType.GAS_HEATER)
        assert ComponentType.HEAT_PUMP in boiler.replaced_by_asset_classes

    def test_the_five_envelope_elements_are_registered_where_an_area_is_known(self) -> None:
        """An element with no area anywhere is not in the register rather than in it at a guess."""
        config = {
            f"{element.value}_area_in_m2": 100.0 for element in ThermalElement
        }
        register = _built(_mockup(), config).context.existing_assets
        classes = {asset.asset_class for asset in register.assets}
        for element in ThermalElement:
            assert EnvelopeAssets.of_element(element) in classes

    def test_an_insulated_element_carries_the_anyway_share_of_its_placement(self) -> None:
        """First-time external insulation is credited with the render-and-scaffolding share only."""
        register = _built(_mockup()).context.existing_assets
        facade = register.find(EnvelopeAssets.of_element(ThermalElement.FACADE))
        assert facade is not None
        assert facade.anyway_share == AnywayShareByPlacement.EXTERNAL_FIRST_TIME
        assert facade.size_unit is Units.SQUARE_METER

    def test_a_device_the_request_does_not_carry_is_not_in_the_register(self) -> None:
        """The mockup's dwelling has no array, which is a different statement from one of unknown size."""
        register = _built(_mockup()).context.existing_assets
        assert register.find(ComponentType.PV) is None

    def test_an_existing_photovoltaic_array_is_in_the_register_and_is_replaced(self) -> None:
        """A device that is there must not be bought again, and the measure that renews it says so."""
        document = _mockup()
        document["house"]["pv_system"] = {"power_in_watt": 4000}
        document["measures"] = [{"id": "photovoltaic_system", "options": {"size_in_percent_of_roof_area": 50}}]
        register = _built(document).context.existing_assets
        array = register.find(ComponentType.PV)
        assert array is not None
        assert array.replaced_by_asset_classes == [ComponentType.PV]


class TestTheEnvelopeCostSubjects:
    """One cost subject per envelope measure, priced from the request or flagged unpriced."""

    def test_every_envelope_measure_becomes_a_cost_subject(self) -> None:
        """A measure the plan carries out has to appear in the economics, priced or not."""
        built = _built(_mockup())
        subjects = {facts.subject for facts in built.context.extra_cost_facts}
        assert "external_insulation" in subjects

    def test_a_measure_without_a_cost_block_is_unpriced_rather_than_free(self) -> None:
        """Step 10 §1: the document says what it does not know instead of hiding the measure."""
        built = _built(_mockup())
        assert "external_insulation" in built.unpriced_subjects
        facts = {entry.subject: entry.facts for entry in built.context.extra_cost_facts}
        assert facts["external_insulation"].investment_cost_override_in_euro.best_estimate == 0.0

    def test_a_cost_block_on_the_measure_prices_the_subject_per_square_metre(self) -> None:
        """The frontend copies the price out of the contract; the translator reads no catalogue.

        Like the installation year, the ``cost`` block is not in the vendored schema yet, so the
        document the builder reads is edited after validation rather than before it.
        """
        builder = _builder(_mockup(), with_cost_block=True)
        # pylint: disable=protected-access
        assert builder._measure_price("external_insulation") == (100.0, 200.0)
        assert builder._measure_price("window_replacement") is None

    def test_the_subject_is_sized_in_square_metres_of_its_element(self) -> None:
        """A price per square metre without a square metre is a wrong answer, not a smaller one."""
        built = _built(_mockup(), {"facade_area_in_m2": 173.0})
        facts = {entry.subject: entry.facts for entry in built.context.extra_cost_facts}
        assert facts["external_insulation"].size == 173.0
        assert facts["external_insulation"].size_unit is Units.SQUARE_METER


class TestTheMappingReportHalf:
    """The two maps the staged evaluator reads back out of ``mapping_report.json``."""

    def test_every_envelope_subject_names_its_measure(self) -> None:
        """``economics_result.json`` stamps ``measure_id`` on ``by_subject`` from this map."""
        built = _built(_mockup())
        assert built.subjects["external_insulation"] == "external_insulation"

    def test_a_simulated_device_names_the_measure_that_installed_it(self) -> None:
        """The engine names the cost subject; only the translator knows which measure put it there."""
        built = _built(_mockup())
        assert built.subjects.get("PVSystem") == "photovoltaic_system"

    def test_the_defaults_carry_the_value_they_applied(self) -> None:
        """A default without its value is a sentence nobody can check (the report's own rule)."""
        built = _built(_mockup())
        for path, value, note in built.defaults:
            assert path and note
            assert value is not None


class TestTheSubsidyContext:
    """Who is applying, and what stays unanswered rather than false."""

    def test_the_building_half_comes_from_the_request(self) -> None:
        """Construction year and floor area are facts the request already states."""
        document = _mockup()
        context = _built(document).context.subsidy_context
        assert context.building.construction_year == document["house"]["building"]["construction_year"]
        assert context.building.heated_floor_area_in_m2 == 140.0

    def test_an_unanswered_applicant_field_stays_none(self) -> None:
        """§5.7: a question nobody answered is undetermined, never a denial."""
        context = _built(_mockup()).context.subsidy_context
        assert context.applicant.taxable_household_income_in_euro is None
        assert context.applicant.household_size is None

    def test_the_three_irish_applicant_answers_stay_none_without_an_applicant_block(self) -> None:
        """The vendored schema has no ``applicant`` block yet, so nothing may be inferred."""
        context = _built(_mockup()).context.subsidy_context
        assert context.applicant.receives_means_tested_benefit is None
        assert context.applicant.first_time_buyer is None
        assert context.applicant.managed_full_retrofit is None

    def test_an_applicant_block_is_read_when_the_request_carries_one(self) -> None:
        """Step 11 §3.2/§3.3: read when present, never guessed, never defaulted to false."""
        document = _mockup()
        document["applicant"] = {
            "receives_means_tested_benefit": True,
            "first_time_buyer": False,
            "managed_full_retrofit": True,
        }
        request = Request.parse(_mockup())
        request = dataclasses.replace(request, document=document)
        applied = apply(request.document["house"], request.measures, Whitelist.load())
        context = EconomicContextBuilder(request, applied, {}).build().context.subsidy_context

        assert context is not None
        assert context.applicant.receives_means_tested_benefit is True
        assert context.applicant.first_time_buyer is False
        assert context.applicant.managed_full_retrofit is True

    def test_the_dwelling_type_comes_from_the_building_type(self) -> None:
        """The mockup is a detached house, which is the band nearly every SEAI grant pays most for."""
        context = _built(_mockup()).context.subsidy_context
        assert context.building.dwelling_type is DwellingType.DETACHED

    @pytest.mark.parametrize(
        "building_type, expected",
        [
            (BuildingType.DETACHED_SFH, DwellingType.DETACHED),
            (BuildingType.BUNGALOW, DwellingType.DETACHED),
            (BuildingType.SEMI_DETACHED_SFH, DwellingType.SEMI_DETACHED_OR_END_TERRACE),
            (BuildingType.TERRACED_SFH, DwellingType.MID_TERRACE),
            (BuildingType.APARTMENT, DwellingType.APARTMENT),
            (BuildingType.OTHER, None),
        ],
    )
    def test_every_building_type_maps_to_its_band(self, building_type, expected) -> None:
        """``other`` gives ``None``, which the engine reads as a question rather than a denial."""
        assert DwellingTypes.of(building_type) is expected

    def test_a_terraced_house_is_banded_as_mid_terrace_and_says_so(self) -> None:
        """An end-of-terrace house is paid more and cannot be told apart, so it is approximated."""
        document = _mockup()
        document["house"]["building"]["building_type"] = BuildingType.TERRACED_SFH.value
        built = _built(document)

        assert built.context.subsidy_context.building.dwelling_type is DwellingType.MID_TERRACE
        paths = {path for path, _value, _note in built.approximations}
        assert EconomicContextBuilder.DWELLING_TYPE_PATH in paths
        assert all("approximated" in note for _path, _value, note in built.approximations)


class TestTheTechnicalAttributes:
    """What a subsidy condition may ask about a subject that its asset class does not say."""

    def test_an_envelope_subject_carries_the_placement_of_its_layer(self) -> None:
        """Ireland's cavity and dry-lining grants share an asset class and differ only in this."""
        attributes = _built(_mockup()).context.technical_attributes_by_subject
        assert attributes["external_insulation"]["placement"] == "external_wall_external"

    def test_an_envelope_subject_carries_its_achieved_u_value(self) -> None:
        """Unchanged by step 11: the U-value the run simulates with, not an assumed one."""
        attributes = _built(
            _mockup(),
            {"absolute_conditioned_floor_area_in_m2": 140.0, "facade_area_in_m2": 173.0,
             "facade_u_value_in_watt_per_m2_per_kelvin": 0.19},
        ).context.technical_attributes_by_subject
        assert attributes["external_insulation"][
            EconomicContextBuilder.U_VALUE_ATTRIBUTE
        ] == pytest.approx(0.19)

    def test_an_array_sized_as_a_share_of_the_roof_publishes_no_peak_power(self) -> None:
        """The mockup sizes its array by roof share, so no kilowatt-peak is known before the run."""
        attributes = _built(_mockup()).context.technical_attributes_by_subject
        assert EconomicContextBuilder.PHOTOVOLTAIC_COMPONENT not in attributes

    def test_a_pinned_array_publishes_its_peak_power_in_kilowatt_peak(self) -> None:
        """Ireland's solar PV grant steps with the array size and reads exactly this attribute."""
        document = _mockup()
        document["house"]["pv_system"] = {"power_in_watt": 3500}
        document["measures"] = [
            measure for measure in document["measures"] if measure["id"] != "photovoltaic_system"
        ]
        attributes = _built(document).context.technical_attributes_by_subject
        assert attributes[EconomicContextBuilder.PHOTOVOLTAIC_COMPONENT] == {
            EconomicContextBuilder.PEAK_POWER_ATTRIBUTE: pytest.approx(3.5)
        }


class TestTheCatalogueTheRunIsPointedAt:
    """Step 11 §3.12: a country whose catalogue ships is evaluated against it, not at NONE.

    The wiring is one line of :mod:`hisim.renovisor.simulation`, but it decides whether a whole
    run books grants or publishes "this country has no catalogue", so it gets a test of its own
    that needs no simulation. The path has to be absolute as well as right: the catalogue loader
    refuses a relative path that could name two different directories.
    """

    def test_ireland_now_has_a_shipped_catalogue(self) -> None:
        """``IE.json`` exists, so an Irish run evaluates the SEAI schemes."""
        assert SubsidyCatalogue.path_for("IE") is not None

    def test_a_country_without_one_still_gets_none(self) -> None:
        """The other branch is what keeps a country with no catalogue from being priced wrongly."""
        assert SubsidyCatalogue.path_for("ZZ") is None

    def test_the_parameters_name_the_shipped_directory_absolutely(self) -> None:
        """``resolve_base_path`` refuses an ambiguous relative path, so this one is absolute."""
        parameters = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=900)
        catalogue = EconomicSetup.attach(parameters, "IE")

        assert catalogue is not None
        economic = parameters.economic_parameters
        assert economic is not None
        configured = economic.subsidy_catalog_path
        assert configured is not None
        assert os.path.isabs(configured)
        assert SubsidyCatalog.load("IE", configured).schemes

    def test_a_country_without_a_catalogue_names_no_path(self) -> None:
        """Naming a catalogue that does not exist is a hard error, so none is named."""
        parameters = SimulationParameters.one_day_only(year=2019, seconds_per_timestep=900)

        assert EconomicSetup.attach(parameters, "ZZ") is None
        economic = parameters.economic_parameters
        assert economic is not None
        assert economic.subsidy_catalog_path is None


class TestTheTables:
    """The two naming tables, which are the translation and not a calculation."""

    def test_every_generator_of_the_vocabulary_has_an_asset_class(self) -> None:
        """A generator with no row would fail a request rather than a test, so it is a test."""
        for generator in HeatGenerator:
            asset_class, carrier = GeneratorAssets.of(generator)
            assert isinstance(asset_class, ComponentType)
            assert carrier is not None

    def test_every_thermal_element_has_an_asset_class(self) -> None:
        """The register has an entry per element, so each needs a class the database knows."""
        for element in ThermalElement:
            assert isinstance(EnvelopeAssets.of_element(element), ComponentType)

    def test_an_unlisted_placement_is_priced_conservatively(self) -> None:
        """A build-up nobody listed gets the most expensive wall row, never the cheapest."""
        assert EnvelopeAssets.of_placement("a placement nobody wrote down") is (
            ComponentType.WALL_EXTERNAL_INSULATION
        )
        assert AnywayShareByPlacement.of("a placement nobody wrote down") == (
            AnywayShareByPlacement.INTERNAL_FIRST_TIME
        )
