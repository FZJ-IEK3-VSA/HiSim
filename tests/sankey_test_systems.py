"""Small synthetic energy systems for the tests of the matplotlib Sankey figure.

Each factory builds the ``SankeyInputs`` of one hand-made ``overall`` view whose numbers can be read off the code: the
case one rule of the drawn graph, the renderer or the automatic layout needs (a boiler fed twice by its meter, a
battery that charges and discharges, a storage without a discharge, a system without a building, two generators
into one buffer, a solar collector with its pump, an undeclared component with a gap, a car). No recorded run is
committed; the figures of every real twin are laid out in the golden jobs. Two of the systems have a hand-written
complete pin file (``heat_pump_house_pins``, ``boiler_fed_twice_pins``) that draws them with no overlap, for the
renderer's tests before the automatic layout exists.
"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from hisim.postprocessing.energy_balance.figure.inputs import SankeyInputs, ViewLink, ViewNode

#: One edge of a view: source, target, kWh, carrier, edge kind, port label.
Edge = Tuple[str, str, float, Optional[str], str, str]


class SankeySystems:
    """Factories of synthetic views, one per case."""

    @staticmethod
    def inputs(
        kinds: Mapping[str, str],
        edges: Sequence[Edge],
        kpis: Optional[Dict[str, float]] = None,
        peak: Optional[Dict[str, float]] = None,
        undeclared: Tuple[str, ...] = (),
    ) -> SankeyInputs:
        """Return the inputs of a view with the given node kinds and edges, over one year at 900 s steps."""
        nodes = tuple(ViewNode(name, kind) for name, kind in kinds.items())
        links = tuple(ViewLink(s, t, v, c, k, label) for s, t, v, c, k, label in edges)
        return SankeyInputs(
            nodes=nodes,
            links=links,
            class_of={},
            verdict_of={},
            undeclared=undeclared,
            verdict="closes",
            run_hours=8760.0,
            seconds_per_timestep=900,
            kpis=kpis or {},
            peak_power_in_watt=peak or {},
            title="test",
        )

    @staticmethod
    def heat_pump_house() -> SankeyInputs:
        """Return a heat pump house: a PV and the grid feed a meter, which feeds a heat pump, a household and a battery.

        The heat pump heats a buffer, the buffer the building; the household's appliances heat the building too; the
        battery charges from and discharges to the meter (a loop).
        """
        kinds = {
            "sun": "boundary",
            "grid import": "boundary",
            "grid export": "boundary",
            "ambient heat source": "boundary",
            "outdoors": "boundary",
            "PV": "component",
            "PV · Loss": "loss",
            "Meter": "meter",
            "HeatPump": "component",
            "Buffer": "component",
            "Buffer · stored": "storage",
            "Buffer · Loss": "loss",
            "Building": "component",
            "Building · Opaque": "loss",
            "Building · Vent": "loss",
            "Household": "component",
            "Household · Rest": "loss",
            "Battery": "component",
            "Battery · stored": "storage",
            "Battery · Loss": "loss",
        }
        edges: List[Edge] = [
            ("sun", "PV", 500.0, "solar", "boundary", "PV.Sun"),
            ("PV", "PV · Loss", 400.0, "solar", "loss", "PV.Loss"),
            ("PV · Loss", "outdoors", 400.0, "solar", "loss", "PV.Loss"),
            ("PV", "Meter", 100.0, "electricity", "metered", "PV.ElectricityOutput"),
            ("grid import", "Meter", 50.0, "electricity", "meter_boundary", "Meter.ElectricityFromGridInWatt"),
            ("Meter", "grid export", 45.0, "electricity", "meter_boundary", "Meter.ElectricityToGridInWatt"),
            ("Meter", "HeatPump", 40.0, "electricity", "metered", "HeatPump.ElectricalInputPowerSH"),
            ("ambient heat source", "HeatPump", 120.0, "ambient_heat", "boundary", "HeatPump.Ambient"),
            ("HeatPump", "Buffer", 160.0, "space_heating_heat", "transfer", "Buffer.loop"),
            ("Buffer", "Buffer · Loss", 8.0, "space_heating_heat", "loss", "Buffer.StandbyHeatLoss"),
            ("Buffer", "Buffer · stored", 2.0, "space_heating_heat", "storage", "Buffer.Stored"),
            ("Buffer", "Building", 150.0, "space_heating_heat", "transfer", "Building.HeatReceivedInWatt"),
            ("Meter", "Household", 60.0, "electricity", "metered", "Household.ElectricalPowerConsumption"),
            ("Household", "Building", 45.0, "room_heat", "transfer", "Building.HeatingByDevices"),
            ("Household", "Household · Rest", 15.0, "electricity", "loss", "Household.Rest"),
            ("Building", "Building · Opaque", 130.0, "room_heat", "loss", "Building.Opaque"),
            ("Building", "Building · Vent", 65.0, "room_heat", "loss", "Building.Vent"),
            ("Meter", "Battery", 30.0, "electricity", "metered", "Battery.ChargingPower"),
            ("Battery", "Meter", 25.0, "electricity", "metered", "Battery.DischargingPower"),
            ("Battery", "Battery · Loss", 4.0, "electricity", "loss", "Battery.InverterLossInWatt"),
            ("Battery", "Battery · stored", 1.0, "electricity", "storage", "Battery.Stored"),
        ]
        return SankeySystems.inputs(kinds, edges, kpis={"Conditioned floor area (Building)": 150.0}, peak={"PV": 10_000.0})

    @staticmethod
    def boiler_fed_twice() -> SankeyInputs:
        """Return a gas boiler fed twice by its meter (space heating and hot water) and an electricity meter apart.

        The boiler heats the building and a hot-water tank; the electricity meter, linked to nothing of the heat chain,
        feeds a household that heats the building: the case of a further tree below the chain.
        """
        kinds = {
            "gas": "boundary",
            "grid": "boundary",
            "out": "boundary",
            "taps": "boundary",
            "G": "meter",
            "B": "component",
            "B · L": "loss",
            "T": "component",
            "Bld": "component",
            "E": "meter",
            "H": "component",
        }
        edges: List[Edge] = [
            ("gas", "G", 14.0, "natural_gas", "meter_boundary", "G.in"),
            ("G", "B", 11.0, "natural_gas", "metered", "B.EnergyDemandSh"),
            ("G", "B", 3.0, "natural_gas", "metered", "B.EnergyDemandDhw"),
            ("B", "B · L", 1.0, "natural_gas", "loss", "B.Loss"),
            ("B", "Bld", 10.5, "space_heating_heat", "transfer", "Bld.in"),
            ("B", "T", 2.5, "domestic_hot_water_heat", "transfer", "T.in"),
            ("T", "taps", 2.5, "domestic_hot_water_heat", "boundary", "T.taps"),
            ("grid", "E", 3.0, "electricity", "meter_boundary", "E.in"),
            ("E", "H", 3.0, "electricity", "metered", "H.in"),
            ("H", "Bld", 3.0, "room_heat", "transfer", "Bld.gains"),
            ("Bld", "out", 13.5, "room_heat", "boundary", "Bld.out"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def storage_without_discharge() -> SankeyInputs:
        """Return a meter feeding a household and a battery that only charges in the run (it stores everything)."""
        kinds = {"grid": "boundary", "M": "meter", "H": "component", "H · L": "loss", "Bat": "component", "Bat · stored": "storage"}
        edges: List[Edge] = [
            ("grid", "M", 10.0, "electricity", "meter_boundary", "M.in"),
            ("M", "H", 8.0, "electricity", "metered", "H.in"),
            ("H", "H · L", 8.0, "electricity", "loss", "H.L"),
            ("M", "Bat", 2.0, "electricity", "metered", "Bat.ChargingPower"),
            ("Bat", "Bat · stored", 2.0, "electricity", "storage", "Bat.Stored"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def no_building() -> SankeyInputs:
        """Return a system without a building: a wind transformer and the grid feed a meter, the meter an electrolyzer."""
        kinds = {
            "wind": "boundary",
            "grid": "boundary",
            "export": "boundary",
            "T": "component",
            "M": "meter",
            "Elz": "component",
            "Elz · L": "loss",
            "HM": "meter",
            "h2 out": "boundary",
        }
        edges: List[Edge] = [
            ("wind", "T", 20.0, "wind_electricity", "boundary", "T.in"),
            ("T", "M", 19.0, "electricity", "metered", "T.out"),
            ("grid", "M", 1.0, "electricity", "meter_boundary", "M.in"),
            ("M", "Elz", 18.0, "electricity", "metered", "Elz.in"),
            ("M", "export", 2.0, "electricity", "meter_boundary", "M.out"),
            ("Elz", "Elz · L", 6.0, "electricity", "loss", "Elz.L"),
            ("Elz", "HM", 12.0, "hydrogen", "metered", "HM.in"),
            ("HM", "h2 out", 12.0, "hydrogen", "meter_boundary", "HM.out"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def several_generators() -> SankeyInputs:
        """Return a heat pump and a gas boiler both heating one buffer: the larger is on the chain."""
        kinds = {
            "grid": "boundary",
            "gas": "boundary",
            "ambient heat source": "boundary",
            "out": "boundary",
            "E": "meter",
            "G": "meter",
            "HP": "component",
            "Boiler": "component",
            "Buf": "component",
            "Buf · stored": "storage",
            "Bld": "component",
        }
        edges: List[Edge] = [
            ("grid", "E", 3.0, "electricity", "meter_boundary", "E.in"),
            ("E", "HP", 3.0, "electricity", "metered", "HP.el"),
            ("ambient heat source", "HP", 9.0, "ambient_heat", "boundary", "HP.amb"),
            ("HP", "Buf", 12.0, "space_heating_heat", "transfer", "Buf.hp"),
            ("gas", "G", 5.0, "natural_gas", "meter_boundary", "G.in"),
            ("G", "Boiler", 5.0, "natural_gas", "metered", "Boiler.in"),
            ("Boiler", "Buf", 5.0, "space_heating_heat", "transfer", "Buf.boiler"),
            ("Buf", "Buf · stored", 0.5, "space_heating_heat", "storage", "Buf.stored"),
            ("Buf", "Bld", 16.5, "space_heating_heat", "transfer", "Bld.in"),
            ("Bld", "out", 16.5, "room_heat", "boundary", "Bld.out"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def solar_collector_with_pump() -> SankeyInputs:
        """Return a boiler and a solar collector heating a hot-water tank; the collector's pump takes meter electricity."""
        kinds = {
            "sun": "boundary",
            "gas": "boundary",
            "grid": "boundary",
            "taps": "boundary",
            "out": "boundary",
            "G": "meter",
            "B": "component",
            "Bld": "component",
            "T": "component",
            "T · stored": "storage",
            "C": "component",
            "C · L": "loss",
            "C · P": "loss",
            "E": "meter",
        }
        edges: List[Edge] = [
            ("gas", "G", 13.0, "natural_gas", "meter_boundary", "G.in"),
            ("G", "B", 13.0, "natural_gas", "metered", "B.in"),
            ("B", "Bld", 10.0, "space_heating_heat", "transfer", "Bld.in"),
            ("B", "T", 3.0, "domestic_hot_water_heat", "transfer", "T.boiler"),
            ("sun", "C", 4.0, "solar", "boundary", "C.sun"),
            ("C", "C · L", 3.0, "solar", "loss", "C.L"),
            ("grid", "E", 0.1, "electricity", "meter_boundary", "E.in"),
            ("E", "C", 0.1, "electricity", "metered", "C.pump"),
            ("C", "C · P", 0.1, "electricity", "loss", "C.P"),
            ("C", "T", 1.0, "domestic_hot_water_heat", "transfer", "T.solar"),
            ("T", "taps", 3.9, "domestic_hot_water_heat", "boundary", "T.taps"),
            ("T", "T · stored", 0.1, "domestic_hot_water_heat", "storage", "T.stored"),
            ("Bld", "out", 10.0, "room_heat", "boundary", "Bld.out"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def undeclared_with_gap() -> SankeyInputs:
        """Return a gas chain whose heat distribution declares no ports: it receives 10.0 kWh and gives 10.6 kWh."""
        kinds = {"gas": "boundary", "out": "boundary", "G": "meter", "B": "component", "B · L": "loss", "undeclared: D": "undeclared", "Bld": "component"}
        edges: List[Edge] = [
            ("gas", "G", 11.0, "natural_gas", "meter_boundary", "G.in"),
            ("G", "B", 11.0, "natural_gas", "metered", "B.in"),
            ("B", "B · L", 1.0, "natural_gas", "loss", "B.L"),
            ("B", "undeclared: D", 10.0, "space_heating_heat", "transfer", "D.in"),
            ("undeclared: D", "Bld", 10.6, "space_heating_heat", "transfer", "Bld.in"),
            ("Bld", "out", 10.6, "room_heat", "boundary", "Bld.out"),
        ]
        return SankeySystems.inputs(kinds, edges, undeclared=("D",))

    @staticmethod
    def car() -> SankeyInputs:
        """Return a meter feeding a household and a car battery, the car battery a car that drives."""
        kinds = {"grid": "boundary", "drive": "boundary", "M": "meter", "H": "component", "H · L": "loss", "CB": "component", "CB · L": "loss", "Car": "component"}
        edges: List[Edge] = [
            ("grid", "M", 10.0, "electricity", "meter_boundary", "M.in"),
            ("M", "H", 7.0, "electricity", "metered", "H.in"),
            ("H", "H · L", 7.0, "electricity", "loss", "H.L"),
            ("M", "CB", 3.0, "electricity", "metered", "CB.in"),
            ("CB", "CB · L", 0.3, "electricity", "loss", "CB.L"),
            ("CB", "Car", 2.7, "vehicle_electricity", "transfer", "Car.in"),
            ("Car", "drive", 2.7, "driving", "boundary", "Car.out"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def summer_tank() -> SankeyInputs:
        """Return a summer week of a gas and solar-thermal house: the collector covers most of the hot water.

        Without space heating the chain is meter, household, building; the tank's largest heat input is the
        collector, so the tank hangs from the collector's tree, and the boiler's little hot-water heat is a connector
        from the gas meter's tree into the tank's south side (the tank's tail holds the collector).
        """
        kinds = {
            "sun": "boundary",
            "gas": "boundary",
            "grid": "boundary",
            "taps": "boundary",
            "out": "boundary",
            "S": "component",
            "S · L": "loss",
            "G": "meter",
            "B": "component",
            "B · L": "loss",
            "T": "component",
            "E": "meter",
            "H": "component",
            "Bld": "component",
        }
        edges: List[Edge] = [
            ("sun", "S", 10.0, "solar", "boundary", "S.sun"),
            ("S", "S · L", 4.0, "solar", "loss", "S.loss"),
            ("S", "T", 6.0, "domestic_hot_water_heat", "transfer", "T.solar"),
            ("gas", "G", 2.0, "natural_gas", "meter_boundary", "G.in"),
            ("G", "B", 2.0, "natural_gas", "metered", "B.in"),
            ("B", "B · L", 0.2, "natural_gas", "loss", "B.loss"),
            ("B", "T", 1.8, "domestic_hot_water_heat", "transfer", "T.boiler"),
            ("T", "taps", 7.8, "domestic_hot_water_heat", "boundary", "T.taps"),
            ("grid", "E", 5.0, "electricity", "meter_boundary", "E.in"),
            ("E", "H", 5.0, "electricity", "metered", "H.in"),
            ("H", "Bld", 5.0, "room_heat", "transfer", "Bld.gains"),
            ("Bld", "out", 5.0, "room_heat", "boundary", "Bld.out"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def hot_water_only() -> SankeyInputs:
        """Return a hot-water system without a building: gas meter, boiler and tank on the chain, a collector beside.

        The tank is on the chain, so the boiler's heat holds its tail; the collector's heat comes in from the north.
        """
        kinds = {
            "sun": "boundary",
            "gas": "boundary",
            "taps": "boundary",
            "S": "component",
            "S · L": "loss",
            "G": "meter",
            "B": "component",
            "B · L": "loss",
            "T": "component",
        }
        edges: List[Edge] = [
            ("sun", "S", 10.0, "solar", "boundary", "S.sun"),
            ("S", "S · L", 4.0, "solar", "loss", "S.loss"),
            ("S", "T", 6.0, "domestic_hot_water_heat", "transfer", "T.solar"),
            ("gas", "G", 2.0, "natural_gas", "meter_boundary", "G.in"),
            ("G", "B", 2.0, "natural_gas", "metered", "B.in"),
            ("B", "B · L", 0.2, "natural_gas", "loss", "B.loss"),
            ("B", "T", 1.8, "domestic_hot_water_heat", "transfer", "T.boiler"),
            ("T", "taps", 7.8, "domestic_hot_water_heat", "boundary", "T.taps"),
        ]
        return SankeySystems.inputs(kinds, edges)

    @staticmethod
    def nothing_to_draw() -> SankeyInputs:
        """Return a view without a component, meter or undeclared node."""
        return SankeySystems.inputs({"sun": "boundary"}, [])

    @staticmethod
    def heat_pump_house_pins() -> Dict[str, Any]:
        """Return a complete pin file for :meth:`heat_pump_house`: the PV upstream of the meter, the chain east of it.

        The household hangs below the meter and heats the building through a connector; the battery hangs below the
        meter and discharges back into it through a second connector. Drawn, it has no fault and no overlap.
        """
        return {
            "schema": "hisim.sankey_pins/1",
            "diagrams": [
                {
                    "component": "PV",
                    "trunklength": 3.5,
                    "cut": True,
                    "flows": [
                        {
                            "link": "PV.ElectricityOutput",
                            "orientation": 0,
                            "pathlength": 3.0,
                            "label": [-2.0, -2.0],
                            "inside": True,
                            "color": "#1a1a1a",
                            "on_bands": ["PV", "Meter"],
                        }
                    ],
                },
                {
                    "component": "Meter",
                    "prior": "PV",
                    "connect": "PV.ElectricityOutput",
                    "at": [0.0, 0.0],
                    "trunklength": 9.6,
                    "flows": [
                        {"link": "PV.ElectricityOutput", "orientation": 0, "pathlength": 0.8},
                        {"link": "Meter.ElectricityFromGridInWatt", "orientation": 1, "pathlength": 1.92},
                        {"link": "Battery.DischargingPower", "orientation": -1, "pathlength": 0.48},
                        {"link": "Meter.ElectricityToGridInWatt", "orientation": 1, "pathlength": 2.88},
                        {"link": "HeatPump.ElectricalInputPowerSH", "orientation": 0, "pathlength": 8.0, "label": [-4.16, 0.75], "ha": "center", "va": "bottom"},
                        {"link": "Household.ElectricalPowerConsumption", "orientation": -1, "pathlength": 5.6, "label": [0.9, 2.88], "ha": "left"},
                        {"link": "Battery.ChargingPower", "orientation": -1, "pathlength": 9.6, "label": [-0.8, 4.48], "ha": "right"},
                    ],
                },
                {
                    "component": "HeatPump",
                    "prior": "Meter",
                    "connect": "HeatPump.ElectricalInputPowerSH",
                    "trunklength": 4.8,
                    "flows": [
                        {"link": "HeatPump.ElectricalInputPowerSH", "orientation": 0, "pathlength": 0.8},
                        {"link": "HeatPump.Ambient", "orientation": -1, "pathlength": 1.92},
                        {"link": "Buffer.loop", "orientation": 0, "pathlength": 7.2, "label": [-4.0, 0.0], "inside": True, "on_bands": ["HeatPump", "Buffer"]},
                    ],
                },
                {
                    "component": "Buffer",
                    "prior": "HeatPump",
                    "connect": "Buffer.loop",
                    "trunklength": 3.52,
                    "flows": [
                        {"link": "Buffer.loop", "orientation": 0, "pathlength": 0.48},
                        {"link": "Buffer.Stored", "orientation": -1, "pathlength": 1.28},
                        {"link": "Buffer.StandbyHeatLoss", "orientation": 1, "pathlength": 1.6},
                        {
                            "link": "Building.HeatReceivedInWatt",
                            "orientation": 0,
                            "pathlength": 6.08,
                            "label": [-3.36, 0.0],
                            "inside": True,
                            "on_bands": ["Buffer", "Building"],
                        },
                    ],
                },
                {
                    "component": "Building",
                    "prior": "Buffer",
                    "connect": "Building.HeatReceivedInWatt",
                    "trunklength": 6.4,
                    "title_offset": [1.28, 0.0],
                    "flows": [
                        {"link": "Building.HeatReceivedInWatt", "orientation": 0, "pathlength": 0.48},
                        {"link": "Building.HeatingByDevices", "orientation": -1, "pathlength": 0.48},
                        {"link": "Building.Vent", "orientation": 1, "pathlength": 2.4},
                        {"link": "Building.Opaque", "orientation": 0, "pathlength": 2.4},
                    ],
                },
                {
                    "component": "Household",
                    "prior": "Meter",
                    "connect": "Household.ElectricalPowerConsumption",
                    "trunklength": 6.72,
                    "title_offset": [0.0, 0.8],
                    "flows": [
                        {"link": "Household.ElectricalPowerConsumption", "orientation": 1, "pathlength": 0.8},
                        {"link": "Building.HeatingByDevices", "orientation": 0, "pathlength": 0.48},
                        {"link": "Household.Rest", "orientation": -1, "pathlength": 2.56},
                    ],
                },
                {
                    "component": "Battery",
                    "prior": "Meter",
                    "connect": "Battery.ChargingPower",
                    "trunklength": 4.0,
                    "title_offset": [-1.92, -1.12],
                    "flows": [
                        {"link": "Battery.ChargingPower", "orientation": 0, "pathlength": 0.48},
                        {"link": "Battery.Stored", "orientation": -1, "pathlength": 0.96},
                        {"link": "Battery.DischargingPower", "orientation": -1, "pathlength": 0.48},
                        {"link": "Battery.InverterLossInWatt", "orientation": 1, "pathlength": 1.28},
                    ],
                },
            ],
            "connectors": [
                {"link": "Battery.DischargingPower", "turns": [], "label": {"segment": 0, "side": "below"}},
                {"link": "Building.HeatingByDevices", "turns": [], "label": {"segment": 0, "side": "above"}},
            ],
        }

    @staticmethod
    def boiler_fed_twice_pins() -> Dict[str, Any]:
        """Return a complete pin file for :meth:`boiler_fed_twice`, the hot-water fuel drawn as an arrow of its own.

        The gas meter's second link into the boiler is a parallel connector below the tree connection; the
        electricity meter's tree is placed below the chain by its at. Drawn, it has no fault and no overlap.
        """
        return {
            "schema": "hisim.sankey_pins/1",
            "canvas": {"kwh_per_unit": 4.0},
            "diagrams": [
                {
                    "component": "G",
                    "trunklength": 4.0,
                    "flows": [
                        {"link": "G.in", "orientation": 0, "pathlength": 1.0},
                        {"link": "B.EnergyDemandSh", "orientation": 0, "pathlength": 3.0, "label": [-1.5, 0.0], "inside": True, "on_bands": ["G", "B"]},
                        {"link": "B.EnergyDemandDhw", "orientation": -1, "pathlength": 0.6},
                    ],
                },
                {
                    "component": "B",
                    "prior": "G",
                    "connect": "B.EnergyDemandSh",
                    "trunklength": 4.0,
                    "flows": [
                        {"link": "B.EnergyDemandSh", "orientation": 0, "pathlength": 0.5},
                        {"link": "B.EnergyDemandDhw", "orientation": -1, "pathlength": 0.6},
                        {"link": "B.Loss", "orientation": -1, "pathlength": 1.0},
                        {"link": "T.in", "orientation": 1, "pathlength": 2.0},
                        {"link": "Bld.in", "orientation": 0, "pathlength": 3.0},
                    ],
                },
                {
                    "component": "T",
                    "prior": "B",
                    "connect": "T.in",
                    "trunklength": 2.0,
                    "flows": [{"link": "T.in", "orientation": -1, "pathlength": 0.5}, {"link": "T.taps", "orientation": 0, "pathlength": 1.0}],
                },
                {
                    "component": "Bld",
                    "prior": "B",
                    "connect": "Bld.in",
                    "trunklength": 3.0,
                    "flows": [
                        {"link": "Bld.in", "orientation": 0, "pathlength": 0.5},
                        {"link": "Bld.gains", "orientation": -1, "pathlength": 0.6},
                        {"link": "Bld.out", "orientation": 0, "pathlength": 1.5},
                    ],
                },
                {
                    "component": "E",
                    "at": [0.0, -9.0],
                    "trunklength": 5.0,
                    "flows": [{"link": "E.in", "orientation": 0, "pathlength": 1.0}, {"link": "H.in", "orientation": 0, "pathlength": 2.0}],
                },
                {
                    "component": "H",
                    "prior": "E",
                    "connect": "H.in",
                    "trunklength": 2.0,
                    "flows": [{"link": "H.in", "orientation": 0, "pathlength": 0.5}, {"link": "Bld.gains", "orientation": 0, "pathlength": 0.6}],
                },
            ],
            "connectors": [
                {"link": "B.EnergyDemandDhw", "parallel": True, "turns": ["-2.0"], "label": {"segment": 1, "side": "below"}},
                {"link": "Bld.gains", "turns": [], "label": {"segment": 0, "side": "below"}},
            ],
        }

    @staticmethod
    def pin_file(document: Mapping[str, Any], directory: Path) -> Path:
        """Write a pin document as pins.yaml into a directory and return its path."""
        path = directory / "pins.yaml"
        path.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")
        return path
