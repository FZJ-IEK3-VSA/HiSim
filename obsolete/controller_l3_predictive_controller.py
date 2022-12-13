# -*- coding: utf-8 -*-
"""
Created on Tue Apr 26 12:59:48 2022

@author: Johanna
"""

# Generic/Built-in
from typing import Optional, List, Tuple
import numpy as np

# Owned
from hisim import dynamic_component
from hisim import log
from hisim import component as cp
from hisim import loadtypes as lt
from hisim.components import loadprofilegenerator_connector
from hisim.components import generic_pv_system
from hisim.components import generic_price_signal
from hisim.components import generic_smart_device
from hisim.components import controller_l1_generic_runtime
from hisim.components import generic_heat_pump_modular
from hisim.simulationparameters import SimulationParameters

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


def price_and_peak(
    totalload: List,
    shiftableload: List,
    pricepurchaseforecast: List,
    priceinjectionforecast: List,
) -> Tuple[float, float]:
    """calculate price per kWh of device which is activated and maximal load peak

    Parameters:
    -----------
    totalload : list
        Original load excluding device (demand is positive, surplus negative).
    shifableload : list
        Load of device which activation is tested.
    pricepurchaseforecast : list
        Forecast of the pricesignal for purchase.
    priceinjectionforecast : list
        Forecast of the pricesignal for injection.
    """
    if sum(shiftableload) == 0:
        price_per_kWh = 2.0 * sum(pricepurchaseforecast)
        peak = 0.0
    else:
        # calculate load when device is switched on
        potentialload = [a + b for (a, b) in zip(totalload, shiftableload)]

        # calculate price
        price_with = [
            a * b for (a, b) in zip(potentialload, pricepurchaseforecast) if a > 0
        ] + [a * b for (a, b) in zip(potentialload, priceinjectionforecast) if a < 0]
        price_without = [
            a * b for (a, b) in zip(totalload, pricepurchaseforecast) if a > 0
        ] + [a * b for (a, b) in zip(totalload, priceinjectionforecast) if a < 0]

        price_per_kWh = (sum(price_with) - sum(price_without)) / sum(shiftableload)

        # calculate peak
        peak = max(totalload)

    return price_per_kWh, peak


def advance(component_type: lt.ComponentType, ind: int) -> int:
    if component_type == lt.ComponentType.HEAT_PUMP:
        return ind + 1
    elif component_type == lt.ComponentType.SMART_DEVICE:
        return ind + 3
    else:
        return ind


class ControllerSignal:
    """class to save predictive output signal from predictive controller
    -1 shut off device if possible
      0 evalueation is not needed
      1 turn on device if possible
    """

    def __init__(self, signal: List = []):
        self.signal = signal

    def clone(self):
        return ControllerSignal(signal=self.signal)


class L3_Controller(dynamic_component.DynamicComponent):
    """
    Predictive controller. It takes data from the dictionary my_simulation_repository
    and decides if device should be activated or not. The predictive controller is a central
    contoller operating device by device following a predefined hierachy
    1. smart appliances
    2. boiler
    3. heating system


    Parameters
    --------------
    threshold_price: float
        Maximum price allowed for switch on
    threshold_peak: float or None
        Maximal peak allowed for switch on.
    """

    # Inputs and Outputs
    my_component_inputs: List[dynamic_component.DynamicConnectionInput] = []
    my_component_outputs: List[dynamic_component.DynamicConnectionOutput] = []

    def __init__(
        self,
        my_simulation_parameters: SimulationParameters,
        threshold_price: float = 25,
        threshold_peak: Optional[float] = None,
    ) -> None:

        super().__init__(
            my_component_inputs=self.my_component_inputs,
            my_component_outputs=self.my_component_outputs,
            name="L2PredictiveSmartDeviceController",
            my_simulation_parameters=my_simulation_parameters,
        )

        self.build(threshold_price, threshold_peak)

    def build(self, threshold_price: float, threshold_peak: Optional[float]) -> None:
        self.threshold_peak = threshold_peak
        self.threshold_price = threshold_price
        self.signal = ControllerSignal()
        self.previous_signal = ControllerSignal()
        self.source_weights_sorted: list = []
        self.components_sorted: list[lt.ComponentType] = []

    def sort_source_weights_and_components(self) -> None:
        SourceTags = [elem.source_tags[0] for elem in self.my_component_inputs]
        SourceWeights = [elem.source_weight for elem in self.my_component_inputs]
        sortindex = sorted(range(len(SourceWeights)), key=lambda k: SourceWeights[k])
        self.source_weights_sorted = [SourceWeights[i] for i in sortindex]
        self.components_sorted = [SourceTags[i] for i in sortindex]

    def decision_maker(self, price_per_kWh: float, peak: float) -> int:
        if (
            (not self.threshold_peak) or peak < self.threshold_peak
        ) and price_per_kWh < self.threshold_price:
            return 1
        else:
            return 0

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        pass

    def write_to_report(self) -> List[str]:
        lines: List[str] = []
        lines.append("L3 Controller Heat Pump: " + self.component_name)
        return lines

    def i_save_state(self) -> None:
        self.previous_signal = self.signal.clone()

    def i_restore_state(self) -> None:
        self.signal = self.previous_signal.clone()

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:

        if timestep == 0:
            self.sort_source_weights_and_components()

        totalload = self.simulation_repository.get_entry(
            loadprofilegenerator_connector.Occupancy.Electricity_Demand_Forecast_24h
        )
        priceinjectionforecast = self.simulation_repository.get_entry(
            generic_price_signal.PriceSignal.Price_Injection_Forecast_24h
        )
        pricepurchaseforecast = self.simulation_repository.get_entry(
            generic_price_signal.PriceSignal.Price_Purchase_Forecast_24h
        )

        # substract PV production from laod, if available
        for elem in self.simulation_repository.get_dynamic_component_weights(
            component_type=lt.ComponentType.PV
        ):
            pvforecast = self.simulation_repository.get_dynamic_entry(
                component_type=lt.ComponentType.PV, source_weight=elem
            )
            totalload = [a - b for (a, b) in zip(totalload, pvforecast)]

        # initialize device signals
        signal = []
        ind = 0  # input index
        pos = 0  # output index, position of output in signal of ControllerSignal
        end = len(self.source_weights_sorted)

        # loops over components -> also fixes hierachy in control
        while ind < end:

            devicestate = 0
            weight_counter = self.source_weights_sorted[ind]
            component_type = self.components_sorted[ind]

            if force_convergence:
                self.set_dynamic_output(
                    stsv=stsv,
                    tags=[component_type],
                    weight_counter=weight_counter,
                    output_value=self.signal.signal[pos],
                )
                pos += 1
                ind = advance(component_type, ind)

            else:
                if (
                    component_type == lt.ComponentType.HEAT_PUMP
                ):  # loop over all source weights, breaks if one is missing

                    # try if input is available -> returns None if not
                    devicestate = self.get_dynamic_input(
                        stsv=stsv, tags=[component_type], weight_counter=weight_counter
                    )
                    shiftableload = self.simulation_repository.get_dynamic_entry(
                        component_type=component_type, source_weight=weight_counter
                    )
                    steps = len(shiftableload)

                    # calculate price and peak and get controller signal
                    price_per_kWh, peak = price_and_peak(
                        totalload[:steps],
                        shiftableload,
                        pricepurchaseforecast[:steps],
                        priceinjectionforecast[:steps],
                    )
                    signal.append(
                        self.decision_maker(price_per_kWh=price_per_kWh, peak=peak)
                    )

                    # recompute base load if device was activated
                    if devicestate == 1:
                        totalload = [
                            a + b for (a, b) in zip(totalload[:steps], shiftableload)
                        ] + totalload[steps:]

                    self.set_dynamic_output(
                        stsv=stsv,
                        tags=[component_type],
                        weight_counter=weight_counter,
                        output_value=signal[-1],
                    )

                elif component_type == lt.ComponentType.SMART_DEVICE:

                    # get forecasts
                    profiles = self.simulation_repository.get_dynamic_entry(
                        component_type=component_type, source_weight=weight_counter
                    )
                    prof_act = profiles[0]
                    prof_next = profiles[1]

                    # get inputs
                    lastactivation = self.get_dynamic_input(
                        stsv=stsv,
                        tags=[component_type, lt.InandOutputType.LAST_ACTIVATION],
                        weight_counter=weight_counter,
                    )
                    earliestactivation = self.get_dynamic_input(
                        stsv=stsv,
                        tags=[component_type, lt.InandOutputType.EARLIEST_ACTIVATION],
                        weight_counter=weight_counter,
                    )
                    latestactivation = self.get_dynamic_input(
                        stsv=stsv,
                        tags=[component_type, lt.InandOutputType.LATEST_ACTIVATION],
                        weight_counter=weight_counter,
                    )

                    # relevant timesteps
                    horizon = int(
                        self.my_simulation_parameters.prediction_horizon
                        / self.my_simulation_parameters.seconds_per_timestep
                    )
                    lastactivated = max(lastactivation + len(prof_act) - timestep, 0)

                    # initialize activation signal
                    activation = timestep + horizon

                    # assign to profile if device is running
                    if lastactivated > 0:
                        if lastactivated < horizon:
                            profile = prof_act[timestep - lastactivation :] + [0] * (
                                horizon - timestep + lastactivation
                            )
                        else:
                            profile = prof_act[
                                timestep
                                - lastactivation : timestep
                                - lastactivation
                                + horizon
                            ]
                    else:
                        profile = [0] * horizon

                    # test activation if possible
                    if earliestactivation - timestep < horizon:
                        # define interval and initialize price
                        iteration_begin = max(
                            earliestactivation - timestep, lastactivated
                        )
                        iteration_end = min(
                            latestactivation - timestep, horizon - len(prof_next)
                        )
                        price = np.inf
                        # self.threshold_price = 25

                        # loop over possibilities and save best ones
                        for possibility in range(iteration_begin, iteration_end):
                            shiftableload = (
                                profile[:possibility]
                                + prof_next
                                + profile[possibility + len(prof_next) :]
                            )
                            price_per_kWh, _ = price_and_peak(
                                totalload,
                                shiftableload,
                                pricepurchaseforecast,
                                priceinjectionforecast,
                            )
                            if price_per_kWh < price:
                                price = price_per_kWh
                                activation = timestep + possibility
                                profile = [*shiftableload]
                                continue

                    # compute new load
                    totalload = [a + b for (a, b) in zip(totalload, profile)]

                    # set output: timestep of best activation
                    signal.append(activation)
                    self.set_dynamic_output(
                        stsv=stsv,
                        tags=[component_type],
                        weight_counter=weight_counter,
                        output_value=signal[-1],
                    )
                else:
                    pass

                ind = advance(component_type, ind)
                self.signal = ControllerSignal(signal=signal)
