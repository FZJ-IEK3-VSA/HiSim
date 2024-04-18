""" Iterative Energy Surplus Controller.

It received the electricity consumption
of all components and the PV production. According to the balance it
sends activation/deactivation siganls to components.
The component with the lowest source weight is activated first.
"""

# clean
from dataclasses import dataclass

from typing import Any, List, Tuple

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim import utils
from hisim.component import ComponentInput, ComponentOutput
from hisim.simulationparameters import SimulationParameters

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class EMSConfig(cp.ConfigBase):

    """L1 Controller Config."""

    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return L2GenericEnergyManagementSystem.get_full_classname()

    #: name of the device
    name: str
    # control strategy, more or less obsolete because only "optimize_own_consumption" is used at the moment.
    strategy: str
    # limit for peak shaving option, more or less obsolete because only "optimize_own_consumption" is used at the moment.
    limit_to_shave: float
    # increase building set temperatures for heating when PV surplus is available.
    # Must be smaller than difference of set_heating_temperature and set_cooling_temperature
    building_indoor_temperature_offset_value: float
    # increase in dhw buffer set temperatures when PV surplus is available for heating
    domestic_hot_water_storage_temperature_offset_value: float
    # increase in SimpleHotWaterStorage set temperatures when PV surplus is available for heating
    space_heating_water_storage_temperature_offset_value: float

    @classmethod
    def get_default_config_ems(cls) -> "EMSConfig":
        """Default Config for Energy Management System."""
        config = EMSConfig(
            name="L2EMSElectricityController",
            strategy="optimize_own_consumption",
            limit_to_shave=0,
            building_indoor_temperature_offset_value=2,
            domestic_hot_water_storage_temperature_offset_value=10,
            space_heating_water_storage_temperature_offset_value=10,
        )
        return config


class EMSState:

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

    def clone(self) -> "EMSState":
        """Copy EMSState efficiently."""
        return EMSState(
            production=self.production_in_watt,
            consumption_uncontrolled=self.consumption_uncontrolled_in_watt,
            consumption_ems_controlled=self.consumption_ems_controlled_in_watt,
        )


class L2GenericEnergyManagementSystem(dynamic_component.DynamicComponent):

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
    "CONSUMPTION_UNCONTROLLED" and the related source weight
    is set to 999.

    For each component, which should receive signals from the
    EMS, the EMS needs to be connected with one dynamic input
    with the tag "ELECTRICITY_REAL" and the source weight of
    the related component. This signal reflects the real
    consumption/production of the device, which is needed to
    update the energy balance in the EMS.
    In addition, the EMS needs to be connected with one dynamic
    output with the tag "ELECTRICITY_TARGET" with the
    source weight of the related component. This signal sends
    information on the available surplus electricity to the
    component, which receives signals from the EMS.

    """

    # Inputs
    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"
    ElectricityToBuildingFromDistrict = "ElectricityToBuildingFromDistrict"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"

    TotalElectricityToOrFromGrid = "TotalElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    ElectricityAvailable = "ElectricityAvailable"
    BuildingIndoorTemperatureModifier = "BuildingIndoorTemperatureModifier"  # connect to HDS controller and Building
    DomesticHotWaterStorageTemperatureModifier = (
        "DomesticHotWaterStorageTemperatureModifier"  # used for L1HeatPumpController  # Todo: change name?
    )
    SpaceHeatingWaterStorageTemperatureModifier = (
        "SpaceHeatingWaterStorageTemperatureModifier"  # used for HeatPumpHplibController
    )
    ElectricityToBuildingFromDistrictEMSOutput = "ElectricityToBuildingFromDistrictEMSOutput"

    CheckPeakShaving = "CheckPeakShaving"

    @utils.measure_execution_time
    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        config: EMSConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ):
        """Initializes."""
        self.my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
        self.my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
        self.ems_config = config
        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name=self.ems_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.state = EMSState(production=0, consumption_uncontrolled=0, consumption_ems_controlled=0)
        self.previous_state = self.state.clone()

        self.component_types_sorted: List[lt.ComponentType] = []
        self.inputs_sorted: List[ComponentInput] = []
        self.outputs_sorted: List[ComponentOutput] = []
        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        self.consumption_ems_controlled_inputs: List[ComponentInput] = []

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

        self.electricity_to_building_from_district: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityToBuildingFromDistrict,
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

        self.total_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumption} will follow.",
        )

        self.electricity_available_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityAvailable,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityAvailable} will follow.",
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

        self.electricity_to_building_from_district_output: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToBuildingFromDistrictEMSOutput,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToBuildingFromDistrictEMSOutput} will follow.",
        )

        self.add_dynamic_default_connections(self.get_default_connections_from_utsp_occupancy())
        self.add_dynamic_default_connections(self.get_default_connections_from_pv_system())
        self.add_dynamic_default_connections(self.get_default_connections_from_dhw_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_advanced_heat_pump())
        self.add_dynamic_default_connections(self.get_default_connections_from_advanced_battery())

    def get_default_connections_from_utsp_occupancy(
        self,
    ):
        """Get utsp occupancy default connections."""

        from hisim.components.loadprofilegenerator_utsp_connector import (  # pylint: disable=import-outside-toplevel
            UtspLpgConnector,
        )

        dynamic_connections = []
        occupancy_class_name = UtspLpgConnector.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=UtspLpgConnector,
                source_class_name=occupancy_class_name,
                source_component_field_name=UtspLpgConnector.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
                source_weight=999,
            )
        )
        return dynamic_connections

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

    def get_default_connections_from_dhw_heat_pump(
        self,
    ):
        """Get dhw heat pump default connections."""

        from hisim.components.generic_heat_pump_modular import (  # pylint: disable=import-outside-toplevel
            ModularHeatPump,
        )

        dynamic_connections = []
        dhw_heat_pump_class_name = ModularHeatPump.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=ModularHeatPump,
                source_class_name=dhw_heat_pump_class_name,
                source_component_field_name=ModularHeatPump.ElectricityOutput,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.HEAT_PUMP_DHW, lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED],
                source_weight=1,
            )
        )

        self.add_component_output(
            source_output_name=f"ElectricityToOrFromGridOf{dhw_heat_pump_class_name}_",
            source_tags=[
                lt.ComponentType.HEAT_PUMP_DHW,
                lt.InandOutputType.ELECTRICITY_TARGET,
            ],
            # source_weight=my_domnestic_hot_water_heatpump.config.source_weight,
            source_weight=1,
            source_load_type=lt.LoadTypes.ELECTRICITY,
            source_unit=lt.Units.WATT,
            output_description="Target electricity for dhw heat pump.",
        )
        return dynamic_connections

    def get_default_connections_from_advanced_heat_pump(
        self,
    ):
        """Get advanced heat pump default connections."""

        from hisim.components.advanced_heat_pump_hplib import HeatPumpHplib  # pylint: disable=import-outside-toplevel

        dynamic_connections = []
        advanced_heat_pump_class_name = HeatPumpHplib.get_classname()
        dynamic_connections.append(
            dynamic_component.DynamicComponentConnection(
                source_component_class=HeatPumpHplib,
                source_class_name=advanced_heat_pump_class_name,
                source_component_field_name=HeatPumpHplib.ElectricalInputPower,
                source_load_type=lt.LoadTypes.ELECTRICITY,
                source_unit=lt.Units.WATT,
                source_tags=[lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.ELECTRICITY_REAL],
                source_weight=2,
            )
        )
        self.add_component_output(
            source_output_name=f"ElectricityToOrFromGridOf{advanced_heat_pump_class_name}_",
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
                source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_REAL],
                source_weight=3,
            )
        )

        return dynamic_connections

    def sort_source_weights_and_components(
        self,
    ) -> Tuple[
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
        production_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION])
        consumption_uncontrolled_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        )
        consumption_ems_controlled_inputs = self.get_dynamic_inputs(tags=[lt.InandOutputType.ELECTRICITY_REAL])
        return (
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

    def control_electricity_component_iterative(
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
        """
        # get electricity demand from input component and substract from available surplus electricity
        electricity_demand_from_current_input_component_in_watt = stsv.get_input_value(component_input=current_input)

        # if available_surplus_electricity > 0: electricity is fed into battery
        # if available_surplus_electricity < 0: electricity is taken from battery
        if current_component_type == lt.ComponentType.BATTERY:
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            # difference between what is fed into battery and what battery really used
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
            )

        # CHP produces electricity which is added to available surplus electricity
        elif current_component_type == lt.ComponentType.CHP:
            available_surplus_electricity_in_watt = (
                available_surplus_electricity_in_watt + electricity_demand_from_current_input_component_in_watt
            )
            stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

        elif current_component_type in [
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
        ]:  # Todo: lt.ComponentType.HEAT_PUMP is from old version, kept here just to avoid errors
            if available_surplus_electricity_in_watt > 0:
                stsv.set_output_value(
                    self.domestic_hot_water_storage_temperature_modifier,
                    self.domestic_hot_water_storage_temperature_offset_value,
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

            else:
                stsv.set_output_value(self.domestic_hot_water_storage_temperature_modifier, 0)
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )

        elif current_component_type == lt.ComponentType.HEAT_PUMP_BUILDING:
            if available_surplus_electricity_in_watt > 0:
                stsv.set_output_value(
                    self.building_indoor_temperature_modifier,
                    self.building_indoor_temperature_offset_value,
                )
                stsv.set_output_value(
                    self.space_heating_water_storage_temperature_modifier,
                    self.space_heating_water_storage_temperature_offset_value,
                )
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)

            else:
                stsv.set_output_value(self.building_indoor_temperature_modifier, 0)
                stsv.set_output_value(self.space_heating_water_storage_temperature_modifier, 0)
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )

        elif current_component_type == lt.ComponentType.ELECTROLYZER:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )

        elif current_component_type in [
            lt.ComponentType.SMART_DEVICE,
            lt.ComponentType.CAR_BATTERY,
        ]:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )
        elif current_component_type == lt.ComponentType.SURPLUS_CONTROLLER_DISTRICT:
            if available_surplus_electricity_in_watt > 0:
                available_surplus_electricity_in_watt = (
                    available_surplus_electricity_in_watt - electricity_demand_from_current_input_component_in_watt
                )
                stsv.set_output_value(output=current_output, value=available_surplus_electricity_in_watt)
            else:
                stsv.set_output_value(
                    output=current_output, value=-electricity_demand_from_current_input_component_in_watt
                )
        return available_surplus_electricity_in_watt

    def optimize_own_consumption_iterative(
        self,
        available_surplus_electricity_in_watt: float,
        stsv: cp.SingleTimeStepValues,
        inputs_sorted: List[ComponentInput],
        component_types_sorted: List[lt.ComponentType],
        outputs_sorted: List[ComponentOutput],
    ) -> None:
        """Evaluates available suplus electricity component by component, iteratively, and sends updated signals back."""

        for index, single_input_sorted in enumerate(inputs_sorted):
            single_component_type_sorted = component_types_sorted[index]
            single_output_sorted = outputs_sorted[index]

            available_surplus_electricity_in_watt = self.control_electricity_component_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                current_component_type=single_component_type_sorted,
                current_input=single_input_sorted,
                current_output=single_output_sorted,
            )

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Simulates iteration of surplus controller."""
        if timestep == 0:
            (
                self.inputs_sorted,
                self.component_types_sorted,
                self.outputs_sorted,
                self.production_inputs,
                self.consumption_uncontrolled_inputs,
                self.consumption_ems_controlled_inputs,
            ) = self.sort_source_weights_and_components()

        district_electricity_unused = stsv.get_input_value(component_input=self.electricity_to_building_from_district)

        stsv.set_output_value(self.electricity_to_building_from_district_output, district_electricity_unused)

        # get total production and consumptions
        self.state.production_in_watt = (
            sum([stsv.get_input_value(component_input=elem) for elem in self.production_inputs])
            + district_electricity_unused
        )
        self.state.consumption_uncontrolled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_uncontrolled_inputs]
        )
        self.state.consumption_ems_controlled_in_watt = sum(
            [stsv.get_input_value(component_input=elem) for elem in self.consumption_ems_controlled_inputs]
        )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        available_surplus_electricity_in_watt = (
            self.state.production_in_watt - self.state.consumption_uncontrolled_in_watt
        )
        if self.strategy == "optimize_own_consumption":
            self.optimize_own_consumption_iterative(
                available_surplus_electricity_in_watt=available_surplus_electricity_in_watt,
                stsv=stsv,
                inputs_sorted=self.inputs_sorted,
                component_types_sorted=self.component_types_sorted,
                outputs_sorted=self.outputs_sorted,
            )

        # Set other output values
        total_electricity_to_or_from_grid_in_watt = (
            self.state.production_in_watt
            - self.state.consumption_uncontrolled_in_watt
            - self.state.consumption_ems_controlled_in_watt
        )
        stsv.set_output_value(self.total_electricity_to_or_from_grid, total_electricity_to_or_from_grid_in_watt)
        stsv.set_output_value(self.electricity_available_channel, available_surplus_electricity_in_watt)
        stsv.set_output_value(
            self.total_electricity_consumption_channel,
            self.state.consumption_uncontrolled_in_watt + self.state.consumption_ems_controlled_in_watt,
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
