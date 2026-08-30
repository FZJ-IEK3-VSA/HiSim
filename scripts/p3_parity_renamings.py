#!/usr/bin/env python3
"""The declared translation between the two paths' aggregator port names.

TEMPORARY — this table belongs to the P3 migration parity rig (requirements R11) and is deleted
with it in P3's last PR (R11.8, AC-P3.20).

An aggregator does not declare a port per participant; it grows one per feed, and the two paths
derive that port's name differently. The imperative add-API names an aggregator input after the
participant, the output being measured and its insertion order — ``Input_<source>_<field>_<n>`` —
and a dispatch output after whatever the setup passed as a prefix plus a counter over the
aggregator's outputs. The declarative path derives both from the frozen templates of the format:
``<field>From<source>`` for an input and ``DispatchTo<source>_<input>`` for a dispatch output. The
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
whole fleet. A key can be that specific because the legacy index is part of the name: the same
participant feeding the same aggregator in two setups produces two different legacy names when it
was inserted at two different positions, and both appear below with the same declarative name.
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
            # Household demand from the occupancy, weight 1 and therefore inserted early.
            "Input_UTSPConnector_ElectricalPowerConsumption_2": "ElectricalPowerConsumptionFromUTSPConnector",
            # PV production, weight 999 and therefore inserted after the controlled loads.
            "Input_PVSystem_ElectricityOutput_2": "ElectricityOutputFromPVSystem",
            "Input_PVSystem_ElectricityOutput_3": "ElectricityOutputFromPVSystem",
            # The battery's realised charge or discharge, fed back so the controller sees what its
            # own dispatch achieved. Four indices: no controlled heater (district heating, gas,
            # hydrogen, pellets, wood chips), one (gas solar thermal), two (electric heating,
            # heat pump) and three (heat pump plus solar thermal).
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
            # The hplib heat pump's two controlled draws in the heat-pump sizers.
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerSH_4": (
                "ElectricalInputPowerSHFromMoreAdvancedHeatPumpHPLib"
            ),
            "Input_MoreAdvancedHeatPumpHPLib_ElectricalInputPowerDHW_5": (
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
            # The battery target of all nine EMS sizers. The legacy name is the prefix the setup
            # passed, 'LoadingPowerInputForBattery_', plus the controller's fifteenth output.
            "LoadingPowerInputForBattery_Output15": "DispatchToBattery_LoadingPowerInput",
            # The dynamic-components example steers four participants and names all four targets
            # 'ElectricityTargetOutput', so in the legacy spelling only the counter tells them
            # apart — which is exactly the fragility the declarative templates remove.
            "ElectricityTargetOutput15": "DispatchToBattery1_LoadingPowerInput",
            "ElectricityTargetOutput16": "DispatchToBattery2_LoadingPowerInput",
            "ElectricityTargetOutput17": "DispatchToCHP1_ElectricityFromCHPTarget",
            "ElectricityTargetOutput18": "DispatchToCHP2_ElectricityFromCHPTarget",
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
