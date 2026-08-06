"""Documentation of the selection of a default PV module and inverter from the CEC database."""

# %%
# Import CEC database file
from pathlib import Path

import pandas as pd

# Resolve the CEC data files relative to this module so that importing it does
# not depend on the current working directory (the original notebook used a
# hardcoded ``data_processed/...`` path that only worked when the CWD was this
# directory).
_MODULE_DIR: Path = Path(__file__).resolve().parent
modules_data: pd.DataFrame = pd.read_csv(_MODULE_DIR / "data_processed" / "cec_modules.csv")

# %%
# Select the default PV module from the CEC database.
#
# The selection logic is isolated in :func:`select_pv_module` so it can be
# exercised with a small synthetic ``DataFrame`` in unit tests instead of
# requiring the full CSV file and a specific working directory. The module-level
# call below keeps the original behaviour identical.


def select_pv_module(modules_df: pd.DataFrame) -> pd.Series:
    """Select the default PV module from a CEC-modules ``DataFrame``.

    Filters the ``Mono-c-Si`` rows, computes the fill factor (``FF``), the
    maximum power (``P_max``) and the efficiency (``eff``), then keeps the most
    efficient ``Trina Solar`` module whose efficiency lies in ``[0.21, 0.225]``
    and whose ``P_max`` lies in ``[420, 440]``.

    Args:
        modules_df: A ``DataFrame`` with the CEC modules columns
            (``Technology``, ``V_mp_ref``, ``I_mp_ref``, ``V_oc_ref``,
            ``I_sc_ref``, ``A_c`` and ``Manufacturer``).

    Returns:
        The selected module as a ``Series`` -- the row with the highest
        efficiency among the matching ``Trina Solar`` candidates.
    """
    # #Calculate additional indicators
    # https://github.com/PV-Tutorials/pyData-2021-Solar-PV-Modeling/blob/main/Tutorial%20C%20-%20Modeling%20Module%27s%20Performance%20Advanced.ipynb
    data_mono = modules_df[modules_df["Technology"] == "Mono-c-Si"].copy()
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


default_module: pd.Series = select_pv_module(modules_data)

# %%
# Inverter
# https://energy.sandia.gov/wp-content/gallery/uploads/Performance-Model-for-Grid-Connected-Photovoltaic-Inverters.pdf
#
# The selection logic is isolated in :func:`select_inverter` so it can be
# exercised with a small synthetic ``DataFrame`` in unit tests instead of
# requiring the full CSV file and a specific working directory. The
# module-level call below keeps the original behaviour identical.


def select_inverter(inverter_df: pd.DataFrame, module: pd.Series) -> pd.Series:
    """Select the default inverter from a CEC-inverters ``DataFrame``.

    Computes the inverter efficiency (``eff`` = ``Paco`` / ``Pdco``) and keeps
    the most efficient inverter whose:

    * DC power (``Pdco``) exceeds the module's maximum power (``P_max``),
    * AC power (``Paco``) lies within ``[0.8, 1.2]`` times the module's
      ``P_max``,
    * DC voltage (``Vdco``) lies within ``[0.95, 1.05]`` times the module's
      MPP voltage (``V_mp_ref``),
    * maximum DC current (``Idcmax``) exceeds the module's MPP current
      (``I_mp_ref``).

    Args:
        inverter_df: A ``DataFrame`` with the CEC inverter columns
            (``Pdco``, ``Paco``, ``Vdco``, ``Idcmax``).
        module: The selected PV module as a ``Series``, providing
            ``P_max``, ``V_mp_ref`` and ``I_mp_ref``.

    Returns:
        The selected inverter as a ``Series`` -- the candidate row with the
        highest efficiency.

    Raises:
        IndexError: if no inverter satisfies all the constraints (the
            filtered frame is empty), mirroring the original inline behaviour.
    """
    inverter_df = inverter_df.copy()
    inverter_df["eff"] = inverter_df["Paco"] / inverter_df["Pdco"]

    candidate_inverters = inverter_df[
        (inverter_df["Pdco"].astype(float) > module["P_max"]) &
        (inverter_df["Paco"].astype(float) > 0.8 * module["P_max"]) &
        (inverter_df["Paco"].astype(float) < 1.2 * module["P_max"]) &
        (inverter_df["Vdco"].astype(float) > 0.95 * module["V_mp_ref"]) &
        (inverter_df["Vdco"].astype(float) < 1.05 * module["V_mp_ref"]) &
        (inverter_df["Idcmax"].astype(float) > module["I_mp_ref"])
    ]
    chosen_inverter = candidate_inverters.sort_values(by='eff').iloc[-1]
    return chosen_inverter


inverter_data: pd.DataFrame = pd.read_csv(_MODULE_DIR / "data_processed" / "cec_inverters.csv", header=[0, 1, 2])
inverter_data = inverter_data.droplevel(level=[1, 2], axis=1)

selected_inverter: pd.Series = select_inverter(inverter_data, default_module)
