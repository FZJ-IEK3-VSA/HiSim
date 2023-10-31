'''Cell4Life_Postprocessing.py
"C4L Specific" saving all Data'

'''
import csv
import os
import json
import openpyxl
import shutil  # Needed for copying of excel file
from datetime import datetime
from openpyxl.styles import NamedStyle
from hisim.result_path_provider import ResultPathProviderSingleton, SortingOptionEnum

def saveInputdata(input_variablen):
    PreResultNumber = input_variablen["PreResultNumber"]
    #----Save all Input Data in a .txt ----
    path = ResultPathProviderSingleton().get_result_directory_name() 
    name = f"//S{PreResultNumber}_InputConfig.csv"
    path = path + name
    del name

    with open(path, mode='w', newline='') as file:
        writer = csv.writer(file)
        # Schreibe den Header (Spaltenüberschriften)
        writer.writerow(['Variable', 'Wert'])
        # Schreibe die Variablen und ihre Werte in separate Zeilen
        for variable, wert in input_variablen.items():
            writer.writerow([variable, wert])
        file.close()

def saveexcelforevaluations(input_variablen, excelfilepathallresults, excel_filename):
    PreResultNumber = input_variablen["PreResultNumber"]
    path = ResultPathProviderSingleton().get_result_directory_name() 
    
    zusammengefuegte_daten = []
    Daten2 = []
    
    # Lade Daten aus dem ersten CSV-Datei "Electricity TO or FROM Grid Ergebnisse" (Spalte 1 und Spalte 2) und füge sie zur Liste hinzu
    erstes_csv_datei = os.path.join(path, 'ElectricityToOrFromGrid_L2EMSElectricityController.csv')
    with open(erstes_csv_datei, 'r') as csvfile:
        csvreader = csv.reader(csvfile, delimiter=';')  # Verwende Semikolon als Trennzeichen
        for row in csvreader:
            zusammengefuegte_daten.append([row[0], row[1]])

    # Lade Daten aus dem zweiten CSV-Datei "Thermal Output CHP in Watt" (ausschließlich Spalte 2) und füge sie zur Liste hinzu (in die dritte Spalte)
    zweites_csv_datei = os.path.join(path, 'ThermalPowerOutput_CHP_w2.csv')
    with open(zweites_csv_datei, 'r') as csvfile:
        csvreader = csv.reader(csvfile, delimiter=';')  # Verwende Semikolon als Trennzeichen
        for row in csvreader:
            Daten2.append(row[1])
    

    #Zusammenfuehren der Daten
    zusammengefuegte_daten = [(x[0], x[1], Daten2[i]) for i, x in enumerate(zusammengefuegte_daten)]
    
    #------------
    # Create a complete new Excel File and save the result data in this resultpath/file
    workbook = openpyxl.Workbook()
    
    #Erstelle ein Excel-Arbeitsblatt
    worksheet = workbook.active
    worksheettitel = f"S{PreResultNumber}-Input std."
    worksheet.title = worksheettitel
    
    # Schreibe die zusammengeführten Daten in das Excel-Arbeitsblatt
    for row in zusammengefuegte_daten:
        worksheet.append(row)
    excelfilepath = path + '//' + worksheettitel + '.xlsx'
    workbook.save(excelfilepath)
    del excelfilepath, workbook, worksheet, row, worksheettitel

    #------------
    # Add all the result data from this simulation round within the economic assessment excel file; 
    workbook = openpyxl.load_workbook(excelfilepathallresults)
    # Wählen Sie das Tabellenblatt aus, in das Sie Ihre Daten kopieren möchten
    
    worksheettitel = 'S'+ str(PreResultNumber) + '-Input std.'
    worksheet = workbook[worksheettitel]
    del worksheettitel

    # Durchlaufen Sie die Spalten und löschen Sie die Daten
    zu_loeschende_spalten = [0, 1, 2]
    # Durchlaufen Sie die Zeilen und löschen Sie die Daten in den gewünschten Spalten
    for zeile in range(worksheet.max_row):
        for spalte in zu_loeschende_spalten:
            zelle = worksheet.cell(row=zeile+1, column=spalte+1)
            zelle.value = None
    
    # Fügen Sie die neuen Daten an den gleichen Stellen ein, an denen die alten Daten gelöscht wurden
    for zeile, daten in enumerate(zusammengefuegte_daten, start=1):  # Start bei Zeile 2, um Überschriften zu vermeiden
        for spalte, wert in enumerate(daten, start=1):
            zelle = worksheet.cell(row=zeile, column=spalte)
            zelle.value = wert

    #------------    
    #Add Input Data to Economic Excel file
    # Select the desired worksheet
    worksheet = workbook['Anlagendaten']  # 'Anlagendaten' durch den tatsächlichen Namen ersetzen

    # Determine the row to append the data
    next_column = worksheet.max_column + 1
    next_row = 1
    # Write the data to the worksheet
    for parameter, value in input_variablen.items():
        if PreResultNumber == 0:
            worksheet.cell(row=next_row, column=1, value=parameter)
            worksheet.cell(row=next_row, column=2, value=value)
        else:
            worksheet.cell(row=next_row, column=next_column, value=value)    
        next_row += 1

    #------------
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    print("Parametervariation---Batteriekapazität: ", str(input_variablen["battery_capacity"]), "  &  Brennstoffzellenleistung: ",input_variablen["fuel_cell_power"])
    print("Simulation mit Parametern abgeschlossen - Ergebnisse werden im ökonomischen Bewertungstool gespeichert")
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    print("------------------------------------------------------------------------------")
    # Save the Excel file
    workbook.save(excelfilepathallresults)



def makeacopyofevaluationfile(path, filepath):
    #Make a copy of result file!
   
    #path = 'C://Users//Standard//C4LResults//results//'
    #filepath = path + 'OriginalExcelFile//20231019_oekonomische_Auswertung_v2.xlsx'
    
    # Erstellen Sie einen Datums- und Zeitstempel für die Sicherheitskopie
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    
    # Erstellen Sie den Dateinamen der Sicherheitskopie
    copyfile_filename = f'Oekonomische_Auswertung_{timestamp}.xlsx'

    # Erstellen Sie den vollständigen Dateipfad für die Sicherheitskopie
    filepath_ofcopy = path + copyfile_filename
    
    # Kopieren Sie die Excel-Datei, um die Sicherheitskopie zu erstellen
    shutil.copy2(filepath, filepath_ofcopy)
    
    return filepath_ofcopy, copyfile_filename
