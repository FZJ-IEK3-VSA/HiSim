"""Dummy class and configuration returning electricity prices (from grid) and returns (injection) in each time step. """

import numpy as np
import pandas as pd
import os

# Owned
from typing import List, Any
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim import loadtypes as lt
from hisim.sim_repository_singleton import SingletonSimRepository, SingletonDictKeyEnum

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


@dataclass_json
@dataclass
class PriceSignalConfig(cp.ConfigBase):
    @classmethod
    def get_main_classname(cls):
        """Return the full class name of the base class."""
        return PriceSignal.get_full_classname()

    #: name of the price signal
    name: str
    country: str
    pricing_scheme: str
    installed_capacity: float
    price_signal_type: str
    fixed_price: list
    static_tou_price: list
    price_injection: float

    @classmethod
    def get_default_price_signal_config(cls) -> Any:
        """Default configuration for price signal."""
        config = PriceSignalConfig(
            name="PriceSignal",
            country="Germany",
            pricing_scheme="fixed",
            installed_capacity=10e3,
            price_signal_type="dummy",
            fixed_price=[],
            static_tou_price=[],
            price_injection=0.0,
        )
        return config


class PriceSignal(cp.Component):
    """
    Class component that provides price for electricity.
    Outputs: Price for injection: cents/kWh, Price for purchase: cents/kWh
    """

    # Forecasts
    Price_Injection_Forecast_24h = "Price_Injection_Forecast_24h"
    Price_Purchase_Forecast_24h = "Price_Purchase_Forecast_24h"

    # Outputs
    PricePurchase = "PricePurchase"
    PriceInjection = "PriceInjection"

    def __init__(
        self, my_simulation_parameters: SimulationParameters, config: PriceSignalConfig
    ) -> None:
        """Initialization of Price Signal class

        :param my_simulation_parameters: _description_
        :type my_simulation_parameters: SimulationParameters
        :param config: _description_
        :type config: PriceSignalConfig
        """
        self.price_signal_config = config
        super().__init__(
            name=self.price_signal_config.name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
        )

        self.build_dummy(
            start=int(10 * 3600 / my_simulation_parameters.seconds_per_timestep),
            end=int(16 * 3600 / my_simulation_parameters.seconds_per_timestep),
        )

        # Outputs
        self.PricePurchaseC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PricePurchase,
            lt.LoadTypes.PRICE,
            lt.Units.EUR_PER_KWH,
            postprocessing_flag=[
                lt.LoadTypes.PRICE,
                lt.InandOutputType.ELECTRICITY_CONSUMPTION,
            ],
            output_description=f"here a description for {self.PricePurchase} will follow.",
        )
        self.PriceInjectionC: cp.ComponentOutput = self.add_output(
            self.component_name,
            self.PriceInjection,
            lt.LoadTypes.PRICE,
            lt.Units.EUR_PER_KWH,
            postprocessing_flag=[
                lt.LoadTypes.PRICE,
                lt.InandOutputType.ELECTRICITY_INJECTION,
            ],
            output_description=f"here a description for {self.PriceInjection} will follow.",
        )

    def i_save_state(self) -> None:
        pass

    def i_restore_state(self) -> None:
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate(
        self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool
    ) -> None:
        """Outputs price signal of time step."""
        priceinjectionforecast = [0.1]
        pricepurchaseforecast = [0.5]
        if (
            self.my_simulation_parameters.predictive_control
            and self.my_simulation_parameters.prediction_horizon
        ):
            priceinjectionforecast = [0.1] * int(
                self.my_simulation_parameters.prediction_horizon
                / self.my_simulation_parameters.seconds_per_timestep
            )
            pricepurchaseforecast = [0.5] * int(
                self.my_simulation_parameters.prediction_horizon
                / self.my_simulation_parameters.seconds_per_timestep
            )
        elif (
            self.price_signal_config.price_signal_type
            == "Prices at second half of 2021"
        ):
            priceinjectionforecast = [self.price_signal_config.price_injection] * int(
                self.my_simulation_parameters.system_config.prediction_horizon
                / self.my_simulation_parameters.seconds_per_timestep
            )
        elif self.price_signal_config.pricing_scheme == "dynamic":
            pricepurchaseforecast = self.price_signal_config.static_tou_price
        elif self.price_signal_config.pricing_scheme == "fixed":
            pricepurchaseforecast = self.price_signal_config.fixed_price
        elif self.price_signal_config.price_signal_type == "dummy":
            priceinjectionforecast = [10] * int(
                self.my_simulation_parameters.system_config.prediction_horizon
                / self.my_simulation_parameters.seconds_per_timestep
            )
            pricepurchaseforecast = [50] * int(
                self.my_simulation_parameters.system_config.prediction_horizon
                / self.my_simulation_parameters.seconds_per_timestep
            )
            # pricepurchaseforecast = [ ]
            # for step in range( self.day ):
            #     x = timestep % self.day
            #     if x > self.start and x < self.end:
            #         pricepurchaseforecast.append( 20 * self.my_simulation_parameters.seconds_per_timestep / 3.6e6 )
            #     else:
            #         pricepurchaseforecast.append( 50 * self.my_simulation_parameters.seconds_per_timestep / 3.6e6 )
        else:
            priceinjectionforecast = [0.1]
            pricepurchaseforecast = [0.5]

        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.Price_Injection_Forecast_24h,
            entry=priceinjectionforecast
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.Price_Purchase_Forecast_24h,
            entry=pricepurchaseforecast
        )
        stsv.set_output_value(self.PricePurchaseC, pricepurchaseforecast[0])
        stsv.set_output_value(self.PriceInjectionC, priceinjectionforecast[0])

    def build_dummy(self, start: int, end: int) -> None:
        """Initialization of information if step function is used for prices."""
        self.start = start
        self.end = end

    def i_prepare_simulation(self) -> None:
        """Prepares the simulation."""
        PricePurchase = pd.read_csv(
            os.path.join(utils.HISIMPATH["price_signal"]["PricePurchase"]),
            index_col=0,
        )
        FeedInTarrif = pd.read_csv(
            os.path.join(utils.HISIMPATH["price_signal"]["FeedInTarrif"]),
            index_col=0,
        )

        if "Fixed_Price_" + self.price_signal_config.country in PricePurchase:
            self.price_signal_config.price_signal_type = "Prices at second half of 2021"
            fixed_price = PricePurchase[
                "Fixed_Price_" + self.price_signal_config.country
            ].tolist()
            # convert euro/kWh to cent/kW-timestep
            p_conversion = 100 / (
                1000 * 3600 / self.my_simulation_parameters.seconds_per_timestep
            )
            fixed_price = [element * p_conversion for element in fixed_price]
            self.price_signal_config.fixed_price = np.repeat(
                fixed_price,
                int(3600 / self.my_simulation_parameters.seconds_per_timestep),
            ).tolist()

            static_tou_price = PricePurchase[
                "Static_TOU_Price_" + self.price_signal_config.country
            ].tolist()
            static_tou_price = [element * p_conversion for element in static_tou_price]
            self.price_signal_config.static_tou_price = np.repeat(
                static_tou_price,
                int(3600 / self.my_simulation_parameters.seconds_per_timestep),
            ).tolist()

            FITdata = FeedInTarrif.loc[self.price_signal_config.country]
            for i in range(len(FITdata)):
                if (
                    FITdata["min_capacity (kW)"].values[i]
                    < self.price_signal_config.installed_capacity
                    and FITdata["max_capacity (kW)"].values[i]
                    >= self.price_signal_config.installed_capacity
                ):
                    price_injection = FITdata["FIT"].values[i]
            self.price_signal_config.price_injection = price_injection * p_conversion
        pass

    def write_to_report(self) -> List[str]:
        """Writes relevant information to report."""
        return self.price_signal_config.get_string_dict()
