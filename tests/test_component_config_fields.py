"""Structural invariant test for component config dataclasses.

Catches the class of bug where a config dataclass *declares* a field but its
hand-written ``__init__`` never assigns it (assigning a differently-named
attribute instead). Example: ``AirConditionerConfig`` declared
``investment_costs_in_euro``/``lifetime_in_years`` but set ``self.cost``/
``self.lifetime``. The component still instantiated fine, so mypy and ordinary
unit tests stayed green — but anything reading the declared field (the editor's
config serialiser, ``get_cost_capex``) blew up with ``AttributeError``.

The reliable, automatable signal is: **every field declared on a config
dataclass must actually be present on its default instance.** This test asserts
exactly that, parametrised over every introspectable component config so a
regression is reported against the offending class by name.

It reuses the discovery/introspection helpers in ``tools/generate_component_db.py``
so it stays in lock-step with how the editor database is built.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os

import pytest

# ---------------------------------------------------------------------------
# Load the generator module by file path (tools/ is not an importable package,
# and its main() is behind an ``if __name__ == "__main__"`` guard, so importing
# only exposes the helper functions without writing any output files).
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GEN_PATH = os.path.join(_REPO_ROOT, "tools", "generate_component_db.py")
_spec = importlib.util.spec_from_file_location("generate_component_db", _GEN_PATH)
assert _spec is not None and _spec.loader is not None
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)


def _unique_config_classes():
    """Map each distinct config dataclass to one owning component (for context)."""
    mapping: dict = {}
    for comp_class in _gen._collect_component_classes():
        try:
            cfg_class = _gen._find_config_class(comp_class)
        except Exception:
            cfg_class = None
        if cfg_class is None or not dataclasses.is_dataclass(cfg_class):
            continue
        mapping.setdefault(cfg_class, comp_class)
    return mapping


_CONFIG_CLASSES = _unique_config_classes()


@pytest.mark.base
@pytest.mark.parametrize(
    "config_class",
    list(_CONFIG_CLASSES.keys()),
    ids=[c.__name__ for c in _CONFIG_CLASSES],
)
def test_default_config_populates_all_declared_fields(config_class):
    """Every field declared on a config dataclass must be set on its default instance."""
    default_config = _gen._find_default_config(config_class)
    if default_config is None:
        # A different failure mode (no obtainable default config) already surfaced
        # by generate_component_db.py's own failure list — not the invariant here.
        pytest.skip(
            f"No default config obtainable from {config_class.__name__} "
            "(tracked separately by generate_component_db.py)."
        )

    missing = [
        f.name
        for f in dataclasses.fields(config_class)
        if not hasattr(default_config, f.name)
    ]

    assert not missing, (
        f"{config_class.__name__} declares field(s) that its default instance never sets: "
        f"{missing}. Assign them in __init__ (or the get_default_* method), or drop the "
        f"unused declaration. Owning component: "
        f"{_CONFIG_CLASSES[config_class].__module__}.{_CONFIG_CLASSES[config_class].__name__}"
    )
