"""Small synthetic energy systems for the tests of the matplotlib Sankey figure.

Each factory builds the ``SankeyInputs`` of one hand-made ``overall`` view whose numbers can be read off the code: the
case one rule of the drawn graph, the renderer or the automatic layout needs (a boiler fed twice by its meter, a
battery that charges and discharges, a storage without a discharge, a system without a building, two generators
into one buffer, a solar collector with its pump, an undeclared component with a gap, a car). No recorded run is
committed; the figures of every real twin are laid out in the golden jobs.
"""

from typing import Dict, List, Mapping, Optional, Sequence, Tuple

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
    def nothing_to_draw() -> SankeyInputs:
        """Return a view without a component, meter or undeclared node."""
        return SankeySystems.inputs({"sun": "boundary"}, [])
