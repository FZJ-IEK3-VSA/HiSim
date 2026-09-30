"""What a heat generator books when a water storage tells it how much of its heat it took.

hisim-4g9.16 (owner decision of 2026-09-27, option B): a :mod:`~hisim.components.simple_water_storage` vessel
accepts a generator's delivery only up to what keeps it at or below the supply temperature, and publishes the heat
it accepted per generator slot as ``ThermalPowerFromHeatGenerator`` / ``ThermalPowerFromSecondaryHeatGenerator``.
A generator wired to that output books the accepted heat as its thermal output and scales its fuel or electricity
by the same share, so it pays for the heat the vessel holds and for nothing else. A generator whose input is left
unconnected -- one that feeds no storage, or a setup that does not wire the feedback -- books its own heat as before.

The accepted heat is an output of the storage in the same timestep and depends only on the generator's flow, its
supply temperature and the vessel's start-of-step state, never on what the generator books. So the feedback adds
at most one iteration to a timestep's convergence loop and cannot oscillate.
"""

from typing import Optional, Tuple

from hisim import loadtypes as lt
from hisim.component import ComponentInput, SingleTimeStepValues
from hisim.components.configuration import PhysicsConfig


class AcceptedHeat:
    """The booking rule of a generator that feeds a water storage."""

    @staticmethod
    def booked(
        own_thermal_power_in_watt: float,
        accepted_channel: ComponentInput,
        stsv: SingleTimeStepValues,
    ) -> Tuple[float, float]:
        """The thermal power a generator books, and the share of its own carrier use that pays for it.

        Connected, the booked power is the storage's accepted heat, never negative, and a generator that
        produced nothing or less (``own <= 0``: a machine that is off or starting, or has just switched circuit)
        books nothing and pays for nothing, whatever the storage still reports at an intermediate iteration
        of the convergence loop. No heat is booked without fuel. The share is therefore never negative.

        The booked power is deliberately not capped at ``own``. The storage accepts what the generator's flow
        carries, ``m c (T_supply - T_return)``, and the heat pump's flow -- hplib's mass flow and outlet
        temperature -- carries up to about 0.5 % more than hplib's thermal power at converged steps
        (household_heatpump_building_sizer, one week at 60 s). A cap at ``own`` would leave that heat in the
        vessel unbooked and break its balance, ``Q_booked - Q_draw - Q_loss - dU = 0``; so the heat pump
        books what the vessel holds, and its share may exceed 1 by that margin.

        Args:
            own_thermal_power_in_watt: What the generator itself computed it delivers on this path.
            accepted_channel: Its input wired to the storage's accepted-heat output for this path.
            stsv: The timestep's values.

        Returns:
            Tuple[float, float]: The thermal power to book in W, and the factor its fuel or electricity for this
            path is multiplied by: ``accepted / own`` when connected (``(0.0, 0.0)`` when the generator itself
            delivered nothing or less), and ``(own, 1.0)`` when the input is unconnected.
        """
        if accepted_channel.source_output is None:
            return own_thermal_power_in_watt, 1.0
        if own_thermal_power_in_watt <= 0:
            return 0.0, 0.0
        accepted_thermal_power_in_watt = max(stsv.get_input_value(accepted_channel), 0.0)
        return accepted_thermal_power_in_watt, accepted_thermal_power_in_watt / own_thermal_power_in_watt


class StorageForecast:
    """What a generator controller reads from a water storage for its feed-forward (hisim-6ehm).

    A storage publishes its draw forecast for the step (the consumer's request and the standby loss, known
    before any generator acts) and its water mass. From them a controller forecasts the vessel's temperature
    at the end of the step if it does not heat: ``T0 - Q_draw / (M c)``. An on/off controller switches on
    that forecast instead of the start temperature, so it starts before the vessel has fallen through its
    band rather than one step late.
    """

    @staticmethod
    def temperature_after_draw_in_celsius(
        start_temperature_in_celsius: float,
        draw_forecast_channel: ComponentInput,
        water_mass_channel: ComponentInput,
        stsv: SingleTimeStepValues,
        seconds_per_timestep: float,
    ) -> Optional[float]:
        """The vessel's temperature after this step's draw with no heating, or None when not wired."""
        if draw_forecast_channel.source_output is None or water_mass_channel.source_output is None:
            return None
        water_mass_in_kg = stsv.get_input_value(water_mass_channel)
        if water_mass_in_kg <= 0:
            return None
        specific_heat_in_joule_per_kg_per_kelvin = PhysicsConfig.get_properties_for_energy_carrier(
            energy_carrier=lt.LoadTypes.WATER
        ).specific_heat_capacity_in_joule_per_kg_per_kelvin
        return start_temperature_in_celsius - stsv.get_input_value(draw_forecast_channel) * seconds_per_timestep / (
            water_mass_in_kg * specific_heat_in_joule_per_kg_per_kelvin
        )


class DhwChargeYield:
    """When a hot-water charge yields a step to the space-heating buffer (hisim-6ehm).

    Hot water has priority and a charge lasts several steps, while a buffer sized at 20 to 50 l/kW holds less
    than one 900 s step of a winter heating draw: once the vessels conserve energy (hisim-4g9.16), every charge
    drained the buffer below the room temperature and the rooms cooled. A controller that charges the hot-water
    tank therefore gives a step to space heating when the buffer's temperature after this step's draw
    (:meth:`StorageForecast.temperature_after_draw_in_celsius`) is forecast more than
    :attr:`SPACE_HEATING_MARGIN_IN_KELVIN` below the set flow temperature, and the tank is at least at
    :attr:`DHW_TEMPERATURE_KEEPING_PRIORITY_IN_CELSIUS`. The charge itself is not ended; it resumes on the next
    step the buffer allows. The boiler controller and the heat pump's hot-water controller both ask this one rule;
    the boiler controller adds its heating-season condition on top.
    """

    #: The hot-water tank temperature below which a charge is never interrupted for space heating: five kelvin
    #: above the 40 degC the tap draws at.
    DHW_TEMPERATURE_KEEPING_PRIORITY_IN_CELSIUS: float = 45.0
    #: How far below the set flow temperature the buffer may be forecast before a charge yields it a step: the
    #: five kelvin spread of a heating circuit.
    SPACE_HEATING_MARGIN_IN_KELVIN: float = 5.0

    @classmethod
    def space_heating_buffer_needs_the_step(
        cls,
        buffer_temperature_after_draw_in_celsius: float,
        set_heating_flow_temperature_in_celsius: float,
        dhw_temperature_in_celsius: float,
    ) -> bool:
        """Whether this step of a hot-water charge goes to the space-heating buffer instead.

        Args:
            buffer_temperature_after_draw_in_celsius: The buffer's forecast temperature after this step's draw
                with no heating.
            set_heating_flow_temperature_in_celsius: The flow temperature the heat distribution system asks for.
            dhw_temperature_in_celsius: The hot-water tank's temperature at the start of the step.

        Returns:
            bool: True when the buffer is forecast more than the margin below the set flow temperature and the
            tank is warm enough to wait a step.
        """
        return (
            buffer_temperature_after_draw_in_celsius
            < set_heating_flow_temperature_in_celsius - cls.SPACE_HEATING_MARGIN_IN_KELVIN
            and dhw_temperature_in_celsius >= cls.DHW_TEMPERATURE_KEEPING_PRIORITY_IN_CELSIUS
        )
