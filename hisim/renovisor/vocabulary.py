"""The closed vocabularies a calculation request carries, spelled as the catalogue spells them.

A *vocabulary* here is a finite set of strings that may appear in a RenoVisor calculation request:
the seventeen heat generators, the three heat distribution systems, and so on. Decision D-B of
``roadmap/renovisor/challenges.md`` §13 fixed the wire spelling as the measure catalogue's own --
lowercase ``snake_case`` -- and reconciled it with HiSim's ``UPPER_SNAKE`` convention by giving
every enum here the catalogue's string as its *value* while keeping the member name in HiSim's
spelling::

    from hisim.renovisor.vocabulary import HeatGenerator
    HeatGenerator.AIR_SOURCE_HEAT_PUMP.value    # 'air_source_heat_pump', what the request carries
    HeatGenerator.AIR_SOURCE_HEAT_PUMP.name     # 'AIR_SOURCE_HEAT_PUMP', how HiSim would spell it

So the "map" between the two vocabularies is the enum definition itself, there is no value table
anywhere, and the energy-system file keeps writing member names. Where a request value must reach
a HiSim enum that already exists, the vocabulary carries the translation as a property of the
member rather than as a lookup table: :attr:`HeatDistributionType.hisim_member` is the whole
mapping of §3.7 of the contract.

Nothing is added here that a request cannot carry. Every value below is a string the vendored
``measures.yaml`` (at the contract commit ``hisim/renovisor/contract/PINNED.yaml`` records) or
``calculation-request.schema.json`` spells, and ``tests/renovisor/test_vocabulary.py`` asserts
that member for member, so the two cannot drift.
"""

from enum import Enum

from hisim.components.heat_distribution_system import HeatDistributionSystemType


class HeatGenerator(str, Enum):
    """The device that produces space heat, as ``heating.type_of_system`` names it.

    These are the sixteen values of the catalogue's ``heating_system.type_of_system`` option plus
    ``SOLID_FUEL_HEATING``, which the request schema adds because a building's *existing*
    generator has to be expressible even when nobody would newly install it (F-spec §3.6). The
    list is therefore wider than the set of outcomes a package can produce.

    Not every member has its own model. Which ones are approximated by another and which are
    accepted without acting on them is not decided here but in ``not_implemented_yet.yaml``; this
    class only says what the word is.
    """

    AIR_SOURCE_HEAT_PUMP = "air_source_heat_pump"
    GROUND_SOURCE_HEAT_PUMP = "ground_source_heat_pump"
    HYBRID_HEAT_PUMP = "hybrid_heat_pump"
    ELECTRIC_HEATING = "electric_heating"
    BIOMASS_HEATING = "biomass_heating"
    PELLET_HEATING = "pellet_heating"
    WOODCHIP_HEATING = "woodchip_heating"
    DISTRICT_HEATING = "district_heating"
    HVO_HEATING = "hvo_heating"
    HYDROGEN_HEATING = "hydrogen_heating"
    CONVENTIONAL_GAS_HEATING = "conventional_gas_heating"
    CONVENTIONAL_OIL_HEATING = "conventional_oil_heating"
    CONVENTIONAL_LPG_HEATING = "conventional_lpg_heating"
    CONDENSING_GAS_HEATING = "condensing_gas_heating"
    CONDENSING_OIL_HEATING = "condensing_oil_heating"
    CONDENSING_LPG_HEATING = "condensing_lpg_heating"
    SOLID_FUEL_HEATING = "solid_fuel_heating"


class HeatDistributionType(str, Enum):
    """How the heat reaches the rooms, as ``heat_distribution.type_of_system`` names it.

    ``SURFACE_HEATING`` is underfloor or wall heating, ``LOW_TEMPERATURE_RADIATOR`` an oversized
    radiator sized for a heat pump, and ``CONVENTIONAL_RADIATOR`` an ordinary one. HiSim owns the
    same vocabulary under different spellings, so the member carries the HiSim member it becomes
    (:attr:`hisim_member`) and the translator writes that member's *name* into the energy-system
    file, which is what ``HeatDistributionController.config.heating_system`` reads.
    """

    SURFACE_HEATING = "surface_heating"
    LOW_TEMPERATURE_RADIATOR = "low_temperature_radiator"
    CONVENTIONAL_RADIATOR = "conventional_radiator"

    @property
    def hisim_member(self) -> HeatDistributionSystemType:
        """Return the HiSim member this request value names.

        Returns:
            ``FLOORHEATING`` for surface heating, ``LOW_TEMPERATURE_RADIATOR`` for the
            low-temperature radiator and ``RADIATOR`` for the conventional one -- the mapping of
            the contract's §3.7, held once and nowhere else.
        """
        if self is HeatDistributionType.SURFACE_HEATING:
            return HeatDistributionSystemType.FLOORHEATING
        if self is HeatDistributionType.LOW_TEMPERATURE_RADIATOR:
            return HeatDistributionSystemType.LOW_TEMPERATURE_RADIATOR
        return HeatDistributionSystemType.RADIATOR


class HotWaterSupply(str, Enum):
    """How domestic hot water is produced, as ``hot_water.supply`` names it.

    ``TOGETHER_WITH_HEATING_SYSTEM`` is the common case, in which the generator that heats the
    rooms also makes the hot water. ``SEPARATE_HEAT_PUMP`` is a dedicated hot-water heat pump and
    ``SEPARATE_DIRECT_ELECTRIC`` an immersion heater.
    """

    TOGETHER_WITH_HEATING_SYSTEM = "together_with_heating_system"
    SEPARATE_HEAT_PUMP = "separate_heat_pump"
    SEPARATE_DIRECT_ELECTRIC = "separate_direct_electric"


class VentilationType(str, Enum):
    """The ventilation the dwelling has, as ``ventilation.type_of_system`` names it.

    ``NATURAL`` is windows and leakage alone; ``WINDOW_TRICKLE_VENT`` adds permanent slot vents;
    ``MECHANICAL_EXTRACT`` is a constant-rate extract fan, ``DEMAND_CONTROLLED_EXTRACT`` the same
    with humidity or CO2 control, and ``MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY`` a balanced
    supply-and-extract unit with a heat exchanger.
    """

    NATURAL = "natural"
    WINDOW_TRICKLE_VENT = "window_trickle_vent"
    MECHANICAL_EXTRACT = "mechanical_extract"
    DEMAND_CONTROLLED_EXTRACT = "demand_controlled_extract"
    MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY = "mechanical_ventilation_with_heat_recovery"


class AirTightness(str, Enum):
    """How airtight the envelope is, as ``ventilation.air_tightness`` names it.

    ``AS_BUILT`` is the building as it stands, ``DIY_SEALED`` the result of the homeowner's own
    draught-proofing, and ``PROFESSIONALLY_SEALED`` the result of a measured airtightness job.
    """

    AS_BUILT = "as_built"
    DIY_SEALED = "diy_sealed"
    PROFESSIONALLY_SEALED = "professionally_sealed"


class TemperatureControl(str, Enum):
    """The heating control the dwelling has, as ``temperature_control.type_of_system`` names it.

    ``TRADITIONAL_THERMOSTATS`` is a room thermostat and thermostatic valves;
    ``SMART_HEATING_CONTROL_SYSTEM`` is a scheduling controller with setback.
    """

    TRADITIONAL_THERMOSTATS = "traditional_thermostats"
    SMART_HEATING_CONTROL_SYSTEM = "smart_heating_control_system"


class WhiteAppliances(str, Enum):
    """The state of the large household appliances, as ``appliances.white_appliances`` names it.

    ``EXISTING`` keeps whatever the household has; ``NEW_EFFICIENT`` is the replacement the
    catalogue's ``replace_white_appliances`` measure describes.
    """

    EXISTING = "existing"
    NEW_EFFICIENT = "new_efficient"


class SolarThermalSupplies(str, Enum):
    """What a solar thermal collector feeds, as ``solar_thermal_system.supplies`` names it.

    ``DHW_ONLY`` preheats domestic hot water and touches nothing about space heating, which is by
    far the common case; ``DHW_AND_SPACE_HEATING`` also feeds the heating circuit.
    """

    DHW_ONLY = "dhw_only"
    DHW_AND_SPACE_HEATING = "dhw_and_space_heating"


class CollectorType(str, Enum):
    """The kind of solar thermal collector, as ``solar_thermal_system.collector_type`` names it.

    ``FLAT_PLATE`` is the glazed flat absorber, ``EVACUATED_TUBE`` the vacuum tube array.
    """

    FLAT_PLATE = "flat_plate"
    EVACUATED_TUBE = "evacuated_tube"


class BuildingType(str, Enum):
    """The kind of dwelling, as ``building.building_type`` names it.

    This is the homeowner's answer, not a TABULA typology code: the translator derives the
    typology from it together with the country (:mod:`hisim.renovisor.tabula`), so no form field
    asks a homeowner for ``SFH`` or ``AB``. ``OTHER`` is the escape the form offers.
    """

    DETACHED_SFH = "detached_sfh"
    SEMI_DETACHED_SFH = "semi_detached_sfh"
    TERRACED_SFH = "terraced_sfh"
    BUNGALOW = "bungalow"
    APARTMENT = "apartment"
    OTHER = "other"


class RoofShape(str, Enum):
    """The shape of the main roof, as ``building.roof.shape`` names it.

    It has no ``Building`` target: its only effect is the default tilt of a photovoltaic array or
    a solar thermal collector mounted on it (:mod:`hisim.renovisor.constants`).
    """

    PITCHED = "pitched"
    FLAT = "flat"


class FrameMaterial(str, Enum):
    """What a window or door frame is made of, as ``frame_material`` names it.

    Provenance and a measure write target only: ``Building`` has no frame parameter, and the
    element's U-value carries the whole opening.
    """

    WOOD = "wood"
    PLASTIC = "plastic"
    METAL = "metal"
    COMPOSITE = "composite"


class Country(str, Enum):
    """The country the dwelling stands in, as ``location.country`` names it.

    It selects the weather (``Weather.for_location``) and the TABULA country. ``ES`` is in the
    request schema and has no ``.N.`` TABULA typology, so a Spanish request is refused with
    ``location.country.unsupported`` until one is chosen -- a data gap, not a feature gap.
    """

    IE = "IE"
    ES = "ES"
    NL = "NL"


class Provenance(str, Enum):
    """Where a value in ``result.json`` came from, per decision Q21.

    ``SIMULATED`` means HiSim computed it from the simulation, ``MOCKED`` that it is a constant
    standing in for a model that does not exist, and ``PARTIAL`` that a real computation used at
    least one mocked or unreviewed input. This is the only vocabulary in this module that no
    request carries: it belongs to the result payload, and lives here because it is a contract
    vocabulary like the others.
    """

    SIMULATED = "SIMULATED"
    MOCKED = "MOCKED"
    PARTIAL = "PARTIAL"


class ReportStatus(str, Enum):
    """What the translation did with one request leaf, one measure or one option.

    ``USED`` -- taken as given and written to a HiSim target. ``APPROXIMATED`` -- represented by
    something close but not equal, with the note saying what stands in for what. ``DEFAULTED`` --
    absent from the request and replaced by a documented default, with the value in the line.
    ``NOT_IMPLEMENTED_YET`` -- accepted, written into the house, and left without a target; only
    an entry of ``not_implemented_yet.yaml`` may produce it, and the line carries that entry's
    note verbatim.
    """

    USED = "used"
    APPROXIMATED = "approximated"
    DEFAULTED = "defaulted"
    NOT_IMPLEMENTED_YET = "not_implemented_yet"

    @classmethod
    def worst_of(cls, *statuses: "ReportStatus") -> "ReportStatus":
        """Return the least favourable of several statuses.

        The order is ``used`` < ``defaulted`` < ``approximated`` < ``not_implemented_yet``: a
        default is a documented substitution of an absent value, an approximation replaces a
        value that *was* given, and a not-implemented item drops it. The capability document
        announces the worst status any probe observed, so that a run can never report an option
        worse than the document promised.

        Args:
            *statuses: The statuses observed; at least one.

        Returns:
            The worst of them.

        Raises:
            ValueError: When no status is given, because there is no neutral element to return.
        """
        if not statuses:
            raise ValueError("worst_of needs at least one status")
        order = (cls.USED, cls.DEFAULTED, cls.APPROXIMATED, cls.NOT_IMPLEMENTED_YET)
        return max(statuses, key=order.index)


class ThermalElement(str, Enum):
    """One of the five envelope elements ``house.building`` carries.

    Every envelope measure resolves onto exactly one of them, every one of them has a required
    U-value in the request, and each lands on
    ``Building.config.<element>_u_value_in_watt_per_m2_per_kelvin`` and ``<element>_area_in_m2``.
    The value is the request's own block name, so the element is its own path segment.
    """

    ROOF = "roof"
    FACADE = "facade"
    FLOOR = "floor"
    WINDOW = "window"
    DOOR = "door"
