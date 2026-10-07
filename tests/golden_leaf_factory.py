"""Golden leaves for the tests of the golden-reference scripts, built from their fields.

Every leaf is built by :func:`scripts.golden_kpis.golden_leaf` from a
:class:`~hisim.postprocessing.kpi_computation.kpi_address.KpiAddress`, so its key is the
address's dotted form and nothing here spells a key by hand.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from hisim.postprocessing.kpi_computation.kpi_address import KpiAddress
from hisim.postprocessing.kpi_computation.kpi_structure import KpiAddressStep, KpiSource
from scripts.golden_kpis import golden_leaf


def derived(name: str, value: Any, unit: str = "kWh", tag: str = "General", building: str = "BUI1") -> Tuple[
    str, Dict[str, Any]
]:
    """One derived KPI (no source) as ``(key, leaf)``."""
    address = KpiAddress(building=building, tag=tag, name=name, source=None)
    return address.dotted, golden_leaf(address, value, unit)


def component(
    name: str,
    source_name: str,
    value: Any,
    unit: str = "kWh",
    tag: str = "Building",
    building: str = "BUI1",
    member: Optional[str] = None,
    path: Tuple[Tuple[str, Optional[str]], ...] = (),
) -> Tuple[str, Dict[str, Any]]:
    """One component KPI as ``(key, leaf)``; the member defaults to the source's runtime name.

    ``path`` is the source's address path as ``(import, instance)`` steps, outermost first; its
    first step is the source's ``import`` and ``instance``, and an empty one makes a site component.
    """
    steps = tuple(KpiAddressStep(import_key=import_key, instance=instance) for import_key, instance in path)
    source = KpiSource(
        import_key=steps[0].import_key if steps else None,
        instance=steps[0].instance if steps else None,
        path=steps,
        member=source_name if member is None else member,
        assembly=None,
        name=source_name,
    )
    address = KpiAddress(building=building, tag=tag, name=name, source=source)
    return address.dotted, golden_leaf(address, value, unit)


def leaf_map(*items: Tuple[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """A golden leaf map from ``(key, leaf)`` items, in the order given."""
    return dict(items)


def general(values: Mapping[str, Any], unit: str = "kWh") -> Dict[str, Dict[str, Any]]:
    """A leaf map of derived ``General`` KPIs, one per ``name -> value``."""
    return leaf_map(*(derived(name, value, unit=unit) for name, value in values.items()))


def key_of(name: str, tag: str = "General", building: str = "BUI1") -> str:
    """The key of a derived KPI, from its fields."""
    return KpiAddress(building=building, tag=tag, name=name, source=None).dotted
