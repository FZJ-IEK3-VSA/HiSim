"""Documentation of the selection of a default PV module and inverter from the CEC database."""

# %%
# Import CEC database file
from pathlib import Path

import pandas as pd

# Resolve the CEC data files relative to this module so that importing it does
# not depend on the current working directory (the original notebook used a
# hardcoded ``data_processed/...`` path that only worked when the CWD was this
# directory).
_MODULE_DIR = Path(__file__).resolve().parent
data = pd.read_csv(_MODULE_DIR / "data_processed" / "cec_modules.csv")

# %%
# Select the default PV module from the CEC database.
#
# The selection logic is isolated in :func:`select_pv_module` so it can be
# exercised with a small synthetic ``DataFrame`` in unit tests instead of
# requiring the full CSV file and a specific working directory. The module-level
# call below keeps the original behaviour identical.


def select_pv_module(data: pd.DataFrame) -> pd.Series:
    """Select the default PV module from a CEC-modules ``DataFrame``.

    Filters the ``Mono-c-Si`` rows, computes the fill factor (``FF``), the
    maximum power (``P_max``) and the efficiency (``eff``), then keeps the most
    efficient ``Trina Solar`` module whose efficiency lies in ``[0.21, 0.225]``
    and whose ``P_max`` lies in ``[420, 440]``.

    Args:
        data: A ``DataFrame`` with the CEC modules columns (``Technology``,
            ``V_mp_ref``, ``I_mp_ref``, ``V_oc_ref``, ``I_sc_ref``, ``A_c`` and
            ``Manufacturer``).

    Returns:
        The selected module as a ``Series`` -- the row with the highest
        efficiency among the matching ``Trina Solar`` candidates.
    """
    # #Calculate additional indicators
    # https://github.com/PV-Tutorials/pyData-2021-Solar-PV-Modeling/blob/main/Tutorial%20C%20-%20Modeling%20Module%27s%20Performance%20Advanced.ipynb
    data_mono = data[data["Technology"] == "Mono-c-Si"].copy()
    for c in ["V_mp_ref", "I_mp_ref", "V_oc_ref", "I_sc_ref", "A_c"]:
        data_mono[c] = data_mono[c].astype(float)

    # https://www.pveducation.org/pvcdrom/solar-cell-operation/fill-factor
    data_mono.loc[:, "FF"] = (data_mono["V_mp_ref"] * data_mono["I_mp_ref"]) / (data_mono["V_oc_ref"] * data_mono["I_sc_ref"])

    # https://www.pveducation.org/pvcdrom/solar-cell-operation/solar-cell-efficiency
    data_mono.loc[:, "P_max"] = data_mono["V_oc_ref"] * data_mono["I_sc_ref"] * data_mono["FF"]
    data_mono.loc[:, "eff"] = data_mono.loc[:, "P_max"] / (1000 * data_mono["A_c"])

    # Select candidates, use most efficient by Trina Solar
    candidates = data_mono[(0.21 <= data_mono["eff"]) & (data_mono["eff"] <= 0.225) & (420 <= data_mono["P_max"]) & (data_mono["P_max"] <= 440)]
    selected_module = candidates[candidates["Manufacturer"] == "Trina Solar"].sort_values(by='eff').iloc[-1]
    return selected_module


selected_module = select_pv_module(data)

# %%
# Inverter
# https://energy.sandia.gov/wp-content/gallery/uploads/Performance-Model-for-Grid-Connected-Photovoltaic-Inverters.pdf
inverter_data = pd.read_csv(_MODULE_DIR / "data_processed" / "cec_inverters.csv", header=[0, 1, 2])
inverter_data = inverter_data.droplevel(level=[1, 2], axis=1)

inverter_data["eff"] = inverter_data["Paco"] / inverter_data["Pdco"]

candidate_inverters = inverter_data[
    (inverter_data["Pdco"].astype(float) > selected_module["P_max"]) &
    (inverter_data["Paco"].astype(float) > 0.8 * selected_module["P_max"]) &
    (inverter_data["Paco"].astype(float) < 1.2 * selected_module["P_max"]) &
    (inverter_data["Vdco"].astype(float) > 0.95 * selected_module["V_mp_ref"]) &
    (inverter_data["Vdco"].astype(float) < 1.05 * selected_module["V_mp_ref"]) &
    (inverter_data["Idcmax"].astype(float) > selected_module["I_mp_ref"])
]

selected_inverter = candidate_inverters.sort_values(by='eff').iloc[-1]
