import pandas as pd
import os
import sys
os.chdir('C://Users//Standard//Desktop//hisim//HiSim//')
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//examples")
sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//")
import Cell4LifeSzenario1

sys.path.append("C://Users//Standard//Desktop//hisim//HiSim//hisim//postprocessing//")
os.chdir("C://Users//Standard//Desktop//hisim//HiSim//hisim//postprocessing//")
import Cell4Life_Postprocessing
os.chdir('C://Users//Standard//Desktop//hisim//HiSim//')


"""This class executes Cell4Life Simulation
    
    -) "Variation Parameters": Parameters, which should be variied 
 
    -) Other "static parameters" are seen as static and defined in "Cell4Life-Model-_d3.py" example


 """

#FuelCellPowerW_list = [20000,40000]  #Electricity Power of Fuel Cell Power in Watt
#BatteryCapkWh_list = [500000,10]     #Total Capacity of Battery in kWh

FuelCellPowerW_list = [200000, 150000, 100000, 50000, 25000]  #Electricity Power of Fuel Cell Power in Watt
BatteryCapkWh_list = [500, 1000, 2000, 4000, 8000]     #Total Capacity of Battery in kWh

FuelCellPowerWUnit = "W"
BatteryCapkWhUnit = "kWh"

PreResultNumber = 0
PreResultNumberUnit = "-"

for FuelCellPowerW in FuelCellPowerW_list:
    for BatteryCapkWh in BatteryCapkWh_list:
        
        # Lege neue Werte für Parameter in config_vars fest
        param_df = pd.read_csv("examples/params_to_loop.csv", sep=",")
        param_df["PreResultNumber"][0] = PreResultNumber
        param_df["PreResultNumberUnit"][0] = PreResultNumberUnit
        param_df["FuelCellPowerW"][0] = FuelCellPowerW
        param_df["FuelCellPowerWUnit"][0] = FuelCellPowerWUnit
        param_df["BatteryCapkWh"][0] = BatteryCapkWh
        param_df["BatteryCapkWhUnit"][0] = BatteryCapkWhUnit
        param_df.to_csv("examples/params_to_loop.csv", sep=",", index= False)
        del param_df
        
        
        sys.argv = ["hisim_main.py", "examples/Cell4LifeSzenario1.py", "Cell4Life"]

        with open("C:/Users/Standard/Desktop/hisim/HiSim/hisim/hisim_main.py") as f:        #with --> Handler--> mach etwas mit ... führe es aus...mache es wieder zu -> with kümmert sich um das :)
            exec(f.read())
        

        #Do a copy of the economic assessment excel file        
        
        if PreResultNumber == 0:
            path = 'C://Users//Standard//Desktop//hisim//C4LResults//results//'
            filepath = path + 'OriginalExcelFile//20231019_oekonomische_Auswertung_v4.xlsx'
            excelfilepathallresults, excel_filename = Cell4Life_Postprocessing.makeacopyofevaluationfile(path, filepath)
        
        #Save all Data in the created excel files
        input_variablen = Cell4LifeSzenario1.InputParameter()
        Cell4Life_Postprocessing.saveInputdata(input_variablen)
        Cell4Life_Postprocessing.saveexcelforevaluations(input_variablen, excelfilepathallresults, excel_filename)
        
        
             

        del input_variablen
        PreResultNumber += 1

       

print("---Parametervariation abgeschlossen---")




