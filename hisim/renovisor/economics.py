"""What the lifecycle cost engine needs to know about a dwelling that no simulation can tell it.

A HiSim simulation models physics: it can say how big the heat pump is and how many kilowatt-hours
crossed the meter. It cannot say what stood in the cellar before, whose money is being spent, how
many square metres of facade a measure covers or what an insulation layer costs, and every one of
those decides the economics. The engine calls that half of its inputs an
:class:`~hisim.economics.bridge.EconomicContext`; this module builds one out of a RenoVisor
request and the package applied to it.

Four things go into it, and each answers a question the run cannot:

* **the existing-asset register** — the generator, the arrays and the five envelope elements that
  were already there, with the year each was installed and which measure supersedes it. Its mere
  presence switches the engine from greenfield accounting (everything is bought new into an empty
  building) to brownfield accounting: kept equipment is not charged, replaced equipment adds its
  removal cost and its written-off book value, and an improvement that the building would partly
  have paid for anyway is credited with that share (``cost_spec.md`` §4.1);
* **the envelope cost subjects** — one per envelope measure, sized in square metres of the element
  it covers. HiSim prices no envelope measure of its own here: the price comes from the request's
  ``cost`` block, written there by the frontend out of the contract's material table, and a
  measure that arrives without one is added to the plan **unpriced** rather than silently left
  out, so the result says what it does not know;
* **the technical attributes** — the achieved U-value of each envelope element, the build-up
  position a layer goes to, and the peak power of the array where the request pins it. Subsidy
  conditions read them and nothing else can supply them: Ireland's cavity-fill and dry-lining
  grants share one asset class and are told apart by the placement alone, and its solar PV grant
  steps with the array size;
* **the applicant** — who is applying, which decides eligibility. Unanswered fields stay
  *undetermined* rather than false (§5.7), which is what turns a half-filled questionnaire into a
  question in the result instead of a denied grant.

Nothing here prices anything and nothing here runs: the module is a translation from the
request's vocabulary into the engine's, and every number it writes is either the request's own or
a reviewed constant of :mod:`hisim.renovisor.constants`.
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple

from hisim.economics.bridge import EconomicContext
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.evaluator import SubjectCostFacts
from hisim.economics.facts import ComponentCostFacts, ExistingAsset, ExistingAssetRegister
from hisim.economics.subsidies import (
    ApplicantProfile,
    DwellingType,
    SubsidyBuildingContext,
    SubsidyContext,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.renovisor.constants import AnywayShareByPlacement, Placement
from hisim.renovisor.request import House, Measure, Request
from hisim.renovisor.vocabulary import BuildingType, HeatGenerator, ThermalElement
from hisim.renovisor.whitelist import TranslatorError


class GeneratorAssets:
    """Which cost-database asset class and energy carrier each heat generator is.

    The request names a generator in the measure catalogue's vocabulary
    (:class:`~hisim.renovisor.vocabulary.HeatGenerator`); the engine prices one by
    :class:`~hisim.loadtypes.ComponentType` and asks subsidy conditions about its
    :class:`~hisim.economics.carriers.EnergyCarrier`. This is the whole translation between the
    two, as one table, so a catalogue rename is a change here and nowhere else.

    Nothing in the table is a number: it is a naming correspondence, and where HiSim has no class
    of its own for a generator the closest one it does have is used, which is recorded in the
    comment beside that row rather than hidden.
    """

    #: Generator -> (asset class the cost database prices it under, carrier it burns).
    BY_GENERATOR: ClassVar[Dict[HeatGenerator, Tuple[ComponentType, EnergyCarrier]]] = {
        HeatGenerator.AIR_SOURCE_HEAT_PUMP: (ComponentType.HEAT_PUMP, EnergyCarrier.ELECTRICITY),
        HeatGenerator.GROUND_SOURCE_HEAT_PUMP: (ComponentType.HEAT_PUMP, EnergyCarrier.ELECTRICITY),
        # A hybrid is a heat pump with a boiler beside it; the register entry is the heat pump,
        # which is the asset a later measure replaces.
        HeatGenerator.HYBRID_HEAT_PUMP: (ComponentType.HEAT_PUMP, EnergyCarrier.ELECTRICITY),
        HeatGenerator.ELECTRIC_HEATING: (ComponentType.ELECTRIC_HEATER, EnergyCarrier.ELECTRICITY),
        # HiSim prices solid biomass as a pellet heater; woodchip and generic biomass share it.
        HeatGenerator.BIOMASS_HEATING: (ComponentType.PELLET_HEATER, EnergyCarrier.PELLETS),
        HeatGenerator.PELLET_HEATING: (ComponentType.PELLET_HEATER, EnergyCarrier.PELLETS),
        HeatGenerator.WOODCHIP_HEATING: (ComponentType.WOOD_CHIP_HEATER, EnergyCarrier.WOOD_CHIPS),
        HeatGenerator.DISTRICT_HEATING: (ComponentType.DISTRICT_HEATING, EnergyCarrier.DISTRICT_HEATING),
        # HVO is a drop-in replacement burned in an oil boiler, so the asset is an oil heater.
        HeatGenerator.HVO_HEATING: (ComponentType.OIL_HEATER, EnergyCarrier.HEATING_OIL),
        HeatGenerator.HYDROGEN_HEATING: (ComponentType.HYDROGEN_HEATER, EnergyCarrier.HYDROGEN),
        HeatGenerator.CONVENTIONAL_GAS_HEATING: (ComponentType.GAS_HEATER, EnergyCarrier.NATURAL_GAS),
        HeatGenerator.CONVENTIONAL_OIL_HEATING: (ComponentType.OIL_HEATER, EnergyCarrier.HEATING_OIL),
        # The engine has no LPG carrier; LPG is billed as the gas it is, which is the closest
        # priced carrier HiSim ships.
        HeatGenerator.CONVENTIONAL_LPG_HEATING: (ComponentType.GAS_HEATER, EnergyCarrier.NATURAL_GAS),
        HeatGenerator.CONDENSING_GAS_HEATING: (ComponentType.GAS_HEATER, EnergyCarrier.NATURAL_GAS),
        HeatGenerator.CONDENSING_OIL_HEATING: (ComponentType.OIL_HEATER, EnergyCarrier.HEATING_OIL),
        HeatGenerator.CONDENSING_LPG_HEATING: (ComponentType.GAS_HEATER, EnergyCarrier.NATURAL_GAS),
        # Solid fuel is only ever an *existing* generator; the closest priced class is the
        # woodchip heater, and the register entry exists so a heating_system measure can replace
        # something rather than install into an empty cellar.
        HeatGenerator.SOLID_FUEL_HEATING: (ComponentType.WOOD_CHIP_HEATER, EnergyCarrier.WOOD_CHIPS),
    }

    @classmethod
    def of(cls, generator: HeatGenerator) -> Tuple[ComponentType, EnergyCarrier]:
        """The asset class and carrier of one generator.

        Args:
            generator: The generator the request named.

        Returns:
            ``(asset class, energy carrier)``.

        Raises:
            KeyError: If the vocabulary has grown a generator this table does not know, which is
                a translator bug and not a bad request.
        """
        return cls.BY_GENERATOR[generator]


class EnvelopeAssets:
    """Which asset class each envelope element and each envelope measure is priced under.

    Two tables, because the register and the cost subjects ask different questions. The register
    needs a class for the *element that is already there* — a facade, a roof, a window — and the
    cost subjects need a class for the *measure* that improves it, which is finer: an external
    insulation layer and an internal dry lining are two different rows of the cost database.

    As in :class:`GeneratorAssets` nothing here is a number; it is the correspondence between the
    measure catalogue's build-up positions and HiSim's own envelope asset classes.
    """

    #: The element that is already there -> the class its register entry carries.
    BY_ELEMENT: ClassVar[Dict[ThermalElement, ComponentType]] = {
        ThermalElement.FACADE: ComponentType.WALL_EXTERNAL_INSULATION,
        ThermalElement.ROOF: ComponentType.WARM_ROOF_INSULATION,
        ThermalElement.FLOOR: ComponentType.BASEMENT_CEILING_BOTTOM_INSULATION,
        ThermalElement.WINDOW: ComponentType.WINDOWS_TRIPLE_GLAZED,
        ThermalElement.DOOR: ComponentType.EXTERIOR_DOOR,
    }

    #: The build-up position a measure writes to -> the class the new layer is priced under.
    BY_PLACEMENT: ClassVar[Dict[str, ComponentType]] = {
        Placement.EXTERNAL_WALL_EXTERNAL.value: ComponentType.WALL_EXTERNAL_INSULATION,
        Placement.EXTERNAL_WALL_INTERNAL.value: ComponentType.WALL_INTERNAL_INSULATION,
        Placement.EXTERNAL_WALL_CAVITY.value: ComponentType.WALL_INTERNAL_INSULATION,
        Placement.BASEMENT_CEILING.value: ComponentType.BASEMENT_CEILING_BOTTOM_INSULATION,
        Placement.BASEMENT_FLOOR_AND_WALLS_INSIDE.value: (
            ComponentType.BASEMENT_WALLS_INTERNAL_INSULATION
        ),
        Placement.BASEMENT_FLOOR_AND_WALLS_OUTSIDE.value: ComponentType.GROUND_EXTERNAL_INSULATION,
        Placement.FLOOR_AND_CEILING.value: ComponentType.BASEMENT_CEILING_BOTTOM_INSULATION,
        Placement.ROOF_EXTERNAL_RAFTER.value: ComponentType.ROOF_INSULATION_OVER_JOISTS,
        Placement.ROOF_BETWEEN_RAFTER.value: ComponentType.ROOF_INSULATION_BETWEEN_JOISTS,
        Placement.TOP_FLOOR_CEILING.value: ComponentType.TOP_CEILING_UPPER_INSULATION,
    }

    #: What the build refuses with when a layer names a build-up position this table has no row
    #: for. It is a translator bug and not a bad request: the placement comes from
    #: ``apply.MeasureBinding.INSULATION``, never from the request.
    UNKNOWN_PLACEMENT_MESSAGE: ClassVar[str] = (
        "no cost-database asset class for build-up position {placement!r}. "
        "EnvelopeAssets.BY_PLACEMENT knows {known}; a placement the measure catalogue added has "
        "to be given a row there, because pricing a layer at the wrong class is a plausible "
        "number for a different build-up."
    )

    #: The two measures that replace a whole unit rather than adding a layer -> its element.
    UNIT_REPLACEMENTS: ClassVar[Dict[str, ThermalElement]] = {
        "window_replacement": ThermalElement.WINDOW,
        "door_replacement": ThermalElement.DOOR,
    }

    @classmethod
    def of_element(cls, element: ThermalElement) -> ComponentType:
        """The class the register entry of one existing element carries."""
        return cls.BY_ELEMENT[element]

    @classmethod
    def of_placement(cls, placement: str) -> ComponentType:
        """The class a new layer at one build-up position is priced under.

        Args:
            placement: The ``materials.yaml`` build-up position the applied layer records.

        Returns:
            The cost-database asset class the layer is priced under.

        Raises:
            TranslatorError: If the table has no row for the placement. Like
                :meth:`GeneratorAssets.of`, an unmapped key is a build failure (exit 3) and not a
                bad request: every catalogue placement is exercised by the capability probe set,
                so a gap here is a table that was not extended with the catalogue. The earlier
                fallback to the external-wall class published a plausible price for a different
                build-up with nothing in the mapping report to say so.
        """
        if placement not in cls.BY_PLACEMENT:
            raise TranslatorError(
                cls.UNKNOWN_PLACEMENT_MESSAGE.format(
                    placement=placement, known=", ".join(sorted(cls.BY_PLACEMENT))
                )
            )
        return cls.BY_PLACEMENT[placement]


class DeviceAssets:
    """The three non-heating devices a request's inventory can carry, and what replaces them.

    Keeping the component name, the asset class and the measure that supersedes each device in
    one row is what lets the register be built by iterating a table rather than by three
    near-identical blocks, and is where a reader looks to see whether a device is in the register
    at all.
    """

    @dataclass(frozen=True)
    class Device:
        """One inventory device: where it lives in the request and what it is to the engine.

        Args:
            house_block: The ``house`` key the request carries it under.
            component: The HiSim component name the engine files its cost flows under, which is
                the same name the energy-system file gives the component. It must stay equal to
                the corresponding ``translate.Targets`` constant, which
                ``tests/renovisor/test_economics_context.py`` pins, because the two spell the same
                identifier on either side of an import cycle.
            asset_class: What the cost database prices it as.
            measure_id: The catalogue measure that replaces it, so the register can say so.
            size_key: The request field holding its size, when it has one.
            size_unit: The unit the register records the size in, which is the unit the cost
                database prices that asset class per.
            to_register_size: What the request's field has to be multiplied by to become
                ``size_unit``. One where the request already states the register's unit, and
                ``1/1000`` for the array, whose request field is in watts while the database
                prices photovoltaics per kilowatt.
        """

        house_block: str
        component: str
        asset_class: ComponentType
        measure_id: str
        size_key: Optional[str]
        size_unit: Units
        to_register_size: float = 1.0

    #: Watts per kilowatt: the array's request field is ``power_in_watt`` and the register's unit
    #: is the kilowatt the cost database prices photovoltaics per.
    WATT_PER_KILOWATT: ClassVar[float] = 1000.0

    #: The devices the register carries besides the generator and the envelope.
    ALL: ClassVar[Tuple["DeviceAssets.Device", ...]] = (
        Device(
            house_block="pv_system",
            component="PVSystem",
            asset_class=ComponentType.PV,
            measure_id="photovoltaic_system",
            size_key="power_in_watt",
            size_unit=Units.KILOWATT,
            to_register_size=1.0 / WATT_PER_KILOWATT,
        ),
        Device(
            house_block="battery",
            component="Battery",
            asset_class=ComponentType.BATTERY,
            measure_id="battery_system",
            size_key="custom_battery_capacity_generic_in_kilowatt_hour",
            size_unit=Units.KWH,
        ),
        Device(
            house_block="solar_thermal_system",
            component="SolarThermalSystem",
            asset_class=ComponentType.SOLAR_THERMAL_SYSTEM,
            measure_id="solar_thermal_system",
            size_key="area_m2",
            size_unit=Units.SQUARE_METER,
        ),
    )


class DwellingTypes:
    """What the request's ``building_type`` is to a subsidy programme that bands its amounts.

    Ireland's SEAI grants pay a different fixed amount per measure for a detached house, a
    semi-detached or end-of-terrace house, a mid-terrace house and an apartment, so the engine's
    eligibility context carries a :class:`~hisim.economics.subsidies.DwellingType` and this table
    is where the frontend's own vocabulary is translated into it.

    Two rows are worth a reader's attention. A bungalow is a detached house for this purpose —
    the band is about how many walls are shared, not about how many storeys there are. And a
    terraced house is mapped to ``MID_TERRACE`` although the request cannot say whether it is at
    the end of its terrace, which would be the better-paid band; the mapping report says so with
    the word ``approximated``, and nothing guesses in the other direction. ``other`` maps to
    nothing at all, which the engine reads as *unanswered* and reports as a question.
    """

    #: The request's building type -> the band the eligibility context uses, or None for "unknown".
    BY_BUILDING_TYPE: ClassVar[Dict[BuildingType, Optional[DwellingType]]] = {
        BuildingType.DETACHED_SFH: DwellingType.DETACHED,
        BuildingType.BUNGALOW: DwellingType.DETACHED,
        BuildingType.SEMI_DETACHED_SFH: DwellingType.SEMI_DETACHED_OR_END_TERRACE,
        BuildingType.TERRACED_SFH: DwellingType.MID_TERRACE,
        BuildingType.APARTMENT: DwellingType.APARTMENT,
        BuildingType.OTHER: None,
    }

    #: The building types whose band is an approximation rather than a translation.
    APPROXIMATED: ClassVar[Tuple[BuildingType, ...]] = (BuildingType.TERRACED_SFH,)

    #: What the mapping report says about an approximated band.
    APPROXIMATION_NOTE: ClassVar[str] = (
        "approximated: a terraced house is banded as mid-terrace because the request cannot say "
        "whether it stands at the end of its terrace, which several grants pay more for"
    )

    @classmethod
    def of(cls, building_type: Optional[BuildingType]) -> Optional[DwellingType]:
        """The band one building type falls into, or ``None`` when it falls into none.

        Args:
            building_type: The request's ``house.building.building_type``, or ``None`` when the
                request does not state one.

        Returns:
            The :class:`~hisim.economics.subsidies.DwellingType`, or ``None`` for ``other`` and
            for an unstated building type — both of which the engine treats as unanswered.
        """
        if building_type is None:
            return None
        return cls.BY_BUILDING_TYPE.get(building_type)


@dataclass
class EconomicContextResult:
    """What the builder produced: the context, and the two things the mapping report publishes.

    The context goes to the engine and the two maps go into ``mapping_report.json``, from where
    the staged evaluator reads them to stamp ``measure_id`` on every subject of the result
    document and to flag the subjects nothing could price. They are produced together because
    they are decided together: the same loop that adds an envelope cost subject knows which
    measure created it and whether the request carried a price for it.

    Attributes:
        context: The :class:`~hisim.economics.bridge.EconomicContext` to attach to the run.
        subjects: Cost subject -> the catalogue measure that created it, or ``None`` for a
            subject that was already there.
        unpriced_subjects: The subjects in the context with no price behind them, in the order
            the package added them.
        defaults: One ``(request path, value, sentence)`` per request leaf the builder had to
            default, so the mapping report can state it with the value it used. Nothing the
            builder does is silent.
        approximations: One ``(path, value, sentence)`` per context field the builder could only
            fill with something close but not equal — today the dwelling-type band, which cannot
            tell an end-of-terrace house from a mid-terrace one. Published as ``approximated``
            lines of the mapping report, under paths of their own so that the request leaf they
            were derived from keeps the line it already has.
    """

    context: EconomicContext
    subjects: Dict[str, Optional[str]] = field(default_factory=dict)
    unpriced_subjects: List[str] = field(default_factory=list)
    defaults: List[Tuple[str, Any, str]] = field(default_factory=list)
    approximations: List[Tuple[str, Any, str]] = field(default_factory=list)


class EconomicContextBuilder:
    """Builds the economic context of one translated calculation.

    Everything it reads is handed in — the validated request, the applied package and the
    ``Building`` config the translator wrote — so it runs without a simulation and a test can
    drive it with a request and nothing else.

    Example::

        built = EconomicContextBuilder(request, applied, building_config).build()
        simulation_parameters.set_economic_context(built.context)

    Args:
        request: The validated request, for the country, the construction year, the applicant
            block and the optional per-measure price blocks.
        applied: The package as :func:`hisim.renovisor.apply.apply` produced it — the renovated
            house, the layers it added and the measures it applied.
        building_config: Every ``Building`` configuration field the translator wrote, its
            ``for_tabula_code`` constructor arguments merged in (``translate._building_config``).
            The ``<element>_area_in_m2`` fields size the envelope subjects and may legitimately be
            absent, in which case each element falls back to the request; the archetype and the
            floor area may not, because the existing generator is sized from the design heat load
            they decide.
        heating_reference_temperature_in_celsius: The outside design temperature the translated
            ``Weather`` carries, which the same heat load is computed against (decision D-21).
        generator_component: The name of the component that produces space heat in the twin the
            translator wrote into -- the cost subject the engine files the generator's flows
            under. It is needed for one thing only: saying that the ``heating_system`` measure is
            what put it there, which the engine cannot know. ``None`` leaves the generator's row
            of the result document without a measure, exactly as a baseline component has none.
    """

    #: The request field naming when a device or an element was installed (E-spec §7). Absent
    #: from the vendored schema today; read when present, defaulted to the construction year.
    INSTALLATION_YEAR_KEY: ClassVar[str] = "installation_year"

    #: The request block naming who is applying (E-spec §7). Absent from the vendored schema
    #: today; read when present, and every field it does not answer stays undetermined.
    APPLICANT_KEY: ClassVar[str] = "applicant"

    #: The fields of that block the builder copies onto the applicant profile, by their name on
    #: :class:`~hisim.economics.subsidies.ApplicantProfile`. The last three are the ones the
    #: Irish catalogue reads (step 11 §3.2/§3.3); a field the block omits stays undetermined.
    APPLICANT_FIELDS: ClassVar[Tuple[str, ...]] = (
        "taxable_household_income_in_euro",
        "household_size",
        "main_residence",
        "region",
        "receives_means_tested_benefit",
        "first_time_buyer",
        "managed_full_retrofit",
    )

    #: The per-measure price block the frontend writes out of the contract's material table
    #: (E-spec §7). Absent from the vendored schema today (findings F7/F10).
    COST_KEY: ClassVar[str] = "cost"

    #: Its two price fields and the provenance string beside them.
    COST_MINIMUM_KEY: ClassVar[str] = "min_in_euro_per_m2"
    COST_MAXIMUM_KEY: ClassVar[str] = "max_in_euro_per_m2"
    COST_SOURCE_KEY: ClassVar[str] = "source"

    #: The request field holding the dwelling's living area (E-spec §7).
    LIVING_AREA_KEY: ClassVar[str] = "living_area_in_m2"

    #: The request field holding one envelope element's area, the fallback when the translated
    #: ``Building`` config carries none.
    ELEMENT_AREA_KEY: ClassVar[str] = "area_in_m2"

    #: The ``Building`` config field holding the conditioned floor area, the fallback for it.
    FLOOR_AREA_KEY: ClassVar[str] = "absolute_conditioned_floor_area_in_m2"

    #: The suffix of a ``Building`` config envelope area field.
    AREA_SUFFIX: ClassVar[str] = "_area_in_m2"

    #: The suffix of a ``Building`` config envelope U-value field, which the achieved U-values
    #: the subsidy conditions read are taken from.
    U_VALUE_SUFFIX: ClassVar[str] = "_u_value_in_watt_per_m2_per_kelvin"

    #: The technical attribute the U-value of an envelope subject is published under.
    U_VALUE_ATTRIBUTE: ClassVar[str] = "achieved_u_value_in_watt_per_m2_per_kelvin"

    #: The technical attribute the build-up position of an envelope subject is published under.
    #: Two different grants can share one asset class and be told apart only by it — Ireland pays
    #: a cavity-fill grant and a dry-lining grant, both of which are ``WALL_INTERNAL_INSULATION``.
    PLACEMENT_ATTRIBUTE: ClassVar[str] = "placement"

    #: The technical attribute the peak power of the photovoltaic array is published under.
    #: Grants that step with array size read it; it is only published when the request pins the
    #: array's power, because an array sized as a share of the roof has no peak power until the
    #: simulation has run.
    PEAK_POWER_ATTRIBUTE: ClassVar[str] = "peak_power_in_kwp"

    #: Watt per kilowatt, for the conversion into that attribute's unit.
    WATT_PER_KILOWATT: ClassVar[float] = 1000.0

    #: The note an unpriced envelope subject carries into the mapping report.
    UNPRICED_NOTE: ClassVar[str] = (
        "no cost block on the measure, so the subject is in the economics with an investment of "
        "zero and flagged unpriced; HiSim does not estimate envelope prices (decisions Q8/Q9)"
    )

    #: The note a defaulted installation year carries.
    INSTALLATION_YEAR_NOTE: ClassVar[str] = (
        "the request states no installation year for this asset, so the building's construction "
        "year is used as its age"
    )

    #: The catalogue measure that installs a new heat generator.
    HEATING_MEASURE_ID: ClassVar[str] = "heating_system"

    #: The ``Building`` config field naming the TABULA archetype, which the design heat load is
    #: computed from. Written by the translator as a ``for_tabula_code`` constructor argument.
    BUILDING_CODE_KEY: ClassVar[str] = "building_code"

    #: Its dwelling-unit count, the second constructor argument the load depends on.
    APARTMENTS_KEY: ClassVar[str] = "number_of_apartments"

    #: The name the rebuilt ``BuildingConfig`` carries. It never reaches a file: the configuration
    #: exists only to be handed to ``BuildingInformation`` for the heat-load calculation.
    BUILDING_COMPONENT: ClassVar[str] = "Building"

    #: The three fields the rebuilt configuration gets from its constructor, which must not be
    #: overwritten afterwards by the same keys read out of the merged mapping.
    BUILDING_CONSTRUCTOR_KEYS: ClassVar[Tuple[str, ...]] = (
        BUILDING_CODE_KEY,
        APARTMENTS_KEY,
        "absolute_conditioned_floor_area_in_m2",
    )

    #: The mapping-report path the existing generator's size is reported under. It is a derived
    #: context field rather than a request leaf, so it gets a path of its own.
    GENERATOR_SIZE_PATH: ClassVar[str] = "house.heating.nominal_power_in_kw"

    #: What the mapping report says about that size.
    GENERATOR_SIZE_NOTE: ClassVar[str] = (
        "sized from the building's design heat load, as the run sizes a new generator; the "
        "request states no boiler rating"
    )

    #: What the build refuses with when the load cannot be computed.
    GENERATOR_SIZE_MESSAGE: ClassVar[str] = (
        "the existing generator cannot be sized: {missing} is missing from the translated "
        "configuration. Its size prices the like-for-like replacement, the sunk cost and the "
        "anyway credit, so a nominal value would publish a plausible price for a different boiler."
    )

    #: The component the engine files the photovoltaic array's cost flows under, which is the
    #: subject the array's technical attributes are published for.
    PHOTOVOLTAIC_COMPONENT: ClassVar[str] = "PVSystem"

    #: The mapping-report path the dwelling-type band is reported under. It is a derived context
    #: field rather than a request leaf, so it gets a path of its own and leaves the line the
    #: request's own ``house.building.building_type`` already carries untouched.
    DWELLING_TYPE_PATH: ClassVar[str] = "economic_context.building.dwelling_type"

    def __init__(
        self,
        request: Request,
        applied: Any,
        building_config: Optional[Mapping[str, Any]] = None,
        generator_component: Optional[str] = None,
        heating_reference_temperature_in_celsius: Optional[float] = None,
    ) -> None:
        """Store the inputs; nothing is read until :meth:`build`."""
        self._request = request
        self._applied = applied
        self._building = dict(building_config or {})
        self._generator_component = generator_component
        self._heating_reference_temperature = heating_reference_temperature_in_celsius
        self._house = House.from_dict(applied.house)
        self._original = House.from_dict(dict(request.document["house"]))
        self._raw_original: Mapping[str, Any] = request.document["house"]
        self._measure_ids = {measure.id for measure in request.measures}

    # ------------------------------------------------------------------ the whole context

    def build(self) -> EconomicContextResult:
        """Return the economic context of this calculation and the maps the report publishes.

        Returns:
            The :class:`EconomicContextResult`.
        """
        result = EconomicContextResult(context=EconomicContext())
        register = ExistingAssetRegister(assets=self._register_assets(result))
        facts = self._envelope_cost_facts(result)
        result.context = EconomicContext(
            existing_assets=register,
            subsidy_context=self._subsidy_context(result),
            extra_cost_facts=facts,
            technical_attributes_by_subject=self._technical_attributes(facts),
            living_area_in_m2=self._living_area(),
            heated_floor_area_in_m2=self._floor_area(),
        )
        self._record_device_subjects(result)
        return result

    # ------------------------------------------------------------------ the register

    def _register_assets(self, result: EconomicContextResult) -> List[ExistingAsset]:
        """Everything that was already in the building, with what replaces it.

        Three groups in one list, in the order a reader of the register would expect them: the
        heat generator, the devices of :class:`DeviceAssets`, and the five envelope elements. Each
        carries the year it was installed — the request's own when it states one, the building's
        construction year otherwise — and the asset classes of the measures that supersede it.
        """
        assets = [self._generator_asset(result)]
        assets.extend(self._device_assets())
        assets.extend(self._envelope_assets())
        return [asset for asset in assets if asset is not None]

    def _generator_asset(self, result: EconomicContextResult) -> ExistingAsset:
        """The heat generator that was in the cellar before the package touched anything.

        Its ``replaced_by_asset_classes`` is the class of the generator the package installs, when
        the package installs one: that is what tells the engine to charge the new generator in
        full, book the old one's removal and write off its remaining book value (§4.1).
        """
        generator = self._original.heating.type_of_system
        asset_class, carrier = GeneratorAssets.of(generator)
        replaced: List[ComponentType] = []
        if self.HEATING_MEASURE_ID in self._measure_ids:
            new_class, _carrier = GeneratorAssets.of(self._house.heating.type_of_system)
            replaced = [new_class]
        return ExistingAsset(
            asset_class=asset_class,
            size=self._generator_size(result),
            size_unit=Units.KILOWATT,
            installation_year=self._installation_year("heating", result),
            is_functional=True,
            energy_carrier=carrier,
            replaced_by_asset_classes=replaced,
        )

    def _generator_size(self, result: EconomicContextResult) -> float:
        """The existing generator's rated output in kilowatts, as the engine's register needs it.

        The size matters, which an earlier version of this method denied: the engine prices the
        like-for-like replacement of a replaced asset at ``investment_for_size(replaced.size)``
        (``calculators/context_resolution.py``), so the register's size decides the old boiler's
        sunk cost and the anyway-cost credit the new generator is given. A nominal one kilowatt
        priced a fifteen-kilowatt boiler at a fifteenth of its cost, and nothing said so.

        The request states no boiler rating, so the size is the building's **design heat load** --
        the same figure the run itself sizes a *new* generator from, so the two generators are
        compared at one capacity. It is computed here exactly as the ``Building`` component
        computes it, through :class:`~hisim.components.building.BuildingInformation` over the
        translated configuration and the design temperature the translated ``Weather`` carries,
        and it is published as an ``approximated`` line of the mapping report, because a
        building's heat load is not a boiler's nameplate.

        Args:
            result: The result being assembled, for the approximation line.

        Returns:
            The design heat load in kilowatts.

        Raises:
            TranslatorError: If the translated configuration states neither the archetype nor the
                design temperature the load is computed from. Sizing the register nominally
                instead would put a plausible price on the wrong boiler, which is the failure this
                method exists to end.
        """
        load_in_kw = self._design_heat_load_in_kw()
        result.approximations.append((self.GENERATOR_SIZE_PATH, load_in_kw, self.GENERATOR_SIZE_NOTE))
        return load_in_kw

    def _design_heat_load_in_kw(self) -> float:
        """The building's design heat load in kilowatts, as the ``Building`` component derives it.

        One instantiation of :class:`~hisim.components.building.BuildingInformation` over a
        configuration rebuilt from what the translator wrote: the TABULA archetype, the floor area
        and the apartment count from the ``for_tabula_code`` constructor, the envelope U-values
        and areas from the component's own config block, and the outside design temperature from
        the weather. Every one of those is a translated value, so the load is the one the run will
        compute rather than a second model of the same thing.

        Returns:
            ``max_thermal_building_demand_in_watt`` divided by one thousand.

        Raises:
            TranslatorError: Naming exactly which of the two required inputs is missing.
        """
        # Function-local: this module is a translation table a test drives with no HiSim component
        # at all, and the building package pulls the TABULA table and pandas in with it.
        from hisim.components.building import (  # pylint: disable=import-outside-toplevel
            BuildingConfig,
            BuildingInformation,
        )

        missing = []
        code = self._building.get(self.BUILDING_CODE_KEY)
        temperature = self._heating_reference_temperature
        if not isinstance(code, str) or not code:
            missing.append(f"the Building config's {self.BUILDING_CODE_KEY}")
        if temperature is None:
            missing.append("the Weather's heating_reference_temperature_in_celsius")
        if missing or temperature is None:
            raise TranslatorError(self.GENERATOR_SIZE_MESSAGE.format(missing=" and ".join(missing)))
        config = BuildingConfig.for_tabula_code(
            name=self.BUILDING_COMPONENT,
            building_code=str(code),
            number_of_apartments=self._as_positive_float(self._building.get(self.APARTMENTS_KEY)),
            absolute_conditioned_floor_area_in_m2=self._floor_area(),
        )
        for name, value in self._building.items():
            if name not in self.BUILDING_CONSTRUCTOR_KEYS and hasattr(config, name):
                setattr(config, name, value)
        config.heating_reference_temperature_in_celsius = float(temperature)
        load = BuildingInformation(config).max_thermal_building_demand_in_watt
        return float(load) / self.WATT_PER_KILOWATT

    def _device_assets(self) -> List[ExistingAsset]:
        """One register entry per inventory device the request carries.

        A block the request does not carry is a device the building does not have, so it produces
        no entry at all — which is what makes the difference between "there is no battery" and "a
        battery of unknown size" visible in the register rather than in a comment.
        """
        assets = []
        for device in DeviceAssets.ALL:
            block = self._raw_original.get(device.house_block)
            if not isinstance(block, Mapping):
                continue
            stated = self._as_positive_float(block.get(device.size_key) if device.size_key else None)
            # The request's unit is not always the register's: the array's field is in watts and
            # the cost database prices photovoltaics per kilowatt, so the row carries the factor
            # between them. A device the request carries without a size is still a device that is
            # there, and one is the size that says "present, size unstated".
            size = stated * device.to_register_size if stated is not None else 1.0
            assets.append(
                ExistingAsset(
                    asset_class=device.asset_class,
                    size=size,
                    size_unit=device.size_unit,
                    installation_year=self._installation_year(device.house_block),
                    is_functional=True,
                    replaced_by_asset_classes=(
                        [device.asset_class] if device.measure_id in self._measure_ids else []
                    ),
                )
            )
        return assets

    def _envelope_assets(self) -> List[ExistingAsset]:
        """One register entry per envelope element, with the share it would have cost anyway.

        The element is as old as the building unless the request says otherwise, and it is
        replaced by whichever measure of the package touches it. ``anyway_share`` is the number
        that decides how much of that measure's price is credited as money the building would
        have spent regardless; it comes from
        :class:`~hisim.renovisor.constants.AnywayShareByPlacement` and is well below one for a
        first-time improvement.
        """
        assets = []
        for element in ThermalElement:
            area = self._element_area(element)
            if area is None:
                continue
            replaced, share = self._replacement_of(element)
            assets.append(
                ExistingAsset(
                    asset_class=EnvelopeAssets.of_element(element),
                    size=area,
                    size_unit=Units.SQUARE_METER,
                    installation_year=self._installation_year(
                        f"building.{element.value}", block=self._raw_element(element)
                    ),
                    is_functional=True,
                    replaced_by_asset_classes=replaced,
                    anyway_share=share,
                )
            )
        return assets

    def _replacement_of(self, element: ThermalElement) -> Tuple[List[ComponentType], float]:
        """What supersedes one envelope element, and at what anyway share.

        A layer measure supersedes the element with the share of its build-up position; a window
        or door replacement is a genuine like-for-like replacement and carries a share of one.
        An element no measure touches is kept, and its share is irrelevant but must stay inside
        ``(0, 1]``, so it keeps the like-for-like default.
        """
        classes: List[ComponentType] = []
        share = AnywayShareByPlacement.LIKE_FOR_LIKE
        for layer in self._applied.layers:
            if layer.element is not element:
                continue
            classes.append(EnvelopeAssets.of_placement(layer.placement))
            share = AnywayShareByPlacement.of(layer.placement)
        for measure_id, replaced_element in EnvelopeAssets.UNIT_REPLACEMENTS.items():
            if replaced_element is element and measure_id in self._measure_ids:
                classes.append(EnvelopeAssets.of_element(element))
                share = AnywayShareByPlacement.LIKE_FOR_LIKE
        return classes, share

    # ------------------------------------------------------------------ envelope cost subjects

    def _envelope_cost_facts(self, result: EconomicContextResult) -> List[SubjectCostFacts]:
        """One cost subject per envelope measure, priced from the request or flagged unpriced.

        The subject's name is the catalogue measure id, which is what makes the mapping report's
        ``subjects`` map trivially right and lets the result document stamp a measure on every
        row of its investment build-up. Its size is the element's area, from the ``Building``
        config the translator wrote or, failing that, from the request. An element with no area
        anywhere is still added — a measure the plan carries out must appear in the economics —
        with a size of one square metre and the ``unpriced`` flag, because pricing per square
        metre without a square metre is not a smaller answer, it is a wrong one.
        """
        facts: List[SubjectCostFacts] = []
        for layer in self._applied.layers:
            facts.append(
                self._envelope_subject(
                    measure_id=layer.measure_id,
                    element=layer.element,
                    asset_class=EnvelopeAssets.of_placement(layer.placement),
                    result=result,
                )
            )
        for measure_id, element in EnvelopeAssets.UNIT_REPLACEMENTS.items():
            if measure_id in self._measure_ids:
                facts.append(
                    self._envelope_subject(
                        measure_id=measure_id,
                        element=element,
                        asset_class=EnvelopeAssets.of_element(element),
                        result=result,
                    )
                )
        return facts

    def _envelope_subject(
        self,
        measure_id: str,
        element: ThermalElement,
        asset_class: ComponentType,
        result: EconomicContextResult,
    ) -> SubjectCostFacts:
        """One envelope measure as a cost subject, with its price or with the absence of one."""
        area = self._element_area(element)
        price = self._measure_price(measure_id)
        result.subjects[measure_id] = measure_id
        if price is None or area is None:
            result.unpriced_subjects.append(measure_id)
            unpriced = True
            investment = UncertainValue.exact(0.0)
        else:
            unpriced = False
            minimum, maximum = price
            investment = UncertainValue(
                best_estimate=(minimum + maximum) / 2.0 * area,
                minimum=minimum * area,
                maximum=maximum * area,
            )
        return SubjectCostFacts(
            subject=measure_id,
            facts=ComponentCostFacts(
                asset_class=asset_class,
                size=float(area) if area else 1.0,
                size_unit=Units.SQUARE_METER,
                investment_cost_override_in_euro=investment,
                installation_cost_override_in_euro=UncertainValue.exact(0.0),
                override_source=(
                    self.UNPRICED_NOTE if unpriced else f"the request's measures[{measure_id}].cost block"
                ),
            ),
        )

    def _measure_price(self, measure_id: str) -> Optional[Tuple[float, float]]:
        """The request's price band for one measure, in euro per square metre, or ``None``.

        The frontend copies the two numbers out of the contract's material table into the
        request's ``measures[i].cost`` block (E-spec §7); the translator reads no catalogue of its
        own and estimates nothing, so a measure without the block has no price here.

        Args:
            measure_id: The catalogue measure.

        Returns:
            ``(minimum, maximum)`` in euro per square metre, or ``None`` when the request carries
            no usable block for the measure.
        """
        for measure in self._request.measures:
            if measure.id != measure_id:
                continue
            block = self._measure_cost_block(measure)
            if not isinstance(block, Mapping):
                return None
            minimum = block.get(self.COST_MINIMUM_KEY)
            maximum = block.get(self.COST_MAXIMUM_KEY)
            if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
                return None
            return (float(minimum), float(maximum))
        return None

    def _measure_cost_block(self, measure: Measure) -> Any:
        """The raw ``cost`` block of one measure as the request carried it, or ``None``.

        The block is not in the vendored request schema yet (findings F7/F10), so it never
        survives into :class:`~hisim.renovisor.request.Measure`; it is looked for on the raw
        document instead, which is what "read when present" means here.
        """
        for raw in self._request.document.get("measures", []):
            if isinstance(raw, Mapping) and raw.get("id") == measure.id:
                return raw.get(self.COST_KEY)
        return None

    # ------------------------------------------------------------------ the rest of the context

    def _technical_attributes(
        self, facts: List[SubjectCostFacts]
    ) -> Dict[str, Dict[str, Any]]:
        """What the subsidy conditions may ask about a subject beyond its asset class and size.

        Three attributes, and only what the run actually realizes is published. The achieved
        U-value of an envelope subject comes from the ``Building`` config the translator wrote,
        which is the value the simulation runs with; the build-up position comes from the applied
        layer, and is what tells two grants on one asset class apart (a cavity fill and a dry
        lining are both ``WALL_INTERNAL_INSULATION``); the array's peak power comes from the
        renovated house when the request pins it. A subject whose element has no U-value in the
        config gets no U-value attribute rather than a guessed one, and an array sized as a share
        of the roof publishes no peak power, so a grant that steps with array size comes out
        *undetermined* instead of being decided on a number nobody stated.

        Args:
            facts: The envelope cost subjects this builder produced.

        Returns:
            Subject name -> attribute map, ready for
            :attr:`~hisim.economics.bridge.EconomicContext.technical_attributes_by_subject`.
            A subject with nothing to say about it is absent from the map.
        """
        # No SCOP: neither the request nor the recorded twins state a seasonal performance factor
        # for a heat pump today, and the engine's subsidy conditions read one as an attribute
        # (`subsidies/context.py`). A scheme keyed on it therefore stays *undetermined* rather
        # than being decided on an invented figure. Recorded as an F-item in `todos.md` H7.
        attributes: Dict[str, Dict[str, Any]] = {}
        placements = {layer.measure_id: layer.placement for layer in self._applied.layers}
        for subject_facts in facts:
            subject = subject_facts.subject
            entry: Dict[str, Any] = {}
            placement = placements.get(subject)
            if placement is not None:
                entry[self.PLACEMENT_ATTRIBUTE] = placement
            element = self._element_of_subject(subject)
            if element is not None:
                value = self._building.get(f"{element.value}{self.U_VALUE_SUFFIX}")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    entry[self.U_VALUE_ATTRIBUTE] = float(value)
            if entry:
                attributes[subject] = entry
        peak_power = self._peak_power_in_kwp()
        if peak_power is not None:
            attributes[self.PHOTOVOLTAIC_COMPONENT] = {self.PEAK_POWER_ATTRIBUTE: peak_power}
        return attributes

    def _peak_power_in_kwp(self) -> Optional[float]:
        """The renovated array's peak power in kilowatt-peak, or ``None`` when it has none.

        Reads the house the package produced rather than the request, so an array a measure
        installed or enlarged is the one reported. A request that sizes the array as a share of
        the roof states no power at all, and a house with no array states zero; both give
        ``None``, because a grant that steps with array size must then stay undetermined rather
        than be denied on a zero nobody wrote.

        Returns:
            The peak power in kWp, or ``None``.
        """
        array = self._house.pv_system
        if array is None or array.power_in_watt is None or array.power_in_watt <= 0:
            return None
        return float(array.power_in_watt) / self.WATT_PER_KILOWATT

    def _element_of_subject(self, subject: str) -> Optional[ThermalElement]:
        """Which envelope element one cost subject improves, or ``None`` when it is not one."""
        for layer in self._applied.layers:
            if layer.measure_id == subject:
                element: ThermalElement = layer.element
                return element
        return EnvelopeAssets.UNIT_REPLACEMENTS.get(subject)

    def _subsidy_context(self, result: EconomicContextResult) -> SubsidyContext:
        """Who is applying and what the building is, for the eligibility conditions.

        The applicant half comes from the request's ``applicant`` block when it carries one
        (E-spec §7); every field it does not answer stays ``None``, which the engine reads as
        *undetermined* and reports as a question rather than as a denial (§5.7). The three
        fields the Irish catalogue reads — ``receives_means_tested_benefit``, ``first_time_buyer``
        and ``managed_full_retrofit``, the last of which is the One Stop Shop route — are read
        from that block exactly like the others and are never inferred from anything else.

        The building half is what the request already states: the construction year, the floor
        area, one dwelling unit (the archetype every RenoVisor calculation simulates) and the
        dwelling-type band of :class:`DwellingTypes`, which grants with per-dwelling-type amounts
        read. A band that could only be approximated is recorded on ``result.approximations``, so
        the mapping report says so.

        Args:
            result: The result being assembled, for the approximation the band may carry.

        Returns:
            The context the eligibility conditions resolve against.
        """
        raw = self._request.document.get(self.APPLICANT_KEY)
        profile = ApplicantProfile()
        if isinstance(raw, Mapping):
            for name in self.APPLICANT_FIELDS:
                if name in raw:
                    setattr(profile, name, raw[name])
        building_type = self._original.building.building_type
        dwelling_type = DwellingTypes.of(building_type)
        if building_type in DwellingTypes.APPROXIMATED and dwelling_type is not None:
            result.approximations.append(
                (self.DWELLING_TYPE_PATH, dwelling_type.value, DwellingTypes.APPROXIMATION_NOTE)
            )
        return SubsidyContext(
            applicant=profile,
            building=SubsidyBuildingContext(
                construction_year=self._original.building.construction_year,
                dwelling_type=dwelling_type,
                heated_floor_area_in_m2=self._floor_area(),
                residential_floor_area_in_m2=self._floor_area(),
            ),
        )

    def _record_device_subjects(self, result: EconomicContextResult) -> None:
        """Note which measure created each simulated component, for the report's ``subjects``.

        The devices are simulation components, so the engine names their cost subjects itself —
        the component name of the energy-system file. What it cannot know is which catalogue
        measure put them there, and that is exactly what the result document stamps on the
        investment build-up, so it is recorded here.
        """
        for device in DeviceAssets.ALL:
            if device.measure_id in self._measure_ids:
                result.subjects[device.component] = device.measure_id
        if self._generator_component and self.HEATING_MEASURE_ID in self._measure_ids:
            result.subjects[self._generator_component] = self.HEATING_MEASURE_ID

    # ------------------------------------------------------------------ small readers

    def _installation_year(
        self,
        block_name: str,
        result: Optional[EconomicContextResult] = None,
        block: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """The installation year of one part of the house, defaulting to the construction year.

        ``installation_year`` is an E-spec §7 field the vendored request schema does not carry
        yet, so most requests state none and the building's construction year stands in for it.

        Args:
            block_name: The ``house`` key the part lives under, for the mapping-report path.
            result: The result being assembled. When given, a defaulted year is recorded on its
                ``defaults`` list with the value used; the register's device and envelope entries
                pass ``None``, because their blocks already carry a line of their own.
            block: The raw block to read, when it is not ``house[block_name]`` — an envelope
                element lives one level down, under ``house.building``.

        Returns:
            The year the part was installed.
        """
        raw = self._raw_original.get(block_name) if block is None else block
        if isinstance(raw, Mapping) and isinstance(raw.get(self.INSTALLATION_YEAR_KEY), int):
            return int(raw[self.INSTALLATION_YEAR_KEY])
        year = self._original.building.construction_year
        if result is not None:
            result.defaults.append(
                (f"house.{block_name}.{self.INSTALLATION_YEAR_KEY}", year, self.INSTALLATION_YEAR_NOTE)
            )
        return year

    def _raw_element(self, element: ThermalElement) -> Mapping[str, Any]:
        """The request's raw block for one envelope element, or an empty mapping."""
        building = self._raw_original.get("building")
        if not isinstance(building, Mapping):
            return {}
        block = building.get(element.value)
        return block if isinstance(block, Mapping) else {}

    def _element_area(self, element: ThermalElement) -> Optional[float]:
        """One element's area in square metres: the realized building first, the request second.

        The translated ``Building`` config is what the simulation will run with, so it is the
        first source; where the translator wrote no area — because the request stated none and
        the archetype derives it — the request cannot supply one either and the answer is
        ``None``, which makes the subject unpriced rather than sized by a guess.
        """
        realized = self._as_positive_float(self._building.get(f"{element.value}{self.AREA_SUFFIX}"))
        if realized is not None:
            return realized
        return self._as_positive_float(self._raw_element(element).get(self.ELEMENT_AREA_KEY))

    def _living_area(self) -> Optional[float]:
        """The dwelling's living area: the request's own, else the conditioned floor area."""
        building = self._raw_original.get("building")
        if isinstance(building, Mapping):
            stated = self._as_positive_float(building.get(self.LIVING_AREA_KEY))
            if stated is not None:
                return stated
        return self._floor_area()

    def _floor_area(self) -> Optional[float]:
        """The conditioned floor area the translated building carries, or ``None``."""
        return self._as_positive_float(self._building.get(self.FLOOR_AREA_KEY))

    @staticmethod
    def _as_positive_float(value: Any) -> Optional[float]:
        """One raw request or config value as a positive number, or ``None``.

        The shape check every numeric leaf of this module needs, in one place: a JSON value is a
        number, is not a boolean (``True`` is an ``int`` in Python and would otherwise pass as a
        size of one), and is greater than zero, because none of the quantities read here — an
        area, a capacity, a peak power — is meaningfully zero or negative. What to do when the
        answer is ``None`` is the caller's decision and differs per call site, which is why the
        helper returns the absence rather than a substitute for it.

        Args:
            value: Whatever the request or the translated config carried.

        Returns:
            The value as a float, or ``None`` when it is not a positive number.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
        return None
