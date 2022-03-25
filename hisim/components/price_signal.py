# Owned
from hisim import component as cp
from hisim.simulationparameters import SimulationParameters

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

    def __init__( self,
                  my_simulation_parameters: SimulationParameters ):
        super( ).__init__( name = "PriceSignal", my_simulation_parameters = my_simulation_parameters )

        self.build_dummy( start = int( 10 * 3600 / my_simulation_parameters.seconds_per_timestep ), 
                          end = int( 16 * 3600 / my_simulation_parameters.seconds_per_timestep ) )
        
        # Outputs
        self.PricePurchaseC: cp.ComponentOutput = self.add_output( self.ComponentName,
                                                                   self.PricePurchase,
                                                                   lt.LoadTypes.Price,
                                                                   lt.Units.c_per_kWh )

    def i_save_state(self):
        pass

    def i_restore_state(self):
        pass

    def i_doublecheck(self, timestep: int, stsv: cp.SingleTimeStepValues):
        pass

    def i_simulate( self, timestep: int, stsv: cp.SingleTimeStepValues,  force_conversion: bool ):
        priceinjectionforecast = [ 10  ] * self.day
        pricepurchaseforecast = [ 50  ] * self.day
        # pricepurchaseforecast = [ ]
        # for step in range( self.day ):
        #     x = timestep % self.day
        #     if x > self.start and x < self.end:
        #         pricepurchaseforecast.append( 20 * self.my_simulation_parameters.seconds_per_timestep / 3.6e6 )
        #     else:
        #         pricepurchaseforecast.append( 50 * self.my_simulation_parameters.seconds_per_timestep / 3.6e6 )
        
        self.simulation_repository.set_entry( self.Price_Injection_Forecast_24h, priceinjectionforecast )
        self.simulation_repository.set_entry( self.Price_Purchase_Forecast_24h, pricepurchaseforecast )
        stsv.set_output_value( self.PricePurchaseC, pricepurchaseforecast[ 0 ] )

    def build_dummy( self, start : int, end: int ):
        self.start = start
        self.end = end
        self.day = int( 24 * 3600 / self.my_simulation_parameters.seconds_per_timestep )

    def write_to_report(self):
        lines = []
        lines.append( "Price signal: {}".format( "dummy" ) )
        return lines
