""" Iterative Energy Surplus Controller For Districts.

It received the electricity consumption
of all components and the PV production. According to the balance it
sends activation/deactivation signals to components.
The component with the lowest source weight is activated first.
"""

# clean
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, List, Tuple, Union
from collections import OrderedDict
from dataclasses_json import dataclass_json
import pandas as pd
from hisim import log
from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import ComponentInput, ComponentOutput
from hisim.simulationparameters import SimulationParameters
from hisim.postprocessing.kpi_computation.kpi_structure import KpiEntry, KpiTagEnumClass, KpiHelperClass
from hisim.components import (
    more_advanced_heat_pump_hplib,
    advanced_heat_pump_hplib,
    generic_heat_pump_modular,
    loadprofilegenerator_utsp_connector,
)

__authors__ = ""
__copyright__ = ""
__credits__ = [""]
__license__ = ""
__version__ = ""
__maintainer__ = " "
__email__ = ""
__status__ = ""


class EMSControlStrategy(IntEnum):
    """Set Control Strategy of EMS."""

    BUILDING_OPTIMIZEOWNCONSUMPTION_ITERATIV = 1
    BUILDING_OPTIMIZEOWNCONSUMPTION_PARALLEL = 2
    DISTRICT_OPTIMIZECONSUMPTION_PARALLEL = 3


@dataclass_json
@dataclass
class EMSDistrictConfig(cp.ConfigBase):
    """L1 Controller Config."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return L2GenericDistrictEnergyManagementSystem.get_full_classname()

    building_name: str
    #: name of the device
    name: str
    # control strategy, more or less obsolete because only "optimize_own_consumption_interativ" is used at the moment.
    strategy: Union[EMSControlStrategy, int]
    # limit for peak shaving option, more or less obsolete because only "optimize_own_consumption_interativ" is used at the moment.
    limit_to_shave: float
    # increase building set temperatures for heating when PV surplus is available.
    # Must be smaller than difference of set_heating_temperature and set_cooling_temperature
    building_indoor_temperature_offset_value: float
    # increase in dhw buffer set temperatures when PV surplus is available for heating
    domestic_hot_water_storage_temperature_offset_value: float
    # increase in SimpleHotWaterStorage set temperatures when PV surplus is available for heating
    space_heating_water_storage_temperature_offset_value: float

    @classmethod
    def get_default_config_ems(
        cls,
        strategy: Union[EMSControlStrategy, int] = EMSControlStrategy.BUILDING_OPTIMIZEOWNCONSUMPTION_ITERATIV,
        building_name: str = "BUI1",
    ) -> "EMSDistrictConfig":
        """Default Config for Energy Management System."""
        config = EMSDistrictConfig(
            building_name=building_name,
            name="L2EMSElectricityController",
            strategy=strategy,
            limit_to_shave=0,
            building_indoor_temperature_offset_value=2,
            domestic_hot_water_storage_temperature_offset_value=10,
            space_heating_water_storage_temperature_offset_value=10,
        )
        return config


class EMSDistrictState:
    """Saves the state of the Energy Management System."""

    def __init__(
        self,
        production: float,
        consumption_uncontrolled: float,
        consumption_ems_controlled: float,
    ) -> None:
        """Initialize the heat pump controller state."""
        self.production_in_watt = production
        self.consumption_uncontrolled_in_watt = consumption_uncontrolled
        self.consumption_ems_controlled_in_watt = consumption_ems_controlled

    def clone(self) -> "EMSDistrictState":
        """Copy EMSState efficiently."""
        return EMSDistrictState(
            production=self.production_in_watt,
            consumption_uncontrolled=self.consumption_uncontrolled_in_watt,
            consumption_ems_controlled=self.consumption_ems_controlled_in_watt,
        )


class L2GenericDistrictEnergyManagementSystem(dynamic_component.DynamicComponent):
    """Surplus electricity controller - time step based.

    Iteratively goes through connected inputs by hierachy of
    source weights of inputs and passes available surplus
    electricity to each device. Needs to be configured with
    dynamic In- and Outputs.

    Recognises production of any component when dynamic input
    is labeled with the flag "CONSUMPTION" and the
    related source weight is set to 999.

    Recognised non controllable consumption of any component
    when dynamic input is labeld with the flag
    "ELECTRICITY_CONSUMPTION_UNCONTROLLED" and the related
    source weight is set to 999.

    For each component, which should receive signals from the
    EMS, the EMS needs to be connected with one dynamic input
    with the tag "ELECTRICITY_CONSUMPTION_EMS_CONTROLLED" and
    the source weight of the related component. This signal
    reflects the real consumption/production of the device,
    which is needed to update the energy balance in the EMS.
    In addition, the EMS needs to be connected with one dynamic
    output with the tag "ELECTRICITY_TARGET" with the
    source weight of the related component. This signal sends
    information on the available surplus electricity to the
    component, which receives signals from the EMS.

    """

    # Inputs
    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"
    SurplusUnusedFromDistrictEMS = "SurplusUnusedFromDistrictEMS"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"

    TotalElectricityToOrFromGrid = "TotalElectricityToOrFromGrid"
    TotalElectricityToGrid = "TotalElectricityToGrid"
    TotalElectricityFromGrid = "TotalElectricityFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    ElectricityProduction = "ElectricityProduction"
    TotalElectricityConsumptionEMSControlled = "TotalElectricityConsumptionEMSControlled"
    TotalElectricityConsumptionUnControlled = "TotalElectricityConsumptionUnControlled"
    BuildingIndoorTemperatureModifier = "BuildingIndoorTemperatureModifier"  # connect to HDS controller and Building
    DomesticHotWaterStorageTemperatureModifier = (
        "DomesticHotWaterStorageTemperatureModifier"  # used for L1HeatPumpController  # Todo: change name?
    )
    SpaceHeatingWaterStorageTemperatureModifier = (
        "SpaceHeatingWaterStorageTemperatureModifier"  # used for HeatPumpHplibController
    )
    SurplusUnusedFromDistrictEMSOutput = "SurplusUnusedFromDistrictEMSOutput"
    SurplusUnusedFromBuildingEMSOutput = "SurplusUnusedFromBuildingEMSOutput"
    ElectricityConsumptionOfBuildingsInWatt = "ElectricityConsumptionOfBuildingsInWatt"

    CheckPeakShaving = "CheckPeakShaving"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: EMSDistrictConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ):
        """Initializes."""
        self.my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
        self.my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
        self.ems_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.state = EMSDistrictState(production=0, consumption_uncontrolled=0, consumption_ems_controlled=0)
        self.previous_state = self.state.clone()

        self.component_types_sorted: List[lt.ComponentType] = []
        self.inputs_sorted: List[ComponentInput] = []
        self.source_weights_sorted: List[int] = []
        self.outputs_sorted: List[ComponentOutput] = []
        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        self.consumption_ems_controlled_inputs: List[ComponentInput] = []
        self.components_parameters: dict = {}
        self.component_and_number_of_same_source_weights: dict = {}

        self.mode: Any
        self.strategy = self.ems_config.strategy
        self.limit_to_shave = self.ems_config.limit_to_shave
        self.building_indoor_temperature_offset_value = self.ems_config.building_indoor_temperature_offset_value
        self.domestic_hot_water_storage_temperature_offset_value = (
            self.ems_config.domestic_hot_water_storage_temperature_offset_value
        )
        self.space_heating_water_storage_temperature_offset_value = (
            self.ems_config.space_heating_water_storage_temperature_offset_value
        )

        # Inputs
        self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityToElectrolyzerUnused,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

        self.surplus_electricity_unused_to_building_ems_from_district_ems: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.SurplusUnusedFromDistrictEMS,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

        # Outputs
        self.total_electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityToOrFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityToOrFromGrid} will follow.",
        )

        self.total_electricity_to_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityToGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityToGrid} will follow.",
        )

        self.total_electricity_from_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityFromGrid} will follow.",
        )

        self.total_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumption} will follow.",
            postprocessing_flag=(
                [
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                    lt.ComponentType.BUILDINGS,
                    lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                ]
                if any(word in config.building_name for word in lt.DistrictNames)
                else []
            ),
        )

        self.total_electricity_consumption_ems_controlled_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumptionEMSControlled,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumptionEMSControlled} will follow.",
        )

        self.electricity_production_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityProduction,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityProduction} will follow.",
        )

        self.total_electricity_consumption_uncontrolled_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumptionUnControlled,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumptionUnControlled} will follow.",
        )

        self.building_indoor_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BuildingIndoorTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.BuildingIndoorTemperatureModifier} will follow.",
        )

        self.domestic_hot_water_storage_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.DomesticHotWaterStorageTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.DomesticHotWaterStorageTemperatureModifier} will follow.",
        )

        self.space_heating_water_storage_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.SpaceHeatingWaterStorageTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.SpaceHeatingWaterStorageTemperatureModifier} will follow.",
        )

        self.check_peak_shaving: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CheckPeakShaving,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CheckPeakShaving} will follow.",
        )

        self.surplus_electricity_unused_to_building_ems_from_district_ems_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.SurplusUnusedFromDistrictEMSOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.SurplusUnusedFromDistrictEMSOutput} will follow.",
        )
        if any(word in config.building_name for word in lt.DistrictNames):
            self.surplus_electricity_unused_to_district_ems_from_building_ems_output: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.SurplusUnusedFromBuildingEMSOutput,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                sankey_flow_direction=False,
                output_description=f"here a description for {self.SurplusUnusedFromBuildingEMSOutput} will follow.",
                postprocessing_flag=(
                    [
                        lt.InandOutputType.ELECTRICITY_PRODUCTION,
                        lt.ComponentType.BUILDINGS,
                        lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                    ]
                    if any(word in config.building_name for word in lt.DistrictNames)
                    else []
                ),
            )
            self.electricity_consumption_building_uncontrolled_in_watt_channel: cp.ComponentOutput = self.add_output(
                object_name=self.component_name,
                field_name=self.ElectricityConsumptionOfBuildingsInWatt,
                load_type=lt.LoadTypes.ELECTRICITY,
                unit=lt.Units.WATT,
                sankey_flow_direction=False,
                output_description=f"here a description for {self.ElectricityConsumptionOfBuildingsInWatt} will follow.",
                postprocessing_flag=(
                    [
                        lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED,
                        lt.ComponentType.BUILDINGS,
                        lt.OutputPostprocessingRules.DISPLAY_IN_WEBTOOL,
                    ]
                    if any(word in config.building_name for word in lt.DistrictNames)
                    else []
                ),
            )

        self.add_dynamic_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_dynamic_default_connections(self.get_default_connections_from_pv_system())
        #  self.add_dynamic_default_connections(self.get_default_connections_from_more_advanced_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_dhw_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_advanced_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_advanced_battery())

    def get_default_connections_from_pv_system(
        self,
    ):
        """Get pv system default connections."""

        from hisim.components.generic_pv_system import PVSystem  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        pv_class_name = PVSystem.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=PVSystem,
                source_class_name=pv_class_name,
                source_component_field_name=PVSystem.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.PV,
                    lt.InandOutputType.ELECTRICITY_PRODUCTION,
                ],
                source_weight=999,
            )
        )

        return dynamic_connections

    def get_default_connections_from_utsp_occupancy(
        self,
    ):
        """Get utsp occupancy default connections."""

        from hisim.components.loadprofilegenerator_utsp_connector import (  # pylint: disable=import-outside-toplevel
            UtspLpgConnector,
        )

        dynamic_connections = []
        self.occupancy_class_name = UtspLpgConnector.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=UtspLpgConnector,
                source_class_name=self.occupancy_class_name,
                source_component_field_name=UtspLpgConnector.ElectricalPowerConsumption,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.RESIDENTS, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=1,
            )
        )
        self.add_component_output(
            source_output_name=f"ElectricityToOrFromGridOf{self.occupancy_class_name}_",
            source_tags=[
                lt.ComponentType.RESIDENTS,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=1,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for Occupancy. ",
        )
        return dynamic_connections

    # def get_default_connections_from_more_advanced_heat_pump(
    #         self,
    # ):
    #     """Get advanced heat pump default connections."""
    #
    #     from hisim.components.more_advanced_heat_pump_hplib import (  # pylint: disable=import-outside-toplevel
    #         MoreAdvancedHeatPumpHPLib,
    #     )
    #
    #     dynamic_connections = []
    #     self.more_advanced_heat_pump_class_name = MoreAdvancedHeatPumpHPLib.get_classname()
    #     dynamic_connections.append(
    #         dynamic_component.DynamicComponentConnection(
    #             source_component_class=MoreAdvancedHeatPumpHPLib,
    #             source_class_name=self.more_advanced_heat_pump_class_name,
    #             source_component_field_name=MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
    #             source_load_type=lt.LoadTypes.ELECTRICITY,
    #             source_unit=lt.Units.WATT,
    #             source_tags=[
    #                 lt.ComponentType.HEAT_PUMP_BUILDING,
    #                 lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
    #             ],
    #             source_weight=2,
    #         )
    #     )
    #     dynamic_connections.append(
    #         dynamic_component.DynamicComponentConnection(
    #             source_component_class=MoreAdvancedHeatPumpHPLib,
    #             source_class_name=self.more_advanced_heat_pump_class_name,
    #             source_component_field_name=MoreAdvancedHeatPumpHPLib.ElectricalInputPowerDHW,
    #             source_load_type=lt.LoadTypes.ELECTRICITY,
    #             source_unit=lt.Units.WATT,
    #             source_tags=[
    #                 lt.ComponentType.HEAT_PUMP_DHW,
    #                 lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
    #             ],
    #             source_weight=3,
    #         )
    #     )
    #     self.add_component_output(
    #         source_output_name=f"ElectricityToOrFromGridOfSH{self.more_advanced_heat_pump_class_name}_",
    #         source_tags=[
    #             lt.ComponentType.HEAT_PUMP_BUILDING,
    #             lt.InandOutputType.ELECTRICITY_TARGET,
    #         ],
    #         source_weight=2,
    #         source_load_type=lt.LoadTypes.ELECTRICITY,
    #         source_unit=lt.Units.WATT,
    #         output_description="Target electricity for Heating Heat Pump. ",
    #     )
    #     self.add_component_output(
    #         source_output_name=f"ElectricityToOrFromGridOfDHW{self.more_advanced_heat_pump_class_name}_",
    #         source_tags=[
    #             lt.ComponentType.HEAT_PUMP_DHW,
    #             lt.InandOutputType.ELECTRICITY_TARGET,
    #         ],
    #         source_weight=3,
    #         source_load_type=lt.LoadTypes.ELECTRICITY,
    #         source_unit=lt.Units.WATT,
    #         output_description="Target electricity for Heating Heat Pump. ",
    #     )
    #     return dynamic_connections

    def get_default_connections_from_advanced_heat_pump(
        self,
    ):
        """Get advanced heat pump default connections."""

        from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        self.advanced_heat_pump_class_name = HeatPumpHplib.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=HeatPumpHplib,
                source_class_name=self.advanced_heat_pump_class_name,
                source_component_field_name=HeatPumpHplib.ElectricalInputPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[
                    lt.ComponentType.HEAT_PUMP_BUILDING,
                    lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,
                ],
                source_weight=2,
            )
        )
        self.add_component_output(
            source_output_name=f"ElectricityToOrFromGridOf{self.advanced_heat_pump_class_name}_",
            source_tags=[
                lt.ComponentType.HEAT_PUMP_BUILDING,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=2,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for Heating Heat Pump. ",
        )
        return dynamic_connections

    def get_default_connections_from_dhw_heat_pump(
        self,
    ):
        """Get dhw heat pump default connections."""

        from hisim.components.generic_heat_pump_modular import (  # pylint: disable=import-outside-toplevel
            ModularHeatPump,
        )

        dynamic_connections = []
        self.dhw_heat_pump_class_name = ModularHeatPump.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=ModularHeatPump,
                source_class_name=self.dhw_heat_pump_class_name,
                source_component_field_name=ModularHeatPump.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=3,
            )
        )

        self.add_component_output(
            source_output_name=f"ElectricityToOrFromGridOf{self.dhw_heat_pump_class_name}_",
            source_tags=[
                lt.ComponentType.HEAT_PUMP_DHW,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            source_weight=3,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for dhw heat pump.",
        )
        return dynamic_connections

    def get_default_connections_from_advanced_battery(
        self,
    ):
        """Get advanced battery default connections."""

        from hisim.components.advanced_battery_bslib import Battery  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        advanced_battery_class_name = Battery.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=Battery,
                source_class_name=advanced_battery_class_name,
                source_component_field_name=Battery.AcBatteryPowerUsed,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED],
                source_weight=4,
            )
        )

        return dynamic_connections

    def sort_source_weights_and_components(
        self,
    ) -> Tuple[
        List[int],
        List[ComponentInput],
        List[lt.ComponentType],
        List[ComponentOutput],
        List[ComponentInput],
        List[ComponentInput],
        List[ComponentInput],
    ]:
        """Sorts dynamic Inputs and Outputs according to source weights."""
        inputs = [elem for elem in self.my_component_inputs if elem.source_weight != 999]

        source_tags = [elem.source_tags[0] for elem in inputs]
        source_weights = [elem.source_weight for elem in inputs]
        sortindex = sorted(range(len(source_weights)), key=lambda k: source_weights[k])
        source_weights = [source_weights[i] for i in sortindex]

        component_types_sorted = [source_tags[i] for i in sortindex]
        inputs_sorted = [getattr(self, inputs[i].source_component_class) for i in sortindex]
        outputs_sorted = []

        for ind, source_weight in enumerate(source_weights):
            outputs = self.get_all_dynamic_outputs(
                tags=[
                    component_types_sorted[ind],
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                weight_counter=source_weight,
            )

            for output in outputs:
                if output is not None:
                    outputs_sorted.append(output)
                else:
                    raise Exception("Dynamic input is not conncted to dynamic output")
        outputs_sorted = list(OrderedDict.fromkeys(outputs_sorted))
        production_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION])
        consumption_uncontrolled_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        )
        consumption_ems_controlled_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED]
        )
        return (
            source_weights,
            inputs_sorted,
            component_types_sorted,
            outputs_sorted,
            production_inputs,
            consumption_uncontrolled_inputs,
            consumption_ems_controlled_inputs,
        )

    def write_to_report(self):
        """Writes relevant information to report."""
        return self.ems_config.get_string_dict()

    def i_save_state(self) -> None:
        """Saves the state."""
        # abändern, siehe Storage
        self.previous_state = self.state

    def i_restore_state(self) -> None:
        """Restores the state."""
        self.state = self.previous_state

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """Doublechecks values."""
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates iteration of surplus controller."""
        if timestep == 0:
            (
                self.source_weights_sorted,
                self.inputs_sorted,
                self.component_types_sorted,
                self.outputs_sorted,
                self.production_inputs,
                self.consumption_uncontrolled_inputs,
                self.consumption_ems_controlled_inputs,
            ) = self.sort_source_weights_and_components()

            self.components_parameters = {}
            self.component_and_number_of_same_source_weights = {}

            for i, source_object_name in enumerate(self.inputs_sorted):
                self.components_parameters[source_object_name.field_name] = {
                    "input": self.inputs_sorted[i],
                    "output": self.outputs_sorted[i],
                    "component_type": self.component_types_sorted[i],
                    "weight": self.source_weights_sorted[i],
                }

            for params in self.components_parameters.values():
                weight = params["weight"]
                field_name = params["input"].field_name

                if weight in self.component_and_number_of_same_source_weights:
                    self.component_and_number_of_same_source_weights[weight]["count"] += 1
                    self.component_and_number_of_same_source_weights[weight]["components"].append(field_name)
                else:
                    self.component_and_number_of_same_source_weights[weight] = {
                        "count": 1,
                        "components": [field_name],
                    }

        district_electricity_surplus_unused = max(
            0.0, stsv.get_input_value(component_input=self.surplus_electricity_unused_to_building_ems_from_district_ems)
        )

        stsv.set_output_value(
            self.surplus_electricity_unused_to_building_ems_from_district_ems_output,
            district_electricity_surplus_unused,
        )

        # get total production and consumptions
        self.state.production_in_watt = (
            sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
            + district_electricity_surplus_unused
        )
        self.state.consumption_uncontrolled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )
        self.state.consumption_ems_controlled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_ems_controlled_inputs]
        )

        if any(word in self.config.building_name for word in lt.DistrictNames):
            production_inputs_from_buildings = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION,
                                                                             lt.ComponentType.BUILDINGS])

            building_electricity_surplus_unused = (
                sum([stsv.get_input_value(component_input=elem) for elem in production_inputs_from_buildings]))

            stsv.set_output_value(
                self.surplus_electricity_unused_to_district_ems_from_building_ems_output,
                building_electricity_surplus_unused,
            )

            consumption_inputs_building = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED, lt.ComponentType.BUILDINGS])

            consumption_of_buildings = (
                sum([stsv.get_input_value(component_input=elem) for elem in consumption_inputs_building]))

            stsv.set_output_value(
                self.electricity_consumption_building_uncontrolled_in_watt_channel,
                consumption_of_buildings,
            )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        available_surplus_electricity_in_watt = (
            self.state.production_in_watt - self.state.consumption_uncontrolled_in_watt
        )

        if self.strategy == EMSControlStrategy.BUILDING_OPTIMIZEOWNCONSUMPTION_ITERATIV:
            available_surplus_electricity_in_watt = (
                self.distribute_available_surplus_electricity_to_building_components_iterative(
                    available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                    stsv=stsv,
                    inputs_sorted=self.inputs_sorted,
                    component_types_sorted=self.component_types_sorted,
                    outputs_sorted=self.outputs_sorted,
                )
            )
            self.modify_set_temperatures_for_building_components_in_case_of_surplus_electricity_iterativ(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                inputs_sorted=self.inputs_sorted,
                component_types_sorted=self.component_types_sorted,
            )

        if self.strategy == EMSControlStrategy.BUILDING_OPTIMIZEOWNCONSUMPTION_PARALLEL:
            available_surplus_electricity_in_watt = (
                self.distribute_available_surplus_electricity_to_building_components_parallel(
                    components_parameters=self.components_parameters,
                    component_and_number_of_same_source_weights=self.component_and_number_of_same_source_weights,
                    available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                    stsv=stsv,
                )
            )

        if self.strategy == EMSControlStrategy.DISTRICT_OPTIMIZECONSUMPTION_PARALLEL:
            available_surplus_electricity_in_watt = (
                self.distribute_available_surplus_electricity_to_building_components_parallel(
                    components_parameters=self.components_parameters,
                    component_and_number_of_same_source_weights=self.component_and_number_of_same_source_weights,
                    available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                    stsv=stsv,
                )
            )

        stsv.set_output_value(self.total_electricity_to_or_from_grid, available_surplus_electricity_in_watt)
        stsv.set_output_value(
            self.total_electricity_to_grid,
            available_surplus_electricity_in_watt if available_surplus_electricity_in_watt > 0 else 0,
        )
        stsv.set_output_value(
            self.total_electricity_from_grid,
            -available_surplus_electricity_in_watt if available_surplus_electricity_in_watt < 0 else 0,
        )
        stsv.set_output_value(
            self.total_electricity_consumption_channel,
            self.state.consumption_uncontrolled_in_watt + self.state.consumption_ems_controlled_in_watt,
        )

        stsv.set_output_value(self.electricity_production_channel, self.state.production_in_watt)
        stsv.set_output_value(
            self.total_electricity_consumption_uncontrolled_channel, self.state.consumption_uncontrolled_in_watt
        )
        stsv.set_output_value(
            self.total_electricity_consumption_ems_controlled_channel, self.state.consumption_ems_controlled_in_watt
        )
        """
        elif self.strategy == "seasonal_storage":
            self.seasonal_storage(delta_demand=delta_demand, stsv=stsv)
        elif self.strategy == "peak_shaving_into_grid":
            self.peak_shaving_into_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        elif self.strategy == "peak_shaving_from_grid":
            self.peak_shaving_from_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        """

        # HEAT #
        # If comftortable temperature of building is to low heat with WarmWaterStorage the building
        # Solution with Control Signal Residence
        # not perfect solution!
        """
        if self.temperature_residence<self.min_comfortable_temperature_residence:
            #heat
            #here has to be added how "strong" HeatingWater Storage can be discharged
            #Working with upper boarder?
        elif self.temperature_residence > self.max_comfortable_temperature_residence:
            #cool
        elif self.temperature_residence>self.min_comfortable_temperature_residence and self.temperature_residence<self.max_comfortable_temperature_residence:
        """

    def distribute_available_surplus_electricity_to_building_components_iterative(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        inputs_sorted: List[ComponentInput],
        component_types_sorted: List[lt.ComponentType],
        outputs_sorted: List[ComponentOutput],
    ) -> float:
        """Evaluates available surplus electricity component by component, iteratively, and sends updated signals back."""

        for index, single_input_sorted in enumerate(inputs_sorted):
            single_component_type_sorted = component_types_sorted[index]
            single_output_sorted = outputs_sorted[index]

            available_surplus_electricity_in_watt = self.control_electricity_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                current_component_type=single_component_type_sorted,
                current_input=single_input_sorted,
                current_output=single_output_sorted,
            )

        return available_surplus_electricity_in_watt

    def control_electricity_iterative(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        current_component_type: lt.ComponentType,
        current_input: cp.ComponentInput,
        current_output: cp.ComponentOutput,
    ) -> float:
        """Calculates available surplus electricity.

        Subtracts the electricity consumption signal of the component from the previous iteration,
        and sends updated signal back.
        This function controls how surplus electricity is distributed and how much of each components'
        electricity need is covered onsite or from grid.
        """
        # get electricity demand from input component and substract from (or add to) available surplus electricity
        electricity_demand_from_current_input_component_in_watt = stsv.get_input_value(component_input=current_input)

        # if available_surplus_electricity > 0: electricity is fed into battery
        # if available_surplus_electricity < 0: electricity is taken from battery
        if current_component_type == lt.ComponentType.BATTERY:
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # difference between what is fed into battery and what battery really used
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
            )

        # Building Tag, if district controller manage the hp in building --> contracting
        # Building has own ems which controll the temperatures of storages
        # District EMS controll how surplus is devided to buildings(ems)
        elif current_component_type == lt.ComponentType.SURPLUS_CONTROLLER_DISTRICT:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity CONSUMERS
        elif current_component_type in [
            lt.ComponentType.RESIDENTS,
            lt.ComponentType.BUILDINGS,
            lt.ComponentType.ELECTROLYZER,
            lt.ComponentType.SMART_DEVICE,
            lt.ComponentType.CAR_BATTERY,
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
            lt.ComponentType.HEAT_PUMP_BUILDING,
        ]:
            # if surplus electricity is available, a part of the component's consumption can be covered onsite
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # otherwise all of the component's consumption is taken from grid
            else:
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity PRODUCERS
        elif current_component_type == lt.ComponentType.CHP:
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
            )
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

        return available_surplus_electricity_in_watt

    def modify_set_temperatures_for_building_components_in_case_of_surplus_electricity_iterativ(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        inputs_sorted: List[ComponentInput],
        component_types_sorted: List[lt.ComponentType],
    ) -> None:
        """In case surplus electricity is available, modify set temperatures for space heating and domestic hot water heat pumps.

        Like this, the heat pumps will start heating up the water storages and the surplus energy can be stored as thermal energy.
        See also SG-ready heatpumps: https://de.gridx.ai/wissen/sg-ready.

        The temperature modification outputs go to the heat pumps, the heat distribution system and the building component (see network charts).
        """
        for index in range(len(inputs_sorted)):
            current_component_type = component_types_sorted[index]

            if current_component_type == lt.ComponentType.HEAT_PUMP_BUILDING:
                if available_surplus_electricity_in_watt > 0:
                    stsv.set_output_value(
                        self.building_indoor_temperature_modifier,
                        self.building_indoor_temperature_offset_value,
                    )
                    stsv.set_output_value(
                        self.space_heating_water_storage_temperature_modifier,
                        self.space_heating_water_storage_temperature_offset_value,
                    )
                else:
                    stsv.set_output_value(self.building_indoor_temperature_modifier, 0)
                    stsv.set_output_value(self.space_heating_water_storage_temperature_modifier, 0)

            elif current_component_type in [
                lt.ComponentType.HEAT_PUMP_DHW,
                lt.ComponentType.HEAT_PUMP,
            ]:
                if available_surplus_electricity_in_watt > 0:
                    stsv.set_output_value(
                        self.domestic_hot_water_storage_temperature_modifier,
                        self.domestic_hot_water_storage_temperature_offset_value,
                    )
                else:
                    stsv.set_output_value(self.domestic_hot_water_storage_temperature_modifier, 0)

    def distribute_available_surplus_electricity_to_building_components_parallel(
        self,
        components_parameters: dict,
        component_and_number_of_same_source_weights: dict,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
    ) -> float:
        """Evaluates available surplus electricity component by component.

        - First step: actual electricity demand of each component is subtracted from surplus
        - second step: available surplus is distributed to components iterative if same source weight

        Parallel distribution of surplus when components have equal source weight.
        To do this, the surplus is divided by the number of components with equal source weight so that each component
        has the chance to receive an equal share. If a component requires less, the rest is returned to available surplus.
        """

        component_electricity_demand: dict = {}

        for element in components_parameters:
            current_electricity_demand = stsv.get_input_value(component_input=components_parameters[element]["input"])
            if components_parameters[element]["component_type"] == "Battery":
                component_electricity_demand[element] = float(current_electricity_demand)
            else:
                component_electricity_demand[element] = -1 * float(abs(current_electricity_demand))

        surplus_next_iteration = 0.0
        additional_electricity_demand = 0.0

        for single_source_weight_sorted in list(component_and_number_of_same_source_weights.keys()):

            available_surplus_electricity_in_watt_next_component = (
                available_surplus_electricity_in_watt + additional_electricity_demand + surplus_next_iteration
            )

            components_with_current_source_weight = component_and_number_of_same_source_weights[
                single_source_weight_sorted
            ]["components"]
            number_of_components_with_current_source_weight = component_and_number_of_same_source_weights[
                single_source_weight_sorted
            ]["count"]

            if number_of_components_with_current_source_weight > 1:

                repeat_loop = True
                repeat_count = 0

                while repeat_loop:
                    repeat_loop = False
                    surplus_next_iteration = 0.0
                    additional_electricity_demand = 0.0
                    counter_inner_interation = 0

                    number_of_components_with_electricity_demand_and_same_source_weight = sum(
                        1
                        for key, wert in component_electricity_demand.items()
                        if wert < 0 and key in components_with_current_source_weight
                    )

                    for single_component in components_with_current_source_weight:
                        single_output_sorted = components_parameters[single_component]["output"]

                        electricity_demand_from_current_input_component_in_watt = component_electricity_demand[
                            single_component
                        ]

                        if repeat_count > 0 and component_electricity_demand[single_component] >= 0:
                            continue

                        if counter_inner_interation == 0:
                            if number_of_components_with_electricity_demand_and_same_source_weight == 0:
                                available_surplus_electricity_in_watt_split = (
                                    available_surplus_electricity_in_watt_next_component
                                    / number_of_components_with_current_source_weight
                                )
                                available_surplus_electricity_in_watt_next_component = (
                                    available_surplus_electricity_in_watt_split
                                )
                            elif number_of_components_with_electricity_demand_and_same_source_weight == 1:
                                available_surplus_electricity_in_watt_split = (
                                    available_surplus_electricity_in_watt_next_component
                                )
                                available_surplus_electricity_in_watt_next_component = 0
                            else:
                                available_surplus_electricity_in_watt_split = (
                                    available_surplus_electricity_in_watt_next_component
                                    / number_of_components_with_electricity_demand_and_same_source_weight
                                )
                                available_surplus_electricity_in_watt_next_component = (
                                    available_surplus_electricity_in_watt_split
                                )
                        else:
                            available_surplus_electricity_in_watt_split = (
                                available_surplus_electricity_in_watt_next_component
                            )

                        available_surplus_electricity_in_watt = self.control_electricity_and_modify_set_temperatures_for_component_parallel(
                            available_surplus_electricity_in_watt=available_surplus_electricity_in_watt_split,
                            stsv=stsv,
                            current_component_type=components_parameters[single_component]["component_type"],
                            current_output=single_output_sorted,
                            electricity_demand_from_current_input_component_in_watt=electricity_demand_from_current_input_component_in_watt,
                        )

                        component_electricity_demand[single_component] = available_surplus_electricity_in_watt

                        if available_surplus_electricity_in_watt > 0:
                            surplus_next_iteration += available_surplus_electricity_in_watt
                            available_surplus_electricity_in_watt = 0
                        else:
                            additional_electricity_demand += available_surplus_electricity_in_watt
                            available_surplus_electricity_in_watt = 0

                        counter_inner_interation += 1

                    if (
                        surplus_next_iteration > 0 > additional_electricity_demand
                        and counter_inner_interation
                        >= number_of_components_with_electricity_demand_and_same_source_weight
                    ):
                        available_surplus_electricity_in_watt_next_component = surplus_next_iteration
                        repeat_count += 1
                        repeat_loop = True

            else:
                surplus_next_iteration = 0.0
                additional_electricity_demand = 0.0
                for single_component in components_with_current_source_weight:

                    single_output_sorted = components_parameters[single_component]["output"]

                    electricity_demand_from_current_input_component_in_watt = component_electricity_demand[
                        single_component
                    ]

                    available_surplus_electricity_in_watt = self.control_electricity_and_modify_set_temperatures_for_component_parallel(
                        available_surplus_electricity_in_watt=available_surplus_electricity_in_watt_next_component,
                        stsv=stsv,
                        current_component_type=components_parameters[single_component]["component_type"],
                        current_output=single_output_sorted,
                        electricity_demand_from_current_input_component_in_watt=electricity_demand_from_current_input_component_in_watt,
                    )

                    component_electricity_demand[single_component] = available_surplus_electricity_in_watt

                    if available_surplus_electricity_in_watt > 0:
                        surplus_next_iteration += available_surplus_electricity_in_watt
                        available_surplus_electricity_in_watt = 0
                    else:
                        additional_electricity_demand += available_surplus_electricity_in_watt
                        available_surplus_electricity_in_watt = 0

        available_surplus_electricity_in_watt = additional_electricity_demand + surplus_next_iteration

        return available_surplus_electricity_in_watt

    def control_electricity_parallel(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        current_component_type: lt.ComponentType,
        current_output: cp.ComponentOutput,
        electricity_demand_from_current_input_component_in_watt: float,
    ) -> float:
        """Calculates available surplus electricity.

        Subtracts the electricity consumption signal of the component from the previous iteration,
        and sends updated signal back.
        This function controls how surplus electricity is distributed and how much of each components'
        electricity need is covered onsite or from grid.
        Electricity input of each component is saved in dict to make iteration possible when parallel distribution is used.
        """

        # if available_surplus_electricity > 0: electricity is fed into battery
        # if available_surplus_electricity < 0: electricity is taken from battery
        if current_component_type == lt.ComponentType.BATTERY:
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # difference between what is fed into battery and what battery really used
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
            )

        # Building Tag, if district controller manage the hp in building --> contracting
        # Building has own ems which controll the temperatures of storages
        # District EMS controll how surplus is devided to buildings(ems)
        elif current_component_type == lt.ComponentType.SURPLUS_CONTROLLER_DISTRICT:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity CONSUMERS
        elif current_component_type in [
            lt.ComponentType.RESIDENTS,
            lt.ComponentType.BUILDINGS,
            lt.ComponentType.ELECTROLYZER,
            lt.ComponentType.SMART_DEVICE,
            lt.ComponentType.CAR_BATTERY,
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
            lt.ComponentType.HEAT_PUMP_BUILDING,
        ]:

            # if surplus electricity is available, a part of the component's consumption can be covered onsite
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # otherwise all of the component's consumption is taken from grid
            else:
                stsv.set_output_value(
                    output=current_output, value=electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity PRODUCERS
        elif current_component_type == lt.ComponentType.CHP:
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
            )
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

        return available_surplus_electricity_in_watt

    def control_electricity_and_modify_set_temperatures_for_component_parallel(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        current_component_type: lt.ComponentType,
        current_output: cp.ComponentOutput,
        electricity_demand_from_current_input_component_in_watt: float,
    ) -> float:
        """Calculates available surplus electricity.

        Subtracts the electricity consumption signal of the component from the previous iteration,
        and sends updated signal back.
        This function controls how surplus electricity is distributed and how much of each components'
        electricity need is covered onsite or from grid.
        Electricity input if each component is saved in dict to make iteration possible when parallel distribution is used.
        """

        # if available_surplus_electricity > 0: electricity is fed into battery
        # if available_surplus_electricity < 0: electricity is taken from battery
        if current_component_type == lt.ComponentType.BATTERY:
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # difference between what is fed into battery and what battery really used
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
            )

        # Building Tag, if district controller manage the hp in building --> contracting
        # Building has own ems which controll the temperatures of storages
        # District EMS controll how surplus is devided to buildings(ems)
        elif current_component_type == lt.ComponentType.SURPLUS_CONTROLLER_DISTRICT:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity CONSUMERS
        elif current_component_type in [
            lt.ComponentType.RESIDENTS,
            lt.ComponentType.BUILDINGS,
            lt.ComponentType.ELECTROLYZER,
            lt.ComponentType.SMART_DEVICE,
            lt.ComponentType.CAR_BATTERY,
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
            lt.ComponentType.HEAT_PUMP_BUILDING,
        ]:

            # if surplus electricity is available, a part of the component's consumption can be covered onsite
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # otherwise all of the component's consumption is taken from grid
            else:
                stsv.set_output_value(
                    output=current_output, value=electricity_demand_from_current_input_component_in_watt
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
                )

        # these are electricity PRODUCERS
        elif current_component_type == lt.ComponentType.CHP:
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
            )
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

        # modify set temperatures directly to use surplus in right order of source weights
        if current_component_type == lt.ComponentType.HEAT_PUMP_BUILDING:
            if available_surplus_electricity_in_watt > 0:
                stsv.set_output_value(
                    self.building_indoor_temperature_modifier,
                    self.building_indoor_temperature_offset_value,
                )
                stsv.set_output_value(
                    self.space_heating_water_storage_temperature_modifier,
                    self.space_heating_water_storage_temperature_offset_value,
                )
            else:
                stsv.set_output_value(self.building_indoor_temperature_modifier, 0)
                stsv.set_output_value(self.space_heating_water_storage_temperature_modifier, 0)

        elif current_component_type in [
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
        ]:
            if available_surplus_electricity_in_watt > 0:
                stsv.set_output_value(
                    self.domestic_hot_water_storage_temperature_modifier,
                    self.domestic_hot_water_storage_temperature_offset_value,
                )
            else:
                stsv.set_output_value(self.domestic_hot_water_storage_temperature_modifier, 0)

        return available_surplus_electricity_in_watt

    def get_component_kpi_entries(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> List[KpiEntry]:
        """Calculates KPIs for the respective component and return all KPI entries as list."""

        advanced_heat_pump_class_name = advanced_heat_pump_hplib.HeatPumpHplib.get_classname()
        more_advanced_heat_pump_class_name = more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.get_classname()
        dhw_heat_pump_class_name = generic_heat_pump_modular.ModularHeatPump.get_classname()
        occupancy_class_name = loadprofilegenerator_utsp_connector.UtspLpgConnector.get_classname()

        list_of_kpi_entries: List[KpiEntry] = []
        for index, output in enumerate(all_outputs):
            if output.component_name == self.component_name:
                if dhw_heat_pump_class_name in output.field_name:
                    dhw_hp_electricity_from_grid_in_watt_series = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] < 0.0
                    ]
                    dhw_heatpump_electricity_from_grid_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=dhw_hp_electricity_from_grid_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    dhw_heatpump_electricity_from_grid_entry = KpiEntry(
                        name=f"Domestic hot water heat pump electricity from grid {self.component_name}",
                        unit="kWh",
                        value=dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(dhw_heatpump_electricity_from_grid_entry)
                elif more_advanced_heat_pump_class_name in output.field_name:
                    if "SH" in str(output.field_name):
                        sh_electricity_from_grid_in_watt_series = postprocessing_results.iloc[:, index].loc[
                            postprocessing_results.iloc[:, index] < 0.0
                        ]
                        sh_heatpump_electricity_from_grid_in_kilowatt_hour = abs(
                            KpiHelperClass.compute_total_energy_from_power_timeseries(
                                power_timeseries_in_watt=sh_electricity_from_grid_in_watt_series,
                                timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                            )
                        )
                        # make kpi entry
                        sh_heatpump_electricity_from_grid_entry = KpiEntry(
                            name=f"Space heating heat pump electricity from grid {self.component_name}",
                            unit="kWh",
                            value=sh_heatpump_electricity_from_grid_in_kilowatt_hour,
                            tag=KpiTagEnumClass.EMS,
                            description=self.component_name,
                        )
                        list_of_kpi_entries.append(sh_heatpump_electricity_from_grid_entry)
                    elif "DHW" in output.field_name:
                        dhw_hp_electricity_from_grid_in_watt_series = postprocessing_results.iloc[:, index].loc[
                            postprocessing_results.iloc[:, index] < 0.0
                        ]
                        dhw_heatpump_electricity_from_grid_in_kilowatt_hour = abs(
                            KpiHelperClass.compute_total_energy_from_power_timeseries(
                                power_timeseries_in_watt=dhw_hp_electricity_from_grid_in_watt_series,
                                timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                            )
                        )
                        dhw_heatpump_electricity_from_grid_entry = KpiEntry(
                            name=f"Domestic hot water heat pump electricity from grid {self.component_name}",
                            unit="kWh",
                            value=dhw_heatpump_electricity_from_grid_in_kilowatt_hour,
                            tag=KpiTagEnumClass.EMS,
                            description=self.component_name,
                        )
                        list_of_kpi_entries.append(dhw_heatpump_electricity_from_grid_entry)
                    else:
                        log.warning(f"No DHW oder SH named in output {output.field_name} of {output.component_name}")
                elif advanced_heat_pump_class_name in output.field_name:
                    sh_electricity_from_grid_in_watt_series = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] < 0.0
                    ]
                    sh_heatpump_electricity_from_grid_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=sh_electricity_from_grid_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    # make kpi entry
                    sh_heatpump_electricity_from_grid_entry = KpiEntry(
                        name=f"Space heating heat pump electricity from grid {self.component_name}",
                        unit="kWh",
                        value=sh_heatpump_electricity_from_grid_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(sh_heatpump_electricity_from_grid_entry)
                elif occupancy_class_name in output.field_name:
                    occupancy_electricity_from_grid_in_watt_series = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] < 0.0
                    ]

                    occupancy_electricity_from_grid_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=occupancy_electricity_from_grid_in_watt_series,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    occupancy_electricity_from_grid_entry = KpiEntry(
                        name=f"Residents' electricity consumption from grid {self.component_name}",
                        unit="kWh",
                        value=occupancy_electricity_from_grid_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(occupancy_electricity_from_grid_entry)
                elif output.field_name == self.TotalElectricityConsumptionEMSControlled:
                    total_electricity_consumption_ems_controlled_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_electricity_consumption_ems_controlled_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_electricity_consumption_ems_controlled_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_consumption_ems_controlled_entry = KpiEntry(
                        name=f"Controlled electricity consumption {self.component_name}",
                        unit="kWh",
                        value=total_electricity_consumption_ems_controlled_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_consumption_ems_controlled_entry)
                elif output.field_name == self.TotalElectricityConsumptionUnControlled:
                    total_electricity_consumption_ems_uncontrolled_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_electricity_consumption_ems_uncontrolled_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_electricity_consumption_ems_uncontrolled_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_consumption_ems_uncontrolled_entry = KpiEntry(
                        name=f"Uncontrolled electricity consumption {self.component_name}",
                        unit="kWh",
                        value=total_electricity_consumption_ems_uncontrolled_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_consumption_ems_uncontrolled_entry)
                elif output.field_name == self.TotalElectricityConsumption:
                    total_electricity_consumption_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_electricity_consumption_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_electricity_consumption_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_consumption_total_entry = KpiEntry(
                        name=f"Total electricity consumption {self.component_name}",
                        unit="kWh",
                        value=total_electricity_consumption_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_consumption_total_entry)
                elif output.field_name == self.SurplusUnusedFromDistrictEMSOutput:
                    total_electricity_surplus_from_district_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_electricity_surplus_from_district_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_electricity_surplus_from_district_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_surplus_from_district_entry = KpiEntry(
                        name=f"Surplus from District {self.component_name}",
                        unit="kWh",
                        value=total_electricity_surplus_from_district_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_surplus_from_district_entry)
                elif output.field_name == self.ElectricityProduction:
                    total_production_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_production_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_production_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_production_entry = KpiEntry(
                        name=f"Production {self.component_name}",
                        unit="kWh",
                        value=total_production_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_production_entry)
                elif output.field_name == self.TotalElectricityFromGrid:
                    total_electricity_from_grid_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_electricity_from_grid_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_electricity_from_grid_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_from_grid_entry = KpiEntry(
                        name=f"Total electricity from grid {self.component_name}",
                        unit="kWh",
                        value=total_electricity_from_grid_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_from_grid_entry)
                elif output.field_name == self.TotalElectricityToGrid:
                    total_electricity_to_grid_in_watt = postprocessing_results.iloc[:, index].loc[
                        postprocessing_results.iloc[:, index] > 0.0
                    ]
                    total_electricity_to_grid_in_kilowatt_hour = abs(
                        KpiHelperClass.compute_total_energy_from_power_timeseries(
                            power_timeseries_in_watt=total_electricity_to_grid_in_watt,
                            timeresolution=self.my_simulation_parameters.seconds_per_timestep,
                        )
                    )
                    electricity_to_grid_entry = KpiEntry(
                        name=f"Total electricity to grid {self.component_name}",
                        unit="kWh",
                        value=total_electricity_to_grid_in_kilowatt_hour,
                        tag=KpiTagEnumClass.EMS,
                        description=self.component_name,
                    )
                    list_of_kpi_entries.append(electricity_to_grid_entry)

        return list_of_kpi_entries

    def get_cost_opex(
        self,
        all_outputs: List,
        postprocessing_results: pd.DataFrame,
    ) -> cp.OpexCostDataClass:
        """Calculate OPEX costs, consisting of electricity costs and revenues."""
        opex_cost_data_class = cp.OpexCostDataClass.get_default_opex_cost_data_class()
        return opex_cost_data_class

    @staticmethod
    def get_cost_capex(config: EMSDistrictConfig, simulation_parameters: SimulationParameters) -> cp.CapexCostDataClass:  # pylint: disable=unused-argument
        """Returns investment cost, CO2 emissions and lifetime."""
        capex_cost_data_class = cp.CapexCostDataClass.get_default_capex_cost_data_class()
        return capex_cost_data_class


"""
                ***IN PROGRESS***
    def distribute_available_surplus_from_district_to_building_parallel(
        self,
        source_weights_sorted: List[int],
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        inputs_sorted: List[ComponentInput],
        component_types_sorted: List[lt.ComponentType],
        outputs_sorted: List[ComponentOutput],
    ) -> float:
        """ """Evaluates available surplus electricity component by component.

        - First step: actual electricity demand of each component is subtracted from surplus
        - second step: available surplus is distributed to components iterative if same source weight

        Parallel distribution of surplus when components have equal source weight.
        To do this, the surplus is divided by the number of components with equal source weight so that each component
        has the chance to receive an equal share. If a component requires less, the rest is returned to available surplus.
        """ """
        component_types_and_number_of_same_source_weights: dict = {}

        for weight, component in zip(source_weights_sorted, component_types_sorted):
            if weight in component_types_and_number_of_same_source_weights:
                component_types_and_number_of_same_source_weights[weight]["count"] += 1
                component_types_and_number_of_same_source_weights[weight]["components"].append(component)
            else:
                component_types_and_number_of_same_source_weights[weight] = {"count": 1, "components": [component]}

        component_electricity_demand: dict = {}

        for index, item in enumerate(component_types_sorted):
            value = stsv.get_input_value(component_input=inputs_sorted[index])
            component_electricity_demand[item] = -1 * float(abs(value))

        repeat_count = 0
        previous_single_source_weight_sorted = 0

        available_surplus_electricity_in_watt_next_component = available_surplus_electricity_in_watt

        # if (available_surplus_electricity_in_watt > 100 and list(component_electricity_demand.values())[
        #          0] == 0 and list(component_electricity_demand.values())[1] < -100) :
        #     print("debug")

        repeat_max = max(
            component_types_and_number_of_same_source_weights[single_source_weight_sorted]["count"]
            for single_source_weight_sorted in source_weights_sorted
        )

        while repeat_count < repeat_max + 5:
            repeat_loop = False
            index = 0
            surplus_next_iteration = 0.0
            additional_electricity_demand = 0.0
            counter_inner_while = 0

            while index < len(inputs_sorted):
                single_component_type_sorted = component_types_sorted[index]
                single_output_sorted = outputs_sorted[index]
                single_source_weight_sorted = source_weights_sorted[index]

                number_of_components_with_same_source_weights = component_types_and_number_of_same_source_weights[
                    single_source_weight_sorted
                ]["count"]

                electricity_demand_from_current_input_component_in_watt = component_electricity_demand[
                    single_component_type_sorted
                ]

                index += 1

                if single_source_weight_sorted > previous_single_source_weight_sorted:
                    counter_inner_while = 0

                available_surplus_electricity_in_watt = available_surplus_electricity_in_watt_next_component

                #
                #  if repeat_count > 0:
                #      print("debug")

                # if (
                #     component_electricity_demand[single_component_type_sorted] >= 0
                #     or number_of_components_with_electricity_demand_and_same_source_weight == 0
                # ):
                #     available_surplus_electricity_in_watt_next_component = available_surplus_electricity_in_watt
                #     additional_electricity_demand = additional_electricity_demand
                #     surplus_next_iteration = surplus_next_iteration
                #     continue

                if repeat_count > 0 and component_electricity_demand[single_component_type_sorted] >= 0:
                    available_surplus_electricity_in_watt_next_component = available_surplus_electricity_in_watt
                    additional_electricity_demand = additional_electricity_demand
                    surplus_next_iteration = surplus_next_iteration
                    component_electricity_demand[single_component_type_sorted] = 0
                    continue

                if component_types_and_number_of_same_source_weights[single_source_weight_sorted]["count"] > 1:

                    number_of_components_with_electricity_demand_and_same_source_weight = sum(
                        1
                        for key, wert in component_electricity_demand.items()
                        if wert < 0
                        and key
                        in component_types_and_number_of_same_source_weights[single_source_weight_sorted]["components"]
                    )

                    if counter_inner_while == 0:

                        if number_of_components_with_electricity_demand_and_same_source_weight <= 1:
                            available_surplus_electricity_in_watt_split = available_surplus_electricity_in_watt
                            available_surplus_electricity_in_watt_next_component = 0
                        # if number_of_components_with_electricity_demand_and_same_source_weight <= 1:
                        #     available_surplus_electricity_in_watt_split = available_surplus_electricity_in_watt
                        #     available_surplus_electricity_in_watt_next_component = 0
                        else:
                            available_surplus_electricity_in_watt_split = (
                                available_surplus_electricity_in_watt
                                / number_of_components_with_electricity_demand_and_same_source_weight
                            )
                            # if number_of_components_with_electricity_demand_and_same_source_weight == 1:
                            #     available_surplus_electricity_in_watt_next_component = 0
                            # else:
                            available_surplus_electricity_in_watt_next_component = (
                                available_surplus_electricity_in_watt_split
                            )

                    else:
                        available_surplus_electricity_in_watt_split = (
                            available_surplus_electricity_in_watt_next_component
                        )

                    available_surplus_electricity_in_watt = self.control_electricity_parallel(
                        available_surplus_electricity_in_watt=available_surplus_electricity_in_watt_split,
                        stsv=stsv,
                        current_component_type=single_component_type_sorted,
                        current_output=single_output_sorted,
                        component_electricity_demand=component_electricity_demand,
                        electricity_demand_from_current_input_component_in_watt=electricity_demand_from_current_input_component_in_watt,
                    )

                    counter_inner_while += 1

                    if available_surplus_electricity_in_watt > 0:
                        surplus_next_iteration += available_surplus_electricity_in_watt
                        available_surplus_electricity_in_watt = 0
                    else:
                        additional_electricity_demand += available_surplus_electricity_in_watt
                        available_surplus_electricity_in_watt = 0

                    if (
                        surplus_next_iteration > 0
                        and additional_electricity_demand < 0
                        and counter_inner_while == number_of_components_with_same_source_weights
                    ):
                        repeat_loop = True

                else:

                    available_surplus_electricity_in_watt = (
                        available_surplus_electricity_in_watt + surplus_next_iteration + additional_electricity_demand
                    )

                    available_surplus_electricity_in_watt = self.control_electricity_parallel(
                        available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                        stsv=stsv,
                        current_component_type=single_component_type_sorted,
                        current_output=single_output_sorted,
                        component_electricity_demand=component_electricity_demand,
                        electricity_demand_from_current_input_component_in_watt=electricity_demand_from_current_input_component_in_watt,
                    )

                    available_surplus_electricity_in_watt_next_component = available_surplus_electricity_in_watt

                previous_single_source_weight_sorted = single_source_weight_sorted

                if repeat_loop:
                    break

            if repeat_loop:
                repeat_count += 1
                available_surplus_electricity_in_watt_next_component = surplus_next_iteration

            else:
                break

        # available_surplus_electricity_in_watt = sum(
        #     values for values in component_electricity_demand.values()
        # )  # if values < 0)

        max_key = max(component_types_and_number_of_same_source_weights.keys())
        components_with_max_count = component_types_and_number_of_same_source_weights[max_key]["components"]

        available_surplus_electricity_in_watt = sum(
            component_electricity_demand[component] for component in components_with_max_count
        )

        return available_surplus_electricity_in_watt
"""
