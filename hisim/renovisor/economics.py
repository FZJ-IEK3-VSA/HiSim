"""What the lifecycle cost engine needs to know about a dwelling that no simulation can tell it.

A HiSim simulation models physics: it can say how big the heat pump is and how many kilowatt-hours
crossed the meter. It cannot say what stood in the cellar before, whose money is being spent, how
many square metres of facade a measure covers or what an insulation layer costs, and every one of
those decides the economics. The engine calls that half of its inputs an
:class:`~hisim.economics.bridge.EconomicContext`; this module builds one out of a RenoVisor
request and the package applied to it.

Four things go into it, and each answers a question the run cannot:

* **the existing-asset register** — the generator, the arrays, the equipment every twin carries
  (the buffer, the hot-water cylinder, the emitters, the meters) and the five envelope elements
  that were already there, with the year each was installed and which measure supersedes it. Its mere
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
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Tuple

from hisim.economics.adapter import FactsExtractors
from hisim.economics.bridge import EconomicContext
from hisim.economics.calculators.context_resolution import ContextResolutionConstants
from hisim.economics.carriers import EnergyCarrier
from hisim.economics.database import CostDatabase
from hisim.economics.evaluator import SubjectCostFacts, effective_price_basis_year
from hisim.economics.facts import (
    ComponentCostFacts,
    ExistingAsset,
    ExistingAssetRegister,
    InstallationYearOrigin,
)
from hisim.economics.parameters import EconomicParameters
from hisim.economics.subsidies import (
    ApplicantActor,
    ApplicantProfile,
    DwellingType,
    SubsidyBuildingContext,
    SubsidyContext,
)
from hisim.economics.uncertainty import UncertainValue
from hisim.loadtypes import ComponentType, Units
from hisim.renovisor.apply import MeasureRegistry
from hisim.renovisor.constants import AnywayShareByPlacement, Placement
from hisim.renovisor.request import House, Measure, Request
from hisim.renovisor.simulation import SimulationSetup
from hisim.renovisor.vocabulary import BuildingType, HeatGenerator, ReportStatus, ThermalElement
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


class TwinEquipment:
    """The equipment every twin carries that the request never describes, and what replaces it.

    Every recorded twin has a hot-water cylinder and an electricity meter, and — unless its
    generator heats the rooms directly — a space-heating buffer and the radiators or floor circuits
    the heat is carried through; a gas twin has a gas meter and a twin with a battery an energy
    management system. The request says nothing about them because they are simply there, and
    that is exactly why they must be in the register: under brownfield accounting a component
    whose class the register does not hold is bought new, so the do-nothing reference used to buy
    the house's own cylinder, buffer, radiators and meter in year 0 (renovisorissues #48).

    Each row names the component class, the catalogue measure that replaces it and the request
    block whose ``installation_year`` dates it. The size and the asset class are not in the table:
    they are what the twin realizes (:class:`RealizedTwin`), read through the cost adapter's own
    extractor so the register and the engine can never describe one vessel two ways.

    Owner decisions of 2026-09-26: ``heating_system`` replaces the buffer like-for-like as part of
    the new heating and keeps the cylinder, the emitters and the meters; ``heating_installation``
    replaces the emitters and ``hot_water_system`` the cylinder, each the same way. The buffer,
    the cylinder and the emitters take ``house.heating.installation_year`` and the controller
    ``house.battery.installation_year`` (it goes with the battery it runs) when the request states
    it. The meters have no block that dates them. Every row without a stated year is at mid-life
    (:class:`UnknownAge`, for its own asset class), never before the construction year.
    """

    @dataclass(frozen=True)
    class Equipment:
        """One component class of the twins, the measure that replaces it and what dates it.

        Args:
            component_class: The HiSim component class name, which is also the key of
                :attr:`~hisim.economics.adapter.FactsExtractors.BY_CLASS_NAME` its facts come from.
            measure_id: The catalogue measure that replaces it, or ``None`` when no measure does.
            dated_by: The ``house`` block whose ``installation_year`` it shares when the request
                states one, or ``None`` when no block dates it; either way an unstated year is the
                :class:`UnknownAge` mid-life year.
        """

        component_class: str
        measure_id: Optional[str]
        dated_by: Optional[str]

    #: Every row, in the order the register lists them.
    ALL: ClassVar[Tuple["TwinEquipment.Equipment", ...]] = (
        Equipment(component_class="SimpleHotWaterStorage", measure_id="heating_system", dated_by="heating"),
        Equipment(component_class="SimpleDHWStorage", measure_id="hot_water_system", dated_by="heating"),
        Equipment(component_class="HeatDistribution", measure_id="heating_installation", dated_by="heating"),
        Equipment(component_class="ElectricityMeter", measure_id=None, dated_by=None),
        Equipment(component_class="GasMeter", measure_id=None, dated_by=None),
        Equipment(component_class="L2GenericEnergyManagementSystem", measure_id=None, dated_by="battery"),
    )


@dataclass(frozen=True)
class RealizedTwin:
    """One translated twin with every sizable field resolved, exactly as the run will build it.

    The register sizes the equipment the house already has from the twin that describes the house
    *before* the package — its buffer from that twin's generator and litres per kilowatt, its
    cylinder from its apartment count, its emitters from its floor area — the way
    :meth:`EconomicContextBuilder._generator_size` sizes the generator: through the code the run
    itself uses rather than a second model of it. Resolving a twin's sizing needs no simulation:
    it is the loader's group expansion, class binding and sizing kernel, a few tens of
    milliseconds.

    Attributes:
        components: ``(component name, component class name, realized configuration)`` per
            component of the enabled set, in file order.
    """

    components: Tuple[Tuple[str, str, Any], ...]

    @classmethod
    def of(cls, model: Any) -> "RealizedTwin":
        """Resolve one translated energy-system document.

        Args:
            model: The :class:`~hisim.energy_system.model.EnergySystemFile` the translator built.

        Returns:
            The twin with its configurations realized.
        """
        # Function-local, like the building package in `_design_heat_load_in_kw`: the loader pulls
        # every component class in, and this module is a translation table a test drives without.
        # pylint: disable=import-outside-toplevel
        from hisim.energy_system.classes import validate_classes
        from hisim.energy_system.configure import configure_energy_system
        from hisim.energy_system.groups import expand_groups
        from hisim.energy_system.validation import validate_structure

        expanded, _record = expand_groups(model)
        validate_structure(expanded)
        bindings = validate_classes(expanded)
        configured = configure_energy_system(expanded, bindings=bindings)
        return cls(
            components=tuple(
                (binding.name, binding.component_class.__name__, configured.config_of(binding.name))
                for binding in bindings
            )
        )

    def cost_facts(self, component_class: str) -> Optional[Tuple[str, ComponentCostFacts]]:
        """The component of one class and its cost facts, as the cost adapter extracts them.

        Args:
            component_class: A key of :attr:`~hisim.economics.adapter.FactsExtractors.BY_CLASS_NAME`.

        Returns:
            ``(component name, facts)`` for the first component of that class, or ``None`` when the
            twin has none, when the adapter prices no variant of it (a low-temperature radiator)
            or when it is configured at zero size, i.e. not installed.
        """
        extractor = FactsExtractors.BY_CLASS_NAME[component_class]
        for name, class_name, config in self.components:
            if class_name != component_class:
                continue
            facts = extractor(config)
            if facts is None or facts.is_not_installed():
                return None
            return name, facts
        return None


class MeasureSubjects:
    """The measures that carry out something the cost engine prices no subject for.

    Every measure a stage acts on must have a row in ``economics_result.json``'s ``by_subject``,
    which is where the frontend reads what the work costs (owner decision of 2026-09-26,
    renovisorissues #58). An envelope measure creates a cost subject named by its id, a device
    measure the component it installs. Two measures create neither: changing the room set point
    buys nothing, and lagging the hot-water cylinder changes a coefficient of a vessel the house
    keeps. Each is declared here, and gets a subject named by its id, as an envelope measure's
    is: :attr:`COSTLESS` ones are priced at a real zero, :attr:`UNPRICED` ones are flagged
    unpriced, and each carries the sentence its row's ``note`` says.

    Anything else a stage acts on without a subject fails the translation
    (:meth:`assert_every_measure_has_subject`): the next measure that needs one fails a test, not
    a reader.
    """

    #: The statuses of a measure the translation acts on -- the ones a stage's ``measures`` list
    #: carries (``hisim.economics.__main__.StagedCli.STAGE_MEASURE_STATUSES``, which
    #: ``tests/renovisor/test_economics_context.py`` pins equal to these).
    ACTED_ON: ClassVar[Tuple[ReportStatus, ...]] = (ReportStatus.USED, ReportStatus.APPROXIMATED)

    #: Measures that cost nothing to carry out -> the note their row carries.
    COSTLESS: ClassVar[Dict[str, str]] = {
        "change_room_temperature": (
            "a setting, not a purchase: changing the room set point costs nothing to carry out, and "
            "its effect is in the energy bill"
        ),
    }

    #: Measures HiSim holds no price for and the request cannot price yet -> the note their row
    #: carries.
    UNPRICED: ClassVar[Dict[str, str]] = {
        "hot_water_tank_and_pipe_insulation": (
            "HiSim holds no price for lagging a hot-water cylinder and its pipes, and the request's "
            "cost block cannot carry one yet (HiSim hisim-5j3h, renovisorissues #39), so the measure "
            "is in the plan unpriced: its investment is unknown, not zero"
        ),
    }

    #: What the translation refuses with when a measure it acts on has no subject.
    MISSING_MESSAGE: ClassVar[str] = (
        "the package acts on {measures}, but no cost subject stands for {them}: economics_result.json "
        "would have no by_subject row for {them}, so the plan's cost would silently leave {them} out. "
        "A measure the engine prices no subject for must be declared in MeasureSubjects.COSTLESS or "
        "MeasureSubjects.UNPRICED (hisim/renovisor/economics.py), with the note its row carries"
    )

    @classmethod
    def acted_on(cls, applied: Any) -> List[str]:
        """The ids of the measures the package acts on, in package order."""
        return [line.id for line in applied.measures if line.status in cls.ACTED_ON]

    @classmethod
    def record(cls, applied: Any, result: "EconomicContextResult") -> None:
        """Give every declared measure the package acts on its own subject, flag and note.

        Args:
            applied: The applied package.
            result: The result being assembled; its ``subjects``, ``unpriced_subjects``,
                ``costless_subjects`` and ``subject_notes`` are extended.
        """
        for measure_id in cls.acted_on(applied):
            if measure_id in result.subjects.values():
                continue
            if measure_id in cls.COSTLESS:
                result.subjects[measure_id] = measure_id
                result.costless_subjects.append(measure_id)
                result.subject_notes[measure_id] = cls.COSTLESS[measure_id]
            elif measure_id in cls.UNPRICED:
                result.subjects[measure_id] = measure_id
                result.unpriced_subjects.append(measure_id)
                result.subject_notes[measure_id] = cls.UNPRICED[measure_id]

    @classmethod
    def assert_every_measure_has_subject(cls, applied: Any, result: "EconomicContextResult") -> None:
        """Refuse a translation in which a measure it acts on stands for no cost subject.

        Called by the translator once the context is built, with every twin in hand; a unit test
        that builds the context without the twins is not asked.

        Args:
            applied: The applied package.
            result: The built context and its maps.

        Raises:
            TranslatorError: Naming every measure without a subject.
        """
        named = set(result.subjects.values())
        missing = [measure_id for measure_id in cls.acted_on(applied) if measure_id not in named]
        if missing:
            raise TranslatorError(
                cls.MISSING_MESSAGE.format(
                    measures=", ".join(missing), them="it" if len(missing) == 1 else "them"
                )
            )


class UnknownAge:
    """How old an existing device is when the request does not say: half-way through its life.

    Owner decision of 2026-09-26. A device whose ``installation_year`` the request leaves out is
    assumed to be at **mid-life**: installed in the price basis year less half its service life,
    rounded to whole years, and never before the building's construction year. The earlier default,
    "as old as the house", made every undated boiler, buffer and meter of a 1975 house fifty years
    old, i.e. worn out, so the do-nothing reference replaced all of it in year 1 and the anyway
    credit of every replacement was the whole like-for-like price. Mid-life is the expectation for
    a device of unknown age in a house that is being kept running.

    It applies to the heat generator, the devices of :class:`DeviceAssets` and the equipment of
    :class:`TwinEquipment`. The envelope elements keep the construction year: they are the
    building's fabric, which is as old as the building unless the request says it was renewed.

    Both inputs are the engine's own. The price basis year is the one the run prices at
    (:func:`~hisim.economics.evaluator.effective_price_basis_year` for the calculation's country and
    simulation year, which ``economic_inputs.json`` records), and the service life is the one the
    engine reads for the asset class at that year from the cost database -- the entry's
    ``service_life_in_years``, or the engine's fallback
    (:attr:`~hisim.economics.calculators.context_resolution.ContextResolutionConstants.FALLBACK_SERVICE_LIFE_IN_YEARS`)
    for a class the database has no entry for at all, which the mapping report then says. A country
    the database has no device data for at all -- NL, which the request schema admits and the
    capability document lists -- is the same case for every class: its assets take the fallback
    too, at the price basis year the engine derives for such a country (the simulation year, as
    :func:`~hisim.economics.evaluator.effective_price_basis_year` has no earlier year to move to),
    and the note says that it is the country, not the class, the data lacks. That keeps such a
    request translatable; whether it can be *priced* is the engine's question, not the
    translator's. Any other failure of the lookup -- a class priced only from a later year in a
    country that has data -- is an error of the data and propagates.

    The database read is the **shipped** one (``CostDatabase(None)``), which is the one every
    RenoVisor calculation is priced against: the translator has no other database to consult, and a
    run pointed at a different one would age its assets by a service life it is not priced with.
    """

    #: What the mapping report says about a mid-life year.
    NOTE: ClassVar[str] = (
        "the request states no installation year, so the asset is assumed to be at mid-life: the price "
        "basis year {basis} less half its {life:g}-year service life, never before the construction "
        "year {built}"
    )

    #: What it says instead when the cost database has no entry for the class, so the service life
    #: is the engine's fallback rather than a figure of the data.
    FALLBACK_NOTE: ClassVar[str] = (
        "the request states no installation year, so the asset is assumed to be at mid-life: the price "
        "basis year {basis} less half its service life of {life:g} years, the engine's fallback: the "
        "cost database has no entry for {asset_class}; never before the construction year {built}"
    )

    #: What it says when the country has no device data at all, so both the price basis year and
    #: the service life are the engine's own choices for a country it cannot price from data.
    NO_COUNTRY_DATA_NOTE: ClassVar[str] = (
        "the request states no installation year, so the asset is assumed to be at mid-life: the price "
        "basis year {basis} (the simulation year, as the engine takes it for a country without device "
        "data) less half its service life of {life:g} years, the engine's fallback: the cost database "
        "has no device data for {country}; never before the construction year {built}"
    )

    #: The shipped cost database, loaded once: it is immutable, and loading it is the expensive part.
    _database: ClassVar[Optional[CostDatabase]] = None

    @classmethod
    def database(cls) -> CostDatabase:
        """The shipped cost database, loaded on first use."""
        if cls._database is None:
            cls._database = CostDatabase(None)
        return cls._database

    @classmethod
    def price_basis_year(cls, country: str) -> int:
        """The price basis year a RenoVisor run of this country prices at, as the engine derives it."""
        return effective_price_basis_year(EconomicParameters(country=country), cls.database(), SimulationSetup.YEAR)

    @classmethod
    def service_life(cls, asset_class: ComponentType, year: int, country: str) -> Tuple[float, bool]:
        """The service life the engine reads for one asset class, and whether it is the fallback.

        Args:
            asset_class: The class to look up.
            year: The price basis year the entry must be valid at.
            country: The calculation's country.

        Returns:
            ``(years, is_fallback)``: the entry's ``service_life_in_years`` and ``False``, or the
            engine's fallback and ``True`` when the country's data has no entry for the class, or
            when the country has no device data at all.

        Raises:
            CostDataError: When the country prices the class but not at ``year``: a fault of the
                data, not an unknown class.
        """
        database = cls.database()
        if not database.has_device_entry(asset_class, country):
            return ContextResolutionConstants.FALLBACK_SERVICE_LIFE_IN_YEARS, True
        return float(database.get_device_entry(asset_class, year, country).service_life_in_years), False

    @classmethod
    def installation_year(cls, asset_class: ComponentType, country: str, construction_year: int) -> Tuple[int, str]:
        """The mid-life installation year of one undated asset, and the sentence that says so.

        Args:
            asset_class: The asset's class, whose service life halves.
            country: The calculation's country, for the database lookup and the basis year.
            construction_year: The building's, the earliest year the asset can be from.

        Returns:
            ``(installation year, mapping-report note)``.
        """
        basis = cls.price_basis_year(country)
        life, is_fallback = cls.service_life(asset_class, basis, country)
        year = max(construction_year, basis - int(round(life / 2.0)))
        if country not in cls.database().devices:
            note = cls.NO_COUNTRY_DATA_NOTE
        else:
            note = cls.FALLBACK_NOTE if is_fallback else cls.NOTE
        return year, note.format(
            basis=basis, life=life, built=construction_year, asset_class=asset_class.name, country=country
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
        costless_subjects: The subjects of measures that cost nothing to carry out
            (:attr:`MeasureSubjects.COSTLESS`); like the unpriced subjects of
            :attr:`MeasureSubjects.UNPRICED` they are in no cost facts of the context, and the
            result document gives each a zero row of its own.
        subject_notes: Subject -> the sentence its row of the result document carries as
            ``note``: why an unpriced subject has no price, why a costless one costs nothing.
        defaults: One ``(request path, value, sentence)`` per request leaf the builder had to
            default, so the mapping report can state it with the value it used. Nothing the
            builder does is silent.
        approximations: One ``(path, value, sentence)`` per context field the builder could only
            fill with something close but not equal — today the dwelling-type band, which cannot
            tell an end-of-terrace house from a mid-terrace one. Published as ``approximated``
            lines of the mapping report, under paths of their own so that the request leaf they
            were derived from keeps the line it already has.
        unread: One ``(request path, value, sentence)`` per *stated* leaf that no figure of this
            calculation reads -- today an envelope element's installation year when no measure
            replaces or insulates the element (hisim-glv7). The translator's early ``used`` line
            for it (:meth:`EconomicContextBuilder.stated_leaves`) is replaced by an
            ``approximated`` line with the sentence, which says where the value landed and when
            it would matter.
    """

    context: EconomicContext
    subjects: Dict[str, Optional[str]] = field(default_factory=dict)
    unpriced_subjects: List[str] = field(default_factory=list)
    costless_subjects: List[str] = field(default_factory=list)
    subject_notes: Dict[str, str] = field(default_factory=dict)
    defaults: List[Tuple[str, Any, str]] = field(default_factory=list)
    approximations: List[Tuple[str, Any, str]] = field(default_factory=list)
    unread: List[Tuple[str, Any, str]] = field(default_factory=list)


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
        baseline_twin: The twin of the request's *original* house -- the house before any measure
            -- with its sizing resolved (:class:`RealizedTwin`). The equipment of
            :class:`TwinEquipment` is registered from it, at the size that twin realizes. ``None``
            registers none of it, which only a unit test of the other register rows does; the
            translator always passes it.
        plan_twin: The twin this calculation runs, resolved the same way; for a request without
            measures it is the baseline twin itself. It says what a replacing measure installs --
            the new emitters' class -- and which of the equipment components the run has, so a
            subject is stamped with its measure only where it exists. ``None`` stands for the
            baseline twin.
    """

    #: The request field naming when a device or an element was installed (E-spec §7), on the
    #: dated ``house`` blocks of the vendored schema; read when stated, defaulted to the
    #: construction year when not.
    INSTALLATION_YEAR_KEY: ClassVar[str] = "installation_year"

    #: The top-level request block naming who is applying (E-spec §7, the vendored schema's
    #: ``applicant``); read when stated, and every field it does not answer stays undetermined.
    APPLICANT_KEY: ClassVar[str] = "applicant"

    #: The key of that block naming who signs the funding application. Its values are the
    #: schema's lowercase spellings of :class:`~hisim.economics.subsidies.ApplicantActor`'s own
    #: values, so ``ApplicantActor(role.upper())`` is the whole mapping; the schema refuses every
    #: other value, so the lookup cannot miss.
    APPLICANT_ROLE_KEY: ClassVar[str] = "role"

    #: The one key of that block the schema requires (renovisorissues !19, §3.17): several bonuses
    #: depend on it, so it is read from the request and never left to the profile's default.
    APPLICANT_MAIN_RESIDENCE_KEY: ClassVar[str] = "main_residence"

    #: The other fields of that block, which the builder copies onto the applicant profile under
    #: the same name on :class:`~hisim.economics.subsidies.ApplicantProfile`. They are exactly the
    #: schema's ``applicant`` properties besides ``role``, which
    #: ``tests/renovisor/test_economics_context.py`` pins. The last three are the ones the Irish
    #: catalogue reads (step 11 §3.2/§3.3); a field the block omits stays undetermined.
    APPLICANT_FIELDS: ClassVar[Tuple[str, ...]] = (
        "taxable_household_income_in_euro",
        "household_size",
        "main_residence",
        "receives_means_tested_benefit",
        "first_time_buyer",
        "managed_full_retrofit",
    )

    #: The per-measure price block the frontend writes out of the contract's material table
    #: (E-spec §7, the vendored schema's ``measures[i].cost``). Only an envelope measure is
    #: priced from it so far (:attr:`PRICED_FROM_REQUEST`).
    COST_KEY: ClassVar[str] = "cost"

    #: Its two price fields and the provenance string beside them.
    COST_MINIMUM_KEY: ClassVar[str] = "min_in_euro_per_m2"
    COST_MAXIMUM_KEY: ClassVar[str] = "max_in_euro_per_m2"
    COST_SOURCE_KEY: ClassVar[str] = "source"

    #: The measures whose envelope cost subject that block prices: the insulation measures, which
    #: add a layer, and the two unit replacements. A block on any other measure is accepted and
    #: not read -- HiSim prices those measures from its cost database -- and the translator
    #: reports it under :attr:`UNREAD_COST_ITEM` of ``not_implemented_yet.yaml``.
    PRICED_FROM_REQUEST: ClassVar[FrozenSet[str]] = frozenset(
        (*MeasureRegistry.INSULATION, *EnvelopeAssets.UNIT_REPLACEMENTS)
    )

    #: The ``not_implemented_yet.yaml`` item for a cost block on a measure outside that set. It is
    #: one entry for every measure of the package, which the ``[]`` stands for.
    UNREAD_COST_ITEM: ClassVar[str] = "measures[].cost"

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

    #: The technical attribute the peak power of the photovoltaic array is published under. It is
    #: published as information for condition authors — a scheme that conditions on array size
    #: can read it — and no shipped scheme reads it: since hisim-cyc.3 the Irish PV grant is
    #: tiered on the cost facts' own size, which every array has. It is only published when the
    #: request pins the array's power, because an array sized as a share of the roof has no peak
    #: power until the simulation has run.
    PEAK_POWER_ATTRIBUTE: ClassVar[str] = "peak_power_in_kwp"

    #: Watt per kilowatt, for the conversion into that attribute's unit.
    WATT_PER_KILOWATT: ClassVar[float] = 1000.0

    #: The note an unpriced envelope subject carries into the mapping report.
    UNPRICED_NOTE: ClassVar[str] = (
        "no cost block on the measure, so the subject is in the economics with an investment of "
        "zero and flagged unpriced; HiSim does not estimate envelope prices (decisions Q8/Q9)"
    )

    #: The note a defaulted installation year of an envelope element carries. A device's
    #: unstated year is a mid-life year instead, with :attr:`UnknownAge.NOTE`.
    INSTALLATION_YEAR_NOTE: ClassVar[str] = (
        "the request states no installation year for this element of the building's fabric, so "
        "the building's construction year is used as its age"
    )

    #: The note a stated installation year carries.
    INSTALLATION_YEAR_USED_NOTE: ClassVar[str] = (
        "the age of the existing asset, as the request states it, for the like-for-like "
        "replacement price and the sunk-cost credit"
    )

    #: The note a stated installation year of an envelope element carries when the element has a
    #: register row but no measure of the package touches it. The engine reads an element's age
    #: only for a replaced row (the sunk cost and the anyway-cost credit, cost_spec §4.1); a kept
    #: envelope row matches no cost subject, because only a measure creates one.
    ENVELOPE_YEAR_KEPT_NOTE: ClassVar[str] = (
        "recorded as the installation year of this element's row of the existing-asset register, "
        "but no measure of the package replaces or insulates the element, so no cost line reads "
        "its age; it only matters once a measure touches the element, for the sunk cost and the "
        "anyway-cost credit"
    )

    #: The note a stated installation year of an envelope element carries when the element has no
    #: area anywhere, so the register has no row for the year to date.
    ENVELOPE_YEAR_UNREGISTERED_NOTE: ClassVar[str] = (
        "read by nothing: neither the translated building nor the request gives this element an "
        "area, so the existing-asset register has no row for it; the year only matters for an "
        "element with an area that a measure replaces or insulates, for the sunk cost and the "
        "anyway-cost credit"
    )

    #: The note a defaulted living area carries.
    LIVING_AREA_NOTE: ClassVar[str] = (
        "the request states no living area, so the conditioned floor area is used for the cost "
        "lines that scale with it"
    )

    #: The note a stated living area carries.
    LIVING_AREA_USED_NOTE: ClassVar[str] = (
        "the living area the cost lines that scale with it are taken over, as the request states it"
    )

    #: The note a cost block on an envelope measure carries.
    COST_USED_NOTE: ClassVar[str] = (
        "the price band of the envelope subject this measure creates, in euro per square metre of "
        "its element; a subject whose element has no area stays unpriced"
    )

    #: The note a stated applicant role carries.
    APPLICANT_ROLE_USED_NOTE: ClassVar[str] = (
        "who signs the funding application, which decides the programmes open to the applicant"
    )

    #: The note an omitted applicant role carries. The schema states the default (``Absent:
    #: owner_occupier``) and the profile applies it, so it is the one ``applicant`` key whose
    #: omission is a default rather than an unanswered question (hisim-p6uq).
    APPLICANT_ROLE_DEFAULTED_NOTE: ClassVar[str] = (
        "the request states no applicant role, so the applicant is taken to be the owner-occupier, "
        "as the request schema defines it; the role decides the programmes open to the applicant"
    )

    #: The note every other stated applicant field carries.
    APPLICANT_USED_NOTE: ClassVar[str] = (
        "an answer the eligibility conditions of the country's subsidy catalogue read, as the "
        "request states it"
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
        baseline_twin: Optional[RealizedTwin] = None,
        plan_twin: Optional[RealizedTwin] = None,
    ) -> None:
        """Store the inputs; nothing is read until :meth:`build`."""
        self._baseline_twin = baseline_twin
        self._plan_twin = plan_twin if plan_twin is not None else baseline_twin
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
        existing_heating = self._generator_asset(result)
        register = ExistingAssetRegister(assets=self._register_assets(result, existing_heating))
        facts = self._envelope_cost_facts(result)
        living_area = self._living_area(result)
        result.context = EconomicContext(
            existing_assets=register,
            subsidy_context=self._subsidy_context(result, existing_heating),
            extra_cost_facts=facts,
            technical_attributes_by_subject=self._technical_attributes(facts),
            living_area_in_m2=living_area,
            heated_floor_area_in_m2=self._floor_area(),
        )
        self._record_device_subjects(result)
        MeasureSubjects.record(self._applied, result)
        return result

    # ------------------------------------------------------------------ the stated leaves

    #: The ``house`` blocks whose installation year the register reads, beside the building's
    #: five envelope elements.
    DATED_HOUSE_BLOCKS: ClassVar[Tuple[str, ...]] = (
        "heating",
        *(device.house_block for device in DeviceAssets.ALL),
    )

    @classmethod
    def stated_leaves(cls, document: Mapping[str, Any]) -> List[Tuple[str, Any, str]]:
        """The economics-only leaves one request states, with the value and the report note.

        The installation years, the living area and the ``applicant`` block feed the economic
        context and no simulation component. The heat pump's rated SCOP is not one of them: it
        describes the pump in the house, which no subsidy is ever decided for, and it is physics
        (renovisorissues #8). The translator records them *before* its stages run — the fail-loud
        stage that accounts for every leftover leaf runs inside the translation, while the builder,
        which needs the translated model for its subjects, runs after it — and both spellings live
        here so they cannot drift.

        Args:
            document: The validated request.

        Returns:
            One ``(request path, value, sentence)`` per stated leaf: the house's in path order,
            then the applicant's in :attr:`APPLICANT_ROLE_KEY`, :attr:`APPLICANT_FIELDS` order.
        """
        leaves: List[Tuple[str, Any, str]] = []
        house = document.get("house", {})
        if isinstance(house, Mapping):
            leaves.extend(cls._stated_house_leaves(house))
        applicant = document.get(cls.APPLICANT_KEY)
        if isinstance(applicant, Mapping):
            for name in (cls.APPLICANT_ROLE_KEY, *cls.APPLICANT_FIELDS):
                if name in applicant:
                    note = (
                        cls.APPLICANT_ROLE_USED_NOTE if name == cls.APPLICANT_ROLE_KEY else cls.APPLICANT_USED_NOTE
                    )
                    leaves.append((f"{cls.APPLICANT_KEY}.{name}", applicant[name], note))
        return leaves

    @classmethod
    def _stated_house_leaves(cls, house: Mapping[str, Any]) -> List[Tuple[str, Any, str]]:
        """The installation years and the living area one ``house`` block states, in path order."""
        leaves: List[Tuple[str, Any, str]] = []
        for block in cls.DATED_HOUSE_BLOCKS:
            year = cls._stated_year(house.get(block))
            if year is not None:
                leaves.append(
                    (f"house.{block}.{cls.INSTALLATION_YEAR_KEY}", year, cls.INSTALLATION_YEAR_USED_NOTE)
                )
        building = house.get("building")
        if isinstance(building, Mapping):
            for element in ThermalElement:
                year = cls._stated_year(building.get(element.value))
                if year is not None:
                    leaves.append(
                        (
                            f"house.building.{element.value}.{cls.INSTALLATION_YEAR_KEY}",
                            year,
                            cls.INSTALLATION_YEAR_USED_NOTE,
                        )
                    )
            stated = cls._as_positive_float(building.get(cls.LIVING_AREA_KEY))
            if stated is not None:
                leaves.append(
                    (
                        f"house.building.{cls.LIVING_AREA_KEY}",
                        stated,
                        cls.LIVING_AREA_USED_NOTE,
                    )
                )
        return leaves

    @classmethod
    def cost_blocks(cls, document: Mapping[str, Any]) -> List[Tuple[str, Mapping[str, Any], bool]]:
        """Every ``measures[i].cost`` block one request carries, and whether the builder reads it.

        The translator reports each: a block on a measure of :attr:`PRICED_FROM_REQUEST` as used
        by the economic context, any other as ``not_implemented_yet`` under
        :attr:`UNREAD_COST_ITEM`, because HiSim prices those measures from its own cost database.

        Args:
            document: The validated request.

        Returns:
            One ``(request path, block, read)`` per measure that carries a block, in package
            order.
        """
        blocks: List[Tuple[str, Mapping[str, Any], bool]] = []
        for index, raw in enumerate(document.get("measures") or []):
            if not isinstance(raw, Mapping) or not isinstance(raw.get(cls.COST_KEY), Mapping):
                continue
            blocks.append(
                (f"measures[{index}].{cls.COST_KEY}", raw[cls.COST_KEY], raw.get("id") in cls.PRICED_FROM_REQUEST)
            )
        return blocks

    # ------------------------------------------------------------------ the register

    def _register_assets(self, result: EconomicContextResult, generator: ExistingAsset) -> List[ExistingAsset]:
        """Everything that was already in the building, with what replaces it.

        Four groups in one list, in the order a reader of the register would expect them: the
        heat generator, the devices of :class:`DeviceAssets`, the equipment of
        :class:`TwinEquipment` the baseline twin carries, and the five envelope elements. Each
        carries the year it was installed — the request's own when it states one, the building's
        construction year otherwise — and the asset classes of the measures that supersede it.

        Args:
            result: The result being assembled, for the defaulted and approximated lines.
            generator: The existing heat generator of :meth:`_generator_asset`, built once by
                :meth:`build` because the subsidy context carries the same entry.

        Returns:
            The register's entries.
        """
        assets = [generator]
        assets.extend(self._device_assets(result))
        assets.extend(self._equipment_assets(result))
        assets.extend(self._envelope_assets(result))
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
            installation_year=self._device_year("heating", asset_class, result),
            is_functional=True,
            energy_carrier=carrier,
            replaced_by_asset_classes=replaced,
            installation_year_origin=self._year_origin(
                self._raw_original.get("heating"), InstallationYearOrigin.MID_LIFE_DEFAULT
            ),
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

    def _device_assets(self, result: Optional[EconomicContextResult] = None) -> List[ExistingAsset]:
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
                    installation_year=self._device_year(device.house_block, device.asset_class, result),
                    is_functional=True,
                    replaced_by_asset_classes=(
                        [device.asset_class] if device.measure_id in self._measure_ids else []
                    ),
                    installation_year_origin=self._year_origin(block, InstallationYearOrigin.MID_LIFE_DEFAULT),
                )
            )
        return assets

    #: The mapping-report path each registered piece of equipment is reported under. It is a
    #: derived context field rather than a request leaf, like the generator's size.
    EQUIPMENT_PATH: ClassVar[str] = "economic_context.existing_assets.{component}"

    #: What the mapping report says about it.
    EQUIPMENT_NOTE: ClassVar[str] = (
        "in the house before the package, so the do-nothing reference is not charged for it: sized "
        "as the twin of the unrenovated house realizes it; dated: {dated}. The request describes no "
        "such part"
    )

    #: What it adds when a measure removes the part and the package's twin has none in its place.
    EQUIPMENT_REMOVED_NOTE: ClassVar[str] = (
        "; removed by {measure} without a successor, since the package's twin has no such part: the "
        "engine books no removal cost and no written-off book value for a part removed without "
        "replacement"
    )

    def _equipment_assets(self, result: Optional[EconomicContextResult] = None) -> List[ExistingAsset]:
        """One register entry per piece of :class:`TwinEquipment` the house has before the package.

        What the house has is what its baseline twin carries: a twin without a buffer (electric
        heating) registers none, and a heat pump that brings one is then an installation, not a
        replacement. The asset class and the size are the cost adapter's own reading of the
        realized configuration, so the entry describes the vessel exactly as the engine will price
        its successor, and the anyway credit compares like with like.

        An entry is replaced when its row's measure is in the package, by the class the plan twin
        installs in its place (the new emitters of ``heating_installation`` may be of another
        class than the old ones). Everything else is kept, and a kept entry is what keeps the
        reference from buying it.

        A measure can also take a part out without putting one in: a ``heating_system`` measure
        that installs electric heating leaves a twin with no buffer and no emitters. Such an entry
        is *not* declared replaced by its own class -- nothing of that class is bought -- and its
        ``replaced_by_asset_classes`` stays empty, which the mapping report words as a removal. The
        engine has no booking for a removal without a successor (``cost_spec.md`` §4.1 books the
        removal cost and the written-off book value only through the subject that replaces an
        asset, ``calculators/context_resolution.py``), so neither is counted, and the note says so
        rather than inventing a booking here (hisim-tqkn).

        Args:
            result: The result being assembled, for the approximation line each entry carries.

        Returns:
            The entries, in :attr:`TwinEquipment.ALL` order; none without a baseline twin.
        """
        if self._baseline_twin is None:
            return []
        assets = []
        for equipment in TwinEquipment.ALL:
            found = self._baseline_twin.cost_facts(equipment.component_class)
            if found is None:
                continue
            component, facts = found
            replaced: List[ComponentType] = []
            removed = ""
            if equipment.measure_id in self._measure_ids:
                plan_twin = self._plan_twin if self._plan_twin is not None else self._baseline_twin
                successor = plan_twin.cost_facts(equipment.component_class)
                if successor is not None:
                    replaced = [successor[1].asset_class]
                else:
                    removed = self.EQUIPMENT_REMOVED_NOTE.format(measure=equipment.measure_id)
            stated = self._stated_year(self._raw_original.get(equipment.dated_by)) if equipment.dated_by else None
            if stated is not None:
                year = stated
                dated = f"house.{equipment.dated_by}.{self.INSTALLATION_YEAR_KEY} as the request states it"
            else:
                year, dated = UnknownAge.installation_year(
                    facts.asset_class, self._request.country.value, self._original.building.construction_year
                )
            assets.append(
                ExistingAsset(
                    asset_class=facts.asset_class,
                    size=facts.size,
                    size_unit=facts.size_unit,
                    installation_year=year,
                    is_functional=True,
                    replaced_by_asset_classes=replaced,
                    installation_year_origin=(
                        InstallationYearOrigin.MID_LIFE_DEFAULT if stated is None else InstallationYearOrigin.REQUEST
                    ),
                )
            )
            if result is not None:
                result.approximations.append(
                    (
                        self.EQUIPMENT_PATH.format(component=component),
                        {
                            "asset_class": facts.asset_class.name,
                            "size": facts.size,
                            "size_unit": facts.size_unit.name,
                            "installation_year": year,
                            "replaced_by_asset_classes": [asset_class.name for asset_class in replaced],
                        },
                        self.EQUIPMENT_NOTE.format(dated=dated) + removed,
                    )
                )
        return assets

    def _envelope_assets(self, result: Optional[EconomicContextResult] = None) -> List[ExistingAsset]:
        """One register entry per envelope element, with the share it would have cost anyway.

        The element is as old as the building unless the request says otherwise, and it is
        replaced by whichever measure of the package touches it. ``anyway_share`` is the number
        that decides how much of that measure's price is credited as money the building would
        have spent regardless; it comes from
        :class:`~hisim.renovisor.constants.AnywayShareByPlacement` and is well below one for a
        first-time improvement.

        The row's installation year counts only for a replaced row: the engine reads the age of
        the asset a measure replaces, and a kept envelope row matches no cost subject. A stated
        year on an element no measure touches, or on one with no row, is recorded on
        ``result.unread`` so the mapping report does not call it used (hisim-glv7).
        """
        assets = []
        for element in ThermalElement:
            area = self._element_area(element)
            if area is None:
                self._record_unread_year(element, result, self.ENVELOPE_YEAR_UNREGISTERED_NOTE)
                continue
            replaced, share = self._replacement_of(element)
            if not replaced:
                self._record_unread_year(element, result, self.ENVELOPE_YEAR_KEPT_NOTE)
            assets.append(
                ExistingAsset(
                    asset_class=EnvelopeAssets.of_element(element),
                    size=area,
                    size_unit=Units.SQUARE_METER,
                    installation_year=self._installation_year(
                        f"building.{element.value}", result, block=self._raw_element(element)
                    ),
                    is_functional=True,
                    replaced_by_asset_classes=replaced,
                    anyway_share=share,
                    installation_year_origin=self._year_origin(
                        self._raw_element(element), InstallationYearOrigin.CONSTRUCTION_YEAR_DEFAULT
                    ),
                )
            )
        return assets

    def _record_unread_year(
        self, element: ThermalElement, result: Optional[EconomicContextResult], note: str
    ) -> None:
        """Record one envelope element's stated installation year as read by no figure.

        An unstated year needs nothing here: the request carries no leaf for it, and a row that
        exists already records its construction-year default.
        """
        year = self._stated_year(self._raw_element(element))
        if result is not None and year is not None:
            result.unread.append(
                (f"house.building.{element.value}.{self.INSTALLATION_YEAR_KEY}", year, note)
            )

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
            result.subject_notes[measure_id] = self.UNPRICED_NOTE
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

        :class:`~hisim.renovisor.request.Measure` carries a measure's id and options only, so the
        block is looked for on the raw document, which is what "read when present" means here.
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
        # No SCOP is published: the request's rated SCOP describes the heat pump already in the
        # house (renovisorissues #8), while a subsidy is only ever decided for a pump the plan
        # buys, whose SCOP nobody states. A scheme keyed on SCOP therefore stays *undetermined*.
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
        installed or enlarged is the one reported. The value is information for condition authors
        (:attr:`PEAK_POWER_ATTRIBUTE`); no shipped scheme reads it. A request that sizes the array
        as a share of the roof states no power at all, and a house with no array states zero; both
        give ``None``, because a condition on array size must then stay undetermined rather than
        be denied on a zero nobody wrote.

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

    def _subsidy_context(self, result: EconomicContextResult, existing_heating: ExistingAsset) -> SubsidyContext:
        """Who is applying and what the building is, for the eligibility conditions.

        The applicant half comes from the request's ``applicant`` block (E-spec §7), which the
        schema requires since 2026-09-24 together with its ``main_residence``: that answer is read
        from the request, never taken from the profile's default. Every other field the block does
        not answer stays ``None``, which the engine reads as
        *undetermined* and reports as a question rather than as a denial (§5.7). The three
        fields the Irish catalogue reads — ``receives_means_tested_benefit``, ``first_time_buyer``
        and ``managed_full_retrofit``, the last of which is the One Stop Shop route — are read
        from that block exactly like the others and are never inferred from anything else. The
        block's ``role`` is mapped onto the profile's actor, which decides which programmes are
        open to the applicant at all; a block that names no role leaves the profile's default
        (owner-occupier) standing, which is the engine's own assertion and the schema's stated
        default, and records a ``defaulted`` line for it (hisim-p6uq). No other key of the block
        has a default: an omitted one is a question, not a value, and gets no line.

        The building half is what the request already states: the construction year, the floor
        area, one dwelling unit (the archetype every RenoVisor calculation simulates) and the
        dwelling-type band of :class:`DwellingTypes`, which grants with per-dwelling-type amounts
        read. A band that could only be approximated is recorded on ``result.approximations``, so
        the mapping report says so.

        The building's existing heating is the generator the request's *original* house states --
        the one the package replaces -- and it is the register's own entry, not a second one built
        from the same table: the heat-pump grants ask what it is and what it burns
        (``building.existing_heating.asset_class`` and ``.energy_carrier``), and the request has
        already answered both through ``house.heating.type_of_system`` (renovisorissues #50).

        Args:
            result: The result being assembled, for the approximation the band may carry.
            existing_heating: The existing heat generator of :meth:`_generator_asset`.

        Returns:
            The context the eligibility conditions resolve against.
        """
        raw: Mapping[str, Any] = self._request.document[self.APPLICANT_KEY]
        profile = ApplicantProfile(main_residence=bool(raw[self.APPLICANT_MAIN_RESIDENCE_KEY]))
        role = raw.get(self.APPLICANT_ROLE_KEY)
        if role is not None:
            profile.actor = ApplicantActor(str(role).upper())
        else:
            result.defaults.append(
                (
                    f"{self.APPLICANT_KEY}.{self.APPLICANT_ROLE_KEY}",
                    profile.actor.value.lower(),
                    self.APPLICANT_ROLE_DEFAULTED_NOTE,
                )
            )
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
                existing_heating=existing_heating,
            ),
        )

    def _record_device_subjects(self, result: EconomicContextResult) -> None:
        """Note which measure created each simulated component, for the report's ``subjects``.

        The devices are simulation components, so the engine names their cost subjects itself —
        the component name of the energy-system file. What it cannot know is which catalogue
        measure put them there, and that is exactly what the result document stamps on the
        investment build-up, so it is recorded here.

        The equipment of :class:`TwinEquipment` a measure replaces is stamped with that measure
        -- the buffer a ``heating_system`` measure installs with the new generator carries
        ``heating_system`` -- where the run has the component at all. Kept equipment is stamped
        with nothing, which the result document reads as a subject of the house as it was.
        """
        for device in DeviceAssets.ALL:
            if device.measure_id in self._measure_ids:
                result.subjects[device.component] = device.measure_id
        if self._generator_component and self.HEATING_MEASURE_ID in self._measure_ids:
            result.subjects[self._generator_component] = self.HEATING_MEASURE_ID
        if self._plan_twin is None:
            return
        for equipment in TwinEquipment.ALL:
            if equipment.measure_id is None or equipment.measure_id not in self._measure_ids:
                continue
            installed = self._plan_twin.cost_facts(equipment.component_class)
            if installed is not None:
                result.subjects[installed[0]] = equipment.measure_id

    # ------------------------------------------------------------------ small readers

    def _device_year(
        self, block_name: str, asset_class: ComponentType, result: Optional[EconomicContextResult] = None
    ) -> int:
        """The installation year of one existing device: the request's own, else mid-life.

        A stated year (:meth:`_stated_year`) is read as it stands; its ``used`` line is the
        translator's early recording (:meth:`stated_leaves`). An unstated one is the
        :class:`UnknownAge` mid-life year of the device's asset class, recorded on the result's
        ``approximations`` under the block's ``installation_year`` path with the reason. The
        envelope elements do not come here: they keep the construction year
        (:meth:`_installation_year`).

        Args:
            block_name: The ``house`` key the device lives under, e.g. ``"heating"``.
            asset_class: Its asset class, whose service life halves.
            result: The result being assembled, for the ``approximated`` line.

        Returns:
            The year the device was installed.
        """
        stated = self._stated_year(self._raw_original.get(block_name))
        if stated is not None:
            return stated
        year, note = UnknownAge.installation_year(
            asset_class, self._request.country.value, self._original.building.construction_year
        )
        if result is not None:
            result.approximations.append((f"house.{block_name}.{self.INSTALLATION_YEAR_KEY}", year, note))
        return year

    def _installation_year(
        self,
        block_name: str,
        result: Optional[EconomicContextResult] = None,
        block: Optional[Mapping[str, Any]] = None,
    ) -> int:
        """The installation year of one envelope element, defaulting to the construction year.

        The envelope is the building's fabric, as old as the building unless the request says it
        was renewed, so an undated element takes the construction year; every *device* -- the
        generator, the arrays, the equipment -- takes its mid-life year instead
        (:meth:`_device_year`, :class:`UnknownAge`).

        A year the request states (:meth:`_stated_year`) is read as it stands; one it omits is
        recorded on the result's ``defaults`` list with the construction year that stood in, so
        the mapping report says which figure the asset's age is. A stated year's ``used`` line is
        the translator's own early recording (:meth:`stated_leaves`), which runs before the
        fail-loud stage.

        Args:
            block_name: The ``house`` key the part lives under, for the mapping-report path.
            result: The result being assembled, for the ``defaulted`` line.
            block: The raw block to read, when it is not ``house[block_name]`` — an envelope
                element lives one level down, under ``house.building``.

        Returns:
            The year the part was installed.
        """
        raw = self._raw_original.get(block_name) if block is None else block
        path = f"house.{block_name}.{self.INSTALLATION_YEAR_KEY}"
        stated = self._stated_year(raw)
        if stated is not None:
            return stated
        year = self._original.building.construction_year
        if result is not None:
            result.defaults.append((path, year, self.INSTALLATION_YEAR_NOTE))
        return year

    @classmethod
    def _year_origin(cls, block: Any, default: InstallationYearOrigin) -> InstallationYearOrigin:
        """Where the installation year the register records for one block came from.

        Args:
            block: The raw block the year is read from, as :meth:`_stated_year` reads it.
            default: What stands in when the block states none: the mid-life year of a device,
                the construction year of an envelope element.

        Returns:
            ``REQUEST`` for a stated year, ``default`` otherwise.
        """
        return InstallationYearOrigin.REQUEST if cls._stated_year(block) is not None else default

    @classmethod
    def _stated_year(cls, block: Any) -> Optional[int]:
        """The installation year one raw ``house`` block states, or ``None`` when it states none.

        The one place that decides whether a year is stated, for the register and for the
        translator's early ``used`` line alike, so the two cannot disagree about one request. The
        schema's ``integer`` accepts an integral float such as ``2008.0``, so that is a stated
        year too, and read as the ``int`` it is; a boolean is not a year, although ``True`` is an
        ``int`` in Python.

        Args:
            block: The raw block, e.g. ``house["heating"]``; anything that is not a mapping states
                no year.

        Returns:
            The stated year, or ``None``.
        """
        if not isinstance(block, Mapping):
            return None
        value = block.get(cls.INSTALLATION_YEAR_KEY)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

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

    def _living_area(self, result: Optional[EconomicContextResult] = None) -> Optional[float]:
        """The dwelling's living area: the request's own, else the conditioned floor area.

        A fallen-back area is recorded on the result's ``defaults`` list, so the mapping report
        says which figure the cost lines scale with; a stated one is covered by the translator's
        own early recording (:meth:`stated_leaves`).

        Args:
            result: The result being assembled, for the ``defaulted`` line.

        Returns:
            The living area in square metres, when one is known.
        """
        building = self._raw_original.get("building")
        if isinstance(building, Mapping):
            stated = self._as_positive_float(building.get(self.LIVING_AREA_KEY))
            if stated is not None:
                return stated
        area = self._floor_area()
        if area is not None and result is not None:
            result.defaults.append(
                (f"house.building.{self.LIVING_AREA_KEY}", area, self.LIVING_AREA_NOTE)
            )
        return area

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
