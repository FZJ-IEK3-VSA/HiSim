#%% 
import pandas as pd
import os
import pvlib

#%%

# ── Paths ──────────────────────────────────────────────────────────────────
# raw_folder     = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\raw"
raw_folder = r"H:\02_Projekte\04_Repositories\HiSim\TRY_au_data\2011-2030\raw"
# cleaned_folder = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\cleaned"
cleaned_folder = r"H:\02_Projekte\04_Repositories\HiSim\TRY_au_data\2011-2030\cleaned"
os.makedirs(cleaned_folder, exist_ok=True)

days_per_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                  7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

# ── Process each raw xlsx ───────────────────────────────────────────────────
for file in os.listdir(raw_folder):
    if not file.endswith(".xlsx"):
        continue

    filepath = os.path.join(raw_folder, file)
    base_name = file.replace(".xlsx", "")
    csv_path  = os.path.join(cleaned_folder, base_name + ".csv")
    dat_path  = os.path.join(cleaned_folder, base_name + ".dat")

    print(f"Processing {file}...")

    # ── Read raw xlsx ───────────────────────────────────────────────────────
    data_au_raw = pd.read_excel(filepath, header=None, skiprows=1)
    lon    = data_au_raw.iloc[0, 7]
    lat    = data_au_raw.iloc[1, 7]
    region = data_au_raw.iloc[0, 1]

    # ── Clean and reshape ───────────────────────────────────────────────────
    data_au = data_au_raw.iloc[8:].copy()
    data_au.columns = data_au_raw.iloc[5]
    data_au = data_au.iloc[:, 1:8].reset_index(drop=True)
    data_au = data_au.drop_duplicates(keep='first')
    data_au = data_au.rename(columns={
        "WG":    "Wspd",
        "Monat": "Month",
        "Tag":   "Day",
        "Stunde":"Hour",
        "LT":    "T",        # renamed directly to T for HiSim
        "RF":    "RelHum",
        "KWSU":  "GHI",
    })
    
    
    # ── Remove duplicate hours ──────────────────────────────────────────────
    for (month, day, hour), group in data_au.groupby(['Month', 'Day', 'Hour']):
        if len(group) > 1:
            data_au = data_au.drop(group.index[1:])

    # ── Remove excess days to get exactly 8760 rows ─────────────────────────
    for month, group in data_au.groupby('Month'):
        expected = days_per_month[month] * 24
        actual   = len(group)
        if actual != expected:
            last_day = group['Day'].max()
            data_au  = data_au.drop(group[group['Day'] == last_day].index)

    
    data_au = data_au.drop(data_au.index[-1])


    # hour 0-23 in UTC, set timestamp = interval mid (xx:30 UTC)
    data_au.index = pd.date_range(
        "2011-01-01 00:30:00", periods=8760, freq="H", tz="UTC"
    )

    for col in ['T', 'GHI', 'Wspd']:
        data_au[col] = pd.to_numeric(
            data_au[col].astype(str).str.replace(',', '.', regex=False),
            errors='coerce'
        )

    # ── Calculate solar components ──────────────────────────────────────────
    data_au['GHI'] = data_au['GHI'] * 3.

    # ── Calculate solar components ──────────────────────────────────────────
    solar_position = pvlib.solarposition.get_solarposition(
        time=data_au.index, latitude=lat, longitude=lon
    )
    decomp = pvlib.irradiance.erbs(data_au['GHI'], solar_position['zenith'], data_au.index)
    data_au['DHI'] = pd.to_numeric(decomp['dhi']).round(2)
    data_au['DNI'] = pd.to_numeric(decomp['dni']).round(2)

    # ── Add dummy columns HiSim expects ────────────────────────────────────
    data_au['Pressure'] = 1013.25   # standard sea-level pressure
    data_au['Wdir']     = 0.0       # dummy wind direction

    # For CSV/Plot: convert to local vienna time
    data_au.index = data_au.index.tz_convert("Europe/Vienna")

    # Remove tz information from datetime
    data_au.index = data_au.index.tz_localize(None)  # necessary for function strip_tz() to work

    # ── Write cleaned CSV ───────────────────────────────────────────────────
    data_au.to_csv(csv_path, sep=';', decimal=',')
    print(f"  CSV written: {csv_path}")

    # ── Write companion .dat file with lat/lon at exact character positions ─
    # Line 0: "XX XX <location_name>"  — 3rd whitespace token = location name
    # Line 1: lat at character positions 20-37
    # Line 2: lon at character positions 15-30
    lat_str      = f"{'Latitude:':>20}{lat:<17.6f}"
    lon_str      = f"{'Longitude:':>15}{lon:<15.6f}"
    location_str = f"XX XX {region}"

    with open(dat_path, "w", encoding="utf-8") as f:
        f.write(location_str + "\n")
        f.write(lat_str      + "\n")
        f.write(lon_str      + "\n")
    print(f"  DAT written: {dat_path}")
    
    
print("\nAll files processed.")
