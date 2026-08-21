"""Layer 2 of the building cleanup harness: a one-day Building simulation snapshot.

Layer 1 (``tests/test_building_characterization.py``) pins what ``BuildingInformation``
derives from the TABULA catalogue; it says nothing about the ~2000-line ``Building``
component that turns those parameters into temperatures and heat flows. This module
closes that gap for the behavior-identical cleanup of that component (the package split
and the removal of its positional-unpacking and ordering hazards): it drives the component
through one 15-minute-resolution day (96 timesteps) and snapshots
**every output vector**, timestep by timestep, into a committed golden.

Vectors, not aggregates, on purpose: a daily mean hides a sign flip, a shifted timestep or a
swapped pair of outputs, and those are exactly the mistakes a positional-unpacking-heavy
refactor makes.

How the run is driven:

* No simulator, no weather component and no weather files. The component is fed synthetic,
  deterministic, pure-Python input vectors (a sinusoidal outdoor temperature, a fixed
  half-wave irradiance day curve with matching sun angles, constant occupancy and device
  heat gains, a stepped thermal-power delivery whose last block deliberately overheats the
  building, a stepped CHP contribution and a windowed set-temperature modifier) through
  hand-built ``SingleTimeStepValues``, wired to the exact input channels the component
  declares. The profiles are stored in the golden alongside the outputs, so a golden that
  goes red because somebody edited an input profile shows that in its own diff instead of
  looking like a physics change.
* The per-timestep protocol mirrors ``Simulator.process_one_timestep``: ``i_save_state``,
  then ``i_restore_state`` and ``i_simulate``. At one mid-run timestep the restore and
  re-simulate step is performed a second time and the outputs are required to be bit-for-bit
  identical, which is the property the simulator's convergence loop relies on and which any
  state-handling refactor must preserve.
* Two variants are snapshotted: the default German single-family home, and a scaled variant
  (a larger absolute conditioned floor area, plus window opening enabled and lowered set
  temperatures) that reaches the scaling branches and the window-opening branch the default
  configuration never takes. Between them the day covers heating, free float, overheating
  with a cooling demand, and an open-window indoor-air override.

The solar-gain disk cache is deliberately neutralized: ``utils.get_cache_file`` is patched
to report "no cache" and to point at a temporary directory. Otherwise the first run would
write a cache keyed by config and simulation parameters, and a later run -- of this test or
of any other test sharing that key -- would read solar gains through a different code path,
which would make this snapshot depend on execution order.

Regeneration: run with ``HISIM_REGENERATE_BUILDING_GOLDENS=1``, e.g.::

    HISIM_REGENERATE_BUILDING_GOLDENS=1 python -m pytest tests/test_building_one_day_snapshot.py

which rewrites ``tests/goldens/building_one_day.json``. Without the variable a missing
golden is a hard error, never a silent create; the golden's diff is part of the merge
request and, during the cleanup, may only be justified by a metadata change.
"""

# clean

import dataclasses
import math
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import pytest

from hisim import component as cp
from hisim import utils
from hisim.config import ComponentID
from hisim.components.building import Building, BuildingConfig
from hisim.simulationparameters import SimulationParameters
from tests import building_golden_support as golden_support
from tests import functions_for_testing as fft


class SyntheticDayProfiles:
    """Deterministic one-day input profiles for the ``Building`` component.

    Every profile is a closed-form function of the timestep index -- no randomness, no
    weather file, no other component -- so the snapshot is reproducible on any machine and
    the whole day is generated in microseconds. The shapes are chosen to be physically
    plausible rather than realistic: a cold night and a mild afternoon, a single solar
    window with matching sun angles, and heat inputs that step between distinct levels so
    that a timestep shift in any output vector is visible as a shifted edge.
    """

    #: 96 quarter-hourly steps make one day at the resolution the snapshot uses.
    TIMESTEPS: int = 96
    SECONDS_PER_TIMESTEP: int = 15 * 60
    SIMULATION_YEAR: int = 2021

    #: Outdoor temperature: cosine with its minimum at 05:00 and maximum at 17:00.
    OUTDOOR_TEMPERATURE_MEAN_IN_CELSIUS: float = 4.0
    OUTDOOR_TEMPERATURE_AMPLITUDE_IN_CELSIUS: float = 7.0
    OUTDOOR_TEMPERATURE_MINIMUM_TIMESTEP: int = 20

    #: Solar window: 08:00 (inclusive) to 18:00 (exclusive), i.e. 40 quarter hours.
    SOLAR_WINDOW_FIRST_TIMESTEP: int = 32
    SOLAR_WINDOW_TIMESTEP_COUNT: int = 40
    DIRECT_NORMAL_IRRADIANCE_PEAK_IN_WATT_PER_M2: float = 620.0
    DIFFUSE_HORIZONTAL_IRRADIANCE_PEAK_IN_WATT_PER_M2: float = 145.0
    GLOBAL_HORIZONTAL_IRRADIANCE_PEAK_IN_WATT_PER_M2: float = 480.0
    #: Extraterrestrial normal irradiance is essentially constant over a day.
    DIRECT_NORMAL_IRRADIANCE_EXTRA_IN_WATT_PER_M2: float = 1361.0
    #: Sun azimuth sweeps from east-south-east to west-north-west across the solar window.
    SUN_AZIMUTH_AT_SUNRISE_IN_DEGREES: float = 100.0
    SUN_AZIMUTH_SWEEP_IN_DEGREES: float = 160.0
    SUN_AZIMUTH_AT_NIGHT_IN_DEGREES: float = 180.0
    #: Apparent zenith dips from just below the horizon to 38 degrees at solar noon.
    APPARENT_ZENITH_AT_HORIZON_IN_DEGREES: float = 88.0
    APPARENT_ZENITH_NOON_DEPRESSION_IN_DEGREES: float = 50.0
    APPARENT_ZENITH_AT_NIGHT_IN_DEGREES: float = 95.0

    #: Internal gains are held constant so their contribution is easy to read off.
    OCCUPANCY_HEAT_GAIN_IN_WATT: float = 180.0
    DEVICE_HEAT_GAIN_IN_WATT: float = 95.0

    #: Delivered thermal power steps between four levels, one per six hours: free float,
    #: ordinary heating, free float again, and a deliberately oversized final block. The
    #: last block exists to drive the building into overheating, which is the only way a
    #: winter day reaches the cooling side of the theoretical thermal demand and (for the
    #: variant that enables it) the window-opening branch.
    THERMAL_POWER_DELIVERED_STEPS_IN_WATT: Tuple[float, ...] = (0.0, 6000.0, 0.0, 20000.0)
    #: A second heat source, switched on at midday, proves both inputs are summed.
    THERMAL_POWER_CHP_IN_WATT: float = 600.0
    THERMAL_POWER_CHP_FIRST_TIMESTEP: int = 48
    #: The set-heating-temperature modifier (the EMS surplus signal) is raised for a window.
    BUILDING_TEMPERATURE_MODIFIER_IN_CELSIUS: float = 2.0
    BUILDING_TEMPERATURE_MODIFIER_FIRST_TIMESTEP: int = 40
    BUILDING_TEMPERATURE_MODIFIER_LAST_TIMESTEP: int = 55

    @classmethod
    def solar_window_shape(cls, timestep: int) -> float:
        """Return the half-sine solar shape factor in ``[0, 1]`` for one timestep.

        Zero outside the solar window, so night steps carry no irradiance at all and the
        component's "no irradiance at all" short circuit is exercised as well.
        """
        last_timestep_of_window = cls.SOLAR_WINDOW_FIRST_TIMESTEP + cls.SOLAR_WINDOW_TIMESTEP_COUNT
        if not cls.SOLAR_WINDOW_FIRST_TIMESTEP <= timestep < last_timestep_of_window:
            return 0.0
        position_in_window = (timestep - cls.SOLAR_WINDOW_FIRST_TIMESTEP + 0.5) / cls.SOLAR_WINDOW_TIMESTEP_COUNT
        return math.sin(math.pi * position_in_window)

    @classmethod
    def is_daylight(cls, timestep: int) -> bool:
        """Whether the timestep lies inside the synthetic solar window."""
        return cls.solar_window_shape(timestep) > 0.0

    @classmethod
    def outdoor_temperature_in_celsius(cls, timestep: int) -> float:
        """Return the sinusoidal outdoor air temperature for one timestep."""
        phase = 2.0 * math.pi * (timestep - cls.OUTDOOR_TEMPERATURE_MINIMUM_TIMESTEP) / cls.TIMESTEPS
        return cls.OUTDOOR_TEMPERATURE_MEAN_IN_CELSIUS - cls.OUTDOOR_TEMPERATURE_AMPLITUDE_IN_CELSIUS * math.cos(phase)

    @classmethod
    def sun_azimuth_in_degrees(cls, timestep: int) -> float:
        """Return the sun azimuth, sweeping across the solar window and fixed at night."""
        if not cls.is_daylight(timestep):
            return cls.SUN_AZIMUTH_AT_NIGHT_IN_DEGREES
        position_in_window = (timestep - cls.SOLAR_WINDOW_FIRST_TIMESTEP + 0.5) / cls.SOLAR_WINDOW_TIMESTEP_COUNT
        return cls.SUN_AZIMUTH_AT_SUNRISE_IN_DEGREES + cls.SUN_AZIMUTH_SWEEP_IN_DEGREES * position_in_window

    @classmethod
    def apparent_zenith_in_degrees(cls, timestep: int) -> float:
        """Return the apparent solar zenith angle, below the horizon outside the window."""
        if not cls.is_daylight(timestep):
            return cls.APPARENT_ZENITH_AT_NIGHT_IN_DEGREES
        noon_depression = cls.APPARENT_ZENITH_NOON_DEPRESSION_IN_DEGREES * cls.solar_window_shape(timestep)
        return cls.APPARENT_ZENITH_AT_HORIZON_IN_DEGREES - noon_depression

    @classmethod
    def sun_altitude_in_degrees(cls, timestep: int) -> float:
        """Return the sun altitude, the complement of the apparent zenith angle."""
        return 90.0 - cls.apparent_zenith_in_degrees(timestep)

    @classmethod
    def thermal_power_delivered_in_watt(cls, timestep: int) -> float:
        """Return the stepped thermal power delivered by the (absent) heating device.

        Four six-hour blocks with distinct levels, including two zero blocks, so both the
        heating and the free-float behaviour of the thermal model appear in one day.
        """
        block_length = cls.TIMESTEPS // len(cls.THERMAL_POWER_DELIVERED_STEPS_IN_WATT)
        return cls.THERMAL_POWER_DELIVERED_STEPS_IN_WATT[timestep // block_length]

    @classmethod
    def thermal_power_chp_in_watt(cls, timestep: int) -> float:
        """Return the stepped CHP heat contribution, switched on from midday."""
        return cls.THERMAL_POWER_CHP_IN_WATT if timestep >= cls.THERMAL_POWER_CHP_FIRST_TIMESTEP else 0.0

    @classmethod
    def building_temperature_modifier_in_celsius(cls, timestep: int) -> float:
        """Return the set-heating-temperature modifier, raised for one afternoon window."""
        if (
            cls.BUILDING_TEMPERATURE_MODIFIER_FIRST_TIMESTEP
            <= timestep
            <= cls.BUILDING_TEMPERATURE_MODIFIER_LAST_TIMESTEP
        ):
            return cls.BUILDING_TEMPERATURE_MODIFIER_IN_CELSIUS
        return 0.0

    @classmethod
    def input_vectors(cls) -> Dict[str, List[float]]:
        """Return one full-day vector per ``Building`` input channel.

        The keys are the component's input-channel attribute names, which is how the wiring
        below connects a profile to the channel it belongs to. Listing them explicitly
        (rather than discovering channels by introspection) is deliberate: the mapping from
        physical quantity to channel must be stated by the harness, not guessed from
        declaration order.
        """
        timesteps = range(cls.TIMESTEPS)
        return {
            "temperature_outside_channel": [cls.outdoor_temperature_in_celsius(step) for step in timesteps],
            "altitude_channel": [cls.sun_altitude_in_degrees(step) for step in timesteps],
            "azimuth_channel": [cls.sun_azimuth_in_degrees(step) for step in timesteps],
            "apparent_zenith_channel": [cls.apparent_zenith_in_degrees(step) for step in timesteps],
            "direct_normal_irradiance_channel": [
                cls.DIRECT_NORMAL_IRRADIANCE_PEAK_IN_WATT_PER_M2 * cls.solar_window_shape(step) for step in timesteps
            ],
            "direct_normal_irradiance_extra_channel": [
                cls.DIRECT_NORMAL_IRRADIANCE_EXTRA_IN_WATT_PER_M2 if cls.is_daylight(step) else 0.0
                for step in timesteps
            ],
            "direct_horizontal_irradiance_channel": [
                cls.DIFFUSE_HORIZONTAL_IRRADIANCE_PEAK_IN_WATT_PER_M2 * cls.solar_window_shape(step)
                for step in timesteps
            ],
            "global_horizontal_irradiance_channel": [
                cls.GLOBAL_HORIZONTAL_IRRADIANCE_PEAK_IN_WATT_PER_M2 * cls.solar_window_shape(step)
                for step in timesteps
            ],
            "occupancy_heat_gain_channel": [cls.OCCUPANCY_HEAT_GAIN_IN_WATT for _ in timesteps],
            "device_heat_gain_channel": [cls.DEVICE_HEAT_GAIN_IN_WATT for _ in timesteps],
            "thermal_power_delivered_channel": [cls.thermal_power_delivered_in_watt(step) for step in timesteps],
            "thermal_power_chp_channel": [cls.thermal_power_chp_in_watt(step) for step in timesteps],
            "building_temperature_modifier_channel": [
                cls.building_temperature_modifier_in_celsius(step) for step in timesteps
            ],
        }


@dataclasses.dataclass
class OneDaySimulationResult:
    """The recorded outcome of one synthetic one-day ``Building`` run.

    Bundles the three things the tests need to check -- the output vectors, whether the
    mid-run restore-and-re-simulate round reproduced its outputs exactly, and a description
    of any mismatch -- so the run itself can happen once in a module-scoped fixture while
    each property is asserted by its own named test.
    """

    #: Output field name to the 96 encoded values the run produced.
    output_vectors: Dict[str, List[Any]]
    #: Whether re-running the mid-run timestep after a state restore reproduced the outputs.
    save_restore_reproduced: bool
    #: Human-readable description of the restore round, empty when it reproduced exactly.
    save_restore_report: str
    #: Conditioned floor area the configuration ended up with, as a scaling witness.
    scaled_conditioned_floor_area_in_m2: float


class OneDaySnapshot:
    """Construction, execution and golden layout of the one-day ``Building`` snapshot.

    The class owns the whole layer-2 contract: which configurations are snapshotted, how the
    component is wired to synthetic inputs, the per-timestep protocol (including the
    convergence-path check), and how all of it is laid out in the committed golden.
    """

    #: Bare file name of the committed golden.
    GOLDEN_FILE_NAME: str = "building_one_day.json"
    #: Golden sections.
    METADATA_SECTION: str = "metadata"
    INPUT_SECTION: str = "synthetic_inputs"
    OUTPUT_SECTION_PREFIX: str = "outputs_"
    #: Variant names, used both as golden key suffixes and as test parameters.
    DEFAULT_VARIANT_NAME: str = "default_config"
    SCALED_VARIANT_NAME: str = "scaled_config"
    #: The timestep at which the restore-and-re-simulate round is performed.
    SAVE_RESTORE_TIMESTEP: int = 48
    #: Conditioned floor area of the scaled variant, roughly twice the default.
    SCALED_ABSOLUTE_CONDITIONED_FLOOR_AREA_IN_M2: float = 250.0
    #: Set temperatures of the scaled variant, chosen so the synthetic day actually crosses
    #: the cooling setpoint of the (larger, therefore cooler) building.
    SCALED_VARIANT_INITIAL_INTERNAL_TEMPERATURE_IN_CELSIUS: float = 19.0
    SCALED_VARIANT_SET_COOLING_TEMPERATURE_IN_CELSIUS: float = 20.0
    #: Name given to the synthetic source outputs feeding the component's inputs.
    SYNTHETIC_SOURCE_NAME: str = "SyntheticInputs"

    @classmethod
    def variant_names(cls) -> List[str]:
        """Return the snapshotted configuration variants, in golden order."""
        return [cls.DEFAULT_VARIANT_NAME, cls.SCALED_VARIANT_NAME]

    @classmethod
    def output_section_name(cls, variant_name: str) -> str:
        """Return the golden section holding the output vectors of one variant."""
        return f"{cls.OUTPUT_SECTION_PREFIX}{variant_name}"

    @classmethod
    def config_for(cls, variant_name: str) -> BuildingConfig:
        """Build the ``BuildingConfig`` of one snapshot variant.

        The default variant is the factory default single-family home, which is what every
        household system setup uses. The scaled variant enlarges the absolute conditioned
        floor area -- reaching the scaling branches of the area and conductance derivation.
        It additionally enables window opening and lowers the initial and cooling set
        temperatures, because the branches guarded by them (the indoor-air override on an
        open window, and the cooling side of the theoretical thermal demand) are unreachable
        at the default setpoints on any synthetic winter day: the default building simply
        never gets to 25 degrees. Lowering the setpoints buys both branches without adding a
        third simulation run.
        """
        config: BuildingConfig = BuildingConfig.presets.german_single_family_home
        if variant_name == cls.SCALED_VARIANT_NAME:
            config = dataclasses.replace(
                config,
                absolute_conditioned_floor_area_in_m2=cls.SCALED_ABSOLUTE_CONDITIONED_FLOOR_AREA_IN_M2,
                enable_opening_windows=True,
                initial_internal_temperature_in_celsius=cls.SCALED_VARIANT_INITIAL_INTERNAL_TEMPERATURE_IN_CELSIUS,
                set_cooling_temperature_in_celsius=cls.SCALED_VARIANT_SET_COOLING_TEMPERATURE_IN_CELSIUS,
            )
        return config

    @classmethod
    def simulation_parameters(cls) -> SimulationParameters:
        """Return the one-day, 15-minute simulation parameters used by both variants."""
        return SimulationParameters.one_day_only(
            year=SyntheticDayProfiles.SIMULATION_YEAR,
            seconds_per_timestep=SyntheticDayProfiles.SECONDS_PER_TIMESTEP,
        )

    @classmethod
    def wire_synthetic_inputs(cls, building: Building) -> Dict[str, cp.ComponentOutput]:
        """Create one synthetic source output per input channel and connect them.

        Each channel gets its own source output carrying the channel's declared load type
        and unit, so the values are written into the very slot the component reads from.
        Optional channels have to be connected explicitly: the component skips an input
        whose ``source_output`` is ``None``, so leaving one unconnected would silently drop
        a whole profile from the run.
        """
        source_outputs: Dict[str, cp.ComponentOutput] = {}
        for channel_name in SyntheticDayProfiles.input_vectors():
            channel = getattr(building, channel_name)
            source_output = cp.ComponentOutput(
                cls.SYNTHETIC_SOURCE_NAME,
                channel.field_name,
                channel.loadtype,
                channel.unit,
                component_id=ComponentID(name=cls.SYNTHETIC_SOURCE_NAME),
            )
            channel.source_output = source_output
            source_outputs[channel_name] = source_output
        return source_outputs

    @classmethod
    def run(cls, variant_name: str) -> OneDaySimulationResult:
        """Run one variant through the synthetic day and record its outputs.

        The per-timestep protocol mirrors ``Simulator.process_one_timestep``: the state is
        saved once per timestep and restored before each ``i_simulate`` call, which is what
        makes a second call within the same timestep a faithful convergence iteration. At
        :py:attr:`SAVE_RESTORE_TIMESTEP` that second iteration is performed on purpose and
        its outputs are compared bit-for-bit with the first one.
        """
        building = Building(
            config=cls.config_for(variant_name),
            my_simulation_parameters=cls.simulation_parameters(),
        )
        source_outputs = cls.wire_synthetic_inputs(building)
        input_vectors = SyntheticDayProfiles.input_vectors()

        fft.add_global_index_of_components([*source_outputs.values(), building])
        stsv = cp.SingleTimeStepValues(fft.get_number_of_outputs([*source_outputs.values(), building]))
        building.i_prepare_simulation()

        output_vectors: Dict[str, List[Any]] = {output.field_name: [] for output in building.outputs}
        save_restore_report = ""
        for timestep in range(SyntheticDayProfiles.TIMESTEPS):
            for channel_name, source_output in source_outputs.items():
                stsv.values[source_output.global_index] = input_vectors[channel_name][timestep]
            building.i_save_state()
            building.i_restore_state()
            building.i_simulate(timestep, stsv, False)
            first_iteration_values = cls.read_outputs(building, stsv)
            if timestep == cls.SAVE_RESTORE_TIMESTEP:
                building.i_restore_state()
                building.i_simulate(timestep, stsv, False)
                save_restore_report = cls.compare_iterations(
                    first_iteration_values, cls.read_outputs(building, stsv)
                )
            for field_name, value in cls.read_outputs(building, stsv).items():
                output_vectors[field_name].append(golden_support.encode_value(value))

        return OneDaySimulationResult(
            output_vectors=output_vectors,
            save_restore_reproduced=not save_restore_report,
            save_restore_report=save_restore_report,
            scaled_conditioned_floor_area_in_m2=float(
                building.my_building_information.scaled_conditioned_floor_area_in_m2
            ),
        )

    @classmethod
    def read_outputs(cls, building: Building, stsv: cp.SingleTimeStepValues) -> Dict[str, float]:
        """Read every declared output of the component out of the value array."""
        return {output.field_name: stsv.values[output.global_index] for output in building.outputs}

    @classmethod
    def compare_iterations(
        cls, first_iteration_values: Dict[str, float], second_iteration_values: Dict[str, float]
    ) -> str:
        """Describe how a repeated convergence iteration differed, if at all.

        Returns an empty string when the restored re-simulation reproduced every output
        exactly, and otherwise a per-output report. Exact equality is the right bar here:
        ``i_restore_state`` is supposed to put the component back into precisely the state
        it was in, so any difference means the state is not fully captured.
        """
        difference_lines = [
            f"  {field_name}: first = {first_iteration_values[field_name]!r}, "
            f"second = {second_iteration_values[field_name]!r}"
            for field_name in sorted(first_iteration_values)
            if first_iteration_values[field_name] != second_iteration_values[field_name]
        ]
        if not difference_lines:
            return ""
        return "\n".join(difference_lines)

    @classmethod
    def build_payload(cls, results: Dict[str, OneDaySimulationResult]) -> Dict[str, Any]:
        """Lay the recorded runs out as the golden payload.

        Besides one section of output vectors per variant, the payload carries the synthetic
        input vectors (so an edited input profile is visible as such in the diff) and a small
        metadata block naming the run parameters and the conditioned floor area each variant
        ended up with -- the witness that the scaled variant really is the scaled one.
        """
        payload: Dict[str, Any] = {
            cls.METADATA_SECTION: {
                "timesteps": SyntheticDayProfiles.TIMESTEPS,
                "seconds_per_timestep": SyntheticDayProfiles.SECONDS_PER_TIMESTEP,
                "year": SyntheticDayProfiles.SIMULATION_YEAR,
                "save_restore_timestep": cls.SAVE_RESTORE_TIMESTEP,
                **{
                    f"scaled_conditioned_floor_area_in_m2_{variant_name}": golden_support.encode_float(
                        result.scaled_conditioned_floor_area_in_m2
                    )
                    for variant_name, result in sorted(results.items())
                },
            },
            cls.INPUT_SECTION: {
                channel_name: [golden_support.encode_value(value) for value in vector]
                for channel_name, vector in SyntheticDayProfiles.input_vectors().items()
            },
        }
        for variant_name, result in results.items():
            payload[cls.output_section_name(variant_name)] = dict(result.output_vectors)
        return payload

    #: Maximum distance, in units of the larger operand's ULP, at which two float outputs
    #: still count as equal. The one-day outputs pass through transcendental functions
    #: (``math.cos`` in the window model, numpy trigonometry inside pvlib) whose last bit is
    #: legitimately platform-dependent — different libm builds round them differently — so a
    #: golden generated on one machine can differ from another machine's run by a few ULPs on
    #: the trig-derived columns (observed 2026-08-21: glibc 2.43 vs the ubuntu-latest CI
    #: runner, 1-ULP shifts on 6–8 daylight timesteps of the solar-gain chain). The variance
    #: also exists *within* the CI fleet: the pinned Docker image runs on heterogeneous host
    #: CPUs, numpy dispatches different SIMD kernels per CPU family, and the same commit then
    #: passes on one runner and fails on the next (observed 2026-08-22: a deterministic 5-ULP
    #: shift on 2 daylight timesteps of ``HeatFluxToThermalMass``, appearing and vanishing
    #: across reruns on main). Sixteen ULPs gives headroom over the largest observed shift for
    #: runner CPU families not yet sampled while staying ~three orders of magnitude below
    #: anything a real model change produces; the pure-arithmetic layer-1 golden stays
    #: bit-exact and remains the referee for summation-order-level changes.
    PLATFORM_ULP_TOLERANCE: ClassVar[int] = 16

    @classmethod
    def values_match(cls, expected: Any, actual: Any) -> bool:
        """Compare one golden value against one current value, ULP-tolerantly for floats.

        Exact equality short-circuits (also covering the non-float outputs); float pairs are
        additionally accepted when they lie within :attr:`PLATFORM_ULP_TOLERANCE` ULPs of
        each other, which is the cross-platform libm variance documented on that constant.
        Anything else — differing types, integers that moved, floats beyond the ULP band —
        is a genuine difference.
        """
        if expected == actual:
            return True
        if isinstance(expected, float) and isinstance(actual, float):
            ulp_of_larger = math.ulp(max(abs(expected), abs(actual)))
            return abs(expected - actual) <= cls.PLATFORM_ULP_TOLERANCE * ulp_of_larger
        return False

    @classmethod
    def describe_vector_differences(cls, expected_vectors: Dict[str, Any], actual_vectors: Dict[str, Any]) -> str:
        """Describe how two sets of output vectors differ, compactly enough to read.

        A drifting thermal model moves whole vectors, so reporting every differing value
        would bury the signal. Instead each differing output is reported once with the number
        of differing timesteps and the first difference, which together identify both the
        affected output and when it starts to deviate. Values are compared through
        :meth:`values_match`, i.e. exactly except for the few-ULP platform band on floats.
        """
        report_lines: List[str] = []
        for field_name in sorted(set(expected_vectors) | set(actual_vectors)):
            if field_name not in expected_vectors:
                report_lines.append(f"  {field_name}: output not present in the golden")
                continue
            if field_name not in actual_vectors:
                report_lines.append(f"  {field_name}: output present in the golden but no longer produced")
                continue
            expected_vector = expected_vectors[field_name]
            actual_vector = actual_vectors[field_name]
            if expected_vector == actual_vector:
                continue
            if len(expected_vector) != len(actual_vector):
                report_lines.append(
                    f"  {field_name}: vector length changed, golden = {len(expected_vector)}, "
                    f"current = {len(actual_vector)}"
                )
                continue
            differing_timesteps = [
                timestep
                for timestep, (expected, actual) in enumerate(zip(expected_vector, actual_vector))
                if not cls.values_match(expected, actual)
            ]
            if not differing_timesteps:
                continue
            first_timestep = differing_timesteps[0]
            report_lines.append(
                f"  {field_name}: {len(differing_timesteps)} of {len(expected_vector)} timesteps differ, "
                f"first at timestep {first_timestep}: golden = {expected_vector[first_timestep]!r}, "
                f"current = {actual_vector[first_timestep]!r}"
            )
        return "\n".join(report_lines)


@pytest.fixture(name="neutralized_solar_gain_cache", scope="module")
def fixture_neutralized_solar_gain_cache(tmp_path_factory):
    """Make the component's solar-gain disk cache inert for this module.

    ``Building`` asks ``utils.get_cache_file`` whether a cached solar-gain series for its
    config and simulation parameters already exists, reads it if so, and writes it at the
    end of a full run otherwise. Left alone, that turns the snapshot into an
    order-dependent test: the second run in a working tree would take the reading path, and
    a cache written by some other test with the same key would supply foreign values. The
    patch reports "no cache" and redirects the write into a temporary directory, so every
    run computes its gains and no file lands in the working tree.
    """
    cache_directory = tmp_path_factory.mktemp("building_one_day_solar_cache")

    def cache_file_without_reuse(
        component_key: str,
        parameter_class: Any,
        my_simulation_parameters: SimulationParameters,
        cache_dir_path: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Report that no cache exists and point the write at a temporary directory."""
        del parameter_class, my_simulation_parameters, cache_dir_path
        return False, str(cache_directory / f"{component_key}_solar_gains.csv")

    patcher = pytest.MonkeyPatch()
    patcher.setattr(utils, "get_cache_file", cache_file_without_reuse)
    yield
    patcher.undo()


@pytest.fixture(name="one_day_simulation_results", scope="module")
def fixture_one_day_simulation_results(neutralized_solar_gain_cache) -> Dict[str, OneDaySimulationResult]:
    """Run both configuration variants through the synthetic day exactly once.

    Both runs take a fraction of a second, but they are shared across the comparison tests
    anyway so that a failure report can distinguish "the outputs drifted" from "the state
    handling broke" without simulating twice for each.
    """
    del neutralized_solar_gain_cache
    return {variant_name: OneDaySnapshot.run(variant_name) for variant_name in OneDaySnapshot.variant_names()}


@pytest.fixture(name="one_day_golden", scope="module")
def fixture_one_day_golden(one_day_simulation_results) -> Dict[str, Any]:
    """Provide the golden payload, regenerating the whole file first if asked to.

    Module-scoped, so the rewrite happens before the per-test stray-file guard in
    ``tests/conftest.py`` takes its snapshot, and so a run narrowed with ``-k`` still writes
    a complete golden rather than a partial one.
    """
    if golden_support.GoldenPolicy.regeneration_requested():
        golden_support.write_golden(
            OneDaySnapshot.GOLDEN_FILE_NAME, OneDaySnapshot.build_payload(one_day_simulation_results)
        )
    return golden_support.load_golden(OneDaySnapshot.GOLDEN_FILE_NAME)


@pytest.mark.buildingtest
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_one_day_output_vectors_match_golden(
    variant_name: str,
    one_day_simulation_results: Dict[str, OneDaySimulationResult],
    one_day_golden: Dict[str, Any],
) -> None:
    """Verify every output vector of one configuration variant against the golden.

    All 96 values of all declared outputs are compared, so a sign flip, a one-timestep
    shift or a pair of swapped outputs fails here even though the daily totals would be
    unchanged. Floats are compared up to the few-ULP platform band of
    :attr:`OneDaySnapshot.PLATFORM_ULP_TOLERANCE` — the trig-derived columns legitimately
    differ in the last bit between libm builds — while everything meaningful sits many
    orders of magnitude above it.
    """
    expected_vectors = one_day_golden[OneDaySnapshot.output_section_name(variant_name)]
    actual_vectors = one_day_simulation_results[variant_name].output_vectors
    differences = OneDaySnapshot.describe_vector_differences(expected_vectors, actual_vectors)
    assert not differences, (
        f"The one-day output vectors of the '{variant_name}' variant no longer match the golden:\n"
        f"{differences}\n\n"
        f"This harness pins current behavior of the Building component. If the change is intended, "
        f"regenerate with {golden_support.GoldenPolicy.REGENERATION_ENVIRONMENT_VARIABLE}=1 and justify "
        f"the golden's diff in the commit message."
    )


@pytest.mark.buildingtest
@pytest.mark.parametrize("variant_name", OneDaySnapshot.variant_names())
def test_state_restore_reproduces_the_timestep(
    variant_name: str,
    one_day_simulation_results: Dict[str, OneDaySimulationResult],
) -> None:
    """Verify that restoring the state and re-simulating a timestep reproduces its outputs.

    This is the property the simulator's convergence loop depends on: within one timestep it
    restores every component and calls ``i_simulate`` again until the values stop moving, so a
    component whose second iteration differs from its first would either never converge or
    converge to something the harness cannot reproduce.
    """
    result = one_day_simulation_results[variant_name]
    assert result.save_restore_reproduced, (
        f"Re-simulating timestep {OneDaySnapshot.SAVE_RESTORE_TIMESTEP} of the '{variant_name}' variant after "
        f"i_restore_state did not reproduce the outputs of the first iteration:\n{result.save_restore_report}"
    )


@pytest.mark.buildingtest
def test_one_day_synthetic_inputs_match_golden(
    one_day_golden: Dict[str, Any],
) -> None:
    """Verify the synthetic input profiles are the ones the golden was recorded with.

    The outputs can only be a reference if the inputs are too. Pinning the input vectors
    separately means an accidental edit to a profile constant is reported as an input change
    instead of masquerading as a behavior change in the building model.
    """
    expected_vectors = one_day_golden[OneDaySnapshot.INPUT_SECTION]
    actual_vectors = {
        channel_name: [golden_support.encode_value(value) for value in vector]
        for channel_name, vector in SyntheticDayProfiles.input_vectors().items()
    }
    differences = OneDaySnapshot.describe_vector_differences(expected_vectors, actual_vectors)
    assert not differences, f"The synthetic input profiles no longer match the golden:\n{differences}"


@pytest.mark.buildingtest
def test_one_day_metadata_matches_golden(
    one_day_simulation_results: Dict[str, OneDaySimulationResult],
    one_day_golden: Dict[str, Any],
) -> None:
    """Verify the run parameters and the per-variant conditioned floor areas.

    The metadata block is what distinguishes "the same run produced other numbers" from "a
    different run was recorded": if the resolution, the day length or the floor area a
    variant scales to ever changes, the output vectors would change too, and this test names
    the reason.
    """
    expected_metadata = one_day_golden[OneDaySnapshot.METADATA_SECTION]
    actual_metadata = OneDaySnapshot.build_payload(one_day_simulation_results)[OneDaySnapshot.METADATA_SECTION]
    assert actual_metadata == expected_metadata, (
        f"The one-day run metadata no longer matches the golden.\n"
        f"  golden:  {expected_metadata}\n"
        f"  current: {actual_metadata}"
    )
