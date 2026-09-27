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

        Args:
            own_thermal_power_in_watt: What the generator itself computed it delivers on this path.
            accepted_channel: Its input wired to the storage's accepted-heat output for this path.
            stsv: The timestep's values.

        Returns:
            Tuple[float, float]: The thermal power to book in W, and the factor its fuel or electricity for this
            path is multiplied by: ``accepted / own`` when connected (0 when the generator itself delivered
            nothing), and ``(own, 1.0)`` when the input is unconnected.
        """
        if accepted_channel.source_output is None:
            return own_thermal_power_in_watt, 1.0
        accepted_thermal_power_in_watt = stsv.get_input_value(accepted_channel)
        if own_thermal_power_in_watt > 0:
            return accepted_thermal_power_in_watt, accepted_thermal_power_in_watt / own_thermal_power_in_watt
        return accepted_thermal_power_in_watt, 0.0


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
