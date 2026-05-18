# %% Load packages & Setup
import pandas as pd
import os
from pathlib import Path
import pvlib
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import math
from scipy import stats


#%%
#folder = "HiSim/TRY_au_data/2011-2030"
folder = "TRY_au_data/2011-2030"
flist = os.listdir(folder)

results_Path =  Path("TRY_au_data/Analyze_data_output")
results_Path.mkdir(parents=True, exist_ok=True)

raw_folder    = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\raw"

#%% Check for jumps during leap years

days_per_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                  7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

results = []
all_changes_per_file = {}

for file in os.listdir(raw_folder):
    if not file.endswith(".xlsx"):
        continue

    filepath = os.path.join(raw_folder, file)
    data_au_raw = pd.read_excel(filepath, header=None, skiprows=1)
    region = data_au_raw.iloc[0, 1]

    data_au = data_au_raw.iloc[8:].copy()
    data_au.columns = data_au_raw.iloc[5]
    data_au = data_au.iloc[:, 1:8].reset_index(drop=True)
    data_au = data_au.drop_duplicates(keep='first')
    data_au = data_au.rename(columns={
        "WG":    "Wspd",
        "Monat": "Month",
        "Tag":   "Day",
        "Stunde":"Hour",
        "LT":    "AirTemp",
        "RF":    "RelHum",
        "KWSU":  "GHI",
    })

    # Force numeric
    for col in ["AirTemp", "RelHum", "GHI", "Wspd"]:
        data_au[col] = pd.to_numeric(
            data_au[col].astype(str).str.replace(',', '.', regex=False),
            errors='coerce'
        )
    for col in ["Month", "Day", "Hour"]:
        data_au[col] = pd.to_numeric(data_au[col], errors='coerce')

    # ── Only process files with Feb 29 ───────────────────────────────────────
    has_feb29 = ((data_au["Month"] == 2) & (data_au["Day"] == 29)).any()
    if not has_feb29:
        print(f"Skipping {file} — no Feb 29")
        continue

    print(f"Processing {file} (has Feb 29)...")

    # Remove duplicate hours
    for (month, day, hour), group in data_au.groupby(['Month', 'Day', 'Hour']):
        if len(group) > 1:
            data_au = data_au.drop(group.index[1:])

    # ── Daily averages using raw Month/Day columns ───────────────────────────
    daily = data_au.groupby(['Month', 'Day'])[['AirTemp', 'RelHum']].mean().reset_index()
    daily = daily.sort_values(['Month', 'Day']).reset_index(drop=True)

    # ── Day-to-day absolute changes ──────────────────────────────────────────
    daily_changes_T   = daily['AirTemp'].diff().abs().dropna()
    daily_changes_hum = daily['RelHum'].diff().abs().dropna()

    # ── Feb 28 and Mar 1 values ──────────────────────────────────────────────
    feb28 = daily[(daily['Month'] == 2) & (daily['Day'] == 28)]
    mar1  = daily[(daily['Month'] == 3) & (daily['Day'] == 1)]

    if feb28.empty or mar1.empty:
        continue

    feb28_T  = feb28['AirTemp'].values[0]
    mar1_T   = mar1['AirTemp'].values[0]
    jump_T   = abs(mar1_T - feb28_T)

    feb28_hum = feb28['RelHum'].values[0]
    mar1_hum  = mar1['RelHum'].values[0]
    jump_hum  = abs(mar1_hum - feb28_hum)

    # ── Z-scores and t-tests ─────────────────────────────────────────────────
    mean_T, std_T   = daily_changes_T.mean(),   daily_changes_T.std()
    mean_hum, std_hum = daily_changes_hum.mean(), daily_changes_hum.std()

    z_T   = (jump_T   - mean_T)   / std_T   if std_T   > 0 else 0
    z_hum = (jump_hum - mean_hum) / std_hum if std_hum > 0 else 0

    _, p_val_T   = stats.ttest_1samp(daily_changes_T,   jump_T)
    _, p_val_hum = stats.ttest_1samp(daily_changes_hum, jump_hum)

    results.append({
        "file":                    file.replace(".xlsx", ""),
        "region":                  region,
        "feb28_T":                 round(feb28_T,   2),
        "mar1_T":                  round(mar1_T,    2),
        "jump_T":                  round(jump_T,    2),
        "mean_daily_change_T":     round(mean_T,    2),
        "std_daily_change_T":      round(std_T,     2),
        "z_score_T":               round(z_T,       2),
        "p_value_T":               round(p_val_T,   4),
        "significant_T":           p_val_T < 0.05,
        "feb28_hum":               round(feb28_hum, 2),
        "mar1_hum":                round(mar1_hum,  2),
        "jump_hum":                round(jump_hum,  2),
        "mean_daily_change_hum":   round(mean_hum,  2),
        "std_daily_change_hum":    round(std_hum,   2),
        "z_score_hum":             round(z_hum,     2),
        "p_value_hum":             round(p_val_hum, 4),
        "significant_hum":         p_val_hum < 0.05,
    })
    all_changes_per_file[file.replace(".xlsx", "")] = {
        "T":   daily_changes_T.values,
        "hum": daily_changes_hum.values,
    }

results_df = pd.DataFrame(results).sort_values("z_score_T", ascending=False)

print("\n── Results sorted by temperature z-score ────────────────────────────")
print(results_df[["file", "jump_T", "mean_daily_change_T",
                   "z_score_T", "p_value_T", "significant_T"]].to_string(index=False))

#%%
def save_results_figure(results_df, results_folder):
    cols_T   = ["file", "feb28_T", "mar1_T", "jump_T", "mean_daily_change_T",
                "std_daily_change_T", "z_score_T", "p_value_T", "significant_T"]
    cols_hum = ["file", "feb28_hum", "mar1_hum", "jump_hum", "mean_daily_change_hum",
                "std_daily_change_hum", "z_score_hum", "p_value_hum", "significant_hum"]

    df_T   = results_df[cols_T].copy()
    df_hum = results_df[cols_hum].copy()

    def make_cell_colours(df, sig_col):
        colours = []
        for _, row in df.iterrows():
            row_colours = []
            for col in df.columns:
                if col == sig_col:
                    row_colours.append("#d4f7d4" if row[col] else "#ffd6d6")
                else:
                    row_colours.append("#f9f9f9")
            colours.append(row_colours)
        return colours

    fig, axes = plt.subplots(2, 1, figsize=(16, 0.5 * len(results_df) + 4))
    fig.suptitle("Feb 28 → Mar 1 Jump Analysis", fontsize=14, fontweight="bold", y=1.01)

    for ax, df, title, sig_col in [
        (axes[0], df_T,   "Temperature (°C)",  "significant_T"),
        (axes[1], df_hum, "Humidity (%)",       "significant_hum"),
    ]:
        ax.axis("off")
        ax.set_title(title, fontweight="bold", loc="left", pad=8)

        cell_text   = df.astype(str).values.tolist()
        cell_colors = make_cell_colours(df, sig_col)

        tbl = ax.table(
            cellText=cell_text,
            colLabels=df.columns.tolist(),
            cellColours=cell_colors,
            loc="center",
            cellLoc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.auto_set_column_width(col=list(range(len(df.columns))))

        # Bold header row
        for col_idx in range(len(df.columns)):
            tbl[0, col_idx].set_facecolor("#333333")
            tbl[0, col_idx].set_text_props(color="white", fontweight="bold")

    # Legend
    sig_patch   = mpatches.Patch(color="#d4f7d4", label="Significant (p < 0.05)")
    insig_patch = mpatches.Patch(color="#ffd6d6", label="Not significant")
    fig.legend(handles=[sig_patch, insig_patch], loc="lower center",
               ncol=2, bbox_to_anchor=(0.5, -0.02), fontsize=9)

    fig.tight_layout()
    out_path = os.path.join(results_folder, "jump_analysis_results.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Figure saved → {out_path}")

save_results_figure(results_df, results_Path)

#%% Compare GHI, DHI, DNI

results_folder = results_Path

# ── Load Austria data ────────────────────────────────────────────────────────
raw_folder  = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\raw"
file        = "TRY__R1__Z1__LL11__A1__S1.xlsx"
data_au_raw = pd.read_excel(os.path.join(raw_folder, file), header=None, skiprows=1)
lon    = data_au_raw.iloc[0, 7]
lat    = data_au_raw.iloc[1, 7]
region = data_au_raw.iloc[0, 1]

cleaned_folder = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\cleaned"
auex = pd.read_csv(
    os.path.join(cleaned_folder, "TRY__R1__Z1__LL11__A1__S1.csv"),
    sep=";", index_col=0
)

# ── Load Vienna data ─────────────────────────────────────────────────────────
wien_data = pd.read_csv(
    r"C:\Alvarez\HiSim\HiSim\hisim\inputs\weather\NSRDB_15min\Viena\902141_48.21_16.38_2019.csv",
    encoding="utf-8", skiprows=[0, 1]
)
wien_data.index = pd.date_range("2011-01-01", periods=24 * 4 * 365, freq="900s")
wien_data = wien_data.rename(columns={"Temperature": "T", "Wind Speed": "Wspd"})

# ── Shared preprocessing ─────────────────────────────────────────────────────
def to_numeric_col(df, col):
    if df[col].dtype == object:
        df[col] = df[col].str.replace(",", ".", regex=False)
    df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

for col in ["DNI", "DHI", "GHI"]:
    auex = to_numeric_col(auex, col)

wien_data["GHI"] = pd.to_numeric(wien_data["GHI"], errors="coerce")

def strip_tz(df):
    df.index = pd.to_datetime(df.index)        # ensure DatetimeIndex first
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df

auex      = strip_tz(auex)
wien_data = strip_tz(wien_data)

# ── Labels ───────────────────────────────────────────────────────────────────
# Build AT label from lat/lon read out of the raw file
LABEL_A = f"AT ({lat}°N, {lon}°E)"
LABEL_B = "Wien (48.21°N, 16.38°E)"

COLORS = {
    "A_DNI": "#E63946", "B_DNI": "#457B9D",
    "A_DHI": "#F4A261", "B_DHI": "#2A9D8F",
    "A_GHI": "#E63946", "B_GHI": "#457B9D",
}

SEASON_MAP = {
    12: "Winter",  1: "Winter",  2: "Winter",
     3: "Spring",  4: "Spring",  5: "Spring",
     6: "Summer",  7: "Summer",  8: "Summer",
     9: "Autumn", 10: "Autumn", 11: "Autumn",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def save_fig(fig, name):
    path = os.path.join(results_folder, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")

def hourly_profile(df, cols, mask=None):
    subset = df[mask] if mask is not None else df
    return subset.groupby(subset.index.hour)[cols].mean()

# ═══════════════════════════════════════════════════════════════════════════════
# DNI / DHI PLOTS
# ═══════════════════════════════════════════════════════════════════════════════

def plot_dni_dhi_annual():
    prof_a = hourly_profile(auex,      ["DNI", "DHI"])
    prof_b = hourly_profile(wien_data, ["DNI", "DHI"])

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=False)
    fig.suptitle(f"Annual Average Hourly Profile\n{LABEL_A}  vs  {LABEL_B}",
                 fontsize=13, fontweight="bold")

    for ax, var, title in zip(
        axes,
        ["DNI", "DHI"],
        ["DNI (Direct Normal Irradiance)", "DHI (Diffuse Horizontal Irradiance)"],
    ):
        ax.plot(prof_a.index, prof_a[var], color=COLORS[f"A_{var}"], lw=2, label=LABEL_A)
        ax.plot(prof_b.index, prof_b[var], color=COLORS[f"B_{var}"], lw=2, label=LABEL_B, ls="--")
        ax.fill_between(prof_a.index, prof_a[var], prof_b[var],
                        alpha=0.15, color="grey", label="Difference")
        ax.set(xlabel="Hour of day", ylabel="W/m²", title=title)
        ax.set_xticks(range(0, 24, 2))
        ax.legend()
        ax.grid(True, ls="--", alpha=0.5)

    plt.tight_layout()
    save_fig(fig, "plot_dni_dhi_annual_hourly.png")


def plot_dni_dhi_seasonal(variable="DNI"):
    seasons = ["Winter", "Spring", "Summer", "Autumn"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharey=True)
    fig.suptitle(f"Seasonal Average Hourly Profile — {variable}\n{LABEL_A}  vs  {LABEL_B}",
                 fontsize=13, fontweight="bold")

    for ax, season in zip(axes.flatten(), seasons):
        months = [m for m, s in SEASON_MAP.items() if s == season]
        prof_a = hourly_profile(auex,      [variable], auex.index.month.isin(months))
        prof_b = hourly_profile(wien_data, [variable], wien_data.index.month.isin(months))

        ax.plot(prof_a.index, prof_a[variable], color=COLORS[f"A_{variable}"], lw=2, label=LABEL_A)
        ax.plot(prof_b.index, prof_b[variable], color=COLORS[f"B_{variable}"], lw=2, label=LABEL_B, ls="--")
        ax.fill_between(prof_a.index, prof_a[variable], prof_b[variable], alpha=0.15, color="grey")
        ax.set(title=season, xlabel="Hour of day", ylabel="W/m²")
        ax.set_xticks(range(0, 24, 2))
        ax.legend(fontsize=8)
        ax.grid(True, ls="--", alpha=0.5)

    plt.tight_layout()
    save_fig(fig, f"plot_dni_dhi_seasonal_{variable}.png")


def plot_dni_dhi_monthly():
    def monthly_mean_daily(df):
        daily = df[["DNI", "DHI"]].resample("D").sum()
        return daily.groupby(daily.index.month).mean()

    monthly_a = monthly_mean_daily(auex)
    monthly_b = monthly_mean_daily(wien_data)

    x      = np.arange(12)
    width  = 0.35
    labels = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"Monthly Mean Daily Total\n{LABEL_A}  vs  {LABEL_B}",
                 fontsize=13, fontweight="bold")

    for ax, var in zip(axes, ["DNI", "DHI"]):
        ax.bar(x - width/2, monthly_a[var], width, label=LABEL_A, color=COLORS[f"A_{var}"], alpha=0.85)
        ax.bar(x + width/2, monthly_b[var], width, label=LABEL_B, color=COLORS[f"B_{var}"], alpha=0.85)
        ax.set(title=var, ylabel="Wh/m²/day")
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.legend()
        ax.grid(True, axis="y", ls="--", alpha=0.5)

    plt.tight_layout()
    save_fig(fig, "plot_dni_dhi_monthly_totals.png")


def plot_dni_dhi_scatter():
    merged = auex[["DNI", "DHI"]].join(
        wien_data[["DNI", "DHI"]], lsuffix="_A", rsuffix="_B", how="inner"
    )
    merged = merged[(merged > 0).all(axis=1)]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle(f"Hourly Scatter (daytime only)\n{LABEL_A}  vs  {LABEL_B}",
                 fontsize=13, fontweight="bold")

    for ax, var in zip(axes, ["DNI", "DHI"]):
        x, y = merged[f"{var}_A"], merged[f"{var}_B"]
        lim  = max(x.max(), y.max())
        ax.scatter(x, y, alpha=0.2, s=5, color=COLORS[f"A_{var}"])
        ax.plot([0, lim], [0, lim], "k--", lw=1, label="1:1 line")
        ax.set(title=f"{var}  (r = {x.corr(y):.3f})",
               xlabel=f"{LABEL_A} (W/m²)", ylabel=f"{LABEL_B} (W/m²)")
        ax.legend()
        ax.grid(True, ls="--", alpha=0.4)

    plt.tight_layout()
    save_fig(fig, "plot_dni_dhi_scatter.png")


# ═══════════════════════════════════════════════════════════════════════════════
# GHI PLOTS
# ═══════════════════════════════════════════════════════════════════════════════

def plot_ghi_annual():
    prof_a = hourly_profile(auex,      ["GHI"])
    prof_b = hourly_profile(wien_data, ["GHI"])

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(prof_a.index, prof_a["GHI"], color=COLORS["A_GHI"], lw=2, label=LABEL_A)
    ax.plot(prof_b.index, prof_b["GHI"], color=COLORS["B_GHI"], lw=2, label=LABEL_B, ls="--")
    ax.fill_between(prof_a.index, prof_a["GHI"], prof_b["GHI"],
                    alpha=0.15, color="grey", label="Difference")
    ax.set(title=f"Annual Average Hourly GHI Profile\n{LABEL_A}  vs  {LABEL_B}",
           xlabel="Hour of day", ylabel="W/m²")
    ax.set_xticks(range(0, 24, 2))
    ax.legend()
    ax.grid(True, ls="--", alpha=0.5)
    plt.tight_layout()
    save_fig(fig, "plot_ghi_annual_hourly.png")


def plot_ghi_seasonal():
    seasons = ["Winter", "Spring", "Summer", "Autumn"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharey=True)
    fig.suptitle(f"Seasonal Average Hourly GHI Profile\n{LABEL_A}  vs  {LABEL_B}",
                 fontsize=13, fontweight="bold")

    for ax, season in zip(axes.flatten(), seasons):
        months = [m for m, s in SEASON_MAP.items() if s == season]
        prof_a = hourly_profile(auex,      ["GHI"], auex.index.month.isin(months))
        prof_b = hourly_profile(wien_data, ["GHI"], wien_data.index.month.isin(months))

        ax.plot(prof_a.index, prof_a["GHI"], color=COLORS["A_GHI"], lw=2, label=LABEL_A)
        ax.plot(prof_b.index, prof_b["GHI"], color=COLORS["B_GHI"], lw=2, label=LABEL_B, ls="--")
        ax.fill_between(prof_a.index, prof_a["GHI"], prof_b["GHI"], alpha=0.15, color="grey")
        ax.set(title=season, xlabel="Hour of day", ylabel="W/m²")
        ax.set_xticks(range(0, 24, 2))
        ax.legend(fontsize=8)
        ax.grid(True, ls="--", alpha=0.5)

    plt.tight_layout()
    save_fig(fig, "plot_ghi_seasonal_hourly.png")


def plot_ghi_monthly():
    def monthly_mean_daily(df):
        daily = df["GHI"].resample("D").sum()
        return daily.groupby(daily.index.month).mean()

    monthly_a = monthly_mean_daily(auex)
    monthly_b = monthly_mean_daily(wien_data)

    x      = np.arange(12)
    width  = 0.35
    labels = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(x - width/2, monthly_a, width, label=LABEL_A, color=COLORS["A_GHI"], alpha=0.85)
    ax.bar(x + width/2, monthly_b, width, label=LABEL_B, color=COLORS["B_GHI"], alpha=0.85)
    ax.set(title=f"Monthly Mean Daily Total GHI\n{LABEL_A}  vs  {LABEL_B}",
           ylabel="Wh/m²/day")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(True, axis="y", ls="--", alpha=0.5)
    plt.tight_layout()
    save_fig(fig, "plot_ghi_monthly_totals.png")


def plot_ghi_scatter():
    merged = auex[["GHI"]].join(wien_data[["GHI"]], lsuffix="_A", rsuffix="_B", how="inner")
    merged = merged[(merged > 0).all(axis=1)]

    x, y = merged["GHI_A"], merged["GHI_B"]
    lim   = max(x.max(), y.max())

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(x, y, alpha=0.2, s=5, color=COLORS["A_GHI"])
    ax.plot([0, lim], [0, lim], "k--", lw=1, label="1:1 line")
    ax.set(title=f"Hourly GHI Scatter (daytime only)  r = {x.corr(y):.3f}\n{LABEL_A}  vs  {LABEL_B}",
           xlabel=f"{LABEL_A} GHI (W/m²)", ylabel=f"{LABEL_B} GHI (W/m²)")
    ax.legend()
    ax.grid(True, ls="--", alpha=0.4)
    plt.tight_layout()
    save_fig(fig, "plot_ghi_scatter.png")


# ═══════════════════════════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════════════════════════
plot_dni_dhi_annual()
plot_dni_dhi_seasonal("DNI")
plot_dni_dhi_seasonal("DHI")
plot_dni_dhi_monthly()
plot_dni_dhi_scatter()

plot_ghi_annual()
plot_ghi_seasonal()
plot_ghi_monthly()
plot_ghi_scatter()