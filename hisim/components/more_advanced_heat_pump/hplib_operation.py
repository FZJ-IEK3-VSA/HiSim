"""hplib's answer as a typed value and the frozen values one step of the hplib heat pump passes between its functions.

:class:`HplibResult` is the adapter between hplib's result dictionary and the heat pump: the heat pump reads typed
fields instead of dictionary keys, and the interpolation between two cached grid points is one pure function.
:class:`CalculationRequest` is the key of that cache. :class:`HeatPumpOperation` and its companions
(:class:`HeatPumpStepConditions`, :class:`HeatPumpRunTimers`, :class:`HeatPumpModeTrack`,
:class:`HeatPumpSwitchCounters`, :class:`HeatPumpEnergyTotals`) carry one step's inputs, timers, counters and running
totals, so the component's step is a chain of pure functions over them. Nothing here needs the component
(:mod:`hisim.components.more_advanced_heat_pump.heat_pump`).
"""

from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple, Optional

from hisim.components.more_advanced_heat_pump.backup_rod import (
    RodAssistedHeating,
)


@dataclass(frozen=True)
class HplibResult:

    """The machine's answer at one operating point, the numbers the heat pump computes with.

    An adapter between the result dictionary of :class:`CarnotCopHeatPump` (hplib's keys: hplib's thermal power,
    outlet and flow, the Carnot-efficiency COP) and the heat pump: the heat pump reads typed fields instead of
    dictionary keys, and the interpolation between two cached grid points is one pure function
    (:meth:`interpolated`).
    """

    #: The water temperature leaving the heat pump's secondary side, in °C (hplib's ``T_out``).
    outlet_temperature_in_celsius: float
    #: The secondary side's mass flow, in kg/s (hplib's ``m_dot``).
    mass_flow_in_kg_per_second: float
    #: The machine's thermal power, hplib's compressor capacity, in W (``P_th``).
    thermal_power_in_watt: float
    #: The machine's electrical power, the thermal power over the COP, in W (``P_el``).
    electrical_power_in_watt: float
    #: The coefficient of performance, dimensionless (``COP``).
    cop: float
    #: The energy efficiency ratio of cooling, dimensionless (``EER``).
    eer: float

    @staticmethod
    def from_hplib(results: Mapping[str, Any]) -> "HplibResult":
        """Return the fields of a machine's result dictionary, as floats.

        For example, ``{"T_out": 35.0, "m_dot": 0.38, "P_th": 7900.0, "P_el": 1880.0, "COP": 4.2, "EER": 0.0}`` gives a
        result with an outlet of 35 °C and a COP of 4.2.

        Args:
            results: The machine's result, calibrated or not, with the keys ``T_out``, ``m_dot``, ``P_th``, ``P_el``,
                ``COP`` and ``EER``.

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
        )

    @staticmethod
    def interpolated(*, lower: "HplibResult", upper: "HplibResult", share_of_upper: float) -> "HplibResult":
        """Return the linear interpolation between hplib's results at two neighbouring return temperatures.

        Every field is ``lower + share_of_upper (upper - lower)``. For example, a return of 47.04 °C on the 0.1 K grid
        takes 60 % of the result at 47.0 °C and 40 % of the one at 47.1 °C. The machine's answers are continuous in the
        return (hplib's jump to its heating rod is gone, :class:`CarnotCopHeatPump`), so every return is interpolated.

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
        )


class SpaceHeatingPowerRange(NamedTuple):
    """The space-heating electricity the hplib heat pump's compressor would draw at full load and at its minimum load, in W.

    The heat pump publishes both once per step, from the step's start temperatures, for its space-heating controller
    and the energy manager. Both are the compressor's: the backup heating rod is not part of them. For example, a
    compressor that gives 6,000 W of heat at a COP of 4 with a 1,800 W minimum thermal power has a range of 1,500 W at
    full load and 450 W at minimum load.
    """

    #: The compressor's electricity at full load, in W.
    full_load_electrical_power_in_watt: float
    #: The compressor's electricity at the minimum thermal power, in W; at most the full-load electricity.
    minimum_load_electrical_power_in_watt: float


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
    properties, summed in one order, so every output derives from the same numbers. A heating circuit's heat is the
    compressor's and the backup heating rod's (:class:`RodAssistedHeating`); its electricity for space heating or hot
    water is the compressor's, and the rod's electricity is its own field.
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
    #: The backup heating rod's electricity, in W, in space heating or hot water; also its heat.
    electrical_power_heating_rod_in_watt: float
    #: The coefficient of performance of heating, the circuit's heat over the compressor's and the rod's
    #: electricity, dimensionless; 0 while not heating.
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
        """Return the electricity of the compressor in every mode, of the backup heating rod and of the brine pump, in W."""
        return (
            self.electrical_power_hot_water_in_watt
            + self.electrical_power_space_heating_in_watt
            + self.electrical_power_cooling_in_watt
            + self.electrical_power_brine_pump_in_watt
            + self.electrical_power_heating_rod_in_watt
        )

    @property
    def thermal_power_from_environment_in_watt(self) -> float:
        """Return the heat taken from the source, the heat delivered minus the electricity, in W.

        Signed: negative while the machine cools (it rejects the heat it draws plus its electricity) and in a step that
        heats at a coefficient of performance below 1, which the SCOP calibration on top of the Carnot-efficiency COP
        can give. The energy balance reads its three parts, :attr:`heat_from_ambient_in_watt`,
        :attr:`heat_to_ambient_in_watt` and :attr:`electricity_lost_in_watt`, whose difference it is.
        """
        return self.total_thermal_power_in_watt - self.total_electrical_power_in_watt

    @property
    def cools(self) -> bool:
        """Whether the step cools: the space-heating circuit carries heat to the machine (a negative heat)."""
        return self.thermal_power_space_heating_in_watt < 0.0

    @property
    def heat_from_ambient_in_watt(self) -> float:
        """Return the heat the machine takes from its source in a step that does not cool, in W, >= 0.

        For example, 8,000 W of heat delivered for 2,000 W of electricity take 6,000 W from the outside air. Zero in a
        cooling step, and in a step whose electricity exceeds its heat (:attr:`electricity_lost_in_watt`).
        """
        if self.cools:
            return 0.0
        return max(self.thermal_power_from_environment_in_watt, 0.0)

    @property
    def heat_to_ambient_in_watt(self) -> float:
        """Return the heat the machine rejects to its source while it cools, in W, >= 0: the heat it draws plus its electricity.

        For example, drawing 6,000 W from the space-heating circuit with 2,000 W of compressor electricity rejects
        8,000 W; passive cooling rejects the drawn heat plus the brine pump's electricity. Zero in a step that does not
        cool.
        """
        if not self.cools:
            return 0.0
        return max(-self.thermal_power_from_environment_in_watt, 0.0)

    @property
    def electricity_lost_in_watt(self) -> float:
        """Return the electricity beyond the heat delivered in a step that does not cool, in W, >= 0.

        At a calibrated coefficient of performance below 1 the machine draws more electricity than it delivers as
        heat: 4,000 W for 3,520 W of heat at a COP of 0.88 lose 480 W. That is no heat taken from the source and no
        heat rejected while cooling, so it is booked as a loss. Zero in a cooling step.
        """
        if self.cools:
            return 0.0
        return max(-self.thermal_power_from_environment_in_watt, 0.0)

    @staticmethod
    def space_heating(
        conditions: HeatPumpStepConditions,
        *,
        heating: RodAssistedHeating,
        electrical_power_brine_pump_in_watt: float,
        eer: float,
    ) -> "HeatPumpOperation":
        """Return a step that heats the building; the hot-water circuit idles at its return.

        The brine pump runs the compressor's share of the step, so it draws that share of its power.
        """
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=heating.thermal_power_in_watt,
            thermal_power_hot_water_in_watt=0.0,
            electrical_power_space_heating_in_watt=heating.compressor_electrical_power_in_watt,
            electrical_power_hot_water_in_watt=0.0,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=heating.compressor_running_share * electrical_power_brine_pump_in_watt,
            electrical_power_heating_rod_in_watt=heating.heating_rod_electrical_power_in_watt,
            cop=heating.cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=heating.outlet_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=conditions.hot_water_return_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=heating.mass_flow_in_kg_per_second,
            mass_flow_hot_water_in_kg_per_second=0.0,
        )

    @staticmethod
    def hot_water(
        conditions: HeatPumpStepConditions,
        *,
        heating: RodAssistedHeating,
        electrical_power_brine_pump_in_watt: float,
        eer: float,
    ) -> "HeatPumpOperation":
        """Return a step that heats the hot water; the space-heating circuit idles at its return.

        The brine pump runs the compressor's share of the step, so it draws that share of its power.
        """
        return HeatPumpOperation(
            thermal_power_space_heating_in_watt=0.0,
            thermal_power_hot_water_in_watt=heating.thermal_power_in_watt,
            electrical_power_space_heating_in_watt=0.0,
            electrical_power_hot_water_in_watt=heating.compressor_electrical_power_in_watt,
            electrical_power_cooling_in_watt=0.0,
            electrical_power_brine_pump_in_watt=heating.compressor_running_share * electrical_power_brine_pump_in_watt,
            electrical_power_heating_rod_in_watt=heating.heating_rod_electrical_power_in_watt,
            cop=heating.cop,
            eer=eer,
            outlet_temperature_space_heating_in_celsius=conditions.space_heating_return_temperature_in_celsius,
            outlet_temperature_hot_water_in_celsius=heating.outlet_temperature_in_celsius,
            mass_flow_space_heating_in_kg_per_second=0.0,
            mass_flow_hot_water_in_kg_per_second=heating.mass_flow_in_kg_per_second,
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
            electrical_power_heating_rod_in_watt=0.0,
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
            electrical_power_heating_rod_in_watt=0.0,
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
