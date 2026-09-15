"""The closed vocabularies the RenoVisor contract carries and HiSim does not already define.

A *vocabulary* here is a finite set of strings that may appear in a RenoVisor request or in a
post-measure home inventory: the eleven heat generators, the three ventilation types, and so on.
Decision C3 of ``roadmap/renovisor/challenges.md`` §9 fixed the spelling of all of them as HiSim's
own wire spelling — member names in ``UPPER_SNAKE``, values equal to the names — so that a value
travelling from a request through the translation layer into an energy-system file is spelled the
same way at every hop::

    from hisim.renovisor.vocabulary import HeatGenerator
    HeatGenerator.HEAT_PUMP.value        # 'HEAT_PUMP', the string written into the inventory

Why they live in one module: the contract PR of step 6 generates the contract's enum lists *from*
these classes, so a vocabulary defined twice would be a contract break waiting to happen. Nothing
is added here that is not a contract vocabulary, and a vocabulary HiSim already owns is re-exported
rather than restated — :class:`HeatDistribution` is
:class:`hisim.components.heat_distribution_system.HeatDistributionSystemType` under a name that
lets callers import one module.

The two construction vocabularies (:class:`FloorConstruction`, :class:`WallConstruction`) are the
inventory facts decision Q3 adds so that HiSim can refuse a measure that does not fit the building.
The vendored contract does not carry them yet; :class:`hisim.renovisor.inventory.PendingContractPaths`
lists the paths that are still missing.
"""

from enum import Enum
from typing import Type, TypeVar

from hisim.components.heat_distribution_system import HeatDistributionSystemType

#: Type variable over the vocabularies of this module, so that a case-tolerant lookup returns
#: the member type it was asked for rather than a bare ``Enum``.
VocabularyMember = TypeVar("VocabularyMember", bound=Enum)

#: The heat distribution vocabulary, re-exported from the component that owns it so that every
#: caller of the translation layer imports its vocabularies from one module. Members:
#: ``FLOORHEATING``, ``RADIATOR``, ``LOW_TEMPERATURE_RADIATOR``.
HeatDistribution = HeatDistributionSystemType


class DwellingType(str, Enum):
    """The kind of dwelling the household lives in, as the home form asks for it.

    This is the homeowner's answer, not a HiSim internal: the translation layer derives the TABULA
    building type from it together with the country, so that no field asks a homeowner for
    ``SFH`` or ``AB``. ``DETACHED_SFH``, ``SEMI_DETACHED_SFH`` and ``BUNGALOW`` derive ``SFH``,
    ``TERRACED_SFH`` derives ``TH`` and ``APARTMENT`` derives ``AB``; ``OTHER`` is the escape the
    form offers and is resolved by the same age-band fallback an unusable TABULA row uses.

    Added for the rewritten input contract, which replaces ``tabula_building_type`` with the
    question the user can actually answer (challenges §12).
    """

    DETACHED_SFH = "DETACHED_SFH"
    SEMI_DETACHED_SFH = "SEMI_DETACHED_SFH"
    TERRACED_SFH = "TERRACED_SFH"
    BUNGALOW = "BUNGALOW"
    APARTMENT = "APARTMENT"
    OTHER = "OTHER"


class HeatGenerator(str, Enum):
    """The device that produces space heat, as the catalogue's ``heating system`` measure names it.

    These are the eleven values of ``HEATING_SYSTEM.type_of_system`` and, after the contract PR,
    of the inventory field ``energy_system_config.heating_system.system``. The list is wider than
    the set anyone would newly install, because a building's *existing* generator has to be
    expressible too: ``OIL_HEATING`` is a valid starting state and a poor outcome.

    Not every member is simulable today. ``HYBRID_HEAT_PUMP`` has no HiSim component and
    ``HVO_HEATING`` no recorded base file; both are refused by the registry (decision Q14).
    ``BIOMASS_HEATING`` is simulated as ``PELLET_HEATING`` and reported as approximated.
    """

    HEAT_PUMP = "HEAT_PUMP"
    HYBRID_HEAT_PUMP = "HYBRID_HEAT_PUMP"
    ELECTRIC_HEATING = "ELECTRIC_HEATING"
    BIOMASS_HEATING = "BIOMASS_HEATING"
    PELLET_HEATING = "PELLET_HEATING"
    WOODCHIP_HEATING = "WOODCHIP_HEATING"
    DISTRICT_HEATING = "DISTRICT_HEATING"
    HVO_HEATING = "HVO_HEATING"
    HYDROGEN_HEATING = "HYDROGEN_HEATING"
    GAS_HEATING = "GAS_HEATING"
    OIL_HEATING = "OIL_HEATING"


class SecondaryHeating(str, Enum):
    """A second heat source the dwelling has beside its main generator, as the home form asks.

    ``OPEN_FIREPLACE`` is a chimney fire, ``WOOD_STOVE`` a closed room heater and
    ``ELECTRIC_HEATER`` a plug-in heater used in one room. None of them is simulated: HiSim models
    one space-heat generator per energy system, so the value is recorded, reported as
    ``non_simulation`` and priced by nothing until a second generator exists.
    """

    OPEN_FIREPLACE = "OPEN_FIREPLACE"
    WOOD_STOVE = "WOOD_STOVE"
    ELECTRIC_HEATER = "ELECTRIC_HEATER"


class DhwSupply(str, Enum):
    """How domestic hot water is produced, as the ``hot water system`` measure names it.

    ``FROM_SPACE_HEATING_GENERATOR`` is the common case, in which the boiler or heat pump that
    heats the rooms also makes the hot water; it replaces the inventory's older boolean
    ``with_dhw_preparation``, so that the three answers the user can give sit in one field instead
    of a flag and an enum that could contradict each other. ``HEAT_PUMP`` is a separate hot-water
    heat pump feeding the domestic hot water storage, and ``DIRECT_ELECTRIC`` is an immersion
    heater. The last has no HiSim component and is refused (decision Q14).
    """

    FROM_SPACE_HEATING_GENERATOR = "FROM_SPACE_HEATING_GENERATOR"
    HEAT_PUMP = "HEAT_PUMP"
    DIRECT_ELECTRIC = "DIRECT_ELECTRIC"


class SolarThermalSupplies(str, Enum):
    """What a solar thermal collector feeds, as the ``solar thermal system`` measure names it.

    A collector that only preheats domestic hot water (``DHW_ONLY``) touches nothing about space
    heating and is by far the common case; one that also feeds the heating circuit
    (``DHW_AND_SPACE_HEATING``) does. The recorded base files wire a collector one way only, so
    the other two values are refused until a file exists for them (decision Q18).
    """

    DHW_ONLY = "DHW_ONLY"
    SPACE_HEATING_ONLY = "SPACE_HEATING_ONLY"
    DHW_AND_SPACE_HEATING = "DHW_AND_SPACE_HEATING"


class PvOrientation(str, Enum):
    """The compass orientation of a roof-mounted solar surface, as the home form asks for it.

    The form offers four answers rather than a degree, because a homeowner knows which way the
    roof faces and not its azimuth; the translation layer turns the member into the azimuth the
    photovoltaic and solar-thermal components take (``SOUTH`` is 180 degrees). ``EAST_WEST`` is
    the split array that covers both roof planes and has no single azimuth, which is why it is a
    member here and not a number.
    """

    SOUTH = "SOUTH"
    SOUTH_EAST = "SOUTH_EAST"
    SOUTH_WEST = "SOUTH_WEST"
    EAST_WEST = "EAST_WEST"


class VentilationType(str, Enum):
    """The mechanical ventilation unit a dwelling ends up with.

    ``MECHANICAL_EXTRACT`` is a constant-rate extract fan, ``DEMAND_CONTROLLED_EXTRACT`` the same
    with humidity or CO2 control, and ``MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY`` a balanced
    supply-and-extract unit with a heat exchanger. HiSim has no ventilation submodel for the MVP,
    so the measure validates the value and then reports that nothing changed (decision Q15).
    """

    MECHANICAL_EXTRACT = "MECHANICAL_EXTRACT"
    DEMAND_CONTROLLED_EXTRACT = "DEMAND_CONTROLLED_EXTRACT"
    MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY = "MECHANICAL_VENTILATION_WITH_HEAT_RECOVERY"


class TemperatureControl(str, Enum):
    """The heating control the dwelling ends up with: plain thermostats or a smart control system.

    Both values are accepted and neither changes a simulation, because the difference between them
    is a control schedule HiSim does not model for the MVP.
    """

    TRADITIONAL_THERMOSTATS = "TRADITIONAL_THERMOSTATS"
    SMART_HEATING_CONTROL_SYSTEM = "SMART_HEATING_CONTROL_SYSTEM"


class RetrofitStatus(str, Enum):
    """The envelope refurbishment level of the building as it stands.

    It selects the TABULA building-code variant suffix: ``UNRENOVATED`` is ``.001`` (existing),
    ``USUAL_REFURB`` ``.002`` and ``ADVANCED_REFURB`` ``.003``. The vendored contract still spells
    these values in lower case; readers of the inventory therefore normalise the case before
    looking a member up, which :meth:`VocabularyLookup.member` does.
    """

    UNRENOVATED = "UNRENOVATED"
    USUAL_REFURB = "USUAL_REFURB"
    ADVANCED_REFURB = "ADVANCED_REFURB"


class TabulaBuildingType(str, Enum):
    """The TABULA building typology of the dwelling.

    ``SFH`` is a single-family house, ``TH`` a terraced house, ``MFH`` a multi-family house and
    ``AB`` an apartment block. The Irish TABULA data has no usable ``MFH`` rows, which the
    archetype lookup of :mod:`hisim.renovisor.tabula_ie` turns into a refusal.
    """

    SFH = "SFH"
    TH = "TH"
    MFH = "MFH"
    AB = "AB"


class ThermalElement(str, Enum):
    """One of the five envelope elements the inventory's ``envelope_details`` block carries.

    Every envelope measure resolves onto exactly one of them, and every element carries one
    U-value that all measures acting on it compose into (decision F5 of the mockup footer). The
    condition assessment's nine elements and the materials database's fourteen application areas
    are *not* mapped onto here; they belong to the catalogue and the frontend.
    """

    ROOF = "ROOF"
    FACADE = "FACADE"
    FLOOR = "FLOOR"
    WINDOW = "WINDOW"
    DOOR = "DOOR"


class FloorConstruction(str, Enum):
    """How the lowest storey of the dwelling is built, as a survey fact.

    It decides which floor measures fit: a ``SLAB_ON_GROUND`` floor can be insulated above the
    slab but has no basement ceiling, a ``SUSPENDED_TIMBER`` floor is insulated between the joists
    from the crawl space, and the two basement constructions carry the three basement measures.
    Added to the inventory by decision Q3; not in the vendored contract yet.
    """

    SLAB_ON_GROUND = "SLAB_ON_GROUND"
    SUSPENDED_TIMBER = "SUSPENDED_TIMBER"
    OVER_UNHEATED_BASEMENT = "OVER_UNHEATED_BASEMENT"
    OVER_HEATED_BASEMENT = "OVER_HEATED_BASEMENT"


class WallConstruction(str, Enum):
    """How the external wall is built, as a survey fact.

    ``CAVITY`` walls have a fillable void between two leaves and are the only construction cavity
    wall insulation applies to; ``SOLID`` and ``TIMBER_FRAME`` walls are insulated on one of their
    faces instead. Added to the inventory by decision Q3; not in the vendored contract yet.
    """

    SOLID = "SOLID"
    CAVITY = "CAVITY"
    TIMBER_FRAME = "TIMBER_FRAME"


class RoofForm(str, Enum):
    """The shape of the main roof, as a survey fact.

    It is an input of the rooftop photovoltaic sizing law, which reads the form together with the
    roof orientation and falls back to the TABULA roof geometry when neither is stated (decision
    Q16). ``ROOM_IN_ROOF`` is a pitched roof whose space is inhabited, which the envelope
    measures treat differently from a cold attic because there is no loft floor to roll insulation
    out over.
    """

    PITCHED = "PITCHED"
    FLAT = "FLAT"
    ROOM_IN_ROOF = "ROOM_IN_ROOF"


class Drivetrain(str, Enum):
    """What a household vehicle runs on.

    One list of vehicles carries one member each, and the member decides which consumption field
    of the entry applies: ``ELECTRIC`` cars state a consumption in kilowatt hours per kilometre
    and are the only ones that reach the electricity simulation, while ``GASOLINE`` and
    ``DIESEL`` cars state litres per hundred kilometres and contribute fuel cost and emissions to
    the household baseline so that a switch to an electric car compares against a complete
    picture.
    """

    ELECTRIC = "ELECTRIC"
    GASOLINE = "GASOLINE"
    DIESEL = "DIESEL"


class Provenance(str, Enum):
    """Where a value in a result came from, per decision Q21.

    ``SIMULATED`` means HiSim computed it from the simulation, ``MOCKED`` that it is a constant
    standing in for a model that does not exist, and ``PARTIAL`` that a real computation used at
    least one mocked or unreviewed input. Used from step 5 on, when results are written; defined
    here because it is a contract vocabulary and the contract PR generates its enum list from
    this module.
    """

    SIMULATED = "SIMULATED"
    MOCKED = "MOCKED"
    PARTIAL = "PARTIAL"


class VocabularyLookup:
    """Case-tolerant lookup of a vocabulary member from a raw contract value.

    The vendored contract still spells its inventory enum values in lower case
    (``"unrenovated"``) while decision C3 fixed HiSim's spelling as ``UPPER_SNAKE``
    (``"UNRENOVATED"``). Until the contract PR of step 6 lands, both spellings reach the
    translation layer, so every read of a vocabulary value goes through this one place::

        VocabularyLookup.member(RetrofitStatus, "unrenovated")   # RetrofitStatus.UNRENOVATED

    Writes never need it: the translation layer writes the HiSim spelling only.
    """

    @classmethod
    def member(cls, vocabulary: Type[VocabularyMember], raw: object) -> VocabularyMember:
        """Return the member of *vocabulary* whose name matches *raw*, ignoring case.

        Args:
            vocabulary: An ``Enum`` subclass from this module whose member names are the values.
            raw: The value read from a request or an inventory; converted to ``str`` first.

        Returns:
            The matching enum member.

        Raises:
            KeyError: When no member's name equals ``str(raw).upper()``.
        """
        return vocabulary[str(raw).strip().upper()]
