"""Tests for :meth:`hisim.simulator.Simulator.get_std_results`.

``get_std_results`` resamples the per-timestep results DataFrame into monthly,
daily, hourly, and cumulative aggregations, using mean aggregation for units in
``UNITS_USING_MEAN_AGGREGATION`` and sum aggregation for everything else.

These tests drive the method directly with a lightweight stub (no full
simulation) and assert that the bulk-resample-by-group implementation produces
results that are numerically identical to a straightforward per-column
reference, preserves the original column order, handles the hourly-resample
shortcut, and survives empty aggregation groups (all-mean / all-sum).
"""

# clean

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.simulator import Simulator


def _make_outputs(units: list[lt.Units]) -> list[cp.ComponentOutput]:
    """Build ComponentOutput objects with the requested units and unique names."""
    outputs: list[cp.ComponentOutput] = []
    for i, unit in enumerate(units):
        outputs.append(
            cp.ComponentOutput(
                object_name=f"Comp{i}",
                field_name=f"Output{i}",
                load_type=lt.LoadTypes.ANY,
                unit=unit,
                component_id=cp.ComponentID(f"Comp{i}"),
            )
        )
    return outputs


def _make_stub(units: list[lt.Units], seconds_per_timestep: int) -> SimpleNamespace:
    """Build a minimal stand-in for Simulator carrying only what get_std_results reads."""
    return SimpleNamespace(
        all_outputs=_make_outputs(units),
        _simulation_parameters=SimpleNamespace(seconds_per_timestep=seconds_per_timestep),
    )


def _reference_std_results(
    df: pd.DataFrame,
    outputs: list[cp.ComponentOutput],
    seconds_per_timestep: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Per-column reference implementation (the pre-refactor behaviour)."""
    units_mean = lt.UNITS_USING_MEAN_AGGREGATION
    use_hourly_resample = seconds_per_timestep != 3600
    monthly_frames: list[pd.Series] = []
    daily_frames: list[pd.Series] = []
    hourly_frames: list[pd.Series] = []
    cumulative_data: dict = {}
    for i, column_name in enumerate(df.columns):
        col_data = df.iloc[:, i]
        unit = outputs[i].unit
        if unit in units_mean:
            monthly = col_data.resample("ME").mean()
            daily = col_data.resample("D").mean()
            hourly = col_data.resample("60min").mean() if use_hourly_resample else col_data
            cumulative = col_data.mean()
        else:
            monthly = col_data.resample("ME").sum()
            daily = col_data.resample("D").sum()
            hourly = col_data.resample("60min").sum() if use_hourly_resample else col_data
            cumulative = col_data.sum()
        monthly_frames.append(monthly.rename(column_name))
        daily_frames.append(daily.rename(column_name))
        hourly_frames.append(hourly.rename(column_name))
        cumulative_data[column_name] = cumulative
    return (
        pd.DataFrame([cumulative_data]),
        pd.concat(monthly_frames, axis=1),
        pd.concat(daily_frames, axis=1),
        pd.concat(hourly_frames, axis=1),
    )


def _make_frame(units: list[lt.Units], seconds_per_timestep: int, seed: int = 0) -> pd.DataFrame:
    """Build a deterministic results DataFrame with a matching DatetimeIndex."""
    n_steps = 24 * 90  # 90 days of data
    index = pd.date_range(
        start="2021-01-01",
        periods=n_steps,
        freq=f"{seconds_per_timestep}s",
    )
    rng = np.random.RandomState(seed)
    data = {f"Comp{j} - Output{j} [ANY - {u}]": rng.rand(n_steps) * (10 if j % 2 else 100) for j, u in enumerate(units)}
    return pd.DataFrame(data, index=index)


@pytest.mark.base
@pytest.mark.parametrize("seconds_per_timestep", [60, 900, 3600])
def test_get_std_results_matches_per_column_reference(seconds_per_timestep: int) -> None:
    """Bulk-resample output must be byte-for-byte identical to per-column reference."""
    units = [
        lt.Units.CELSIUS,  # mean
        lt.Units.KWH,  # sum
        lt.Units.WATT,  # mean
        lt.Units.KWH,  # sum
        lt.Units.PERCENT,  # mean
    ]
    df = _make_frame(units, seconds_per_timestep)
    stub = _make_stub(units, seconds_per_timestep)

    cumulative, monthly, daily, hourly = Simulator.get_std_results(stub, df)
    ref_cum, ref_mon, ref_day, ref_hr = _reference_std_results(df, stub.all_outputs, seconds_per_timestep)

    assert list(cumulative.columns) == list(df.columns)
    assert list(monthly.columns) == list(df.columns)
    assert list(daily.columns) == list(df.columns)
    assert list(hourly.columns) == list(df.columns)

    pd.testing.assert_frame_equal(cumulative, ref_cum)
    pd.testing.assert_frame_equal(monthly, ref_mon)
    pd.testing.assert_frame_equal(daily, ref_day)
    pd.testing.assert_frame_equal(hourly, ref_hr)


@pytest.mark.base
def test_get_std_results_column_order_preserved() -> None:
    """Columns must come back in the original order, not mean-then-sum grouped order."""
    # Interleave mean/sum units so a naive mean-first concat would reorder.
    units = [lt.Units.KWH, lt.Units.WATT, lt.Units.KWH, lt.Units.CELSIUS, lt.Units.KWH]
    seconds_per_timestep = 900
    df = _make_frame(units, seconds_per_timestep)
    stub = _make_stub(units, seconds_per_timestep)

    _, monthly, daily, hourly = Simulator.get_std_results(stub, df)

    assert list(monthly.columns) == list(df.columns)
    assert list(daily.columns) == list(df.columns)
    assert list(hourly.columns) == list(df.columns)


@pytest.mark.base
def test_get_std_results_mean_vs_sum_aggregation() -> None:
    """Mean-aggregated units must be averaged; sum-aggregated units must be summed."""
    units = [lt.Units.CELSIUS, lt.Units.KWH]
    seconds_per_timestep = 3600  # hourly grid -> hourly resample skipped
    df = _make_frame(units, seconds_per_timestep, seed=3)
    stub = _make_stub(units, seconds_per_timestep)

    cumulative, monthly, _daily, hourly = Simulator.get_std_results(stub, df)

    mean_col = df.columns[0]
    sum_col = df.columns[1]

    # Cumulative: mean for CELSIUS, sum for KWH.
    assert cumulative[mean_col].iloc[0] == pytest.approx(df[mean_col].mean())
    assert cumulative[sum_col].iloc[0] == pytest.approx(df[sum_col].sum())

    # Monthly aggregation: mean vs sum per group.
    assert monthly[mean_col].equals(df[mean_col].resample("ME").mean())
    assert monthly[sum_col].equals(df[sum_col].resample("ME").sum())

    # On an hourly grid the hourly frame is the raw data, unchanged.
    pd.testing.assert_frame_equal(hourly, df)


@pytest.mark.base
@pytest.mark.parametrize(
    "units",
    [
        [lt.Units.CELSIUS, lt.Units.KELVIN, lt.Units.WATT],  # all mean
        [lt.Units.KWH, lt.Units.KWH, lt.Units.KWH],  # all sum
        [lt.Units.CELSIUS],  # single mean
        [lt.Units.KWH],  # single sum
    ],
    ids=["all-mean", "all-sum", "single-mean", "single-sum"],
)
def test_get_std_results_empty_aggregation_group(units: list[lt.Units]) -> None:
    """An empty mean- or sum-group must not break aggregation or reordering."""
    seconds_per_timestep = 900
    df = _make_frame(units, seconds_per_timestep, seed=7)
    stub = _make_stub(units, seconds_per_timestep)

    cumulative, monthly, daily, hourly = Simulator.get_std_results(stub, df)
    ref_cum, ref_mon, ref_day, ref_hr = _reference_std_results(df, stub.all_outputs, seconds_per_timestep)

    assert list(monthly.columns) == list(df.columns)
    assert list(daily.columns) == list(df.columns)
    assert list(hourly.columns) == list(df.columns)
    pd.testing.assert_frame_equal(cumulative, ref_cum)
    pd.testing.assert_frame_equal(monthly, ref_mon)
    pd.testing.assert_frame_equal(daily, ref_day)
    pd.testing.assert_frame_equal(hourly, ref_hr)
