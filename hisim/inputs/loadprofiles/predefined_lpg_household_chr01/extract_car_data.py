import json, io, ast
import pandas as pd
 
with open('/fast/home/n-goelz/Repositories/HiSim/hisim/inputs/cache/UTSPConnector_e44c173e2ef76abb9396ba3313b9b41b0733f3c8125f7370e0a3ed74d8b54756.cache', encoding='latin-1') as f:
    data = json.load(f)
 
car_df = pd.read_csv(io.StringIO(data['car_data']), sep=',', decimal='.', encoding='cp1252', index_col=0)
 
target_dir = '/fast/home/n-goelz/Repositories/HiSim/hisim/inputs/loadprofiles/predefined_lpg_household_chr01/data_processed'
 
# CarLocations — braucht LoadTypeName und HouseKey
col = 'car_locations'
entry = {
    'LoadTypeName': car_df.loc['Name', col],
    'HouseKey': {'HouseholdName': 'CHR01 Couple both at Work'},
    'TimeResolution': car_df.loc['TimeResolution', col],
    'Values': ast.literal_eval(car_df.loc['Values', col]),
}
with open(f"{target_dir}/CarLocations.HH1.json", 'w', encoding='utf-8') as f:
    json.dump(entry, f, indent=2)
print("Geschrieben: CarLocations.HH1.json")
 
# CarStates — schauen wir welche Keys generic_car.py braucht
col = 'car_states'
entry = {
    'LoadTypeName': car_df.loc['Name', col],
    'HouseKey': {'HouseholdName': 'CHR01 Couple both at Work'},
    'TimeResolution': car_df.loc['TimeResolution', col],
    'Values': ast.literal_eval(car_df.loc['Values', col]),
}
with open(f"{target_dir}/CarStates.HH1.json", 'w', encoding='utf-8') as f:
    json.dump(entry, f, indent=2)
print("Geschrieben: CarStates.HH1.json")
 
# DrivingDistances — nur Values gebraucht laut generic_car.py
col = 'driving_distances'
entry = {
    'LoadTypeName': car_df.loc['Name', col],
    'HouseKey': {'HouseholdName': 'CHR01 Couple both at Work'},
    'TimeResolution': car_df.loc['TimeResolution', col],
    'Values': ast.literal_eval(car_df.loc['Values', col]),
}
with open(f"{target_dir}/DrivingDistances.HH1.json", 'w', encoding='utf-8') as f:
    json.dump(entry, f, indent=2)
print("Geschrieben: DrivingDistances.HH1.json")
