"""The second array and the second battery a modular household can carry beside its own.

A renovation measure that installs photovoltaics or a battery on a house that already has one adds
a second device beside it; it does not replace the first (owner decision of 2026-09-29,
hisim-epc.28). The building-sizer setups therefore build, on request, a second ``PVSystem`` with its
own orientation and size and a second ``Battery`` behind the same energy manager, and their recorded
twins carry both as options of the ``electricity_management`` variant, which the RenoVisor
translator selects.

Both are off by default (:class:`~hisim.building_sizer_utils.interface_configs.system_config.
EnergySystemConfig` ``use_added_pv`` / ``use_added_battery``), so every existing configuration builds
exactly the household it built before. The code lives here once rather than in each of the ten
setups, which only call :meth:`SecondaryDevices.add_array`, :meth:`SecondaryDevices.add_battery` and
:meth:`SecondaryDevices.add_energy_manager`.

Why the energy manager has to be wired by hand when there are two batteries: its default
connection names every ``Battery`` on the one source weight 6, and a dispatch port is identified by
its component type and weight, so a second battery through the defaults is refused
(``DynamicComponent._refuse_a_dispatch_port_this_aggregator_already_has``). The manager itself
dispatches any number of batteries: it ranks its controlled participants by weight and hands each
the surplus the ones before it left, so the house's battery (weight 6) charges and discharges first
and the added one (weight :attr:`SecondaryDevices.ADDED_BATTERY_WEIGHT`) takes the rest.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, List

from hisim import loadtypes as lt
from hisim.building_sizer_utils.interface_configs.system_config import EnergySystemConfig
from hisim.components import advanced_battery_bslib, generic_pv_system
from hisim.config import SizingContext, concrete
from hisim.simulationparameters import SimulationParameters


class SecondaryDevices:
    """Builds the second array and the second battery of a modular household, when asked to."""

    #: The component names, which are also the cost subjects the lifecycle engine prices them as.
    ADDED_ARRAY: ClassVar[str] = "PVSystemAdded"
    ADDED_BATTERY: ClassVar[str] = "BatteryAdded"

    #: The energy manager's source weight for the added battery: right after the house's own (6),
    #: so the house's battery is served first, and before the no-dispatch weight 999.
    ADDED_BATTERY_WEIGHT: ClassVar[int] = 7

    @classmethod
    def add_array(
        cls,
        my_sim: Any,
        energy_system_config: EnergySystemConfig,
        main_array_config: generic_pv_system.PVSystemConfig,
        roof_area_in_m2: float,
        weather_identity: str,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        """Add the second array when the configuration asks for one.

        It is the house's array again under its own name -- the same site, module, orientation and
        rooftop share -- because a configuration states no second orientation; a consumer of the
        recorded file sets the second array's own azimuth, tilt and power.

        Args:
            my_sim: The simulator the setup builds.
            energy_system_config: The configuration, whose ``use_added_pv`` decides.
            main_array_config: The house's array, resolved.
            roof_area_in_m2: The roof the rooftop law sizes the array against.
            weather_identity: The weather the array is computed with.
            my_simulation_parameters: The run's parameters.
        """
        if not energy_system_config.use_added_pv:
            return
        config = generic_pv_system.PVSystemConfig.preset_rooftop(cls.ADDED_ARRAY)
        config.location = main_array_config.location
        config.share_of_maximum_pv_potential = main_array_config.share_of_maximum_pv_potential
        config.azimuth = main_array_config.azimuth
        config.tilt = main_array_config.tilt
        config = config.resolve(SizingContext(roof_area_in_m2=roof_area_in_m2, weather_identity=weather_identity))
        array = generic_pv_system.PVSystem(config=config, my_simulation_parameters=my_simulation_parameters)
        my_sim.add_component(array, connect_automatically=True)

    @classmethod
    def battery_config(
        cls,
        name: str,
        energy_system_config: EnergySystemConfig,
        main_array_config: generic_pv_system.PVSystemConfig,
    ) -> advanced_battery_bslib.BatteryConfig:
        """A battery sized from the house's array, as every setup sizes its battery.

        With a second array the battery could be sized from either, and a recorded twin names no
        sizing source, so the recorder would pin the numbers with a comment the grouped file cannot
        restate. The setup therefore states them itself: the same numbers, sized from the house's
        array, written as values the setup assigned.

        Args:
            name: The battery's component name.
            energy_system_config: The configuration, whose ``use_added_pv`` decides.
            main_array_config: The house's array, resolved.

        Returns:
            The battery's configuration.
        """
        sized = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv(name).resolve(
            SizingContext(pv_peak_power_in_watt=concrete(main_array_config.power_in_watt))
        )
        if not energy_system_config.use_added_pv:
            return sized
        stated = advanced_battery_bslib.BatteryConfig.preset_sized_to_pv(name)
        stated.custom_pv_inverter_power_generic_in_watt = concrete(sized.custom_pv_inverter_power_generic_in_watt)
        stated.custom_battery_capacity_generic_in_kilowatt_hour = concrete(
            sized.custom_battery_capacity_generic_in_kilowatt_hour
        )
        return stated

    @classmethod
    def add_battery(
        cls,
        my_sim: Any,
        energy_system_config: EnergySystemConfig,
        energy_manager: Any,
        main_array_config: generic_pv_system.PVSystemConfig,
        my_simulation_parameters: SimulationParameters,
    ) -> None:
        """Add the second battery and its dispatch port when the configuration asks for one.

        Sized from the house's array like the house's battery; a consumer of the recorded file pins
        its own capacity and power.

        Args:
            my_sim: The simulator the setup builds.
            energy_system_config: The configuration, whose ``use_added_battery`` decides.
            energy_manager: The energy manager that dispatches both batteries.
            main_array_config: The house's array, resolved, which the battery is sized from.
            my_simulation_parameters: The run's parameters.
        """
        if not energy_system_config.use_added_battery:
            return
        config = cls.battery_config(cls.ADDED_BATTERY, energy_system_config, main_array_config)
        battery = advanced_battery_bslib.Battery(my_simulation_parameters=my_simulation_parameters, config=config)
        target = energy_manager.add_component_output(
            source_output_name="LoadingPowerInputForBatteryAdded_",
            source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
            source_weight=cls.ADDED_BATTERY_WEIGHT,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for the added battery's control. ",
        )
        battery.connect_dynamic_input(
            input_fieldname=advanced_battery_bslib.Battery.LoadingPowerInput, src_object=target
        )
        my_sim.add_component(battery)

    @classmethod
    def add_energy_manager(cls, my_sim: Any, energy_system_config: EnergySystemConfig, energy_manager: Any) -> None:
        """Add the energy manager, wired by its defaults -- by hand when there are two batteries.

        With one battery the manager connects itself automatically, as every setup always did. With
        two, the same defaults are applied here to every component the setup has added, except that
        the added battery is measured on its own weight, which matches its dispatch port.

        Args:
            my_sim: The simulator the setup builds; every participant is already added.
            energy_system_config: The configuration, whose ``use_added_battery`` decides.
            energy_manager: The energy manager.
        """
        if not energy_system_config.use_added_battery:
            my_sim.add_component(energy_manager, connect_automatically=True)
            return
        for wrapped in my_sim.wrapped_components:
            source = wrapped.my_component
            if source.get_classname() not in energy_manager.dynamic_default_connections:
                continue
            connections: List[Any] = energy_manager.get_dynamic_default_connections(source_component=source)
            if source.component_name == cls.ADDED_BATTERY:
                connections = [
                    dataclasses.replace(connection, source_weight=cls.ADDED_BATTERY_WEIGHT)
                    for connection in connections
                ]
            energy_manager.connect_with_dynamic_connections_list(connections)
        my_sim.add_component(energy_manager)
