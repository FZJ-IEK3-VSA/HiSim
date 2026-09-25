"""Every number the translator writes that a simulation did not produce, in one reviewable place.

The physics owner reviews this module, not the code. Each constant carries a comment naming its
source; every constant whose source is an estimate rather than a measurement or a HiSim class
default is marked **TO BE REVIEWED**, which is the list §12 of the calculation-request
specification asks someone who owns the physics to work through.

Nothing else in the package writes a number of its own. A registry function that needs a default
thickness asks :class:`LayerDefaults`; a window replacement that needs a U-value asks
:class:`OpeningUValues`; the translator that needs a tilt asks :class:`RoofDefaults`. Two numbers
the earlier draft held here are gone: the per-country heating reference temperature, because
TABULA carries the climate region's own ``Theta_e_Base`` on every row (review §6), and the daily
electricity per resident, because the MVP sizes a battery from the CHR01 profile the run itself
uses rather than from a table (decision D-C).
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, Generic, Tuple, TypeVar

#: The type of a grade; every scale in use grades on the contract's shared Rating 1..5 (an ``int``).
Grade = TypeVar("Grade")


class Placement(str, Enum):
    """Where in a build-up an insulation layer goes, as ``materials.yaml`` spells it.

    The ten ``building_components`` values of the material catalogue. They are the key three
    different tables are read by — which element a measure insulates and with what layer
    (``apply.MeasureRegistry.INSULATION``), what share of the measure the building would have paid
    anyway (:class:`AnywayShareByPlacement`), and which cost-database asset class prices the new
    layer (``economics.EnvelopeAssets.BY_PLACEMENT``) — so they live here once instead of as
    literal strings in each. A placement added to the catalogue is then one member plus three
    table rows, and a placement present in one table and missing from another is a failing test
    rather than a silent fallback.

    Example::

        Placement.EXTERNAL_WALL_EXTERNAL.value   # 'external_wall_external', what a layer records

    The member names are HiSim's ``UPPER_SNAKE`` spelling and the values are the catalogue's own,
    exactly as :mod:`hisim.renovisor.vocabulary` does it for the request's vocabularies. This enum
    is *not* one of those: a request never carries a placement, the translator derives it from the
    measure, which is why it belongs here beside the tables that read it.
    """

    EXTERNAL_WALL_EXTERNAL = "external_wall_external"
    EXTERNAL_WALL_INTERNAL = "external_wall_internal"
    EXTERNAL_WALL_CAVITY = "external_wall_cavity"
    BASEMENT_CEILING = "basement_ceiling"
    BASEMENT_FLOOR_AND_WALLS_INSIDE = "basement_floor_and_walls_inside"
    BASEMENT_FLOOR_AND_WALLS_OUTSIDE = "basement_floor_and_walls_outside"
    FLOOR_AND_CEILING = "floor_and_ceiling"
    ROOF_EXTERNAL_RAFTER = "roof_external_rafter"
    ROOF_BETWEEN_RAFTER = "roof_between_rafter"
    TOP_FLOOR_CEILING = "top_floor_ceiling"

    @classmethod
    def values(cls) -> Tuple[str, ...]:
        """Every placement string, in declaration order, for an error message or a coverage test."""
        return tuple(member.value for member in cls)


class LayerDefaults:
    """How thick an insulation layer is when the request states no thickness.

    ``thickness_in_mm`` is an ``experts`` option of all twelve insulation measures (of
    ``basement_internal_insulation`` and ``top_floor_ceiling_insulation`` too since contract
    ``882a8c1``, hisim-1h7f), so most requests leave it out and the translator supplies one. Each
    number is the typical installed thickness of that build-up as the frontend side's
    specification §4.2 tabulates it; every use is a ``defaulted`` line in the mapping report
    naming the value.
    """

    # Source: calculation-request.md §4.2, "thickness_in_mm default" column. Typical installed
    # thicknesses per build-up, not derived from any target U-value. TO BE REVIEWED.
    BY_MEASURE: ClassVar[Dict[str, int]] = {
        "external_insulation": 100,
        "internal_dry_lining_insulation": 60,
        "cavity_wall_insulation": 100,
        "basement_ceiling_insulation": 100,
        "basement_internal_insulation": 80,
        "basement_external_insulation": 100,
        "solid_ground_floor_insulation": 100,
        "suspended_ground_floor_insulation": 150,
        "warm_roof_insulation": 120,
        "rafter_insulation": 150,
        "rolled_out_attic_insulation": 300,
        "top_floor_ceiling_insulation": 200,
    }

    # Source: calculation-request.md §4.2, "100, capped at 150" for cavity_wall_insulation. A
    # cavity cannot be filled deeper than it is wide, and 150 mm is the widest cavity the Irish
    # country pack describes. TO BE REVIEWED.
    CAVITY_MAXIMUM_IN_MM: ClassVar[int] = 150

    # Source: translator-implementation-spec.md §4, "option defaults: air_barrier true". The
    # option has no HiSim target either way, so the default only decides what the report says.
    AIR_BARRIER: ClassVar[bool] = True

    @classmethod
    def thickness_of(cls, measure_id: str) -> int:
        """Return the default layer thickness of one insulation measure, in millimetres.

        Args:
            measure_id: A catalogue id that adds an insulation layer.

        Returns:
            The thickness in millimetres.

        Raises:
            KeyError: When the measure adds no layer, which is a bug in the registry rather than
                in a request.
        """
        return cls.BY_MEASURE[measure_id]


class OpeningUValues:
    """The U-value a replaced window or door gets when the request states no expert value.

    Both tables are whole-unit U-values, glass and frame together, because that is what
    ``Building`` reads. A request that carries the expert
    ``u_value_in_watt_per_m2_per_kelvin`` option overrides them, and that is the only way to get
    a ``used`` rather than an ``approximated`` line out of an opening replacement.
    """

    # Source: calculation-request.md §4.2, window_replacement. Typical certified whole-window
    # U-values by pane count and low-emissivity coating; not measured, not from materials.yaml
    # (the window rows do not exist yet, §12 blocker (b)). TO BE REVIEWED.
    WINDOW: ClassVar[Dict[Tuple[int, bool], float]] = {
        (2, False): 1.4,
        (2, True): 1.1,
        (3, False): 0.8,
        (3, True): 0.7,
    }

    # Source: calculation-request.md §4.2, door_replacement. Zero panes is a solid door, two and
    # three are glazed doors whose glazing dominates the unit value. TO BE REVIEWED.
    DOOR: ClassVar[Dict[int, float]] = {0: 1.4, 2: 1.8, 3: 1.2}

    @classmethod
    def window(cls, glazing_panes: int, low_emissivity_coating: bool) -> float:
        """Return the whole-window U-value of a replacement, in W/(m²·K).

        Args:
            glazing_panes: Two or three.
            low_emissivity_coating: Whether the panes carry a low-emissivity coating.

        Returns:
            The U-value.

        Raises:
            KeyError: When the combination is not in the table, which the catalogue's value list
                rules out before this is reached.
        """
        return cls.WINDOW[(glazing_panes, low_emissivity_coating)]

    @classmethod
    def door(cls, glazing_panes: int) -> float:
        """Return the whole-door U-value of a replacement, in W/(m²·K).

        Args:
            glazing_panes: Zero, two or three.

        Returns:
            The U-value.

        Raises:
            KeyError: When the pane count is not in the table.
        """
        return cls.DOOR[glazing_panes]


class StorageDefaults:
    """What insulating a hot-water tank and its pipes does to the storage's loss coefficient."""

    # Source: half the class default of SimpleDHWStorage
    # (`heat_transfer_coefficient_in_watt_per_m2_per_kelvin` = 0.36,
    # hisim/components/simple_water_storage.py). Halving stands in for a measure that also
    # insulates the pipes, which have no HiSim parameter at all. TO BE REVIEWED.
    TANK_INSULATED_HEAT_TRANSFER: ClassVar[float] = 0.18


class RoofDefaults:
    """The orientation a roof-mounted array gets when the request does not state one."""

    # Source: calculation-request.md §3.4, "PV and solar-thermal tilt: pitched → 30, flat → 10".
    # A typical Irish pitched roof and the shallow frame angle of a flat-roof array.
    # TO BE REVIEWED.
    TILT_BY_ROOF_SHAPE: ClassVar[Dict[str, float]] = {"pitched": 30.0, "flat": 10.0}

    # Source: calculation-request.md §5.6, "house.building.roof.shape default pitched".
    SHAPE: ClassVar[str] = "pitched"

    # Source: calculation-request.md §3.13, "azimuth absent: 180, defaulted" — due south, the
    # HiSim convention and the best orientation in the northern hemisphere.
    AZIMUTH: ClassVar[float] = 180.0


class DesignTemperatures:
    """The outside design temperature a heating system is sized for, per country.

    HiSim's weather owns this fact (``WeatherConfig.heating_reference_temperature_in_celsius``,
    a required constructor argument since HiSim #771) and hands it to the building and the
    generators as the sizing fact of the same name; the recorded twins carry Aachen's -7 °C. TABULA
    cannot supply it: its ``Theta_e_Base`` column is the heating-degree-day base temperature (12 °C
    for every country) and ``Theta_e`` the annual mean, neither of which is a design condition.
    So the number is a reviewed constant per country, and a country without one is refused rather
    than sized for Aachen.

    Every value here is TO BE REVIEWED by whoever owns the physics.
    """

    #: Country code -> outside design temperature in °C.
    #:
    #: * ``IE``: -3.0 -- the external design temperature used for Irish heat-loss sizing
    #:   (I.S. EN 12831 national annex / SEAI heat pump sizing guidance). TO BE REVIEWED
    #: * ``NL``: -10.0 -- the Dutch design temperature (NEN 5060 / ISSO 51). TO BE REVIEWED
    #:
    #: ES has no entry: it has no TABULA typology and is refused before the weather is built.
    BY_COUNTRY: ClassVar[Dict[str, float]] = {"IE": -3.0, "NL": -10.0}


class BuildingDefaults:
    """What the translator writes into ``Building`` that no request field states."""

    # Source: calculation-request.md §3.3, "number_of_apartments is set by the translator: 1 for
    # every building_type (the AB archetype models one dwelling)".
    NUMBER_OF_APARTMENTS: ClassVar[int] = 1


class BatteryLaw:
    """How a battery's two pinned numbers follow from the one the request states."""

    # Source: hisim/components/advanced_battery_bslib.py — the class's own C-rate of 0.5 means
    # an inverter of 500 W per kilowatt hour of capacity. Pinning both keeps them consistent
    # when the request pins the capacity; it is a HiSim law, not an estimate. It is also the rule
    # measures.yaml states for the battery_system measure at contract 882a8c1: "power from the
    # capacity at the usual 0.5 C, which is the same rule HiSim applies when power is left unset".
    INVERTER_WATT_PER_KILOWATT_HOUR: ClassVar[float] = 500.0

    # Source: none beyond the catalogue's own conversion. Since contract 882a8c1 the
    # battery_system measure carries capacity_in_kwh and power_in_watt, both ``experts`` options,
    # and the frontend turns its "how many days should the battery carry the house?" question into
    # them before it sends anything. A request that carries neither is sized the way the frontend
    # would size it, at the smallest days_to_cover the request schema's house.battery admits: one
    # day of the household electricity the run simulates. TO BE REVIEWED.
    DAYS_TO_COVER_WHEN_UNSIZED: ClassVar[int] = 1


class BoilerEfficiency:
    """How a stated seasonal efficiency becomes a boiler's two efficiency bounds."""

    # Source: calculation-request.md §3.6 — "eff_th_max = value/100 and eff_th_min = eff_th_max
    # − 0.3, floor 0.3". The spread stands in for the part-load curve HiSim's GenericBoiler
    # interpolates between the two bounds (class defaults 0.90 / 0.60). TO BE REVIEWED.
    SPREAD: ClassVar[float] = 0.3

    # Source: the same sentence; a minimum efficiency below 0.3 describes no real boiler.
    MINIMUM: ClassVar[float] = 0.3

    # Source: a percentage is a hundredth.
    PERCENT: ClassVar[float] = 100.0


class OccupancyMode(str, Enum):
    """Where the household's electricity profile comes from.

    ``PREDEFINED_CHR01`` is the MVP (decision D-C): every household is simulated as the
    precomputed ``CHR01 Couple both at Work`` profile, written explicitly so that HiSim's own
    fallback chain is never entered. ``LOCAL_LPG`` is the mode the LoadProfileGenerator binary in
    the image would enable, which is why the household matcher of
    :mod:`hisim.renovisor.occupancy` is kept although nothing calls it today.
    """

    PREDEFINED_CHR01 = "PREDEFINED_CHR01"
    LOCAL_LPG = "LOCAL_LPG"

    @property
    def acquisition_mode(self) -> str:
        """Return the name of the HiSim ``LpgDataAcquisitionMode`` member this mode writes.

        The energy-system file carries enum member names, so the translator writes the name
        rather than the member: ``USE_PREDEFINED_PROFILE`` reads a shipped profile from disk and
        ``USE_LOCAL_LPG`` computes one with the binary in the image.
        """
        return "USE_PREDEFINED_PROFILE" if self is OccupancyMode.PREDEFINED_CHR01 else "USE_LOCAL_LPG"


class PredefinedHousehold:
    """The one LoadProfileGenerator household the MVP image ships a profile for.

    The reference is written into the energy-system file with the LoadProfileGenerator's own
    capitalised key names, because the constructor-argument codec deserialises ``name``/``guid``
    silently into an empty reference and only ``Name``/``Guid.StrVal`` round-trips.
    """

    # Source: utspclient.helpers.lpgdata.Households.CHR01_Couple_both_at_Work — the only
    # household HiSim ships a precomputed profile for (hisim/utils.py).
    NAME: ClassVar[str] = "CHR01 Couple both at Work"
    GUID: ClassVar[str] = "516a33ab-79e1-4221-853b-967fc11cc85a"

    @classmethod
    def reference(cls) -> Dict[str, object]:
        """Return the household as the energy-system file's constructor-argument codec reads it."""
        return {"Name": cls.NAME, "Guid": {"StrVal": cls.GUID}}


class AnywayShareByPlacement:
    """How much of an envelope measure's price the building would have spent anyway (§4.1).

    The lifecycle cost engine credits a renovation with the cost it *avoids*: money the building
    would have had to spend regardless, on a like-for-like replacement, is not a cost of the
    renovation. ``ExistingAsset.anyway_share`` is the fraction of the **new** measure's price that
    counterfactual would truly have bought, and it is the number that decides how flattering a
    retrofit's economics look. A first-time improvement must be well below 1: a facade that was
    never insulated would have been *repaired*, not insulated, so only the repair share —
    scaffolding, render, paint — was going to be paid. A genuine like-for-like replacement is 1.0:
    dead windows are replaced by windows.

    The table is keyed by the ``materials.yaml`` ``building_components`` placement, because that
    is what distinguishes the three cases that matter: an external layer that comes with
    scaffolding and a new render, an internal or cavity layer that comes with almost no shared
    work, and a replacement of a whole unit.

    Every share here is an estimate and none of them is measured.
    """

    # Source: cost_module_issues.md #12, which records that the anyway shares are rough and asks
    # for a reviewed table. These three are the shares the RenoVisor translator writes; they are
    # estimates of the like-for-like share of a first-time envelope improvement, not measurements
    # and not taken from any price list. TO BE REVIEWED.
    EXTERNAL_FIRST_TIME: ClassVar[float] = 0.3
    INTERNAL_FIRST_TIME: ClassVar[float] = 0.15
    LIKE_FOR_LIKE: ClassVar[float] = 1.0

    #: Placement -> the share of the new measure's price the counterfactual would have spent.
    #: An external layer carries the render-and-scaffolding share; an internal, cavity or
    #: basement layer carries almost nothing, because nothing about the existing build-up had to
    #: be touched. TO BE REVIEWED, with the three shares above.
    BY_PLACEMENT: ClassVar[Dict[str, float]] = {
        Placement.EXTERNAL_WALL_EXTERNAL.value: EXTERNAL_FIRST_TIME,
        Placement.EXTERNAL_WALL_INTERNAL.value: INTERNAL_FIRST_TIME,
        Placement.EXTERNAL_WALL_CAVITY.value: INTERNAL_FIRST_TIME,
        Placement.BASEMENT_CEILING.value: INTERNAL_FIRST_TIME,
        Placement.BASEMENT_FLOOR_AND_WALLS_INSIDE.value: INTERNAL_FIRST_TIME,
        Placement.BASEMENT_FLOOR_AND_WALLS_OUTSIDE.value: EXTERNAL_FIRST_TIME,
        Placement.FLOOR_AND_CEILING.value: INTERNAL_FIRST_TIME,
        Placement.ROOF_EXTERNAL_RAFTER.value: EXTERNAL_FIRST_TIME,
        Placement.ROOF_BETWEEN_RAFTER.value: INTERNAL_FIRST_TIME,
        Placement.TOP_FLOOR_CEILING.value: INTERNAL_FIRST_TIME,
    }

    @classmethod
    def of(cls, placement: str) -> float:
        """Return the anyway share of one build-up position.

        Args:
            placement: The ``building_components`` value the insulation measure records.

        Returns:
            The share in ``(0, 1]``. A placement the table does not know falls back to
            :attr:`INTERNAL_FIRST_TIME`, the smaller of the two first-time shares, so an unlisted
            build-up is credited conservatively rather than generously.
        """
        return cls.BY_PLACEMENT.get(placement, cls.INTERNAL_FIRST_TIME)


@dataclass(frozen=True)
class GradeScale(Generic[Grade]):
    """One grade scale: the limits that cut annual degree-hours into grades, best grade first.

    A value on a limit takes the better grade, and a value above the last limit takes
    :attr:`worst`. The scale checks itself on construction, so a mistyped limit is an error at
    import rather than a grade that quietly never occurs.

    Args:
        limits: The upper limit of each grade in degree-hours per year, strictly ascending and
            never negative.
        grades: The grade each limit closes, in the same order, the best first.
        worst: The grade of every value above the last limit.
    """

    limits: Tuple[float, ...]
    grades: Tuple[Grade, ...]
    worst: Grade

    def __post_init__(self) -> None:
        """Refuse a scale whose limits are not strictly ascending, are negative or lack a grade."""
        if not self.limits:
            raise ValueError("a grade scale needs at least one limit")
        if len(self.limits) != len(self.grades):
            raise ValueError(
                f"a grade scale needs one grade per limit; got limits {self.limits} and grades {self.grades}"
            )
        if self.limits[0] < 0.0:
            raise ValueError(f"the limits of a grade scale are never negative; got {self.limits}")
        if any(lower >= upper for lower, upper in zip(self.limits, self.limits[1:])):
            raise ValueError(f"the limits of a grade scale must be strictly ascending; got {self.limits}")

    def grade(self, degree_hours: float) -> Grade:
        """Return the grade of the first limit the degree-hours do not exceed.

        Args:
            degree_hours: The annual degree-hours, a finite number that is never negative.

        Returns:
            The grade.

        Raises:
            ValueError: When the degree-hours are negative or not finite, which no sum of clipped
                temperature differences produces.
        """
        if not math.isfinite(degree_hours) or degree_hours < 0.0:
            raise ValueError(f"degree-hours to grade must be finite and never negative; got {degree_hours!r}")
        for limit, grade in zip(self.limits, self.grades):
            if degree_hours <= limit:
                return grade
        return self.worst

    def describe(self) -> str:
        """Return the scale as one phrase, e.g. ``5 <= 50, 4 <= 100, 3 <= 250, 2 <= 500, else 1``."""
        bands = ", ".join(f"{grade} <= {limit:g}" for limit, grade in zip(self.limits, self.grades))
        return f"{bands}, else {self.worst}"


class ComfortGrades:
    """Where the comfort grades of ``result.json`` cut the simulated degree-hours (hisim-sska).

    Decision of 2026-09-23 (renovisorissues #29): the grades are computed from a full year of the
    simulated indoor air temperature, not copied from the contract's examples. Owner decision of
    2026-09-25: every grade is on the contract's shared ``Rating``, an integer from 1 to 5 with 5
    the best, so the comfort grades and summer heat protection read alike.

    * ``comfort.heating`` grades the degree-hours the room spent more than 1 K below the house's
      own heating setpoint. The tolerance keeps an on/off controller's ripple out: on the mockup
      it leaves a working gas boiler at 3 and a working heat pump at 57 K·h/a, where the ripple
      alone made 331 and 729 (decision of 2026-09-23, after the controller fix hisim-q1rm).
    * ``comfort.cooling`` and ``summer_heat_protection`` grade the same quantity, the degree-hours
      above 26 °C (DIN 4108-2, summer climate region B), on the same scale: :attr:`SUMMER` *is*
      :attr:`SUMMER_HEAT_PROTECTION`, so the two grades agree by construction. No RenoVisor house
      has active cooling, so this is summer overheating of the free-floating building. Its last
      limit is 1200 K·h/a, DIN 4108-2's requirement for homes: 1 means "would fail it".

    A value on a limit takes the better grade. Every limit here is HiSim's proposal, chosen with
    the owner and not taken from a rating procedure except where named. TO BE REVIEWED.
    """

    #: Degree-hours per year more than 1 K below the heating setpoint -> Rating 5..1, 5 the best.
    HEATING: ClassVar[GradeScale[int]] = GradeScale(limits=(50.0, 100.0, 250.0, 500.0), grades=(5, 4, 3, 2), worst=1)

    #: Degree-hours per year above 26 °C -> Rating 5..1, 5 the best.
    SUMMER_HEAT_PROTECTION: ClassVar[GradeScale[int]] = GradeScale(
        limits=(250.0, 500.0, 900.0, 1200.0), grades=(5, 4, 3, 2), worst=1
    )

    #: ``comfort.cooling``'s scale: the very same object as :attr:`SUMMER_HEAT_PROTECTION`.
    SUMMER: ClassVar[GradeScale[int]] = SUMMER_HEAT_PROTECTION
