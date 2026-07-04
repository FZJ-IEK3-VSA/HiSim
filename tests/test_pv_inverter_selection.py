"""Tests for the default inverter selection from the CEC database.

These tests cover :func:`hisim.inputs.photovoltaic.module_selection.select_inverter`,
the pure selection logic that was previously executed inline at module import
time with no function boundary.
"""
from __future__ import annotations

import pandas as pd
import pytest

from hisim.inputs.photovoltaic.module_selection import (
    select_inverter,
    selected_inverter as real_selected_inverter,
    default_module as real_selected_module,
)


def _default_module(p_max: float, v_mp: float, i_mp: float) -> pd.Series:
    """Build a minimal PV-module ``Series`` for the inverter selection."""
    return pd.Series({"P_max": p_max, "V_mp_ref": v_mp, "I_mp_ref": i_mp})


def _inv(
    name: str,
    pdco: float,
    paco: float,
    vdco: float,
    idcmax: float,
) -> dict[str, str | float]:
    """Build a single CEC-inverter-shaped record for the synthetic frame."""
    return {"Name": name, "Pdco": pdco, "Paco": paco, "Vdco": vdco, "Idcmax": idcmax}


@pytest.mark.base
def test_select_inverter_picks_most_efficient_matching() -> None:
    """select_inverter keeps the constraints and takes the highest-eff row.

    Builds a small synthetic inverter frame around a module with
    ``P_max=430``, ``V_mp_ref=40``, ``I_mp_ref=10`` (so the constraints become
    ``Pdco>430``, ``344<Paco<516``, ``38<Vdco<42``, ``Idcmax>10``):

      * ``Good1`` -- matches all constraints, eff = 1.0 (expected pick).
      * ``Good2`` -- matches all constraints, eff = 0.96 (lower efficiency).
      * ``Bad_Pdco`` -- ``Pdco`` too small.
      * ``Bad_Paco_low`` / ``Bad_Paco_high`` -- ``Paco`` out of range.
      * ``Bad_Vdco_low`` / ``Bad_Vdco_high`` -- ``Vdco`` out of range.
      * ``Bad_Idcmax`` -- ``Idcmax`` too small.
    """
    module = _default_module(p_max=430.0, v_mp=40.0, i_mp=10.0)
    data = pd.DataFrame(
        [
            # Good1: Pdco=450>430, 344<450<516, 38<40<42, 12>10 -> eff=1.0
            _inv("Good1", 450.0, 450.0, 40.0, 12.0),
            # Good2: Pdco=500>430, 344<480<516, 38<41<42, 11>10 -> eff=0.96
            _inv("Good2", 500.0, 480.0, 41.0, 11.0),
            # Bad_Pdco: Pdco=420 not > 430 -> excluded
            _inv("Bad_Pdco", 420.0, 450.0, 40.0, 12.0),
            # Bad_Paco_low: Paco=300 not > 0.8*430=344 -> excluded
            _inv("Bad_Paco_low", 500.0, 300.0, 40.0, 12.0),
            # Bad_Paco_high: Paco=600 not < 1.2*430=516 -> excluded
            _inv("Bad_Paco_high", 500.0, 600.0, 40.0, 12.0),
            # Bad_Vdco_low: Vdco=37 not > 0.95*40=38 -> excluded
            _inv("Bad_Vdco_low", 500.0, 450.0, 37.0, 12.0),
            # Bad_Vdco_high: Vdco=43 not < 1.05*40=42 -> excluded
            _inv("Bad_Vdco_high", 500.0, 450.0, 43.0, 12.0),
            # Bad_Idcmax: Idcmax=9 not > 10 -> excluded
            _inv("Bad_Idcmax", 500.0, 450.0, 40.0, 9.0),
        ]
    )

    result = select_inverter(data, module)

    # The result is a single row (Series).
    assert isinstance(result, pd.Series)

    # Good1 has the highest efficiency among the matching candidates.
    assert result["Name"] == "Good1"

    # The derived efficiency is computed as documented.
    assert result["eff"] == pytest.approx(450.0 / 450.0)

    # The selection invariants hold.
    assert float(result["Pdco"]) > module["P_max"]
    assert 0.8 * module["P_max"] < float(result["Paco"]) < 1.2 * module["P_max"]
    assert 0.95 * module["V_mp_ref"] < float(result["Vdco"]) < 1.05 * module["V_mp_ref"]
    assert float(result["Idcmax"]) > module["I_mp_ref"]


@pytest.mark.base
def test_select_inverter_does_not_mutate_input() -> None:
    """select_inverter must not add the derived ``eff`` column to its input."""
    module = _default_module(p_max=430.0, v_mp=40.0, i_mp=10.0)
    data = pd.DataFrame([_inv("Good1", 450.0, 450.0, 40.0, 12.0)])
    assert "eff" not in data.columns

    select_inverter(data, module)

    # The caller's frame is left untouched (no side effects).
    assert "eff" not in data.columns


@pytest.mark.base
def test_select_inverter_excludes_all_rows_raises() -> None:
    """When no inverter matches, iloc[-1] on the empty frame raises IndexError."""
    module = _default_module(p_max=430.0, v_mp=40.0, i_mp=10.0)
    # Every row violates at least one constraint.
    data = pd.DataFrame(
        [
            _inv("Bad_Pdco", 420.0, 450.0, 40.0, 12.0),
            _inv("Bad_Paco", 500.0, 600.0, 40.0, 12.0),
            _inv("Bad_Vdco", 500.0, 450.0, 43.0, 12.0),
            _inv("Bad_Idcmax", 500.0, 450.0, 40.0, 9.0),
        ]
    )
    with pytest.raises(IndexError):
        select_inverter(data, module)


@pytest.mark.base
def test_module_level_inverter_selection_satisfies_invariants() -> None:
    """Importing the module selects a real inverter satisfying the constraints.

    This guards that the module is importable from any working directory (the
    CSV paths are resolved relative to the file) and that the documented
    selection invariants hold for the real CEC database against the real
    default module.
    """
    inv = real_selected_inverter
    module = real_selected_module
    assert isinstance(inv, pd.Series)
    assert "eff" in inv.index
    assert float(inv["Pdco"]) > float(module["P_max"])
    assert 0.8 * float(module["P_max"]) < float(inv["Paco"]) < 1.2 * float(module["P_max"])
    assert 0.95 * float(module["V_mp_ref"]) < float(inv["Vdco"]) < 1.05 * float(module["V_mp_ref"])
    assert float(inv["Idcmax"]) > float(module["I_mp_ref"])
