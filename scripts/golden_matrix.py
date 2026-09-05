#!/usr/bin/env python3
"""Emit a GitHub Actions matrix from ``golden_config.json``.

Prints ``{"include": [{"setup": <id>, "param": <id>}, ...]}`` — one entry per
``(setup, parameter_set)`` pair — for consumption via ``fromJSON`` in the golden
workflows. ``--horizon week|year|day`` restricts to the parameter sets built by
the matching ``SimulationParameters`` factory, so each CI tier fans out only its
own pairs.

Deliberately depends on the standard library only (no ``hisim`` / ``runner``
import): it runs in the lightweight ``discover`` job before dependencies are
installed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"

# Map a human-facing horizon name to the SimulationParameters factory that
# produces it. Selecting by factory keeps this robust to parameter-set id naming.
HORIZON_FACTORIES = {
    "day": "one_day_only",
    "week": "one_week_only",
    "year": "full_year",
}

# The reverse view: which horizon a parameter set's factory belongs to. Used to honour a
# setup's own "horizons" restriction (a setup listed only for the week gate must not
# enter the full-year matrix, whose cost is what the restriction exists to spare).
FACTORY_HORIZONS = {factory: horizon for horizon, factory in HORIZON_FACTORIES.items()}


def setup_runs_factory(setup: dict, factory: str) -> bool:
    """Whether a config setup entry participates in parameter sets built by ``factory``.

    A setup without a ``horizons`` key runs everything, which keeps the original eight
    golden setups exactly as they were. A setup with one runs only the horizons it
    names; the golden week gate grew to the whole fleet this way while the expensive
    full-year matrix stayed with the eight.
    """
    horizons = setup.get("horizons")
    return horizons is None or FACTORY_HORIZONS.get(factory) in horizons


def build_matrix(config: dict, horizon: Optional[str] = None) -> dict:
    """Return a GitHub matrix dict for the config's pairs, optionally filtered.

    Raises:
        ValueError: if ``horizon`` is not one of :data:`HORIZON_FACTORIES`.
    """
    param_sets = config["parameter_sets"]
    if horizon is not None:
        if horizon not in HORIZON_FACTORIES:
            raise ValueError(f"Unknown horizon {horizon!r}; choose from {sorted(HORIZON_FACTORIES)}.")
        factory = HORIZON_FACTORIES[horizon]
        param_sets = [p for p in param_sets if p["factory"] == factory]

    include = [
        {"setup": setup["id"], "param": param["id"]}
        for setup in config["setups"]
        for param in param_sets
        if setup_runs_factory(setup, param["factory"])
    ]
    return {"include": include}


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit a GitHub Actions matrix from golden_config.json.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--horizon", choices=sorted(HORIZON_FACTORIES), default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    config = json.loads(args.config.read_text())
    matrix = build_matrix(config, horizon=args.horizon)
    # Compact single line: consumed by ``echo "matrix=$(...)" >> $GITHUB_OUTPUT``.
    print(json.dumps(matrix, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
