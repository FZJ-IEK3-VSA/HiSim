# %% Load packages & Setup
import pandas as pd
import geopandas as gpd
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

# raw_folder    = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\raw"
raw_folder = r"H:\02_Projekte\04_Repositories\HiSim\TRY_au_data\2011-2030\raw"

# %% Check for jumps during leap years

days_per_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                  7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

COLS_RENAME = {"WG": "Wspd", "Monat": "Month", "Tag": "Day",
               "Stunde": "Hour", "LT": "AirTemp", "RF": "RelHum", "KWSU": "GHI"}
NUMERIC_FLOAT = ["AirTemp", "RelHum", "GHI", "Wspd"]
NUMERIC_INT   = ["Month", "Day", "Hour"]

# ── Load & filter files ───────────────────────────────────────────────────────
def load_file(filepath):
    raw = pd.read_excel(filepath, header=None, skiprows=1)
    region = raw.iloc[0, 1]
    df = raw.iloc[8:].copy()
    df.columns = raw.iloc[5]
    df = (df.iloc[:, 1:8]
            .reset_index(drop=True)
            .drop_duplicates(keep='first')
            .rename(columns=COLS_RENAME))
    for col in NUMERIC_FLOAT:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(',', '.', regex=False), errors='coerce')
    for col in NUMERIC_INT:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df, region


def analyse_file(df):
    """Return daily stats and Feb28→Mar1 jump metrics."""
    # Remove duplicate hours
    dup_mask = df.duplicated(subset=['Month', 'Day', 'Hour'], keep='first')
    df = df[~dup_mask]

    daily = (df.groupby(['Month', 'Day'])[['AirTemp', 'RelHum']]
               .mean()
               .reset_index()
               .sort_values(['Month', 'Day'])
               .reset_index(drop=True))

    changes_T   = daily['AirTemp'].diff().abs().dropna()
    changes_hum = daily['RelHum'].diff().abs().dropna()

    feb28 = daily[(daily['Month'] == 2) & (daily['Day'] == 28)]
    mar1  = daily[(daily['Month'] == 3) & (daily['Day'] == 1)]
    if feb28.empty or mar1.empty:
        return None, None, None

    def jump_stats(jump, changes):
        mean, std = changes.mean(), changes.std()
        z = (jump - mean) / std if std > 0 else 0
        _, p = stats.ttest_1samp(changes, jump)
        return round(mean, 2), round(std, 2), round(z, 2), round(p, 4), p < 0.05

    jump_T   = abs(mar1['AirTemp'].values[0]  - feb28['AirTemp'].values[0])
    jump_hum = abs(mar1['RelHum'].values[0]   - feb28['RelHum'].values[0])

    row = {
        "feb28_T":  round(feb28['AirTemp'].values[0], 2),
        "mar1_T":   round(mar1['AirTemp'].values[0],  2),
        "jump_T":   round(jump_T, 2),
        "feb28_hum": round(feb28['RelHum'].values[0], 2),
        "mar1_hum":  round(mar1['RelHum'].values[0],  2),
        "jump_hum":  round(jump_hum, 2),
    }
    for key, jump, changes in [("T", jump_T, changes_T), ("hum", jump_hum, changes_hum)]:
        mean, std, z, p, sig = jump_stats(jump, changes)
        row.update({f"mean_daily_change_{key}": mean, f"std_daily_change_{key}": std,
                    f"z_score_{key}": z, f"p_value_{key}": p, f"significant_{key}": sig})

    return row, changes_T.values, changes_hum.values


# ── Main loop ─────────────────────────────────────────────────────────────────
results, all_changes = [], {}

for file in os.listdir(raw_folder):
    if not file.endswith(".xlsx"):
        continue
    df, region = load_file(os.path.join(raw_folder, file))
    if not ((df["Month"] == 2) & (df["Day"] == 29)).any():
        print(f"Skipping {file} — no Feb 29")
        continue

    print(f"Processing {file} (has Feb 29)...")
    row, ch_T, ch_hum = analyse_file(df)
    if row is None:
        continue

    name = file.replace(".xlsx", "")
    row.update({"file": name, "region": region})
    results.append(row)
    all_changes[name] = {"T": ch_T, "hum": ch_hum}

results_df = pd.DataFrame(results).sort_values("z_score_T", ascending=False)
print("\n── Results sorted by temperature z-score ────────────────────────────")
print(results_df[["file", "jump_T", "mean_daily_change_T",
                   "z_score_T", "p_value_T", "significant_T"]].to_string(index=False))


# %% ── Plotting helpers ────────────────────────────────────────────────────────
BLUE, RED, GREEN = "#378ADD", "#E24B4A", "#1D9E75"

def savefig(fig, name):
    path = os.path.join(results_Path, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved → {path}")


# ── Figure 1: Results table ───────────────────────────────────────────────────
def plot_results_table(results_df):
    cols = {
        "T":   ["file", "feb28_T", "mar1_T", "jump_T", "mean_daily_change_T",
                 "std_daily_change_T", "z_score_T", "p_value_T", "significant_T"],
        "hum": ["file", "feb28_hum", "mar1_hum", "jump_hum", "mean_daily_change_hum",
                 "std_daily_change_hum", "z_score_hum", "p_value_hum", "significant_hum"],
    }
    titles   = {"T": "Temperature (°C)", "hum": "Humidity (%)"}
    sig_cols = {"T": "significant_T",    "hum": "significant_hum"}

    fig, axes = plt.subplots(2, 1, figsize=(16, 0.5 * len(results_df) + 4))
    fig.suptitle("Feb 28 → Mar 1 Jump Analysis", fontsize=14, fontweight="bold", y=1.01)

    for ax, key in zip(axes, ["T", "hum"]):
        df = results_df[cols[key]].copy()
        sig_col = sig_cols[key]

        cell_colors = [
            ["#d4f7d4" if (col == sig_col and row[col]) else
             "#ffd6d6" if (col == sig_col) else "#f9f9f9"
             for col in df.columns]
            for _, row in df.iterrows()
        ]

        ax.axis("off")
        ax.set_title(titles[key], fontweight="bold", loc="left", pad=8)
        tbl = ax.table(cellText=df.astype(str).values.tolist(),
                       colLabels=df.columns.tolist(),
                       cellColours=cell_colors,
                       loc="center", cellLoc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.auto_set_column_width(col=list(range(len(df.columns))))
        for c in range(len(df.columns)):
            tbl[0, c].set_facecolor("#333333")
            tbl[0, c].set_text_props(color="white", fontweight="bold")

    fig.legend(handles=[mpatches.Patch(color="#d4f7d4", label="Significant (p < 0.05)"),
                        mpatches.Patch(color="#ffd6d6", label="Not significant")],
               loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.02), fontsize=9)
    fig.tight_layout()
    savefig(fig, "jump_analysis_results.png")


# ── Figure 2: Z-score bar chart ───────────────────────────────────────────────
def plot_zscores(results_df):
    fig, ax = plt.subplots(figsize=(13, 5))
    colors = [RED if s else BLUE for s in results_df["significant_T"]]
    ax.bar(range(len(results_df)), results_df["z_score_T"], color=colors, width=0.6)
    for y in (1.96, -1.96):
        ax.axhline(y, color=RED, linestyle="--", linewidth=1,
                   label="p=0.05 (z=1.96)" if y > 0 else "")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_xticks(range(len(results_df)))
    ax.set_xticklabels(results_df["file"], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Z-score of Feb28→Mar1 jump")
    ax.set_title("Statistical significance of Feb 28 → Mar 1 temperature jump (leap year files only)")
    ax.legend(handles=[mpatches.Patch(color=RED,  label="Significant (p<0.05)"),
                       mpatches.Patch(color=BLUE, label="Not significant")], fontsize=9)
    plt.tight_layout()
    savefig(fig, "zscore_T_jump.png")


# ── Shared boxplot builder ────────────────────────────────────────────────────
def plot_boxplot(results_df, all_changes, var, ylabel, title, filename,
                 box_face, box_edge, med_color):
    labels  = list(results_df["file"])
    changes = [all_changes[f][var] for f in labels]
    sig_col = f"significant_{var}"
    jump_col = f"jump_{var}"

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.boxplot(changes, positions=range(len(labels)), widths=0.5, patch_artist=True,
               medianprops=dict(color=med_color, linewidth=1.5),
               boxprops=dict(facecolor=box_face, color=box_edge),
               whiskerprops=dict(color=box_edge), capprops=dict(color=box_edge),
               flierprops=dict(marker="o", markersize=3, markerfacecolor=box_face, alpha=0.4))

    for i, (_, row) in enumerate(results_df.iterrows()):
        ax.scatter(i, row[jump_col], color=RED if row[sig_col] else GREEN, zorder=5, s=70)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(handles=[mpatches.Patch(color=RED,       label="Feb28→Mar1 jump — significant"),
                       mpatches.Patch(color=GREEN,     label="Feb28→Mar1 jump — not significant"),
                       mpatches.Patch(color=box_face,  label="Distribution of all daily changes")],
               fontsize=9)
    plt.tight_layout()
    savefig(fig, filename)


# %% ── Generate all figures ───────────────────────────────────────────────────
plot_results_table(results_df)

plot_zscores(results_df)

plot_boxplot(results_df, all_changes,
             var="T", ylabel="Temperature change (°C)",
             title="Feb 28 → Mar 1 jump vs distribution of all daily changes (leap year files only)",
             filename="jump_vs_distribution_T.png",
             box_face="#B5D4F4", box_edge="#185FA5", med_color="#185FA5")

plot_boxplot(results_df, all_changes,
             var="hum", ylabel="Humidity change (%)",
             title="Feb 28 → Mar 1 humidity jump vs distribution of all daily changes (leap year files only)",
             filename="jump_vs_distribution_hum.png",
             box_face="#9FE1CB", box_edge="#0F6E56", med_color="#0F6E56")

#%% Compare GHI, DHI, DNI

results_folder = results_Path

# ── Load Austria data ────────────────────────────────────────────────────────
# raw_folder  = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\raw"
file        = "TRY__R1__Z1__LL11__A1__S1.xlsx"
data_au_raw = pd.read_excel(os.path.join(raw_folder, file), header=None, skiprows=1)
lon    = data_au_raw.iloc[0, 7]
lat    = data_au_raw.iloc[1, 7]
region = data_au_raw.iloc[0, 1]

# cleaned_folder = r"C:\Alvarez\HiSim\HiSim\TRY_au_data\2011-2030\cleaned"
cleaned_folder = r"H:\02_Projekte\04_Repositories\HiSim\TRY_au_data\2011-2030\cleaned"
auex = pd.read_csv(
    os.path.join(cleaned_folder, "TRY__R1__Z1__LL11__A1__S1.csv"),
    sep=";", index_col=0
)

# ── Load Vienna data ─────────────────────────────────────────────────────────
wien_data = pd.read_csv(
    # r"C:\Alvarez\HiSim\HiSim\hisim\inputs\weather\NSRDB_15min\Viena\902141_48.21_16.38_2019.csv",
    r"H:\02_Projekte\04_Repositories\HiSim\hisim\inputs\weather\NSRDB_15min\Viena\902141_48.21_16.38_2019.csv",
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
LABEL_A = f"TRY AT ({lat}°N, {lon}°E)"
LABEL_B = "NSRDB Wien (48.21°N, 16.38°E)"

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
# %%

base_path = Path(__file__).resolve().parent
geodata_FILEPATH = base_path / "../ai4c/data/external/STATISTIK_AUSTRIA_NUTS3_20260101/STATISTIK_AUSTRIA_NUTS3_20260101.shp"
geodata_FILEPATH = geodata_FILEPATH.resolve()

folder = "TRY_au_data/2011-2030/raw"
files = os.listdir(folder)

coords = []

for file in files:
    if not file.endswith(".xlsx"):
        continue
    filepath = os.path.join(folder, file)
    try:
        # skiprows=0: Zeile 1 ist leer/Header, Longitude in Zeile 2 (iloc[1,7]), Latitude in Zeile 3 (iloc[2,7])
        raw = pd.read_excel(filepath, header=None)
        
        # Debug: zeige erste Zeilen um Struktur zu prüfen
        print(f"\n{file} — erste 4 Zeilen, Spalten 5-8:")
        print(raw.iloc[0:4, 5:9].to_string())
        
        lon_raw = raw.iloc[1, 7]  # Zeile 2 (0-indexed: 1), Spalte H (0-indexed: 7)
        lat_raw = raw.iloc[2, 7]  # Zeile 3 (0-indexed: 2), Spalte H (0-indexed: 7)
        
        lon = pd.to_numeric(str(lon_raw).replace(",", "."), errors="coerce")
        lat = pd.to_numeric(str(lat_raw).replace(",", "."), errors="coerce")
        
        name = file.replace(".xlsx", "")
        coords.append({"file": name, "lon": lon, "lat": lat})
        print(f"  → lon={lon}, lat={lat}")
        
    except Exception as e:
        print(f"Could not read {file}: {e}")

coords_df = pd.DataFrame(coords).dropna(subset=["lon", "lat"])
print(f"\nFound {len(coords_df)} TRY files with valid coordinates")
print(coords_df)

# ── Load Austria shapefile ────────────────────────────────────────────────────
gdf_at = gpd.read_file(geodata_FILEPATH).to_crs(epsg=4326)

# ── Plot ──────────────────────────────────────────────────────────────────────
if len(coords_df) > 0:
    fig, ax = plt.subplots(figsize=(13, 7))

    gdf_at.plot(ax=ax, color="#f2f2f2", edgecolor="#aaaaaa", linewidth=0.8)

    palette = plt.cm.tab20.colors
    for i, row in coords_df.iterrows():
        color = palette[i % len(palette)]
        ax.scatter(row["lon"], row["lat"],
                   color=color, s=120, zorder=5,
                   edgecolors="white", linewidths=0.6,
                   label=row["file"])
        ax.annotate(row["file"],
                    xy=(row["lon"], row["lat"]),
                    xytext=(4, 4), textcoords="offset points",
                    fontsize=7, color="#333333")

    ax.set_xlim(9.3, 17.3)
    ax.set_ylim(46.3, 49.1)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"TRY file locations on Austria map (N={len(coords_df)})")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left",
              fontsize=7, title="TRY file", title_fontsize=8,
              frameon=True, borderaxespad=0)

    plt.tight_layout()
    try:
        save_path = os.path.join(results_Path, "TRY_locations_austria.png")
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Saved → {save_path}")
        plt.close(fig)
    except Exception:
        plt.show()
else:
    print("No valid coordinates found — check the debug output above!")
