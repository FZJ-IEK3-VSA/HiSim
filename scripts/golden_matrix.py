#!/usr/bin/env python3
"""Emit a GitHub Actions matrix from ``golden_config.json``.

Without ``--shards`` it prints ``{"include": [{"setup": <id>, "param": <id>}, ...]}`` — one
entry per (setup, parameter_set) pair the setup's own ``horizons`` restriction permits.
``--horizon week|year|day`` restricts to the parameter sets built by the matching
``SimulationParameters`` factory, so each CI tier fans out only its own pairs.

The golden workflows use ``--shards N``: each matrix cell is then a *shard*, a list of pairs one
job runs several at a time (``golden_check.py --pairs ... --jobs <jobs>``), because a job per pair
paid about a minute of container, checkout and install for 6 to 250 s of simulation. Each horizon
is cut into at most N shards of its own, so a shard never mixes a week pair with a year pair and
its ``jobs`` — how many pairs run at once — can follow the horizon's memory (see
:data:`PAIRS_AT_ONCE`). A cell reads::

    {"name": "week-1", "horizon": "week", "jobs": 4, "count": 6, "seconds": 254,
     "pairs": "setup_a:one_week_60s setup_b:one_week_60s ..."}

``pairs`` is space-separated ``setup:param`` tokens, heaviest first, so the pairs the runner
starts first are the ones that would otherwise finish last; ``seconds`` is the shard's expected
wall time, its runs spread over ``jobs`` lanes.

``--with-yaml`` is for golden-check, whose shards run each pair's Python setup and its recorded
YAML twin side by side in one pool (``golden_check.py --mode both``): every pair then counts as
two runs of its weight, on two different lanes. The twin weighs what its Python pair weighs; the
two measured within about 10 % of each other when they were still separate jobs.

**The weight is the pair's measured duration, kept in the config.** A setup entry may carry
``"seconds": {"<parameter set id>": <s>}``: the wall time of the pair's ``golden_check.py`` step
on a GitHub runner, as last measured (``report.json`` records ``duration_s`` per pair, which is
where a refreshed number comes from). A pair without one weighs :data:`DEFAULT_PAIR_SECONDS`.
The numbers only steer the balance — a stale one makes a shard finish a little later, never makes
the gate cover less — so they are refreshed when a shard's wall time drifts, not on every change.
The balance is over lanes, not over sums alone: a shard runs ``jobs`` runs at once, so its wall
time is its busiest lane. The pairs go longest first, each onto a shard's lightest lane (with
``--with-yaml``, its two lightest lanes). Among the
shards where it does not lengthen the longest lane of the whole horizon (which the heaviest pair
sets and no split can undercut), it goes to the one with the least work in total, so the shards
carry about equal CPU; when it would lengthen it everywhere, it goes where the result is shortest.

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
from typing import Any, Optional

try:  # importable both as ``scripts.golden_matrix`` (tests) and as a script from ``scripts/``
    from golden_horizons import HorizonVocabulary  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - depends on how scripts/ is on the path
    from scripts.golden_horizons import HorizonVocabulary

DEFAULT_CONFIG_PATH = Path(__file__).parent / "golden_config.json"

# The vocabulary under its established local names; the definitions live in golden_horizons.
HORIZON_FACTORIES = HorizonVocabulary.HORIZON_FACTORIES
FACTORY_HORIZONS = HorizonVocabulary.FACTORY_HORIZONS

#: How many pairs of a horizon one job runs at once, set by memory on a 16 GB runner: a week pair
#: peaks at about 1.2 GiB, so four fit beside the runner's four cores; a full-year pair holds its
#: year of results and peaks at about 6 GiB, so two.
PAIRS_AT_ONCE: dict[str, int] = {"day": 4, "week": 4, "year": 2}

#: The weight of a pair whose setup records no ``seconds`` for it: a typical building-sizer week.
DEFAULT_PAIR_SECONDS = 120


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


def pair_seconds(setup: dict, param_id: str) -> float:
    """Return the weight of one pair: the setup's recorded seconds for it, or the default.

    Raises:
        ValueError: if the setup's ``seconds`` is not a mapping of parameter-set ids to positive
            numbers. A typo'd weight is refused rather than silently read as the default.
    """
    recorded: Any = setup.get("seconds", {})
    if not isinstance(recorded, dict) or not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        for value in recorded.values()
    ):
        raise ValueError(
            f"setup {setup.get('id')!r}: 'seconds' must map parameter-set ids to positive numbers, "
            f"not {recorded!r}."
        )
    return float(recorded.get(param_id, DEFAULT_PAIR_SECONDS))


def _choose_shard(lanes: list[list[float]], seconds: float, runs: int) -> int:
    """Return the index of the shard the next (no longer than any before) pair goes to.

    ``lanes`` holds each shard's lane loads so far; the pair adds ``seconds`` to its ``runs``
    lightest lanes. The rule that picks the shard is the module docstring's.
    """
    longest = max(load for shard in lanes for load in shard)
    ends = [max(*shard, sorted(shard)[runs - 1] + seconds) for shard in lanes]
    totals = [sum(shard) for shard in lanes]
    fitting = [index for index, end in enumerate(ends) if end <= longest]
    if fitting:
        return min(fitting, key=lambda index: (totals[index], index))
    return min(range(len(lanes)), key=lambda index: (ends[index], totals[index], index))


def build_shards(config: dict, shards: int, horizon: Optional[str] = None, with_yaml: bool = False) -> dict:
    """Return a GitHub matrix whose cells are shards: balanced lists of pairs, per horizon.

    Every pair :func:`build_matrix` would emit lands in exactly one shard. Each horizon gets at
    most ``shards`` shards (fewer when it has fewer pairs; never an empty one), balanced by
    :func:`pair_seconds` over the shards' :data:`PAIRS_AT_ONCE` lanes with the longest-first
    greedy rule (see the module docstring). ``with_yaml`` counts each pair twice, its Python
    run and its YAML twin side by side. Ties keep config order, so the output is deterministic.

    Raises:
        ValueError: if ``shards`` is less than one, a ``seconds`` weight is malformed or names a
            parameter set the config does not have, or :func:`build_matrix` refuses the config.
    """
    if shards < 1:
        raise ValueError(f"--shards must be at least 1, not {shards}.")
    pairs = build_matrix(config, horizon=horizon)["include"]
    setups = {setup["id"]: setup for setup in config["setups"]}
    params = {param["id"]: param for param in config["parameter_sets"]}
    for setup in config["setups"]:
        pair_seconds(setup, "")  # refuses a malformed 'seconds' before its keys are read
        unknown = sorted(set(setup.get("seconds", {})) - set(params))
        if unknown:
            raise ValueError(f"setup {setup['id']!r}: 'seconds' names unknown parameter sets {unknown}.")

    include = []
    for name in HORIZON_FACTORIES:  # day, week, year: a stable cell order
        members = [p for p in pairs if FACTORY_HORIZONS[params[p["param"]]["factory"]] == name]
        if not members:
            continue
        weighted = sorted(
            ((pair_seconds(setups[p["setup"]], p["param"]), position, p) for position, p in enumerate(members)),
            key=lambda item: (-item[0], item[1]),
        )
        jobs = PAIRS_AT_ONCE[name]
        runs = min(2 if with_yaml else 1, jobs)
        count = min(shards, len(members))
        lanes = [[0.0] * jobs for _ in range(count)]
        chosen: list[list[dict]] = [[] for _ in range(count)]
        for seconds, _, pair in weighted:
            target = _choose_shard(lanes, seconds, runs)
            lane = lanes[target]
            for _ in range(runs):
                lane[lane.index(min(lane))] += seconds
            chosen[target].append(pair)
        for index in range(count):
            include.append(
                {
                    "name": f"{name}-{index + 1}",
                    "horizon": name,
                    "jobs": jobs,
                    "count": len(chosen[index]),
                    "seconds": round(max(lanes[index])),
                    "pairs": " ".join(f"{pair['setup']}:{pair['param']}" for pair in chosen[index]),
                }
            )
    return {"include": include}


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parses the command line of one matrix emission."""
    parser = argparse.ArgumentParser(description="Emit a GitHub Actions matrix from golden_config.json.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--horizon", choices=sorted(HORIZON_FACTORIES), default=None)
    parser.add_argument(
        "--shards",
        type=int,
        default=None,
        help="Emit at most this many shards per horizon, each a list of pairs, instead of one cell per pair.",
    )
    parser.add_argument(
        "--with-yaml",
        action="store_true",
        help="Balance the shards for running each pair's YAML twin beside it (golden_check.py --mode both).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Prints the matrix as one compact JSON line, ready for ``$GITHUB_OUTPUT``."""
    args = _parse_args(argv)
    config = json.loads(args.config.read_text())
    if args.shards is None:
        matrix = build_matrix(config, horizon=args.horizon)
    else:
        matrix = build_shards(config, args.shards, horizon=args.horizon, with_yaml=args.with_yaml)
    # Compact single line: consumed by ``echo "matrix=$(...)" >> $GITHUB_OUTPUT``.
    print(json.dumps(matrix, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
