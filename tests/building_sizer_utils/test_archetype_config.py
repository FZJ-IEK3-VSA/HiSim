"""Tests for ``ArcheTypeConfig.resolve_lpg_households``.

The eleven building-sizer setups used to resolve the configured LPG household profile
names themselves, with a loop that skipped any name the Load Profile Generator's
``Households`` registry did not define. A misspelled name therefore built a building with
fewer occupants than the configuration asked for, without a warning. The resolution now
lives on :class:`~hisim.building_sizer_utils.interface_configs.archetype_config.ArcheTypeConfig`,
next to the ``lpg_households`` field it reads, and refuses an unknown name. These tests
cover the resolver directly; that the eleven setups actually go through it is covered by
``tests/test_system_setups_households_for_building_sizer.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from utspclient.helpers.lpgdata import Households
from utspclient.helpers.lpgpythonbindings import JsonReference

from hisim.building_sizer_utils.interface_configs.archetype_config import ArcheTypeConfig

#: Two household profile names the LPG registry really defines, used wherever a test needs
#: a name that has to resolve rather than be refused.
KNOWN_HOUSEHOLD_NAMES: list[str] = [
    "CHR01_Couple_both_at_Work",
    "CHR03_Family_1_child_both_at_work",
]


@pytest.mark.base
def test_single_configured_household_resolves_to_one_reference() -> None:
    """A one-name configuration resolves to the single registry entry, not to a list.

    The occupancy component distinguishes the two: one reference asks for one household,
    a list asks for a stacked multi-household building.
    """
    resolved = ArcheTypeConfig(lpg_households=[KNOWN_HOUSEHOLD_NAMES[0]]).resolve_lpg_households()

    assert isinstance(resolved, JsonReference)
    assert resolved == getattr(Households, KNOWN_HOUSEHOLD_NAMES[0])


@pytest.mark.base
def test_several_configured_households_resolve_in_configured_order() -> None:
    """A multi-name configuration resolves to the references in the order they were configured."""
    resolved = ArcheTypeConfig(lpg_households=list(KNOWN_HOUSEHOLD_NAMES)).resolve_lpg_households()

    assert isinstance(resolved, list)
    assert resolved == [getattr(Households, name) for name in KNOWN_HOUSEHOLD_NAMES]


@pytest.mark.base
@pytest.mark.parametrize(
    "configured_names",
    [
        ["CHR01_Couple_both_at_Wrok"],
        ["CHR01_Couple_both_at_Work", "CHR01_Couple_both_at_Wrok"],
        ["CHR01_Couple_both_at_Wrok", "CHR01_Couple_both_at_Work"],
    ],
    ids=["alone", "after_a_known_name", "before_a_known_name"],
)
def test_unknown_household_name_is_refused_wherever_it_stands(configured_names: list[str]) -> None:
    """An unknown name is refused whether it stands alone or beside a known one.

    The old loop only dropped names silently in the multi-name branch, so the position of
    the typo decided whether the run failed loudly, failed obscurely or succeeded with the
    wrong household. All three positions now produce the same refusal, and it names the
    misspelled string and the registry that holds the legal names so the caller can find
    the right spelling.
    """
    with pytest.raises(ValueError) as refusal:
        ArcheTypeConfig(lpg_households=configured_names).resolve_lpg_households()

    message = str(refusal.value)
    assert "CHR01_Couple_both_at_Wrok" in message
    assert "utspclient.helpers.lpgdata.Households" in message
    # A near miss gets the closest known spellings named outright.
    assert "CHR01_Couple_both_at_Work" in message


@pytest.mark.base
def test_empty_household_list_is_refused() -> None:
    """An empty ``lpg_households`` list names no occupants at all and is refused."""
    with pytest.raises(ValueError) as refusal:
        ArcheTypeConfig(lpg_households=[]).resolve_lpg_households()

    assert "empty" in str(refusal.value)


@pytest.mark.base
@pytest.mark.parametrize(
    "configured_value",
    ["CHR01_Couple_both_at_Work", None, 5],
    ids=["bare_string", "none", "number"],
)
def test_non_list_household_field_is_refused_as_a_type_error(configured_value: Any) -> None:
    """``lpg_households`` has to be a list of names; anything else is a ``TypeError``.

    A bare string is the interesting case: it would iterate character by character and
    resolve nothing, so it is rejected on its type rather than on its contents.
    """
    with pytest.raises(TypeError) as refusal:
        ArcheTypeConfig(lpg_households=configured_value).resolve_lpg_households()

    assert "List[str]" in str(refusal.value)


@pytest.mark.base
def test_default_configuration_resolves() -> None:
    """The dataclass default names a household the registry defines.

    Guards the default against drifting to a name the LPG no longer ships, which every
    caller relying on the default would then hit as a refusal.
    """
    assert isinstance(ArcheTypeConfig().resolve_lpg_households(), JsonReference)


@pytest.mark.base
def test_registry_constant_names_the_module_the_names_come_from() -> None:
    """The registry quoted in the refusals is the module the resolver really reads.

    The refusal sends the caller to a module path spelled out as a string, so this pins
    that string to the class the resolver imports rather than letting the two drift apart.
    """
    assert ArcheTypeConfig.LPG_HOUSEHOLD_REGISTRY == f"{Households.__module__}.{Households.__name__}"
    # A ClassVar, not a dataclass field: it must not show up in the serialized config.
    assert "LPG_HOUSEHOLD_REGISTRY" not in ArcheTypeConfig().to_dict()  # type: ignore[attr-defined]
