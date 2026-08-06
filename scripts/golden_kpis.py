"""Pure KPI helpers for the golden-reference system.

HiSim writes ``all_kpis.json`` as a nested structure
``{building: {category: {kpi_name: {"value": <number|str>, "unit": ...}}}}``.
:func:`flatten` turns that into a flat ``{dotted.key: value}`` mapping;
:func:`compare` diffs a fresh mapping against a stored golden one with numeric
tolerance. Both are side-effect-free and fully unit-testable without running
HiSim, so they carry no dependency on the ``hisim`` package.
"""
from __future__ import annotations

import math
from typing import Any

# Tolerance policy (spec §7): same-machine output is byte-exact, so a very tight
# relative tolerance still passes while absorbing sub-ULP cross-platform drift.
REL_TOL = 1e-9
ABS_TOL = 0.0


def _is_number(value: Any) -> bool:
    """True for real numbers (``bool`` excluded — it is compared exactly)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def flatten(all_kpis: Any) -> dict[str, Any]:
    """Flatten an ``all_kpis.json`` tree into ``{dotted.key: value}``.

    A dict carrying a non-dict ``"value"`` entry is treated as a KPI leaf and
    contributes ``value`` under its dotted path. All other dicts are recursed
    into. Numeric leaves are coerced to ``float``; non-numeric leaves (strings,
    ``None``) are kept as-is for exact comparison.
    """
    out: dict[str, Any] = {}

    def _walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            if "value" in node and not isinstance(node["value"], dict):
                out[prefix.rstrip(".")] = _coerce(node["value"])
                return
            for key, child in node.items():
                _walk(child, f"{prefix}{key}.")
        else:
            # A bare non-dict leaf (uncommon) — record it under its path.
            out[prefix.rstrip(".")] = _coerce(node)

    _walk(all_kpis, "")
    out.pop("", None)  # guard against a degenerate empty-key root
    return out


def _coerce(value: Any) -> Any:
    """Coerce numeric values to ``float``; leave everything else untouched."""
    return float(value) if _is_number(value) else value


def _format_numeric_change(name: str, key: str, ref_v: float, got_v: float, rel_tol: float, abs_tol: float) -> str:
    """Explain a numeric KPI divergence, including the magnitude and the tolerance exceeded."""
    abs_diff = abs(got_v - ref_v)
    rel_diff = abs_diff / abs(ref_v) if ref_v != 0 else math.inf
    rel_pct = "inf%" if math.isinf(rel_diff) else f"{rel_diff:.3%}"
    return (
        f"{name}: KPI '{key}' changed: ref={ref_v!r} got={got_v!r} "
        f"(abs diff={abs_diff:.6g}, rel diff={rel_pct}; "
        f"tolerance rel={rel_tol:g} abs={abs_tol:g})"
    )


def compare(
    name: str,
    got: dict[str, Any],
    ref: dict[str, Any],
    rel_tol: float = REL_TOL,
    abs_tol: float = ABS_TOL,
) -> list[str]:
    """Return a list of human-readable deviations between ``got`` and ``ref``.

    Numeric KPIs are compared with :func:`math.isclose`; non-numeric KPIs by
    exact equality. Numeric divergences report the absolute and relative delta
    plus the tolerance that was exceeded, so a reader can see exactly how far off
    each value is. Reports (in this order): KPIs missing from ``got``, KPIs whose
    value changed, then KPIs present only in ``got``. Ordering is deterministic
    (``ref`` insertion order, then sorted new keys).
    """
    errs: list[str] = []
    for key, ref_v in ref.items():
        if key not in got:
            errs.append(f"{name}: missing KPI '{key}' in current run")
            continue
        got_v = got[key]
        if _is_number(ref_v) and _is_number(got_v):
            if not math.isclose(got_v, ref_v, rel_tol=rel_tol, abs_tol=abs_tol):
                errs.append(_format_numeric_change(name, key, ref_v, got_v, rel_tol, abs_tol))
        elif got_v != ref_v:
            errs.append(f"{name}: KPI '{key}' changed: ref={ref_v!r} got={got_v!r}")
    for key in sorted(got.keys() - ref.keys()):
        errs.append(f"{name}: new KPI '{key}' not in reference (regenerate if intended)")
    return errs
