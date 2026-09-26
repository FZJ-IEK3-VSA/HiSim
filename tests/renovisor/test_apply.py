"""T-APPLY: every row of §4.2 writes exactly the stated fields, and the original is untouched.

The measure layer's whole claim is that applying a measure is a plain overwrite into a copy of
the house. These tests hold it to that: for each measure a deep diff between the house before
and the house after is compared against the fields the contract's table names, so a measure that
quietly writes a sixth field fails, and so does one that stops writing a fifth.

Two behaviours need their own tests beyond the table: layers stack in list order, and
``hot_water_system.supply=separate_heat_pump`` depends on the generator the *package* installs
rather than on the one it replaced.
"""

import copy
from typing import Any, Dict, Mapping, Set, Tuple

import pytest

from hisim.renovisor.apply import MeasureRegistry, SelectsNothing, apply
from hisim.renovisor.capabilities import ProbeSet
from hisim.renovisor.constants import BatteryLaw, LayerDefaults, OpeningUValues
from hisim.renovisor.contract import ContractFiles
from hisim.renovisor.request import CatalogueTable, Measure, Request
from hisim.renovisor.vocabulary import ReportStatus
from hisim.renovisor.whitelist import TranslatorError, Whitelist


def whitelist() -> Whitelist:
    """Return a freshly parsed committed whitelist."""
    return Whitelist.load()


def anchor_house() -> Dict[str, Any]:
    """Return the house of the vendored mockup, with no package applied."""
    return copy.deepcopy(ContractFiles.request_mockup()["house"])


def measures_of(*entries: Mapping[str, Any]) -> Tuple[Measure, ...]:
    """Return typed measures from plain request entries."""
    return tuple(Measure.from_dict(entry) for entry in entries)


def leaves(document: Any, prefix: str = "") -> Dict[str, Any]:
    """Return every leaf of a nested structure, by dotted path."""
    found: Dict[str, Any] = {}
    if isinstance(document, dict):
        for key, value in document.items():
            found.update(leaves(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(document, list):
        for index, item in enumerate(document):
            found.update(leaves(item, f"{prefix}[{index}]"))
    else:
        found[prefix] = document
    return found


def difference(before: Mapping[str, Any], after: Mapping[str, Any]) -> Set[str]:
    """Return every path whose value changed, appeared or disappeared."""
    first, second = leaves(before), leaves(after)
    return {
        path
        for path in set(first) | set(second)
        if first.get(path, "__absent__") != second.get(path, "__absent__")
    }


#: measure id -> the house paths it is allowed to touch. Paths under ``added_insulation`` are
#: collapsed to the block, because a layer's own fields are the layer's business.
TOUCHED: Dict[str, Set[str]] = {
    "external_insulation": {"building.facade"},
    "internal_dry_lining_insulation": {"building.facade"},
    "cavity_wall_insulation": {"building.facade"},
    "basement_ceiling_insulation": {"building.floor"},
    "basement_internal_insulation": {"building.floor"},
    "basement_external_insulation": {"building.floor"},
    "solid_ground_floor_insulation": {"building.floor"},
    "suspended_ground_floor_insulation": {"building.floor"},
    "warm_roof_insulation": {"building.roof"},
    "rafter_insulation": {"building.roof"},
    "rolled_out_attic_insulation": {"building.roof"},
    "top_floor_ceiling_insulation": {"building.roof"},
    "window_replacement": {
        "building.window.glazing_panes",
        "building.window.frame_material",
        "building.window.low_emissivity_coating",
        "building.window.u_value_in_watt_per_m2_per_kelvin",
    },
    "door_replacement": {
        "building.door.glazing_panes",
        "building.door.frame_material",
        "building.door.u_value_in_watt_per_m2_per_kelvin",
    },
    "outside_shading": {"building.window.outside_shading"},
    "thermocover_for_the_windows": {"building.window.thermocover"},
    "ventilation_system": {"ventilation.type_of_system"},
    "shallow_air_tightness_measures": {"ventilation.air_tightness"},
    "diy_sealing_of_air_leaks": {"ventilation.air_tightness"},
    "hot_water_tank_and_pipe_insulation": {"hot_water.tank_and_pipe_insulated"},
    "heating_system": {"heating.type_of_system", "heating.installation_year"},
    "heating_installation": {"heat_distribution.type_of_system"},
    "air_conditioners": {"air_conditioning.power_in_watt"},
    "hot_water_system": {"hot_water.supply"},
    "temperature_control_system": {"temperature_control.type_of_system"},
    "replace_white_appliances": {"appliances.white_appliances"},
    "photovoltaic_system": {
        "pv_system.size_in_percent_of_roof_area",
        "pv_system.power_in_watt",
        "pv_system.azimuth",
        "pv_system.tilt",
    },
    "battery_system": {
        "battery.custom_battery_capacity_generic_in_kilowatt_hour",
        "battery.power_in_watt",
        "battery.days_to_cover",
    },
    "solar_thermal_system": {"solar_thermal_system.supplies"},
    "electric_vehicle": {"electric_vehicles.number"},
    "change_room_temperature": {"building.set_heating_temperature_in_celsius"},
    "optimize_behaviour_for_self_consumption_of_pv": {"occupancy.pv_self_consumption_optimised"},
}


def collapse(paths: Set[str]) -> Set[str]:
    """Collapse every path inside an element block to that block, for the diff comparison."""
    collapsed: Set[str] = set()
    for path in paths:
        for block in ("building.facade", "building.floor", "building.roof"):
            if path.startswith(block):
                collapsed.add(block)
                break
        else:
            collapsed.add(path)
    return collapsed


@pytest.mark.base
class TestEveryRowOfTheTable:
    """Each measure writes exactly the fields §4.2 says, on a deep diff of the whole house."""

    @pytest.mark.parametrize("measure_id", sorted(CatalogueTable.ids()))
    def test_a_measure_touches_no_field_the_table_does_not_name(self, measure_id: str) -> None:
        """A deep diff of the house before and after names nothing the table does not.

        The comparison is one-sided on purpose. A measure whose probe writes the value the
        anchor already carries -- ``window_replacement`` with two panes onto a two-pane window --
        changes nothing at that path, and that is right: applying a measure is an overwrite, and
        overwriting a value with itself is not a change. What must never happen is the other
        direction, a field the table does not name being written, which is what this asserts.
        """
        before = anchor_house()
        entry = ProbeSet.package(measure_id)

        applied = apply(before, measures_of(entry), whitelist())

        assert collapse(difference(before, applied.house)) <= TOUCHED[measure_id]

    @pytest.mark.parametrize("measure_id", sorted(CatalogueTable.ids()))
    def test_a_measure_writes_something_unless_it_writes_what_was_already_there(
        self, measure_id: str
    ) -> None:
        """Every measure leaves a mark on the house, or the value it wrote was already set."""
        before = anchor_house()

        applied = apply(before, measures_of(ProbeSet.package(measure_id)), whitelist())

        changed = collapse(difference(before, applied.house))
        already = {path for path in TOUCHED[measure_id] if path in leaves(before)}
        assert changed or already, f"{measure_id} wrote nothing anywhere"

    @pytest.mark.parametrize("measure_id", sorted(CatalogueTable.ids()))
    def test_the_original_house_is_never_mutated(self, measure_id: str) -> None:
        """``apply`` works on a deep copy; the probe set depends on it and so does the caller."""
        before = anchor_house()
        untouched = copy.deepcopy(before)

        apply(before, measures_of(ProbeSet.package(measure_id)), whitelist())

        assert before == untouched

    @pytest.mark.parametrize("measure_id", sorted(CatalogueTable.ids()))
    def test_every_measure_produces_exactly_one_report_entry(self, measure_id: str) -> None:
        """One line per measure, with one option line per option the request carried."""
        applied = apply(anchor_house(), measures_of(ProbeSet.package(measure_id)), whitelist())

        assert len(applied.measures) == 1
        line = applied.measures[0]
        assert line.id == measure_id
        carried = set(ProbeSet.package(measure_id).get("options", {}))
        assert carried <= {option.name for option in line.options}


@pytest.mark.base
class TestInsulationLayers:
    """Layers stack in list order, defaults are reported, and the arithmetic is the composer's."""

    def test_two_measures_on_one_element_stack(self) -> None:
        """External insulation then cavity fill is one wall with two layers, not two answers."""
        applied = apply(
            anchor_house(),
            measures_of(
                ProbeSet.package("external_insulation"),
                ProbeSet.package("cavity_wall_insulation"),
            ),
            whitelist(),
        )

        facade = applied.house["building"]["facade"]
        assert len(facade["added_insulation"]) == 2
        assert facade["u_value_in_watt_per_m2_per_kelvin"] < 0.2
        assert len(applied.layers) == 2

    def test_the_list_order_is_the_order_the_layers_were_added(self) -> None:
        """The second layer is applied to the result of the first, so order is part of the answer."""
        applied = apply(
            anchor_house(),
            measures_of(
                ProbeSet.package("cavity_wall_insulation"),
                ProbeSet.package("external_insulation"),
            ),
            whitelist(),
        )

        placements = [layer["placement"] for layer in applied.house["building"]["facade"]["added_insulation"]]
        assert placements == ["external_wall_cavity", "external_wall_external"]

    def test_an_absent_thickness_is_defaulted_and_the_default_is_reported(self) -> None:
        """Every default is a report line with the value, never a silent number."""
        applied = apply(anchor_house(), measures_of(ProbeSet.package("warm_roof_insulation")), whitelist())

        line = next(
            option for option in applied.measures[0].options if option.name == "thickness_in_mm"
        )
        assert line.status is ReportStatus.DEFAULTED
        assert str(LayerDefaults.thickness_of("warm_roof_insulation")) in (line.note or "")
        assert (line.note or "").startswith("absent from the request")
        assert applied.layers[0].thickness_in_mm == LayerDefaults.thickness_of("warm_roof_insulation")

    @pytest.mark.parametrize("measure_id", ["basement_internal_insulation", "top_floor_ceiling_insulation"])
    def test_the_two_measures_that_gained_a_thickness_option_read_it(self, measure_id: str) -> None:
        """Since contract 882a8c1 these two offer ``thickness_in_mm`` too (hisim-1h7f), and it is read.

        A stated thickness is ``used`` and makes the layer; an absent one is the ordinary
        ``defaulted`` line every other insulation measure reports. Either way the measure stays
        ``used``, because the option is an ``experts`` one.
        """
        assert CatalogueTable.option(measure_id, "thickness_in_mm") is not None
        stated = apply(
            anchor_house(),
            measures_of({"id": measure_id, "options": {"material": ProbeSet.material(), "thickness_in_mm": 140}}),
            whitelist(),
        )
        line = next(option for option in stated.measures[0].options if option.name == "thickness_in_mm")
        assert line.status is ReportStatus.USED
        assert stated.layers[0].thickness_in_mm == 140
        assert not stated.layers[0].thickness_defaulted
        assert stated.measures[0].status is ReportStatus.USED

        absent = apply(anchor_house(), measures_of(ProbeSet.package(measure_id)), whitelist())
        default = LayerDefaults.thickness_of(measure_id)
        line = next(option for option in absent.measures[0].options if option.name == "thickness_in_mm")
        assert line.status is ReportStatus.DEFAULTED
        assert line.note == f"absent from the request; the translator's default for {measure_id} is {default} mm"
        assert absent.layers[0].thickness_in_mm == default
        assert absent.layers[0].thickness_defaulted
        assert absent.measures[0].status is ReportStatus.USED

    def test_a_cavity_deeper_than_the_cavity_is_capped_and_said_so(self) -> None:
        """A cavity cannot be filled deeper than it is wide; the cap is an approximation."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "cavity_wall_insulation", "options": {
                "material": ProbeSet.material(), "thickness_in_mm": 400}}),
            whitelist(),
        )

        assert applied.layers[0].thickness_in_mm == LayerDefaults.CAVITY_MAXIMUM_IN_MM
        line = next(option for option in applied.measures[0].options if option.name == "thickness_in_mm")
        assert line.status is ReportStatus.APPROXIMATED

    @pytest.mark.parametrize(
        "measure_id",
        ["cavity_wall_insulation", "basement_internal_insulation", "top_floor_ceiling_insulation"],
    )
    def test_the_three_formerly_option_less_measures_use_the_requests_material(self, measure_id: str) -> None:
        """Contract PR #10 gave them a ``material`` option; the translator no longer picks one.

        Until then none of the three had a ``material`` option (``cavity_wall_insulation`` had
        only ``thickness_in_mm``), and the translator used a fixed material and reported the
        measure ``approximated``. The layer now carries the request's own material, the option
        and the measure are ``used``, and nothing about it is approximated.
        """
        material = dict(ProbeSet.material(), asp_id="stone_wool", thermal_conductivity_w_mk=0.036)
        applied = apply(anchor_house(), measures_of({"id": measure_id, "options": {"material": material}}), whitelist())

        assert applied.layers[0].material.asp_id == "stone_wool"
        assert applied.layers[0].material.thermal_conductivity_w_mk == 0.036
        line = next(option for option in applied.measures[0].options if option.name == "material")
        assert line.status is ReportStatus.USED
        assert applied.measures[0].status is ReportStatus.USED
        assert "translator uses" not in (applied.measures[0].note or "")

    def test_a_material_that_is_not_an_object_is_a_translator_error(self) -> None:
        """The request checks refuse it first, so reaching the translator with one is exit 3."""
        measure = Measure(id="external_insulation", options={CatalogueTable.MATERIAL: "EPS"})

        with pytest.raises(TranslatorError) as raised:
            apply(anchor_house(), (measure,), whitelist())
        assert "without a material object" in raised.value.message

    def test_the_element_note_carries_the_arithmetic_with_its_numbers(self) -> None:
        """A reader has to be able to redo the division, which is what the note is for."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "external_insulation", "options": {
                "material": ProbeSet.material(), "thickness_in_mm": 120}}),
            whitelist(),
        )

        note = applied.element_note(applied.layers[0].element)
        assert note is not None
        assert "1/(1/1.78 + 0.12/0.0355)" in note


@pytest.mark.base
class TestOpenings:
    """Windows and doors take their U-value from the expert option or from the table."""

    def test_a_replaced_window_without_an_expert_value_uses_the_table(self) -> None:
        """Two panes without a coating is 1.4 W/(m2K) in the translator's own table."""
        applied = apply(
            anchor_house(),
            measures_of(
                {
                    "id": "window_replacement",
                    "options": {"glazing_panes": 3, "frame_material": "wood", "low_emissivity_coating": True},
                }
            ),
            whitelist(),
        )

        window = applied.house["building"]["window"]
        assert window["u_value_in_watt_per_m2_per_kelvin"] == OpeningUValues.window(3, True)
        assert applied.measures[0].status is ReportStatus.APPROXIMATED

    def test_an_expert_u_value_wins_over_the_table(self) -> None:
        """The only way to get a ``used`` line out of a replacement is to state the number."""
        applied = apply(
            anchor_house(),
            measures_of(
                {
                    "id": "window_replacement",
                    "options": {
                        "glazing_panes": 3,
                        "frame_material": "wood",
                        "low_emissivity_coating": True,
                        "u_value_in_watt_per_m2_per_kelvin": 0.62,
                    },
                }
            ),
            whitelist(),
        )

        assert applied.house["building"]["window"]["u_value_in_watt_per_m2_per_kelvin"] == 0.62
        line = next(
            option
            for option in applied.measures[0].options
            if option.name == "u_value_in_watt_per_m2_per_kelvin"
        )
        assert line.status is ReportStatus.USED

    def test_a_replaced_door_takes_its_value_from_the_pane_count(self) -> None:
        """A solid door and a glazed one are different doors, which the table knows."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "door_replacement", "options": {"glazing_panes": 0, "frame_material": "wood"}}),
            whitelist(),
        )

        assert applied.house["building"]["door"]["u_value_in_watt_per_m2_per_kelvin"] == OpeningUValues.door(0)


@pytest.mark.base
class TestReplacementsAndRemovals:
    """The measures whose effect is "this is now a different device", and the one that removes."""

    def test_a_new_generator_removes_what_described_the_old_one(self) -> None:
        """Flow temperature, efficiency, secondary heater and range cooker all go with it."""
        house = anchor_house()
        house["heating"].update(
            {
                "flow_temperature_in_celsius": 70,
                "seasonal_efficiency_in_percent": 80,
                "secondary": "wood_stove",
                "cooking_range": True,
            }
        )

        applied = apply(
            house,
            measures_of({"id": "heating_system", "options": {"type_of_system": "air_source_heat_pump"}}),
            whitelist(),
        )

        assert applied.house["heating"] == {"type_of_system": "air_source_heat_pump"}
        assert "removed" in (applied.measures[0].note or "")

    def test_a_new_array_replaces_the_old_one_and_keeps_its_orientation(self) -> None:
        """The measure describes the new total, not an addition to what was there."""
        house = anchor_house()
        house["pv_system"] = {"power_in_watt": 3000, "azimuth": 200, "tilt": 25}

        applied = apply(
            house,
            measures_of({"id": "photovoltaic_system", "options": {"size_in_percent_of_roof_area": 80}}),
            whitelist(),
        )

        assert applied.house["pv_system"] == {
            "size_in_percent_of_roof_area": 80,
            "azimuth": 200,
            "tilt": 25,
        }

    def test_the_array_s_own_figures_replace_the_old_ones_and_the_power_wins(self) -> None:
        """Contract 882a8c1: power, azimuth and tilt are written; the share is kept and recorded."""
        house = anchor_house()
        house["pv_system"] = {"power_in_watt": 3000, "azimuth": 200, "tilt": 25}

        applied = apply(
            house,
            measures_of({"id": "photovoltaic_system", "options": {
                "size_in_percent_of_roof_area": 60, "power_in_watt": 5500,
                "azimuth_in_degree": 170, "tilt_in_degree": 35}}),
            whitelist(),
        )

        assert applied.house["pv_system"] == {
            "size_in_percent_of_roof_area": 60,
            "power_in_watt": 5500.0,
            "azimuth": 170.0,
            "tilt": 35.0,
        }
        options = {option.name: option for option in applied.measures[0].options}
        for name in ("power_in_watt", "azimuth_in_degree", "tilt_in_degree"):
            assert options[name].status is ReportStatus.USED
        share = options["size_in_percent_of_roof_area"]
        assert share.status is ReportStatus.USED
        assert "power_in_watt sizes the array" in (share.note or "")
        assert applied.measures[0].status is ReportStatus.USED

    def test_the_shading_loss_is_copied_into_the_array_and_not_implemented(self) -> None:
        """§4.2: the measure writes the loss into pv_system; the array is simulated unshaded all the same."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "photovoltaic_system", "options": {
                "size_in_percent_of_roof_area": 60, "shading_losses_in_percent": 8}}),
            whitelist(),
        )

        line = next(option for option in applied.measures[0].options if option.name == "shading_losses_in_percent")
        assert line.status is ReportStatus.NOT_IMPLEMENTED_YET
        assert "unshaded" in (line.note or "")
        assert applied.house["pv_system"]["shading_losses_in_percent"] == 8.0
        assert [option.name for option in applied.measures[0].options].count("shading_losses_in_percent") == 1

    def test_a_new_battery_replaces_the_old_one_entirely(self) -> None:
        """The measure states the new battery; nothing of the old one survives."""
        house = anchor_house()
        house["battery"] = {"custom_battery_capacity_generic_in_kilowatt_hour": 5, "installation_year": 2015}

        applied = apply(
            house,
            measures_of({"id": "battery_system", "options": {"capacity_in_kwh": 10, "power_in_watt": 4000}}),
            whitelist(),
        )

        assert applied.house["battery"] == {
            "custom_battery_capacity_generic_in_kilowatt_hour": 10.0,
            "power_in_watt": 4000.0,
        }
        options = {option.name: option.status for option in applied.measures[0].options}
        assert options == {"capacity_in_kwh": ReportStatus.USED, "power_in_watt": ReportStatus.USED}
        assert applied.measures[0].status is ReportStatus.USED

    def test_an_absent_battery_power_is_the_catalogue_s_half_c_rule(self) -> None:
        """The catalogue: power from the capacity at 0.5 C when unset; reported defaulted with the rule."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "battery_system", "options": {"capacity_in_kwh": 8}}),
            whitelist(),
        )

        assert applied.house["battery"] == {"custom_battery_capacity_generic_in_kilowatt_hour": 8.0}
        line = next(option for option in applied.measures[0].options if option.name == "power_in_watt")
        assert line.status is ReportStatus.DEFAULTED
        assert "0.5 C" in (line.note or "") and "500 W per kWh" in (line.note or "")
        assert applied.measures[0].status is ReportStatus.USED

    def test_an_absent_battery_capacity_is_read_back_from_a_stated_power(self) -> None:
        """The same 0.5 C rule, the other way round: 3000 W is a 6 kWh battery."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "battery_system", "options": {"power_in_watt": 3000}}),
            whitelist(),
        )

        assert applied.house["battery"] == {
            "custom_battery_capacity_generic_in_kilowatt_hour": 6.0,
            "power_in_watt": 3000.0,
        }
        line = next(option for option in applied.measures[0].options if option.name == "capacity_in_kwh")
        assert line.status is ReportStatus.DEFAULTED

    def test_a_battery_measure_stating_neither_number_is_sized_like_the_frontend_sizes_one(self) -> None:
        """One day of the simulated household's electricity, and the measure says it approximated."""
        applied = apply(anchor_house(), measures_of({"id": "battery_system", "options": {}}), whitelist())

        assert applied.house["battery"] == {"days_to_cover": BatteryLaw.DAYS_TO_COVER_WHEN_UNSIZED}
        assert applied.measures[0].status is ReportStatus.APPROXIMATED
        statuses = {option.name: option.status for option in applied.measures[0].options}
        assert statuses == {"capacity_in_kwh": ReportStatus.DEFAULTED, "power_in_watt": ReportStatus.DEFAULTED}


@pytest.mark.base
class TestTheWhitelistIsAskedOfTheRenovatedHouse:
    """``when`` is evaluated after the package, not before it."""

    def test_a_dhw_heat_pump_is_the_space_heating_one_when_the_package_installs_a_heat_pump(self) -> None:
        """The generator the request had is a gas boiler; the package makes it a heat pump.

        The heat pump then makes the hot water too, which stands in for a separate one
        (hisim-7hq9): the value is approximated with a substitution sentence, not used.
        """
        applied = apply(
            anchor_house(),
            measures_of(
                {"id": "heating_system", "options": {"type_of_system": "air_source_heat_pump"}},
                {"id": "hot_water_system", "options": {"supply": "separate_heat_pump"}},
            ),
            whitelist(),
        )

        supply = next(
            option for option in applied.measures[1].options if option.name == "supply"
        )
        assert supply.status is ReportStatus.APPROXIMATED
        assert supply.note == SelectsNothing.HOT_WATER_SEPARATE_HEAT_PUMP
        assert "modelled as" in supply.note
        assert applied.measures[1].status is ReportStatus.APPROXIMATED

    def test_the_same_measure_is_not_implemented_on_a_boiler_house(self) -> None:
        """No separate domestic-hot-water heat pump component exists for a boiler."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "hot_water_system", "options": {"supply": "separate_heat_pump"}}),
            whitelist(),
        )

        supply = next(option for option in applied.measures[0].options if option.name == "supply")
        assert supply.status is ReportStatus.NOT_IMPLEMENTED_YET
        assert supply.note == "No separate domestic-hot-water heat pump component."

    def test_the_supply_every_twin_simulates_selects_nothing(self) -> None:
        """``together_with_heating_system`` is what every twin does anyway, so it is no ``used``."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "hot_water_system", "options": {"supply": "together_with_heating_system"}}),
            whitelist(),
        )

        supply = next(option for option in applied.measures[0].options if option.name == "supply")
        assert supply.status is ReportStatus.APPROXIMATED
        assert supply.note == SelectsNothing.HOT_WATER_TOGETHER
        assert "modelled as" not in supply.note

    @pytest.mark.parametrize(
        "supplies, status, note",
        [
            ("dhw_only", ReportStatus.APPROXIMATED, SelectsNothing.SOLAR_THERMAL_DHW_ONLY),
            (
                "dhw_and_space_heating",
                ReportStatus.NOT_IMPLEMENTED_YET,
                "The collector is wired to the hot-water storage only; modelled as dhw_only.",
            ),
        ],
    )
    def test_the_collectors_supplies_select_nothing(self, supplies: str, status: ReportStatus, note: str) -> None:
        """Both solar-thermal twins feed the hot-water storage alone (hisim-l56w)."""
        applied = apply(
            anchor_house(),
            measures_of({"id": "solar_thermal_system", "options": {"supplies": supplies}}),
            whitelist(),
        )

        line = next(option for option in applied.measures[0].options if option.name == "supplies")
        assert (line.status, line.note) == (status, note)
        assert applied.measures[0].status is ReportStatus.APPROXIMATED

    def test_an_installation_year_is_recorded_and_carries_its_note(self) -> None:
        """Six measures have it and no HiSim parameter takes it."""
        applied = apply(
            anchor_house(),
            measures_of(
                {"id": "battery_system", "options": {"capacity_in_kwh": 8, "installation_year": 2020}}
            ),
            whitelist(),
        )

        line = next(
            option for option in applied.measures[0].options if option.name == "installation_year"
        )
        assert line.status is ReportStatus.NOT_IMPLEMENTED_YET
        assert line.note == "No HiSim parameter for the age of a new battery."

    def test_an_unlisted_unmapped_item_fails_the_build(self) -> None:
        """Rule 6: the translator's own build fails, and the user's request never does."""
        empty = Whitelist([])

        with pytest.raises(TranslatorError):
            apply(anchor_house(), measures_of(ProbeSet.package("outside_shading")), empty)


@pytest.mark.base
class TestTheRegistryAndTheCatalogueAgree:
    """A measure the catalogue adds and nobody implements is a failing test, not a dropped field."""

    def test_the_registry_names_exactly_the_catalogues_measures(self) -> None:
        """The assertion runs on every ``apply``; this is it on its own."""
        MeasureRegistry.assert_complete()

    def test_the_mockups_own_package_applies(self) -> None:
        """The worked example both sides point at, end to end through the measure layer."""
        request = Request.parse(copy.deepcopy(ContractFiles.request_mockup()))

        applied = apply(request.document["house"], request.measures, whitelist())

        assert [line.id for line in applied.measures] == [
            measure.id for measure in request.measures
        ]
        assert applied.house["heating"]["type_of_system"] == "air_source_heat_pump"
