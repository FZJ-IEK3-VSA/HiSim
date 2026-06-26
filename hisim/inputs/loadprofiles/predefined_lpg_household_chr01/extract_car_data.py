import json, io, ast
import pandas as pd
 
with open('/fast/home/n-goelz/Repositories/HiSim/hisim/inputs/cache/UTSPConnector_e44c173e2ef76abb9396ba3313b9b41b0733f3c8125f7370e0a3ed74d8b54756.cache', encoding='latin-1') as f:
    data = json.load(f)
 
car_df = pd.read_csv(io.StringIO(data['car_data']), sep=',', decimal='.', encoding='cp1252', index_col=0)
 
target_dir = '/fast/home/n-goelz/Repositories/HiSim/hisim/inputs/loadprofiles/predefined_lpg_household_chr01/data_processed'
 
for col, filename in [
    ('car_states',        'CarStates.HH1.json'),
    ('car_locations',     'CarLocations.HH1.json'),
    ('driving_distances', 'DrivingDistances.HH1.json'),
]:
    house_key = ast.literal_eval(car_df.loc['HouseKey', col])
    entry = {
        'LoadTypeName':   car_df.loc['LoadTypeName', col],
        'HouseKey':       house_key,
        'TimeResolution': car_df.loc['TimeResolution', col],
        'StartTime':      car_df.loc['StartTime', col],
        'Values':         ast.literal_eval(car_df.loc['Values', col]),
    }
    out_path = f"{target_dir}/{filename}"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(entry, f, indent=2)
    print(f"Geschrieben: {out_path}")
