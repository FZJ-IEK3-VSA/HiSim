""" Iterative Energy Surplus Controller.

It received the electricity consumption
of all components and the PV production. According to the balance it
sends activation/deactivation siganls to components.
The component with the lowest source weight is activated first.
"""

# clean
from dataclasses import dataclass

from typing import Any, List

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
    building_temperature_offset_value: float
    # increase in buffer set temperatures when PV surplus is available for heating
    storage_temperature_offset_value: float
    # increase in SimpleHotWaterStorage set temperatures when PV surplus is available for heating
    simple_hot_water_storage_temperature_offset_value: float

    @classmethod
    def get_default_config_ems(cls) -> "EMSConfig":
        """Default Config for Energy Management System."""
        config = EMSConfig(
            name="L2EMSElectricityController",
            strategy="optimize_own_consumption",
            limit_to_shave=0,
            building_temperature_offset_value=2,
            storage_temperature_offset_value=10,
            simple_hot_water_storage_temperature_offset_value=10,
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
        self.production = production
        self.consumption_uncontrolled = consumption_uncontrolled
        self.consumption_ems_controlled = consumption_ems_controlled

    def clone(self) -> "EMSState":
        """Copy EMSState efficiently."""
        return EMSState(
            production=self.production,
            consumption_uncontrolled=self.consumption_uncontrolled,
            consumption_ems_controlled=self.consumption_ems_controlled,
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

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"

    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    FlexibleElectricity = "FlexibleElectricity"
    BuildingTemperatureModifier = (
        "BuildingTemperatureModifier"  # connect to HDS controller and Building
    )
    StorageTemperatureModifier = "StorageTemperatureModifier"  # used for L1HeatPumpController  # Todo: change name?
    SimpleHotWaterStorageTemperatureModifier = (
        "SimpleHotWaterStorageTemperatureModifier"  # used for HeatPumpHplibController
    )

    CheckPeakShaving = "CheckPeakShaving"

    @utils.measure_execution_time
    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: EMSConfig
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
        )

        self.state = EMSState(
            production=0, consumption_uncontrolled=0, consumption_ems_controlled=0
        )
        self.previous_state = self.state.clone()

        self.components_sorted: List[lt.ComponentType] = []
        self.inputs_sorted: List[ComponentInput] = []
        self.outputs_sorted: List[ComponentOutput] = []
        self.production_inputs: List[ComponentInput] = []
        self.consumption_uncontrolled_inputs: List[ComponentInput] = []
        self.consumption_ems_controlled_inputs: List[ComponentInput] = []

        self.mode: Any
        self.strategy = self.ems_config.strategy
        self.limit_to_shave = self.ems_config.limit_to_shave
        self.building_temperature_offset_value = (
            self.ems_config.building_temperature_offset_value
        )
        self.storage_temperature_offset_value = (
            self.ems_config.storage_temperature_offset_value
        )
        self.simple_hot_water_storage_temperature_offset_value = (
            self.ems_config.simple_hot_water_storage_temperature_offset_value
        )

        # Inputs
        self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(
            object_name=self.component_name,
            field_name=self.ElectricityToElectrolyzerUnused,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            mandatory=False,
        )

        # Outputs
        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.ElectricityToOrFromGrid,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.ElectricityToOrFromGrid} will follow.",
        )

        self.total_electricity_consumption_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.TotalElectricityConsumption,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.TotalElectricityConsumption} will follow.",
        )

        self.flexible_electricity_channel: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.FlexibleElectricity,
            load_type=lt.LoadTypes.ELECTRICITY,
            unit=lt.Units.WATT,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.FlexibleElectricity} will follow.",
        )

        self.building_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.BuildingTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.BuildingTemperatureModifier} will follow.",
        )

        self.storage_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.StorageTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.StorageTemperatureModifier} will follow.",
        )

        self.simple_hot_water_storage_temperature_modifier: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.SimpleHotWaterStorageTemperatureModifier,
            load_type=lt.LoadTypes.TEMPERATURE,
            unit=lt.Units.CELSIUS,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.SimpleHotWaterStorageTemperatureModifier} will follow.",
        )

        self.check_peak_shaving: cp.ComponentOutput = self.add_output(
            object_name=self.component_name,
            field_name=self.CheckPeakShaving,
            load_type=lt.LoadTypes.ANY,
            unit=lt.Units.ANY,
            sankey_flow_direction=False,
            output_description=f"here a description for {self.CheckPeakShaving} will follow.",
        )

    def sort_source_weights_and_components(self) -> None:
        """Sorts dynamic Inputs and Outputs according to source weights."""
        inputs = [
            elem for elem in self.my_component_inputs if elem.source_weight != 999
        ]
        source_tags = [elem.source_tags[0] for elem in inputs]
        source_weights = [elem.source_weight for elem in inputs]
        sortindex = sorted(range(len(source_weights)), key=lambda k: source_weights[k])
        source_weights = [source_weights[i] for i in sortindex]
        self.components_sorted = [source_tags[i] for i in sortindex]
        self.inputs_sorted = [
            getattr(self, inputs[i].source_component_class) for i in sortindex
        ]
        self.outputs_sorted = []

        for ind, source_weight in enumerate(source_weights):
            output = self.get_dynamic_output(
                tags=[
                    self.components_sorted[ind],
                    lt.InandOutputType.ELECTRICITY_TARGET,
                ],
                weight_counter=source_weight,
            )
            if output is not None:
                self.outputs_sorted.append(output)
            else:
                raise Exception("Danamic input is not conncted to dynamic output")

        self.production_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION]
        )
        self.consumption_uncontrolled_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]
        )
        self.consumption_ems_controlled_inputs = self.get_dynamic_inputs(
            tags=[lt.InandOutputType.ELECTRICITY_REAL]
        )
        print([elem.field_name for elem in self.consumption_ems_controlled_inputs])

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
        deltademand: float,
        stsv: cp.SingleTimeStepValues,
        component_type: lt.ComponentType,
        input_channel: cp.ComponentInput,
        output: cp.ComponentOutput,
    ) -> float:
        """Calculates available surplus electricity.

        Subtracts the electricity consumption signal of the component from the previous iteration,
        and sends updated signal back.
        """
        # get previous signal and substract from total balance
        previous_signal = stsv.get_input_value(component_input=input_channel)

        # control from substracted balance
        if component_type == lt.ComponentType.BATTERY:
            stsv.set_output_value(output=output, value=deltademand)
            # deltademand = deltademand - previous_signal

        elif component_type == lt.ComponentType.CHP:
            stsv.set_output_value(output=output, value=deltademand)

        elif component_type in [
            lt.ComponentType.HEAT_PUMP_DHW,
            lt.ComponentType.HEAT_PUMP,
        ]:  # Todo: lt.ComponentType.HEAT_PUMP is from old version, kept here just to avoid errors
            if deltademand > 0:
                stsv.set_output_value(
                    self.storage_temperature_modifier,
                    self.storage_temperature_offset_value,
                )
                stsv.set_output_value(output=output, value=deltademand)
                deltademand = deltademand - previous_signal
            else:
                stsv.set_output_value(self.storage_temperature_modifier, 0)
                stsv.set_output_value(output=output, value=deltademand)

        elif component_type == lt.ComponentType.HEAT_PUMP_BUILDING:
            if deltademand > 0:
                stsv.set_output_value(
                    self.building_temperature_modifier,
                    self.building_temperature_offset_value,
                )
                stsv.set_output_value(
                    self.simple_hot_water_storage_temperature_modifier,
                    self.simple_hot_water_storage_temperature_offset_value,
                )
                stsv.set_output_value(output=output, value=deltademand)
                deltademand = deltademand - previous_signal
            else:
                stsv.set_output_value(self.building_temperature_modifier, 0)
                stsv.set_output_value(
                    self.simple_hot_water_storage_temperature_modifier, 0
                )
                stsv.set_output_value(output=output, value=deltademand)
        elif component_type == lt.ComponentType.ELECTROLYZER:
            if deltademand > 0:
                stsv.set_output_value(output=output, value=deltademand)
                deltademand = deltademand + previous_signal
            else:
                stsv.set_output_value(output=output, value=0)

        elif component_type in [
            # lt.ComponentType.ELECTROLYZER,
            lt.ComponentType.SMART_DEVICE,
            lt.ComponentType.CAR_BATTERY,
        ]:
            if deltademand > 0:
                stsv.set_output_value(output=output, value=deltademand)
                deltademand = deltademand - previous_signal
            else:
                stsv.set_output_value(output=output, value=deltademand)

        return deltademand

    def optimize_own_consumption_iterative(
        self,
        delta_demand: float,
        stsv: cp.SingleTimeStepValues,
    ) -> None:
        """Evaluates available suplus electricity component by component, iteratively, and sends updated signals back."""

        for ind, input_sorted in enumerate(self.inputs_sorted):
            component_type = self.components_sorted[ind]
            single_input = input_sorted
            output = self.outputs_sorted[ind]

            delta_demand = self.control_electricity_component_iterative(
                deltademand=delta_demand,
                stsv=stsv,
                component_type=component_type,
                input_channel=single_input,
                output=output,
            )

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Simulates iteration of surplus controller."""
        if timestep == 0:
            self.sort_source_weights_and_components()

        # get production
        self.state.production = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.production_inputs
            ]
        )
        self.state.consumption_uncontrolled = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.consumption_uncontrolled_inputs
            ]
        )
        self.state.consumption_ems_controlled = sum(
            [
                stsv.get_input_value(component_input=elem)
                for elem in self.consumption_ems_controlled_inputs
            ]
        )

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        flexible_electricity = (
            self.state.production - self.state.consumption_uncontrolled
        )
        if self.strategy == "optimize_own_consumption":
            self.optimize_own_consumption_iterative(
                delta_demand=flexible_electricity,
                stsv=stsv,
            )

        # Set other output values
        electricity_to_grid = (
            self.state.production
            - self.state.consumption_uncontrolled
            - self.state.consumption_ems_controlled
        )
        stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_grid)
        stsv.set_output_value(self.flexible_electricity_channel, flexible_electricity)
        stsv.set_output_value(
            self.total_electricity_consumption_channel,
            self.state.consumption_uncontrolled + self.state.consumption_ems_controlled,
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
