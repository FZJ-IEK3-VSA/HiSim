"""The electric backup heating rod inside the hplib heat pump, which lifts the water beyond the compressor's maximal outlet.

:class:`BackupHeatingRod` holds the rod's rated electrical power and decides, per step, how much heat it adds at a COP
of 1: only where the required outlet exceeds the compressor's maximal outlet. :class:`RodAssistedHeating` is one heating
circuit's step shared between the compressor and the rod: the heat and electricity of each, the averaged flow and the
outlet the circuit publishes.
"""

import math
from dataclasses import dataclass
from typing import ClassVar

from hisim import hydronics
from hisim.subcomponents.base import Subcomponent


@dataclass(frozen=True)
class RodAssistedHeating:
    """One heating circuit's step shared between the compressor and the backup heating rod.

    The compressor delivers what it can at its COP, the rod adds the missing heat at a COP of 1, and the circuit's water
    carries both. The flow and outlet are what the circuit publishes: the averaged flow (the full-load flow times the
    part-load ratio) at the outlet that flow reaches with the delivered heat. For example, a whole step of 0.4 kg/s from
    60 °C to 65 °C whose compressor stops at 62 °C delivers 3344 W from the compressor and 5016 W from the rod, at
    0.4 kg/s and 65 °C. A step that runs nothing has no flow, no heat and no electricity, its outlet is its return and
    its COP 0.
    """

    #: The heat the compressor delivers, in W.
    compressor_thermal_power_in_watt: float
    #: The electricity the compressor draws for it, in W.
    compressor_electrical_power_in_watt: float
    #: The electricity the backup heating rod draws, in W; it is also the rod's heat (COP 1).
    heating_rod_electrical_power_in_watt: float
    #: The fraction of the step the compressor runs at its full output, from 0 to 1: the brine pump runs with it.
    compressor_running_share: float
    #: The circuit's averaged mass flow, in kg/s.
    mass_flow_in_kg_per_second: float
    #: The circuit's outlet temperature, in °C.
    outlet_temperature_in_celsius: float

    @property
    def thermal_power_in_watt(self) -> float:
        """Return the heat the circuit's water carries, the compressor's and the rod's, in W."""
        return self.compressor_thermal_power_in_watt + self.heating_rod_electrical_power_in_watt

    @property
    def electrical_power_in_watt(self) -> float:
        """Return the electricity of the compressor and the rod, in W."""
        return self.compressor_electrical_power_in_watt + self.heating_rod_electrical_power_in_watt

    @property
    def cop(self) -> float:
        """Return the circuit's coefficient of performance with the rod, heat over electricity; 0 without electricity.

        For example, 3344 W from the compressor at a COP of 2.5 and 5016 W from the rod give 8360 W for 6353.6 W, a COP
        of 1.32.
        """
        electrical_power_in_watt = self.electrical_power_in_watt
        if electrical_power_in_watt <= 0.0:
            return 0.0
        return self.thermal_power_in_watt / electrical_power_in_watt


@dataclass(frozen=True)
class BackupHeatingRod(Subcomponent):
    """The electric backup heating rod inside the heat pump: it adds the heat the compressor cannot, at a COP of 1.

    Real air/water and brine/water units ship with an electric heating element for the coldest days. The rod heats the
    same water as the compressor and runs only where the required outlet exceeds the compressor's maximal outlet
    (:meth:`CarnotCopHeatPump.maximal_outlet_temperature_in_celsius`): the compressor lifts the circuit's flow to its
    maximal outlet, and the rod lifts it on to the required outlet, ``m c (T_required - T_max)``.

    The rod does not fill a request above the compressor's capacity (owner decision of 2026-11-26). The heat pump holds
    its outlet at a set point, so its request is a control target, not a demand: a cold return after a hot-water charge
    asks for far more than the house needs, and a rod filling that gap would run where no real unit's rod runs. A real
    unit's controller releases its backup on a sustained shortfall instead (bead hisim-heat-pump-model-rework-fpqz.8).
    What the compressor cannot deliver below its maximal outlet is not delivered, and the circuit's outlet falls short.

    The decision is one of energy over the whole step (:meth:`heating`): the compressor runs at its full output for as
    much of the step as the request needs, and the rod covers only the part of the request the compressor could not
    deliver even at unlimited capacity, the heat beyond its maximal outlet, ``E_rod = min(Q_requested - m c (T_max -
    T_return) dt, P_rod dt)``. What the rod cannot cover either is not delivered. Its electricity is the heat pump's
    output ``ElectricalInputPowerHeatingRod`` with its own energy port; the rod is no part of the EN 14825 rating or the
    SCOP calibration.

    Source of the default rating (:attr:`DEFAULT_LARGEST_RATED_ELECTRICAL_POWER_IN_WATT`, spec §3.1): the residential
    indoor units of the main manufacturers carry one element class of 6 or 9 kW, switchable in 3 kW stages by the
    installer (Vaillant uniTOWER plus / VWZ MEH 97: 9 kW; Viessmann Vitocal 250-A: 9 kW in 3/6/9 kW stages; Daikin
    Altherma 3: 3, 6 or 9 kW; Bosch Compress 7000i AW: 9 kW; Stiebel Eltron HPA-O and WPL: 8.8 kW), and the installer's
    stage setting keeps the rod below the compressor's rated heat; the heat pump's sizing law takes the smaller of the
    two.

    Attributes:
        rated_electrical_power_in_watt: The rod's rated electrical power, in W, 0 or more; 0 is a heat pump without one.
    """

    rated_electrical_power_in_watt: float

    #: The rod's rated electrical power where the heat pump's rated thermal power is larger, in W: the 9 kW element
    #: class of the residential indoor units (see the class docstring).
    DEFAULT_LARGEST_RATED_ELECTRICAL_POWER_IN_WATT: ClassVar[float] = 9000.0

    def __post_init__(self) -> None:
        """Refuse a rated power that is negative or not finite, which would book negative or infinite electricity.

        Raises:
            ValueError: If the rated power is negative, infinite or not a number.
        """
        if not math.isfinite(self.rated_electrical_power_in_watt) or self.rated_electrical_power_in_watt < 0.0:
            raise ValueError(
                "The backup heating rod's rated electrical power must be a finite number of watts, 0 or more, got "
                f"{self.rated_electrical_power_in_watt}."
            )

    def heating(
        self,
        *,
        part_load_ratio: float,
        mass_flow_in_kg_per_second: float,
        return_temperature_in_celsius: float,
        required_outlet_temperature_in_celsius: float,
        compressor_thermal_capacity_in_watt: float,
        compressor_maximal_outlet_temperature_in_celsius: float,
        compressor_cop: float,
    ) -> RodAssistedHeating:
        """Return a heating circuit's step shared between the compressor and the rod.

        All powers are averages over the step. The request is the part-load ratio's share of the full-load heat at the
        required outlet, ``P_req = r m c (T_required - T_return)``. What the flow carries up to the compressor's maximal
        outlet is ``P_outlet = m c (min(T_max, T_required) - T_return)``, at least 0, and the compressor's most over the
        whole step is the smaller of that and its capacity, ``P_cmax = min(P_cap, P_outlet)``. The compressor delivers
        ``min(P_req, P_cmax)`` and runs that share of the step at full output; the rod adds the heat beyond the maximal
        outlet, ``min(max(P_req - P_outlet, 0), P_rod)``, and nothing for a request above the capacity. The circuit
        publishes the averaged flow ``r m`` at the required outlet, or, where the compressor and the rod together fall
        short, at the lower outlet that flow reaches with the delivered heat.

        For example, 0.4 kg/s from a 60 °C return to a 65 °C required outlet (8360 W), a compressor of 8 kW capacity
        whose maximal outlet is 62 °C at a COP of 2.5, and a 9 kW rod: at ratio 1 the compressor delivers 3344 W for
        1337.6 W and the rod 5016 W; at ratio 0.4 the request of 3344 W is what the compressor delivers over the whole
        step at its smaller lift, so the rod stays off, at 0.16 kg/s and 65 °C. With a 2 kW rod at ratio 1 the circuit
        gets 5344 W and an outlet of 63.2 °C. A compressor of 6 kW capacity below a 75 °C maximal outlet delivers its
        6000 W of the 8360 W, the rod stays off and the outlet is 63.59 °C.

        Args:
            part_load_ratio: The fraction of the step the request asks for at full load, from 0 to 1.
            mass_flow_in_kg_per_second: The circuit's flow at full load, in kg/s.
            return_temperature_in_celsius: The circuit's return temperature, in °C.
            required_outlet_temperature_in_celsius: The outlet the step asks for, in °C.
            compressor_thermal_capacity_in_watt: The compressor's thermal capacity at the step's temperatures, in W.
            compressor_maximal_outlet_temperature_in_celsius: The compressor's maximal outlet, in °C.
            compressor_cop: The compressor's COP at its own outlet, the lower of the required and the maximal outlet,
                dimensionless; above 0 wherever the compressor delivers heat.

        Returns:
            The step's heat, electricity, flow and outlet.

        Raises:
            ValueError: If the compressor delivers heat at a COP of 0 or less, which would book infinite or negative
                electricity.
        """
        heat_per_kelvin_in_watt_per_kelvin = mass_flow_in_kg_per_second * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
        full_load_heat_in_watt = heat_per_kelvin_in_watt_per_kelvin * max(
            required_outlet_temperature_in_celsius - return_temperature_in_celsius, 0.0
        )
        requested_heat_in_watt = part_load_ratio * full_load_heat_in_watt
        mass_flow_averaged_in_kg_per_second = part_load_ratio * mass_flow_in_kg_per_second
        if requested_heat_in_watt <= 0.0:
            # Nothing requested: the step runs nothing (ratio 0), or its water passes at no lift.
            return RodAssistedHeating(
                compressor_thermal_power_in_watt=0.0,
                compressor_electrical_power_in_watt=0.0,
                heating_rod_electrical_power_in_watt=0.0,
                compressor_running_share=0.0,
                mass_flow_in_kg_per_second=mass_flow_averaged_in_kg_per_second,
                outlet_temperature_in_celsius=return_temperature_in_celsius,
            )
        heat_up_to_maximal_outlet_in_watt = max(
            heat_per_kelvin_in_watt_per_kelvin
            * (
                min(compressor_maximal_outlet_temperature_in_celsius, required_outlet_temperature_in_celsius)
                - return_temperature_in_celsius
            ),
            0.0,
        )
        compressor_maximal_heat_in_watt = max(min(compressor_thermal_capacity_in_watt, heat_up_to_maximal_outlet_in_watt), 0.0)
        compressor_heat_in_watt = min(requested_heat_in_watt, compressor_maximal_heat_in_watt)
        rod_heat_in_watt = min(
            max(requested_heat_in_watt - heat_up_to_maximal_outlet_in_watt, 0.0), self.rated_electrical_power_in_watt
        )
        if compressor_heat_in_watt + rod_heat_in_watt < requested_heat_in_watt:
            outlet_in_celsius = return_temperature_in_celsius + (compressor_heat_in_watt + rod_heat_in_watt) / (
                mass_flow_averaged_in_kg_per_second * hydronics.Water.SPECIFIC_HEAT_J_PER_KG_K
            )
        else:
            outlet_in_celsius = required_outlet_temperature_in_celsius
        # The heat the published flow carries, so the booked heat is exactly the flow's; the compressor books the rest.
        carried_heat_in_watt = hydronics.circuit_power_w(
            mass_flow_kg_per_s=mass_flow_averaged_in_kg_per_second,
            t_supply_c=outlet_in_celsius,
            t_return_c=return_temperature_in_celsius,
        )
        compressor_heat_in_watt = max(carried_heat_in_watt - rod_heat_in_watt, 0.0)
        if compressor_heat_in_watt > 0.0 and not compressor_cop > 0.0:
            raise ValueError(
                f"A running compressor needs a coefficient of performance above 0 to book its electricity, got "
                f"{compressor_cop} for {compressor_heat_in_watt} W of heat."
            )
        return RodAssistedHeating(
            compressor_thermal_power_in_watt=compressor_heat_in_watt,
            compressor_electrical_power_in_watt=compressor_heat_in_watt / compressor_cop if compressor_heat_in_watt else 0.0,
            heating_rod_electrical_power_in_watt=rod_heat_in_watt,
            compressor_running_share=(
                min(compressor_heat_in_watt / compressor_maximal_heat_in_watt, 1.0)
                if compressor_maximal_heat_in_watt > 0.0
                else 0.0
            ),
            mass_flow_in_kg_per_second=mass_flow_averaged_in_kg_per_second,
            outlet_temperature_in_celsius=outlet_in_celsius,
        )
