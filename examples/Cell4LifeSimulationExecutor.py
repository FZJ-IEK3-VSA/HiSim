import pandas as pd
import os
import sys
import graphviz
os.chdir('C://Users//Standard//Desktop//hisim//HiSim//')
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//examples")
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//")
import Cell4LifeSzenario1
from hisim.result_path_provider import ResultPathProviderSingleton
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//hisim//postprocessing//")
os.chdir("C://Users//Standard//Desktop//hisim//HiSim//hisim//postprocessing//")
import Cell4Life_Postprocessing
os.chdir('C://Users//Standard//Desktop//hisim//HiSim//')
import math


"""This class executes Cell4Life Simulation
    
    -) "Variation Parameters": Parameters, which should be variied 
 
    -) Other "static parameters" are seen as static and defined in "Cell4Life-Model-_d3.py" example


 """

#FuelCellPowerW_list = [200000]  #Electricity Power of Fuel Cell Power in Watt
#BatteryCapkWh_list = [0]     #Total Capacity of Battery in kWh


#FuelCellPowerW_list = [200000, 100000, 50000, 25000, 12500]  #Electricity Power of Fuel Cell Power in Watt
FuelCellPowerW_list = [200000, 100000]  #Electricity Power of Fuel Cell Power in Watt
Inverter_Ratio_list = [0.5, 0.333, 0.25, 0.2,0.1666] #Means: Inverter_power_demand  = Battery capacity multiplied with a factor of the list; Battery Capacity = BatterieFaktor * (electrolyzer_energy + h2 storage)

FuelCellPowerWUnit = "W"
BatteryCapkWhUnit = "kWh"
Inverter_RatioUnit = "-"

# PreResultNumber = 0
# PreResultNumberUnit = "-"

BatterieFaktorList = [4,5,6,7,8]


for BatterieFaktor in BatterieFaktorList:

    PreResultNumber = 0
    PreResultNumberUnit = "-"

    for FuelCellPowerW in FuelCellPowerW_list:
    #    for BatteryCapkWh in BatteryCapkWh_list:
        for Inverter_Ratio in Inverter_Ratio_list:
            


            # Lege neue Werte für Parameter in config_vars fest
            param_df = pd.read_csv("examples/params_to_loop.csv", sep=",")
            param_df["PreResultNumber"][0] = PreResultNumber
            param_df["PreResultNumberUnit"][0] = PreResultNumberUnit
            param_df["FuelCellPowerW"][0] = FuelCellPowerW
            param_df["FuelCellPowerWUnit"][0] = FuelCellPowerWUnit
            #param_df["BatteryCapkWh"][0] = BatteryCapkWh
            #param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
            param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
            
                    
            input_variablen = Cell4LifeSzenario1.InputParameter()
            charging_rate = input_variablen["p_el_elektrolyzer"]["value"] / (3600*40000) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
            power_demand_charging_h2storage = charging_rate * input_variablen["h_fuel"]["value"] * 3.6e3 * 1000 * input_variablen["h2storage_energy_for_charge_based_on_massflow_h_fuel"]["value"]/100 # electricity power_demand of hydrogen storage for compression of H2 in Watt;
            inverte_power_demand_min = power_demand_charging_h2storage  + input_variablen["p_el_elektrolyzer"]["value"]
           
            BatteryCapkWh = math.ceil(inverte_power_demand_min / 1000 * BatterieFaktor) #Befehl Ceil Rundet auf; BatteryCKapazität in kWh..INverterleistung in Watt gerechnet; Minimum Inverterleistung = 0,25 der Batteriekapazität --> folglich ist die Batteriekapazität immer 4* der Inverterleistung

            
            param_df["BatteryCapkWh"][0] = BatteryCapkWh
            param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
            param_df["Inverter_Ratio"][0] = Inverter_Ratio
            param_df["Inverter_RatioUnit"][0] = Inverter_RatioUnit

            param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
            #del param_df
            sys.argv = ["hisim_main.py", "examples/Cell4LifeSzenario1.py", "Cell4Life"]

            with open("C:/Users/Standard/Desktop/hisim/HiSim/hisim/hisim_main.py") as f:        #with --> Handler--> mach etwas mit ... führe es aus...mache es wieder zu -> with kümmert sich um das :)
                exec(f.read())
            

            #Do a copy of the economic assessment excel file        
            
            if PreResultNumber == 0:
                pathbase = 'C://Users//Standard//Desktop//hisim//C4LResults//results//'
                name1 = '20231107_AllSimulationResults_Assessment_v5'
                filepath1 = pathbase + 'OriginalExcelFile//' + name1 + '.xlsx'
                copytopath1= pathbase
                excelfilepathallresults1, excel_filename1 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath1, filepath1, name1)

            copyfrompath = 'C://Users//Standard//Desktop//hisim//C4LResults//results//'
            name2 = 'Sim_Oek_Assessment_v6'
            filepath2 = copyfrompath + 'OriginalExcelFile//' + name2 + '.xlsx'
            copytopath2 = ResultPathProviderSingleton().get_result_directory_name()
            copytopath2 = copytopath2 + '//'
            
            name2 = 'S'+ str(PreResultNumber) + '_Oek_Assessment'
            excelfilepathresults, excel_filename2 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath2, filepath2, name2)
            del copytopath2, filepath2, name2,
            
            
            #Save all Data in the created excel files
            input_variablen = Cell4LifeSzenario1.InputParameter()
            Cell4Life_Postprocessing.saveInputdata(input_variablen)
            Cell4Life_Postprocessing.saveexcelforevaluations(input_variablen, excelfilepathallresults1, excel_filename1)
            Cell4Life_Postprocessing.save_data_in_excel_for_economic_assessment(input_variablen, excelfilepathresults, excel_filename2)
            del excelfilepathresults


            del input_variablen
            PreResultNumber += 1

       

print("---Parametervariation abgeschlossen---")




