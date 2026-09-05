"""Tests for ``RandomNumbers`` value generation.

The value-generation logic used to live inline in ``RandomNumbers.__init__``
and draw from the module-global ``random.random()``, which made it impossible
to pin the produced values without either seeding the global RNG (leaking
state across tests) or monkeypatching ``random.random`` (fragile). It now
lives in the pure :meth:`RandomNumbers._generate_values` helper that takes an
injected :class:`random.Random`, and ``RandomNumbers.__init__`` accepts an
optional ``rng`` argument.

These tests assert the seam directly: exact reproducible values, bounds, the
requested count, the ``ValueError`` guard, and that the component wires the
injected RNG through to ``self.values``.
"""

# clean

# These tests deliberately exercise the private helper ``RandomNumbers._generate_values``.
# pylint: disable=protected-access

import datetime
import random

import pytest

from hisim.config import ComponentID, DisplayConfig
from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.simulationparameters import SimulationParameters


def _make_config(minimum: float = 1.0, maximum: float = 20.0, seed: int = 1) -> RandomNumbersConfig:
    """Build a small ``RandomNumbersConfig`` for testing.

    How many values the component draws is no longer part of its configuration — it is the length of
    the simulation — so this helper only carries the range and the seed, and the tests pair it with
    :func:`_make_parameters`.
    """
    return RandomNumbersConfig(
        component_id=ComponentID(name="RandomNumbers"),
        minimum=minimum,
        maximum=maximum,
        seed=seed,
    )


def _make_parameters(timesteps: int) -> SimulationParameters:
    """Build simulation parameters that run for exactly ``timesteps`` steps.

    The component draws one value per timestep of the run, so a test that wants a short series says
    so here rather than in the configuration. One minute per step keeps the arithmetic obvious.
    """
    seconds_per_timestep = 60
    start = datetime.datetime(2021, 1, 1)
    return SimulationParameters(
        start,
        start + datetime.timedelta(seconds=timesteps * seconds_per_timestep),
        seconds_per_timestep,
    )


@pytest.mark.base
def test_generate_values_is_reproducible_with_seeded_rng() -> None:
    """A seeded ``random.Random`` yields an exact, pinable sequence."""
    expected = [
        17.044015178975915,
        15.401133655865747,
        8.990860035786055,
        5.9194182555663035,
        10.714219706003561,
        8.693748611557872,
        15.89217319166068,
        6.762941795499621,
        10.055342128894761,
        12.084258749645592,
    ]
    values = RandomNumbers._generate_values(1.0, 20.0, 10, random.Random(0))
    assert values == expected


@pytest.mark.base
def test_generate_values_respects_bounds() -> None:
    """Every value lies in ``[minimum, maximum)`` for a non-trivial sample."""
    minimum, maximum, timesteps = -5.0, 5.0, 1000
    values = RandomNumbers._generate_values(minimum, maximum, timesteps, random.Random(42))
    assert len(values) == timesteps
    assert all(minimum <= v < maximum for v in values), (
        f"values out of [{minimum}, {maximum}): {min(values)}, {max(values)}"
    )


@pytest.mark.base
def test_generate_values_count_matches_timesteps() -> None:
    """The helper returns exactly ``timesteps`` values (incl. zero)."""
    assert not RandomNumbers._generate_values(0.0, 1.0, 0, random.Random(0))
    assert len(RandomNumbers._generate_values(0.0, 1.0, 7, random.Random(0))) == 7


@pytest.mark.base
def test_generate_values_negative_timesteps_raises() -> None:
    """A negative ``timesteps`` is rejected with a clear ``ValueError``."""
    with pytest.raises(ValueError):
        RandomNumbers._generate_values(0.0, 1.0, -1, random.Random(0))


@pytest.mark.base
def test_generate_values_does_not_touch_global_random() -> None:
    """The helper only consumes the injected ``rng``, never the global stream.

    We seed the global RNG, run the helper with a separate ``rng``, then draw
    one number from the global RNG and confirm it is the first value of the
    seeded global sequence -- i.e. the helper left the global state untouched.
    """
    random.seed(123)
    expected_first_global = random.random()  # consume the first global draw
    random.seed(123)  # reset to the same state

    # The helper uses its own rng, not the global one.
    _ = RandomNumbers._generate_values(0.0, 1.0, 50, random.Random(7))

    first_global_after = random.random()
    assert first_global_after == expected_first_global


@pytest.mark.base
def test_component_uses_injected_rng() -> None:
    """Passing ``rng`` to the constructor makes ``self.values`` reproducible."""
    sp = _make_parameters(10)
    config = _make_config(minimum=1.0, maximum=20.0)
    expected = RandomNumbers._generate_values(1.0, 20.0, 10, random.Random(0))

    component = RandomNumbers(
        config=config,
        my_simulation_parameters=sp,
        my_display_config=DisplayConfig(),
        rng=random.Random(0),
    )
    assert component.values == expected
    assert component.minimum == 1.0
    assert component.maximum == 20.0


@pytest.mark.base
def test_component_default_rng_stays_in_bounds() -> None:
    """Without ``rng`` the component still produces in-range, correctly-sized values."""
    sp = _make_parameters(25)
    config = _make_config(minimum=10.0, maximum=30.0)

    component = RandomNumbers(
        config=config,
        my_simulation_parameters=sp,
        my_display_config=DisplayConfig(),
    )
    assert len(component.values) == 25
    assert all(10.0 <= v < 30.0 for v in component.values)


@pytest.mark.base
def test_default_display_config_not_shared_between_instances() -> None:
    """Omitting ``my_display_config`` must not share one object across instances.

    A mutable default argument (``DisplayConfig()`` evaluated at definition
    time) would make every component that omits the argument reference the
    same ``DisplayConfig`` instance, so mutating one would affect all others.
    Using ``None`` as a sentinel and creating a fresh ``DisplayConfig`` inside
    ``__init__`` prevents that shared state.
    """
    sp = _make_parameters(5)
    config = _make_config(minimum=1.0, maximum=20.0)

    # The seeded rng is only required to satisfy the constructor signature;
    # the assertions concern display-config identity, not RNG output.
    first = RandomNumbers(
        config=config,
        my_simulation_parameters=sp,
        rng=random.Random(0),
    )
    second = RandomNumbers(
        config=config,
        my_simulation_parameters=sp,
        rng=random.Random(0),
    )

    assert first.my_display_config is not None
    assert first.my_display_config is not second.my_display_config
