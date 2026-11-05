"""Energy demand under row scaling versus the EU.01 estimate: two full-year RenoVisor runs per row.

Evidence for storeys_geometry.md §8. Run eval_areas.py first, then from the repository root:

    python roadmap/renovisor/implementation/storeys_geometry/eval_runs.py <out_dir> [IE ...]

Each national SFH/TH row of the given countries (default IE) runs once with no element areas
(row scaling) and once with the EU.01 areas of eval_areas.py stated as area_in_m2, at 150 m2,
gas boiler, no measures. HiSim aggregates every element anyway, so a stated area per element
reproduces the estimate exactly. NL is accepted by the schema but every NL run exits 5 for want
of cost data (hisim-wec7). Writes <out_dir>/runs.csv; finished runs are reused.
"""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

AREA = 150.0
CSV = "hisim/inputs/housing/data_processed/episcope-tabula.csv"
BUILDING_TYPE = {"B_Alone": "detached_sfh", "B_N1": "semi_detached_sfh", "B_N2": "terraced_sfh"}
REQUEST_ELEMENTS = (("roof", "roof"), ("facade", "wall"), ("floor", "floor"), ("window", "window"), ("door", "door"))


def main(out_dir: Path, countries) -> None:
    res = pd.read_csv(out_dir / "geometry_eval.csv")
    res = res[(res["at"] == "A=150") & res.country.isin(countries)]
    tab = pd.read_csv(CSV, sep=";", encoding="latin-1", decimal=",", low_memory=False).set_index("Code_BuildingVariant")

    def request(row, with_estimate):
        code = row.code + ".Gen.ReEx.001.001"
        t = tab.loc[[code]].iloc[0]
        year = int(t["Year1_Building"]) if int(t["Year1_Building"]) > 1700 else int(t["Year2_Building"])
        building = {
            "building_type": "detached_sfh" if ".SFH." in code else BUILDING_TYPE[row.nb],
            "construction_year": year,
            "absolute_conditioned_floor_area_in_m2": AREA,
            "set_heating_temperature_in_celsius": 20,
            "tabula_building_code": code,
        }
        for element, field in REQUEST_ELEMENTS:
            building[element] = {"area_in_m2": round(float(row[f"{field}_est"]), 3)} if with_estimate else {}
        return {"schema_version": 1, "location": {"country": row.country},
                "house": {"building": building, "occupancy": {"number_of_residents": 3},
                          "heating": {"type_of_system": "conventional_gas_heating"},
                          "heat_distribution": {"type_of_system": "conventional_radiator"}},
                "applicant": {"main_residence": True}, "measures": []}

    def job(args):
        row, variant = args
        run_dir = out_dir / "runs" / f"{row.code}_{variant}"
        req = out_dir / "runs" / f"{row.code}_{variant}.yaml"
        req.parent.mkdir(parents=True, exist_ok=True)
        req.write_text(yaml.safe_dump(request(row, variant == "est"), sort_keys=False), encoding="utf-8")
        kpi_file = run_dir / "results" / "all_kpis.json"
        if not kpi_file.exists():
            command = [sys.executable, "-m", "hisim.renovisor", "run", str(req), "--out", str(run_dir),
                       "--cache-dir", str(out_dir / "cache")]
            p = subprocess.run(command, capture_output=True, text=True, check=False)
            if p.returncode:
                return {"code": row.code, "variant": variant, "error": f"exit {p.returncode}"}
        k = json.loads(kpi_file.read_text(encoding="utf-8"))["BUI1"]
        building, boiler = k["Building"], k["Gas Boiler"]
        return {"code": row.code, "variant": variant,
                "heat_demand_kWh": building["Theoretical heating demand (Building)"]["value"],
                "heating_load_W": building["Building heating load (Building)"]["value"],
                "gas_sh_kWh": boiler["Energy Gas consumption for space heating (CondensingGasBoiler)"]["value"]}

    jobs = [(r, v) for _, r in res.iterrows() for v in ("row", "est")]
    with ThreadPoolExecutor(4) as pool:
        runs = pd.DataFrame(list(pool.map(job, jobs)))
    runs.to_csv(out_dir / "runs.csv", index=False)
    if "error" in runs:
        print("failed runs:", runs.error.notna().sum())
        runs = runs[runs.error.isna()]
    p = runs.pivot(index="code", columns="variant", values=["heat_demand_kWh", "gas_sh_kWh", "heating_load_W"]).dropna()
    t = pd.DataFrame({"dDemand_%": 100 * (p["heat_demand_kWh"]["est"] / p["heat_demand_kWh"]["row"] - 1),
                      "dGas_%": 100 * (p["gas_sh_kWh"]["est"] / p["gas_sh_kWh"]["row"] - 1),
                      "dLoad_%": 100 * (p["heating_load_W"]["est"] / p["heating_load_W"]["row"] - 1)}
                     ).join(res.set_index("code")[["dH_%"]])
    print(t.round(1).to_string())
    print(t.describe(percentiles=[.05, .25, .5, .75, .95]).round(1).to_string())
    print("median dDemand/dH:", round((t["dDemand_%"] / t["dH_%"]).median(), 2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2:] or ["IE"])
