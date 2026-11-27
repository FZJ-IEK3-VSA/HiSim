"""The port energies the energy balance hands to the cost engine: their content, their file spelling and their way in.

The record (:class:`~hisim.contracts.port_energies.PortEnergies`) is built from the balance of a synthetic run whose
numbers can be read off the test: a heat pump and a PV system whose electricity one meter reads, three 900 s steps
(``tests/test_energy_balance.py``, ``house``). The heat pump takes 1.0, 0.2 and 0.0 kWh of electricity and 3.0, 0.6 and
0.0 kWh of ambient heat and gives 4.0, 0.8 and 0.0 kWh of space heat; the PV system turns 0.4, 0.5 and 0.3 kWh of sun
into as much electricity; the meter publishes 0.6 kWh imported and 0.6 kWh exported.
"""

from types import SimpleNamespace
from typing import Any, Dict, Tuple
from unittest import mock

import numpy as np
import pytest

from hisim import component as cp
from hisim import loadtypes as lt
from hisim.contracts.port_energies import LoopTag, MeterEnergy, PeerKind, PortEnergies, PortEnergy
from hisim.energy_port import FluidLoop, LoopFluid
from hisim.postprocessing.energy_balance import ports as balance_ports
from hisim.postprocessing.postprocessing_main import PostProcessor
from hisim.simulationparameters import SimulationParameters
from tests.test_energy_balance import house, report

pytestmark = pytest.mark.base

CONSUMPTION, PRODUCTION = lt.EnergyRole.CONSUMPTION, lt.EnergyRole.PRODUCTION
ELECTRICITY = lt.EnergyBalanceCarrier.ELECTRICITY
SPACE_HEATING = lt.EnergyBalanceCarrier.SPACE_HEATING_HEAT


def ports_by_name(record: PortEnergies) -> Dict[str, PortEnergy]:
    """The record's ports by ``subject.port``."""
    return {f"{port.subject}.{port.port}": port for port in record.ports}


class TaggingEmitter(cp.PhysicsComponent):
    """A stand-in emitter that tags its space-heating loop but books none of it, as the heat distribution system without a buffer does."""

    LOOP = FluidLoop("space_heating", carrier=SPACE_HEATING, fluid=LoopFluid.WATER, supplies=True, may_cool=False, pumps=True)

    def port_sets(self) -> Tuple[cp.FluidLoopPortSet, ...]:
        """The one loop it tags."""
        return (cp.FluidLoopPortSet(self.component_name, self.LOOP, {}),)

    def port_sets_in_the_energy_balance(self) -> Tuple[cp.FluidLoopPortSet, ...]:
        """None of them: the balance books no heat on its loop."""
        return ()


class TestTheRecordOfARun:
    """``EnergyBalanceReport.port_energies`` lists what the balance computed, and nothing it did not."""

    def test_every_declared_port_carries_its_total_its_steps_and_its_peer(self) -> None:
        """Catches a port that is dropped, summed with the wrong sign, or handed on without its peer.

        The heat pump's electricity is 1.0 + 0.2 + 0.0 = 1.2 kWh from the meter; its heat, 4.8 kWh, leaves to the
        boundary node it names; the PV system's 1.2 kWh of electricity go to the meter.
        """
        record = house(through_the_manager=False).port_energies()

        by_name = ports_by_name(record)
        assert set(by_name) == {"HeatPump.ElectricityIn", "HeatPump.AmbientIn", "HeatPump.HeatOut", "PV.SunIn", "PV.ElectricityOut"}
        electricity = by_name["HeatPump.ElectricityIn"]
        assert (electricity.role, electricity.carrier, electricity.environment) == (CONSUMPTION, ELECTRICITY, None)
        assert (electricity.peer, electricity.peer_kind) == ("Meter", PeerKind.METER)
        assert electricity.energy_of_simulated_period_in_kwh == pytest.approx(1.2)
        assert electricity.energy_per_step_in_kwh is not None
        np.testing.assert_allclose(electricity.energy_per_step_in_kwh, [1.0, 0.2, 0.0])
        heat = by_name["HeatPump.HeatOut"]
        assert (heat.role, heat.carrier, heat.environment) == (PRODUCTION, SPACE_HEATING, lt.EnvironmentNode.HEAT_LOSS_TO_OUTDOORS)
        assert heat.peer_kind == PeerKind.BOUNDARY
        assert heat.energy_of_simulated_period_in_kwh == pytest.approx(4.8)
        pv = by_name["PV.ElectricityOut"]
        assert (pv.role, pv.peer, pv.energy_of_simulated_period_in_kwh) == (PRODUCTION, "Meter", pytest.approx(1.2))

    def test_the_meter_entry_is_what_the_meter_published_and_whether_it_agrees(self) -> None:
        """Catches a meter entry that reports what the ports moved instead of what the meter published.

        The meter publishes 2,800 W in the first 900 s step, 0.7 kWh, though its devices take only 0.6 kWh net: it
        does not agree, and the record says so with the published 0.7 kWh.
        """
        agreeing = house(through_the_manager=False).port_energies()
        disagreeing = house(metered_import_watt=(2800.0, 0.0, 0.0), through_the_manager=False).port_energies()

        (agreeing_meter,) = agreeing.meters
        assert (agreeing_meter.meter, agreeing_meter.carrier) == ("Meter", ELECTRICITY)
        assert (agreeing_meter.import_output, agreeing_meter.export_output) == ("FromGridInWatt", "ToGridInWatt")
        assert agreeing_meter.import_of_simulated_period_in_kwh == pytest.approx(0.6)
        assert agreeing_meter.export_of_simulated_period_in_kwh == pytest.approx(0.6)
        assert agreeing_meter.agrees is True
        (meter,) = disagreeing.meters
        assert meter.import_of_simulated_period_in_kwh == pytest.approx(0.7)
        assert meter.agrees is False

    def test_a_loop_the_balance_does_not_book_is_still_a_loop_tag(self) -> None:
        """Catches the loop tags being read from the balance's booked loops, which leaves out such an emitter.

        The emitter tags its space-heating loop and books no heat on it, as the heat distribution system without a
        buffer does; it still takes part in the heating, so its tag is in the record.
        """
        column = ("Load", "ElectricityIn", lt.Units.KWH, None, [0.1])
        record = report([column], extra_components=["Emitter"], classes={"Emitter": TaggingEmitter}).port_energies()

        assert record.loop_tags == (LoopTag("Emitter", "space_heating", SPACE_HEATING, supplies=True),)
        assert record.ports == ()

    def test_the_peer_kind_of_the_balance_is_the_record_s(self) -> None:
        """Catches a second peer-kind enum in the balance, which the record's peers would no longer compare with."""
        assert balance_ports.PeerKind is PeerKind


class TestTheFileSpelling:
    """``economic_inputs.json`` keeps the totals and spells every enum by its member name."""

    RECORD = PortEnergies(
        ports=(
            PortEnergy("HeatPump", "ElectricityIn", CONSUMPTION, ELECTRICITY, None, "Meter", PeerKind.METER, 1.2, np.array([1.0, 0.2, 0.0])),
            PortEnergy(
                "DHWStorage",
                "ThermalPowerConsumptionDHW",
                PRODUCTION,
                lt.EnergyBalanceCarrier.DOMESTIC_HOT_WATER_HEAT,
                lt.EnvironmentNode.HOT_WATER_USE,
                "hot water drawn at the taps",
                PeerKind.BOUNDARY,
                3036.6,
            ),
        ),
        loop_tags=(LoopTag("GasBoiler", "space_heating", SPACE_HEATING, supplies=True),),
        meters=(
            MeterEnergy("ElectricityMeter", ELECTRICITY, "ElectricityFromGridInWatt", "ElectricityToGridInWatt", 2500.0, 100.0, agrees=True),
            MeterEnergy("GasMeter", lt.EnergyBalanceCarrier.NATURAL_GAS, "GasFromGrid", None, 12000.0, 0.0, agrees=True),
        ),
    )

    def test_the_record_round_trips_without_its_steps(self) -> None:
        """Catches a field the file loses, and an enum written by its value, which another reader would not find."""
        written = self.RECORD.to_json()
        read = PortEnergies.from_json(written)

        assert read == self.RECORD
        assert read.ports[0].energy_per_step_in_kwh is None
        assert written["ports"][0] == {
            "subject": "HeatPump",
            "port": "ElectricityIn",
            "role": "CONSUMPTION",
            "carrier": "ELECTRICITY",
            "environment": None,
            "peer": "Meter",
            "peer_kind": "METER",
            "energy_of_simulated_period_in_kwh": 1.2,
        }
        assert written["ports"][1]["environment"] == "HOT_WATER_USE"
        assert written["meters"][1]["export_output"] is None

    def test_a_record_without_lists_reads_as_empty(self) -> None:
        """A run in which nothing declares a port writes empty lists; a reader must not need them."""
        assert PortEnergies.from_json({}) == PortEnergies()

    @pytest.mark.parametrize(
        "change, refusal",
        [
            ({"carrier": "electricity"}, "'electricity' is not a member name of EnergyBalanceCarrier"),
            ({"role": None}, "None is not a member name of EnergyRole"),
            ({"peer_kind": "NEIGHBOUR"}, "'NEIGHBOUR' is not a member name of PeerKind"),
        ],
    )
    def test_a_port_spelled_otherwise_is_refused(self, change: Dict[str, Any], refusal: str) -> None:
        """Catches a file in another spelling being read as a different carrier, role or peer, or silently as None."""
        raw = {**self.RECORD.ports[0].to_json(), **change}
        with pytest.raises(ValueError, match=refusal):
            PortEnergy.from_json(raw)

    def test_a_flag_that_is_not_a_boolean_is_refused(self) -> None:
        """Catches ``"false"`` read as true, which would make a disagreeing meter or a receiving loop end look otherwise."""
        with pytest.raises(ValueError, match="agrees='false'"):
            MeterEnergy.from_json({**self.RECORD.meters[0].to_json(), "agrees": "false"})
        with pytest.raises(ValueError, match="supplies=1"):
            LoopTag.from_json({**self.RECORD.loop_tags[0].to_json(), "supplies": 1})


class TestMemberNamed:
    """``PortEnergies.member_named`` reads an enum by its member name and nothing else."""

    def test_a_member_name_gives_the_member(self) -> None:
        """The normal case: the name of the member, in capitals."""
        assert PortEnergies.member_named(lt.EnergyBalanceCarrier, "DISTRICT_HEAT") is lt.EnergyBalanceCarrier.DISTRICT_HEAT

    @pytest.mark.parametrize("name", ["district_heat", "", 3, None])
    def test_a_value_an_empty_text_or_no_text_is_refused_with_the_names_it_could_be(self, name: Any) -> None:
        """Catches the value spelling being accepted, and a number or None being looked up at all."""
        with pytest.raises(ValueError, match="expected one of ELECTRICITY, "):
            PortEnergies.member_named(lt.EnergyBalanceCarrier, name)


class TestTheWayIn:
    """Post-processing hands the cost engine the record of the balance it judged."""

    def test_the_cost_engine_gets_the_balance_s_record(self) -> None:
        """Catches the cost engine being called without the record, or with one the balance did not compute."""
        balance = house(through_the_manager=False)
        parameters = SimulationParameters.full_year(year=2021, seconds_per_timestep=900)
        ppdt = SimpleNamespace(
            wrapped_components=[], all_outputs=[], results=None, simulation_parameters=parameters, post_processing_options=[]
        )
        with mock.patch("hisim.economics.bridge.compute_lifecycle_costs") as engine:
            PostProcessor().compute_lifecycle_costs(ppdt, balance)  # type: ignore[arg-type]

        assert engine.call_args.kwargs["port_energies"] == balance.port_energies()
