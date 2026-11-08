"""hplib's answer as a typed value and the frozen values one step of the hplib heat pump passes between its functions.

:class:`HplibResult` is the adapter between hplib's result dictionary and the heat pump: the heat pump reads typed
fields instead of dictionary keys, and the interpolation between two cached grid points is one pure function.
:class:`CalculationRequest` is the key of that cache. :class:`HeatPumpOperation` and its companions
(:class:`HeatPumpStepConditions`, :class:`HeatPumpRunTimers`, :class:`HeatPumpModeTrack`,
:class:`HeatPumpSwitchCounters`, :class:`HeatPumpEnergyTotals`, :class:`HeatingCircuitPowers`) carry one step's
inputs, timers, counters and running totals, so the component's step is a chain of pure functions over them. Nothing
here needs the component (:mod:`hisim.components.more_advanced_heat_pump.heat_pump`).
"""

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Optional


@dataclass(frozen=True)
class HeatingCircuitPowers:
    """The thermal and electrical power a running heating circuit of the hplib heat pump books for one step.

    Both are derived from the same water flow: the thermal power is the heat the circuit's water carries, the
    electrical power that heat over the step's coefficient of performance. A value object, so the two powers
    cannot be swapped by position.
    """

    #: The heat the circuit's water carries, in W.
    thermal_power_in_watt: float
    #: The electricity the compressor draws for that heat, in W.
    electrical_power_in_watt: float


@dataclass(frozen=True)
class HplibResult:

    """hplib's answer at one operating point, the numbers the heat pump computes with.

    An adapter between hplib's result dictionary and the heat pump: the heat pump reads typed fields instead of
    dictionary keys, and the interpolation between two cached grid points is one pure function
    (:meth:`interpolated`).
    """

    #: The water temperature leaving the heat pump's secondary side, in °C (hplib's ``T_out``).
    outlet_temperature_in_celsius: float
    #: The secondary side's mass flow, in kg/s (hplib's ``m_dot``).
    mass_flow_in_kg_per_second: float
    #: hplib's own thermal power, in W (``P_th``).
    thermal_power_in_watt: float
    #: hplib's own electrical power, in W (``P_el``).
    electrical_power_in_watt: float
    #: The coefficient of performance, dimensionless (``COP``).
    cop: float
    #: The energy efficiency ratio of cooling, dimensionless (``EER``).
    eer: float
    #: Whether hplib itself runs its heating rod alone at this operating point: its own, uncalibrated coefficient of
    #: performance of heating is at most :attr:`HEATING_ROD_ALONE_MAXIMAL_COP`. A stated SCOP's calibration scales the
    #: compressor's COP (:class:`ScopCalibration`), so the calibrated :attr:`cop` can fall below 1 while hplib still
    #: runs the compressor; this flag keeps hplib's verdict. Always False for cooling.
    heating_rod_alone: bool

    @staticmethod
    def from_hplib(results: Mapping[str, Any], *, heating_rod_alone: bool) -> "HplibResult":
        """Return the fields of an hplib result dictionary, as floats, with hplib's verdict on its heating rod.

        For example, a calibrated compressor result next to the switch has a COP of 0.88 and
        ``heating_rod_alone=False``, because hplib's own COP there is 1.0033. The flag is not read from ``results``,
        because those may be calibrated (:meth:`runs_the_heating_rod_alone` reads hplib's own result).

        Args:
            results: hplib's result, calibrated or not, with the keys ``T_out``, ``m_dot``, ``P_th``, ``P_el``,
                ``COP`` and ``EER``.
            heating_rod_alone: Whether hplib itself runs its heating rod alone at this operating point.

        Returns:
            The typed result.
        """
        return HplibResult(
            outlet_temperature_in_celsius=float(results["T_out"]),
            mass_flow_in_kg_per_second=float(results["m_dot"]),
            thermal_power_in_watt=float(results["P_th"]),
            electrical_power_in_watt=float(results["P_el"]),
            cop=float(results["COP"]),
            eer=float(results["EER"]),
            heating_rod_alone=heating_rod_alone,
        )

    @staticmethod
    def runs_the_heating_rod_alone(hplib_results: Mapping[str, Any], mode: int) -> bool:
        """Return whether hplib runs its heating rod alone, read from its own, uncalibrated result.

        hplib heats with its rod alone where its fitted coefficient of performance is at most 1; it then reports a COP
        of exactly 1. For example, hplib's result at a COP of 1.0 in heating mode gives True, one at 1.0033 False.
        Cooling (mode 2) has no heating rod and gives False.

        Args:
            hplib_results: hplib's own result, before any calibration, with the key ``COP``.
            mode: hplib's mode, 1 heating, 2 cooling.

        Returns:
            Whether hplib's heating rod alone delivers the heat.
        """
        return mode == 1 and float(hplib_results["COP"]) <= HplibResult.HEATING_ROD_ALONE_MAXIMAL_COP

    #: The coefficient of performance at and below which hplib runs its heating rod alone, dimensionless: hplib then
    #: books its reference thermal power as both heat and electricity.
    HEATING_ROD_ALONE_MAXIMAL_COP: ClassVar[float] = 1.0

    @staticmethod
    def straddle_the_heating_rod_switch(*, lower: "HplibResult", upper: "HplibResult") -> bool:
        """Return whether hplib runs its heating rod alone at exactly one of two neighbouring operating points.

        hplib switches from its compressor to its heating rod alone where its fitted coefficient of performance falls
        to 1, and its thermal power jumps there: for example, for one machine, from 5.0 kW (compressor at a COP of
        1.01) to the 7.55 kW of the rod. A linear interpolation between a grid point on either side of that switch
        turns that jump into a ramp of about 25 kW/K over 0.1 K.

        The check compares hplib's own verdicts (:attr:`heating_rod_alone`), not the COPs: a stated SCOP below
        hplib's fit calibrates the compressor's COP next to the switch below 1 (1.0033 x 0.876 = 0.879), and a
        comparison of the calibrated COPs would count both points as the rod's and interpolate across the jump
        (hisim-g0dp).

        Args:
            lower: hplib's result at the lower grid point.
            upper: hplib's result at the upper grid point.

        Returns:
            Whether one result is the heating rod's and the other is not.
        """
        return lower.heating_rod_alone != upper.heating_rod_alone

    @staticmethod
    def interpolated(*, lower: "HplibResult", upper: "HplibResult", share_of_upper: float) -> "HplibResult":
        """Return the linear interpolation between hplib's results at two neighbouring return temperatures.

        Every numeric field is ``lower + share_of_upper (upper - lower)``; :attr:`heating_rod_alone` is the lower
        point's, because two points interpolated lie on one side of the switch and share it. For example, a return of
        47.04 °C on the 0.1 K grid takes 60 % of the result at 47.0 °C and 40 % of the one at 47.1 °C.

        Args:
            lower: The result at the lower grid point.
            upper: The result at the upper grid point.
            share_of_upper: The upper point's weight, dimensionless, from 0 to 1.

        Returns:
            The interpolated result.
        """

        def between(lower_value: float, upper_value: float) -> float:
            """Return the value at ``share_of_upper`` of the way from ``lower_value`` to ``upper_value``."""
            return lower_value + share_of_upper * (upper_value - lower_value)

        return HplibResult(
            outlet_temperature_in_celsius=between(
                lower.outlet_temperature_in_celsius, upper.outlet_temperature_in_celsius
            ),
            mass_flow_in_kg_per_second=between(lower.mass_flow_in_kg_per_second, upper.mass_flow_in_kg_per_second),
            thermal_power_in_watt=between(lower.thermal_power_in_watt, upper.thermal_power_in_watt),
            electrical_power_in_watt=between(lower.electrical_power_in_watt, upper.electrical_power_in_watt),
            cop=between(lower.cop, upper.cop),
            eer=between(lower.eer, upper.eer),
            heating_rod_alone=lower.heating_rod_alone,
        )


@dataclass(frozen=True)
class HeatPumpStepConditions:

    """The temperatures the hplib heat pump reads in one step.

    A set temperature the configuration does not read is None: the space-heating set temperature is read only in
    the fixed-flow mode and for passive cooling, the hot-water set temperature only with hot-water preparation.
    """

    #: The source temperature on the primary side, in °C.
    source_temperature_in_celsius: float
    #: The outside air temperature, in °C.
    ambient_temperature_in_celsius: float
    #: The return temperature of the space-heating circuit, in °C.
    space_heating_return_temperature_in_celsius: float
    #: The return temperature of the hot-water circuit, the tank's step mean, in °C; 0 without hot water.
    hot_water_return_temperature_in_celsius: float
    #: The space-heating set temperature, in °C, or None where it is not read.
    space_heating_set_temperature_in_celsius: Optional[float]
    #: The hot-water supply temperature the hot-water controller aims at, in °C, or None without hot water.
    hot_water_supply_set_temperature_in_celsius: Optional[float]


@dataclass(frozen=True)
class HeatPumpRunTimers:

    """How long the heat pump has been heating, cooling and off, in s.

    Only the timer of the current mode runs; the two others are 0. Space heating and hot water share the heating
    timer.
    """

    #: How long the machine has been heating, in s.
    time_on_heating_in_seconds: int
    #: How long the machine has been cooling, in s.
    time_on_cooling_in_seconds: int
    #: How long the machine has been off, in s.
    time_off_in_seconds: int


@dataclass(frozen=True)
class HeatPumpModeTrack:
    """The heat pump's mode in a step and its run timers after that step, on one of its two tracks.

    The control track holds the mode the heat pump runs on after its minimum running and idle times; its timers feed
    those minimum times and the start-up ramp, so it decides what the machine does. The published track holds the
    mode the machine ran, where a space-heating step at a part-load ratio of 0 is off; its mode and timers are what
    the heat pump publishes as its on/off state, ``TimeOnHeating`` and ``TimeOff``, and what its switch counters and
    KPIs count. For example, at 900 s a space-heating step at ratio 0 after 600 s of heating is
    ``HeatPumpModeTrack(1, HeatPumpRunTimers(1500, 0, 0))`` on the control track and
    ``HeatPumpModeTrack(0, HeatPumpRunTimers(0, 0, 900))`` on the published one.
    """

    #: The mode: 1 space heating, 2 hot water, -1 cooling, 0 off.
    on_off: float
    #: The run timers after the step in that mode, in s.
    timers: HeatPumpRunTimers


@dataclass(frozen=True)
class HeatPumpSwitchCounters:

    """How often the heat pump has entered space heating, hot water and operation, since the run began.

    Each count is a number of switches, dimensionless.
    """

    #: Switches into space heating.
    space_heating: int
    #: Switches into hot water.
    hot_water: int
    #: Switches from off into any mode.
    on_off: int


@dataclass(frozen=True)
class HeatPumpOperation:

    """The hplib heat pump's powers, outlets and flows in one step, whatever mode it runs in.

    Each mode fills it through one factory (:meth:`space_heating`, :meth:`hot_water`, :meth:`cooling`,
    :meth:`idle`); the circuit that does not run has no flow and its outlet is its return. The totals are
    properties, summed in one order, so every output derives from the same numbers.
    """

    #: The heat the space-heating circuit carries, in W; negative while cooling.
    thermal_power_space_heating_in_watt: float
    #: The heat the hot-water circuit carries, in W.
    thermal_power_hot_water_in_watt: float
    #: The compressor's electricity for space heating, in W.
    electrical_power_space_heating_in_watt: float
    #: The compressor's electricity for hot water, in W.
    electrical_power_hot_water_in_watt: float
    #: The compressor's electricity for active cooling, in W.
    electrical_power_cooling_in_watt: float
    #: The brine or well pump's electricity, in W.
    electrical_power_brine_pump_in_watt: float
    #: The coefficient of performance, dimensionless; 0 while not heating.
    cop: float
    #: The energy efficiency ratio of cooling, dimensionless.
    eer: float
    #: The space-heating circuit's outlet temperature, in °C.
    outlet_temperature_space_heating_in_celsius: float
    #: The hot-water circuit's outlet temperature, in °C.
    outlet_temperature_hot_water_in_celsius: float
    #: The space-heating circuit's mass flow, in kg/s.
    mass_flow_space_heating_in_kg_per_second: float
    #: The hot-water circuit's mass flow, in kg/s.
    mass_flow_hot_water_in_kg_per_second: float

    @property
    def total_thermal_power_in_watt(self) -> float:
        """Return the heat of both circuits, in W."""
        return self.thermal_power_hot_water_in_watt + self.thermal_power_space_heating_in_watt

    @property
    def total_electrical_power_in_watt(self) -> float:
        """Return the electricity of the compressor in every mode and of the brine pump, in W."""
        return (
            self.electrical_power_hot_water_in_watt
            + self.electrical_power_space_heating_in_watt
            + self.electrical_power_cooling_in_watt
            + self.electrical_power_brine_pump_in_watt
        )

    @property
    def thermal_power_from_environment_in_watt(self) -> float:
        """Return the heat taken from the source, the heat delivered minus the electricity, in W."""
        return self.total_thermal_power_in_watt - self.total_electrical_power_in_watt

    @staticmethod
    def space_heating(
        conditions: HeatPumpStepConditions,
        *,
        powers: "HeatingCircuitPowers",
        electrical_power_brine_pump_in_watt: float,
        cop: float,
        eer: float,
        outlet_temperature_in_celsius: float,
        mass_flow_in_kg_per_second: float,
    ) -> "HeatPumpOperation":
        """Return a step that heats the building; the hot-water circuit idles at its return."""
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=powers.thermal_power_in_watt,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=powers.electrical_power_in_watt,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=electrical_power_brine_pump_in_watt,
            cop=cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=outlet_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=mass_flow_in_kg_per_second,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )

    @staticmethod
    def hot_water(
        conditions: HeatPumpStepConditions,
        *,
        powers: "HeatingCircuitPowers",
        electrical_power_brine_pump_in_watt: float,
        cop: float,
        eer: float,
        outlet_temperature_in_celsius: float,
        mass_flow_in_kg_per_second: float,
    ) -> "HeatPumpOperation":
        """Return a step that heats the hot water; the space-heating circuit idles at its return."""
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=0.0,
            thermal_power_hot_water_in_watt=powers.thermal_power_in_watt,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=powers.electrical_power_in_watt,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=electrical_power_brine_pump_in_watt,
            cop=cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=conditions.space_heating_return_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=outlet_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=0.0,
            mass_flow_hot_water_in_kg_per_second=mass_flow_in_kg_per_second,
        )

    @staticmethod
    def cooling(
        conditions: HeatPumpStepConditions,
        *,
        thermal_power_in_watt: float,
        electrical_power_cooling_in_watt: float,
        electrical_power_brine_pump_in_watt: float,
        cop: float,
        eer: float,
        outlet_temperature_in_celsius: float,
        mass_flow_in_kg_per_second: float,
    ) -> "HeatPumpOperation":
        """Return a step that cools the building through the space-heating circuit; the hot water idles."""
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=thermal_power_in_watt,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=electrical_power_cooling_in_watt,
            electrical_power_brine_pump_in_watt=electrical_power_brine_pump_in_watt,
            cop=cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=outlet_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=mass_flow_in_kg_per_second,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )

    @staticmethod
    def idle(conditions: HeatPumpStepConditions) -> "HeatPumpOperation":
        """Return a step with the machine off: no heat, no electricity, both circuits idle at their returns.

        The COP and EER are 0 rather than undefined, since post-processing cannot read a missing value.
        """
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=0.0,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=0.0,
            cop=0.0,
            eer=0.0,
            outlet_temperature_space_heating_in_celsius=conditions.space_heating_return_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=0.0,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )


@dataclass(frozen=True)
class HeatPumpEnergyTotals:

    """The hplib heat pump's thermal and electrical energies of one step and its running totals after it, in Wh.

    The running totals grow by the magnitude of each step's energy.
    """

    #: The heat of both circuits in the step, in Wh.
    thermal_total_in_watt_hour: float
    #: The heat of the space-heating circuit in the step, in Wh.
    thermal_space_heating_in_watt_hour: float
    #: The heat of the hot-water circuit in the step, in Wh.
    thermal_hot_water_in_watt_hour: float
    #: All electricity of the step, in Wh.
    electrical_total_in_watt_hour: float
    #: The compressor's electricity for space heating in the step, in Wh.
    electrical_space_heating_in_watt_hour: float
    #: The compressor's electricity for hot water in the step, in Wh.
    electrical_hot_water_in_watt_hour: float
    #: The running total of the heat of both circuits, in Wh.
    cumulative_thermal_total_in_watt_hour: float
    #: The running total of the space-heating heat, in Wh.
    cumulative_thermal_space_heating_in_watt_hour: float
    #: The running total of the hot-water heat, in Wh.
    cumulative_thermal_hot_water_in_watt_hour: float
    #: The running total of all electricity, in Wh.
    cumulative_electrical_total_in_watt_hour: float
    #: The running total of the space-heating electricity, in Wh.
    cumulative_electrical_space_heating_in_watt_hour: float
    #: The running total of the hot-water electricity, in Wh.
    cumulative_electrical_hot_water_in_watt_hour: float


@dataclass
class CalculationRequest:
    """Class for caching HPLib parameters so that HPLib.simulate does not need to run so often."""

    t_in_primary: float
    t_in_secondary: float
    t_amb: float
    mode: int
    operation_mode: str

    def get_key(self):
        """Get key of class with important parameters."""

        return (
            str(self.t_in_primary)
            + " "
            + str(self.t_in_secondary)
            + " "
            + str(self.t_amb)
            + " "
            + str(self.mode)
            + " "
            + str(self.operation_mode)
        )
