# This notebook documents the selection of a default PV module and inverter
# From the CEC database
#%% Import CEC database file
import pandas as pd
data = pd.read_csv("data_processed/cec_modules.csv")

#%% Calculate additional indicators
# https://github.com/PV-Tutorials/pyData-2021-Solar-PV-Modeling/blob/main/Tutorial%20C%20-%20Modeling%20Module%27s%20Performance%20Advanced.ipynb
data_mono = data[data["Technology"] == "Mono-c-Si"].copy()
for c in ["V_mp_ref", "I_mp_ref", "V_oc_ref", "I_sc_ref", "A_c"]:
    data_mono.loc[:,c] = data_mono[c].astype(float)

# https://www.pveducation.org/pvcdrom/solar-cell-operation/fill-factor
data_mono.loc[:,"FF"] = (data_mono["V_mp_ref"] * data_mono["I_mp_ref"]) / (data_mono["V_oc_ref"] * data_mono["I_sc_ref"])

# https://www.pveducation.org/pvcdrom/solar-cell-operation/solar-cell-efficiency
data_mono.loc[:,"P_max"] = data_mono["V_oc_ref"] * data_mono["I_sc_ref"] * data_mono["FF"]
data_mono.loc[:,"eff"] =  data_mono.loc[:,"P_max"] / (1000 * data_mono["A_c"])

#%% Select candidates, use most efficient by Trina Solar
candidates = data_mono[(0.21 <= data_mono["eff"]) & (data_mono["eff"] <= 0.225) & (420 <= data_mono["P_max"]) & (data_mono["P_max"] <= 440)]
selected = candidates[candidates["Manufacturer"] == "Trina Solar"].sort_values(by='eff').iloc[-1]

#%% Inverter
# https://energy.sandia.gov/wp-content/gallery/uploads/Performance-Model-for-Grid-Connected-Photovoltaic-Inverters.pdf
inverter_data = pd.read_csv("data_processed/cec_inverters.csv", header=[0,1,2])
inverter_data = inverter_data.droplevel(level=[1,2], axis=1)

inverter_data["eff"] = inverter_data["Paco"] / inverter_data["Pdco"]

candidate_inverters = inverter_data[
    (inverter_data["Pdco"].astype(float) > selected["P_max"]) & 
    (inverter_data["Paco"].astype(float) > 0.8 * selected["P_max"]) & 
    (inverter_data["Paco"].astype(float) < 1.2 * selected["P_max"]) &
    (inverter_data["Vdco"].astype(float) > 0.95 * selected["V_mp_ref"]) &
    (inverter_data["Vdco"].astype(float) < 1.05 * selected["V_mp_ref"]) &
    (inverter_data["Idcmax"].astype(float) > selected["I_mp_ref"]) 
    ]

selected_inverter = candidate_inverters.sort_values(by='eff').iloc[-1]