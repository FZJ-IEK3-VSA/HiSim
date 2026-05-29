# %% Initial config
"""
plot_seasonal_reconstruct.py
----------------------------
Lädt ein cleaned TRY-AT CSV, rekonstruiert DHI und DNI aus GHI
mit wählbarer Methode, und plottet seasonale Profile gegen NSRDB Wien
und DWD TRY Aachen.

Ordnerstruktur (relativ zum Script):
    HiSim/
    ├── TRY_au_data/
    │   └── 2011-2030/
    │       └── cleaned/
    ├── hisim/
    │   ├── components/weather.py
    │   └── inputs/weather/NSRDB_15min/Viena/
    └── plot_seasonal_reconstruct.py

──────────────────────────────────────────────────────
NUR HIER ANPASSEN:
"""

AT_FILE = "TRY__R1__Z1__LL11__A1__S1.csv"  # Dateiname des cleaned CSV
METHOD = "dirint"  # Optionen: "erbs" | "boland" | "reindl" | "dirint"
ELEVATION_THRESHOLD = 5  # Grad — unter diesem Wert werden DNI/DHI/GHI auf 0 gesetzt
AACHEN_YEAR = 2011  # Jahr für den Aachen-Datensatz (für den Zeitindex)

"""
──────────────────────────────────────────────────────
"""

# %% Imports
import sys
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import pvlib
from pathlib import Path
from hisim.components import weather as hisim_weather

# %% Paths
base_dir = Path(__file__).resolve().parent
cleaned_folder = base_dir / "TRY_au_data" / "2011-2030" / "cleaned"
raw_folder = base_dir / "TRY_au_data" / "2011-2030" / "raw"
results_path = base_dir / "TRY_au_data" / f"seasonal_plots_{AT_FILE}"
results_path.mkdir(parents=True, exist_ok=True)

WIEN_FILE = base_dir / "hisim" / "inputs" / "weather" / "NSRDB_15min" / "Viena" / "902141_48.21_16.38_2019.csv"
AACHEN_FILE = str(
    base_dir
    / "hisim"
    / "inputs"
    / "weather"
    / "test-reference-years_1995-2012_1-location"
    / "data_processed"
    / "aachen_center"
)

# %% ── Load Austria cleaned data ─────────────────────────────────────────────
auex = pd.read_csv(cleaned_folder / AT_FILE, sep=";", decimal=",", index_col=0, parse_dates=True)
for col in ["GHI", "DHI", "DNI", "T", "Wspd"]:
    if col not in auex.columns:
        continue
    if auex[col].dtype == object:
        auex[col] = auex[col].str.replace(",", ".", regex=False)
    auex[col] = pd.to_numeric(auex[col], errors="coerce")

auex.index = pd.to_datetime(auex.index)  # keep in UTC

if auex.index.tz is not None:
    auex.index = auex.index.tz_localize(None)

# Lat/lon aus Raw-File lesen (für Sonnenposition)
raw_file = AT_FILE.replace(".csv", ".xlsx")
raw = pd.read_excel(raw_folder / raw_file, header=None, skiprows=1)
lat = raw.iloc[1, 7]
lon = raw.iloc[0, 7]
print(f"AT Koordinaten: lat={lat}, lon={lon}")

# %% ── Reconstruct DHI and DNI from GHI ──────────────────────────────────────
solar_pos = pvlib.solarposition.get_solarposition(time=auex.index, latitude=lat, longitude=lon)

if METHOD == "erbs":
    decomp = pvlib.irradiance.erbs(auex["GHI"], solar_pos["zenith"], auex.index)
    auex["DHI"] = decomp["dhi"].clip(lower=0)
    cos_z = pvlib.tools.cosd(solar_pos["zenith"]).clip(lower=0.01)
    auex["DNI"] = ((auex["GHI"] - auex["DHI"]) / cos_z).clip(lower=0)

elif METHOD == "boland":
    decomp = pvlib.irradiance.boland(auex["GHI"], solar_pos["zenith"], auex.index)
    auex["DHI"] = decomp["dhi"].clip(lower=0)
    cos_z = pvlib.tools.cosd(solar_pos["zenith"]).clip(lower=0.01)
    auex["DNI"] = ((auex["GHI"] - auex["DHI"]) / cos_z).clip(lower=0)

elif METHOD == "reindl":
    dni_extra = pvlib.irradiance.get_extra_radiation(auex.index)
    decomp = pvlib.irradiance.reindl(auex["GHI"], solar_pos["zenith"], dni_extra)
    auex["DHI"] = decomp["dhi"].clip(lower=0)
    cos_z = pvlib.tools.cosd(solar_pos["zenith"]).clip(lower=0.01)
    auex["DNI"] = ((auex["GHI"] - auex["DHI"]) / cos_z).clip(lower=0)

elif METHOD == "dirint":
    auex["DNI"] = pvlib.irradiance.dirint(auex["GHI"], solar_pos["zenith"], auex.index).clip(lower=0)
    cos_z = pvlib.tools.cosd(solar_pos["zenith"]).clip(lower=0.01)
    auex["DHI"] = (auex["GHI"] - auex["DNI"] * cos_z).clip(lower=0)

else:
    raise ValueError(f"Unbekannte Methode: {METHOD}. Wähle: erbs | boland | reindl | dirint")

# Elevationsfilter
valid_sun = solar_pos["apparent_elevation"] > ELEVATION_THRESHOLD
auex.loc[~valid_sun.values, ["DNI", "DHI"]] = 0.0
print(f"AT Rekonstruktion mit '{METHOD}' abgeschlossen.")

# UTC → Lokalzeit (Europe/Vienna), tz-naive for consistent plot
auex.index = auex.index.tz_localize("UTC").tz_convert("Europe/Vienna").tz_localize(None)


# %% ── Load NSRDB Wien data ───────────────────────────────────────────────────
wien_data = pd.read_csv(WIEN_FILE, encoding="utf-8", skiprows=[0, 1])
wien_data.index = pd.date_range("2011-01-01", periods=24 * 4 * 365, freq="900s")
for col in ["GHI", "DHI", "DNI"]:
    wien_data[col] = pd.to_numeric(wien_data[col], errors="coerce")

wien_data["T"] = pd.to_numeric(wien_data["Temperature"], errors="coerce")

# 15-Min → Stundenmittel (damit Summen vergleichbar mit AT und Aachen)
wien_data = wien_data.resample("H").mean()


# %% ── Load Aachen DWD TRY data ───────────────────────────────────────────────
aachen_data = hisim_weather.read_dwd_try_data(AACHEN_FILE, year=AACHEN_YEAR)
# Timezone entfernen für konsistentes Groupby
if aachen_data.index.tz is not None:
    aachen_data.index = aachen_data.index.tz_localize(None)
print(f"Aachen geladen: {len(aachen_data)} Zeilen, Spalten: {list(aachen_data.columns)}")

# %% ── Labels & config ────────────────────────────────────────────────────────
LABEL_AT = f"TRY AT ({lat}°N, {lon}°E) — {METHOD}"
LABEL_WIEN = "NSRDB Wien (48.21°N, 16.38°E)"
LABEL_AACHEN = "DWD TRY Aachen"

SEASON_MAP = {
    12: "Winter",
    1: "Winter",
    2: "Winter",
    3: "Spring",
    4: "Spring",
    5: "Spring",
    6: "Summer",
    7: "Summer",
    8: "Summer",
    9: "Autumn",
    10: "Autumn",
    11: "Autumn",
}
SEASONS = ["Winter", "Spring", "Summer", "Autumn"]
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

COLOR_AT = "#E63946"
COLOR_WIEN = "#457B9D"
COLOR_AACHEN = "#2A9D8F"


# %% ── Helpers ────────────────────────────────────────────────────────────────
def hourly_profile(df, variable, mask=None):
    subset = df[mask] if mask is not None else df
    return subset.groupby(subset.index.hour)[variable].mean()


def save_fig(fig, name):
    path = results_path / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved → {path}")


# %% ── Plot 1: Seasonal hourly profiles ───────────────────────────────────────
def plot_seasonal(variable):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharey=True)
    fig.suptitle(
        f"Seasonal Average Hourly Profile — {variable}\n" f"{LABEL_AT}  |  {LABEL_WIEN}  |  {LABEL_AACHEN}",
        fontsize=12,
        fontweight="bold",
    )

    for ax, season in zip(axes.flatten(), SEASONS):
        months = [m for m, s in SEASON_MAP.items() if s == season]

        prof_at = hourly_profile(auex, variable, auex.index.month.isin(months))
        prof_wien = hourly_profile(wien_data, variable, wien_data.index.month.isin(months))
        prof_aachen = hourly_profile(aachen_data, variable, aachen_data.index.month.isin(months))

        ax.plot(prof_at.index, prof_at.values, color=COLOR_AT, lw=2, label=LABEL_AT)
        ax.plot(prof_wien.index, prof_wien.values, color=COLOR_WIEN, lw=2, label=LABEL_WIEN, ls="--")
        ax.plot(prof_aachen.index, prof_aachen.values, color=COLOR_AACHEN, lw=1.5, label=LABEL_AACHEN, ls=":")
        ax.set(title=season, xlabel="Hour of day", ylabel="W/m²")
        ax.set_xticks(range(0, 24, 2))
        ax.legend(fontsize=7)
        ax.grid(True, ls="--", alpha=0.5)

    plt.tight_layout()
    save_fig(fig, f"seasonal_{variable}_{METHOD}.png")


# %% ── Plot 2: Monthly total bar chart ────────────────────────────────────────
def plot_monthly_totals(variable):
    def monthly_daily_mean(df, var):
        # Stundenwerte → Tagessumme → Monatsmittel der Tagessummen (Wh/m²/Tag)
        daily = df[var].resample("D").sum()
        return daily.groupby(daily.index.month).mean()

    m_at = monthly_daily_mean(auex, variable)
    m_wien = monthly_daily_mean(wien_data, variable)
    m_aachen = monthly_daily_mean(aachen_data, variable)

    annual_at = auex[variable].resample("D").sum().sum()
    annual_wien = wien_data[variable].resample("D").sum().sum()
    annual_aachen = aachen_data[variable].resample("D").sum().sum()

    x = np.arange(12)
    width = 0.25

    fig, ax = plt.subplots(figsize=(14, 6))
    bars_at = ax.bar(x - width, m_at.values, width, label=LABEL_AT, color=COLOR_AT, alpha=0.85)
    bars_wien = ax.bar(x, m_wien.values, width, label=LABEL_WIEN, color=COLOR_WIEN, alpha=0.85)
    bars_aachen = ax.bar(x + width, m_aachen.values, width, label=LABEL_AACHEN, color=COLOR_AACHEN, alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(MONTH_LABELS)
    ax.set_ylabel("Wh/m²/day (monthly mean)")
    ax.set_title(f"Monthly Mean Daily Total — {variable}\n" f"{LABEL_AT}  |  {LABEL_WIEN}  |  {LABEL_AACHEN}")
    ax.grid(True, axis="y", ls="--", alpha=0.5)

    # Jahressummen als Text in die Legende
    ax.legend(
        handles=[bars_at, bars_wien, bars_aachen],
        labels=[
            f"{LABEL_AT}  (Σ {annual_at/1000:.0f} kWh/m²/a)",
            f"{LABEL_WIEN}  (Σ {annual_wien/1000:.0f} kWh/m²/a)",
            f"{LABEL_AACHEN}  (Σ {annual_aachen/1000:.0f} kWh/m²/a)",
        ],
        fontsize=9,
    )

    plt.tight_layout()
    save_fig(fig, f"monthly_totals_{variable}_{METHOD}.png")


# %% ── Plot 3: Seasonal temperature profiles ────────────────────────────────────────
def plot_seasonal_temp(variable, ylabel):

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharey=False)
    fig.suptitle(
        f"Seasonal Average Hourly Profile — Temperature\n" f"{LABEL_AT}  |  {LABEL_WIEN}  |  {LABEL_AACHEN}",
        fontsize=12,
        fontweight="bold",
    )

    for ax, season in zip(axes.flatten(), SEASONS):
        months = [m for m, s in SEASON_MAP.items() if s == season]

        prof_at = hourly_profile(auex, variable, auex.index.month.isin(months))
        prof_wien = hourly_profile(wien_data, variable, wien_data.index.month.isin(months))
        prof_aachen = hourly_profile(aachen_data, variable, aachen_data.index.month.isin(months))

        ax.plot(prof_at.index, prof_at.values, color=COLOR_AT, lw=2, label=LABEL_AT)
        ax.plot(prof_wien.index, prof_wien.values, color=COLOR_WIEN, lw=2, label=LABEL_WIEN, ls="--")
        ax.plot(prof_aachen.index, prof_aachen.values, color=COLOR_AACHEN, lw=1.5, label=LABEL_AACHEN, ls=":")
        ax.set(title=season, xlabel="Hour of day", ylabel=ylabel)
        ax.set_xticks(range(0, 24, 2))
        ax.legend(fontsize=7)
        ax.grid(True, ls="--", alpha=0.5)

    plt.tight_layout()
    save_fig(fig, f"seasonal_temperature_{METHOD}.png")


def plot_three_day_temperature(day, month):
    start = pd.Timestamp(f"2011-{month:02d}-{day:02d}")
    end = start + pd.Timedelta(days=3)

    at_slice = auex.loc[start:end, "T"]
    wien_slice = wien_data.loc[start:end, "T"]
    aachen_slice = aachen_data.loc[start:end, "T"]

    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(at_slice.index, at_slice.values, color=COLOR_AT, lw=2, label=LABEL_AT)
    ax.plot(wien_slice.index, wien_slice.values, color=COLOR_WIEN, lw=2, label=LABEL_WIEN, ls="--")
    ax.plot(aachen_slice.index, aachen_slice.values, color=COLOR_AACHEN, lw=1.5, label=LABEL_AACHEN, ls=":")
    ax.set(
        title=f"Temperature — {day:02d}.{month:02d}.2011 + next 2 days", xlabel="Datetime", ylabel="Temperature (°C)"
    )
    ax.legend(fontsize=9)
    ax.grid(True, ls="--", alpha=0.5)
    fig.autofmt_xdate()
    plt.tight_layout()
    save_fig(fig, f"three_day_temperature_{day:02d}_{month:02d}.png")


# %% ── Run ────────────────────────────────────────────────────────────────────
for var in ["GHI", "DHI", "DNI"]:
    plot_seasonal(var)
    plot_monthly_totals(var)

plot_seasonal_temp(variable="T", ylabel="Temperature °C")
plot_three_day_temperature(day=15, month=7)

print(f"\nAlle Plots gespeichert in: {results_path}")
# %%
