"""Compare the four car double-counting scenarios.

Reads each run's ``all_kpis.json`` and its exported result CSV and prints the electricity
balance side by side, plus the two derived quantities the study is about:

* how much electricity the LPG booked into the residents' profile for car charging
  (S2/S4 minus the transportation-free baseline), and
* how much the HiSim car chain adds on top (``BatteryChargingPowerToEMS``).

Run from this directory after the four simulations have finished::

    python compare_results.py
"""
# clean
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SECONDS_PER_TIMESTEP = 900

SCENARIOS = [
    ("s1_no_car", "S1 no car"),
    ("s2_lpg_car_only", "S2 LPG car only"),
    ("s3_car_element_only", "S3 car element only"),
    ("s4_lpg_car_and_car_element", "S4 LPG car + car element"),
]

#: Result columns of interest -> label. Matched as a substring of the CSV column header.
COLUMNS = {
    "UTSPConnector - ElectricalPowerConsumption": "LPG residents' electricity",
    "L1EVChargeControl_1 - BatteryChargingPowerToEMS": "HiSim car charging (to EMS)",
    "Car_2_22kW_Charging_Power_avg_Speed_30_kmh_0 - ElectricityOutput": "HiSim car driving demand",
    "Car_2_22kW_Charging_Power_avg_Speed_30_kmh_0 - DrivenMeters": "driven distance",
    "MoreAdvancedHeatPumpHPLib - ElectricalInputPowerTotalHeatpump": "heat pump electricity",
    "PVSystem - ElectricityOutput": "PV production",
    "L2EMSElectricityController - TotalElectricityConsumption": "EMS total consumption",
    "ElectricityMeter - ElectricityFromGridInWatt": "grid import",
    "ElectricityMeter - ElectricityToGridInWatt": "grid export",
}


def find_result_dir(stem):
    """Locate the newest result directory produced for *stem*."""
    hits = [os.path.dirname(p) for p in
            glob.glob(os.path.join(HERE, "results", "**", "all_kpis.json"), recursive=True)
            if stem in p]
    if not hits:
        hits = [os.path.dirname(p) for p in
                glob.glob(os.path.join(HERE, "results", "**", "*.csv"), recursive=True)
                if stem in p]
    return max(hits, key=os.path.getmtime) if hits else None


def read_series_totals(result_dir):
    """Sum every exported output time series, converting W to kWh.

    ``EXPORT_TO_CSV`` writes one file per output, with the column header carrying the
    unit, e.g. ``ElectricityMeter - ElectricityFromGridInWatt [Electricity - W]``.
    """
    import pandas as pd

    totals = {}
    for path in glob.glob(os.path.join(result_dir, "*.csv")):
        try:
            frame = pd.read_csv(path, index_col=0)
        except Exception:  # noqa: BLE001 - non-timeseries csv (cost dumps etc.)
            continue
        if len(frame.columns) != 1:
            continue
        column = frame.columns[0]
        series = pd.to_numeric(frame[column], errors="coerce")
        if series.isna().all():
            continue
        if column.rstrip().endswith("- W]"):
            totals[column] = series.sum() * SECONDS_PER_TIMESTEP / 3600 / 1000
        else:
            totals[column] = series.sum()
    return totals


def pick(totals, needle):
    """Return the total of the first column containing *needle*."""
    for column, value in totals.items():
        if needle in column:
            return value
    return None


def main():
    """Print the comparison table."""
    per_scenario = {}
    for stem, label in SCENARIOS:
        result_dir = find_result_dir(stem)
        if result_dir is None:
            print(f"!! no results found for {stem}")
            continue
        per_scenario[label] = (result_dir, read_series_totals(result_dir))

    labels = [label for _, label in SCENARIOS if label in per_scenario]
    width = 26
    print(f"\n{'quantity (kWh unless noted)':<32}" + "".join(f"{lab:>{width}}" for lab in labels))
    print("-" * (32 + width * len(labels)))
    for needle, nice in COLUMNS.items():
        row = ""
        for label in labels:
            value = pick(per_scenario[label][1], needle)
            row += f"{'--':>{width}}" if value is None else f"{value:>{width},.2f}"
        print(f"{nice:<32}{row}")

    print("\nresult directories:")
    for label in labels:
        print(f"  {label:<26} {per_scenario[label][0]}")


if __name__ == "__main__":
    main()
