"""Tests for the default PV-module selection from the CEC database.

These tests cover :func:`hisim.inputs.photovoltaic.module_selection.select_pv_module`,
the pure selection logic that was previously executed inline at module import
time with no function boundary.
"""
from __future__ import annotations

import pandas as pd
import pytest

from hisim.inputs.photovoltaic.module_selection import (
    select_pv_module,
    default_module as real_selected_module,
)


def _row(
    name: str,
    manufacturer: str,
    technology: str,
    v_mp: float,
    i_mp: float,
    v_oc: float,
    i_sc: float,
    a_c: float,
) -> dict[str, str | float]:
    """Build a single CEC-module-shaped record for the synthetic frame."""
    return {
        "Name": name,
        "Manufacturer": manufacturer,
        "Technology": technology,
        "V_mp_ref": v_mp,
        "I_mp_ref": i_mp,
        "V_oc_ref": v_oc,
        "I_sc_ref": i_sc,
        "A_c": a_c,
    }


@pytest.mark.base
def test_select_pv_module_picks_most_efficient_trina_in_range() -> None:
    """select_pv_module filters Mono-c-Si/Trina Solar/in-range and takes max eff.

    Builds a small synthetic CEC-modules frame with six rows:
      * ``B`` -- Trina Solar, Mono-c-Si, in range, highest efficiency (expected).
      * ``A`` -- Trina Solar, Mono-c-Si, in range, lower efficiency.
      * ``C`` -- same as ``B`` but a different manufacturer (must be excluded).
      * ``D`` -- Trina Solar, Mono-c-Si, efficiency and P_max above the range.
      * ``E`` -- Trina Solar, Mono-c-Si, P_max above the range / eff below.
      * ``F`` -- Trina Solar, Thin Film (must be excluded by technology).
    """
    data = pd.DataFrame(
        [
            # B: P_max = V_mp * I_mp = 435.0, eff = 435 / (1000 * 1.97) ~ 0.2208
            _row("B", "Trina Solar", "Mono-c-Si", 43.5, 10.0, 50.0, 10.5, 1.97),
            # A: P_max = 428.4, eff = 0.2142 (in range, lower than B)
            _row("A", "Trina Solar", "Mono-c-Si", 42.0, 10.2, 50.0, 10.5, 2.00),
            # C: identical to B but not Trina Solar -> excluded by manufacturer
            _row("C", "OtherCo", "Mono-c-Si", 43.5, 10.0, 50.0, 10.5, 1.97),
            # D: P_max = 500 (>440) and eff ~0.278 (>0.225) -> out of range
            _row("D", "Trina Solar", "Mono-c-Si", 50.0, 10.0, 55.0, 11.0, 1.80),
            # E: P_max = 500 (>440), eff = 0.20 (<0.21) -> out of range
            _row("E", "Trina Solar", "Mono-c-Si", 50.0, 10.0, 55.0, 11.0, 2.50),
            # F: right numbers but Thin Film -> excluded by technology
            _row("F", "Trina Solar", "Thin Film", 43.5, 10.0, 50.0, 10.5, 1.97),
        ]
    )

    result = select_pv_module(data)

    # The result is a single row (Series).
    assert isinstance(result, pd.Series)

    # B is the only in-range Trina Solar Mono-c-Si row with the highest eff.
    assert result["Name"] == "B"
    assert result["Manufacturer"] == "Trina Solar"
    assert result["Technology"] == "Mono-c-Si"

    # The derived indicators are computed as documented.
    expected_ff = (43.5 * 10.0) / (50.0 * 10.5)
    expected_p_max = 50.0 * 10.5 * expected_ff  # == V_mp * I_mp == 435.0
    expected_eff = expected_p_max / (1000 * 1.97)
    assert result["FF"] == pytest.approx(expected_ff)
    assert result["P_max"] == pytest.approx(expected_p_max)
    assert result["P_max"] == pytest.approx(435.0)
    assert result["eff"] == pytest.approx(expected_eff)

    # The selection invariants hold.
    assert 0.21 <= result["eff"] <= 0.225
    assert 420 <= result["P_max"] <= 440


@pytest.mark.base
def test_select_pv_module_excludes_non_matching_rows() -> None:
    """Rows outside the technology/manufacturer/range filters are never returned."""
    # Only a non-Trina Mono-c-Si row in range -> no Trina Solar candidate.
    data = pd.DataFrame(
        [
            _row("X", "OtherCo", "Mono-c-Si", 43.5, 10.0, 50.0, 10.5, 1.97),
            _row("Y", "Trina Solar", "Thin Film", 43.5, 10.0, 50.0, 10.5, 1.97),
        ]
    )
    # No matching candidate -> iloc[-1] on an empty frame raises IndexError.
    with pytest.raises(IndexError):
        select_pv_module(data)


@pytest.mark.base
def test_module_level_selection_satisfies_invariants() -> None:
    """Importing the module selects a real Trina Solar Mono-c-Si module in range.

    This guards that the module is importable from any working directory (the
    CSV paths are resolved relative to the file) and that the documented
    selection invariants hold for the real CEC database.
    """
    sm = real_selected_module
    assert isinstance(sm, pd.Series)
    assert sm["Manufacturer"] == "Trina Solar"
    assert sm["Technology"] == "Mono-c-Si"
    assert 0.21 <= float(sm["eff"]) <= 0.225
    assert 420 <= float(sm["P_max"]) <= 440
