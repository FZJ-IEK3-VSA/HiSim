# -*- coding: utf-8 -*-
"""
This postprocessing option computes overoll consumption, production, self-consumption and injection
as well as self consumption rate and autarky rate""" 

from typing import List
import pandas as pd

from hisim.component import ComponentOutput
from hisim.simulationparameters import SimulationParameters

#sum consumption and production of individual components
def compute_KPIs(results: pd.DataFrame, all_outputs: List[ComponentOutput], simulation_parameters: SimulationParameters):
    results[ 'consumption' ] = 0
    results[ 'production' ] = 0
    results[ 'storage' ] = 0
    index: int
    output: ComponentOutput
    
    # replace that loop by searching for flags -> include also battery things and hydrogen things
    # flags for Postprocessing: cp.ComponentOutput.postprocessing_flag
    # flags for ComponentTypes: cp.ComponentOutput.component_type
    # flags for LoadTypes: cp.ComponentOutput.load_type
    # flags for Units: cp.ComponentOutput.unit
    for index, output in enumerate(all_outputs):
        if 'ElectricityOutput' in output.full_name:
            if ( 'PVSystem' in output.full_name) or ('CHP' in output.full_name) :
                results[ 'production' ] = results[ 'production' ] + results.iloc[:, index]
            else:
                results[ 'consumption' ] = results[ 'consumption' ] + results.iloc[:, index]
        elif 'AcBatteryPower' in output.full_name:
            results[ 'storage' ] = results[ 'storage' ] + results.iloc[:, index]
        else:
            continue
     
    #sum over time make it more clear and better
    consumption_sum = results[ 'consumption' ].sum( ) * simulation_parameters.seconds_per_timestep / 3.6e6
    production_sum = results[ 'production' ].sum( ) * simulation_parameters.seconds_per_timestep / 3.6e6
    
    if production_sum > 0:
        #evaluate injection, sum over time
        injection = ( results[ 'production' ] - results[ 'storage' ] - results[ 'consumption' ] ) 
        injection_sum = injection[ injection > 0 ].sum( ) * simulation_parameters.seconds_per_timestep / 3.6e6
        
        battery_losses = results[ 'storage' ].sum( ) * simulation_parameters.seconds_per_timestep / 3.6e6
        self_consumption_sum = production_sum - injection_sum - battery_losses
    else:
        self_consumption_sum = 0
        injection_sum = 0
        battery_losses = 0
    h2_system_losses = 0  # explicitly compute that
        
    if production_sum > 0:
        #evaluate electricity price
        if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in results:
            price = - ( ( injection[ injection < 0 ] * results[ 'PriceSignal - PricePurchase [Price - Cents per kWh]' ][ injection < 0 ] ).sum( ) \
                    + ( injection[ injection > 0 ] * results[ 'PriceSignal - PriceInjection [Price - Cents per kWh]' ][ injection > 0 ]).sum( ) ) \
                    * simulation_parameters.seconds_per_timestep / 3.6e6
    else:
        if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in results:
            price = ( results[ 'consumption' ] * results[ 'PriceSignal - PricePurchase [Price - Cents per kWh]' ] ).sum( ) \
                * simulation_parameters.seconds_per_timestep / 3.6e6
            lines.append( "Price paid for electricity: {:3.0f} EUR".format( price *1e-2 ) )
    
    #initilize lines for report
    lines = []
    lines.append("Consumption: {:4.0f} kWh".format(consumption_sum))
    lines.append("Production: {:4.0f} kWh".format(production_sum))
    lines.append("Self-Consumption: {:4.0f} kWh".format(self_consumption_sum))
    lines.append("Injection: {:4.0f} kWh".format(injection_sum))
    lines.append("Battery losses: {:4.0f} kWh".format(battery_losses))
    lines.append("Battery content: {:4.0f} kWh".format(0))
    lines.append("Hydrogen system losses: {:4.0f} kWh".format(h2_system_losses))
    lines.append("Hydrogen storage content: {:4.0f} kWh".format(0))
    lines.append("Autarky Rate: {:3.1f} %".format( 100 * (self_consumption_sum / consumption_sum) ))
    lines.append("Self Consumption Rate: {:3.1f} %".format( 100 * (self_consumption_sum / production_sum ) ))
    lines.append("Price paid for electricity: {:3.0f} EUR".format(price *1e-2))
    
    return lines