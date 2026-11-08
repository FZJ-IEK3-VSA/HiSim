"""The seasonal COP of the hplib heat pump by EN 14825 and the calibration of its COP to a datasheet SCOP.

:class:`StandardizedSeasonalCop` rates a machine by the bin method of EN 14825 (average climate) for the two
applications of :class:`ScopApplication`, W35 and W55. :class:`ScopCalibration` finds the factors on the compressor's
COP that make that rating give exactly the SCOPs a datasheet states, so a heat pump configured with a stated SCOP
performs like it. For example, ``ScopCalibration.of(heatpump, scop_w35=4.6, scop_w55=3.4)`` returns the calibration the
component applies to every hplib result.
"""

import math
from enum import Enum, unique
from typing import Any, Callable, ClassVar, Dict, Optional, Tuple

import numpy as np

from hisim.subcomponents.base import Subcomponent
from hisim.components.more_advanced_heat_pump.hplib_operation import (
    HplibResult,
)


@unique
class ScopApplication(str, Enum):
    """The two EN 14825 applications a datasheet rates a heat pump's SCOP for."""

    W35 = "W35"
    W55 = "W55"


class StandardizedSeasonalCop(Subcomponent):
    """The seasonal COP of an hplib heat pump by the bin method of EN 14825, average climate.

    What a manufacturer states on the datasheet or ErP fiche is a SCOP rated this way, so
    computing the same figure for hplib's curve fit is what lets the fit be calibrated to it
    (:class:`ScopCalibration`). The method, in the simplified form the calibration needs:

    * the heating season is :attr:`BINS`, one outdoor temperature per bin with its hours;
    * the building's demand falls linearly from the design load at :attr:`DESIGN_TEMPERATURE` to
      zero at :attr:`HEATING_LIMIT_TEMPERATURE`;
    * the design load is the machine's thermal output at A-7/W52 divided by the part load of
      A-7, :attr:`DESIGN_SIZING_PART_LOAD` (the machine covers A-7 exactly, as EN 14825's
      reference sizing has it);
    * the outlet temperature follows the application's line, :attr:`FLOW_LINES`, through two
      points and extended beyond them;
    * the machine modulates at the COP hplib gives for the bin (hplib's own 25 % electrical floor
      is the only part-load limit it knows), and whatever the demand exceeds its output is met by
      an electric back-up heater at COP 1, as is a bin where hplib's COP is 1 or less.

    Source: EN 14825:2022, average climate (bins -10 to +15 °C, 4910 h) and the variable-outlet
    lines of its low- (W35) and medium-temperature (W55) applications. For a brine/water machine
    (hplib group 2) the brine enters at :attr:`BRINE_TEMPERATURE` in every bin, EN 14825's rating
    condition, while the ambient term still follows the bin. Any other hplib group is refused.

    Example::

        StandardizedSeasonalCop.of(heatpump, ScopApplication.W55)   # 3.40 for hplib's generic air/water fit
    """

    #: Outdoor bin temperature in °C and its hours in the EN 14825 average heating season.
    BINS: ClassVar[Tuple[Tuple[int, int], ...]] = (
        (-10, 1), (-9, 25), (-8, 23), (-7, 24), (-6, 27), (-5, 68), (-4, 91), (-3, 89), (-2, 165),
        (-1, 173), (0, 240), (1, 280), (2, 320), (3, 357), (4, 356), (5, 303), (6, 330), (7, 326),
        (8, 348), (9, 335), (10, 315), (11, 215), (12, 169), (13, 151), (14, 105), (15, 74),
    )

    #: Design outdoor temperature of the average climate, °C.
    DESIGN_TEMPERATURE: ClassVar[float] = -10.0

    #: Outdoor temperature at which the heating demand is zero, °C.
    HEATING_LIMIT_TEMPERATURE: ClassVar[float] = 16.0

    #: The rating point the design load is taken from: A-7/W52.
    SIZING_OUTDOOR_TEMPERATURE: ClassVar[float] = -7.0
    SIZING_FLOW_TEMPERATURE: ClassVar[float] = 52.0

    #: Part load at A-7, (-7 - 16) / (-10 - 16) ≈ 0.885: the design load is the A-7 output over it.
    DESIGN_SIZING_PART_LOAD: ClassVar[float] = (SIZING_OUTDOOR_TEMPERATURE - HEATING_LIMIT_TEMPERATURE) / (
        DESIGN_TEMPERATURE - HEATING_LIMIT_TEMPERATURE
    )

    #: Brine temperature of every bin for a brine/water machine, °C.
    BRINE_TEMPERATURE: ClassVar[float] = 0.0

    #: hplib's secondary-side temperature rise: the outlet is the inlet plus this, K.
    SECONDARY_TEMPERATURE_RISE: ClassVar[float] = 5.0

    #: The hplib groups the rating covers, by the kind of machine each is.
    RATED_GROUPS: ClassVar[Dict[int, str]] = {1: "air/water", 2: "brine/water"}

    #: Outlet-temperature line per application, as two (outdoor °C, outlet °C) points.
    FLOW_LINES: ClassVar[Dict[ScopApplication, Tuple[Tuple[float, float], Tuple[float, float]]]] = {
        ScopApplication.W35: ((-7.0, 34.0), (12.0, 24.0)),
        ScopApplication.W55: ((-7.0, 52.0), (12.0, 30.0)),
    }

    @classmethod
    def flow_temperature(cls, application: ScopApplication, outdoor_temperature: float) -> float:
        """Return the application's outlet temperature at one outdoor temperature, °C."""
        (t_1, f_1), (t_2, f_2) = cls.FLOW_LINES[application]
        return f_1 + (f_2 - f_1) * (outdoor_temperature - t_1) / (t_2 - t_1)

    @classmethod
    def part_load(cls, outdoor_temperature: float) -> float:
        """Return the building's demand at one outdoor temperature as a share of the design load."""
        return (outdoor_temperature - cls.HEATING_LIMIT_TEMPERATURE) / (
            cls.DESIGN_TEMPERATURE - cls.HEATING_LIMIT_TEMPERATURE
        )

    @classmethod
    def mean_outlet_temperature(cls, application: ScopApplication) -> float:
        """Return the application's outlet temperature averaged over the season, weighted by heat, °C.

        28.9 °C for W35 and 40.7 °C for W55: where the rating actually spends its heat, which is
        why :class:`ScopCalibration` anchors each rating's factor there.
        """
        weights = [(hours * cls.part_load(outdoor), outdoor) for outdoor, hours in cls.BINS]
        return sum(weight * cls.flow_temperature(application, outdoor) for weight, outdoor in weights) / sum(
            weight for weight, _outdoor in weights
        )

    @classmethod
    def of(
        cls, heatpump: Any, application: ScopApplication, calibration: Optional["ScopCalibration"] = None
    ) -> float:
        """Return the seasonal COP of one hplib heat pump for one application.

        Args:
            heatpump: An ``hplib.HeatPump`` of group 1 (air/water) or 2 (brine/water); it is only
                read, through pure ``simulate`` calls.
            application: The rating's application, which picks the outlet-temperature line.
            calibration: Applied to every call when given, so the calibrated machine is rated.

        Returns:
            Heat delivered over electricity drawn across the season.

        Raises:
            ValueError: When the machine is of any other hplib group, for which the bins' source
                temperature is not defined here.
        """
        if heatpump.group_id not in cls.RATED_GROUPS:
            raise ValueError(
                "the EN 14825 rating here covers air/water (hplib group 1) and brine/water (group 2) "
                f"machines only, and this one is hplib group {heatpump.group_id:g}."
            )

        def run(outdoor: float, flow: float) -> Tuple[float, float]:
            source = outdoor if heatpump.group_id == 1 else cls.BRINE_TEMPERATURE
            result = heatpump.simulate(
                t_in_primary=source,
                t_in_secondary=flow - cls.SECONDARY_TEMPERATURE_RISE,
                t_amb=outdoor,
                mode=1,
                p_th_min=0,
            )
            if calibration is not None:
                result = calibration.apply(heatpump, result, 1)
            return float(result["P_th"]), float(result["COP"])

        design_load, _cop = run(cls.SIZING_OUTDOOR_TEMPERATURE, cls.SIZING_FLOW_TEMPERATURE)
        design_load /= cls.DESIGN_SIZING_PART_LOAD
        heat = 0.0
        electricity = 0.0
        for outdoor, hours in cls.BINS:
            demand = design_load * cls.part_load(outdoor)
            output, cop = run(outdoor, cls.flow_temperature(application, outdoor))
            by_heat_pump = min(demand, output) if cop > HplibResult.HEATING_ROD_ALONE_MAXIMAL_COP else 0.0
            heat += hours * demand
            electricity += hours * ((by_heat_pump / cop if by_heat_pump else 0.0) + demand - by_heat_pump)
        return heat / electricity


class ScopCalibration(Subcomponent):
    """Scales an hplib heat pump's COP to the SCOP its datasheet states (hisim-component-physics-4g9.15).

    hplib evaluates a linear curve fit; for ``model="Generic"`` it is the fit over its whole
    database group, so the simulated machine is the average unit of its group. A stated
    standardised SCOP says how much better or worse the real unit is. Every heating call multiplies
    hplib's COP by a factor for its own outlet temperature and divides the compressor's
    electricity by it; the thermal output is unchanged, so the building gets the same heat for
    less or more electricity. The factors are solved (:meth:`of`) so that the calibrated machine,
    rated by :class:`StandardizedSeasonalCop`, gives exactly the stated SCOPs.

    * Both ratings stated: the factor is linear in the outlet temperature between the two
      :attr:`ANCHORS` and held at the nearer anchor's value outside. The anchors are the
      heat-weighted mean outlets of the two rating lines (28.9 and 40.7 °C), where each rating
      spends its heat; anchoring at the nominal 35 / 55 °C instead is ill-conditioned and drives
      the 55 °C factor to 0.2–0.5 for wide pairs (decision with Noah, 2026-09-23; the check against
      measured units is hisim-x2mu).
    * One stated: its factor everywhere.
    * The heating rod is never calibrated: a call where hplib runs the rod alone (COP 1) is left
      as it is, and in hplib's compressor-plus-rod branch only the compressor's share is scaled.
    * Cooling (mode 2) is out of scope and left as hplib returns it.

    What the solve rests on: each rating is continuous and non-decreasing in each factor (a bin
    whose calibrated COP falls to 1 switches to the back-up heater at COP 1, which draws the same
    electricity, so there is no jump); it is 1 when the factors are so small that every bin runs
    its back-up heater, and however large they grow it stays bounded by the back-up heater's share
    of the season, which no factor scales. So a bracketed solve finds a rating's factor whenever
    one exists, and a stated SCOP outside what the fit can reach is refused by name.

    Example::

        calibration = ScopCalibration.of(heatpump, scop_w35=4.6, scop_w55=3.4)
        calibration.apply(heatpump, heatpump.simulate(...), mode=1)
    """

    #: How close the calibrated rating must come to the stated SCOP.
    TOLERANCE: ClassVar[float] = 1e-5

    #: The factors the solve searches between. At 0.001 no hplib COP comes near 1, so every bin
    #: runs its back-up heater and the rating is 1; at 1000 the compressor draws a thousandth of
    #: hplib's electricity, and the back-up heater's share is all that still bounds a rating.
    MINIMUM_FACTOR: ClassVar[float] = 1e-3
    MAXIMUM_FACTOR: ClassVar[float] = 1e3

    #: The rounds one bracketed solve may take; the Illinois method needs twenty to thirty.
    MAXIMUM_ITERATIONS: ClassVar[int] = 100

    #: The outlet temperatures the W35 and the W55 factor hold at, °C (28.9 and 40.7): the
    #: heat-weighted mean outlets of the two rating lines, computed once.
    ANCHORS: ClassVar[Tuple[float, float]] = (
        StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W35),
        StandardizedSeasonalCop.mean_outlet_temperature(ScopApplication.W55),
    )

    def __init__(self, factors: Dict[ScopApplication, float]) -> None:
        """Hold the factor per stated application; an empty map calibrates nothing."""
        self.factors = dict(factors)

    @classmethod
    def of(
        cls, heatpump: Any, scop_w35: Optional[float], scop_w55: Optional[float], machine: str = "hplib's fit"
    ) -> "ScopCalibration":
        """Return the factors that bring the fit, rated by the bin method, to the stated SCOPs.

        One rating: its factor is the root of ``rated(factor) = stated`` (:meth:`solve`). Two
        ratings: for a given W55 factor the W35 factor that rates W35 exactly is solved, and the
        W55 rating of that pair rises with the W55 factor (the W55 factor moves the W55 line's hot
        bins more than the W35 factor it displaces moves its mild ones; checked on the Generic
        air/water and brine/water fits for W35 ratings from 1.1 to 10 over the whole factor range),
        so the W55 factor is solved on it in turn. A single ratio ``stated / rated`` is not exact:
        the back-up heater's share of the season is not scaled, and with two ratings each factor
        reaches into the other rating's line. A typical pair takes a few hundred ratings, some tens
        of milliseconds.

        Args:
            heatpump: An ``hplib.HeatPump`` of group 1 or 2, only read.
            scop_w35: The stated W35 rating, or ``None``.
            scop_w55: The stated W55 rating, or ``None``.
            machine: How a refusal names the fit, e.g. "hplib's Generic air/water fit".

        Raises:
            ValueError: When no factors between :attr:`MINIMUM_FACTOR` and :attr:`MAXIMUM_FACTOR`
                reach the stated SCOPs; the message names the rating that cannot be met and the
                limit the fit reaches.
        """
        stated = {
            application: float(value)
            for application, value in ((ScopApplication.W35, scop_w35), (ScopApplication.W55, scop_w55))
            if value is not None
        }
        if not stated:
            return cls({})
        ratings = " and ".join(f"{application.value} {value}" for application, value in stated.items())
        refused = f"the stated SCOP{'s' if len(stated) > 1 else ''} {ratings} cannot be met: "
        lowest, highest = cls.MINIMUM_FACTOR, cls.MAXIMUM_FACTOR
        if len(stated) == 1:
            ((application, target),) = stated.items()

            def single(factor: float) -> float:
                return StandardizedSeasonalCop.of(heatpump, application, cls({application: factor}))

            at_lowest, at_highest = single(lowest), single(highest)
            if target <= at_lowest:
                raise ValueError(refused + cls.unreachable(machine, application, at_lowest, False, False, None))
            if target >= at_highest:
                raise ValueError(refused + cls.unreachable(machine, application, at_highest, True, True, None))
            return cls({application: cls.solve(single, target, cls.TOLERANCE / 10)})

        w35, w55 = stated[ScopApplication.W35], stated[ScopApplication.W55]

        def rated(application: ScopApplication, factor_w35: float, factor_w55: float) -> float:
            calibration = cls({ScopApplication.W35: factor_w35, ScopApplication.W55: factor_w55})
            return StandardizedSeasonalCop.of(heatpump, application, calibration)

        def w35_factor(factor_w55: float) -> float:
            # Solved finer than the outer solve's tolerance, so its rest does not stall that solve.
            return cls.solve(lambda factor: rated(ScopApplication.W35, factor, factor_w55), w35, cls.TOLERANCE / 1000)

        def w55_rating(factor_w55: float) -> float:
            return rated(ScopApplication.W55, w35_factor(factor_w55), factor_w55)

        highest_w35 = rated(ScopApplication.W35, highest, highest)
        if w35 >= highest_w35:
            raise ValueError(refused + cls.unreachable(machine, ScopApplication.W35, highest_w35, True, True, None))
        # The W55 factors at which some W35 factor in range still rates W35 exactly: from where the
        # highest W35 factor just reaches it to where the lowest one just does.
        first = cls.solve(lambda factor: rated(ScopApplication.W35, highest, factor), w35, cls.TOLERANCE / 1000)
        last = cls.solve(lambda factor: rated(ScopApplication.W35, lowest, factor), w35, cls.TOLERANCE / 1000)
        condition = f"with W35 at {w35}, "
        lowest_w55 = w55_rating(first)
        if w55 <= lowest_w55:
            # At the lowest W55 factor the W55 factor itself would have to go lower; above it, the W35
            # factor that holds W35 would have to go above the range.
            anchor, beyond = (ScopApplication.W55, False) if first <= lowest else (ScopApplication.W35, True)
            raise ValueError(
                refused
                + condition
                + cls.unreachable(machine, ScopApplication.W55, lowest_w55, False, beyond, anchor)
            )
        highest_w55 = w55_rating(last)
        if w55 >= highest_w55:
            # Mirrored: the W55 factor would have to go higher, or the W35 factor below the range.
            anchor, beyond = (ScopApplication.W55, True) if last >= highest else (ScopApplication.W35, False)
            raise ValueError(
                refused
                + condition
                + cls.unreachable(machine, ScopApplication.W55, highest_w55, True, beyond, anchor)
            )
        factor_w55 = cls.solve(w55_rating, w55, cls.TOLERANCE / 10)
        return cls({ScopApplication.W35: w35_factor(factor_w55), ScopApplication.W55: factor_w55})

    @classmethod
    def unreachable(
        cls,
        machine: str,
        application: ScopApplication,
        limit: float,
        above: bool,
        factor_above: bool,
        anchor: Optional[ScopApplication],
    ) -> str:
        """Return the sentence that refuses one rating beyond what the fit reaches.

        Args:
            machine: How the sentence names the fit.
            application: The rating that cannot be met.
            limit: The furthest the fit rates it.
            above: Whether the stated rating is above that limit (else below it).
            factor_above: Whether reaching it would need a factor above :attr:`MAXIMUM_FACTOR`
                (else below :attr:`MINIMUM_FACTOR`).
            anchor: The anchor whose factor would have to leave the range, or ``None`` for a
                single rating, whose factor holds everywhere.
        """
        bound = f"above {cls.MAXIMUM_FACTOR:g}" if factor_above else f"below {cls.MINIMUM_FACTOR:g}"
        where = ""
        if anchor is not None:
            temperature = cls.ANCHORS[0 if anchor is ScopApplication.W35 else 1]
            where = f" at the {anchor.value} anchor ({temperature:.1f} °C)"
        sentence = (
            f"{machine} cannot rate {'above' if above else 'below'} {application.value} {limit:.2f}; a "
            f"{'higher' if above else 'lower'} {application.value} rating would need a factor {bound}{where}"
        )
        if above and factor_above and anchor in (None, application):
            sentence += ", and the back-up heater's share of the season, which no factor scales, caps it there"
        return sentence + "."

    @classmethod
    def solve(cls, rating: Callable[[float], float], target: float, tolerance: float) -> float:
        """Return the factor at which a non-decreasing rating meets a target, to a tolerance.

        The Illinois method (regula falsi that halves the stale end's miss when the same end moves
        twice) on the factor's logarithm between :attr:`MINIMUM_FACTOR` and :attr:`MAXIMUM_FACTOR`;
        a step that would leave the bracket bisects instead. A target the rating does not cross
        in that range returns the nearer bound: the callers check the bounds first where a miss
        must be refused, and the nested solve relies on the clamp.

        Raises:
            RuntimeError: When :attr:`MAXIMUM_ITERATIONS` rounds pass without meeting the
                tolerance, which a continuous rating does not allow.
        """
        low, high = math.log(cls.MINIMUM_FACTOR), math.log(cls.MAXIMUM_FACTOR)
        miss_low = rating(cls.MINIMUM_FACTOR) - target
        if miss_low >= 0:
            return cls.MINIMUM_FACTOR
        miss_high = rating(cls.MAXIMUM_FACTOR) - target
        if miss_high <= 0:
            return cls.MAXIMUM_FACTOR
        moved = 0
        for _round in range(cls.MAXIMUM_ITERATIONS):
            point = (low * miss_high - high * miss_low) / (miss_high - miss_low)
            if not low < point < high:
                point = (low + high) / 2
            miss = rating(math.exp(point)) - target
            if abs(miss) < tolerance:
                return math.exp(point)
            if miss < 0:
                low, miss_low = point, miss
                if moved < 0:
                    miss_high /= 2
                moved = -1
            else:
                high, miss_high = point, miss
                if moved > 0:
                    miss_low /= 2
                moved = 1
        raise RuntimeError(
            f"the SCOP calibration's solve for a rating of {target} did not converge in "
            f"{cls.MAXIMUM_ITERATIONS} rounds (bracket {math.exp(low):.6g} to {math.exp(high):.6g})."
        )

    def factor(self, outlet_temperature: float) -> float:
        """Return the factor for one outlet temperature, °C: linear between the anchors, held outside."""
        if not self.factors:
            return 1.0
        if len(self.factors) == 1:
            return next(iter(self.factors.values()))
        return float(
            np.interp(
                outlet_temperature,
                self.ANCHORS,
                (self.factors[ScopApplication.W35], self.factors[ScopApplication.W55]),
            )
        )

    def apply(self, heatpump: Any, results: Dict[str, Any], mode: int) -> Dict[str, Any]:
        """Return hplib's results for one call with the calibration applied (a new dictionary)."""
        if not self.factors or mode != 1:
            return results
        cop = float(results["COP"])
        if cop <= HplibResult.HEATING_ROD_ALONE_MAXIMAL_COP:
            return results
        factor = self.factor(float(results["T_out"]))
        p_th = float(results["P_th"])
        p_el = float(results["P_el"])
        rod = float(heatpump.p_th_ref)
        compressor = float(heatpump.p_el_ref)
        if abs(p_el - (compressor + rod)) < 1e-6 * max(p_el, 1.0):
            # hplib's compressor-plus-rod branch: only the compressor's share is calibrated.
            calibrated_el = compressor / factor + rod
        else:
            calibrated_el = p_el / factor
        calibrated = dict(results)
        calibrated["P_el"] = calibrated_el
        calibrated["COP"] = p_th / calibrated_el
        return calibrated
