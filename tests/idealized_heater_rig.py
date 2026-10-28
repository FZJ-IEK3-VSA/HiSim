"""The controller the building tests put between the building and the idealized electric heater."""

from hisim.components import building, idealized_electric_heater


def controller_between(
    my_building: building.Building, my_heater: idealized_electric_heater.IdealizedElectricHeater
) -> idealized_electric_heater.IdealizedElectricHeaterController:
    """Build the idealized heater's controller and wire it between the building and the heater.

    The controller reads the building's theoretical demand and sets the heater's thermal power to it, so the pair
    delivers exactly what the building needs. The caller adds the returned controller to its simulator.

    Args:
        my_building: The building whose demand the heater covers.
        my_heater: The idealized heater.

    Returns:
        The wired controller.
    """
    controller = idealized_electric_heater.IdealizedElectricHeaterController(
        my_simulation_parameters=my_heater.my_simulation_parameters,
        config=idealized_electric_heater.IdealizedHeaterControllerConfig.preset_standard("IdealizedHeaterController"),
    )
    controller.connect_input(
        controller.TheoreticalThermalBuildingDemand,
        my_building.component_name,
        my_building.TheoreticalThermalBuildingDemand,
    )
    my_heater.connect_input(
        my_heater.ThermalPowerSetInWatt, controller.component_name, controller.ThermalPowerSetInWatt
    )
    return controller


def heat_into(my_building: building.Building, my_heater: idealized_electric_heater.IdealizedElectricHeater) -> None:
    """Wire the heater's heat into the building: the signed heat it computes with, and the two directions it books.

    The building computes with the signed ``ThermalPowerDelivered``; its energy ports book the heating direction on
    ``HeatReceivedInWatt`` and the cooling direction on ``HeatRemovedByCoolingInWatt``, so a run with the heater
    closes the building's energy balance.

    Args:
        my_building: The building the heater heats or cools.
        my_heater: The idealized heater.
    """
    for building_input, heater_output in (
        (my_building.ThermalPowerDelivered, my_heater.ThermalPowerDelivered),
        (my_building.HeatReceivedInWatt, my_heater.HeatingPowerDelivered),
        (my_building.HeatRemovedByCoolingInWatt, my_heater.HeatDrawnForCoolingInWatt),
    ):
        my_building.connect_input(building_input, my_heater.component_name, heater_output)
