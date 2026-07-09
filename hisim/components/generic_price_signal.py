"""Dummy class and configuration returning electricity prices (from grid) and returns (injection) in each time step. """

from __future__ import annotations

# clean

import os
from typing import List, Optional, cast
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import pandas as pd
import numpy as np

# Owned
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
    """Configuration for the PriceSignal component.

    Holds all parameters required to configure the price signal component:
    the building name it applies to, the country (used to select the
    electricity-price data set), the pricing scheme (e.g. "fixed"), the
    installed capacity used for feed-in tariff selection, the price-signal
    type, the fixed and static time-of-use (TOU) price arrays, the price paid
    for grid injection, and the flags/horizon for predictive control.
    """

    @classmethod
    def get_main_classname(cls) -> str:
        """Return the fully-qualified class name of the PriceSignal component.

        Returns:
            The full class name string used by the framework to identify the
            component type associated with this configuration.
        """
        return cast(str, PriceSignal.get_full_classname())

    building_name: str
    #: name of the price signal
    name: str
    country: str
    pricing_scheme: str
    installed_capacity: float
    price_signal_type: str
    fixed_price: List[float]
    static_tou_price: List[float]
    price_injection: float
    predictive_control: bool
    prediction_horizon: Optional[int]

    @classmethod
    def get_default_price_signal_config(
        cls,
        building_name: str = "BUI1",
    ) -> PriceSignalConfig:
        """Return a default PriceSignalConfig for the given building.

        Args:
            building_name: Identifier of the building the price signal applies to.

        Returns:
            A PriceSignalConfig with German fixed pricing defaults, 10 kW installed
            capacity, dummy price-signal type, and predictive control disabled.
        """
        config = PriceSignalConfig(
            building_name=building_name,
            name="PriceSignal",
            country="Germany",
            pricing_scheme="fixed",
            installed_capacity=10e3,
            price_signal_type="dummy",
            fixed_price=[],
            static_tou_price=[],
            price_injection=0.0,
            predictive_control=False,
            prediction_horizon=None,
        )
        return config


class PriceSignal(cp.Component):
    """Price Signal class.

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
        self,
        my_simulation_parameters: SimulationParameters,
        config: PriceSignalConfig,
        my_display_config: cp.DisplayConfig = cp.DisplayConfig(),
    ) -> None:
        """Initialize the PriceSignal component.

        Args:
            my_simulation_parameters: Simulation parameters providing timestep info.
            config: PriceSignalConfig with pricing scheme and price data.
            my_display_config: Display configuration for the component.
        """
        self.price_signal_config = config
        self.my_simulation_parameters = my_simulation_parameters
        self.config = config
        component_name = self.get_component_name()
        super().__init__(
            name=component_name,
            my_simulation_parameters=my_simulation_parameters,
            my_config=config,
            my_display_config=my_display_config,
        )

        self.build_dummy(
            start=int(10 * 3600 / my_simulation_parameters.seconds_per_timestep),
            end=int(16 * 3600 / my_simulation_parameters.seconds_per_timestep),
        )

        # Outputs
        self.price_purchase_channel: cp.ComponentOutput = self.add_output(
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
        self.price_injection_channel: cp.ComponentOutput = self.add_output(
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
        """No-op; PriceSignal holds no mutable state across timesteps."""
        pass

    def i_restore_state(self) -> None:
        """No-op; PriceSignal holds no mutable state across timesteps."""
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        """No-op; no consistency checks are needed for this component.

        Args:
            timestep: Index of the current simulation time step (unused).
            stsv: Single-time-step value container (unused).
        """
        pass

    def i_simulate(self, timestep: int, stsv: cp.SingleTimeStepValues, force_convergence: bool) -> None:
        """Provide the electricity purchase and injection prices for the timestep.

        Depending on the configured pricing scheme, price-signal type, and
        predictive-control settings, sets the purchase and injection price
        outputs for the current step and, when predictive control is enabled,
        stores the 24 h price forecasts in the shared simulation repository.

        Args:
            timestep: Index of the current simulation time step.
            stsv: Single-time-step value container used to set the outputs.
            force_convergence: Unused; kept for the component interface.
        """
        priceinjectionforecast = [0.1]
        pricepurchaseforecast = [0.5]
        if self.config.predictive_control and self.config.prediction_horizon:
            priceinjectionforecast = [0.1] * int(
                self.config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
            pricepurchaseforecast = [0.5] * int(
                self.config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
        elif self.price_signal_config.price_signal_type == "Prices at second half of 2021":
            priceinjectionforecast = [self.price_signal_config.price_injection] * int(
                self.config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
        elif self.price_signal_config.pricing_scheme == "dynamic":
            pricepurchaseforecast = self.price_signal_config.static_tou_price
        elif self.price_signal_config.pricing_scheme == "fixed":
            pricepurchaseforecast = self.price_signal_config.fixed_price
        elif self.price_signal_config.price_signal_type == "dummy":
            priceinjectionforecast = [10] * int(
                self.config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
            )
            pricepurchaseforecast = [50] * int(
                self.config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep
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
            key=SingletonDictKeyEnum.PRICEINJECTIONFORECAST24H,
            entry=priceinjectionforecast,
        )
        SingletonSimRepository().set_entry(
            key=SingletonDictKeyEnum.PRICEPURCHASEFORECAST24H,
            entry=pricepurchaseforecast,
        )
        stsv.set_output_value(self.price_purchase_channel, pricepurchaseforecast[0])
        stsv.set_output_value(self.price_injection_channel, priceinjectionforecast[0])

    def build_dummy(self, start: int, end: int) -> None:
        """Store the start and end timestep indices of a step-function price window.

        Used when a dummy step-function price signal is enabled; the stored
        `start` and `end` attributes delimit the high-price window within a day.

        Args:
            start: Timestep index marking the beginning of the high-price window.
            end: Timestep index marking the end of the high-price window.
        """
        self.start = start
        self.end = end

    def i_prepare_simulation(self) -> None:
        """Load price data from CSV files and populate the config with per-timestep prices.

        Reads the purchase-price and feed-in-tariff CSV tables for the configured
        country, converts EUR/kWh values to cent per kW-timestep, expands hourly
        prices to the simulation timestep resolution, and selects the feed-in
        tariff band matching `installed_capacity`. Results are stored back on
        `self.price_signal_config` (fixed_price, static_tou_price, price_injection,
        and price_signal_type).

        Raises:
            KeyError: If the feed-in-tariff table has no entry for the configured
                country (via the ``.loc`` lookup), or if the
                "Static_TOU_Price_<country>" column is missing from the
                price-signal data. Note: if "Fixed_Price_<country>" is absent,
                the method silently returns without populating any prices.
        """
        price_purchase = pd.read_csv(
            os.path.join(utils.HISIMPATH["price_signal"]["PricePurchase"]),
            index_col=0,
        )
        feed_in_tarrif = pd.read_csv(
            os.path.join(utils.HISIMPATH["price_signal"]["FeedInTarrif"]),
            index_col=0,
        )

        if "Fixed_Price_" + self.price_signal_config.country in price_purchase:
            self.price_signal_config.price_signal_type = "Prices at second half of 2021"
            fixed_price = price_purchase["Fixed_Price_" + self.price_signal_config.country].tolist()
            # convert euro/kWh to cent/kW-timestep
            p_conversion = 100 / (1000 * 3600 / self.my_simulation_parameters.seconds_per_timestep)
            fixed_price = [element * p_conversion for element in fixed_price]
            self.price_signal_config.fixed_price = np.repeat(
                fixed_price,
                int(3600 / self.my_simulation_parameters.seconds_per_timestep),
            ).tolist()

            static_tou_price = price_purchase["Static_TOU_Price_" + self.price_signal_config.country].tolist()
            static_tou_price = [element * p_conversion for element in static_tou_price]
            self.price_signal_config.static_tou_price = np.repeat(
                static_tou_price,
                int(3600 / self.my_simulation_parameters.seconds_per_timestep),
            ).tolist()

            fit_data = feed_in_tarrif.loc[self.price_signal_config.country]
            price_injection = 0.0
            for i in range(len(fit_data)):
                if (
                    fit_data["min_capacity (kW)"].values[i] < self.price_signal_config.installed_capacity
                    and fit_data["max_capacity (kW)"].values[i] >= self.price_signal_config.installed_capacity
                ):
                    price_injection = fit_data["FIT"].values[i]
            self.price_signal_config.price_injection = price_injection * p_conversion
        pass

    def write_to_report(self) -> List[str]:
        """Return the price-signal configuration as a list of report strings.

        Returns:
            A list of human-readable strings describing the current
            `PriceSignalConfig`, obtained from `self.price_signal_config.get_string_dict()`.
        """
        return self.price_signal_config.get_string_dict()
