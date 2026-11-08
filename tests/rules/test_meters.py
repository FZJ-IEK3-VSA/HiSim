"""Direct tests of the meter rules after the run, on the hand-built houses of the energy-balance tests.

``house`` (``tests/test_energy_balance.py``) is a heat pump and a PV system whose electricity a meter reads, three
15-minute steps; ``boiler`` a gas boiler with or without its gas meter. Both rules refuse only while no component of
the run is undeclared (owner decision Q-H), which an extra component without ports switches off. The demo waiver of
``MT-02`` is tested with the rule's ``WAIVED_FOR`` emptied, which shows the waiver, not the rule, is what a demo
relies on.
"""

from types import MappingProxyType

import pytest

from hisim.setup_rules.kinds import SetupKind
from hisim.setup_rules.meters import MeterBalanceAgrees, MeteredPortHasAMeter
from hisim.setup_rules.results import EnergyBalanceError
from tests import functions_for_testing as fft
from tests.test_energy_balance import boiler, house

pytestmark = pytest.mark.base


class TestMeterBalanceAgrees:
    """``MT-01``: a meter publishes what it reads, in a run without undeclared components."""

    def test_a_meter_that_publishes_more_than_it_reads_is_a_fault_and_one_that_agrees_none(self) -> None:
        """Catches 0.4 kWh appearing at the meter, and a refusal of a meter that publishes exactly what its devices take."""
        (fault,) = MeterBalanceAgrees().check(fft.AfterRunRules.subject(house(metered_import_watt=(4000.0, 0.0, 0.0))))

        assert (fault.code, fault.component, fault.port) == ("MT-01", "Meter", "FromGridInWatt")
        assert "meter Meter publishes 1 kWh of electricity imported and 0.6 kWh exported" in fault.message
        assert "(tolerance: 0.1 % of the throughput over the run" in fault.message
        assert not MeterBalanceAgrees().check(fft.AfterRunRules.subject(house()))

    def test_while_a_component_is_undeclared_a_gap_is_only_reported(self) -> None:
        """Catches the Q-H switch lost in the move: a meter may read a flow no port declares while a component is undeclared."""
        balance = house(metered_import_watt=(4000.0, 0.0, 0.0), extra_components=["Occupancy"])

        assert not MeterBalanceAgrees().check(fft.AfterRunRules.subject(balance))
        fft.AfterRunRules.refuse(balance)

    def test_its_fault_refuses_the_run_as_energy_balance_error(self) -> None:
        """Catches a meter fault an ``except EnergyBalanceError`` would miss."""
        with pytest.raises(EnergyBalanceError, match="MT-01 meter-balance: meter Meter publishes"):
            fft.AfterRunRules.refuse(house(metered_import_watt=(4000.0, 0.0, 0.0)))


class TestMeteredPortHasAMeter:
    """``MT-02``: a bought carrier's port passes a meter, in a run without undeclared components; a demo waives it."""

    def test_a_gas_port_no_meter_reads_is_a_fault_and_a_metered_one_none(self) -> None:
        """Catches gas bought past the meter, and a refusal of a boiler whose gas meter reads it."""
        (fault,) = MeteredPortHasAMeter().check(fft.AfterRunRules.subject(boiler(metered=False)))

        assert (fault.code, fault.component, fault.port) == ("MT-02", "Boiler", "GasIn")
        assert "port Boiler.GasIn (consumption, natural_gas, 1 kWh) passes no meter: its peer is natural gas supply" in fault.message
        assert not MeteredPortHasAMeter().check(fft.AfterRunRules.subject(boiler(metered=True)))

    def test_while_a_component_is_undeclared_an_unmetered_port_is_only_reported(self) -> None:
        """Catches the Q-H switch lost in the move: the wiring is not complete while a component declares no port."""
        balance = boiler(metered=False, extra_components=["Weather"])

        assert not MeteredPortHasAMeter().check(fft.AfterRunRules.subject(balance))
        assert balance.meter_check.unmetered

    @pytest.mark.parametrize("kind", [SetupKind.RESIDENTIAL, SetupKind.COMMERCIAL, SetupKind.PLANT])
    def test_every_kind_but_a_demo_is_refused(self, kind: SetupKind) -> None:
        """Catches the waiver reaching a kind it does not name."""
        with pytest.raises(EnergyBalanceError, match=r"MT-02 metered-port-has-a-meter: port Boiler\.GasIn"):
            fft.AfterRunRules.refuse(boiler(metered=False), kind)

    def test_a_demo_runs_with_an_unmetered_port(self) -> None:
        """Catches the rule refusing a demonstration that leaves a bought carrier unmetered."""
        fft.AfterRunRules.refuse(boiler(metered=False), SetupKind.DEMO)

    def test_without_its_waiver_the_rule_refuses_the_demo_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a demo that passes because the rule skips it some other way, not because of the waiver it declares."""
        monkeypatch.setattr(MeteredPortHasAMeter, "WAIVED_FOR", MappingProxyType({}))

        with pytest.raises(EnergyBalanceError, match=r"MT-02 metered-port-has-a-meter: port Boiler\.GasIn"):
            fft.AfterRunRules.refuse(boiler(metered=False), SetupKind.DEMO)

    def test_the_waiver_states_its_reason(self) -> None:
        """Catches a waiver whose reason drifted from the one the spec decided (§4.2)."""
        assert MeteredPortHasAMeter.WAIVED_FOR == {
            SetupKind.DEMO: "a demo may leave a bought carrier unmetered; the balance reports the port as unmetered"
        }
