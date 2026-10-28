"""The isolation system of one assembly and one sample, and its one-day run (``assemblies_spec.md`` §9.4).

**The system.** The assembly is imported once, under the key :data:`SUBJECT`, with every parameter
of the sample written out. Every port that is active with those parameters and whose binding
changes what the assembly computes gets a test partner from the registry (:mod:`.partners`), with
the partners those read: a need its first registered partner class (or the partner of that class a
circuit end of the assembly brings, as the cylinder a collector charges is the one its controller
reads, the engine's default rule binding the one candidate in scope), a circuit end the other end
registered for its circuit and its members' classes, a carrier need its carrier's provider, a fuel
the assembly provides a consumer, a fact need a provider of the fact, an observer member a component
it observes, and a ``controllable: {target_input}`` output the controller ranking it. A partner that another
partner requires does not join when a member of the assembly has its class: the member meets the requirement
(:meth:`~.partners.TestPartnerRegistry.closure`), as the energy manager under test is the one the grid meter
observes. A sizing fact
a member's class reads that no member provides and no fact port names crosses the boundary by the
engine's bare-fact rule, as it does in the system that imports the assembly (§6), so it gets the
registered provider of that fact as well (:func:`add_fact_providers`, which recomputes what is
still needed after each provider joins), unless a partner already in the
system contributes it by its class, as the consumer of a fuel contributes the carrier its meter
copies: the engine binds the fact there, and sizing refuses a second provider of the fact, of any
value, since the meter's law then has no one provider to read. Optional
ports are bound like required ones, so the run exercises the whole interface the parameters offer;
the verb is written out (``bind:`` for a required port, ``optional-bind:`` for an optional one)
wherever the format has one. A port no partner serves refuses the build by name.

**The meters.** Every flow of electricity or of a bought carrier passes a meter (owner decision Q-J of
``roadmap/complete_energy_sankeys_spec.md``), and the energy balance fails a run with a metered port no meter
reads once no component is undeclared (Q-H). An isolation system therefore meters every carrier its components
move through a metered port: the harness builds the system once without running it, reads the carriers of the
metered output ports of its constructed components (:func:`unmetered_carriers`), and for each carrier no component
of the system meters adds the partner the registry serves the carrier with, ``{carrier: <carrier>}``, the same
provider a carrier need binds (:func:`carriers_to_meter`). Example: beside a boiler, the residents' cylinder brings
the occupancy, whose electricity no meter reads, so the system gets the registry's electricity meter, ``Grid``. A
carrier with no registered provider refuses the build by name, as a port does. The probe build is the one way to
know the carriers, since a component declares its energy ports when it is constructed.

**The run.** One calculation through :func:`~hisim.energy_system.executor.run_energy_system`, the
entry point every run takes, in a fresh directory the caller names: the system file and its
simulation parameters (one day at 900 s, the KPIs written as JSON) are written there first, so the
directory reproduces the run until :meth:`IsolationRun.release` deletes it with the run. The
energy-balance check and every component's ``i_doublecheck`` run in every simulation. A run that
raises is captured with its error, one finding about the sample the checks report (:mod:`.checks`),
naming the innermost raising ``file.py:line`` so the reader sees whether the assembly or the harness
raised; an error of the harness before the run — a missing partner — is raised.

**A refused sample.** An assembly's parameter box may contain combinations a member's component
refuses at construction, such as a heat pump's W55 SCOP above its W35 SCOP: each value lies in its
range, their combination does not exist. The component raises
:class:`~hisim.config.ConfigurationRefusedError`, the build wraps it as its cause, and
:attr:`IsolationRun.refusal` finds it there. The checks count such a run as handled, not failed
(:class:`~.checks.SampleRefused`); any other error stays a failure.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd
import yaml

from hisim.calculation_scope import CalculationScope
from hisim.component import Component
from hisim.config import AddressStep, ComponentID, ConfigurationRefusedError
from hisim.config.contributions import declared_facts_of
from hisim.energy_system.assemblies.model import MemberTemplate
from hisim.energy_system.assemblies.parameters import select
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing.partners import (
    ServedKey,
    TestPartner,
    TestPartnerRegistry,
    TestPartnerRegistryError,
)
from hisim.energy_system.bindings import facts_read_by
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system, run_energy_system
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


def facts_declared_by(config_classes: Iterable[type]) -> Set[str]:
    """The sizing facts any of the configuration classes declares it contributes.

    Example: ``{GenericBoilerConfig}`` gives ``energy_carrier``, the fuel's heating value and
    density, and the boiler's power band.

    Args:
        config_classes: Configuration dataclasses.

    Returns:
        The union of their declared facts.
    """
    return {fact for config_class in config_classes for fact in declared_facts_of(config_class)}


def facts_needed(
    port_facts: Collection[str], members: Mapping[str, MemberTemplate], partner_facts: Collection[str]
) -> List[Tuple[ServedKey, ...]]:
    """The facts the members' classes read that no member, no fact port and no partner present provides.

    Example: a battery member whose law reads ``pv_peak_power_in_watt``, with no fact port and no
    array among the partners, needs ``(("fact", "pv_peak_power_in_watt"),)``; with an array partner
    it needs nothing.

    Args:
        port_facts: The facts the assembly's active fact ports name.
        members: The members present with the sample's parameters.
        partner_facts: The facts the classes of the partners already in the system contribute.

    Returns:
        Each fact still to be provided, as its alternatives: sorted per member class, the members in
        declaration order, each fact once.
    """
    classes = [ClassBinder.config_class_of(name, member.entry) for name, member in members.items()]
    provided = facts_declared_by(classes)
    read = dict.fromkeys(fact for config_class in classes for fact in facts_read_by(config_class))
    return [
        (("fact", fact),)
        for fact in read
        if fact not in provided and fact not in port_facts and fact not in partner_facts
    ]


def facts_of_partners(
    registry: TestPartnerRegistry, names: Sequence[str], member_classes: Collection[str] = ()
) -> Set[str]:
    """The sizing facts the classes of the partners and of every partner they require contribute.

    Example: an oil boiler partner contributes ``energy_carrier``, so a fuel meter beside it reads
    its carrier from it and needs no provider of its own.

    Args:
        registry: The test partners.
        names: The partners in the system so far.
        member_classes: The short class names of the subject's members: a requirement of one of these classes is met
            by the member and does not join (:meth:`TestPartnerRegistry.closure`), so it contributes nothing here.

    Returns:
        The union of the facts their classes declare.
    """
    return facts_declared_by(
        registry.config_class_of(name) for name in registry.closure(names, member_classes)
    )


def add_fact_providers(
    partners: List[str],
    port_facts: Collection[str],
    members: Mapping[str, MemberTemplate],
    registry: TestPartnerRegistry,
    assembly: str,
) -> None:
    """Appends to ``partners`` the registered provider of each fact still unprovided, until none is.

    The facts are recomputed after each provider joins, because a provider's class may contribute
    more than the fact it was found for: a building found for the heating load also contributes
    the number of apartments, so the partner registered for that fact is never added beside it:
    sizing refuses a fact with two providers.

    Args:
        partners: The partners in the system so far; extended in place.
        port_facts: The facts the assembly's active fact ports name.
        members: The members present with the sample's parameters.
        registry: The test partners.
        assembly: The assembly under test, for the messages.

    Raises:
        TestPartnerMissingError: When no partner is registered for a fact still needed.
        TestPartnerRegistryError: When the partner registered for a fact does not contribute it.
    """
    added: Set[str] = set()
    member_classes = [member.entry.class_path.rsplit(".", 1)[-1] for member in members.values()]
    while True:
        needed = facts_needed(port_facts, members, facts_of_partners(registry, partners, member_classes))
        if not needed:
            return
        fact = needed[0][0][1]
        partner = registry.find(needed[0], f"the fact read '{fact}'", assembly)
        if fact in added:
            raise TestPartnerRegistryError(
                f"the test partner '{partner.name}' is registered as the provider of the fact {fact} "
                f"({partner.origin}), but its class does not contribute it."
            )
        added.add(fact)
        partners.append(partner.name)


def brought_by_circuit_end(
    need: str,
    alternatives: Tuple[ServedKey, ...],
    circuit_ends: Mapping[Tuple[str, Tuple[ServedKey, ...]], TestPartner],
    assembly: str,
) -> Optional[TestPartner]:
    """The partner of a need's class that a circuit end of the assembly brings, or ``None`` when none brings one.

    Example: the need ``cylinder_temperature`` of ``heating/solar_thermal`` names the class
    ``SimpleDHWStorage``, and the ``solar_coil`` circuit end brings ``SolarCylinder``, a
    ``SimpleDHWStorage``: the need binds ``SolarCylinder``. Only a ``("partner", class)``
    alternative is matched, by the short class name of each brought partner's component, the first
    alternative with a match deciding. One partner brought by two circuit ends is one candidate.

    Args:
        need: The port that needs the partner, for the message.
        alternatives: What would serve the port, in order.
        circuit_ends: The partner each active circuit end brings, keyed by the end's port name and its alternatives.
        assembly: The assembly under test, for the message.

    Raises:
        TestPartnerRegistryError: When two circuit ends bring two different partners of the class:
            the binding is ambiguous, as the engine's default rule refuses two candidates.
    """
    for key in alternatives:
        if key[0] != "partner":
            continue
        brought: Dict[str, Tuple[str, TestPartner]] = {}
        for (port, _), partner in circuit_ends.items():
            if partner.component["class"].rsplit(".", 1)[-1] == key[1]:
                brought.setdefault(partner.name, (port, partner))
        if len(brought) > 1:
            candidates = " and ".join(f"'{end.name}' through the port '{port}'" for port, end in brought.values())
            raise TestPartnerRegistryError(
                f"the port '{need}' of '{assembly}' needs a partner of the class {key[1]}, and {len(brought)} circuit "
                f"ends bring one: {candidates}. The binding is ambiguous, as the engine's default rule refuses two "
                "candidates."
            )
        if brought:
            return next(iter(brought.values()))[1]
    return None


def isolation_document(
    assembly: ResolvedAssembly,
    values: Mapping[str, Any],
    registry: TestPartnerRegistry,
    metered_carriers: Sequence[str] = (),
) -> Dict[str, Any]:
    """The energy-system document of one isolation system.

    Besides the partners its ports and facts need, the system holds the registered provider of every carrier in
    ``metered_carriers``, the meter :func:`carriers_to_meter` finds the system lacking (see the module docstring).
    Example: ``heating/oil_boiler`` with ``metered_carriers=["electricity"]`` gains the partner ``Grid``, the
    electricity meter, and the energy manager it requires.

    A need binds the partner of its class that an active circuit end of the same assembly brings,
    if there is one: the cylinder a collector charges is the cylinder its controller reads, as the
    engine's default rule binds the one candidate in scope. Example: ``heating/solar_thermal``'s
    ``cylinder_temperature`` need names ``SimpleDHWStorage``; its ``solar_coil`` circuit end brings
    the partner ``SolarCylinder`` of that class, so the need binds ``SolarCylinder`` instead of the
    registry's own ``SimpleDHWStorage`` partner, and the system holds one cylinder.

    Args:
        assembly: The assembly under test.
        values: The sample's parameters.
        registry: The test partners.
        metered_carriers: The carriers whose registered provider joins as the system's meter, by their values
            (``electricity``); none by default.

    Raises:
        EnergySystemAssemblyError: When the parameters do not fit the assembly (a bug of the sampler).
        TestPartnerMissingError: When an active port, or a carrier of ``metered_carriers``, has no registered test
            partner.
        TestPartnerRegistryError: When a need's partner class is brought by two circuit ends as two
            different partners: the binding is ambiguous, as the engine's default rule refuses two
            candidates.
    """
    model = assembly.model
    selection = select(model, assembly.label, values, f"the isolation system of '{assembly.path}'")
    classes = {name: member.entry.class_path.rsplit(".", 1)[-1] for name, member in selection.members.items()}
    partners: List[str] = []
    verbs: Dict[str, Dict[str, str]] = {"bind": {}, "optional-bind": {}}
    # The partners the active circuit ends bring, found once: a need reads them below (see the docstring).
    circuit_ends = {
        (name, alternatives): registry.find(alternatives, f"the port '{name}'", assembly.path)
        for name, port in model.ports.items()
        if port.kind == PortKind.CIRCUIT and selection.state(port) != PortState.INACTIVE
        for alternatives in partners_needed(port, classes)
    }
    for name, port in model.ports.items():
        state = selection.state(port)
        if state == PortState.INACTIVE:
            continue
        for alternatives in partners_needed(port, classes):
            partner = (
                circuit_ends.get((name, alternatives))
                or brought_by_circuit_end(name, alternatives, circuit_ends, assembly.path)
                or registry.find(alternatives, f"the port '{name}'", assembly.path)
            )
            # A need bound to a partner a circuit end brings names a partner already in the system.
            if partner.name not in partners:
                partners.append(partner.name)
            if (
                port.kind in (PortKind.NEED, PortKind.CIRCUIT, PortKind.FACT)
                and not port.is_provision
                and not port.many
            ):
                verbs["bind" if state == PortState.REQUIRED else "optional-bind"][name] = partner.name
    # An inactive fact port names nothing: its fact crosses by the bare-fact rule like any unported read.
    port_facts = [
        port.fact
        for port in model.ports.values()
        if port.kind == PortKind.FACT and port.fact and selection.state(port) != PortState.INACTIVE
    ]
    for carrier in metered_carriers:
        meter = registry.find((("carrier", carrier),), f"the metered carrier '{carrier}'", assembly.path)
        if meter.name not in partners:
            partners.append(meter.name)
    add_fact_providers(partners, port_facts, selection.members, registry, assembly.path)
    entry: Dict[str, Any] = {"assembly": assembly.path, "parameters": dict(values)}
    entry.update({verb: bound for verb, bound in verbs.items() if bound})
    # A partner's requirement a member of the subject has the class of is met by that member (partners.py).
    components = registry.document(partners, f"the test partners of '{assembly.path}'", classes.values())
    bind_optional_partner_ports(components, classes)
    return {
        "schema_version": EnergySystemFile.ASSEMBLIES_SCHEMA_VERSION,
        "name": f"isolation_{assembly.path.replace('/', '_')}",
        "description": f"The isolation system of {assembly.path} (assemblies_spec.md §9.4).",
        "components": components,
        "imports": {SUBJECT: entry},
    }


def bind_optional_partner_ports(components: Dict[str, Dict[str, Any]], member_classes: Mapping[str, str]) -> None:
    """Write ``optional-bind:`` for every optional partner port of a test partner that has exactly one candidate.

    A test partner stands beside every assembly whose ports it serves, so the component its optional port reads may
    sit in the subject in one isolation system, among the other partners in another, and nowhere in a third. One
    verb in the registry cannot name all three, and the engine refuses an optional port with a candidate and no verb.
    The harness therefore writes the verb per system, as it does for the subject's own optional ports: the subject
    when exactly one member of the subject has the port's partner class and no other partner has it, the partner when
    exactly one other partner has it and no member does, and no verb when none has it (the port stays unbound).
    Several candidates write no verb either, and the engine refuses the binding as ambiguous. A port the entry already
    gives a verb keeps it. Example: the heat distribution controller's ``buffer`` port (partner
    ``SimpleHotWaterStorage``) binds the subject beside a heating assembly with a buffer, the partner ``Buffer``
    beside a hot-water cylinder whose heat pump charges one, and nothing beside district heating. A port naming several
    partner classes counts a candidate of any of them: the building's ``heating`` port (``HeatDistribution`` or
    ``ElectricHeating``) binds the subject's direct heater beside ``heating/electric_resistive`` and the partner
    ``HeatDistributionSystem`` beside a boiler.

    Args:
        components: The partners' site entries by name, changed in place.
        member_classes: The class name of every member of the subject, by member name.
    """
    for name, component in components.items():
        written = {
            port
            for verb in ("bind", "optional-bind")
            for port in (component.get(verb) or {})
        } | set(component.get("none") or ())
        for port_name, port in (component.get("ports") or {}).items():
            if not isinstance(port, Mapping) or not port.get("optional") or "partner" not in port:
                continue
            if port_name in written:
                continue
            partner = port["partner"]
            wanted = set(partner) if isinstance(partner, list) else {partner}
            in_subject = sum(1 for class_name in member_classes.values() if class_name in wanted)
            in_partners = [
                other
                for other, entry in components.items()
                if other != name and str(entry.get("class", "")).rsplit(".", 1)[-1] in wanted
            ]
            if in_subject == 1 and not in_partners:
                component.setdefault("optional-bind", {})[port_name] = SUBJECT
            elif in_subject == 0 and len(in_partners) == 1:
                component.setdefault("optional-bind", {})[port_name] = in_partners[0]


def refusal_in(error: Optional[BaseException]) -> Optional[ConfigurationRefusedError]:
    """The configuration refusal an error was raised from, following its explicit causes; ``None`` when there is none.

    Example: a component's constructor raises ``ConfigurationRefusedError``; the build re-raises it
    as an ``EnergySystemWiringError`` (``EF-33``) ``from`` the refusal, so the refusal is that
    error's ``__cause__``. Only ``__cause__`` is followed: an error raised while another was being
    handled (``__context__``) is a failure of its own, not a refusal.

    Args:
        error: What a run raised, or ``None``.
    """
    seen: Set[int] = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, ConfigurationRefusedError):
            return error
        seen.add(id(error))
        error = error.__cause__
    return None


class IsolationRunError(Exception):
    """A check read an isolation run that was released, or has no results although it raised nothing: a harness bug."""


@dataclass
class IsolationRun:
    """What one isolation run produced; the checks read it, and :meth:`release` drops it.

    Attributes:
        label: How a failure names the run: ``pv/array sample s003``.
        assembly: The assembly's library path.
        directory: The run's directory, holding its system, parameters, records and KPIs.
        error: What the run raised, or ``None`` when it finished.
        results: The result frame of a finished run.
        outputs: The simulator's outputs, matching the frame's columns.
        members: Member name of the assembly under test to its constructed component.
        components: Runtime name to constructed component, for every component of the system: the subject's members
            and the test partners (the heat checks read the partners, :class:`~.checks.HeatDelivery`).
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
    components: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, str] = field(default_factory=dict)
    released: bool = False
    _finder: Optional[KpiFinder] = field(default=None, repr=False)

    @property
    def refusal(self) -> Optional[ConfigurationRefusedError]:
        """The refusal of a member's component the run's error was raised from, or ``None`` (:func:`refusal_in`)."""
        return refusal_in(self.error)

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
        self.components = {}
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

    The system holds a meter for every carrier its components move through a metered port
    (:func:`carriers_to_meter`, which builds the system once without the meters to find them).

    Raises:
        TestPartnerMissingError: When an active port, or a carrier to meter, has no registered test partner.
        TestPartnerRegistryError: When the partners' site entries do not read.
    """
    document = isolation_document(assembly, values, registry)
    directory.mkdir(parents=True, exist_ok=False)
    metered = carriers_to_meter(document, directory / "meter_probe", resolver)
    if metered:
        document = isolation_document(assembly, values, registry, metered)
    system, parameters = write_isolation_files(document, directory)
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
    run.components = components
    return run


def write_isolation_files(document: Mapping[str, Any], directory: Path) -> Tuple[Path, Path]:
    """Writes an isolation system and the simulation parameters of its run into a directory.

    Args:
        document: The isolation system (:func:`isolation_document`).
        directory: Where both files go; it exists.

    Returns:
        The paths of the system file and of the parameters file.
    """
    system = directory / "isolation.energy_system.yaml"
    parameters = directory / "isolation.simulation.yaml"
    system.write_text(yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=True), encoding="utf-8")
    parameters.write_text(yaml.safe_dump(dict(SIMULATION_PARAMETERS), sort_keys=False), encoding="utf-8")
    return system, parameters


def unmetered_carriers(components: Iterable[Component]) -> List[str]:
    """The carriers the components move through a metered port and no component of them meters, sorted.

    A metered port (:attr:`~hisim.energy_port.EnergyPort.is_metered`) is a ``CONSUMPTION`` or ``PRODUCTION`` output
    port of electricity or of a bought carrier; a component meters the carriers of its
    :meth:`~hisim.component.Component.metered_exchanges`. Example: an occupancy whose electricity port is metered and
    an oil boiler whose heating-oil port is, beside a fuel meter of heating oil, give ``["electricity"]``.

    Args:
        components: The constructed components of one system.

    Returns:
        The carriers' values, each once.
    """
    components = list(components)
    moved = {
        output.energy_port.carrier.value
        for component in components
        for output in component.outputs
        if output.energy_port is not None and output.energy_port.is_metered
    }
    metered = {exchange.carrier.value for component in components for exchange in component.metered_exchanges()}
    return sorted(moved - metered)


def carriers_to_meter(document: Mapping[str, Any], directory: Path, resolver: AssemblyResolver) -> List[str]:
    """The carriers an isolation system moves through a metered port and meters nowhere, found by building it once.

    The system is built in a calculation of its own in ``directory``, without running a step, and the directory is
    deleted afterwards; its constructed components give the carriers (:func:`unmetered_carriers`). A system that
    does not build gives none: its run raises the same error, which the checks report as the finding about the
    sample (a member refusing the sample at construction, for example).

    Args:
        document: The isolation system without the meters (:func:`isolation_document`).
        directory: A directory that does not exist yet, for the build.
        resolver: The library the system's import resolves in.

    Returns:
        The carriers' values, sorted.
    """
    directory.mkdir(parents=True, exist_ok=False)
    try:
        system, parameters_path = write_isolation_files(document, directory)
        with CalculationScope.open(label=f"the meter probe of {system}", run_directory=str(directory)):
            parameters = SimulationParametersReader.read(parameters_path)
            parameters.result_directory = str(directory)
            built = build_energy_system(system, parameters, assembly_resolver=resolver)
            return unmetered_carriers(component for _, component in built.wired.components)
    # The run of the same system raises the same error, and the checks report it with the sample.
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def members_of(addresses: Mapping[str, ComponentID]) -> Dict[str, str]:
    """The members of the assembly under test, member name to runtime name."""
    subject = (AddressStep(SUBJECT),)
    return {identity.name: name for name, identity in addresses.items() if tuple(identity.path) == subject}
