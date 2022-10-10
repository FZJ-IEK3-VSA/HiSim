"""Postprocessing option computes overall consumption, production,self-consumption and injection as well as selfconsumption rate and autarky rate."""

from typing import List, Any
import pandas as pd
import hisim.log
from hisim.loadtypes import InandOutputType
from hisim.component import ComponentOutput
from hisim.simulationparameters import SimulationParameters


def compute_kpis(results: pd.DataFrame, all_outputs: List[ComponentOutput], simulation_parameters: SimulationParameters) -> Any:  # noqa: MC0001
    """Calculation of several KPIs."""
    results['consumption'] = 0
    results['production'] = 0
    results['storage'] = 0
    index: int
    output: ComponentOutput

    # replace that loop by searching for flags -> include also battery things and hydrogen things
    # flags for Postprocessing: cp.ComponentOutput.postprocessing_flag -> loadtpyes.InandOutputType :
    # Consumption, Production, StorageContent, ChargeDischarge
    # CHARGE_DISCHARGE from battery has + and - sign and is production and consumption both in one output
    # heat production of heat pump has - sign in summer, either separate or take absolute value
    # flags for ComponentTypes: cp.ComponentOutput.component_type
    # flags for LoadTypes: cp.ComponentOutput.load_type
    # flags for Units: cp.ComponentOutput.unit

    for index, output in enumerate(all_outputs):

        if output.postprocessing_flag is not None:
            if (InandOutputType.ELECTRICITY_PRODUCTION in output.postprocessing_flag):
                hisim.log.information("Ich werde an die Production results Spalte angehängt:" + output.postprocessing_flag[0] + output.full_name + "INDEX:" + str(index) )
                results['production'] = results[ 'production'] + results.iloc[:, index]

                
    
            elif (InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED in output.postprocessing_flag) or InandOutputType.ELECTRICITY_CONSUMPTION_UNCONTROLLED in output.postprocessing_flag:
                hisim.log.information("I am appended to consumption column:" + output.postprocessing_flag[0] + output.full_name + "INDEX:" + str(index) )
                
                results['consumption'] = results['consumption'] + results.iloc[:, index]
                
            elif (InandOutputType.STORAGE_CONTENT in output.postprocessing_flag):
                results[ 'storage' ] = results[ 'storage' ] + results.iloc[:, index] 
                hisim.log.information("I am appended to storage column:" + output.postprocessing_flag[0] + output.full_name + "INDEX:" + str(index))  
                    
            elif (InandOutputType.CHARGE_DISCHARGE in output.postprocessing_flag):
                hisim.log.information("I am a battery, when positiv added to consumption and negative to production column:" + output.postprocessing_flag[0] + output.full_name + "INDEX:" + str(index))
              
                results["pos_battery"]=results.iloc[:,index].tolist()

                #Replace negative values with zero
                results["pos_battery"].clip(upper=0, inplace=True) 
                results[ 'consumption' ] = results[ 'consumption' ] + results["pos_battery"]
              
                results["neg_battery"]=results.iloc[:,index].tolist()
                #Replace positve values with zero
                results["neg_battery"].clip(lower=0, inplace=True)

                results[ 'production' ] = results[ 'production' ] + results["neg_battery"] 
                results=results.drop(['neg_battery', 'pos_battery'], axis=1)
        else:
            continue

    # sum over time make it more clear and better
    consumption_sum = results['consumption'].sum() * simulation_parameters.seconds_per_timestep / 3.6e6
    production_sum = results['production'].sum() * simulation_parameters.seconds_per_timestep / 3.6e6

    if production_sum > 0:
        # evaluate injection, sum over time
        injection = (results['production'] - results['storage'] - results['consumption'])
        injection_sum = injection[injection > 0].sum() * simulation_parameters.seconds_per_timestep / 3.6e6

        battery_losses = results['storage'].sum() * simulation_parameters.seconds_per_timestep / 3.6e6
        self_consumption_sum = production_sum - injection_sum - battery_losses
    else:
        self_consumption_sum = 0
        injection_sum = 0
        battery_losses = 0
    h2_system_losses = 0  # explicitly compute that

    # Electricity Price
    if production_sum > 0:
        # evaluate electricity price
        if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in results:
            price = - ((injection[injection < 0] * results['PriceSignal - PricePurchase [Price - Cents per kWh]'][injection < 0]).sum()
                       + (injection[injection > 0] * results['PriceSignal - PriceInjection [Price - Cents per kWh]'][injection > 0]).sum())\
                * simulation_parameters.seconds_per_timestep / 3.6e6
        else:
            price = 0
        self_consumption_rate = 100 * (self_consumption_sum / production_sum)
        autarky_rate = 100 * (self_consumption_sum / consumption_sum)
    else:
        if 'PriceSignal - PricePurchase [Price - Cents per kWh]' in results:
            price = (results['consumption'] * results['PriceSignal - PricePurchase [Price - Cents per kWh]']).sum() \
                * simulation_parameters.seconds_per_timestep / 3.6e6
        else:
            price = 0
        self_consumption_rate = 0
        autarky_rate = 0

    # initilize lines for report
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
    lines.append("Price paid for electricity: {:3.0f} EUR".format(price * 1e-2))

    # initialize list for the KPI.scv
    kpis_list = ["Consumption:", "Production:", "Self consumption:", "Injection:", "Battery losses:",
                 "Hydrogen system losses:", "Autarky Rate:", "Self Consumption Rate:", "Price paid for electricity:"]
    kpis_values_list = [consumption_sum, production_sum, self_consumption_sum, injection_sum, battery_losses,
                        h2_system_losses, autarky_rate, self_consumption_rate, price]

    return lines, kpis_list, kpis_values_list
