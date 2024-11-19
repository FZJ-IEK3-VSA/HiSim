import pandas as pd
import os
import sys
#import graphviz
os.chdir('C://Users//Standard//Desktop//hisim//HiSim//')
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//examples")
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//")

#Import of different hisim examples for the Cell4Life project ("Gesamtmodelle" in HiSim)
import Cell4LifeSzenario1a_1b
import Cell4LifeSzenario2a
from hisim.result_path_provider import ResultPathProviderSingleton
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//hisim//postprocessing//")
os.chdir("C://Users//Standard//Desktop//hisim//HiSim//hisim//postprocessing//")
import Cell4Life_Postprocessing
import Cell4Life_ControllExcelSheet
os.chdir('C://Users//Standard//Desktop//hisim//HiSim//')
import math


"""This class executes Cell4Life Simulation
    To Do:
    # [x] Für die Öknomische Bewertung im Excel wird derzeit noch eine falsche H2 Speichergröße ins Excel gespeichert: Dieser ins Excel gespeicherte Wert gehört noch um die theoretischen Speicherverluste korrigiert! 
    # [x] im Cell4LifeScenario1a_1b.py --> Electrolyzer Fuel Cell Controller bzgl. Namen verändern;
    # [x] Hydrogen storage: Vorgabe Maximal Füllstand und Minimal-Füllstand funktioniert: Electrolyser kann Hydrogen storage weder zuviel auffüllen noch die fuel cell einen Storage minimalwert unterschreiten!  
    
    Script to Start Cell4Life "Gesamtmodell".
    Executor for Szenario: "1a", "1b" or "2a"
    The present script executes the hisim example "Cell4LifeSzenario1a_1b.py" OR "Cell4LifeSzenario2a.py" 


    ###
    Szenario 0a: Only Battery and Photovoltaik System, but no rSOC System implemeted (

    Szenario 1a: Batterie is providing energy for the complete household during the complete year and not only for electrolyzer. 
                    Electrolyzer and Fuel Cell is seasonal turned on or off (Constant running mode)
    Szenario 1b: Electrolyzer and Fuel Cell is running constant for a season (as in Szenario 1a). The difference to Szenario 1a is,
                    that if the electrolyzer is running ,the batterie is only providing energy for the electrolyzer. If the fuel cell is running,
                    the batterie is providing energy also for the complete household. Please consider, that in the economic excel file, electricity grid tarifs
                    are considered, if the batterie is providing electricity for the household due to the fact, that household means "apartment house". The electricity
                    from batterie to each appartment is running over the general grid which means, that tarifs for using the general grid must be paid. 
    Szenario 2: xxx
    ### 

    -) Parameters for automatic parameter-variations: Are prepared for variation in the present execution script
        All other parameters are assumed to be static and can be found in "Cell4LifeSzenario1a_1b.py", "Cell4LifeSzenario2a.py" or within the components.

    -) Results of simulation are saved in excel file for economic assessment: 
        For that, an excel file prepared in advance has to be saved in the path, specified within the present execution script.
        Original Excel File for economic assessment: Sim_Oek_Assessment_v7.xlsx --> can be found on dropbox: 4ER_Projekte_intern\CELL4LIFE\00_Arbeitsbereich\AP6\Auswertung_ExcelFiles\Einzelauswertung
    
    All for the simulation relevant Excel files for e.g economic assessment, which were not part of the original "hisim tool", can be found:
         4ER_Projekte_intern\CELL4LIFE\00_Arbeitsbereich\AP6\Auswertung_ExcelFiles\Einzelauswertung\


    Please copy them in folders, specified within the code, according to your local folder structure.


"""


# # #*************************************************

# #Szenario 1b

# szenario = "1b" 
# szenarioUnit = "-"
# PV_Peak_in_kW = 781  # in kW
# PV_Peak_in_W = PV_Peak_in_kW * 1000 # Umrechnung in Watt


# #FuelCellPowerW_list = [PV_Peak_in_W/(6*2.1),PV_Peak_in_W/(8*2.1), PV_Peak_in_W/(10*2.1),PV_Peak_in_W/(12*2.1),PV_Peak_in_W/(14*2.1) ]  #Electricity Power of Fuel Cell Power in Watt, depending on PV - Peak power

# #FuelCellPowerW_list = [PV_Peak_in_W/(2.1),PV_Peak_in_W/(2*2.1), PV_Peak_in_W/(4*2.1), PV_Peak_in_W/(6*2.1), PV_Peak_in_W/(8*2.1), PV_Peak_in_W/(10*2.1),PV_Peak_in_W/(12*2.1),PV_Peak_in_W/(14*2.1) ]  #Electricity Power of Fuel Cell Power in Watt, depending on PV - Peak power

# #Inverter_Ratio_list = [15,10,7,5,3,2.5,2]

# #Inverter_Ratio_list = [5, 3, 2.5, 2, 1.5, 1] #Means: Inverter_power_demand  = (electrolyzer_energy + h2 storage)*Inverter_Ratio_lis --> inverter is always able to deliver at least power demand of electrolyzer and h2 storage!

# #BatterieFaktorList = [150,100,80] 
# #BatterieFaktorList = [60,30,28,26,24,22] # Battery Capacity =Inverter Power * Batteriefaktorlist --> Batteriekapazität = Faktor * Inverterleistung

# #BatterieFaktorList = [20, 18, 16,14, 12,10, 8, 6, 4, 2, 1] # Battery Capacity =Inverter Power * Batteriefaktorlist --> Batteriekapazität = Faktor * Inverterleistung

# FuelCellPowerWUnit = "W"
# BatteryCapkWhUnit = "kWh"
# Inverter_RatioUnit = "-"

# prediction_horizon = 0
# prediction_horizonUnit = "seconds"
# if prediction_horizon != 0:
#     #Szenario 1a and 1b has no prediction integrated!
#     #So please choose a prediciton horizon of zero!
#     quit()


# # PreResultNumber = 0
# # PreResultNumberUnit = "-"

# PreResultNumber = 1000
# PreResultNumberUnit = "-"

# for BatterieFaktor in BatterieFaktorList:



#     for FuelCellPowerW in FuelCellPowerW_list:
#         FuelCellPowerW = int(math.ceil(FuelCellPowerW))
#         for Inverter_Ratio in Inverter_Ratio_list:
            


#             # Lege neue Werte für Parameter in config_vars fest
#             param_df = pd.read_csv("examples/params_to_loop.csv", sep=",")
#             param_df["PreResultNumber"][0] = PreResultNumber
#             param_df["PreResultNumberUnit"][0] = PreResultNumberUnit
#             param_df["FuelCellPowerW"][0] = FuelCellPowerW
#             param_df["FuelCellPowerWUnit"][0] = FuelCellPowerWUnit
#             #param_df["BatteryCapkWh"][0] = BatteryCapkWh
#             #param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
#             param_df["szenario"][0] = szenario
#             param_df["szenarioUnit"][0] = szenarioUnit
#             param_df["prediction_horizon"][0] = prediction_horizon
#             param_df["prediction_horizonUnit"][0] = prediction_horizonUnit


#             param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
            
                    
#             input_variablen = Cell4LifeSzenario1a_1b.InputParameter()

#             charging_rate = input_variablen["p_el_elektrolyzer"]["value"] / (3600*40000) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
#             power_demand_charging_h2storage = charging_rate * input_variablen["h_fuel"]["value"] * 3.6e3 * 1000 * input_variablen["h2storage_energy_for_charge_based_on_massflow_h_fuel"]["value"]/100 # electricity power_demand of hydrogen storage for compression of H2 in Watt;
            

#             inverte_power_demand = int(math.ceil((power_demand_charging_h2storage  + input_variablen["p_el_elektrolyzer"]["value"])*Inverter_Ratio)) # Inverterleistung hängt von
#             inverte_power_demandUnit = 'W'

#             BatteryCapkWh = int(math.ceil((inverte_power_demand/1000)*BatterieFaktor)) #Befehl Ceil Rundet auf; BatteryCKapazität in kWh...Batterie-Kapazität hängt von Elektrolyzer + H2 Storage Verbrauch ab!


#             ###
#                 # battery_inverter_power = battery_capacity*1000*Inverter_Ratio #in Watt: Batterie Inverter power is assumed to depend on Battery Capacity which is given in kWh!
#                 # battery_inverter_powerUnit = "W"

#             ###

 
#             param_df["BatteryCapkWh"][0] = BatteryCapkWh
#             param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
#             param_df["Inverter_Ratio"][0] = Inverter_Ratio
#             param_df["Inverter_RatioUnit"][0] = Inverter_RatioUnit
#             param_df["inverte_power_demand"][0] = inverte_power_demand
#             param_df["inverte_power_demandUnit"][0] = inverte_power_demandUnit

#             param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
#             #del param_df
#             sys.argv = ["hisim_main.py", "examples/Cell4LifeSzenario1a_1b.py", "Cell4Life"]
#             with open("C:/Users/Standard/Desktop/hisim/HiSim/hisim/hisim_main.py") as f:        #with --> Handler--> mach etwas mit ... führe es aus...mache es wieder zu -> with kümmert sich um das :)
#                 exec(f.read())

#             ##Do a copy of the economic assessment excel file        
#             ##All Economic Data Assessment Excel File
#             # if PreResultNumber == 0:
#             #     pathbase = 'C://Users//Standard//Desktop//hisim//results//'
#             #     name1 = '20231107_AllSimulationResults_Assessment_v5'
#             #     filepath1 = pathbase + 'OriginalExcelFile//' + name1 + '.xlsx'
#             #     copytopath1= pathbase
#             #     excelfilepathallresults1, excel_filename1 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath1, filepath1, name1)

#             #For economic assessment, create a copy of original excel file
#             copyfrompath = 'C://Users//Standard//Desktop//hisim//results//'
#             name2 = 'Sim_Oek_Assessment_v12'
#             filepath2 = copyfrompath + 'OriginalExcelFile//' + name2 + '.xlsx'
#             copytopath2 = ResultPathProviderSingleton().get_result_directory_name()
#             copytopath2 = copytopath2 + '//'
            
#             name2 = 'S'+ str(PreResultNumber) + '_Oek_Assessment'
#             excelfilepathresults2, excel_filename2 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath2, filepath2, name2)
#             del copytopath2, filepath2, name2

#             #For Excel Controll File, create a copy of original excel file
#             copyfrompath = 'C://Users//Standard//Desktop//hisim//results//'
#             name3 = 'ControllFileData-plotting'
#             filepath3 = copyfrompath + 'OriginalExcelFile//' + name3 + '.xlsx'
#             copytopath3 = ResultPathProviderSingleton().get_result_directory_name()
#             copytopath3 = copytopath3 + '//'
            
#             name3 = 'ControllFileData'
#             excelfilepathresults3, excel_filename3 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath3, filepath3, name3)
#             del copytopath3, filepath3, name3
            
            
            
#             #Save all Data in the created excel files
#             input_variablen = Cell4LifeSzenario1a_1b.InputParameter()
#             Cell4Life_Postprocessing.saveInputdata(input_variablen)
#             #Cell4Life_Postprocessing.saveexcelforevaluations(input_variablen, excelfilepathallresults1, excel_filename1)
#             Cell4Life_Postprocessing.save_data_in_excel_for_economic_assessment(input_variablen, excelfilepathresults2)
#             Cell4Life_ControllExcelSheet.ControllSheetExcel(excelfilepathresults3, input_variablen)

#             del excelfilepathresults2, excelfilepathresults3
#             finishtext = "---Parametervariation  --vII: " + input_variablen["szenario"]["value"] + "--  abgeschlossen---"

#             del input_variablen
#             PreResultNumber += 1

# print(finishtext)


# # # # # # # #*************************************************
# # # # ##Szenario 2a

# szenario = "2a" 
# szenarioUnit = "-"
# print("Szenario ", szenario)

# PV_Peak_in_kW = 781  # in kW
# PV_Peak_in_W = PV_Peak_in_kW * 1000 # Umrechnung in Watt

# #PV_Peak_in_W/(2.1)
# #FuelCellPowerW_list = [PV_Peak_in_W/(2.1),PV_Peak_in_W/(2*2.1), PV_Peak_in_W/(4*2.1), PV_Peak_in_W/(6*2.1), PV_Peak_in_W/(8*2.1), PV_Peak_in_W/(10*2.1), PV_Peak_in_W/(12*2.1) ]  #Electricity Power of Fuel Cell Power in Watt, depending on PV - Peak power

# FuelCellPowerW_list = [PV_Peak_in_W/(4*2.1), PV_Peak_in_W/(6*2.1), PV_Peak_in_W/(8*2.1), PV_Peak_in_W/(10*2.1), PV_Peak_in_W/(12*2.1)]  #Electricity Power of Fuel Cell Power in Watt, depending on PV - Peak power

# #Inverter_Ratio_list = [5,3, 2.5, 2, 1.5, 1] #Means: Inverter_power_demand  = (electrolyzer_energy + h2 storage)*Inverter_Ratio_lis --> inverter is always able to deliver at least power demand of electrolyzer and h2 storage!
# Inverter_Ratio_list = [15,10,7,5,3,2.5,2]

# BatterieFaktorList = [150,100,80] 

# #BatterieFaktorList = [60,30,28,26,26,24,22]
# #BatterieFaktorList = [60,30,28,26,24,22] 

# #BatterieFaktorList = [20, 18, 16,14, 12,10, 8, 6, 4, 2, 1] # Battery Capacity =Inverter Power * Batteriefaktorlist --> Batteriekapazität = Faktor * Inverterleistung




# FuelCellPowerWUnit = "W"
# BatteryCapkWhUnit = "kWh"
# Inverter_RatioUnit = "-"

# prediction_horizon = 3600*3  # Based on "Kurzfristprognosen" laut zamg Seite: https://data.hub.geosphere.at/group/wettervorhersagen --> Kurzfristprognosen gibt es für die nächsten 3 Stunden jeweils
# prediction_horizonUnit = "seconds"


# PreResultNumber = 725
# PreResultNumberUnit = "-"

# #BatterieFaktorList = [4,5,6,7,8]
# #BatterieFaktorList = [6,7,8,]

# for BatterieFaktor in BatterieFaktorList:



#     for FuelCellPowerW in FuelCellPowerW_list:
#         FuelCellPowerW = int(math.ceil(FuelCellPowerW))
#         for Inverter_Ratio in Inverter_Ratio_list:
            


#             # Lege neue Werte für Parameter in config_vars fest
#             param_df = pd.read_csv("examples/params_to_loop.csv", sep=",")
#             param_df["PreResultNumber"][0] = PreResultNumber
#             param_df["PreResultNumberUnit"][0] = PreResultNumberUnit
#             param_df["FuelCellPowerW"][0] = FuelCellPowerW
#             param_df["FuelCellPowerWUnit"][0] = FuelCellPowerWUnit
#             #param_df["BatteryCapkWh"][0] = BatteryCapkWh
#             #param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
#             param_df["szenario"][0] = szenario
#             param_df["szenarioUnit"][0] = szenarioUnit
#             param_df["prediction_horizon"][0] = prediction_horizon
#             param_df["prediction_horizonUnit"][0] = prediction_horizonUnit


#             param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
            
                    
#             input_variablen = Cell4LifeSzenario2a.InputParameter()

#             '''
#                 ### aus 1b kopiert
#                     charging_rate = input_variablen["p_el_elektrolyzer"]["value"] / (3600*40000) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
#                     power_demand_charging_h2storage = charging_rate * input_variablen["h_fuel"]["value"] * 3.6e3 * 1000 * input_variablen["h2storage_energy_for_charge_based_on_massflow_h_fuel"]["value"]/100 # electricity power_demand of hydrogen storage for compression of H2 in Watt;
                    

#                     inverte_power_demand = int(math.ceil((power_demand_charging_h2storage  + input_variablen["p_el_elektrolyzer"]["value"])*Inverter_Ratio)) # Inverterleistung hängt von
#                     inverte_power_demandUnit = 'W'

#                     BatteryCapkWh = int(math.ceil((inverte_power_demand/1000)*BatterieFaktor)) #Befehl Ceil Rundet auf; BatteryCKapazität in kWh...Batterie-Kapazität hängt von Elektrolyzer + H2 Storage Verbrauch ab!


#                 ###
#             '''

#             charging_rate = input_variablen["p_el_elektrolyzer"]["value"] / (3600*40000) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
#             power_demand_charging_h2storage = charging_rate * input_variablen["h_fuel"]["value"] * 3.6e3 * 1000 * input_variablen["h2storage_energy_for_charge_based_on_massflow_h_fuel"]["value"]/100 # electricity power_demand of hydrogen storage for compression of H2 in Watt;
#             inverte_power_demand = int(math.ceil((power_demand_charging_h2storage  + input_variablen["p_el_elektrolyzer"]["value"])*Inverter_Ratio))
#             inverte_power_demandUnit = 'W'

#             BatteryCapkWh = int(math.ceil(inverte_power_demand / 1000 * BatterieFaktor)) #Befehl Ceil Rundet auf; BatteryCKapazität in kWh..INverterleistung in Watt gerechnet; Minimum Inverterleistung = 0,25 der Batteriekapazität --> folglich ist die Batteriekapazität immer 4* der Inverterleistung

            
#             param_df["BatteryCapkWh"][0] = BatteryCapkWh
#             param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
#             param_df["Inverter_Ratio"][0] = Inverter_Ratio
#             param_df["Inverter_RatioUnit"][0] = Inverter_RatioUnit
#             param_df["inverte_power_demand"][0] = inverte_power_demand
#             param_df["inverte_power_demandUnit"][0] = inverte_power_demandUnit


#             param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
#             #del param_df
#             sys.argv = ["hisim_main.py", "examples/Cell4LifeSzenario2a.py", "Cell4Life"]
#             with open("C:/Users/Standard/Desktop/hisim/HiSim/hisim/hisim_main.py") as f:        #with --> Handler--> mach etwas mit ... führe es aus...mache es wieder zu -> with kümmert sich um das :)
#                 exec(f.read())
            

#             #Do a copy of the economic assessment excel file        
            
# #             if PreResultNumber == 0:
# #                 pathbase = 'C://Users//Standard//Desktop//hisim//results//'
# #                 name1 = '20231107_AllSimulationResults_Assessment_v5'
# #                 filepath1 = pathbase + 'OriginalExcelFile//' + name1 + '.xlsx'
# #                 copytopath1= pathbase
# #                 excelfilepathallresults1, excel_filename1 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath1, filepath1, name1)

#             #For economic assessment, create a copy of original excel file
#             copyfrompath = 'C://Users//Standard//Desktop//hisim//results//'
#             name2 = 'Sim_Oek_Assessment_v12'
#             filepath2 = copyfrompath + 'OriginalExcelFile//' + name2 + '.xlsx'
#             copytopath2 = ResultPathProviderSingleton().get_result_directory_name()
#             copytopath2 = copytopath2 + '//'
            
#             name2 = 'S'+ str(PreResultNumber) + '_Oek_Assessment'
#             excelfilepathresults2, excel_filename2 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath2, filepath2, name2)
#             del copytopath2, filepath2, name2

#             #For Excel Controll File, create a copy of original excel file
#             copyfrompath = 'C://Users//Standard//Desktop//hisim//results//'
#             name3 = 'ControllFileData-plotting'
#             filepath3 = copyfrompath + 'OriginalExcelFile//' + name3 + '.xlsx'
#             copytopath3 = ResultPathProviderSingleton().get_result_directory_name()
#             copytopath3 = copytopath3 + '//'
            
#             name3 = 'ControllFileData'
#             excelfilepathresults3, excel_filename3 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath3, filepath3, name3)
#             del copytopath3, filepath3, name3
            
            
            
#             #Save all Data in the created excel files
#             input_variablen = Cell4LifeSzenario2a.InputParameter()
#             Cell4Life_Postprocessing.saveInputdata(input_variablen)
#             #Cell4Life_Postprocessing.saveexcelforevaluations(input_variablen, excelfilepathallresults1, excel_filename1)
#             Cell4Life_Postprocessing.save_data_in_excel_for_economic_assessment(input_variablen, excelfilepathresults2)
#             Cell4Life_ControllExcelSheet.ControllSheetExcel(excelfilepathresults3, input_variablen)

#             del excelfilepathresults2, excelfilepathresults3
#             finishtext = "---Parametervariation  --vII: " + input_variablen["szenario"]["value"] + "--  abgeschlossen---"

#             del input_variablen

#             print('Stop')
#             print('Stop')

#             PreResultNumber += 1



# print(finishtext)




####ONLY BATTERY BUT NO FUEL CELL****
# #*************************************************
####ONLY BATTERY BUT NO FUEL CELL****
#Szenario 0a

szenario = "1a" 
szenarioUnit = "-"
PV_Peak_in_kW = 781  # in kW
PV_Peak_in_W = PV_Peak_in_kW * 1000 # Umrechnung in Watt


##Only Battery Calculation*************

#FuelCellPowerW_list = [PV_Peak_in_W/(14*2.1)]
FuelCellPowerW_list = [PV_Peak_in_W/(781000*2.1)]
BatterieFaktorList = [150,100,80,60,30,28,26,24,22, 20, 18, 16,14, 12,10, 8, 6, 4, 2, 1]
Inverter_Ratio_list = [15,10,7,5,3,2.5,2,1.5, 1]

##**************************
FuelCellPowerWUnit = "W"
BatteryCapkWhUnit = "kWh"
Inverter_RatioUnit = "-"

prediction_horizon = 0
prediction_horizonUnit = "seconds"
if prediction_horizon != 0:
    #Szenario 0a, 1a and 1b has no prediction integrated!
    #So please choose a prediciton horizon of zero!
    quit()


# PreResultNumber = 0
# PreResultNumberUnit = "-"

PreResultNumber = 1000
PreResultNumberUnit = "-"

for BatterieFaktor in BatterieFaktorList:

    for FuelCellPowerW in FuelCellPowerW_list:
        FuelCellPowerW = int(math.ceil(FuelCellPowerW))
        for Inverter_Ratio in Inverter_Ratio_list:
            


            # Lege neue Werte für Parameter in config_vars fest
            param_df = pd.read_csv("examples/params_to_loop.csv", sep=",")
            param_df["PreResultNumber"][0] = PreResultNumber
            param_df["PreResultNumberUnit"][0] = PreResultNumberUnit
            param_df["FuelCellPowerW"][0] = FuelCellPowerW
            param_df["FuelCellPowerWUnit"][0] = FuelCellPowerWUnit
            #param_df["BatteryCapkWh"][0] = BatteryCapkWh
            #param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
            param_df["szenario"][0] = szenario
            param_df["szenarioUnit"][0] = szenarioUnit
            param_df["prediction_horizon"][0] = prediction_horizon
            param_df["prediction_horizonUnit"][0] = prediction_horizonUnit


            param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
            
                    
            input_variablen = Cell4LifeSzenario1a_1b.InputParameter()

            charging_rate = 2.1*26564.625850340136054421768707483*input_variablen["p_el_elektrolyzer"]["value"]/ (3600*40000) #umrechnung von Watt [=Joule/Sekunde, Leistung) p_el in  kg/s H2
            power_demand_charging_h2storage = charging_rate * input_variablen["h_fuel"]["value"] * 3.6e3 * 1000 * input_variablen["h2storage_energy_for_charge_based_on_massflow_h_fuel"]["value"]/100 # electricity power_demand of hydrogen storage for compression of H2 in Watt;
            

            inverte_power_demand = int(math.ceil((power_demand_charging_h2storage  + 2.1*26564.625850340136054421768707483*input_variablen["p_el_elektrolyzer"]["value"])*Inverter_Ratio)) # Inverterleistung hängt von
            inverte_power_demandUnit = 'W'

            BatteryCapkWh = int(math.ceil((inverte_power_demand/1000)*BatterieFaktor)) #Befehl Ceil Rundet auf; BatteryCKapazität in kWh...Batterie-Kapazität hängt von Elektrolyzer + H2 Storage Verbrauch ab!


            ###
                # battery_inverter_power = battery_capacity*1000*Inverter_Ratio #in Watt: Batterie Inverter power is assumed to depend on Battery Capacity which is given in kWh!
                # battery_inverter_powerUnit = "W"

            ###

            param_df["FuelCellPowerW"][0] = FuelCellPowerW
            param_df["BatteryCapkWh"][0] = BatteryCapkWh
            param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
            param_df["Inverter_Ratio"][0] = Inverter_Ratio
            param_df["Inverter_RatioUnit"][0] = Inverter_RatioUnit
            param_df["inverte_power_demand"][0] = inverte_power_demand
            param_df["inverte_power_demandUnit"][0] = inverte_power_demandUnit

            param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
            #del param_df
            sys.argv = ["hisim_main.py", "examples/Cell4LifeSzenario1a_1b.py", "Cell4Life"]
            with open("C:/Users/Standard/Desktop/hisim/HiSim/hisim/hisim_main.py") as f:        #with --> Handler--> mach etwas mit ... führe es aus...mache es wieder zu -> with kümmert sich um das :)
                exec(f.read())

            ##Do a copy of the economic assessment excel file        
            ##All Economic Data Assessment Excel File
            # if PreResultNumber == 0:
            #     pathbase = 'C://Users//Standard//Desktop//hisim//results//'
            #     name1 = '20231107_AllSimulationResults_Assessment_v5'
            #     filepath1 = pathbase + 'OriginalExcelFile//' + name1 + '.xlsx'
            #     copytopath1= pathbase
            #     excelfilepathallresults1, excel_filename1 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath1, filepath1, name1)

            #For economic assessment, create a copy of original excel file
            copyfrompath = 'C://Users//Standard//Desktop//hisim//results//'
            name2 = 'Sim_Oek_Assessment_v12'
            filepath2 = copyfrompath + 'OriginalExcelFile//' + name2 + '.xlsx'
            copytopath2 = ResultPathProviderSingleton().get_result_directory_name()
            copytopath2 = copytopath2 + '//'
            
            name2 = 'S'+ str(PreResultNumber) + '_Oek_Assessment'
            excelfilepathresults2, excel_filename2 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath2, filepath2, name2)
            del copytopath2, filepath2, name2

            #For Excel Controll File, create a copy of original excel file
            copyfrompath = 'C://Users//Standard//Desktop//hisim//results//'
            name3 = 'ControllFileData-plotting'
            filepath3 = copyfrompath + 'OriginalExcelFile//' + name3 + '.xlsx'
            copytopath3 = ResultPathProviderSingleton().get_result_directory_name()
            copytopath3 = copytopath3 + '//'
            
            name3 = 'ControllFileData'
            excelfilepathresults3, excel_filename3 = Cell4Life_Postprocessing.makeacopyofevaluationfile(copytopath3, filepath3, name3)
            del copytopath3, filepath3, name3
            
            
            
            #Save all Data in the created excel files
            input_variablen = Cell4LifeSzenario1a_1b.InputParameter()
            Cell4Life_Postprocessing.saveInputdata(input_variablen)
            #Cell4Life_Postprocessing.saveexcelforevaluations(input_variablen, excelfilepathallresults1, excel_filename1)
            Cell4Life_Postprocessing.save_data_in_excel_for_economic_assessment(input_variablen, excelfilepathresults2)
            Cell4Life_ControllExcelSheet.ControllSheetExcel(excelfilepathresults3, input_variablen)

            del excelfilepathresults2, excelfilepathresults3
            finishtext = "---Parametervariation  --vII: " + input_variablen["szenario"]["value"] + "--  abgeschlossen---"

            del input_variablen
            PreResultNumber += 1

print(finishtext)

