"""The isolation system of one assembly and one sample, and its one-day run (``assemblies_spec.md`` §9.4).

**The system.** The assembly is imported once, under the key :data:`SUBJECT`, with every parameter
of the sample written out. Every port that is active with those parameters and whose binding
changes what the assembly computes gets a test partner from the registry (:mod:`.partners`), with
the partners those read: a need its first registered partner class, a circuit end the other end
registered for its circuit and its members' classes, a carrier need its carrier's provider, a fuel
the assembly provides a consumer, a fact need a provider of the fact, an observer member a component
it observes, and a ``controllable: {target_input}`` output the controller ranking it. A sizing fact
a member's class reads that no member provides and no fact port names crosses the boundary by the
engine's bare-fact rule, as it does in the system that imports the assembly (§6), so it gets the
registered provider of that fact as well (:func:`facts_needed`). Optional
ports are bound like required ones, so the run exercises the whole interface the parameters offer;
the verb is written out (``bind:`` for a required port, ``optional-bind:`` for an optional one)
wherever the format has one. A port no partner serves refuses the build by name.

**The run.** One calculation through :func:`~hisim.energy_system.executor.run_energy_system`, the
entry point every run takes, in a fresh directory the caller names: the system file and its
simulation parameters (one day at 900 s, the KPIs written as JSON) are written there first, so the
directory reproduces the run until :meth:`IsolationRun.release` deletes it with the run. The
energy-balance check and every component's ``i_doublecheck`` run in every simulation. A run that
raises is captured with its error, one finding about the sample the checks report (:mod:`.checks`),
naming the innermost raising ``file.py:line`` so the reader sees whether the assembly or the harness
raised; an error of the harness before the run — a missing partner — is raised.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd
import yaml

from hisim.config import AddressStep, ComponentID
from hisim.config.contributions import declared_facts_of
from hisim.energy_system.assemblies.model import MemberTemplate
from hisim.energy_system.assemblies.parameters import select
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing.partners import ServedKey, TestPartnerRegistry
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.executor import run_energy_system
from hisim.energy_system.imports_model import Port, PortKind, PortState
from hisim.energy_system.model import EnergySystemFile
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder

#: The import key of the assembly under test in its isolation system.
SUBJECT = "subject"

#: The simulation parameters of every isolation run: one day at 900 s (an assembly declares no
#: resolution), errors-only logging, and the KPIs the declarations are read from.
SIMULATION_PARAMETERS: Mapping[str, Any] = {
    "start_date": "2021-01-01T00:00:00",
    "end_date": "2021-01-02T00:00:00",
    "seconds_per_timestep": 900,
    "country": "DE",
    "logging_level": 1,
    "post_processing_options": ["COMPUTE_KPIS", "WRITE_KPIS_TO_JSON"],
}


def partners_needed(  # pylint: disable=too-many-return-statements  # one return per kind of port
    port: Port, classes: Mapping[str, str]
) -> List[Tuple[ServedKey, ...]]:
    """What one active port needs beside it, each as its alternatives; empty for a port that only offers.

    Args:
        port: The port.
        classes: The class name of every member present with the sample's parameters.
    """
    if port.kind == PortKind.NEED:
        return [tuple(("partner", name) for name in port.partner)]
    if port.kind == PortKind.CIRCUIT:
        ends = frozenset(classes[member] for member in port.members if member in classes)
        return [(("circuit", port.circuit, ends),)]
    if port.kind == PortKind.CARRIER:
        if not port.is_provision:
            return [(("carrier", port.carrier),)]
        return [(("consumes", port.carrier),)] if port.is_fuel_provision else []
    if port.kind == PortKind.FACT:
        return [] if port.is_provision else [(("fact", port.fact),)]
    if port.kind == PortKind.OBSERVER:
        return [(("observed_by", classes[member]),) for member in port.into if member in classes]
    if "target_input" in port.controllable and port.output_member in classes:
        return [(("controls", classes[port.output_member]),)]
    return []


def facts_needed(port_facts: Sequence[str], members: Mapping[str, MemberTemplate]) -> List[Tuple[ServedKey, ...]]:
    """The facts the members' classes read that no member provides and no fact port names, each as its alternatives.

    Args:
        port_facts: The facts the assembly's fact ports name.
        members: The members present with the sample's parameters.
    """
    classes = [ClassBinder.config_class_of(name, member.entry) for name, member in members.items()]
    provided = {fact for config_class in classes for fact in declared_facts_of(config_class)}
    read = dict.fromkeys(fact for config_class in classes for fact in facts_read_by(config_class))
    return [(("fact", fact),) for fact in read if fact not in provided and fact not in port_facts]


def isolation_document(
    assembly: ResolvedAssembly, values: Mapping[str, Any], registry: TestPartnerRegistry
) -> Dict[str, Any]:
    """The energy-system document of one isolation system.

    Raises:
        EnergySystemAssemblyError: When the parameters do not fit the assembly (a bug of the sampler).
        TestPartnerMissingError: When an active port has no registered test partner.
    """
    model = assembly.model
    selection = select(model, assembly.label, values, f"the isolation system of '{assembly.path}'")
    classes = {name: member.entry.class_path.rsplit(".", 1)[-1] for name, member in selection.members.items()}
    partners: List[str] = []
    verbs: Dict[str, Dict[str, str]] = {"bind": {}, "optional-bind": {}}
    for name, port in model.ports.items():
        state = selection.state(port)
        if state == PortState.INACTIVE:
            continue
        for alternatives in partners_needed(port, classes):
            partner = registry.find(alternatives, f"the port '{name}'", assembly.path)
            partners.append(partner.name)
            if (
                port.kind in (PortKind.NEED, PortKind.CIRCUIT, PortKind.FACT)
                and not port.is_provision
                and not port.many
            ):
                verbs["bind" if state == PortState.REQUIRED else "optional-bind"][name] = partner.name
    port_facts = [port.fact for port in model.ports.values() if port.kind == PortKind.FACT and port.fact]
    for alternatives in facts_needed(port_facts, selection.members):
        partners.append(registry.find(alternatives, f"the fact read '{alternatives[0][1]}'", assembly.path).name)
    entry: Dict[str, Any] = {"assembly": assembly.path, "parameters": dict(values)}
    entry.update({verb: bound for verb, bound in verbs.items() if bound})
    return {
        "schema_version": EnergySystemFile.ASSEMBLIES_SCHEMA_VERSION,
        "name": f"isolation_{assembly.path.replace('/', '_')}",
        "description": f"The isolation system of {assembly.path} (assemblies_spec.md §9.4).",
        "components": registry.document(partners, f"the test partners of '{assembly.path}'"),
        "imports": {SUBJECT: entry},
    }


class IsolationRunError(Exception):
    """A check read an isolation run that was released, or has no results although it raised nothing: a harness bug."""


@dataclass
class IsolationRun:
    """What one isolation run produced; the checks read it, and :meth:`release` drops it.

    Attributes:
        label: How a failure names the run: ``mock/pv_array sample s003``.
        assembly: The assembly's library path.
        directory: The run's directory, holding its system, parameters, records and KPIs.
        error: What the run raised, or ``None`` when it finished.
        results: The result frame of a finished run.
        outputs: The simulator's outputs, matching the frame's columns.
        members: Member name of the assembly under test to its constructed component.
        runtime: Member name to its runtime name in the expanded system.
        released: Whether :meth:`release` dropped the run; no check reads it afterwards.
    """

    label: str
    assembly: str
    directory: Path
    error: Optional[Exception] = None
    results: Optional[pd.DataFrame] = None
    outputs: List[Any] = field(default_factory=list)
    members: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, str] = field(default_factory=dict)
    released: bool = False
    _finder: Optional[KpiFinder] = field(default=None, repr=False)

    def finder(self) -> KpiFinder:
        """The run's KPI collection, read from its ``all_kpis.json`` once.

        Raises:
            FileNotFoundError: When the run wrote none.
        """
        if self._finder is None:
            self._finder = KpiFinder(json.loads((self.directory / "all_kpis.json").read_text(encoding="utf-8")))
        return self._finder

    def release(self) -> None:
        """Drops the frame, the outputs, the components, the KPIs and the run's directory once its checks are done."""
        self.results = None
        self.outputs = []
        self.members = {}
        self._finder = None
        self.released = True
        if self.directory.exists():
            shutil.rmtree(self.directory)


def run_isolation(
    assembly: ResolvedAssembly,
    values: Mapping[str, Any],
    registry: TestPartnerRegistry,
    resolver: AssemblyResolver,
    directory: Path,
    label: str,
) -> IsolationRun:
    """Builds and runs the isolation system of one parameter set in a fresh directory.

    Raises:
        TestPartnerMissingError: When an active port has no registered test partner.
        TestPartnerRegistryError: When the partners' site entries do not read.
    """
    document = isolation_document(assembly, values, registry)
    directory.mkdir(parents=True, exist_ok=False)
    system = directory / "isolation.energy_system.yaml"
    parameters = directory / "isolation.simulation.yaml"
    system.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    parameters.write_text(yaml.safe_dump(dict(SIMULATION_PARAMETERS), sort_keys=False), encoding="utf-8")
    run = IsolationRun(label=label, assembly=assembly.path, directory=directory)
    try:
        built = run_energy_system(system, parameters, result_directory=str(directory), assembly_resolver=resolver)
    # Every failure of a run is a finding about the assembly, which the checks name with the sample.
    except Exception as error:  # pylint: disable=broad-exception-caught
        run.error = error
        return run
    run.results = built.simulator.results_data_frame
    run.outputs = list(built.simulator.all_outputs)
    run.runtime = members_of(built.imports.addresses)
    components = dict(built.wired.components)
    run.members = {member: components[name] for member, name in run.runtime.items()}
    return run


def members_of(addresses: Mapping[str, ComponentID]) -> Dict[str, str]:
    """The members of the assembly under test, member name to runtime name."""
    subject = (AddressStep(SUBJECT),)
    return {identity.name: name for name, identity in addresses.items() if tuple(identity.path) == subject}
