#!/usr/bin/env python3
"""The declared translation between the two paths' aggregator port names.

TEMPORARY — this table belongs to the P3 migration parity rig (requirements R11) and is deleted
with it in phase P6 (R11.8 amended and AC-P3.20 deferred to P6, 2026-08-31).

An aggregator does not declare a port per participant; it grows one per feed, and the two paths
derive that port's name differently. The imperative add-API names an aggregator input after the
participant, the output being measured and its insertion order — ``Input_<source>_<field>_<n>`` —
and a dispatch output after whatever the setup passed as a prefix plus the source weight it
steers on. The declarative path derives both from the frozen templates of the format:
``<field>From<source>`` for an input, ``DispatchTo<source>_<input>`` for a dispatch output another
component reads, and ``DispatchFor<source>_<output>`` for one whose signal is only recorded. The
two names denote the same wire, so comparing them literally would report a difference where there
is none — and dropping the comparison would hide a real one (C-P3.2).

Every pair below is a **claim someone made**: that this legacy name and this declarative name are
two spellings of one wire. The claims were derived empirically rather than guessed. Each setup was
built through both paths and the two wire sets were diffed; a legacy wire and a declarative wire
were paired when they agreed on the ``(source component, source output, target component)`` triple,
which is the part neither path renames, and the pair was then written down here. Anything this
table does not list must still match literally, so a name difference nobody declared fails the
comparison instead of being absorbed by it.

The table is keyed by ``(aggregator component name, legacy port name)`` and is the union over the
whole fleet. A key can be that specific because the legacy input name carries an index: the same
participant feeding the same aggregator in two setups produces two different legacy names when it
was inserted at two different positions, and both appear below with the same declarative name. The
dispatch outputs no longer work that way — since F-1 their legacy names carry a weight instead of
a counter — so one row per channel and weight covers the whole fleet.
"""

from __future__ import annotations

from typing import ClassVar, Dict, Mapping, Tuple

from hisim.energy_system.parity import PortRenaming


class DeclaredPortRenamings:
    """Every legacy aggregator port name the rig claims is a declarative port under another name.

    Split into the inputs an aggregator grows per feed and the outputs it grows per dispatch,
    because the two carry different consequences. An input name never reaches a result file, so it
    only affects the wiring comparison; a dispatch output *is* a result column, so its translation
    is what lets the second comparison cover every column rather than the ones whose names happen
    to agree.

    Both tables are nested by aggregator, so that a reviewer reads one aggregator's claims
    together — the unit a person can actually check (DQ4).
    """

    #: Aggregator inputs, per aggregator: the port the legacy add-API grew for a feed, and the
    #: port the declarative resolver grows for the same feed. The legacy spelling carries the
    #: participant, the output being measured and the insertion index; the declarative one carries
    #: the output and the participant and no index at all, which is why one declarative name
    #: answers several legacy ones.
    AGGREGATOR_INPUTS: ClassVar[Mapping[str, Mapping[str, str]]] = {
        # The house electricity meter. What feeds it differs from setup to setup — in the
        # EMS-controlled sizers it sees only the controller's residual, in the plain households it
        # sees every producer and consumer directly — which is why the same participant appears
        # here under more than one index.
        "ElectricityMeter": {
            # PV production, inserted first in basic_household and default_connections.
            "Input_PVSystem_ElectricityOutput_0": "ElectricityOutputFromPVSystem",
            # The same PV production, inserted second in automatic_default_connections.
            "Input_PVSystem_ElectricityOutput_1": "ElectricityOutputFromPVSystem",
            # Household demand from the occupancy, inserted first where the meter is fed by the
            # component's own declared defaults.
            "Input_UTSPConnector_ElectricalPowerConsumption_0": "ElectricalPowerConsumptionFromUTSPConnector",
            # The same demand, inserted second where the setup wires PV before it.
            "Input_UTSPConnector_ElectricalPowerConsumption_1": "ElectricalPowerConsumptionFromUTSPConnector",
            # The simple heat pump's electricity draw in basic_household and default_connections.
            "Input_HeatPump_ElectricityOutput_2": "ElectricityOutputFromHeatPump",
            # The hplib heat pump's two draws, space heating and domestic hot water.
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerSH_2": (
                "ElectricalInputPowerSHFromMoreAdvancedHeatPumpHPLib"
            ),
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerDHW_3": (
                "ElectricalInputPowerDHWFromMoreAdvancedHeatPumpHPLib"
            ),
            # The solar-thermal collector's pump draw, which household_gas_solar_thermal measures
            # at the meter directly because it has no energy-management controller.
            "Input_SolarThermalSystem_ElectricityConsumptionOutput_1": (
                "ElectricityConsumptionOutputFromSolarThermalSystem"
            ),
            # The air conditioner's draw, which air_conditioned_house likewise measures at the
            # meter directly, inserted after the PV production and the household demand.
            "Input_AirConditioner_ElectricalPowerConsumption_2": (
                "ElectricalPowerConsumptionFromAirConditioner"
            ),
            # In every EMS-controlled sizer the meter has exactly one participant — the
            # controller's residual — so the index is always zero.
            "Input_L2EMSElectricityController_TotalElectricityToOrFromGrid_0": (
                "TotalElectricityToOrFromGridFromL2EMSElectricityController"
            ),
        },
        # The energy-management controller, which is an aggregator on its consumption and
        # production side as well as a dispatcher on its target side. The battery is always the
        # last participant, so its index counts however many controlled heaters precede it, and
        # that is the whole reason four spellings of one wire appear below.
        "L2EMSElectricityController": {
            # Household demand from the occupancy, weight 1 and therefore inserted early. Index
            # three is the car sizer, the one setup that inserts a participant before the demand.
            "Input_UTSPConnector_ElectricalPowerConsumption_2": "ElectricalPowerConsumptionFromUTSPConnector",
            "Input_UTSPConnector_ElectricalPowerConsumption_3": "ElectricalPowerConsumptionFromUTSPConnector",
            # PV production, weight 999 and therefore inserted after the controlled loads.
            "Input_PVSystem_ElectricityOutput_2": "ElectricityOutputFromPVSystem",
            "Input_PVSystem_ElectricityOutput_3": "ElectricityOutputFromPVSystem",
            "Input_PVSystem_ElectricityOutput_4": "ElectricityOutputFromPVSystem",
            # The charge control of the electric car, which only the car sizer has. It is the
            # first participant that setup wires, which is why every index of it counts one higher
            # than the same participant's index in the sizers without a car.
            "Input_L1EVChargeControl_1_BatteryChargingPowerToEMS_2": (
                "BatteryChargingPowerToEMSFromL1EVChargeControl_1"
            ),
            # The battery's realised charge or discharge, fed back so the controller sees what its
            # own dispatch achieved. Four indices: no controlled heater (district heating, gas,
            # hydrogen, pellets, wood chips), one (gas solar thermal), two (electric heating,
            # heat pump) and three (heat pump plus solar thermal, and heat pump plus car).
            "Input_Battery_AcBatteryPowerUsed_4": "AcBatteryPowerUsedFromBattery",
            "Input_Battery_AcBatteryPowerUsed_5": "AcBatteryPowerUsedFromBattery",
            "Input_Battery_AcBatteryPowerUsed_6": "AcBatteryPowerUsedFromBattery",
            "Input_Battery_AcBatteryPowerUsed_7": "AcBatteryPowerUsedFromBattery",
            # The two batteries of the dynamic-components example, which is the setup proving the
            # derived names stay distinct when one participant class appears twice.
            "Input_Battery1_AcBatteryPowerUsed_3": "AcBatteryPowerUsedFromBattery1",
            "Input_Battery2_AcBatteryPowerUsed_4": "AcBatteryPowerUsedFromBattery2",
            # The two CHPs of the same example.
            "Input_CHP1_ElectricityOutput_5": "ElectricityOutputFromCHP1",
            "Input_CHP2_ElectricityOutput_6": "ElectricityOutputFromCHP2",
            # The hplib heat pump's two controlled draws in the heat-pump sizers, and the same two
            # one index later in the car sizer, where the car's charge control precedes them.
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerSH_4": (
                "ElectricalInputPowerSHFromMoreAdvancedHeatPumpHPLib"
            ),
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerDHW_5": (
                "ElectricalInputPowerDHWFromMoreAdvancedHeatPumpHPLib"
            ),
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerSH_5": (
                "ElectricalInputPowerSHFromMoreAdvancedHeatPumpHPLib"
            ),
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerDHW_6": (
                "ElectricalInputPowerDHWFromMoreAdvancedHeatPumpHPLib"
            ),
            # The resistive heater's two controlled draws in the electric-heating sizer.
            "Input_ElectricHeating_ElectricOutputShPower_4": "ElectricOutputShPowerFromElectricHeating",
            "Input_ElectricHeating_ElectricOutputDhwPower_5": "ElectricOutputDhwPowerFromElectricHeating",
            # The solar-thermal collector's pump draw, an EMS-controlled load like any other. Its
            # index differs between the gas and the heat-pump sizer for the reason above.
            "Input_SolarThermalSystem_ElectricityConsumptionOutput_4": (
                "ElectricityConsumptionOutputFromSolarThermalSystem"
            ),
            "Input_SolarThermalSystem_ElectricityConsumptionOutput_6": (
                "ElectricityConsumptionOutputFromSolarThermalSystem"
            ),
        },
        # The gas meter, which measures a boiler's two fuel demands. Space heating is always
        # inserted before domestic hot water.
        "GasMeter": {
            "Input_CondensingGasBoiler_EnergyDemandSh_0": "EnergyDemandShFromCondensingGasBoiler",
            "Input_CondensingGasBoiler_EnergyDemandDhw_1": "EnergyDemandDhwFromCondensingGasBoiler",
            "Input_CondensingHydrogenBoiler_EnergyDemandSh_0": "EnergyDemandShFromCondensingHydrogenBoiler",
            "Input_CondensingHydrogenBoiler_EnergyDemandDhw_1": "EnergyDemandDhwFromCondensingHydrogenBoiler",
        },
        # The fuel meter, which plays the same role for the solid-fuel and district-heating
        # sizers as the gas meter does for the gas ones.
        "FuelMeter": {
            "Input_ConventionalOilBoiler_EnergyDemandSh_0": "EnergyDemandShFromConventionalOilBoiler",
            "Input_ConventionalOilBoiler_EnergyDemandDhw_1": "EnergyDemandDhwFromConventionalOilBoiler",
            "Input_ConventionalPelletBoiler_EnergyDemandSh_0": "EnergyDemandShFromConventionalPelletBoiler",
            "Input_ConventionalPelletBoiler_EnergyDemandDhw_1": "EnergyDemandDhwFromConventionalPelletBoiler",
            "Input_ConventionalWoodChipBoiler_EnergyDemandSh_0": "EnergyDemandShFromConventionalWoodChipBoiler",
            "Input_ConventionalWoodChipBoiler_EnergyDemandDhw_1": (
                "EnergyDemandDhwFromConventionalWoodChipBoiler"
            ),
            "Input_DistrictHeating_ThermalOutputShEnergy_0": "ThermalOutputShEnergyFromDistrictHeating",
            "Input_DistrictHeating_ThermalOutputDhwEnergy_1": "ThermalOutputDhwEnergyFromDistrictHeating",
        },
    }

    #: Dispatch outputs, per aggregator: the back-channel an aggregator grows to steer one
    #: participant. Unlike the inputs above these are real result columns, so translating them is
    #: what makes the second comparison cover the whole frame rather than the part whose names
    #: happen to agree.
    DISPATCH_OUTPUTS: ClassVar[Mapping[str, Mapping[str, str]]] = {
        "L2EMSElectricityController": {
            # The battery target of the ten EMS sizers that pass this prefix. The legacy name is
            # that prefix, 'LoadingPowerInputForBattery_', plus the source weight the controller
            # steers the battery on, which is 6 in every one of them. Until F-1 was fixed the
            # suffix was instead the controller's output counter — the battery was its fourteenth
            # output, after seven static ones and six grown eagerly by its default connections —
            # so a port's name was a function of how many unrelated ports had been declared
            # before it. That is how this table came to be authored stale: retiring the old
            # advanced heat pump (#604) had already removed one of those outputs when the rows
            # were first written, yet they spelled the pre-retirement 'Output15', and every EMS
            # setup failed the parity comparison from the table's first day. The suffix is now
            # the weight, which no unrelated edit moves, and the canary test still asserts these
            # spellings against a live build.
            "LoadingPowerInputForBattery_6": "DispatchToBattery_LoadingPowerInput",
            # The dynamic-components example steers four participants and names all four targets
            # 'ElectricityTarget', so what tells them apart is the weight each is dispatched on:
            # the two batteries carry 1 and 2, the two fuel cells 3 and 4.
            "ElectricityTarget1": "DispatchToBattery1_LoadingPowerInput",
            "ElectricityTarget2": "DispatchToBattery2_LoadingPowerInput",
            "ElectricityTarget3": "DispatchToCHP1_ElectricityFromCHPTarget",
            "ElectricityTarget4": "DispatchToCHP2_ElectricityFromCHPTarget",
            # The car sizer steers two participants and passes a prefix of its own for each: the
            # car's charge control on weight 5, the house battery on weight 6. That setup is
            # therefore the one place where the battery target is not spelled
            # 'LoadingPowerInputForBattery_' — the declarative name is the same either way,
            # because it is derived from the participant and its input rather than from what a
            # setup chose to call the channel.
            "ElectricityToOrFromGridOfL1Controller_5": (
                "DispatchToL1EVChargeControl_1_ElectricityTargetFromEMS"
            ),
            "ChargingPowerForBattery_6": "DispatchToBattery_LoadingPowerInput",
            # The six participant targets below are the other kind of dispatch output: nothing
            # reads them, they exist so that the electricity the manager grants each controlled
            # load appears in the result file and in the per-participant KPIs. Both paths grow
            # them, and each names them in its own scheme — the legacy one from the prefix the
            # manager's own default connection passes, 'ElectricityToOrFromGridOf' plus the
            # participant's class name (with the flow, SH or DHW, in front of that class name
            # wherever one participant has two), and the source weight it is steered on; the
            # declarative one from the recorded-dispatch template, 'DispatchFor' plus the
            # participant and the output being measured.
            #
            # These wires needed no row until F-1. The manager used to publish a target port per
            # participant class it can describe from its constructor, so both paths carried the
            # same constructor-made ports under the same counter-derived names and the comparison
            # matched them literally. F-1 retired those constructor ports and materialises the
            # target beside the feed instead, for the participants a run actually has, which is
            # what left each path spelling these six in its own scheme with nothing declaring the
            # two spellings equal — and all eleven EMS sizers failing the result comparison over
            # nothing but names.
            #
            # The occupancy, weight 1, in all eleven EMS sizers. It is the one pair whose halves
            # name the participant differently: the legacy prefix carries the connector's class
            # name, ``UtspLpgConnector``, while the twins call the instance ``UTSPConnector``.
            "ElectricityToOrFromGridOfUtspLpgConnector_1": (
                "DispatchForUTSPConnector_ElectricalPowerConsumption"
            ),
            # The hplib heat pump's two controlled draws, space heating on weight 2 and domestic
            # hot water on weight 3, in the three heat-pump sizers: the plain one, the one with a
            # solar-thermal collector and the one with a car.
            "ElectricityToOrFromGridOfSHMoreAdvancedHeatPumpHPLib_2": (
                "DispatchForMoreAdvancedHeatPumpHPLib_ElectricalInputPowerSH"
            ),
            "ElectricityToOrFromGridOfDHWMoreAdvancedHeatPumpHPLib_3": (
                "DispatchForMoreAdvancedHeatPumpHPLib_ElectricalInputPowerDHW"
            ),
            # The resistive heater's two controlled draws, on the same weights 2 and 3, in the
            # electric-heating sizer. Sharing a weight with the heat pump costs nothing because no
            # setup has both; here the class name and the twin's component key agree, so only the
            # scheme differs.
            "ElectricityToOrFromGridOfSHElectricHeating_2": (
                "DispatchForElectricHeating_ElectricOutputShPower"
            ),
            "ElectricityToOrFromGridOfDHWElectricHeating_3": (
                "DispatchForElectricHeating_ElectricOutputDhwPower"
            ),
            # The solar-thermal collector's pump draw, weight 4, in the two solar-thermal sizers,
            # the gas one and the heat-pump one.
            "ElectricityToOrFromGridOfSolarThermalSystem_4": (
                "DispatchForSolarThermalSystem_ElectricityConsumptionOutput"
            ),
        },
    }

    @classmethod
    def pairs(cls) -> Dict[Tuple[str, str], str]:
        """Flattens the two nested tables into the form :class:`PortRenaming` takes.

        Returns:
            A mapping of ``(component name, legacy port name)`` to the declarative port name.

        Raises:
            ValueError: If one component declares the same legacy port twice with two different
                meanings, which would mean the two halves of the table disagree about a name.
        """
        flattened: Dict[Tuple[str, str], str] = {}
        for table in (cls.AGGREGATOR_INPUTS, cls.DISPATCH_OUTPUTS):
            for component_name, renamings in table.items():
                for legacy, declarative in renamings.items():
                    key = (component_name, legacy)
                    if key in flattened and flattened[key] != declarative:
                        raise ValueError(
                            f"'{component_name}.{legacy}' is declared to mean both "
                            f"'{flattened[key]}' and '{declarative}'."
                        )
                    flattened[key] = declarative
        return flattened

    @classmethod
    def port_renaming(cls) -> PortRenaming:
        """The table as the parity harness consumes it.

        Returns:
            A :class:`PortRenaming` carrying every declared pair, which rewrites a wiring snapshot
            and a result frame through one and the same claim.
        """
        return PortRenaming(renamings=cls.pairs())
