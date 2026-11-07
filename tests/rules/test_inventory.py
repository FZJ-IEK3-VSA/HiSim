"""Tests of the inventory: the one list of every check HiSim applies to a setup, in step with the registry."""

import pytest

from hisim.setup_rules.inventory import Inventory, RuleNature
from hisim.setup_rules.registry import SetupRuleRegistry
from hisim.setup_rules.rule import RuleCodes, RuleStage

pytestmark = pytest.mark.base


def test_every_rule_of_the_registry_is_in_the_inventory_and_every_record_in_the_package_is_a_rule() -> None:
    """Catches a moved rule without its record, or a record that says a check moved while the registry lacks it."""
    registry = SetupRuleRegistry.collect()
    rule_ids = {f"{RuleCodes.family_of(rule.__module__)}.{rule.NAME.replace('-', '_')}": rule for rule in registry.rules}
    in_package = {record.rule_id: record for record in Inventory.RECORDS if record.in_package}

    assert set(rule_ids) == set(in_package)
    for rule_id, rule in rule_ids.items():
        record = in_package[rule_id]
        assert rule.CODE in record.codes and record.stage == rule.STAGE and record.target == RuleCodes.family_of(rule.__module__), rule_id


def test_every_record_names_a_nature_a_time_and_a_place() -> None:
    """Catches a record a reader cannot place: no nature, no time it runs, no place it lives."""
    for record in Inventory.RECORDS:
        assert isinstance(record.nature, RuleNature), record.rule_id
        assert record.when and record.lives and record.checks, record.rule_id


def test_the_ids_are_unique_and_the_moving_records_name_a_family_and_a_stage() -> None:
    """Catches two records under one id, or a check that moves to no family or to no stage."""
    ids = [record.rule_id for record in Inventory.RECORDS]
    assert len(set(ids)) == len(ids)
    for record in Inventory.RECORDS:
        if record.moves:
            assert record.target in RuleCodes.PREFIX_BY_FAMILY and isinstance(record.stage, RuleStage), record.rule_id
            assert all(RuleCodes.CODE_PATTERN.match(code) for code in record.codes), record.rule_id
        else:
            assert record.stage is None and not record.in_package, record.rule_id


def test_the_inventory_holds_every_row_of_its_design_record() -> None:
    """Catches records lost or duplicated when a family pull request edits the list: 199 checks, 128 of which move."""
    assert len(Inventory.RECORDS) == 199
    assert sum(record.moves for record in Inventory.RECORDS) == 128


def test_a_record_is_found_by_its_id() -> None:
    """Catches ``show`` and ``list`` missing the nature of a rule whose record exists."""
    record = Inventory.record("energy_ports.room_heat_needs_receiver")

    assert record is not None and record.nature == RuleNature.DOMAIN and record.target == "energy_ports"
    assert Inventory.record("energy_ports.no_such_rule") is None
