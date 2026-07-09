"""Tests for the side-effect-free parts of :mod:`hisim.log`.

Covers :meth:`LogPrio.get_prio_string` (a static method that maps an
``int`` priority to its short abbreviation, falling back to ``"???"`` for
unknown keys) plus a few cheap, deterministic assertions that pin the
contract of the :class:`LogPrio` enum and the ``LOGGING_DEFAULT_LEVEL``
module constant. The I/O / state-mutating ``Logger`` methods are intentionally
not touched here.
"""
# clean

import pytest

from hisim import log
from hisim.log import LOGGING_DEFAULT_LEVEL, LogPrio


@pytest.mark.base
@pytest.mark.parametrize(
    ("prio", "expected"),
    [
        (LogPrio.ERROR, "ERR"),
        (LogPrio.WARNING, "WRN"),
        (LogPrio.INFORMATION, "IFO"),
        (LogPrio.DEBUG, "DBG"),
        (LogPrio.PROFILE, "PRF"),
        (LogPrio.TRACE, "TRC"),
    ],
)
def test_get_prio_string_known_priorities(prio: int, expected: str) -> None:
    """Each defined ``LogPrio`` value maps to its abbreviation."""

    assert LogPrio.get_prio_string(prio) == expected


@pytest.mark.base
@pytest.mark.parametrize(
    "unknown",
    [0, 7, -1, 999],
)
def test_get_prio_string_unknown_priorities(unknown: int) -> None:
    """Unknown / out-of-range integers return the ``"???"`` fallback."""

    assert LogPrio.get_prio_string(unknown) == "???"


@pytest.mark.base
def test_logprio_enum_and_default_level_contract() -> None:
    """Pin the numeric contract of the enum and default level constant."""

    assert int(LogPrio.ERROR) == 1
    assert int(LogPrio.TRACE) == 6
    assert LOGGING_DEFAULT_LEVEL == 3


@pytest.mark.base
def test_get_prio_string_accepts_bare_int_for_enum_members() -> None:
    """``get_prio_string`` is keyed on the enum member's ``int`` value.

    Passing the raw integer should produce the same result as passing the
    enum member, since the lookup dict uses ``LogPrio`` members (which are
    ``IntEnum`` instances) as keys.
    """

    for member in LogPrio:
        assert LogPrio.get_prio_string(int(member)) == LogPrio.get_prio_string(member)


@pytest.mark.base
def test_get_prio_string_is_side_effect_free() -> None:
    """``get_prio_string`` must not depend on or mutate module/class state."""

    level_before = log.Logger.logging_level
    path_before = log.Logger.logging_path
    buffer_before = log.Logger.log_buffer

    # Call with a known and an unknown value.
    LogPrio.get_prio_string(LogPrio.INFORMATION)
    LogPrio.get_prio_string(42)

    assert log.Logger.logging_level == level_before
    assert log.Logger.logging_path == path_before
    assert log.Logger.log_buffer == buffer_before
