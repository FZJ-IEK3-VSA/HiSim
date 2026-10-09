"""The registry of test partners (``assemblies_spec.md`` §9.4: "a stub partner of the declared class").

An assembly runs in isolation beside the smallest set of site components its ports need: a test
partner for each. Which component stands in for what is data, not code: every library directory
holds a ``test_partners.yaml`` beside its assemblies, and the registry is the union of those files
along the library's search path. A partner is a named site entry, written exactly as an energy-system
file writes one, with what it serves and the other partners it reads. The real library's DHW
cylinder, abridged to a boiler's circuit (``energy_systems/assemblies/test_partners.yaml``)::

    partners:
      DHWStorage:
        serves: [{circuit: dhw, other_end: [GenericBoiler]}, {partner: SimpleDHWStorage}]
        requires: [UTSPConnector]
        component:
          class: hisim.components.simple_water_storage.SimpleDHWStorage
          preset: standard
          inputs: [UTSPConnector, {$port: dhw}]
          ports: {dhw: {circuit: dhw}}

``serves`` is one of the forms below, or a list of them; a partner without ``serves`` joins a
system only as another partner's requirement:

- ``{partner: <class name>}`` — the partner of a need whose ``partner:`` lists that class;
- ``{circuit: <circuit>, other_end: [<class name>, …]}`` — the other end of a circuit whose end in
  the assembly consists of members of exactly those classes;
- ``{carrier: <carrier>}`` — the provider a carrier need of that carrier binds;
- ``{consumes: <carrier>}`` — a consumer of a fuel the assembly provides (an idle provider is refused, §5.2);
- ``{fact: <fact>}`` — a component whose class contributes that sizing fact;
- ``{observed_by: <class name>}`` — a component an observer member of that class observes (§4.3);
- ``{controls: <class name>}`` — the controller ranking a ``controllable: {target_input}`` output of a
  member of that class (§4.4).

One thing is served by one partner, every name a ``requires`` lists is a partner, and no partner
requires itself through others; a file that breaks this, or a site entry that does not read, is
refused whole (:class:`TestPartnerRegistryError`). A port no partner serves is refused when its
isolation system is built (:class:`TestPartnerMissingError`), naming the class, circuit, carrier or fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Mapping, Sequence, Tuple

import yaml

from hisim import loadtypes as lt
from hisim.energy_system.classes import ClassBinder
from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.model import ComponentEntry, EnergySystemFile

#: What a partner serves, as the registry finds it: the form's key, then its values.
ServedKey = Tuple[Any, ...]


class TestPartnerRegistryError(Exception):
    """A ``test_partners.yaml`` that does not read: a malformed entry, a duplicate, an unknown requirement."""

    __test__ = False


class TestPartnerMissingError(Exception):
    """A port of an assembly under test that no registered test partner serves."""

    __test__ = False


@dataclass(frozen=True)
class TestPartner:
    """One registered test partner: its site name, what it serves, the partners it reads and its site entry."""

    __test__: ClassVar[bool] = False

    name: str
    serves: Tuple[ServedKey, ...]
    requires: Tuple[str, ...]
    component: Mapping[str, Any]
    origin: str


class TestPartnerRegistry:
    """Every test partner of a library, found by what it serves."""

    __test__: ClassVar[bool] = False

    #: The file a library directory registers its test partners in.
    FILENAME: ClassVar[str] = "test_partners.yaml"

    #: The forms of ``serves``: the keys each writes, the first naming the form.
    FORMS: ClassVar[Tuple[Tuple[str, ...], ...]] = (
        ("partner",),
        ("circuit", "other_end"),
        ("carrier",),
        ("consumes",),
        ("fact",),
        ("observed_by",),
        ("controls",),
    )

    def __init__(self, partners: Sequence[TestPartner], files: Sequence[str]) -> None:
        """Builds the registry and checks it whole.

        Raises:
            TestPartnerRegistryError: For a partner or a served thing registered twice, an unknown
                requirement, a cycle of requirements, or a site entry that does not read.
        """
        self.files = tuple(files)
        self.partners: Dict[str, TestPartner] = {}
        self.served: Dict[ServedKey, TestPartner] = {}
        for partner in partners:
            if partner.name in self.partners:
                raise TestPartnerRegistryError(
                    f"the test partner '{partner.name}' is registered twice, in {self.partners[partner.name].origin} "
                    f"and in {partner.origin}."
                )
            self.partners[partner.name] = partner
            for key in partner.serves:
                if key in self.served:
                    raise TestPartnerRegistryError(
                        f"{self.describe(key)} is served twice, by '{self.served[key].name}' and by '{partner.name}' "
                        f"({partner.origin}); one thing, one partner."
                    )
                self.served[key] = partner
        for partner in self.partners.values():
            unknown = [name for name in partner.requires if name not in self.partners]
            if unknown:
                raise TestPartnerRegistryError(
                    f"the test partner '{partner.name}' ({partner.origin}) requires {', '.join(unknown)}, which is no "
                    f"registered partner (registered: {', '.join(sorted(self.partners))})."
                )
        for partner in self.partners.values():
            self.document([partner.name], partner.origin)

    @classmethod
    def from_directories(cls, directories: Sequence[Path]) -> "TestPartnerRegistry":
        """The registry of every ``test_partners.yaml`` in the library directories."""
        files = [Path(directory) / cls.FILENAME for directory in directories]
        files = [path for path in files if path.is_file()]
        return cls([partner for path in files for partner in cls.read(path)], [str(path) for path in files])

    @classmethod
    def read(cls, path: Path) -> List[TestPartner]:
        """The partners of one registry file.

        Raises:
            TestPartnerRegistryError: For a file that is not a mapping of ``partners``, or a malformed entry.
        """
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise TestPartnerRegistryError(f"{path} is not YAML: {error}") from error
        if (
            not isinstance(document, Mapping)
            or set(document) != {"partners"}
            or not isinstance(document["partners"], Mapping)
        ):
            raise TestPartnerRegistryError(f"{path} holds exactly one key, 'partners', mapping names to entries.")
        partners: List[TestPartner] = []
        for name, entry in document["partners"].items():
            where = f"{path}: partners.{name}"
            if not isinstance(name, str) or not name.isidentifier():
                raise TestPartnerRegistryError(f"{where}: a partner's name is an identifier.")
            if not isinstance(entry, Mapping) or not set(entry) <= {"serves", "requires", "component"}:
                raise TestPartnerRegistryError(f"{where}: an entry has 'component', 'serves' and 'requires', no more.")
            if not isinstance(entry.get("component"), Mapping) or "class" not in entry["component"]:
                raise TestPartnerRegistryError(f"{where}: 'component' is a site entry with a 'class'.")
            requires = entry.get("requires", [])
            if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
                raise TestPartnerRegistryError(f"{where}: 'requires' is a list of partner names.")
            serves = entry.get("serves", [])
            serves = serves if isinstance(serves, list) else [serves]
            keys = tuple(cls.key_of(item, where) for item in serves)
            partners.append(TestPartner(name, keys, tuple(requires), dict(entry["component"]), where))
        return partners

    @classmethod
    def key_of(cls, serves: Any, where: str) -> ServedKey:
        """The key one ``serves`` form registers under.

        Raises:
            TestPartnerRegistryError: For a form that is none of :attr:`FORMS`, or an unknown carrier.
        """
        form = next((keys for keys in cls.FORMS if isinstance(serves, Mapping) and set(serves) == set(keys)), None)
        if form is None or not all(isinstance(serves[key], str) for key in form if key != "other_end"):
            raise TestPartnerRegistryError(
                f"{where}: serves {serves!r}; write one of "
                + ", ".join("{" + ", ".join(f"{key}: …" for key in keys) + "}" for keys in cls.FORMS)
                + "."
            )
        if form[0] in ("carrier", "consumes") and serves[form[0]] not in [
            item.value for item in lt.EnergyBalanceCarrier
        ]:
            raise TestPartnerRegistryError(f"{where}: {serves[form[0]]!r} is no energy carrier.")
        if form[0] == "circuit":
            ends = serves["other_end"]
            if not isinstance(ends, list) or not ends or not all(isinstance(item, str) for item in ends):
                raise TestPartnerRegistryError(f"{where}: 'other_end' is a non-empty list of class names.")
            return ("circuit", serves["circuit"], frozenset(ends))
        return (form[0], serves[form[0]])

    @staticmethod
    def describe(key: ServedKey) -> str:
        """A served thing as a message names it."""
        if key[0] == "circuit":
            return f"the other end of the circuit {key[1]} for an end of {', '.join(sorted(key[2]))}"
        return {
            "partner": "a partner of the class",
            "carrier": "the provider of",
            "consumes": "a consumer of",
            "fact": "the provider of the fact",
            "observed_by": "a component observed by the class",
            "controls": "the controller of the class",
        }[key[0]] + f" {key[1]}"

    def find(self, alternatives: Sequence[ServedKey], need: str, assembly: str) -> TestPartner:
        """The partner registered for the first of the alternatives that has one.

        Args:
            alternatives: What would serve, in order.
            need: What needs it, as a message names it: ``the port 'weather'``, ``the fact read 'roof_area_in_m2'``.
            assembly: The assembly under test.

        Raises:
            TestPartnerMissingError: When none has, naming every alternative and the files searched.
        """
        for key in alternatives:
            if key in self.served:
                return self.served[key]
        raise TestPartnerMissingError(
            f"{need} of '{assembly}' needs a test partner, "
            + " or ".join(self.describe(key) for key in alternatives)
            + f", and no {self.FILENAME} serves it (searched: {', '.join(self.files) or 'none on the library path'})."
            f" Register one in the {self.FILENAME} of the library directory."
        )

    def config_class_of(self, name: str) -> type:
        """The configuration class of a registered partner's component.

        Example: ``config_class_of("OilBoiler")`` is ``GenericBoilerConfig`` when the partner's site
        entry names ``hisim.components.generic_boiler.GenericBoiler``. The harness reads the sizing
        facts a partner contributes from it.

        Args:
            name: The partner's site name.

        Returns:
            The configuration dataclass its component's constructor takes.

        Raises:
            EnergySystemBindingError: When the entry's class does not import to a component.
        """
        entry = ComponentEntry(name=name, class_path=self.partners[name].component["class"])
        return ClassBinder.config_class_of(name, entry)

    def closure(self, names: Sequence[str]) -> List[str]:
        """The partners and every partner they require, transitively, each once, a requirement first.

        Raises:
            TestPartnerRegistryError: For partners requiring each other in a cycle, naming it.
        """
        ordered: List[str] = []
        visiting: List[str] = []

        def visit(name: str) -> None:
            if name in ordered:
                return
            if name in visiting:
                cycle = visiting[visiting.index(name) :] + [name]
                raise TestPartnerRegistryError(
                    f"the test partners require each other in a cycle, {' → '.join(cycle)} "
                    f"({self.partners[name].origin}); drop one requirement of the cycle: a partner that joins only "
                    "with another reads it without requiring it back."
                )
            visiting.append(name)
            for required in self.partners[name].requires:
                visit(required)
            visiting.pop()
            ordered.append(name)

        for name in names:
            visit(name)
        return ordered

    def document(self, names: Sequence[str], origin: str) -> Dict[str, Dict[str, Any]]:
        """The site entries of the partners and their requirements, checked to read as a site.

        Raises:
            TestPartnerRegistryError: When the entries do not read as the components of a file.
        """
        components = {name: dict(self.partners[name].component) for name in self.closure(names)}
        document = {
            "schema_version": EnergySystemFile.ASSEMBLIES_SCHEMA_VERSION,
            "name": "test_partners",
            "components": components,
        }
        text = yaml.safe_dump(document, sort_keys=False)
        try:
            EnergySystemReader.build(RawDocument.parse_text(text, origin), origin)
        except EnergySystemError as error:
            raise TestPartnerRegistryError(f"{origin}: the site entry does not read: {error}") from error
        return components
