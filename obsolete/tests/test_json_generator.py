"""The tests of the retired v1 scenario-JSON writer, lifted out of three live files (F-9).

Archived on 2026-09-18 with `obsolete/json_generator.py`, the writer they exercise. Unlike the
other files here they were never a test module of their own: each was a function inside a live
test file, and they are collected here so that what the writer was pinned to survives the move.
Where they came from:

  - `test_the_scenario_json_writes_the_targets_a_setup_made_and_no_others`, with its
    `_resolved_feed_of` helper, from `tests/test_controller_l2_energy_management_system.py` --
    the output half of `convert_component_to_json`, which wrote the dispatch targets a setup
    added by hand and refused a port named by the declarative format's templates.
  - `test_the_description_reaches_scenario_json_without_the_scenario_evaluation`, from
    `tests/test_postprocessing_run_metadata.py` -- that `scenario.json` carried the run's own
    name and description even when the scenario-evaluation option was not selected. That file
    keeps its other test, which pins the pyam `scenario` column.
  - `test_postprocessing_option_write_component_configs_to_json` and
    `test_postprocessing_option_write_configs_for_scenario_evaluation_to_json`, from
    `tests/test_postprocessing_options.py` -- that each option produced the two files.

Nothing here is maintained or runnable in place: the imports, fixtures and helpers of the three
original files are not reproduced, and the two options these name no longer exist.
"""

def _resolved_feed_of(heat_pump: more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib) -> ResolvedDynamicConnection:
    """Builds the resolved feed an energy-system file produces for a steered participant.

    Args:
        heat_pump: The participant the feed measures.

    Returns:
        A feed whose dispatch block names no target input, so its port is named by the
        ``DispatchFor`` template.
    """
    return ResolvedDynamicConnection(
        source_name=heat_pump.component_name,
        source_component=heat_pump,
        source_output=more_advanced_heat_pump_hplib.MoreAdvancedHeatPumpHPLib.ElectricalInputPowerSH,
        source_port=heat_pump.outputs[0],
        target_name="L2EMSElectricityController",
        component_type=lt.ComponentType.HEAT_PUMP_BUILDING,
        flow_tags=(lt.InandOutputType.ELECTRICITY_CONSUMPTION_EMS_CONTROLLED,),
        weight=2,
        channel=controller_l2_energy_management_system.L2GenericEnergyManagementSystem.get_channel(
            controller_l2_energy_management_system.L2GenericEnergyManagementSystem.CONSUMPTION_CONTROLLED_CHANNEL
        ),
        origin="a test's feed",
        dispatch=ResolvedDispatch(
            target_input=None,
            tags=(lt.ComponentType.HEAT_PUMP_BUILDING, lt.InandOutputType.ELECTRICITY_TARGET),
        ),
    )


@pytest.mark.base
def test_the_scenario_json_writes_the_targets_a_setup_made_and_no_others() -> None:
    """Catches the scenario JSON writing a port twice, losing one, or writing a garbled name.

    The file is the legacy path's own: the JSON executor applies the same default connections
    when it rebuilds the component, so a target grown from one must not be written down, while
    every target the setup added by hand must be — under the prefix it was added with. Both
    answers are read off the port's own bookkeeping now, which is also why a port named by the
    declarative format's templates, having no prefix at all, is refused by name instead of
    written as whatever the arithmetic made of it.
    """
    manager = _energy_manager()
    manager.add_component_output(
        source_output_name="LoadingPowerInputForBattery_",
        source_tags=[lt.ComponentType.BATTERY, lt.InandOutputType.ELECTRICITY_TARGET],
        source_weight=6,
        source_load_type=lt.LoadTypes.ELECTRICITY,
        source_unit=lt.Units.WATT,
        output_description="Target electricity for Battery Control. ",
    )
    heat_pump = _heat_pump("HeatPump")
    manager.connect_with_dynamic_connections_list(manager.get_dynamic_default_connections(heat_pump))

    written, _, _ = json_generator.convert_component_to_json(manager.config, manager)

    assert [(out["source_output_name"], out["source_weight"]) for out in written.outputs] == [
        ("LoadingPowerInputForBattery_", 6)
    ]

    declarative_manager = _energy_manager()
    dispatch_output = declarative_manager.add_resolved_dispatch_output(_resolved_feed_of(heat_pump))
    assert dispatch_output.field_name == "DispatchForHeatPump_ElectricalInputPowerSH"

    with pytest.raises(ValueError, match=dispatch_output.field_name):
        json_generator.convert_component_to_json(declarative_manager.config, declarative_manager)


@pytest.mark.base
def test_the_description_reaches_scenario_json_without_the_scenario_evaluation(
    named_case: PreparedPostProcessingCase,
) -> None:
    """Catches ``scenario.json`` depending on a second, independently selected option.

    ``WRITE_COMPONENT_CONFIGS_TO_JSON`` is the option a run selects when it wants the
    configuration of its components and nothing else. It writes ``scenario.json``, and until
    the metadata was read off the transfer object where it is used, the name and the description
    in that file were whatever the scenario-evaluation option had left behind - nothing, when
    that option was not selected.
    """
    directories_to_clean: List[Path] = []
    try:
        run_directory = _post_process(named_case, [PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON])
        directories_to_clean.append(run_directory)

        scenario_json = json.loads((run_directory / "scenario.json").read_text(encoding="utf-8"))
        assert scenario_json["name"] == SCENARIO_NAME
        assert scenario_json["description"] == DESCRIPTION
    finally:
        _clean_up(directories_to_clean)


def test_postprocessing_option_write_component_configs_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON produces scenario and simulation JSON files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_COMPONENT_CONFIGS_TO_JSON,
        expected_files=["scenario.json", "simulation.json"],
    )


def test_postprocessing_option_write_configs_for_scenario_evaluation_to_json(
    postprocessing_option_framework: PostProcessingOptionTestFramework,
) -> None:
    """Test that PostProcessingOptions.WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON produces scenario and simulation JSON files."""
    postprocessing_option_framework.run(
        PostProcessingOptions.WRITE_CONFIGS_FOR_SCENARIO_EVALUATION_TO_JSON,
        expected_files=["scenario.json", "simulation.json"],
    )
