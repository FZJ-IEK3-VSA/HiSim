# Generic/Built-in
from dataclasses import dataclass
from typing import Any
from typing import List

from dataclasses_json import dataclass_json

from hisim import component as cp
from hisim import dynamic_component
from hisim import loadtypes as lt
from hisim import utils
from hisim.simulationparameters import SimulationParameters

__authors__ = "Maximilian Hillen"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Maximilian Hillen"
__email__ = "maximilian.hillen@rwth-aachen.de"
__status__ = "development"


class L2GenericEnergyManagementSystem(dynamic_component.DynamicComponent):
    """
    Surplus electricity controller - time step based. 
    Iteratively goes through hierachy of devices given by
    source weights of components and passes available surplus
    electricity to each device. Needs to be configured with 
    dynamic In- and Outputs.
    """
    # Inputs

    ElectricityToElectrolyzerUnused = "ElectricityToElectrolyzerUnused"

    # Outputs
    ElectricityToElectrolyzerTarget = "ElectricityToElectrolyzerTarget"

    ElectricityToOrFromGrid = "ElectricityToOrFromGrid"
    TotalElectricityConsumption = "TotalElectricityConsumption"
    FlexibleElectricity = "FlexibleElectricity"
    BuildingTemperatureModifier = "BuildingTemperatureModifier"
    StorageTemperatureModifier = "StorageTemperatureModifier"

    CheckPeakShaving = "CheckPeakShaving"

    @utils.measure_execution_time
    def __init__(self, my_simulation_parameters: SimulationParameters, strategy: str = "optimize_own_consumption",
                 # strategy=["optimize_own_consumption","peak_shaving_from_grid", "peak_shaving_into_grid","seasonal_storage"]
                 limit_to_shave: float = 0):
        self.my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
        self.my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []
        super().__init__(my_component_inputs=self.my_component_inputs, my_component_outputs=self.my_component_inputs,
                         name="L2EMSElectricityController", my_simulation_parameters=my_simulation_parameters)

        self.strategy = strategy
        self.limit_to_shave = limit_to_shave
        self.electricity_to_electrolyzer_unused: cp.ComponentInput = self.add_input(object_name=self.component_name,
                                                                                    field_name=self.ElectricityToElectrolyzerUnused,
                                                                                    load_type=lt.LoadTypes.ELECTRICITY, unit=lt.Units.WATT,
                                                                                    mandatory=False)

        # Outputs
        self.electricity_to_or_from_grid: cp.ComponentOutput = self.add_output(object_name=self.component_name,
                                                                               field_name=self.ElectricityToOrFromGrid,
                                                                               load_type=lt.LoadTypes.ELECTRICITY, unit=lt.Units.WATT,
                                                                               sankey_flow_direction=False)

        self.total_electricity_consumption_channel: cp.ComponentOutput = self.add_output(object_name=self.component_name,
                                                                               field_name=self.TotalElectricityConsumption,
                                                                               load_type=lt.LoadTypes.ELECTRICITY, unit=lt.Units.WATT,
                                                                               sankey_flow_direction=False)

        self.flexible_electricity: cp.ComponentOutput = self.add_output(object_name=self.component_name,
                                                                               field_name=self.FlexibleElectricity,
                                                                               load_type=lt.LoadTypes.ELECTRICITY, unit=lt.Units.WATT,
                                                                               sankey_flow_direction=False)


        self.building_temperature_modifier: cp.ComponentOutput = self.add_output(object_name=self.component_name,
                                                                               field_name=self.BuildingTemperatureModifier,
                                                                               load_type=lt.LoadTypes.TEMPERATURE, unit=lt.Units.CELSIUS,
                                                                               sankey_flow_direction=False)

        self.storage_temperature_modifier: cp.ComponentOutput = self.add_output(object_name=self.component_name,
                                                                                 field_name=self.StorageTemperatureModifier,
                                                                                 load_type=lt.LoadTypes.TEMPERATURE, unit=lt.Units.CELSIUS,
                                                                                 sankey_flow_direction=False)

        self.check_peak_shaving: cp.ComponentOutput = self.add_output(object_name=self.component_name, field_name=self.CheckPeakShaving,
                                                                      load_type=lt.LoadTypes.ANY, unit=lt.Units.ANY, sankey_flow_direction=False)

    def sort_source_weights_and_components(self) -> None:
        SourceTags = [elem.source_tags[0] for elem in self.my_component_inputs]
        SourceWeights = [elem.source_weight for elem in self.my_component_outputs]
        sortindex = sorted(range(len(SourceWeights)), key=lambda k: SourceWeights[k])
        self.source_weights_sorted = [SourceWeights[i] for i in sortindex]
        self.components_sorted = [SourceTags[i] for i in sortindex]

    def get_entries_of_type(self, component_type: lt.ComponentType) -> Any:
        return self.components_sorted.index(component_type)

    def build(self, mode: Any) -> None:
        self.mode = mode

    def write_to_report(self) -> None:
        pass

    def i_save_state(self) -> None:
        # abändern, siehe Storage
        pass  # self.previous_state = self.state

    def i_restore_state(self) -> None:
        pass  # self.state = self.previous_state

    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def control_electricity_component_iterative(self, deltademand: float, stsv: cp.SingleTimeStepValues, weight_counter: int,
                                                component_type: lt.ComponentType) -> Any:
        is_battery = None
        # get previous signal and substract from total balance
        previous_signal = self.get_dynamic_input(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_REAL], weight_counter=weight_counter)
        
        if component_type == lt.ComponentType.CAR_BATTERY:
            self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET], weight_counter=weight_counter,
                                    output_value=deltademand)
            if previous_signal > 0:
                deltademand = deltademand - previous_signal

        # control from substracted balance
        elif component_type == lt.ComponentType.BATTERY:
            self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET], weight_counter=weight_counter,
                                    output_value=deltademand)
            deltademand = deltademand - previous_signal

        elif component_type == lt.ComponentType.FUEL_CELL:
            if deltademand < 0:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET], weight_counter=weight_counter,
                                        output_value=-deltademand)
                deltademand = deltademand + previous_signal
                if deltademand > 0:
                    is_battery = self.get_entries_of_type(lt.ComponentType.BATTERY)
            else:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET], weight_counter=weight_counter,
                                        output_value=0)

        elif component_type in [lt.ComponentType.ELECTROLYZER, lt.ComponentType.HEAT_PUMP, lt.ComponentType.SMART_DEVICE]:

            if deltademand > 0:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET], weight_counter=weight_counter,
                                        output_value=deltademand)
                deltademand = deltademand - previous_signal
            else:
                self.set_dynamic_output(stsv=stsv, tags=[component_type, lt.InandOutputType.ELECTRICITY_TARGET], weight_counter=weight_counter,
                                        output_value=0)

        return deltademand, is_battery

    def postprocess_battery(self, deltademand: float, stsv: cp.SingleTimeStepValues, ind: int) -> Any:
        previous_signal = self.get_dynamic_input(stsv=stsv, tags=[self.components_sorted[ind]], weight_counter=self.source_weights_sorted[ind])
        self.set_dynamic_output(stsv=stsv, tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
                                weight_counter=self.source_weights_sorted[ind], output_value=deltademand + previous_signal)
        return deltademand - previous_signal

    def optimize_own_consumption_iterative(self, delta_demand: float, stsv: cp.SingleTimeStepValues) -> None:
        skip_CHP = False
        for ind in range(len(self.source_weights_sorted)):
            component_type = self.components_sorted[ind]
            source_weight = self.source_weights_sorted[ind]
            if component_type in [lt.ComponentType.BATTERY, lt.ComponentType.FUEL_CELL, lt.ComponentType.ELECTROLYZER, lt.ComponentType.HEAT_PUMP,
                                  lt.ComponentType.SMART_DEVICE, lt.ComponentType.CAR_BATTERY]:
                if not skip_CHP or component_type in [lt.ComponentType.BATTERY, lt.ComponentType.ELECTROLYZER, lt.ComponentType.HEAT_PUMP,
                                                      lt.ComponentType.SMART_DEVICE, lt.ComponentType.CAR_BATTERY]:
                    delta_demand, is_battery = self.control_electricity_component_iterative(deltademand=delta_demand, stsv=stsv,
                                                                                            weight_counter=source_weight,
                                                                                            component_type=component_type)
                else:
                    self.set_dynamic_output(stsv=stsv, tags=[lt.ComponentType.FUEL_CELL, lt.InandOutputType.ELECTRICITY_TARGET],
                                            weight_counter=source_weight, output_value=0)
                if is_battery is not None:
                    delta_demand = self.postprocess_battery(deltademand=delta_demand, stsv=stsv, ind=is_battery)
                    skip_CHP = True

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        if force_convergence:
            return

        if timestep == 0:
            self.sort_source_weights_and_components()

        ###ELECTRICITY#####

        # get production
        production = sum(self.get_dynamic_inputs(stsv=stsv, tags=[lt.InandOutputType.ELECTRICITY_PRODUCTION]))
        consumption_uncontrolled = sum(self.get_dynamic_inputs(stsv=stsv, tags=[lt.InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED]))
        consumption_ems_controlled = sum(self.get_dynamic_inputs(stsv=stsv, tags=[lt.InandOutputType.ELECTRICITY_REAL]))

        # Production of Electricity positve sign
        # Consumption of Electricity negative sign
        flexible_electricity = production - consumption_uncontrolled
        electricity_to_grid = production - consumption_uncontrolled - consumption_ems_controlled
        if self.strategy == "optimize_own_consumption":
            self.optimize_own_consumption_iterative(delta_demand=electricity_to_grid, stsv=stsv)
            stsv.set_output_value(self.electricity_to_or_from_grid, electricity_to_grid)
            stsv.set_output_value(self.flexible_electricity, flexible_electricity)
        stsv.set_output_value(self.total_electricity_consumption_channel, consumption_uncontrolled + consumption_ems_controlled)
        if flexible_electricity > 0:
            stsv.set_output_value(self.building_temperature_modifier, 1)
            stsv.set_output_value(self.storage_temperature_modifier, 10)
        else:
            stsv.set_output_value(self.building_temperature_modifier, 0)
            stsv.set_output_value(self.storage_temperature_modifier, 0)
        '''
        elif self.strategy == "seasonal_storage":
            self.seasonal_storage(delta_demand=delta_demand, stsv=stsv)
        elif self.strategy == "peak_shaving_into_grid":
            self.peak_shaving_into_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        elif self.strategy == "peak_shaving_from_grid":
            self.peak_shaving_from_grid(delta_demand=delta_demand, limit_to_shave=limit_to_shave,stsv=stsv)
        '''

        #######HEAT########
        # If comftortable temperature of building is to low heat with WarmWaterStorage the building
        # Solution with Control Signal Residence
        # not perfect solution!
        '''
        if self.temperature_residence<self.min_comfortable_temperature_residence:
            #heat
            #here has to be added how "strong" HeatingWater Storage can be discharged
            #Working with upper boarder?
        elif self.temperature_residence > self.max_comfortable_temperature_residence:
            #cool
        elif self.temperature_residence>self.min_comfortable_temperature_residence and self.temperature_residence<self.max_comfortable_temperature_residence:
        '''
