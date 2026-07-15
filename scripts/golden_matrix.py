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
