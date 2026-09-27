"""Participant canary for ``household_heatpump_solar_thermal_building_sizer``.

It grows the occupancy, both heat pump draws and the collector pump.

TEMPORARY — part of the P3 parity rig's tests and deleted with ``test_p3_parity.py`` when P6 retires
the rig (hisim-b3b.22). The check itself lives there as ``check_participant_targets``; this module
exists only so that ``--dist loadfile`` can run this canary on another worker than its sibling
(hisim-60sv.10). ``test_every_participant_canary_has_its_own_module`` keeps one module per canary.
"""

from pathlib import Path

import pytest

from tests.test_p3_parity import check_participant_targets

#: The setup this module builds, a key of ``Rig.PARTICIPANT_CANARIES``.
STEM = "household_heatpump_solar_thermal_building_sizer"


# Builds a building sizer for real, load profile and all, so it belongs to a full-simulation shard;
# it is not simulated, only built far enough to name its ports.
@pytest.mark.extendedbase
def test_the_table_still_spells_the_participant_targets_the_ems_sizers_actually_grow(tmp_path: Path) -> None:
    """Catches a table missing, or misspelling, a row for a dispatch output this sizer grows."""
    check_participant_targets(STEM, tmp_path)
