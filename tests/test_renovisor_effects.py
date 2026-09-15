"""Tests of the effect accumulator and its resolver.

The resolver is where "several measures on one wall" becomes one number, and where two measures
that disagree become a refusal instead of a last-writer-wins. Both are pinned here against
hand-computed values and a stubbed U-value source, so the arithmetic is checked without a TABULA
lookup in the way.
"""

from typing import Dict

import pytest

from hisim.renovisor.base_files import BaseFileKey
from hisim.renovisor.effects import Effects, LawRequest, SizingLaw
from hisim.renovisor.envelope import RegulatoryTargets, UValueComposer
from hisim.renovisor.materials import InsulationMaterials
from hisim.renovisor.reasons import ReasonCode
from hisim.renovisor.vocabulary import HeatGenerator, SolarThermalSupplies, ThermalElement

pytestmark = pytest.mark.base


class FixedUValues:
    """A U-value source with hand-chosen numbers, so the resolver's arithmetic is checkable."""

    def __init__(self, values: Dict[ThermalElement, float]) -> None:
        """Store the per-element values this source reports."""
        self._values = values

    def u_value(self, element: ThermalElement) -> float:
        """Return the stored value for the element."""
        return self._values[element]

    def source_of(self, element: ThermalElement) -> str:
        """Return a fixed phrase naming this stub as the source."""
        del element
        return "a fixed test source"


@pytest.fixture(name="materials", scope="module")
def fixture_materials() -> InsulationMaterials:
    """The committed material table."""
    return InsulationMaterials.load()


@pytest.fixture(name="targets", scope="module")
def fixture_targets() -> RegulatoryTargets:
    """The committed Irish target table."""
    return RegulatoryTargets.load()


@pytest.fixture(name="current")
def fixture_current() -> FixedUValues:
    """A building whose every element starts at a round, checkable U-value."""
    return FixedUValues({element: 2.0 for element in ThermalElement})


@pytest.fixture(name="effects")
def fixture_effects(
    materials: InsulationMaterials, current: FixedUValues, targets: RegulatoryTargets
) -> Effects:
    """An empty accumulator over the committed tables and the stubbed U-values."""
    return Effects(materials, current, targets)


def test_two_layers_on_one_element_compose_once(effects: Effects, current: FixedUValues) -> None:
    """Requirement M2: both layers contribute and exactly one U-value comes out."""
    effects.add_thermal_resistance(ThermalElement.FACADE, "polystyrene_eps_rigid_board", 80, "EXTERNAL_INSULATION")
    effects.add_thermal_resistance(ThermalElement.FACADE, "wood_fiber_rigid_board", 60, "INTERNAL_DRY_LINING")

    resolved = effects.resolve(None, current)

    expected = UValueComposer.compose(
        2.0, [UValueComposer.resistance(80, 0.0355), UValueComposer.resistance(60, 0.041)]
    )
    assert resolved.u_values == {ThermalElement.FACADE: pytest.approx(expected)}
    assert "EXTERNAL_INSULATION" in resolved.u_value_notes[ThermalElement.FACADE]
    assert "INTERNAL_DRY_LINING" in resolved.u_value_notes[ThermalElement.FACADE]


def test_a_replacement_sets_the_baseline_a_layer_builds_on(effects: Effects, current: FixedUValues) -> None:
    """A replaced element starts from the new unit's value, and a later layer improves on that."""
    effects.set_u_value(ThermalElement.WINDOW, 1.2, "WINDOW_REPLACEMENT")
    resolved = effects.resolve(None, current)
    assert resolved.u_values[ThermalElement.WINDOW] == pytest.approx(1.2)

    effects.add_thermal_resistance(ThermalElement.WINDOW, "metac_glasswool", 20, "SOMETHING")
    resolved = effects.resolve(None, current)
    assert resolved.u_values[ThermalElement.WINDOW] < 1.2


def test_an_untouched_element_gets_no_write(effects: Effects, current: FixedUValues) -> None:
    """Only the elements a measure acted on are written, so the report stays truthful."""
    effects.add_thermal_resistance(ThermalElement.ROOF, "metac_glasswool", 100, "WARM_ROOF_INSULATION")

    assert set(effects.resolve(None, current).u_values) == {ThermalElement.ROOF}


def test_inventory_writes_are_collected(effects: Effects, current: FixedUValues) -> None:
    """A value write reaches the resolution as a plain path-to-value entry."""
    effects.set_inventory_field("building_config.general.set_heating_temperature_in_celsius", "M", value=21)

    assert effects.resolve(None, current).writes == {
        "building_config.general.set_heating_temperature_in_celsius": 21
    }


def test_two_writes_of_the_same_value_do_not_conflict(effects: Effects, current: FixedUValues) -> None:
    """Agreement is not a conflict; only a disagreement is."""
    effects.set_inventory_field("a.b", "FIRST", value=21)
    effects.set_inventory_field("a.b", "SECOND", value=21)

    assert effects.resolve(None, current).refusals == ()


def test_two_writes_of_different_values_refuse(effects: Effects, current: FixedUValues) -> None:
    """Last-writer-wins would hide one of the two measures; a refusal names both."""
    effects.set_inventory_field("a.b", "FIRST", value=21)
    effects.set_inventory_field("a.b", "SECOND", value=23)

    refusals = effects.resolve(None, current).refusals
    assert len(refusals) == 1
    assert refusals[0].reason is ReasonCode.CONFLICTING_WRITES
    assert refusals[0].path == "a.b"


def test_a_law_is_recorded_rather_than_run(effects: Effects, current: FixedUValues) -> None:
    """Decision Q12: the battery law needs simulation inputs, so it travels as a request."""
    request = LawRequest(law=SizingLaw.BATTERY_FROM_DAYS_TO_COVER, argument=2.0)
    effects.set_inventory_field("energy_system_config.battery_storage.capacity_in_kwh", "M", law=request)

    resolved = effects.resolve(None, current)
    assert resolved.pending_laws == {"energy_system_config.battery_storage.capacity_in_kwh": request}
    assert resolved.writes == {}


def test_a_write_needs_exactly_one_of_a_value_and_a_law(effects: Effects) -> None:
    """Both or neither would leave the resolver guessing which one to apply."""
    with pytest.raises(ValueError):
        effects.set_inventory_field("a.b", "M")
    with pytest.raises(ValueError):
        effects.set_inventory_field("a.b", "M", value=1, law=LawRequest(SizingLaw.PV_SHARE_OF_ROOF, 0.5))


def test_variants_and_groups_are_collected(effects: Effects, current: FixedUValues) -> None:
    """Variant selections and group switches reach the parametriser as they were asked for."""
    effects.select_variant("electricity_management", "ems_with_battery", "BATTERY_SYSTEM")
    effects.enable_group("photovoltaics", "PHOTOVOLTAIC_SYSTEM")
    effects.enable_group("photovoltaics", "PHOTOVOLTAIC_SYSTEM")

    resolved = effects.resolve(None, current)
    assert resolved.variant_selections == {"electricity_management": "ems_with_battery"}
    assert resolved.enabled_groups == ("photovoltaics",)


def test_two_variant_options_for_one_variant_refuse(effects: Effects, current: FixedUValues) -> None:
    """A variant is exclusive by definition, so two options for it is a contradiction."""
    effects.select_variant("electricity_management", "ems_with_battery", "FIRST")
    effects.select_variant("electricity_management", "metered_directly", "SECOND")

    refusals = effects.resolve(None, current).refusals
    assert refusals[0].reason is ReasonCode.CONFLICTING_WRITES


def test_base_file_wishes_merge_field_by_field(effects: Effects, current: FixedUValues) -> None:
    """Each measure speaks to one field of the key and stays silent about the others."""
    effects.select_base_file("HEATING_SYSTEM", generator=HeatGenerator.HEAT_PUMP)
    effects.select_base_file("SOLAR_THERMAL_SYSTEM", solar_thermal=SolarThermalSupplies.DHW_ONLY)
    effects.select_base_file("ELECTRIC_VEHICLE", cars=1)

    selection = effects.resolve(None, current).base_file
    assert selection.generator is HeatGenerator.HEAT_PUMP
    assert selection.solar_thermal is SolarThermalSupplies.DHW_ONLY
    assert selection.cars == 1


def test_two_generators_refuse(effects: Effects, current: FixedUValues) -> None:
    """Two measures cannot both decide which generator the building ends up with."""
    effects.select_base_file("FIRST", generator=HeatGenerator.HEAT_PUMP)
    effects.select_base_file("SECOND", generator=HeatGenerator.GAS_HEATING)

    refusals = effects.resolve(None, current).refusals
    assert refusals[0].reason is ReasonCode.CONFLICTING_WRITES
    assert "generator" in refusals[0].path


def test_no_effects_and_refusals_are_kept(effects: Effects, current: FixedUValues) -> None:
    """Requirement M8: nothing is dropped, so the report can state what did nothing."""
    effects.no_effect(ReasonCode.NO_SHADING_MODEL, "OUTSIDE_SHADING")
    effects.refuse(ReasonCode.MATERIAL_NOT_IN_DATABASE, "p", "no row", "WARM_ROOF_INSULATION")

    resolved = effects.resolve(None, current)
    assert [item.reason for item in resolved.no_effects] == [ReasonCode.NO_SHADING_MODEL]
    assert [item.reason for item in resolved.refusals] == [ReasonCode.MATERIAL_NOT_IN_DATABASE]
    assert resolved.refusals[0].as_detail().measure_id == "WARM_ROOF_INSULATION"


def test_target_driven_thickness_names_its_rule(effects: Effects) -> None:
    """The default a measure applies carries the target, the inputs and the simplification."""
    default = effects.target_driven_thickness(ThermalElement.FACADE, "polystyrene_eps_rigid_board", "wall")

    assert default.value == 90
    assert "0.35 W/m2K" in default.source
    assert "no thermal-bridge surcharge" in default.source
    assert "PROVISIONAL" in default.source


def test_effects_are_recorded_per_measure(effects: Effects) -> None:
    """The report needs to know what one measure produced, in order."""
    effects.no_effect(ReasonCode.NO_SHADING_MODEL, "OUTSIDE_SHADING")
    effects.enable_group("photovoltaics", "PHOTOVOLTAIC_SYSTEM")

    assert len(effects.by_measure("OUTSIDE_SHADING")) == 1
    assert len(effects.all()) == 2


def test_the_base_file_key_is_hashable() -> None:
    """The selection table is a dictionary keyed by the combination, so the key has to hash."""
    key = BaseFileKey(generator=HeatGenerator.GAS_HEATING, solar_thermal=False, cars=0)
    assert {key: "file"}[key] == "file"


def test_every_reason_code_has_a_description_and_a_group() -> None:
    """``errors.json`` carries the text, so a code added without one would ship as an empty string."""
    for code in ReasonCode:
        assert code.describe()
        assert code.describe().endswith(".")
        assert code.group()


def test_reason_codes_are_spelled_like_every_other_vocabulary() -> None:
    """Decision C3: member names are the wire spelling and the values equal them."""
    for code in ReasonCode:
        assert code.value == code.name == code.name.upper()
