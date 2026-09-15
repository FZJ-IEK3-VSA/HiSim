#!/usr/bin/env python3
"""Emit a GitHub Actions matrix from ``golden_config.json``.

Prints ``{"include": [{"setup": <id>, "param": <id>}, ...]}`` — one entry per
(setup, parameter_set) pair the setup's own ``horizons`` restriction permits —
for consumption via ``fromJSON`` in the golden workflows. ``--horizon
week|year|day`` restricts to the parameter sets built by the matching
``SimulationParameters`` factory, so each CI tier fans out only its own pairs.

Deliberately depends on the standard library only (no ``hisim`` / ``runner``
import): it runs in the lightweight ``discover`` job before dependencies are
installed. The horizon vocabulary and the participation rule live in
``golden_horizons.py``, the one home the runner reads them from too, so the
matrix CI fans out and the pairs the runner executes can never silently
disagree.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

try:  # importable both as ``scripts.golden_matrix`` (tests) and as a script from ``scripts/``
    from golden_horizons import HorizonVocabulary  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - depends on how scripts/ is on the path
    from scripts.golden_horizons import HorizonVocabulary

DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"

# The vocabulary under its established local names; the definitions live in golden_horizons.
HORIZON_FACTORIES = HorizonVocabulary.HORIZON_FACTORIES
FACTORY_HORIZONS = HorizonVocabulary.FACTORY_HORIZONS


def setup_runs_factory(setup: dict, factory: str) -> bool:
    """Whether a config setup entry participates in parameter sets built by ``factory``.

    A setup without a ``horizons`` key runs everything, which keeps the original eight
    golden setups exactly as they were. A setup with one runs only the horizons it
    names; the golden week gate grew to cover the recordable fleet this way while the
    expensive full-year matrix stayed with the eight. The rule itself lives in
    :class:`HorizonVocabulary`, shared with the runner.
    """
    return bool(HorizonVocabulary.runs_factory(setup.get("horizons"), factory))


def build_matrix(config: dict, horizon: Optional[str] = None) -> dict:
    """Return a GitHub matrix dict for the config's permitted pairs, optionally filtered.

    The config is validated before anything is emitted: a malformed ``horizons`` value or an
    unmapped factory fails the discover job loudly instead of silently shrinking the matrix —
    a gate that quietly covers less is the one failure a matrix emitter must never allow.

    Raises:
        ValueError: if ``horizon`` is not one of :data:`HORIZON_FACTORIES`, a setup's
            ``horizons`` is malformed, or a parameter set's factory maps to no horizon.
    """
    for setup in config["setups"]:
        HorizonVocabulary.check_horizons(setup.get("horizons"), f"setup {setup.get('id')!r}")
    for param in config["parameter_sets"]:
        HorizonVocabulary.check_factory(param["factory"], f"parameter set {param.get('id')!r}")

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
    """Parses the command line of one matrix emission."""
    parser = argparse.ArgumentParser(description="Emit a GitHub Actions matrix from golden_config.json.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--horizon", choices=sorted(HORIZON_FACTORIES), default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Prints the matrix as one compact JSON line, ready for ``$GITHUB_OUTPUT``."""
    args = _parse_args(argv)
    config = json.loads(args.config.read_text())
    matrix = build_matrix(config, horizon=args.horizon)
    # Compact single line: consumed by ``echo "matrix=$(...)" >> $GITHUB_OUTPUT``.
    print(json.dumps(matrix, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
