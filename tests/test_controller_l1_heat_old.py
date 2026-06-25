"""Pure unit tests for the extracted hysteresis core of the L1 heat controller.

``ControllerHeat.simulate_storage`` previously mixed the real 2-point
hysteresis control logic with two side effects: mutating
``self.state.control_signal_*`` and writing results through
``stsv.set_output_value(...)``. The pure logic has been extracted into the
``@staticmethod`` :meth:`ControllerHeat.compute_storage_control`, which only
depends on its (float/int) arguments. These tests exercise every branch of
that function with plain values and without constructing a full
``ControllerHeat`` instance or a ``SingleTimeStepValues``.
"""

import pytest

from hisim.components.controller_l1_heat_old import ControllerHeat


# Convenience alias for the (long) tuple layout returned by the function:
# (control_signal_chp, control_signal_gas_heater, control_signal_heat_pump,
#  temperature_storage_target_c, timestep_of_hysteresis)
compute = ControllerHeat.compute_storage_control


# --------------------------------------------------------------------------- #
# Inactive storage: temperature_storage <= 0 leaves everything untouched.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_compute_storage_control_inactive_storage_keeps_signals_off() -> None:
    """A storage reporting a non-positive temperature never triggers heating."""
    result = compute(
        delta_temperature=10.0,
        temperature_storage=0.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=50.0,
        timestep_of_hysteresis=0,
        timestep=5,
        prev_control_signal_chp=1,
        prev_control_signal_gas_heater=1,
    )
    # Even with a large positive delta_temperature, a non-positive storage
    # temperature means no branch fires -> all signals stay 0 and the carried
    # target / hysteresis timestep are returned unchanged.
    assert result == (0, 0, 0, 50.0, 0)


@pytest.mark.base
def test_compute_storage_control_negative_storage_temperature_is_inactive() -> None:
    """A negative storage temperature is treated the same as zero."""
    result = compute(
        delta_temperature=3.0,
        temperature_storage=-1.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=2,
        timestep=7,
        prev_control_signal_chp=0,
        prev_control_signal_gas_heater=0,
    )
    assert result == (0, 0, 0, 45.0, 2)


# --------------------------------------------------------------------------- #
# delta_temperature > max_temperature_limit (5): turn everything on.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_compute_storage_control_large_positive_delta_turns_all_heaters_on() -> None:
    """A delta above the 5 K limit activates all three control signals at once."""
    result = compute(
        delta_temperature=6.0,
        temperature_storage=40.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=3,
        timestep=9,
        prev_control_signal_chp=0,
        prev_control_signal_gas_heater=0,
    )
    assert result == (1, 1, 1, 50.0, 3)
    # The carried target is reset to the upper target while the hysteresis
    # timestep bookkeeping is left untouched.
    assert result[3] == 50.0
    assert result[4] == 3


@pytest.mark.base
def test_compute_storage_control_large_delta_resets_target_even_if_already_hysteresis() -> None:
    """When heating is requested the carried target snaps back to the upper target."""
    result = compute(
        delta_temperature=42.0,
        temperature_storage=10.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=99,
        timestep=100,
        prev_control_signal_chp=0,
        prev_control_signal_gas_heater=0,
    )
    assert result == (1, 1, 1, 50.0, 99)


# --------------------------------------------------------------------------- #
# 0 < delta_temperature <= max_temperature_limit (5): the "small deficit" branch.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_compute_storage_control_small_positive_delta_turns_all_heaters_on() -> None:
    """A small positive delta (within the 5 K limit) also activates all heaters."""
    result = compute(
        delta_temperature=5.0,
        temperature_storage=45.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=1,
        timestep=2,
        prev_control_signal_chp=0,
        prev_control_signal_gas_heater=0,
    )
    assert result == (1, 1, 1, 50.0, 1)


@pytest.mark.base
def test_compute_storage_control_small_delta_boundary_zero_excluded() -> None:
    """Delta == 0 must NOT enter the small-positive branch; it falls through to delta <= 0."""
    result = compute(
        delta_temperature=0.0,
        temperature_storage=50.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=50.0,
        timestep_of_hysteresis=0,
        timestep=5,
        prev_control_signal_chp=0,
        prev_control_signal_gas_heater=0,
    )
    # delta == 0 hits the delta <= 0 branch: target_c == target and
    # timestep_of_hysteresis (0) != timestep (5) -> flip to hysteresis target.
    assert result == (0, 0, 0, 45.0, 5)


@pytest.mark.base
def test_compute_storage_control_small_delta_prev_signals_do_not_override_on() -> None:
    """The previous-signal comparisons in the small-delta branch are no-ops: signals stay on.

    The branch sets all three signals to 1 first, then re-assigns 1 inside the
    ``if prev_control_signal_chp < ...`` / ``elif prev_control_signal_gas_heater
    < ...`` guards, so the output is always (1, 1, 1) regardless of the previous
    signals. This locks the current (preserved) behaviour.
    """
    for prev_chp, prev_gas in [(0, 0), (1, 1), (1, 0), (0, 1), (0.5, 0.5)]:
        result = compute(
            delta_temperature=2.0,
            temperature_storage=48.0,
            temperature_storage_target=50.0,
            temperature_storage_target_hysteresis=45.0,
            temperature_storage_target_c=45.0,
            timestep_of_hysteresis=1,
            timestep=2,
            prev_control_signal_chp=prev_chp,
            prev_control_signal_gas_heater=prev_gas,
        )
        assert result == (1, 1, 1, 50.0, 1), f"prev=({prev_chp},{prev_gas}) -> {result}"


# --------------------------------------------------------------------------- #
# delta_temperature <= 0: the "warm enough" hysteresis branch.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_compute_storage_control_warm_enough_flips_to_hysteresis_target() -> None:
    """When the storage is warm enough and at the upper target, flip to the lower hysteresis target.

    First time the storage reaches the upper target (``temperature_storage_target_c
    == temperature_storage_target`` and ``timestep_of_hysteresis != timestep``)
    the carried target is lowered to the hysteresis target and the hysteresis
    timestep is recorded. No heaters are turned on.
    """
    result = compute(
        delta_temperature=-1.0,
        temperature_storage=51.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=50.0,
        timestep_of_hysteresis=0,
        timestep=7,
        prev_control_signal_chp=1,
        prev_control_signal_gas_heater=1,
    )
    assert result == (0, 0, 0, 45.0, 7)


@pytest.mark.base
def test_compute_storage_control_warm_enough_turns_heaters_off() -> None:
    """When already on the hysteresis target and warm enough, turn all heaters off.

    ``temperature_storage_target_c != temperature_storage_target`` together with
    ``timestep_of_hysteresis != timestep`` zeroes every control signal while
    keeping the carried hysteresis target.
    """
    result = compute(
        delta_temperature=-1.0,
        temperature_storage=44.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=0,
        timestep=7,
        prev_control_signal_chp=1,
        prev_control_signal_gas_heater=1,
    )
    assert result == (0, 0, 0, 45.0, 0)


@pytest.mark.base
def test_compute_storage_control_warm_enough_same_timestep_does_nothing() -> None:
    """If the hysteresis was already flipped this timestep, neither sub-branch fires.

    With ``timestep_of_hysteresis == timestep`` both guards in the
    ``delta_temperature <= 0`` branch are False, so the signals stay 0 and the
    carried target / hysteresis timestep are returned unchanged.
    """
    result = compute(
        delta_temperature=-1.0,
        temperature_storage=51.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=50.0,
        timestep_of_hysteresis=7,
        timestep=7,
        prev_control_signal_chp=1,
        prev_control_signal_gas_heater=1,
    )
    assert result == (0, 0, 0, 50.0, 7)


@pytest.mark.base
def test_compute_storage_control_warm_enough_hysteresis_target_same_timestep_keeps_target() -> None:
    """Same-timestep guard with the hysteresis target active keeps that target."""
    result = compute(
        delta_temperature=-1.0,
        temperature_storage=44.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=7,
        timestep=7,
        prev_control_signal_chp=1,
        prev_control_signal_gas_heater=1,
    )
    assert result == (0, 0, 0, 45.0, 7)


# --------------------------------------------------------------------------- #
# The function is pure: it must not depend on any instance state.
# --------------------------------------------------------------------------- #
@pytest.mark.base
def test_compute_storage_control_is_static_and_pure() -> None:
    """Calling the staticmethod twice with identical arguments yields identical results."""
    args = {
        "delta_temperature": 6.0,
        "temperature_storage": 40.0,
        "temperature_storage_target": 50.0,
        "temperature_storage_target_hysteresis": 45.0,
        "temperature_storage_target_c": 45.0,
        "timestep_of_hysteresis": 3,
        "timestep": 9,
        "prev_control_signal_chp": 0,
        "prev_control_signal_gas_heater": 0,
    }
    first = compute(**args)
    second = compute(**args)
    assert first == second == (1, 1, 1, 50.0, 3)
    # The function must not mutate its arguments (ints/floats are immutable, but
    # this guards against future regressions that e.g. return cached state).
    assert args["temperature_storage_target_c"] == 45.0
    assert args["timestep_of_hysteresis"] == 3


@pytest.mark.base
def test_compute_storage_control_return_order_and_types() -> None:
    """The returned tuple follows the documented element order and types."""
    chp, gas, hp, target_c, hyst = compute(
        delta_temperature=6.0,
        temperature_storage=40.0,
        temperature_storage_target=50.0,
        temperature_storage_target_hysteresis=45.0,
        temperature_storage_target_c=45.0,
        timestep_of_hysteresis=3,
        timestep=9,
        prev_control_signal_chp=0,
        prev_control_signal_gas_heater=0,
    )
    assert (chp, gas, hp) == (1, 1, 1)
    assert target_c == 50.0
    assert hyst == 3
