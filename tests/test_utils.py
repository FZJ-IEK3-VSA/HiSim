"""Tests for public utility functions in :mod:`hisim.utils`.

These tests target regressions flagged during docstring review of
``hisim/utils.py``: the ``convert_lpg_timestep_to_utc`` return value and the
``get_cache_file`` cache-path generation.
"""

# clean

import os
import pathlib
from dataclasses import dataclass

import pytest

from hisim import utils
from hisim.simulationparameters import SimulationParameters


@pytest.mark.base
def test_convert_lpg_timestep_to_utc_returns_shifted_indices() -> None:
    """``convert_lpg_timestep_to_utc`` must return the UTC-shifted indices.

    The function builds ``data_utc`` with the shifted values but previously
    returned the original unmodified ``data`` list, so callers silently
    received local-time indices. This regression test pins the corrected
    ``return data_utc`` behaviour: every index is shifted towards UTC (by one
    hour for winter timesteps at a 3600 s resolution) and the returned list is
    not the input list.
    """
    data = [10, 100, 1000, 5000]
    result = utils.convert_lpg_timestep_to_utc(
        data=data, year=2021, seconds_per_timestep=3600
    )
    assert result is not data
    assert result == [9, 99, 999, 4999]


@pytest.mark.base
def test_get_cache_file_returns_cache_path_for_valid_parameters(
    tmp_path: pathlib.Path,
) -> None:
    """``get_cache_file`` returns a cache path for valid ``SimulationParameters``.

    With ``cache_dir_path`` left at its default (``None``) the function falls
    back to ``my_simulation_parameters.cache_dir_path`` and returns
    ``(exists, absolute_path)`` for the hashed cache filename.
    """

    @dataclass
    class _ParameterClass:
        name: str = "demo"

        def to_json(self) -> str:
            """Return a JSON serialization of the parameter for cache hashing."""
            return '{"name": "demo"}'

    my_simulation_parameters = SimulationParameters.one_day_only(
        year=2021, seconds_per_timestep=3600
    )
    my_simulation_parameters.cache_dir_path = str(tmp_path)
    exists, cache_filepath = utils.get_cache_file(
        component_key="demo",
        parameter_class=_ParameterClass(),
        my_simulation_parameters=my_simulation_parameters,
        cache_dir_path=None,
    )
    assert exists is False
    assert cache_filepath.startswith(str(tmp_path))
    assert cache_filepath.endswith(".cache")


@pytest.mark.base
def test_load_smart_appliance_memoizes_file_read() -> None:
    """``load_smart_appliance`` must parse the smart-devices JSON once per process.

    Repeated calls share a single parsed dict (via ``lru_cache`` on
    ``_load_smart_appliances_file``) so the file is opened and ``json.load``-ed
    at most once; only the dict lookup (plus a cheap deep copy of the entry)
    runs per call. The returned data and the ``KeyError`` on a missing name must
    be unchanged from the uncached behaviour.
    """
    import json

    from hisim.utils import HISIMPATH, _load_smart_appliances_file

    path = HISIMPATH["smart_appliances"]
    if not os.path.isfile(path):
        pytest.skip(f"smart-appliances database not found at {path}")
    with open(path, encoding="utf-8") as fh:
        fresh = json.load(fh)
    if "Battery" not in fresh:
        pytest.skip("smart-appliances database has no 'Battery' entry")

    _load_smart_appliances_file.cache_clear()
    try:
        first = utils.load_smart_appliance("Battery")
        second = utils.load_smart_appliance("Battery")

        # Each call returns an independent deep copy, so identity differs but
        # the contents are equal.
        assert first is not second
        assert first == second == fresh["Battery"]

        # The file is read exactly once; the second call is a cache hit.
        # astroid mis-models functools.lru_cache's cache_info() built-in as a
        # BoundMethod with an implicit self but zero formal parameters, so
        # pylint reports a spurious extra positional argument; the runtime
        # call is correct (cache_info() takes no arguments).
        info = _load_smart_appliances_file.cache_info()  # pylint: disable=too-many-function-args
        assert info.misses == 1
        assert info.hits >= 1

        # KeyError on a missing name is preserved (identical dict lookup).
        with pytest.raises(KeyError):
            utils.load_smart_appliance("Definitely Not An Appliance")
    finally:
        _load_smart_appliances_file.cache_clear()


@pytest.mark.base
def test_load_smart_appliance_returns_independent_copies(monkeypatch, tmp_path) -> None:
    """Mutating a returned appliance entry must not corrupt the shared cache.

    ``_load_smart_appliances_file`` is memoized, so without a defensive copy
    every caller would receive a reference into the same cached dict. A caller
    that mutates its entry would then silently corrupt the data seen by every
    subsequent component initialization in a parametric study.
    ``load_smart_appliance`` therefore returns a deep copy of the looked-up
    entry; this test pins that contract using a self-contained fixture so it
    does not depend on the real smart-devices database.
    """
    import json

    from hisim.utils import _load_smart_appliances_file

    database = {
        "TestAppliance": [
            {"Manufacturer": "ACME", "Model": "X", "Capacity": 5.0},
            {"Manufacturer": "ACME", "Model": "Y", "Capacity": 10.0},
        ]
    }
    path = tmp_path / "smart_devices.json"
    path.write_text(json.dumps(database), encoding="utf-8")

    monkeypatch.setitem(utils.HISIMPATH, "smart_appliances", str(path))
    _load_smart_appliances_file.cache_clear()
    try:
        first = utils.load_smart_appliance("TestAppliance")

        # Mutate the returned entry in place and append to it.
        first[0]["Capacity"] = 999.0
        first.append({"Manufacturer": "EVIL", "Model": "Z", "Capacity": 0.0})

        # A fresh call must see the uncorrupted cached data.
        second = utils.load_smart_appliance("TestAppliance")
        assert second == database["TestAppliance"]
        assert second[0]["Capacity"] == 5.0
        assert len(second) == 2
        assert first is not second
    finally:
        _load_smart_appliances_file.cache_clear()


@pytest.mark.base
def test_load_smart_appliances_file_rejects_non_dict_json(monkeypatch, tmp_path) -> None:
    """A smart-devices file whose top-level JSON is not an object fails loudly.

    Without a runtime check, a malformed file (e.g. a JSON list) would be
    cached and only surface as a confusing ``TypeError`` on the first
    ``__getitem__`` in ``load_smart_appliance``. The loader validates the
    structure immediately so the error is reported at the source.
    """
    from hisim.utils import _load_smart_appliances_file

    path = tmp_path / "bad_smart_devices.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    monkeypatch.setitem(utils.HISIMPATH, "smart_appliances", str(path))
    _load_smart_appliances_file.cache_clear()
    try:
        with pytest.raises(ValueError, match="JSON object keyed by appliance name"):
            utils.load_smart_appliance("Anything")
    finally:
        _load_smart_appliances_file.cache_clear()
