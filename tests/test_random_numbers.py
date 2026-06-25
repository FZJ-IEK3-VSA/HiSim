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

import random

import pytest

from hisim.component import DisplayConfig
from hisim.components.random_numbers import RandomNumbers, RandomNumbersConfig
from hisim.simulationparameters import SimulationParameters


def _make_config(timesteps: int = 10, minimum: float = 1.0, maximum: float = 20.0) -> RandomNumbersConfig:
    """Build a small ``RandomNumbersConfig`` for testing."""
    return RandomNumbersConfig(
        building_name="BUI1",
        name="RandomNumbers",
        timesteps=timesteps,
        minimum=minimum,
        maximum=maximum,
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
    assert RandomNumbers._generate_values(0.0, 1.0, 0, random.Random(0)) == []
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
    sp = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = _make_config(timesteps=10, minimum=1.0, maximum=20.0)
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
    sp = SimulationParameters.full_year(year=2021, seconds_per_timestep=60)
    config = _make_config(timesteps=25, minimum=10.0, maximum=30.0)

    component = RandomNumbers(
        config=config,
        my_simulation_parameters=sp,
        my_display_config=DisplayConfig(),
    )
    assert len(component.values) == 25
    assert all(10.0 <= v < 30.0 for v in component.values)
