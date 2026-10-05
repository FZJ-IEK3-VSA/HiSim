"""The member contract: the parts of an assembly's test contract only its constructed members can check (§9.4).

The library check (:func:`~hisim.energy_system.assemblies.library.check_assembly`) reads the assembly
file and refuses what the file alone decides. A member's outputs, their load types and units, and
the KPIs it reports come into being when the member is constructed and run — no class declares them
a second time — so three rules of the test contract (D24) are checked here, on the members of the
isolation runs, before any ``bounds``, ``monotone`` or ``expect`` declaration is evaluated:

- **completeness**: every energy-carrying or temperature output of a member has a ``tests.bounds``
  entry (:meth:`MemberContract.carries_energy_or_temperature` decides which outputs);
- **units**: a ``bounds`` entry's unit is its output's unit, and the output exists;
- **KPI names**: a declaration's KPI is one its member reports, read from the member's
  ``get_component_kpi_entries`` on a finished run.

Every violation is a contract failure named by member and output (or KPI), and the harness then
evaluates no declaration of the assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd

from hisim import loadtypes as lt
from hisim.component import ComponentOutput
from hisim.energy_system.assemblies.model import TestContract


@dataclass(frozen=True)
class ContractViolation:
    """One violated rule of the member contract.

    Attributes:
        subject: What it is about: ``Member.Output`` or ``Member: KPI``.
        rule: The rule: ``bounds completeness``, ``bounds unit`` or ``KPI name``.
        message: The sentence a report prints.
    """

    subject: str
    rule: str
    message: str


class MemberContract:
    """Checks the member contract of one assembly against the constructed members of its isolation runs."""

    #: The units in which an output carries energy: a power or an energy, the units an
    #: ``EnergyPort`` accepts (``hisim/energy_port.py``).
    ENERGY_UNITS: ClassVar[FrozenSet[lt.Units]] = frozenset(
        {
            lt.Units.WATT,
            lt.Units.KILOWATT,
            lt.Units.WATT_HOUR,
            lt.Units.KWH,
            lt.Units.KWH_PER_TIMESTEP,
            lt.Units.JOULE,
            lt.Units.KILOJOULE,
        }
    )

    #: The units of a temperature.
    TEMPERATURE_UNITS: ClassVar[FrozenSet[lt.Units]] = frozenset({lt.Units.CELSIUS, lt.Units.KELVIN})

    @classmethod
    def carries_energy_or_temperature(cls, load_type: lt.LoadTypes, unit: lt.Units) -> bool:
        """Whether an output of this load type and unit carries energy or a temperature (§9.4, D24).

        An output carries energy when its unit is a power or an energy (W, kW, Wh, kWh, kWh per
        timestep, J, kJ); it carries a temperature when its load type is ``TEMPERATURE`` or its
        unit is °C or K.
        """
        return unit in cls.ENERGY_UNITS or unit in cls.TEMPERATURE_UNITS or load_type == lt.LoadTypes.TEMPERATURE

    def __init__(self, tests: Optional[TestContract]) -> None:
        """Prepares the check of one assembly's test contract."""
        self.tests = tests
        self.violations: List[ContractViolation] = []
        self._seen: Set[Tuple[str, str]] = set()

    def _violate(self, subject: str, rule: str, message: str) -> None:
        """Records a violation once, however many runs show it."""
        if (subject, rule) in self._seen:
            return
        self._seen.add((subject, rule))
        self.violations.append(ContractViolation(subject=subject, rule=rule, message=message))

    def bounded_outputs(self) -> Dict[Tuple[str, str], Optional[str]]:
        """``(member, output)`` to the unit its bounds entry states."""
        bounded: Dict[Tuple[str, str], Optional[str]] = {}
        for bounds in self.tests.bounds if self.tests is not None else ():
            if bounds.output is not None:
                member, output = bounds.output.split(".", 1)
                bounded[(member, output)] = bounds.unit
        return bounded

    def check_outputs(self, members: Mapping[str, Any]) -> None:
        """Completeness and units on the constructed members of one run.

        Args:
            members: Member name to the constructed component.
        """
        bounded = self.bounded_outputs()
        for member, component in members.items():
            outputs: Dict[str, ComponentOutput] = {output.field_name: output for output in component.outputs}
            for name, output in outputs.items():
                subject = f"{member}.{name}"
                if (member, name) not in bounded and self.carries_energy_or_temperature(output.load_type, output.unit):
                    self._violate(
                        subject,
                        "bounds completeness",
                        f"the {output.unit.name} output '{subject}' ({output.load_type.name}) has no bounds entry.",
                    )
            for (bounded_member, name), unit in bounded.items():
                if bounded_member != member:
                    continue
                subject = f"{member}.{name}"
                bounded_output = outputs.get(name)
                if bounded_output is None:
                    self._violate(
                        subject,
                        "bounds unit",
                        f"the bounds entry names '{subject}', which is no output of the constructed {member} "
                        f"(it has: {', '.join(sorted(outputs)) or 'none'}).",
                    )
                elif unit != bounded_output.unit.name:
                    self._violate(
                        subject,
                        "bounds unit",
                        f"the bounds entry states {unit} for '{subject}', whose unit is {bounded_output.unit.name}.",
                    )

    def kpi_declarations(self) -> List[Tuple[str, str]]:
        """Every ``(member, KPI)`` a bounds, monotone or expect declaration names."""
        named: List[Tuple[str, str]] = []
        if self.tests is None:
            return named
        for bounds in self.tests.bounds:
            if bounds.kpi is not None:
                named.append((bounds.member or "", bounds.kpi))
        # A monotone entry without a member names a derived KPI, which no member reports; its run's
        # lookup checks it (:func:`~.checks.kpi_value`).
        named.extend(
            (monotone.member, monotone.kpi) for monotone in self.tests.monotone if monotone.member is not None
        )
        named.extend((expect.member, expect.kpi) for expect in self.tests.expect)
        return list(dict.fromkeys(named))

    def check_kpis(
        self, members: Mapping[str, Any], all_outputs: Sequence[Any], results: pd.DataFrame
    ) -> Set[Tuple[str, str]]:
        """KPI names on the constructed members of one finished run, read from ``get_component_kpi_entries``.

        Args:
            members: Member name to the constructed component.
            all_outputs: The run's outputs, matching the result frame's columns.
            results: The run's result frame.

        Returns:
            The ``(member, KPI)`` pairs this run could check (its members include the member).
        """
        checked: Set[Tuple[str, str]] = set()
        reported: Dict[str, List[str]] = {}
        for member, kpi in self.kpi_declarations():
            component = members.get(member)
            if component is None:
                continue
            if member not in reported:
                reported[member] = [
                    entry.name for entry in component.get_component_kpi_entries(list(all_outputs), results)
                ]
            checked.add((member, kpi))
            if kpi not in reported[member]:
                self._violate(
                    f"{member}: {kpi}",
                    "KPI name",
                    f"'{member}' reports no KPI '{kpi}' (it reports: {', '.join(reported[member]) or 'none'}).",
                )
        return checked
