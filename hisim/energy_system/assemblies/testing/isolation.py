"""The isolation system of one assembly and one sample, and its one-day run (``assemblies_spec.md`` §9.4).

**The system.** The assembly is imported once, under the key :data:`SUBJECT`, with the sample's
parameters (its preset where it has one, and every value that differs from what the preset or the
defaults give). Every port the import offers is resolved for those parameters by the expansion
itself (:meth:`~hisim.energy_system.assemblies.expansion.ImportExpander.offered_ports`), and every
active port whose binding changes what the assembly computes gets a test partner from the registry
(:mod:`.partners`), with the partners those read:

- a need — required or optional — gets a component of its first registered partner class;
- a circuit end — required or optional — gets the other end registered for its circuit and the
  classes of its members;
- a carrier need gets the carrier's registered provider, and a carrier the assembly provides gets
  a registered consumer (a fuel provider nobody consumes is refused, §5.2);
- a fact need gets a component whose class contributes the fact.

A provided output or a provided fact only offers a value to others and gets no partner. Optional
ports are bound like required ones: the isolation run exercises the whole interface the assembly
offers with these parameters, and an optional end its members cannot run without would otherwise
stay untested. The verbs are written out — ``bind:`` for a required port, ``optional-bind:`` for
an optional one — except for an electricity need and a provision, which the format binds by its
default rule alone (§3.2, §4.3). A port no registered partner serves refuses the build
(:class:`~.errors.TestPartnerMissingError`).

**The run.** One calculation (:class:`~hisim.calculation_scope.CalculationScope`) in a fresh
directory under the harness's output root: the system file and the simulation parameters are
written there, the system is built against the harness's own library resolver, its records are
written and its timesteps run. The energy-balance check and every component's ``i_doublecheck``
run in every simulation; a failure of the run is captured with its kind rather than raised, so that
the harness can report it and go on (:mod:`.checks`).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
import yaml

from hisim.calculation_scope import CalculationScope
from hisim.config import AddressStep, ComponentID
from hisim.energy_system.assemblies.expansion import ImportExpander, OfferedPort
from hisim.energy_system.assemblies.resolver import AssemblyResolver, ResolvedAssembly
from hisim.energy_system.assemblies.testing.errors import HarnessUsageError, SamplerError
from hisim.energy_system.assemblies.testing.partners import TestPartner, TestPartnerRegistry
from hisim.energy_system.assemblies.testing.samples import ParameterSpace, Sample
from hisim.energy_system.document import RawDocument
from hisim.energy_system.executor import SimulationParametersReader, build_energy_system, write_records
from hisim.energy_system.imports_model import Carriers, PortKind
from hisim.energy_system.loader import EnergySystemReader
from hisim.postprocessing.energy_balance import EnergyBalanceError
from hisim.postprocessing.kpi_computation.kpi_address import KpiFinder

#: The import key of the assembly under test in its isolation system.
SUBJECT = "subject"

#: The file name of an isolation system in its run directory.
SYSTEM_FILENAME = "isolation.energy_system.yaml"


@dataclass(frozen=True)
class PartnerBinding:
    """One port of the assembly under test and the test partner that serves it.

    Attributes:
        port: The port's name.
        kind: Its kind.
        state: ``required`` or ``optional``.
        partner: The partner's site name.
        verb: The verb written for it, or ``default`` where the format's default rule binds it.
    """

    port: str
    kind: str
    state: str
    partner: str
    verb: str

    def to_document(self) -> Dict[str, str]:
        """The binding as plain data."""
        return {"port": self.port, "kind": self.kind, "state": self.state, "partner": self.partner, "verb": self.verb}


@dataclass(frozen=True)
class IsolationSystem:
    """The energy-system document of one isolation run, and the bindings it was built from."""

    assembly: str
    sample_id: str
    document: Mapping[str, Any]
    bindings: Tuple[PartnerBinding, ...]

    def text(self) -> str:
        """The document as the file is written."""
        return yaml.safe_dump(dict(self.document), sort_keys=False, allow_unicode=True)


class IsolationBuilder:
    """Builds the isolation system of one assembly and one sample."""

    def __init__(self, resolver: AssemblyResolver, registry: TestPartnerRegistry) -> None:
        """Prepares the builder over the library under test and its test partners."""
        self.resolver = resolver
        self.registry = registry

    @staticmethod
    def import_entry(space: ParameterSpace, sample: Sample) -> Dict[str, Any]:
        """The import of the assembly under test: its preset, and every value the preset or defaults do not give."""
        entry: Dict[str, Any] = {"assembly": space.assembly.path}
        base = space.preset_values(sample.preset) if sample.preset is not None else space.defaults()
        if sample.preset is not None:
            entry["preset"] = sample.preset
        given = {
            name: value
            for name, value in sample.values.items()
            if ParameterSpace.key({name: value}) != ParameterSpace.key({name: base[name]})
        }
        if given:
            entry["parameters"] = given
        return entry

    def document(self, assembly: ResolvedAssembly, components: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict:
        """An isolation document: the site entries, then the import."""
        document: Dict[str, Any] = {
            "schema_version": 4,
            "name": f"isolation_{assembly.path.replace('/', '_')}",
            "description": f"The isolation system of {assembly.path} (assemblies_spec.md §9.4).",
        }
        document["components"] = dict(components)
        document["imports"] = {SUBJECT: dict(entry)}
        return document

    def offered(self, assembly: ResolvedAssembly, entry: Mapping[str, Any]) -> Dict[str, OfferedPort]:
        """The ports the import offers with these parameters, as the expansion resolves them."""
        text = yaml.safe_dump(self.document(assembly, {}, entry), sort_keys=False, allow_unicode=True)
        origin = f"isolation of {assembly.path}"
        model = EnergySystemReader.build(RawDocument.parse_text(text, origin), origin)
        return ImportExpander(model, self.resolver).offered_ports(SUBJECT)

    def partner_for(self, assembly: ResolvedAssembly, port: OfferedPort) -> Optional[Tuple[TestPartner, str]]:
        """The test partner of one offered port and the verb that binds it; ``None`` for a port that only offers.

        Raises:
            TestPartnerMissingError: When no registered partner serves the port.
            HarnessUsageError: For an observer or actuator port, which the expansion does not lower yet.
        """
        kind = port.kind
        verb = "bind" if port.state == "required" else "optional-bind"
        where = (port.name, assembly.path)
        if kind == PortKind.PROVIDED or (kind == PortKind.FACT and port.is_provision):
            return None
        if kind == PortKind.NEED:
            return self.registry.for_partner(port.partner, *where), verb
        if kind == PortKind.CIRCUIT:
            return self.registry.for_circuit(port.circuit or "", port.end_classes, *where), verb
        if kind == PortKind.CARRIER:
            if port.is_provision:
                return self.registry.for_consumer(port.carrier or "", *where), "default"
            partner = self.registry.for_carrier(port.carrier or "", *where)
            return partner, ("default" if port.carrier == Carriers.ELECTRICITY else verb)
        if kind == PortKind.FACT:
            return self.registry.for_fact(port.fact or "", *where), verb
        raise HarnessUsageError(
            f"the port '{port.name}' of '{assembly.path}' is a {kind.value} port, which the expansion does not "
            f"lower yet ({kind.delivering_step}); the harness cannot partner it."
        )

    def build(self, assembly: ResolvedAssembly, space: ParameterSpace, sample: Sample) -> IsolationSystem:
        """The isolation system of one sample.

        Raises:
            EnergySystemError: When the expansion refuses the import with these parameters.
            TestPartnerMissingError: When a port has no registered test partner.
        """
        entry = self.import_entry(space, sample)
        bindings: List[PartnerBinding] = []
        partners: List[str] = []
        verbs: Dict[str, Dict[str, str]] = {"bind": {}, "optional-bind": {}}
        for port in self.offered(assembly, entry).values():
            if port.state == "inactive":
                continue
            found = self.partner_for(assembly, port)
            if found is None:
                continue
            partner, verb = found
            if partner.name not in partners:
                partners.append(partner.name)
            if verb != "default":
                verbs[verb][port.name] = partner.name
            bindings.append(
                PartnerBinding(port=port.name, kind=port.kind.value, state=port.state, partner=partner.name, verb=verb)
            )
        for verb, mapping in verbs.items():
            if mapping:
                entry[verb] = mapping
        components = {
            name: dict(self.registry.partners[name].component) for name in self.registry.closure(partners)
        }
        return IsolationSystem(
            assembly=assembly.path,
            sample_id=sample.sample_id,
            document=self.document(assembly, components, entry),
            bindings=tuple(bindings),
        )


@dataclass
class RunOutcome:
    """What one isolation run produced.

    Attributes:
        sample_id: The sample run.
        directory: Its result directory.
        seconds: Its wall time.
        failure: ``(check, message)`` of a run that raised: ``energy_balance`` for an
            ``EnergyBalanceError``, ``exception`` for anything else; ``None`` when it finished.
        results: The result frame, when the timesteps ran.
        outputs: The simulator's outputs, matching the frame's columns.
        members: Member name of the assembly under test to its runtime name.
        bindings: The test partners of the run.
    """

    sample_id: str
    directory: Path
    seconds: float
    failure: Optional[Tuple[str, str]] = None
    results: Optional[pd.DataFrame] = None
    outputs: List[Any] = field(default_factory=list)
    members: Dict[str, str] = field(default_factory=dict)
    bindings: Tuple[PartnerBinding, ...] = ()
    _finder: Optional[KpiFinder] = None

    @property
    def finished(self) -> bool:
        """Whether the run raised nothing."""
        return self.failure is None

    def kpis(self) -> KpiFinder:
        """The run's KPI collection, read from its ``all_kpis.json``.

        Raises:
            ValueError: When the run wrote none.
        """
        if self._finder is None:
            path = self.directory / "all_kpis.json"
            if not path.is_file():
                raise ValueError(f"the run wrote no {path.name}")
            self._finder = KpiFinder(json.loads(path.read_text(encoding="utf-8")))
        return self._finder


class IsolationRunner:
    """Runs isolation systems, one calculation each, against the library under test."""

    #: The post-processing every isolation run needs: the KPIs, written where the finder reads them.
    POST_PROCESSING = ("COMPUTE_KPIS", "WRITE_KPIS_TO_JSON")

    def __init__(self, resolver: AssemblyResolver, parameters_path: Path) -> None:
        """Prepares the runner.

        Args:
            resolver: The library under test.
            parameters_path: The one-day simulation parameters every run reads.
        """
        self.resolver = resolver
        self.parameters_path = parameters_path

    @classmethod
    def write_parameters(cls, path: Path, seconds_per_timestep: int) -> Path:
        """Writes the simulation parameters of every isolation run: one day, errors-only logging."""
        document = {
            "start_date": "2021-01-01T00:00:00",
            "end_date": "2021-01-02T00:00:00",
            "seconds_per_timestep": seconds_per_timestep,
            "country": "DE",
            "logging_level": 1,
            "post_processing_options": list(cls.POST_PROCESSING),
        }
        path.write_text(
            "# One simulated day for every isolation run of the assembly test harness (assemblies_spec.md §9.4).\n"
            + yaml.safe_dump(document, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def run(self, system: IsolationSystem, space: ParameterSpace, sample: Sample, directory: Path) -> RunOutcome:
        """Runs one isolation system in a fresh directory and captures how it ended.

        Raises:
            SamplerError: When the expansion resolved other parameters than the sample holds,
                which would make every check of the run test something else.
        """
        directory.mkdir(parents=True, exist_ok=False)
        system_path = directory / SYSTEM_FILENAME
        system_path.write_text(system.text(), encoding="utf-8")
        outcome = RunOutcome(sample_id=sample.sample_id, directory=directory, seconds=0.0, bindings=system.bindings)
        built = None
        start = time.perf_counter()
        try:
            with CalculationScope.open(label=f"assembly test {system.assembly} {sample.sample_id}",
                                       run_directory=directory):
                parameters = SimulationParametersReader.read(self.parameters_path)
                parameters.result_directory = str(directory)
                built = build_energy_system(
                    system_path,
                    parameters,
                    simulation_parameters_path=self.parameters_path,
                    assembly_resolver=self.resolver,
                )
                write_records(built, str(directory))
                built.simulator.run_all_timesteps()
        except EnergyBalanceError as error:
            outcome.failure = ("energy_balance", str(error))
        # Every failure of a run is a finding of the harness, reported by name with the sample.
        except Exception as error:  # pylint: disable=broad-exception-caught
            outcome.failure = ("exception", f"{type(error).__name__}: {error}")
        outcome.seconds = time.perf_counter() - start
        if built is not None:
            self._verify_parameters(built, space, sample)
            outcome.results = getattr(built.simulator, "results_data_frame", None)
            outcome.outputs = list(built.simulator.all_outputs)
            outcome.members = self.members_of(built.imports.addresses)
        return outcome

    @staticmethod
    def members_of(addresses: Mapping[str, ComponentID]) -> Dict[str, str]:
        """The members of the assembly under test (not of its inner imports), by member name."""
        return {
            identity.name: runtime_name
            for runtime_name, identity in addresses.items()
            if tuple(identity.path) == (AddressStep(import_key=SUBJECT),)
        }

    @staticmethod
    def _verify_parameters(built: Any, space: ParameterSpace, sample: Sample) -> None:
        """Refuses a run whose import resolved to other parameters than the sample (a harness bug)."""
        record = next((instance for instance in built.imports.instances if instance.path == SUBJECT), None)
        if record is None:
            raise SamplerError(f"the isolation run of {sample.sample_id} has no import record of '{SUBJECT}'.")
        resolved = space.normalised(record.parameters_resolved)
        if ParameterSpace.key(resolved) != ParameterSpace.key(sample.values):
            raise SamplerError(
                f"the isolation run of {sample.sample_id} of '{space.assembly.path}' resolved {resolved}, but the "
                f"sample holds {sample.values}."
            )
