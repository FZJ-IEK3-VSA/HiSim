"""One function per catalogue measure, turning its options into effects on the inventory.

This is the measure layer of ``measures_v2_requirements.md`` §7.2, and its rule is the one that
makes the rest maintainable: **a measure never names a HiSim component or a HiSim config field**
(requirement M6). A measure writes inventory paths, thermal resistances, variant selections and
refusals; the bindings table of step 5 alone knows which component and field an inventory path
reaches. A HiSim rename then changes one binding, not 33 measure functions.

Example of what one function does::

    MeasureRegistry.external_insulation(options, inventory, effects)
    # reads options.material and options.thickness_in_mm, records
    # AddThermalResistance(FACADE, 'polystyrene_eps_rigid_board', 80)

Every function carries the same four things in its docstring: what the measure is physically, what
it writes, which defaults it applies and where they come from, and a closing ``Decisions:`` line
naming the decision ids that shaped it. The translation map of step 4 reads those lines, so they
are data as much as documentation.

Nine measures produce no effect at all and one refuses outright, which is deliberate rather than
unfinished: decision Q15 leaves the three optional ``BuildingConfig`` fields out of the MVP, and a
measure with no model is reported rather than dropped (requirement M8).
"""

from typing import Callable, ClassVar, Dict, Tuple

from hisim.renovisor.base_files import BaseFiles
from hisim.renovisor.catalogue import CatalogueSpellings
from hisim.renovisor.effects import Effects, LawRequest, SizingLaw
from hisim.renovisor.inventory import Inventory
from hisim.renovisor.options import Default, Options
from hisim.renovisor.reasons import ReasonCode
from hisim.renovisor.report import ReportStatus
from hisim.renovisor.vocabulary import DhwSupply, HeatGenerator, SolarThermalSupplies, ThermalElement


class MeasurePaths:
    """The inventory paths the measures write, outside the envelope block.

    Collected here so that a path is spelled once and the test that checks every written path
    against the contract has something to read. The envelope's U-value paths live in
    :class:`hisim.renovisor.envelope.EnvelopePaths`, because the resolver writes those.
    """

    #: Which generator heats the building.
    HEATING_SYSTEM: ClassVar[str] = "energy_system_config.heating_system.system"

    #: Which emitter distributes the heat.
    HEAT_DISTRIBUTION_SYSTEM: ClassVar[str] = "energy_system_config.heating_system.heat_distribution_system"

    #: How domestic hot water is produced. Pending in the contract (decision Q14).
    DHW_SUPPLY: ClassVar[str] = "energy_system_config.heating_system.dhw_supply"

    #: The share of the usable roof the photovoltaic array covers, between 0 and 1. Pending in the
    #: contract (decision Q18); the recorded ``rooftop`` law takes it as its input.
    PV_SHARE: ClassVar[str] = "energy_system_config.photovoltaics.share_of_maximum_pv_potential"

    #: The battery's usable capacity.
    BATTERY_CAPACITY: ClassVar[str] = "energy_system_config.battery_storage.capacity_in_kwh"

    #: The temperature the dwelling is heated to.
    SET_HEATING_TEMPERATURE: ClassVar[str] = "building_config.general.set_heating_temperature_in_celsius"


class MeasureSwitches:
    """The base-file switches the measures ask for, by their recorded names.

    Decision Q18 fixed which switches the recorded grouped files actually offer: the battery is an
    ``electricity_management`` variant, solar thermal and the car are file selections, and PV is an
    always-present component whose sizing law takes a share. Naming the switches here rather than
    inside a function keeps them findable when step 5 checks each one resolves in each file.
    """

    #: The variant choosing how the household's electricity is managed.
    ELECTRICITY_MANAGEMENT_VARIANT: ClassVar[str] = "electricity_management"

    #: The option of that variant which puts a battery behind an energy-management controller.
    ELECTRICITY_MANAGEMENT_WITH_BATTERY: ClassVar[str] = "ems_with_battery"


class MeasureDefaults:
    """The defaults the measures apply, with the source each one carries into the report.

    Only two numbers are stated here. Every insulation thickness is derived instead, from the
    element's current U-value and the regulatory target (decision Q11, in
    :meth:`hisim.renovisor.effects.Effects.target_driven_thickness`), which is why this class is
    short: a number that can be derived is not a default.
    """

    #: The cavity width assumed when a request does not state one. PROVISIONAL: the cavity width is
    #: a survey fact, not a choice, and requirement N7 moves it into the inventory.
    CAVITY_WIDTH: ClassVar[Default] = Default(
        value=100,
        source=(
            "PROVISIONAL — typical Irish cavity 50–100 mm; N7 moves the cavity width into the "
            "inventory, where it is a survey fact rather than a default"
        ),
    )

    #: The only material in the database that can be blown into a wall cavity.
    CAVITY_MATERIAL_ASP_ID: ClassVar[str] = "eps_beads_cavity"

    #: The room temperatures a behaviour measure may ask for. Below 15 °C is not a comfort setting
    #: and above 26 °C is a cooling set-point, so both are validation errors rather than results.
    MINIMUM_ROOM_TEMPERATURE_IN_CELSIUS: ClassVar[int] = 15
    MAXIMUM_ROOM_TEMPERATURE_IN_CELSIUS: ClassVar[int] = 26

    #: A photovoltaic array covers between none and all of the usable roof.
    MINIMUM_ROOF_SHARE_IN_PERCENT: ClassVar[int] = 0
    MAXIMUM_ROOF_SHARE_IN_PERCENT: ClassVar[int] = 100

    #: Percent to share, so the inventory carries the fraction the sizing law takes (requirement M5).
    PERCENT: ClassVar[float] = 100.0

    #: A battery has to cover at least one day to be a battery.
    MINIMUM_DAYS_TO_COVER: ClassVar[int] = 1

    #: The generator a biomass boiler is simulated as, per decision Q14.
    BIOMASS_SUBSTITUTE: ClassVar[HeatGenerator] = HeatGenerator.PELLET_HEATING


class MeasureRegistry:
    """Maps every catalogue measure id to the function that turns its options into effects.

    :attr:`BY_ID` has one entry per catalogue measure and the bijection is a test (check 1 of
    ``measures_v2_requirements.md`` §7.5): a catalogue revision that adds a measure fails the build
    rather than silently ignoring it.

    Each function takes the same three arguments — the validated options, the pre-measure inventory
    (read only, for the facts a measure needs about the building) and the effect accumulator — and
    returns nothing. Effects are the only output; a function that wrote the inventory directly
    would break the composition rule of requirement M2.
    """

    #: The regulatory target row each insulation measure sizes its default thickness against. The
    #: cavity measure is absent on purpose: its thickness is the cavity width, a survey fact, so
    #: the table's ``cavity_fill_wall`` row is not used until requirement N7 lands.
    TARGET_BY_MEASURE: ClassVar[Dict[str, str]] = {
        "EXTERNAL_INSULATION": "wall",
        "INTERNAL_DRY_LINING_INSULATION": "wall",
        "BASEMENT_CEILING_INSULATION": "ground_floor",
        "BASEMENT_EXTERNAL_INSULATION": "ground_floor",
        "SOLID_GROUND_FLOOR_INSULATION": "ground_floor",
        "SUSPENDED_GROUND_FLOOR_INSULATION": "ground_floor",
        "WARM_ROOF_INSULATION": "flat_roof",
        "RAFTER_INSULATION": "pitched_roof_insulated_on_slope",
        "ROLLED_OUT_ATTIC_INSULATION": "pitched_roof_insulated_at_ceiling",
    }

    #: The option every insulation measure names its material with.
    MATERIAL_OPTION: ClassVar[str] = "material"

    #: The option every insulation measure names its layer thickness with.
    THICKNESS_OPTION: ClassVar[str] = "thickness_in_mm"

    # ------------------------------------------------------------------ envelope: walls

    @staticmethod
    def external_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate the external wall from the outside, under a render or a cladding.

        Writes one insulation layer on the facade. The material comes from the catalogue's
        ``material`` option (EPS or XPS, both of which have database rows); the thickness from the
        Experts ``thickness_in_mm`` option, or, when the request omits it, from the target-driven
        rule aiming at the Irish wall target of 0.35 W/m2K.

        Decisions: Q1, Q10, Q11, C3.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.FACADE)

    @staticmethod
    def internal_dry_lining_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate the external wall from the inside, behind a plasterboard lining.

        Writes one insulation layer on the facade, exactly as external insulation does, so that a
        package carrying both composes two layers. Today both materials the catalogue offers —
        thermal laminate drylining board and "mineral wool" — refuse: the first has no database
        row and the second is ambiguous between two, and decisions Q6 and Q7 forbid guessing.

        Decisions: Q1, Q6, Q7, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.FACADE)

    @staticmethod
    def add_internal_dry_lining(options: Options, inventory: Inventory, out: Effects) -> None:
        """Line the inside of an external wall with plasterboard, without adding insulation.

        Refuses. The measure carries no options at all, and no build-up is defined for it: a
        plasterboard lining on battens has a thermal resistance that depends on the batten depth
        and the cavity behind it, which nothing in the request states. The contract PR asks the
        catalogue owners for a defined build-up.

        Decisions: Q1.
        """
        out.refuse(
            ReasonCode.UNDEFINED_MEASURE_BUILDUP,
            options.path,
            "no build-up is defined for a dry lining without insulation, and the measure carries no options",
            options.spec.measure_id,
        )

    @staticmethod
    def cavity_wall_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Fill the void inside a cavity wall by blowing beads into it.

        Writes one insulation layer on the facade with ``eps_beads_cavity``, the only material in
        the database that can be blown into a cavity, so the measure has no material option. The
        thickness is the cavity width, which is a survey fact rather than a choice: until the
        inventory carries it (requirement N7), the Experts ``thickness_in_mm`` option supplies it
        and its default is the PROVISIONAL 100 mm of :class:`MeasureDefaults`.

        Fits only a building whose ``wall_construction`` is ``CAVITY``, checked when the inventory
        states it.

        Decisions: Q1, Q3, Q10, N7.
        """
        thickness = options.integer(
            MeasureRegistry.THICKNESS_OPTION, default=MeasureDefaults.CAVITY_WIDTH, minimum=1
        )
        out.add_thermal_resistance(
            ThermalElement.FACADE,
            MeasureDefaults.CAVITY_MATERIAL_ASP_ID,
            thickness,
            options.spec.measure_id,
        )
        options.note(
            ReportStatus.USED if options.is_supplied(MeasureRegistry.THICKNESS_OPTION) else ReportStatus.DEFAULTED,
            f"{thickness} mm of {MeasureDefaults.CAVITY_MATERIAL_ASP_ID} blown into the wall cavity",
            rule=None if options.is_supplied(MeasureRegistry.THICKNESS_OPTION) else MeasureDefaults.CAVITY_WIDTH.source,
        )

    # ------------------------------------------------------------------ envelope: openings

    @staticmethod
    def window_replacement(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace the windows with double- or triple-glazed units.

        Would set the window U-value, which is a replacement rather than an added layer. It
        refuses today: decision Q13 put the U-value and the price per pane class into the
        materials database, and the database has no window rows yet. The glazing-pane count is
        still validated against the catalogue, so a request naming an impossible glazing is a
        validation error rather than a refusal.

        Decisions: Q1, Q13.
        """
        MeasureRegistry._glazing_refusal(options, out, ThermalElement.WINDOW)

    @staticmethod
    def door_replacement(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace the external door with a solid or a glazed one.

        Refuses on the same grounds as the window replacement: the materials database has no door
        rows. ``glazing_panes`` counts a solid door as zero panes, which the catalogue chose so
        that a rule asking for at least two panes excludes a solid door instead of skipping it.

        Decisions: Q1, Q13.
        """
        MeasureRegistry._glazing_refusal(options, out, ThermalElement.DOOR)

    @staticmethod
    def outside_shading(options: Options, inventory: Inventory, out: Effects) -> None:
        """Fit external shutters, awnings or brise-soleil to the windows.

        Changes nothing. Shading needs an external vertical shading factor on ``BuildingConfig``
        that HiSim does not have; decision Q15 left it out of the MVP, so the package still
        simulates and the report says the measure did nothing.

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_SHADING_MODEL, options.spec.measure_id)

    # ------------------------------------------------------------------ envelope: floors

    @staticmethod
    def basement_ceiling_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate the underside of the ground-floor slab from inside an unheated basement.

        Writes one insulation layer on the floor element, whose target is the 0.45 W/m2K ground
        floor row. Of the two materials the catalogue offers, EPS foam resolves and "mineral wool"
        refuses. Fits only a building over a basement, checked when the inventory states its floor
        construction.

        Decisions: Q1, Q3, Q7, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.FLOOR)

    @staticmethod
    def basement_internal_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate the basement's own walls and floor from the inside.

        Refuses: the measure carries no options and no build-up is defined for it. The contract PR
        asks the catalogue owners for one.

        Decisions: Q1.
        """
        out.refuse(
            ReasonCode.UNDEFINED_MEASURE_BUILDUP,
            options.path,
            "no build-up is defined for internal basement insulation, and the measure carries no options",
            options.spec.measure_id,
        )

    @staticmethod
    def basement_external_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate the basement's walls from the outside, against the excavated ground.

        Writes one insulation layer on the floor element against the 0.45 W/m2K ground floor
        target, on the same terms as the basement ceiling measure. Fits only a building over a
        basement, checked when the inventory states its floor construction.

        Decisions: Q1, Q3, Q7, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.FLOOR)

    @staticmethod
    def solid_ground_floor_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate a solid concrete ground-floor slab, above or below the slab.

        Writes one insulation layer on the floor element against the 0.45 W/m2K ground floor
        target. EPS foam resolves; "liquid insulation" refuses, because the dump has three
        insulating screeds and nothing says which one the catalogue means (decision Q6). Fits only
        a slab-on-ground floor, checked when the inventory states its construction.

        Decisions: Q1, Q3, Q6, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.FLOOR)

    @staticmethod
    def suspended_ground_floor_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate a suspended timber ground floor between its joists, from the crawl space.

        Writes one insulation layer on the floor element against the 0.45 W/m2K ground floor
        target. The Experts ``air_barrier`` option asks whether an air-tightness membrane is fitted
        with the insulation; HiSim has no infiltration model for the MVP (decision Q15), so a
        supplied value is reported as changing nothing and an absent one is not asked for. Fits
        only a suspended timber floor, checked when the inventory states its construction.

        Decisions: Q1, Q3, Q10, Q11, Q15.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.FLOOR)
        if options.is_supplied("air_barrier"):
            options.boolean("air_barrier")
            out.no_effect(ReasonCode.NO_INFILTRATION_MODEL, options.spec.measure_id)

    # ------------------------------------------------------------------ envelope: roof

    @staticmethod
    def warm_roof_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate a flat roof above its deck, so the structure stays on the warm side.

        Writes one insulation layer on the roof element against the 0.25 W/m2K flat roof target.
        Glass wool and wood fibre resolve; PIR and "mineral wool" refuse (decisions Q6, Q7).

        Decisions: Q1, Q6, Q7, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.ROOF)

    @staticmethod
    def rafter_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate a pitched roof between and under its rafters, making the attic a warm space.

        Writes one insulation layer on the roof element against the 0.25 W/m2K pitched-roof target
        for insulation on the slope. Open-cell spray foam, glass wool and wood fibre resolve; PIR
        refuses.

        Decisions: Q1, Q6, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.ROOF)

    @staticmethod
    def rolled_out_attic_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Roll insulation out over the top-floor ceiling, leaving the attic cold.

        Writes one insulation layer on the roof element against the 0.16 W/m2K pitched-roof target
        for insulation at ceiling level, which is the most demanding row of the table because a
        cold loft is the cheapest place to reach it. Glass wool and wood fibre resolve; "mineral
        wool" refuses.

        Decisions: Q1, Q7, Q10, Q11.
        """
        MeasureRegistry._insulation_layer(options, out, ThermalElement.ROOF)

    @staticmethod
    def top_floor_ceiling_insulation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Insulate the ceiling of the top floor as a board rather than a rolled-out quilt.

        Refuses: the measure carries no options and no build-up is defined for it. The contract PR
        asks the catalogue owners for one, or for the measure to be merged with the rolled-out
        attic measure.

        Decisions: Q1.
        """
        out.refuse(
            ReasonCode.UNDEFINED_MEASURE_BUILDUP,
            options.path,
            "no build-up is defined for top floor ceiling insulation, and the measure carries no options",
            options.spec.measure_id,
        )

    # ------------------------------------------------------------------ ventilation

    @staticmethod
    def ventilation_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Fit a mechanical ventilation unit, with or without heat recovery.

        Changes nothing. The value is validated against the catalogue, so an unknown unit is a
        validation error, but HiSim has no air-change or heat-recovery field on ``BuildingConfig``
        for the MVP (decision Q15) and therefore no way to represent the unit.

        Decisions: Q1, Q15, F6.
        """
        options.enum("type_of_system")
        out.no_effect(ReasonCode.NO_VENTILATION_MODEL, options.spec.measure_id)

    @staticmethod
    def shallow_air_tightness_measures(options: Options, inventory: Inventory, out: Effects) -> None:
        """Seal the attic hatch, the service penetrations and the window surrounds.

        Changes nothing: the infiltration rate is not a HiSim input for the MVP (decision Q15).

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_INFILTRATION_MODEL, options.spec.measure_id)

    # ------------------------------------------------------------------ heating

    @staticmethod
    def heating_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace the heat generator.

        The one measure that changes which recorded base file the calculation runs, so it writes
        both a base-file selection and the inventory's ``heating_system.system`` field. Three of
        the eleven generators do not reach a simulation: a hybrid heat pump has no HiSim component
        and is refused as unsupported; HVO has a fuel and a preset but no recorded base file and is
        refused as an unavailable combination; biomass is simulated as pellets and reported as an
        approximation, with a request to the catalogue owners to drop the value (decision Q14).

        Decisions: Q1, Q14, Q18, C3.
        """
        generator = HeatGenerator(options.enum("type_of_system"))
        if generator is HeatGenerator.HYBRID_HEAT_PUMP:
            out.refuse(
                ReasonCode.UNSUPPORTED_SYSTEM,
                options.option_path("type_of_system"),
                "HiSim has no hybrid heat pump component",
                options.spec.measure_id,
            )
            return
        if generator is HeatGenerator.HVO_HEATING:
            out.refuse(
                ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                options.option_path("type_of_system"),
                "no recorded energy-system file runs an HVO boiler",
                options.spec.measure_id,
            )
            return
        simulated: HeatGenerator = generator
        if generator is HeatGenerator.BIOMASS_HEATING:
            simulated = MeasureDefaults.BIOMASS_SUBSTITUTE
            options.note(
                ReportStatus.APPROXIMATED,
                f"{generator.value} simulated as {simulated.value}",
                rule="Q14: biomass is simulated as pellets until the catalogue drops the value",
            )
        out.select_base_file(options.spec.measure_id, generator=simulated)
        out.set_inventory_field(MeasurePaths.HEATING_SYSTEM, options.spec.measure_id, value=simulated.value)

    @staticmethod
    def heating_installation(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace the heat emitters: underfloor circuits, or radiators of one of two kinds.

        Writes the inventory's ``heat_distribution_system`` field. The catalogue's
        ``surface_heating`` covers underfloor, wall and ceiling circuits alike and is HiSim's
        ``FLOORHEATING``; ``conventional_radiator`` is HiSim's ``RADIATOR``. Both re-spellings live
        in the catalogue spelling table and nowhere else.

        Decisions: Q1, C3.
        """
        out.set_inventory_field(
            MeasurePaths.HEAT_DISTRIBUTION_SYSTEM,
            options.spec.measure_id,
            value=options.enum("type_of_system"),
        )

    @staticmethod
    def air_conditioners(options: Options, inventory: Inventory, out: Effects) -> None:
        """Fit air conditioning for summer cooling.

        Refuses. HiSim has an air-conditioner component and a recorded file that uses it, but no
        recorded file combines an air conditioner with a heating system, and decision Q18 forbids
        a RenoVisor-owned base file: the combination arrives through the energy-system golden gate
        or not at all. The requested power is not read, so the refusal does not depend on it.

        Decisions: Q1, Q18, F6.
        """
        out.refuse(
            ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
            options.path,
            "no recorded energy-system file combines an air conditioner with a heating system",
            options.spec.measure_id,
        )

    @staticmethod
    def hot_water_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Change how domestic hot water is produced.

        A hot-water heat pump writes a base-file wish and the inventory's ``dhw_supply`` field; the
        application refuses it on a house whose generator is not a heat pump, because no recorded
        file puts a domestic hot-water heat pump on a boiler house. Direct electric heating is
        refused outright: HiSim has no immersion-heater component (decision Q14).

        Decisions: Q1, Q14, Q18.
        """
        supply = DhwSupply(options.enum("supply"))
        if supply is DhwSupply.DIRECT_ELECTRIC:
            out.refuse(
                ReasonCode.UNSUPPORTED_SYSTEM,
                options.option_path("supply"),
                "HiSim has no direct-electric domestic hot water component",
                options.spec.measure_id,
            )
            return
        out.select_base_file(options.spec.measure_id, dhw_supply=supply)
        out.set_inventory_field(MeasurePaths.DHW_SUPPLY, options.spec.measure_id, value=supply.value)

    @staticmethod
    def temperature_control_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace thermostats with a smart heating control system, or the other way round.

        Changes nothing: the difference between the two is a control schedule, and HiSim runs one
        fixed heating schedule for the MVP. The value is validated all the same.

        Decisions: Q1, Q15.
        """
        options.enum("type_of_system")
        out.no_effect(ReasonCode.NO_CONTROL_SCHEDULE_MODEL, options.spec.measure_id)

    # ------------------------------------------------------------------ appliances and generation

    @staticmethod
    def replace_white_appliances(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace fridge, washing machine and dishwasher with more efficient models.

        Changes nothing: the household electricity profile comes from the occupancy simulation as
        one load, with no separable appliance sub-load to scale (open question Q-N4, answer (a)).

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_APPLIANCE_SUBMODEL, options.spec.measure_id)

    @staticmethod
    def install_new_led_lights(options: Options, inventory: Inventory, out: Effects) -> None:
        """Replace the lighting with LEDs.

        Changes nothing, for the same reason as the appliance measure: the lighting load is not
        separable from the occupancy profile.

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_APPLIANCE_SUBMODEL, options.spec.measure_id)

    @staticmethod
    def photovoltaic_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Install a rooftop photovoltaic array covering a share of the usable roof.

        Writes the share into the inventory as a fraction, not a percentage, so that the
        post-measure inventory is in its own units (requirement M5). The share is the input of the
        recorded files' ``rooftop`` sizing law, which turns it into an installed power using the
        roof geometry, so the measure overrides an existing law rather than adding one. The value
        is a post-measure total, so 0 removes an existing array (decision Q2).

        Decisions: Q1, Q2, Q16, Q18, M5.
        """
        percent = options.integer(
            "size_in_percent_of_roof_area",
            minimum=MeasureDefaults.MINIMUM_ROOF_SHARE_IN_PERCENT,
            maximum=MeasureDefaults.MAXIMUM_ROOF_SHARE_IN_PERCENT,
        )
        out.set_inventory_field(
            MeasurePaths.PV_SHARE, options.spec.measure_id, value=percent / MeasureDefaults.PERCENT
        )
        options.note(
            ReportStatus.USED,
            f"{percent}% of the usable roof, as the share the rooftop sizing law takes",
            rule="Q18: the recorded files size the array from this share and the roof geometry",
        )

    @staticmethod
    def battery_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Install a household battery sized to cover a stated number of days.

        Selects the base file's ``ems_with_battery`` variant and records a sizing law for the
        capacity: days to cover become kilowatt hours from the household load plus the heat pump
        plus the vehicle, which needs the occupancy profile and the heating demand and therefore
        happens in the parametriser (decision Q12). Until then the capacity is a pending law and
        the report says ``approximated``.

        Decisions: Q1, Q12, Q17, Q18, M4.
        """
        days = options.integer("days_to_cover", minimum=MeasureDefaults.MINIMUM_DAYS_TO_COVER)
        out.select_variant(
            MeasureSwitches.ELECTRICITY_MANAGEMENT_VARIANT,
            MeasureSwitches.ELECTRICITY_MANAGEMENT_WITH_BATTERY,
            options.spec.measure_id,
        )
        out.set_inventory_field(
            MeasurePaths.BATTERY_CAPACITY,
            options.spec.measure_id,
            law=LawRequest(law=SizingLaw.BATTERY_FROM_DAYS_TO_COVER, argument=float(days)),
        )
        options.note(
            ReportStatus.APPROXIMATED,
            f"a battery covering {days} day(s) of demand",
            rule=(
                f"{SizingLaw.BATTERY_FROM_DAYS_TO_COVER.value}: household load plus heat pump plus vehicle, "
                "resolved before the simulation"
            ),
        )

    @staticmethod
    def solar_thermal_system(options: Options, inventory: Inventory, out: Effects) -> None:
        """Install solar thermal collectors feeding hot water, space heating, or both.

        Selects a base file rather than writing a field: solar thermal is wired into the recorded
        files that carry it, and the two that do feed domestic hot water. Asking for space heating
        from the collector is refused until a recorded file wires it that way.

        Decisions: Q1, Q17, Q18.
        """
        supplies = SolarThermalSupplies(options.enum("supplies"))
        if supplies not in BaseFiles.SOLAR_THERMAL_SUPPLIES_SUPPORTED:
            out.refuse(
                ReasonCode.NO_BASE_FILE_FOR_COMBINATION,
                options.option_path("supplies"),
                (
                    f"no recorded energy-system file wires a collector for {supplies.value}; the recorded "
                    f"files feed {', '.join(item.value for item in BaseFiles.SOLAR_THERMAL_SUPPLIES_SUPPORTED)}"
                ),
                options.spec.measure_id,
            )
            return
        out.select_base_file(options.spec.measure_id, solar_thermal=supplies)

    @staticmethod
    def electric_vehicle(options: Options, inventory: Inventory, out: Effects) -> None:
        """Own a stated number of electric vehicles, charged at the dwelling.

        Selects a base file: only one recorded file carries a car, and it is the heat-pump one. The
        number is a post-measure total (decision Q2), so 0 removes an existing car and 1 keeps or
        adds one; more than one is refused, because no recorded file charges two.

        Decisions: Q1, Q2, Q18, F4.
        """
        number = options.integer("number", minimum=0)
        if number > BaseFiles.MAXIMUM_CARS:
            out.refuse(
                ReasonCode.TOO_MANY_VEHICLES,
                options.option_path("number"),
                f"no recorded energy-system file charges more than {BaseFiles.MAXIMUM_CARS} vehicle",
                options.spec.measure_id,
            )
            return
        out.select_base_file(options.spec.measure_id, cars=number)

    # ------------------------------------------------------------------ behaviour

    @staticmethod
    def change_room_temperature(options: Options, inventory: Inventory, out: Effects) -> None:
        """Heat the dwelling to a different temperature.

        Writes the inventory's heating set-point. One inventory field, but two HiSim fields once
        the bindings of step 5 map it: the building's set-point and the heat-distribution
        controller's (requirement M11) — which is exactly the kind of thing the measure layer must
        not know, and does not.

        Decisions: Q1, M11.
        """
        temperature = options.integer(
            "new_room_temperature",
            minimum=MeasureDefaults.MINIMUM_ROOM_TEMPERATURE_IN_CELSIUS,
            maximum=MeasureDefaults.MAXIMUM_ROOM_TEMPERATURE_IN_CELSIUS,
        )
        out.set_inventory_field(
            MeasurePaths.SET_HEATING_TEMPERATURE, options.spec.measure_id, value=temperature
        )

    @staticmethod
    def diy_sealing_of_air_leaks(options: Options, inventory: Inventory, out: Effects) -> None:
        """Seal draughts around doors, windows and floorboards by hand.

        Changes nothing: HiSim has no infiltration input for the MVP (decision Q15).

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_INFILTRATION_MODEL, options.spec.measure_id)

    @staticmethod
    def thermocover_for_the_windows(options: Options, inventory: Inventory, out: Effects) -> None:
        """Fit removable insulating covers to the windows at night.

        Changes nothing: the benefit depends on when the occupants put them up, and HiSim has no
        occupant behaviour model to express that.

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_BEHAVIOUR_MODEL, options.spec.measure_id)

    @staticmethod
    def optimize_behaviour_for_self_consumption_of_pv(
        options: Options, inventory: Inventory, out: Effects
    ) -> None:
        """Run the washing machine and the dishwasher when the sun shines.

        Changes nothing: the occupancy profile fixes when loads run, and shifting them would need
        a behaviour model HiSim does not have.

        Decisions: Q15.
        """
        out.no_effect(ReasonCode.NO_BEHAVIOUR_MODEL, options.spec.measure_id)

    # ------------------------------------------------------------------ shared helpers

    @staticmethod
    def _insulation_layer(options: Options, out: Effects, element: ThermalElement) -> None:
        """Record one insulation layer for a measure that has a material and a thickness option.

        Reads the material, refuses when it has no database row, then reads the thickness — from
        the request, or from the target-driven default of decision Q11 aimed at the regulatory
        target row this measure is registered with.

        Args:
            options: The measure's validated options.
            out: The effect accumulator.
            element: Which envelope element the layer sits on.
        """
        measure_id = options.spec.measure_id
        asp_id = options.enum(MeasureRegistry.MATERIAL_OPTION)
        if CatalogueSpellings.is_unresolved(measure_id, MeasureRegistry.MATERIAL_OPTION, asp_id):
            out.refuse(
                ReasonCode.MATERIAL_NOT_IN_DATABASE,
                options.option_path(MeasureRegistry.MATERIAL_OPTION),
                f"'{asp_id}' has no row in the insulation-material database, so its conductivity is unknown",
                measure_id,
            )
            return
        target_id = MeasureRegistry.TARGET_BY_MEASURE.get(measure_id)
        if target_id is None:
            out.refuse(
                ReasonCode.UNDEFINED_MEASURE_BUILDUP,
                options.path,
                f"no regulatory target row is registered for {measure_id}, so a thickness cannot be derived",
                measure_id,
            )
            return
        try:
            default = out.target_driven_thickness(element, asp_id, target_id)
        except KeyError as error:
            out.refuse(
                ReasonCode.UNDEFINED_MEASURE_BUILDUP,
                options.path,
                f"the target-driven thickness rule cannot be applied: {error}",
                measure_id,
            )
            return
        thickness = options.integer(MeasureRegistry.THICKNESS_OPTION, default=default, minimum=1)
        out.add_thermal_resistance(element, asp_id, thickness, measure_id)
        supplied = options.is_supplied(MeasureRegistry.THICKNESS_OPTION)
        options.note(
            ReportStatus.USED if supplied else ReportStatus.DEFAULTED,
            f"{thickness} mm of {asp_id} on the {element.value.lower()}",
            rule=None if supplied else default.source,
        )

    @staticmethod
    def _glazing_refusal(options: Options, out: Effects, element: ThermalElement) -> None:
        """Validate a glazing-pane count and refuse, because the database has no glazing rows.

        Args:
            options: The measure's validated options.
            out: The effect accumulator.
            element: ``WINDOW`` or ``DOOR``, named in the refusal so the caller knows which.
        """
        panes = options.integer("glazing_panes")
        out.refuse(
            ReasonCode.MATERIAL_NOT_IN_DATABASE,
            options.option_path("glazing_panes"),
            (
                f"the materials database has no {element.value.lower()} row for {panes} pane(s), so its "
                "U-value is unknown"
            ),
            options.spec.measure_id,
        )

    BY_ID: ClassVar[Dict[str, Callable[[Options, Inventory, Effects], None]]] = {
        "EXTERNAL_INSULATION": external_insulation,
        "INTERNAL_DRY_LINING_INSULATION": internal_dry_lining_insulation,
        "ADD_INTERNAL_DRY_LINING": add_internal_dry_lining,
        "CAVITY_WALL_INSULATION": cavity_wall_insulation,
        "WINDOW_REPLACEMENT": window_replacement,
        "OUTSIDE_SHADING": outside_shading,
        "DOOR_REPLACEMENT": door_replacement,
        "BASEMENT_CEILING_INSULATION": basement_ceiling_insulation,
        "BASEMENT_INTERNAL_INSULATION": basement_internal_insulation,
        "BASEMENT_EXTERNAL_INSULATION": basement_external_insulation,
        "SOLID_GROUND_FLOOR_INSULATION": solid_ground_floor_insulation,
        "SUSPENDED_GROUND_FLOOR_INSULATION": suspended_ground_floor_insulation,
        "WARM_ROOF_INSULATION": warm_roof_insulation,
        "RAFTER_INSULATION": rafter_insulation,
        "ROLLED_OUT_ATTIC_INSULATION": rolled_out_attic_insulation,
        "TOP_FLOOR_CEILING_INSULATION": top_floor_ceiling_insulation,
        "VENTILATION_SYSTEM": ventilation_system,
        "SHALLOW_AIR_TIGHTNESS_MEASURES": shallow_air_tightness_measures,
        "HEATING_SYSTEM": heating_system,
        "HEATING_INSTALLATION": heating_installation,
        "AIR_CONDITIONERS": air_conditioners,
        "HOT_WATER_SYSTEM": hot_water_system,
        "TEMPERATURE_CONTROL_SYSTEM": temperature_control_system,
        "REPLACE_WHITE_APPLIANCES": replace_white_appliances,
        "INSTALL_NEW_LED_LIGHTS": install_new_led_lights,
        "PHOTOVOLTAIC_SYSTEM": photovoltaic_system,
        "BATTERY_SYSTEM": battery_system,
        "SOLAR_THERMAL_SYSTEM": solar_thermal_system,
        "ELECTRIC_VEHICLE": electric_vehicle,
        "CHANGE_ROOM_TEMPERATURE": change_room_temperature,
        "DIY_SEALING_OF_AIR_LEAKS": diy_sealing_of_air_leaks,
        "THERMOCOVER_FOR_THE_WINDOWS": thermocover_for_the_windows,
        "OPTIMIZE_BEHAVIOUR_FOR_SELF_CONSUMPTION_OF_PV": optimize_behaviour_for_self_consumption_of_pv,
    }

    #: How a measure function's docstring introduces the decision ids that shaped it. Step 3 §0
    #: requires the line; the translation map of step 4 reads it (decision V2).
    DECISIONS_PREFIX: ClassVar[str] = "Decisions:"

    @classmethod
    def measure_ids(cls) -> Tuple[str, ...]:
        """Return every measure id the registry handles, sorted."""
        return tuple(sorted(cls.BY_ID))

    @classmethod
    def decisions_for(cls, measure_id: str) -> Tuple[str, ...]:
        """Return the decision ids the measure's docstring names, in the order it names them.

        Every registry function's docstring ends with a line such as ``Decisions: Q1, Q10, C3``,
        naming the entries of ``roadmap/renovisor/challenges.md`` §9 that shaped it. The
        translation map turns those into its decision filter (decision V2), so the line is read
        here rather than parsed again wherever it is wanted::

            MeasureRegistry.decisions_for("EXTERNAL_INSULATION")   # ('Q1', 'Q10', 'Q11', 'C3')

        Args:
            measure_id: The catalogue measure id.

        Returns:
            The ids with their punctuation stripped, or an empty tuple when the docstring carries
            no such line.

        Raises:
            KeyError: When no function is registered for *measure_id*.
        """
        docstring = cls.BY_ID[measure_id].__doc__ or ""
        lines = [line.strip() for line in docstring.splitlines() if line.strip()]
        for line in reversed(lines):
            if not line.startswith(cls.DECISIONS_PREFIX):
                continue
            body = line[len(cls.DECISIONS_PREFIX):].strip().rstrip(".")
            return tuple(part.strip() for part in body.split(",") if part.strip())
        return ()

    @classmethod
    def function_for(cls, measure_id: str) -> Callable[[Options, Inventory, Effects], None]:
        """Return the function that applies one measure.

        Args:
            measure_id: The catalogue measure id.

        Returns:
            The registry function.

        Raises:
            KeyError: When no function is registered; the bijection test makes that impossible for
                a catalogue measure, so it only happens for a made-up id.
        """
        return cls.BY_ID[measure_id]
