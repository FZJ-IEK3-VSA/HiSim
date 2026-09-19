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

from enum import Enum
from typing import ClassVar, Dict, Tuple


class LayerDefaults:
    """How thick an insulation layer is when the request states no thickness.

    ``thickness_in_mm`` is an ``experts`` option of every insulation measure, so most requests
    leave it out and the translator supplies one. Each number is the typical installed thickness
    of that build-up as the frontend side's specification §4.2 tabulates it; every use is a
    ``defaulted`` line in the mapping report naming the value.
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


class FixedMaterials:
    """The materials of the three insulation measures the catalogue gives no material option.

    ``cavity_wall_insulation``, ``basement_internal_insulation`` and
    ``top_floor_ceiling_insulation`` carry ``options: []``, so no request can name a material for
    them and the translator has to pick one. Every use is an ``approximated`` line naming the
    material and its conductivity, because the homeowner did not choose it.
    """

    # Source: materials.yaml row `eps_beads_cavity`, thermal conductivity 0.0355 W/(m·K) as the
    # 2026-09-17 dump gives it (the frontend side's spec quotes 0.037; the dump is the number
    # used here, per the review's note). TO BE REVIEWED.
    CAVITY_BEADS: ClassVar[Tuple[str, float]] = ("eps_beads_cavity", 0.0355)

    # Source: materials.yaml row `stone_wool_flexible_insulation_blankets`, thermal conductivity
    # 0.035 W/(m·K). TO BE REVIEWED.
    STONE_WOOL: ClassVar[Tuple[str, float]] = ("stone_wool_flexible_insulation_blankets", 0.035)

    #: measure id -> (materials database id, thermal conductivity in W/(m·K)).
    BY_MEASURE: ClassVar[Dict[str, Tuple[str, float]]] = {
        "cavity_wall_insulation": CAVITY_BEADS,
        "basement_internal_insulation": STONE_WOOL,
        "top_floor_ceiling_insulation": STONE_WOOL,
    }

    @classmethod
    def of(cls, measure_id: str) -> Tuple[str, float]:
        """Return the fixed material of one option-less insulation measure.

        Args:
            measure_id: One of the three ids above.

        Returns:
            ``(asp_id, thermal conductivity in W/(m·K))``.

        Raises:
            KeyError: When the measure has a material option and should have read the request's.
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


class BuildingDefaults:
    """What the translator writes into ``Building`` that no request field states."""

    # Source: calculation-request.md §3.3, "number_of_apartments is set by the translator: 1 for
    # every building_type (the AB archetype models one dwelling)".
    NUMBER_OF_APARTMENTS: ClassVar[int] = 1


class BatteryLaw:
    """How a battery's two pinned numbers follow from the one the request states."""

    # Source: hisim/components/advanced_battery_bslib.py — the class's own C-rate of 0.5 means
    # an inverter of 500 W per kilowatt hour of capacity. Pinning both keeps them consistent
    # when the request pins the capacity; it is a HiSim law, not an estimate.
    INVERTER_WATT_PER_KILOWATT_HOUR: ClassVar[float] = 500.0


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
