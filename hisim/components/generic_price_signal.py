import numpy as np
import pandas as pd
import os                  
# Owned
from typing import  List
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters
from hisim import utils
from hisim import loadtypes as lt

__authors__ = "Johanna Ganglbauer"
__copyright__ = "Copyright 2021, the House Infrastructure Project"
__credits__ = ["Noah Pflugradt"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Vitor Hugo Bellotto Zago"
__email__ = "vitor.zago@rwth-aachen.de"
__status__ = "development"


class PriceSignal(cp.Component):
    """
    Class component that provides price for electricity.

    Parameters
    -----------------------------------------------
    profile: string
        profile code corresponded to the family or residents configuration

    ComponentInputs:
    -----------------------------------------------
       None

    ComponentOutputs:
    -----------------------------------------------
       Price for injection: cents/kWh
       Price for purchase: cents/kWh
    """
    #Forecasts
    Price_Injection_Forecast_24h = "Price_Injection_Forecast_24h"
    Price_Purchase_Forecast_24h = "Price_Purchase_Forecast_24h"
    
    #Outputs
    PricePurchase = 'PricePurchase'
    PriceInjection = 'PriceInjection'

    def __init__( self, my_simulation_parameters: SimulationParameters, country:str = 'Germany',pricing_scheme: str = 'fixed',installed_capcity:float= 10E3 ) -> None:
        super( ).__init__( name = "PriceSignal", my_simulation_parameters = my_simulation_parameters )
        self.country=country
        self.pricing_scheme=pricing_scheme
        self.installed_capcity: float = (installed_capcity)/1000  # converting from W to kW 
        self.price_signal_type='dummy'                    
        self.build_dummy( start = int( 10 * 3600 / my_simulation_parameters.seconds_per_timestep ), 
                          end = int( 16 * 3600 / my_simulation_parameters.seconds_per_timestep ) )
        
        # Outputs
        self.PricePurchaseC: cp.ComponentOutput = self.add_output(self.component_name,
                                                                  self.PricePurchase,
                                                                  lt.LoadTypes.PRICE,
                                                                  lt.Units.CENTS_PER_KWH)
        self.PriceInjectionC: cp.ComponentOutput = self.add_output(self.component_name,
                                                                   self.PriceInjection,
                                                                   lt.LoadTypes.PRICE,
                                                                   lt.Units.CENTS_PER_KWH)

    def i_save_state(self) -> None:
        pass

    def i_restore_state(self) -> None:
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues) -> None:
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_conversion: bool ) -> None:
        if self.price_signal_type =='Prices at second half of 2021':
            priceinjectionforecast= [self.price_injection ] * int( self.my_simulation_parameters.system_config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep )
            if self.pricing_scheme=='dynamic':
                pricepurchaseforecast=self.static_tou_price
            if self.pricing_scheme=='fixed':
                pricepurchaseforecast=self.fixed_price
        elif self.price_signal_type =='dummy':
            priceinjectionforecast = [ 10  ] * int( self.my_simulation_parameters.system_config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep )
            pricepurchaseforecast = [ 50  ] * int( self.my_simulation_parameters.system_config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep )
            # pricepurchaseforecast = [ ]
            # for step in range( self.day ):
            #     x = timestep % self.day
            #     if x > self.start and x < self.end:
            #         pricepurchaseforecast.append( 20 * self.my_simulation_parameters.seconds_per_timestep / 3.6e6 )
            #     else:
            #         pricepurchaseforecast.append( 50 * self.my_simulation_parameters.seconds_per_timestep / 3.6e6 )
        
        self.simulation_repository.set_entry( self.Price_Injection_Forecast_24h, priceinjectionforecast )
        self.simulation_repository.set_entry( self.Price_Purchase_Forecast_24h, pricepurchaseforecast )
        stsv.set_output_value( self.PricePurchaseC, pricepurchaseforecast[ timestep%int( self.my_simulation_parameters.system_config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep ) ] )
        stsv.set_output_value( self.PriceInjectionC, priceinjectionforecast[ timestep%int( self.my_simulation_parameters.system_config.prediction_horizon / self.my_simulation_parameters.seconds_per_timestep ) ] )

    def build_dummy( self, start : int, end: int ) -> None:
        self.start = start
        self.end = end

    def i_prepare_simulation(self) -> None:
        """ Prepares the simulation. """
        PricePurchase = pd.read_csv(os.path.join(utils.HISIMPATH["price_signal"]["PricePurchase"]), index_col=0, )
        FeedInTarrif = pd.read_csv(os.path.join(utils.HISIMPATH["price_signal"]["FeedInTarrif"]), index_col=0, )
        
        if "Fixed_Price_" + self.country in PricePurchase:
            self.price_signal_type='Prices at second half of 2021'
            fixed_price=PricePurchase["Fixed_Price_" + self.country].tolist()
            # convert euro/kWh to cent/kW-timestep
            p_conversion= 100 / (1000 * 3600/self.my_simulation_parameters.seconds_per_timestep)
            fixed_price = [element * p_conversion for element in fixed_price]
            self.fixed_price=np.repeat(fixed_price, int(3600/self.my_simulation_parameters.seconds_per_timestep)).tolist()
            
            static_tou_price=PricePurchase["Static_TOU_Price_" + self.country].tolist()
            static_tou_price = [element * p_conversion for element in static_tou_price]
            self.static_tou_price=np.repeat(static_tou_price, int(3600/self.my_simulation_parameters.seconds_per_timestep)).tolist()
            
            FITdata=FeedInTarrif.loc[self.country]
            for i in range(len(FITdata)):
                if FITdata['min_capacity (kW)'].values[i] < self.installed_capcity and FITdata['max_capacity (kW)'].values[i] >= self.installed_capcity:
                    price_injection=FITdata['FIT'].values[i]
            self.price_injection=price_injection * p_conversion
        
    def write_to_report(self) -> List[str]:
        lines = []
        lines.append( "Price signal: {}".format( self.price_signal_type ) )
        return lines
