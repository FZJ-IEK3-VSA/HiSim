"""Row scaling (HiSim today) versus the TABULA EU.01 envelope estimate, per national SFH/TH row.

Evidence for storeys_geometry.md §8. Run from the repository root:

    python roadmap/renovisor/implementation/storeys_geometry/eval_areas.py <out_dir>

Writes <out_dir>/geometry_eval.csv and prints the summaries quoted in the spec.

Both area sets are turned into transmission the way HiSim's BuildingInformation does it: per
element, the row's area-weighted U_Actual times the element's largest b_Transmission, plus
delta_U_ThermalBridging (0.1 when the row says 0) times the total envelope area. The estimate
uses the row's own codes (storeys, attic, cellar, neighbours, complexity); a self-check
reproduces TABULA's own A_Estim_* columns.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

CSV = "hisim/inputs/housing/data_processed/episcope-tabula.csv"
ROW_PATTERN = r"^[A-Z]{2}\.N\.(SFH|TH)\.\d\d\.Gen\.ReEx\.001\.001$"

# EU.01 roof-plane parameters (p, q) by attic code (MOBASY 2021, Bild 45); the top ceiling's
# p and q are the row's own p_Ceiling / q_Ceiling columns.
ROOF_PQ = {"-": (1.2, 5.0), "0": (0.0, 0.0), "N": (0.0, 0.0), "P": (0.8, 7.0), "C": (1.6, 15.0),
           "NI": (1.6, 15.0), "PI": (1.6, 15.0)}
Q_NEIGHBOUR = {"B_Alone": 50.0, "B_N1": 25.0, "B_N2": 5.0}

ELEMENTS = {
    "roof": (["A_Roof_1", "A_Roof_2"], ["U_Actual_Roof_1", "U_Actual_Roof_2"],
             ["b_Transmission_Roof_1", "b_Transmission_Roof_2"]),
    "wall": (["A_Wall_1", "A_Wall_2", "A_Wall_3"], ["U_Actual_Wall_1", "U_Actual_Wall_2", "U_Actual_Wall_3"],
             ["b_Transmission_Wall_1", "b_Transmission_Wall_2", "b_Transmission_Wall_3"]),
    "floor": (["A_Floor_1", "A_Floor_2"], ["U_Actual_Floor_1", "U_Actual_Floor_2"],
              ["b_Transmission_Floor_1", "b_Transmission_Floor_2"]),
    "window": (["A_Window_1", "A_Window_2"], ["U_Actual_Window_1", "U_Actual_Window_2"], []),
    "door": (["A_Door_1"], ["U_Actual_Door_1"], []),
}


def load_rows() -> pd.DataFrame:
    df = pd.read_csv(CSV, sep=";", encoding="latin-1", decimal=",", low_memory=False)
    return df[df.Code_BuildingVariant.str.match(ROW_PATTERN, na=False)].copy()


def col(r, name) -> float:
    v = r.get(name)
    return 0.0 if v is None or pd.isna(v) else float(v)


def estimate(r, area, storeys=None):
    """EU.01 areas for conditioned floor area `area`, with the row's codes (storeys overridable)."""
    n0 = col(r, "n_Storey")
    n = n0 if storeys is None else storeys
    n_eff = col(r, "n_Storey_effective") - n0 + n
    n_env = col(r, "n_Storey_effective_envelope") - n0 + n
    a_s = area / n_eff
    p_r, q_r = ROOF_PQ[str(r.Code_AtticCond)]
    roof = col(r, "f_ComplexRoof") * (p_r * a_s + q_r) if p_r else 0.0
    ceiling = col(r, "p_Ceiling") * a_s + col(r, "q_Ceiling") if col(r, "p_Ceiling") else 0.0
    gross_storey = (0.7 * a_s + Q_NEIGHBOUR[r.Code_AttachedNeighbours]) * col(r, "f_ComplexFootprint") * col(
        r, "f_Corr_CeilingHeight")
    wall_soil = col(r, "A_Estim_Wall_ToCellarOrSoil") / col(r, "A_Estim_GrossWall_Storey") * gross_storey
    door = 0.01 * area + 1.5
    window = 0.18 * area - door
    wall = n_env * gross_storey - wall_soil - window - door
    floor = 1.2 * a_s + 5.0
    return {"roof": roof + ceiling, "wall": wall + wall_soil, "floor": floor, "window": window, "door": door,
            "_roof_plane": roof, "_ceiling": ceiling, "_wall_air": wall}


def row_areas(r, area):
    k = area / col(r, "A_C_Ref")
    out = {e: sum(col(r, a) for a in cols[0]) * k for e, cols in ELEMENTS.items()}
    if out["door"] == 0:  # HiSim substitutes TABULA's estimated door
        out["door"] = col(r, "A_Estim_Door") * k
    return out


def ub(r, e):
    areas, us, bs = ELEMENTS[e]
    a = np.array([col(r, x) for x in areas])
    u = np.array([col(r, x) for x in us])
    u_avg = (a * u).sum() / a.sum() if a.sum() > 0 else u[0]
    if e == "door" and u_avg == 0:
        u_avg = 3.0
    b = max([col(r, x) for x in bs]) if bs else 1.0
    return u_avg * b


def h_tr(r, areas):
    du = col(r, "delta_U_ThermalBridging") or 0.1
    return sum(ub(r, e) * areas[e] for e in ELEMENTS) + du * sum(areas[e] for e in ELEMENTS)


def main(out_dir: Path) -> None:
    rows = load_rows()
    records, sweep = [], []
    for _, r in rows.iterrows():
        a0 = col(r, "A_C_Ref")
        est0 = estimate(r, a0)
        check = max(abs(est0["_roof_plane"] - col(r, "A_Estim_Roof")),
                    abs(est0["_ceiling"] - col(r, "A_Estim_UpperCeiling")),
                    abs(est0["_wall_air"] - col(r, "A_Estim_Wall_ExtAir")),
                    abs(est0["floor"] - col(r, "A_Estim_Floor")),
                    abs(est0["window"] - col(r, "A_Estim_Window")), abs(est0["door"] - col(r, "A_Estim_Door")))
        code = r.Code_BuildingVariant.replace(".Gen.ReEx.001.001", "")
        kind = code.split(".")[2]
        for label, area in (("row A", a0), ("A=150", 150.0)):
            ra, ea = row_areas(r, area), estimate(r, area)
            h_row, h_est = h_tr(r, ra), h_tr(r, ea)
            rec = {"code": code, "country": r.Code_Country, "type": kind, "at": label, "A": area,
                   "n": col(r, "n_Storey"), "attic": r.Code_AtticCond, "cellar": r.Code_CellarCond,
                   "nb": r.Code_AttachedNeighbours, "selfcheck_m2": check, "H_row": h_row, "H_est": h_est,
                   "dH_%": 100 * (h_est / h_row - 1)}
            for e in ELEMENTS:
                rec[f"{e}_row"], rec[f"{e}_est"] = ra[e], ea[e]
                rec[f"d{e}_%"] = 100 * (ea[e] / ra[e] - 1) if ra[e] > 0 else np.nan
            records.append(rec)
        h = {n: h_tr(r, estimate(r, 150.0, storeys=n)) for n in (1, 2, 3)}
        sweep.append({"type": kind, "1_vs_2_%": 100 * (h[1] / h[2] - 1), "3_vs_2_%": 100 * (h[3] / h[2] - 1)})

    res = pd.DataFrame(records)
    out_dir.mkdir(parents=True, exist_ok=True)
    res.to_csv(out_dir / "geometry_eval.csv", index=False)
    print("rows:", res.code.nunique(), " self-check max |estimate - A_Estim_*| =",
          round(res.selfcheck_m2.max(), 3), "m2")
    for at in ("row A", "A=150"):
        s = res[res["at"] == at]
        print(f"\n=== estimate vs row scaling, at {at} ===")
        for c in ["dH_%", "droof_%", "dwall_%", "dfloor_%", "dwindow_%", "ddoor_%"]:
            x = s[c].dropna()
            print(f"{c:10s} p5/p25/median/p75/p95: {x.quantile(.05):+.0f} / {x.quantile(.25):+.0f} / "
                  f"{x.median():+.0f} / {x.quantile(.75):+.0f} / {x.quantile(.95):+.0f}   "
                  f"|>20%|: {(x.abs() > 20).mean():.0%}")
        print(s.groupby("country")["dH_%"].agg(["count", "median", "min", "max"]).round(1).to_string())
    sw = pd.DataFrame(sweep)
    print("\n=== storey effect under the estimate, A=150 ===")
    print(sw[["1_vs_2_%", "3_vs_2_%"]].describe(percentiles=[.05, .5, .95]).round(1).to_string())
    print(sw.groupby("type")[["1_vs_2_%", "3_vs_2_%"]].median().round(1).to_string())


if __name__ == "__main__":
    main(Path(sys.argv[1]))
