"""Tests for the one lookup behind the three readers of the electrolyzer manufacturer table.

``electrolyzer_manufacturer_config.json`` is read by three configurations: the electrolyzer's
own (``generic_electrolyzer_h2``), its L1 controller's (``controller_l1_electrolyzer_h2``) and
the L2 PtX controller's (``controller_l2_ptx_energy_management_system``). The electrolyzer's
reader has always refused a device name the table does not carry; the two controllers' readers
answered the same name with an empty dictionary, which their per-field ``.get(key, 0.0)``
fallbacks turned into a controller whose loads were all zero. A mistyped device name therefore
did not fail -- it produced a plausible-looking run with an idle electrolyzer, and the two
readers of one file disagreed about the same name.

These tests pin the decision (P4 D-26): all three readers refuse an unknown name through one
shared lookup, the message names the devices that do exist, a row missing a field a reader needs
is refused by the name of the field, and the one device the live setup uses still builds exactly
the values the table carries for it.
"""

# clean

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from hisim.components import controller_l1_electrolyzer_h2 as l1
from hisim.components import controller_l2_ptx_energy_management_system as l2
from hisim.components import generic_electrolyzer_h2 as electrolyzer
from hisim.config import ComponentID

#: the device the live setup ``electrolyzer_with_renewables`` builds, through two of the readers.
KNOWN_DEVICE = "HTecME450"

#: a name close enough to the known one to be a plausible typo, and absent from the table.
UNKNOWN_DEVICE = "HTecME451"


def _table() -> Dict[str, Any]:
    """Returns the "Electrolyzer variants" section of the shipped manufacturer table."""
    path = electrolyzer.electrolyzer_table_path()
    variants: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["Electrolyzer variants"]
    return variants


def _read_through_each_reader(device: str) -> Dict[str, Any]:
    """Calls all three readers on one device name, returning what each returned."""
    return {
        "electrolyzer": electrolyzer.ElectrolyzerConfig.read_config(device),
        "l1": l1.ElectrolyzerControllerConfig.read_config(device),
        "l2": l2.PTXControllerConfig.read_config(device),
    }


@pytest.mark.base
def test_every_reader_refuses_an_unknown_device_naming_the_ones_that_exist() -> None:
    """All three readers raise on a name the table does not carry, and list the names it does.

    The two controllers' readers used to return ``{}`` here, so this is the case where they
    disagreed with the electrolyzer's own. The assertion is on the three ingredients of a repair
    instruction: the name written, the near match, and the full list.
    """
    available = sorted(_table())

    for reader_name, read in (
        ("electrolyzer", electrolyzer.ElectrolyzerConfig.read_config),
        ("l1", l1.ElectrolyzerControllerConfig.read_config),
        ("l2", l2.PTXControllerConfig.read_config),
    ):
        with pytest.raises(ValueError) as raised:
            read(UNKNOWN_DEVICE)

        message = str(raised.value)
        assert UNKNOWN_DEVICE in message, reader_name
        assert KNOWN_DEVICE in message, reader_name
        assert all(device in message for device in available), reader_name
        assert "electrolyzer_manufacturer_config.json" in message, reader_name


@pytest.mark.base
def test_the_three_factories_refuse_an_unknown_device_too() -> None:
    """The refusal reaches the config builders, not only the readers under them.

    A setup calls ``config_electrolyzer`` and the two ``control_electrolyzer`` classmethods; the
    zero-filled config was built there, so that is where the failure has to arrive.
    """
    with pytest.raises(ValueError, match=UNKNOWN_DEVICE):
        electrolyzer.ElectrolyzerConfig.config_electrolyzer(UNKNOWN_DEVICE)

    with pytest.raises(ValueError, match=UNKNOWN_DEVICE):
        l1.ElectrolyzerControllerConfig.control_electrolyzer(UNKNOWN_DEVICE)

    with pytest.raises(ValueError, match=UNKNOWN_DEVICE):
        l2.PTXControllerConfig.control_electrolyzer(UNKNOWN_DEVICE, l2.PtxOperationMode.NOMINAL_LOAD)


@pytest.mark.base
def test_all_three_readers_return_the_same_row_for_a_known_device() -> None:
    """One device name, one row: the readers no longer differ in what they see."""
    rows = _read_through_each_reader(KNOWN_DEVICE)

    assert rows["electrolyzer"] == rows["l1"] == rows["l2"] == _table()[KNOWN_DEVICE]


@pytest.mark.base
def test_the_known_device_still_builds_the_values_the_table_carries() -> None:
    """``HTecME450`` builds what it built before, pinned against the table's literals.

    The figures are written out rather than read back off the JSON, so that an edit to the table
    or to a reader has to be intentional to pass. They are the ones the live setup runs on.
    """
    machine = electrolyzer.ElectrolyzerConfig.config_electrolyzer(
        KNOWN_DEVICE, component_id=ComponentID(name=KNOWN_DEVICE)
    )
    assert machine.electrolyzer_type == "PEM"
    assert machine.nom_load == 987.0
    assert machine.max_load == 1028.225
    assert machine.nom_h2_flow_rate == 18.875
    assert machine.faraday_eff == 0.999

    controller = l1.ElectrolyzerControllerConfig.control_electrolyzer(KNOWN_DEVICE)
    assert controller.nom_load == 987.0
    assert controller.min_load == 205.462
    assert controller.max_load == 1028.225
    assert controller.standby_load == 41.225
    assert controller.warm_start_time == 30.0
    assert controller.cold_start_time == 600.0

    ptx = l2.PTXControllerConfig.control_electrolyzer(KNOWN_DEVICE, l2.PtxOperationMode.NOMINAL_LOAD)
    assert ptx.nom_load == 987.0
    assert ptx.min_load == 205.462
    assert ptx.max_load == 1028.225
    assert ptx.standby_load == 41.225
    assert ptx.operation_mode is l2.PtxOperationMode.NOMINAL_LOAD


@pytest.mark.base
def test_every_shipped_variant_carries_the_fields_the_two_controllers_read() -> None:
    """No shipped row leans on a fallback: every one carries all six controller fields.

    This is what makes dropping the ``.get(key, 0.0)`` fallbacks result-neutral for the table as
    it stands, and it fails the day a row is added without them -- which is the point.
    """
    needed = set(l1.ElectrolyzerControllerConfig.TABLE_FIELDS) | set(l2.PTXControllerConfig.TABLE_FIELDS)

    for device, row in _table().items():
        assert needed <= set(row), f"{device} is missing {sorted(needed - set(row))}"


@pytest.mark.base
def test_a_row_missing_a_field_is_refused_by_the_name_of_the_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A field a reader needs and a row does not carry is an error, not a zero.

    The table is replaced with one whose single row lacks ``standby_load`` and ``cold_start_time``.
    Before the change these read as ``0.0``, giving a controller that never went to standby and
    restarted instantly.
    """
    broken = tmp_path / "electrolyzer_manufacturer_config.json"
    broken.write_text(
        json.dumps({"Electrolyzer variants": {"OnlyDevice": {"nom_load": 10.0, "min_load": 1.0, "max_load": 11.0}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(electrolyzer, "electrolyzer_table_path", lambda: broken)

    with pytest.raises(ValueError) as raised:
        l1.ElectrolyzerControllerConfig.control_electrolyzer("OnlyDevice")
    message = str(raised.value)
    assert "OnlyDevice" in message
    assert "standby_load" in message and "cold_start_time" in message

    with pytest.raises(ValueError, match="standby_load"):
        l2.PTXControllerConfig.control_electrolyzer("OnlyDevice", l2.PtxOperationMode.NOMINAL_LOAD)

    # The name lookup keeps working against the substituted table, so the two failures are told
    # apart: an unknown name lists the one device this table does carry.
    with pytest.raises(ValueError, match="OnlyDevice"):
        l1.ElectrolyzerControllerConfig.read_config("NoSuchDevice")


@pytest.mark.base
def test_a_field_written_as_null_is_passed_through_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A field present as ``null`` is not treated as missing.

    Five of the nine shipped rows write ``standby_load`` as ``null``, and both controllers have
    always passed that value through -- ``.get`` defaults on an absent key, not on a written
    ``null``. Refusing it would retire devices that run today, so presence is what is checked.
    """
    with_null = tmp_path / "electrolyzer_manufacturer_config.json"
    with_null.write_text(
        json.dumps(
            {
                "Electrolyzer variants": {
                    "NullStandby": {
                        "nom_load": 10.0,
                        "min_load": 1.0,
                        "max_load": 11.0,
                        "standby_load": None,
                        "warm_start_time": 30.0,
                        "cold_start_time": 600.0,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(electrolyzer, "electrolyzer_table_path", lambda: with_null)

    assert l1.ElectrolyzerControllerConfig.control_electrolyzer("NullStandby").standby_load is None


@pytest.mark.base
def test_a_missing_table_file_propagates_with_its_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A table that is not there raises ``FileNotFoundError`` naming the path it looked for."""
    absent = tmp_path / "not_here" / "electrolyzer_manufacturer_config.json"
    monkeypatch.setattr(electrolyzer, "electrolyzer_table_path", lambda: absent)

    with pytest.raises(FileNotFoundError) as raised:
        electrolyzer.ElectrolyzerConfig.read_config(KNOWN_DEVICE)

    assert str(absent) in str(raised.value)
