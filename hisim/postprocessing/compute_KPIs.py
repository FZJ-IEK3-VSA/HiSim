# -*- coding: utf-8 -*-
"""
This postprocessing option computes overoll consumption, production, self-consumption and injection
as well as self consumption rate and autarky rate""" 

import os

import numpy as np
from typing import List, Any
import pandas as pd

from hisim.loadtypes import InandOutputType
from hisim.component import ComponentOutput
from hisim.simulationparameters import SimulationParameters
#from hisim.postprocessing.postprocessing_main import PostProcessingDataTransfer



#sum consumption and production of individual components

def compute_KPIs(results: pd.DataFrame, all_outputs: List[ComponentOutput], simulation_parameters: SimulationParameters)-> Any:


        #self.kpis_values_list=[consumption_sum, production_sum,self_consumption_sum,injection_sum,battery_losses,h2_system_losses,autarky_rate,self_consumption_rate,price]

    results[ 'consumption' ] = 0
    results[ 'production' ] = 0
    results[ 'storage' ] = 0
    index: int
    output: ComponentOutput
    
    # replace that loop by searching for flags -> include also battery things and hydrogen things
    # flags for Postprocessing: cp.ComponentOutput.postprocessing_flag -> loadtpyes.InandOutputType : Consumption, Production, StorageContent, ChargeDischarge
    # CHARGE_DISCHARGE from battery has + and - sign and is production and consumption both in one output
    # heat production of heat pump has - sign in summer, either separate or take absolute value
    # flags for ComponentTypes: cp.ComponentOutput.component_type
    # flags for LoadTypes: cp.ComponentOutput.load_type
    # flags for Units: cp.ComponentOutput.unit

    #old method usig keywords to find according ouputs
    #for index, output in enumerate(all_outputs):
    #    #print(output.postprocessing_flag)
    #    if 'ElectricityOutput' in output.full_name:
    #        if ( 'PVSystem' in output.full_name) or ('CHP' in output.full_name) :
    #            results[ 'production' ] = results[ 'production' ] + results.iloc[:, index]
    #        else:
    #            results[ 'consumption' ] = results[ 'consumption' ] + results.iloc[:, index]
    #    elif 'AcBatteryPower' in output.full_name:
    #        results[ 'storage' ] = results[ 'storage' ] + results.iloc[:, index]
    #    else:
    #        continue
        
        
        
     #new method using flags to find according outputs

    for index, output in enumerate(all_outputs):
    
        if output.postprocessing_flag!=None:
            #print(output.postprocessing_flag,output.full_name)    
            if (InandOutputType.PRODUCTION in output.postprocessing_flag):
                print("Ich werde an die Production results Spalte angehängt:",output.postprocessing_flag,output.full_name,"INDEX:",index )
                results[ 'production' ] = results[ 'production' ] + results.iloc[:, index]

                
    
            elif (InandOutputType.CONSUMPTION in output.postprocessing_flag):
                print("Ich werde an die Consumption results Spalte angehängt:",output.postprocessing_flag,output.full_name,"INDEX:",index )
                
                results[ 'consumption' ] = results[ 'consumption' ] + results.iloc[:, index]
                
            elif (InandOutputType.STORAGE_CONTENT in output.postprocessing_flag):
                results[ 'storage' ] = results[ 'storage' ] + results.iloc[:, index] 
                print("Ich werde an die Storage results Spalte angehängt:",output.postprocessing_flag,output.full_name,"INDEX:",index)  
                    
            elif (InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag):
                print("Ich bin eine Batterie und werde wenn ich positiv bin an consumption angehängt:",output.postprocessing_flag,output.full_name ,"INDEX:",index)
                   
                #results[ 'consumption' ] = results[ 'consumption' ] + results[results.iloc[:, index] < 0]
                #print("Kleiner Null:",results[results.iloc[:, index] < 0])
                neg_battery=results[results.iloc[:, index] < 0].iloc[:,index]
                #neudf=neudf.iloc[:,index]
                pos_battery=results[results.iloc[:, index] > 0].iloc[:,index]
                #print("Negative Batteriewerte:",neg_battery)
                #print("Positive Batteriewerte:",pos_battery)
                #print("Gleich Null:",results[results.iloc[:, index] == 0])
                #print(results.iloc[:, index])alle
                
                results["pos_battery"]=results.iloc[:,index].tolist()
                #Replace negative values with zero
                results["pos_battery"].values[results["pos_battery"]<0]=0 
                results[ 'consumption' ] = results[ 'consumption' ] + results["pos_battery"]
                
                results["neg_battery"]=results.iloc[:,index].tolist()
                #Replace positve values with zero
                results["neg_battery"].values[results["neg_battery"]>0]=0 
                results[ 'production' ] = results[ 'production' ] + results["neg_battery"] 
               
                
               
                            
                    
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
        
    
    
    #Electricity Price
    if production_sum > 0:
        #evaluate electricity price
        if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in results:
            price = - ( ( injection[ injection < 0 ] * results[ 'PriceSignal - PricePurchase [Price - Cents per kWh]' ][ injection < 0 ] ).sum( ) \
                    + ( injection[ injection > 0 ] * results[ 'PriceSignal - PriceInjection [Price - Cents per kWh]' ][ injection > 0 ]).sum( ) ) \
                    * simulation_parameters.seconds_per_timestep / 3.6e6
        else:
            price = 0
        self_consumption_rate = 100 * (self_consumption_sum / production_sum)
        autarky_rate = 100 * (self_consumption_sum / consumption_sum)
    else:
        if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in results:
            price = ( results[ 'consumption' ] * results[ 'PriceSignal - PricePurchase [Price - Cents per kWh]' ] ).sum( ) \
                * simulation_parameters.seconds_per_timestep / 3.6e6
        else:
            price = 0
        self_consumption_rate = 0
        autarky_rate = 0

    #initilize lines for report
    lines: List = []
    lines.append("Consumption: {:4.0f} kWh".format(consumption_sum))
    lines.append("Production: {:4.0f} kWh".format(production_sum))
    lines.append("Self-Consumption: {:4.0f} kWh".format(self_consumption_sum))
    lines.append("Injection: {:4.0f} kWh".format(injection_sum))
    lines.append("Battery losses: {:4.0f} kWh".format(battery_losses))
    lines.append("Battery content: {:4.0f} kWh".format(0))
    lines.append("Hydrogen system losses: {:4.0f} kWh".format(h2_system_losses))
    lines.append("Hydrogen storage content: {:4.0f} kWh".format(0))
    lines.append("Autarky Rate: {:3.1f} %".format(autarky_rate))
    lines.append("Self Consumption Rate: {:3.1f} %".format(self_consumption_rate))
    lines.append("Price paid for electricity: {:3.0f} EUR".format(price *1e-2)) 
    
    kpis_list =["Consumption:","Production:","Self consumption:","Injection:","Battery losses:","Hydrogen system losses:","Autarky Rate:","Self Consumption Rate:","Price paid for electricity:"]
    kpis_values_list=[consumption_sum, production_sum,self_consumption_sum,injection_sum,battery_losses,h2_system_losses,autarky_rate,self_consumption_rate,price]

    
    return lines, kpis_list,kpis_values_list

#class KPIComputation(kpis_list,kpis_values_list):  # noqa: too-few-public-methods
    #    """ Data class for transfering the result data to this class. """
      #  kpis_list=kpis_list
       # kpis_values_list=compute_KPIs.kpis_values_list
        
