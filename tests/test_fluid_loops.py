"""The loop walk (``hisim/fluid_loops.py``): the build finds every fluid loop from the wiring and refuses one that is no loop.

Stage 1b of ``roadmap/fluid_loops_spec.md`` (§3.3, rules 0 to 3): every member of a loop tags four channels, its flow
and temperature in and out; the walk follows each ``flow_out`` to the ``flow_in`` that reads it until it is back at
the start, names the loop after its one pump owner and refuses, naming the loop's members and the channel concerned,
a loop that does not close, a port set wired by half, a loop tagged by half, a tagged flow read through or fed by an
untagged channel, members that disagree on the carrier or the fluid, a flow read by two port sets, a temperature wire
that does not follow the flow, and a loop with no or two pump owners.

The first tests wire small stand-in components by hand: a generator that supplies its loop and a tank that receives
it, each with the four channels of one loop. The last test (D22) builds every recorded energy system and checks that
each of its loops is found and gets the id written here, which is what catches a forgotten tag in a shipped system.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from hisim import component as cp
from hisim import hydronics
from hisim import loadtypes as lt
from hisim.calculation_scope import CalculationScope
from hisim.config import ComponentID
from hisim.energy_port import FluidLoop, FluidLoopChannel, LoopFluid, LoopQuantity
from hisim.energy_system.executor import build_energy_system
from hisim.fluid_loops import FluidLoopWalk
from hisim.simulationparameters import SimulationParameters

pytestmark = pytest.mark.base


class StandIn(cp.PhysicsComponent):
    """A physics component built without a configuration, to tag and wire channels on."""


class Member:

    """Builds stand-in members of a loop: each tags the four channels of one loop under names of its own.

    A member's channels are named ``FlowIn``, ``TemperatureIn`` (inputs), ``FlowOut`` and ``TemperatureOut``
    (outputs), with the loop's name in front when it has more than one loop.
    """

    #: The loop's heat, unless a test names another.
    CARRIER = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT

    #: The quantities in the order their channels are declared, each with its channel's name and load type.
    CHANNELS: Tuple[Tuple[LoopQuantity, str, lt.LoadTypes], ...] = (
        (LoopQuantity.FLOW_IN, "FlowIn", lt.LoadTypes.WARM_WATER),
        (LoopQuantity.TEMPERATURE_IN, "TemperatureIn", lt.LoadTypes.TEMPERATURE),
        (LoopQuantity.FLOW_OUT, "FlowOut", lt.LoadTypes.WARM_WATER),
        (LoopQuantity.TEMPERATURE_OUT, "TemperatureOut", lt.LoadTypes.TEMPERATURE),
    )

    @staticmethod
    def component(name: str) -> Any:
        """A component with its name and no channels, as ``add_input`` and ``add_output`` need, without a configuration."""
        component: Any = object.__new__(StandIn)
        component.component_name = name
        component.inputs = []
        component.outputs = []
        component.config = SimpleNamespace(component_id=ComponentID(name))
        component.my_display_config = None
        component.enable_logging = False
        return component

    @classmethod
    def tag(
        cls,
        component: Any,
        loop: FluidLoop,
        *,
        prefix: str = "",
        quantities: Tuple[LoopQuantity, ...] = tuple(LoopQuantity),
    ) -> Any:
        """Declare the channels of ``quantities`` on ``component``, each tagged with ``loop``; return the component."""
        for quantity, name, load_type in cls.CHANNELS:
            if quantity not in quantities:
                continue
            tag = FluidLoopChannel(loop, quantity)
            if quantity.is_input:
                component.add_input(component.component_name, prefix + name, load_type, quantity.unit, False, fluid_loop=tag)
            else:
                component.add_output(
                    component.component_name, prefix + name, load_type, quantity.unit, output_description=name, fluid_loop=tag
                )
        return component

    @classmethod
    def of(
        cls,
        name: str,
        *,
        loop_name: str,
        supplies: bool,
        pumps: bool,
        carrier: Optional[lt.EnergyBalanceCarrier] = None,
        fluid: LoopFluid = LoopFluid.WATER,
    ) -> Any:
        """A stand-in member of one loop, all four channels tagged and none wired."""
        loop = FluidLoop(
            loop_name, carrier=carrier or cls.CARRIER, fluid=fluid, supplies=supplies, may_cool=False, pumps=pumps
        )
        return cls.tag(cls.component(name), loop)

    @staticmethod
    def wire(target: Any, input_name: str, source: Any, output_name: str) -> None:
        """Connect ``target.input_name`` to ``source.output_name``."""
        target.connect_input(input_name, source.component_name, output_name)

    @classmethod
    def link(cls, upstream: Any, downstream: Any) -> None:
        """Send ``upstream``'s fluid to ``downstream``: its flow and its temperature out to the other's flow and temperature in."""
        cls.wire(downstream, "FlowIn", upstream, "FlowOut")
        cls.wire(downstream, "TemperatureIn", upstream, "TemperatureOut")

    @classmethod
    def generator_and_tank(cls, *, generator_pumps: bool = True, tank_pumps: bool = False) -> Tuple[Any, Any]:
        """A boiler that supplies and pumps its loop to a tank that passes it back, wired both ways."""
        boiler = cls.of("Boiler", loop_name="space_heating", supplies=True, pumps=generator_pumps)
        tank = cls.of("Tank", loop_name="generator", supplies=False, pumps=tank_pumps)
        cls.link(boiler, tank)
        cls.link(tank, boiler)
        return boiler, tank


def refusal(components: List[Any]) -> str:
    """The message of the walk's refusal of ``components``; fails the test if the walk accepts them."""
    with pytest.raises(hydronics.FluidLoopWiringError) as raised:
        FluidLoopWalk.find_loops(components)
    return str(raised.value)


def test_a_loop_of_a_generator_and_a_tank_is_found_and_named_after_its_pump_owner() -> None:
    """The flow out of each member is read by the next member's flow in until the start: one loop, the pump owner's id.

    Fails if the walk misses the loop, orders its members against the flow or names it after another member.
    """
    boiler, tank = Member.generator_and_tank()
    loops = FluidLoopWalk.find_loops([tank, boiler])
    assert len(loops) == 1
    loop = loops[0]
    assert loop.loop_id == "Boiler.space_heating"
    assert [port_set.label for port_set in loop.port_sets] == ["Boiler.space_heating", "Tank.generator"]
    assert loop.pump_owner.component_name == "Boiler"
    # the tank pumping and the boiler passing the flow on names the loop after the tank
    boiler, tank = Member.generator_and_tank(generator_pumps=False, tank_pumps=True)
    loops = FluidLoopWalk.find_loops([boiler, tank])
    assert [(loop.loop_id, [port_set.label for port_set in loop.port_sets]) for loop in loops] == [(
        "Tank.generator",
        ["Tank.generator", "Boiler.space_heating"],
    )]


def test_a_loop_of_three_members_starts_at_its_pump_owner() -> None:
    """A buffer in series with its generator: generator -> distribution -> buffer -> generator, the distribution pumps."""
    generator = Member.of("Generator", loop_name="space_heating", supplies=True, pumps=False)
    distribution = Member.of("Distribution", loop_name="distribution", supplies=False, pumps=True)
    buffer = Member.of("Buffer", loop_name="series", supplies=False, pumps=False)
    Member.link(generator, distribution)
    Member.link(distribution, buffer)
    Member.link(buffer, generator)
    loops = FluidLoopWalk.find_loops([generator, buffer, distribution])
    assert [(loop.loop_id, [port_set.component_name for port_set in loop.port_sets]) for loop in loops] == [
        ("Distribution.distribution", ["Distribution", "Buffer", "Generator"])
    ]


def test_a_port_set_with_no_channel_connected_is_idle() -> None:
    """A boiler's hot-water side in a house without a tank wires none of its four channels: allowed, no loop."""
    assert not FluidLoopWalk.find_loops([Member.of("Boiler", loop_name="dhw", supplies=True, pumps=True)])


@pytest.mark.parametrize(("generator_pumps", "tank_pumps", "count"), [(True, True, 2), (False, False, 0)])
def test_a_loop_with_no_or_two_pump_owners_is_refused(generator_pumps: bool, tank_pumps: bool, count: int) -> None:
    """Rule 3: the pre-#1117 ``gas_boiler_household`` had two pumps on one loop, a loop of two pass-throughs has none.

    The message names the loop's members and the pumping members' flow outputs, or every member's when none pumps.
    """
    boiler, tank = Member.generator_and_tank(generator_pumps=generator_pumps, tank_pumps=tank_pumps)
    message = refusal([boiler, tank])
    assert f"the loop (Boiler.space_heating, Tank.generator) has {count} pumping port sets" in message
    assert "exactly one member of a loop pumps" in message
    if count == 2:
        assert "Boiler.space_heating (Boiler.FlowOut) and Tank.generator (Tank.FlowOut)" in message
    else:
        assert "(flows Boiler.FlowOut, Tank.FlowOut)" in message


def test_a_loop_that_does_not_close_is_refused() -> None:
    """The tank's flow goes back to nobody: the boiler's flow in and the tank's flow out each name the broken loop."""
    boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
    tank = Member.of("Tank", loop_name="generator", supplies=False, pumps=False)
    Member.link(boiler, tank)
    Member.wire(boiler, "TemperatureIn", tank, "TemperatureOut")
    message = refusal([boiler, tank])
    assert re.search(
        r"the loop through \(Boiler.space_heating, Tank.generator\) does not close: Boiler.FlowIn \(flow_in\) of its "
        r"member Boiler.space_heating is connected to no other member",
        message,
    )
    assert "Tank.FlowOut (flow_out) of its member Tank.generator is connected to no other member" in message


def test_a_port_set_wired_by_half_is_refused() -> None:
    """Both flows wired, the return temperature not: the boiler would book heat on a return it never reads."""
    boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
    tank = Member.of("Tank", loop_name="generator", supplies=False, pumps=False)
    Member.link(boiler, tank)
    Member.wire(boiler, "FlowIn", tank, "FlowOut")
    message = refusal([boiler, tank])
    assert "Boiler.space_heating is wired by half: its flows are connected and Boiler.TemperatureIn (temperature_in) is not" in message
    assert "Tank.generator is wired by half: its flows are connected and Tank.TemperatureOut (temperature_out) is not" in message


def test_a_loop_tagged_by_half_is_refused() -> None:
    """Rule 0: a tank that tags its loop's flow in and its temperatures but no flow out names what it lacks."""
    loop = FluidLoop(
        "generator", carrier=Member.CARRIER, fluid=LoopFluid.WATER, supplies=False, may_cool=False, pumps=False
    )
    tank = Member.tag(
        Member.component("Tank"),
        loop,
        quantities=(LoopQuantity.FLOW_IN, LoopQuantity.TEMPERATURE_IN, LoopQuantity.TEMPERATURE_OUT),
    )
    message = refusal([tank])
    assert (
        "Tank tags the loop 'generator' by half: it tags FlowIn (flow_in), TemperatureIn (temperature_in), "
        "TemperatureOut (temperature_out) and lacks flow_out" in message
    )


def test_a_tagged_flow_read_or_fed_through_an_untagged_channel_is_refused() -> None:
    """The far end of a tagged flow must be tagged: an untagged reader of the flow out, an untagged source of the flow in."""
    boiler, tank = Member.generator_and_tank()
    meter = Member.component("Meter")
    meter.add_input("Meter", "Flow", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, False)
    meter.add_output("Meter", "FlowCopy", lt.LoadTypes.WARM_WATER, lt.Units.KG_PER_SEC, output_description="copy")
    Member.wire(meter, "Flow", boiler, "FlowOut")
    message = refusal([boiler, tank, meter])
    assert "the loop Boiler.space_heating leaves Boiler.FlowOut for Meter.Flow, which carries no flow_in tag" in message
    boiler, tank = Member.generator_and_tank()
    Member.wire(tank, "FlowIn", meter, "FlowCopy")
    message = refusal([boiler, tank, meter])
    assert "the loop Tank.generator arrives at Tank.FlowIn from Meter.FlowCopy, which carries no flow_out tag" in message


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"carrier": lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT},
            "is space_heating_heat at Boiler.space_heating and domestic_hot_water_heat at Tank.generator: every member "
            "of a loop states the same carrier",
        ),
        (
            {"fluid": LoopFluid.BRINE},
            "is water at Boiler.space_heating and brine at Tank.generator: every member of a loop states the same fluid",
        ),
    ],
)
def test_members_that_state_different_carriers_or_fluids_are_refused(changes: Dict[str, Any], expected: str) -> None:
    """A space-heating end wired to a hot-water end, a water end to a brine end: neither the wiring tells, so both say."""
    boiler = Member.of("Boiler", loop_name="space_heating", supplies=True, pumps=True)
    tank = Member.of("Tank", loop_name="generator", supplies=False, pumps=False, **changes)
    Member.link(boiler, tank)
    Member.link(tank, boiler)
    assert f"the loop (Boiler.space_heating, Tank.generator) {expected}" in refusal([boiler, tank])


def test_a_flow_read_by_two_port_sets_is_refused() -> None:
    """One flow delivers its heat once: a second tank reading the boiler's flow is a split without a valve."""
    boiler, tank = Member.generator_and_tank()
    twin = Member.of("Twin", loop_name="generator", supplies=False, pumps=False)
    Member.link(boiler, twin)
    message = refusal([boiler, tank, twin])
    assert (
        "the flow Boiler.FlowOut of the loop Boiler.space_heating is read by 2 port sets, Tank.generator, "
        "Twin.generator: one flow delivers its heat once" in message
    )


def test_a_temperature_wire_that_does_not_follow_the_flow_is_refused() -> None:
    """A tank whose flow comes from one boiler and whose supply temperature from another books the wrong heat.

    Two boilers charge two tanks with the supply temperatures crossed, so every channel is connected and only the
    temperature wires do not follow the flow.
    """
    boiler, tank = Member.generator_and_tank()
    other = Member.of("Other", loop_name="space_heating", supplies=True, pumps=True)
    other_tank = Member.of("OtherTank", loop_name="generator", supplies=False, pumps=False)
    Member.link(other, other_tank)
    Member.link(other_tank, other)
    Member.wire(tank, "TemperatureIn", other, "TemperatureOut")
    Member.wire(other_tank, "TemperatureIn", boiler, "TemperatureOut")
    message = refusal([boiler, tank, other, other_tank])
    assert (
        "the temperature wire of the loop (Boiler.space_heating, Tank.generator) does not follow its flow: "
        "Tank.TemperatureIn reads Other.TemperatureOut, but the flow arrives from Boiler.space_heating, so it reads "
        "Boiler.TemperatureOut" in message
    )


class RecordedSystems:

    """Every recorded energy system and the ids of the fluid loops its wiring forms (D22 of the fluid loops spec)."""

    #: The repository root.
    ROOT = Path(__file__).resolve().parent.parent

    @staticmethod
    def boiler_house(boiler: str, *, hot_water: bool = True) -> Tuple[str, ...]:
        """The loop ids of a house whose boiler ``boiler`` charges a buffer and, with ``hot_water``, a tank.

        The boiler pumps its loops to the buffer and the tank, the heat distribution system its loop to the buffer.
        """
        hot = (f"{boiler}.dhw",) if hot_water else ()
        return (f"{boiler}.space_heating", *hot, "HeatDistributionSystem.distribution")

    @classmethod
    def expected(cls) -> Dict[str, Tuple[str, ...]]:
        """Every recorded file's stem with its loop ids; a file without a fluid loop has none."""
        heat_pump = ("MoreAdvancedHeatPumpHPLib.space_heating", "MoreAdvancedHeatPumpHPLib.dhw", "HeatDistributionSystem.distribution")
        expected: Dict[str, Tuple[str, ...]] = {
            stem: ()
            for stem in (
                "air_conditioned_house",
                "basic_household",
                "default_connections",
                "dynamic_components",
                "electrolyzer_with_renewables",
                "simple_air_conditioner_household_building_sizer",
                "simple_air_conditioner_household_building_sizer.grouped",
                "simple_system_setup_one",
                "simple_system_setup_two",
            )
        }
        expected.update(
            {
                "automatic_default_connections": heat_pump,
                "basic_household_only_heating": cls.boiler_house("CondensingGasBoiler", hot_water=False),
                "gas_boiler_household": ("hds.distribution",),
                "energy_system_mockup_minimal": ("hds.distribution",),
                "uc1.realized": ("hds.distribution",),
                "household_district_heating_building_sizer.composed": (
                    "HeatDistributionSystem.distribution",
                    "heating-Generator.dhw",
                ),
                "household_electric_heating_building_sizer.composed": ("heating-Heater.dhw",),
            }
        )
        for kind in ("", ".grouped"):
            expected[f"household_district_heating_building_sizer{kind}"] = (
                "HeatDistributionSystem.distribution",
                "DistrictHeating.dhw",
            )
            expected[f"household_electric_heating_building_sizer{kind}"] = ("ElectricHeating.dhw",)
            for stem in ("household_gas_solar_thermal", "household_gas_solar_thermal_building_sizer"):
                expected[stem + kind] = (*cls.boiler_house("CondensingGasBoiler"), "SolarThermalSystem.solar_dhw")
            for stem in ("household_heatpump_building_sizer", "household_heatpump_car_building_sizer"):
                expected[stem + kind] = heat_pump
            expected[f"household_heatpump_solar_thermal_building_sizer{kind}"] = (*heat_pump, "SolarThermalSystem.solar_dhw")
            for stem, boiler in (
                ("household_gas_building_sizer", "CondensingGasBoiler"),
                ("household_hydrogen_boiler_building_sizer", "CondensingHydrogenBoiler"),
                ("household_oil_building_sizer", "ConventionalOilBoiler"),
                ("household_pellets_building_sizer", "ConventionalPelletBoiler"),
                ("household_wood_chips_building_sizer", "ConventionalWoodChipBoiler"),
            ):
                expected[stem + kind] = cls.boiler_house(boiler)
        for stem, generator in (
            ("household_gas_building_sizer", "Boiler"),
            ("household_hydrogen_boiler_building_sizer", "Boiler"),
            ("household_oil_building_sizer", "Boiler"),
            ("household_pellets_building_sizer", "Boiler"),
            ("household_wood_chips_building_sizer", "Boiler"),
            ("household_heatpump_building_sizer", "HeatPump"),
        ):
            expected[f"{stem}.composed"] = (
                f"heating-{generator}.space_heating",
                f"heating-{generator}.dhw",
                "HeatDistributionSystem.distribution",
            )
        return expected

    @classmethod
    def paths(cls) -> Dict[str, Path]:
        """Every recorded file by its stem: the twins and composed files, the minimal mockup and the realized record."""
        paths = {
            path.name[: -len(".energy_system.yaml")]: path for path in (cls.ROOT / "energy_systems").glob("*.energy_system.yaml")
        }
        paths["energy_system_mockup_minimal"] = cls.ROOT / "roadmap" / "declarative_energy_systems" / "energy_system_mockup_minimal.yaml"
        paths["uc1.realized"] = cls.ROOT / "tests" / "energy_systems" / "uc1.realized.energy_system.yaml"
        return paths


def test_every_recorded_file_is_listed_with_its_loops() -> None:
    """A new recorded file needs its loop ids written below, so that a forgotten tag in it cannot pass unseen."""
    assert sorted(RecordedSystems.paths()) == sorted(RecordedSystems.expected())


@pytest.mark.parametrize("stem", sorted(RecordedSystems.expected()))
def test_every_recorded_file_builds_and_each_loop_gets_its_id(stem: str, tmp_path: Path) -> None:
    """D22: every recorded system builds, and the walk finds exactly the loops listed, each named after its pump owner.

    Catches a forgotten tag in a shipped system (its loop is refused as untagged at one end, or is missing), a pumping
    flag on the wrong member (refused or named after the wrong member) and a fourth wire a default connection fails
    to make. The occupancy reads its predefined profile, as the build alone needs no new load profile.
    """
    path = RecordedSystems.paths()[stem]
    text = path.read_text(encoding="utf-8")
    if "USE_LOCAL_LPG" in text:
        path = tmp_path / path.name
        path.write_text(text.replace("USE_LOCAL_LPG", "USE_PREDEFINED_PROFILE"), encoding="utf-8")
    parameters = SimulationParameters.one_day_only(2021, 900)
    parameters.result_directory = str(tmp_path / "results")
    with CalculationScope.open(label=str(path), run_directory=parameters.result_directory):
        built = build_energy_system(path, parameters, rerun=stem.endswith(".realized"))
        loops = FluidLoopWalk.find_loops([wrapped.my_component for wrapped in built.simulator.wrapped_components])
    assert sorted(loop.loop_id for loop in loops) == sorted(RecordedSystems.expected()[stem])
    for loop in loops:
        assert len(loop.port_sets) == 2 and loop.pump_owner.loop.pumps
