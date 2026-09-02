"""A cached run and an uncached one must be the same run.

Every cache in HiSim is a CSV of float64. pandas' default CSV reader uses a fast, inexact float
parser, so reading one back does not always give the values that were written -- and it is the
*reader* that loses them, not the digits on disk: writing more precision makes it worse, because a
longer decimal gives the fast parser more to be inexact about.

The consequence was that a warm run and a cold run of the same system disagreed. Measured on
``household_gas_solar_thermal_building_sizer``: 17 of its 110 KPIs differed between the two, and
went to 0 once every cache reader asked for exact parsing.

These tests pin both halves -- that the exact parser is exact, and that every cache reader asks for
it -- because the argument is easy to leave out of the next one and nothing else would notice.
"""

# clean

import io
import pathlib
from typing import Tuple

import numpy as np
import pandas as pd
import pytest


class CacheReaders:
    """Every place HiSim reads one of its own caches back.

    Listed explicitly rather than discovered, because a new cache reader should have to be added
    here deliberately -- which is the moment to ask whether it parses exactly.

    The solar thermal collector is absent on purpose: its cache is rebuilt on a separate branch
    (roadmap/pylpg_flakiness.md F8) and its own test asserts the same guarantee behaviourally, by
    comparing a computed run against a cached one. Whichever of the two lands second should add it
    here, so that this list is once again every reader rather than most of them.
    """

    SITES: Tuple[Tuple[str, str], ...] = (
        ("hisim/components/generic_pv_system.py", "Get PV results from cache."),
        ("hisim/components/building/building.py", "if not self.is_in_cache:  #"),
        ("hisim/components/weather.py", "my_weather = pd.read_csv("),
        ("hisim/components/loadprofilegenerator_utsp_connector.py", "dataframe = pd.read_csv("),
    )

    EXACT = 'float_precision="round_trip"'


@pytest.mark.base
def test_the_default_reader_is_inexact_and_the_exact_one_is_not() -> None:
    """The premise: this is a property of the reader, not of the written precision.

    If pandas ever makes its default parser exact, this test fails and the argument can go. That is
    the right way round: the workaround should be removed on evidence, not left in perpetuity.
    """
    values = pd.DataFrame({"a": np.random.default_rng(0).normal(150, 30, 2000)})

    losses = {}
    for write_format in (None, "%.17g", "%.20g"):
        for precision in ("high", "round_trip"):
            buffer = io.StringIO()
            values.to_csv(buffer, index=False, float_format=write_format)
            read_back = pd.read_csv(io.StringIO(buffer.getvalue()), float_precision=precision)
            losses[(write_format, precision)] = int((read_back["a"].to_numpy() != values["a"].to_numpy()).sum())

    for write_format in (None, "%.17g", "%.20g"):
        assert losses[(write_format, "round_trip")] == 0, (
            f"exact parsing lost {losses[(write_format, 'round_trip')]} values written as "
            f"{write_format} -- the caches rely on it losing none"
        )
    assert losses[(None, "high")] > 0, (
        "the default reader no longer loses precision; the float_precision arguments in the cache "
        "readers can be removed, and this test with them"
    )
    assert losses[("%.20g", "high")] >= losses[(None, "high")], (
        "writing more digits should not help the inexact parser; if it now does, the note in each "
        "cache reader explaining that it does not is wrong"
    )


@pytest.mark.base
def test_every_cache_reader_parses_exactly() -> None:
    """Each cache read asks for exact parsing, so no cache silently changes what it stored."""
    repository_root = pathlib.Path(__file__).resolve().parent.parent

    for relative_path, anchor in CacheReaders.SITES:
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        assert anchor in source, f"{relative_path} no longer contains '{anchor}'; update CacheReaders.SITES"
        start = source.index(anchor)
        window = source[start : start + 600]
        assert CacheReaders.EXACT in window, (
            f"the cache read at {relative_path} ('{anchor}') does not pass {CacheReaders.EXACT}, so a "
            f"warm run of that component will not match a cold one"
        )
