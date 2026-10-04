"""The registry of test partners (``assemblies_spec.md`` §9.4: "a stub partner of the declared class").

An assembly runs in isolation beside the smallest set of site components its ports need: a test
partner for each. Which component stands in for what is data, not code: every library directory
may hold a ``test_partners.yaml`` beside its assemblies, and the registry is the union of those
files along the library's search path. The mock library's file maps the mock classes; the
repository's library gets its own when its first assemblies land (§13 step 4), mapping each real
partner class to a minimal preset.

A partner is a named site entry, written exactly as an energy-system file writes one, with what it
serves and the other partners it reads::

    partners:
      Weather:
        serves: {partner: MockWeather}
        component: {class: tests.assemblies.mock_components.MockWeather, preset: standard}
      Cylinder:
        serves: {circuit: dhw, other_end: [MockBoiler]}
        requires: [Occupancy]
        component:
          class: tests.assemblies.mock_components.MockCylinder
          preset: standard
          inputs: [Occupancy, {$port: dhw}]
          ports: {dhw: {circuit: dhw}}

``serves`` is one of

- ``{partner: <class name>}`` — the partner of a need whose ``partner:`` lists that class;
- ``{circuit: <circuit>, other_end: [<class name>, …]}`` — the other end of a circuit whose end in
  the assembly consists of members of exactly those classes;
- ``{carrier: <carrier>}`` — the provider a carrier need of that carrier binds;
- ``{consumes: <carrier>}`` — a consumer bound to a carrier the assembly provides (a fuel's
  provider without a bound consumer is refused, §5.2);
- ``{fact: <fact>}`` — a component whose class contributes that sizing fact;
- ``{observed_by: <class name>}`` — a component an observer of that class observes, so that an
  observer port in isolation has something to select (§4.1; an observer that selects nothing is
  refused as idle);
- ``{controls: <class name>}`` — the controller of a provided output of a member of that class that
  is actuated through ``controllable: {target_input: …}`` and binds one controller (§4.4).

A controller is an assembly — its priorities are its parameter — so a partner may be written as an
import instead of a site entry, ``import: {assembly: control/ems_self_consumption}``; it joins the
isolation system under its partner name as a key of ``imports``.

Every partner serves one thing, every thing is served by at most one partner, and every name a
``requires`` lists is a partner. A file that breaks any of this is refused as a whole
(:class:`~.errors.TestPartnerRegistryError`); a port no partner serves is refused when the
isolation system is built (:class:`~.errors.TestPartnerMissingError`), naming the class, circuit,
carrier or fact and the registry files searched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

import yaml

from hisim.energy_system.document import RawDocument
from hisim.energy_system.errors import EnergySystemError
from hisim.energy_system.imports_model import Carriers
from hisim.energy_system.loader import EnergySystemReader
from hisim.energy_system.assemblies.testing.errors import TestPartnerMissingError, TestPartnerRegistryError

#: The key a served thing is registered under.
ServedKey = Tuple[Any, ...]


@dataclass(frozen=True)
class TestPartner:
    """One registered test partner.

    Attributes:
        name: Its site name in the isolation system.
        serves: What it serves, as written.
        requires: The partners it reads, which join the isolation system with it.
        component: Its site entry, as an energy-system file writes it; empty for an import partner.
        origin: The registry file it is written in.
        import_entry: For a partner that is an import (a controller assembly), its import block.
    """

    __test__: ClassVar[bool] = False

    name: str
    serves: Mapping[str, Any]
    requires: Tuple[str, ...]
    component: Mapping[str, Any]
    origin: str
    import_entry: Optional[Mapping[str, Any]] = None

    @property
    def served_key(self) -> ServedKey:
        """The key it is found by."""
        return TestPartnerRegistry.key_of(self.serves, self.origin, self.name)


class TestPartnerRegistry:
    """Every test partner of a library, found by what it serves."""

    __test__: ClassVar[bool] = False

    #: The file a library directory registers its test partners in.
    FILENAME: ClassVar[str] = "test_partners.yaml"

    #: The keys of one partner entry.
    ENTRY_KEYS: ClassVar[FrozenSet[str]] = frozenset({"serves", "requires", "component", "import"})

    def __init__(self, partners: Sequence[TestPartner], files: Sequence[str]) -> None:
        """Builds the registry from parsed partners and checks it whole.

        Raises:
            TestPartnerRegistryError: For a duplicate name or served key, an unknown requirement, or
                a site entry that does not read.
        """
        self.files: Tuple[str, ...] = tuple(files)
        self.partners: Dict[str, TestPartner] = {}
        self._served: Dict[ServedKey, TestPartner] = {}
        for partner in partners:
            if partner.name in self.partners:
                raise TestPartnerRegistryError(
                    f"the test partner '{partner.name}' is registered twice, in {self.partners[partner.name].origin} "
                    f"and in {partner.origin}."
                )
            key = partner.served_key
            if key in self._served:
                raise TestPartnerRegistryError(
                    f"{self.describe_key(key)} is served twice, by '{self._served[key].name}' "
                    f"({self._served[key].origin}) and by '{partner.name}' ({partner.origin}); one thing, one partner."
                )
            self.partners[partner.name] = partner
            self._served[key] = partner
        for partner in self.partners.values():
            for required in partner.requires:
                if required not in self.partners:
                    raise TestPartnerRegistryError(
                        f"the test partner '{partner.name}' ({partner.origin}) requires '{required}', which is no "
                        f"registered partner (registered: {', '.join(sorted(self.partners)) or 'none'})."
                    )
        for partner in self.partners.values():
            self._check_reads(partner)

    # --------------------------------------------------------------------------------------- reading

    @classmethod
    def from_directories(cls, directories: Sequence[Path]) -> "TestPartnerRegistry":
        """The registry of every ``test_partners.yaml`` in the library directories (none is fine)."""
        partners: List[TestPartner] = []
        files: List[str] = []
        for directory in directories:
            path = Path(directory) / cls.FILENAME
            if path.is_file():
                files.append(str(path))
                partners.extend(cls.read(path))
        return cls(partners, files)

    @classmethod
    def read(cls, path: Path) -> List[TestPartner]:
        """The partners of one registry file.

        Raises:
            TestPartnerRegistryError: For a file that is not a mapping with ``partners``, or an
                entry that is malformed.
        """
        origin = str(path)
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise TestPartnerRegistryError(f"{origin} is not YAML: {error}") from error
        if not isinstance(document, Mapping) or set(document) != {"partners"}:
            raise TestPartnerRegistryError(f"{origin} must hold exactly one key, 'partners'.")
        entries = document["partners"]
        if not isinstance(entries, Mapping):
            raise TestPartnerRegistryError(f"{origin}: 'partners' must map a partner name to its entry.")
        partners: List[TestPartner] = []
        for name, entry in entries.items():
            where = f"{origin}: partners.{name}"
            if not isinstance(name, str) or not name.isidentifier():
                raise TestPartnerRegistryError(f"{where}: a partner's name is an identifier.")
            if not isinstance(entry, Mapping) or not set(entry) <= cls.ENTRY_KEYS or "serves" not in entry:
                raise TestPartnerRegistryError(
                    f"{where}: an entry has 'serves', 'component' or 'import', and optionally 'requires', nothing else."
                )
            if ("component" in entry) == ("import" in entry):
                raise TestPartnerRegistryError(f"{where}: an entry is either a site 'component' or an 'import'.")
            if "import" in entry and (not isinstance(entry["import"], Mapping) or "assembly" not in entry["import"]):
                raise TestPartnerRegistryError(f"{where}: 'import' is an import block with an 'assembly'.")
            if "component" in entry and (
                not isinstance(entry.get("component"), Mapping) or "class" not in entry["component"]
            ):
                raise TestPartnerRegistryError(f"{where}: 'component' is a site entry with a 'class'.")
            requires = entry.get("requires", [])
            if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
                raise TestPartnerRegistryError(f"{where}: 'requires' is a list of partner names.")
            serves = entry["serves"]
            if not isinstance(serves, Mapping):
                raise TestPartnerRegistryError(f"{where}: 'serves' is a mapping.")
            partner = TestPartner(
                name=name,
                serves=dict(serves),
                requires=tuple(requires),
                component=dict(entry.get("component") or {}),
                origin=where,
                import_entry=dict(entry["import"]) if "import" in entry else None,
            )
            cls.key_of(partner.serves, where, name)
            partners.append(partner)
        return partners

    @staticmethod
    def key_of(  # pylint: disable=too-many-return-statements  # one return per form of serves
        serves: Mapping[str, Any], origin: str, name: str
    ) -> ServedKey:
        """The key a ``serves`` block registers under.

        Raises:
            TestPartnerRegistryError: For a block that is none of the five forms.
        """
        keys = set(serves)
        if keys == {"partner"} and isinstance(serves["partner"], str):
            return ("partner", serves["partner"])
        if (
            keys == {"circuit", "other_end"}
            and isinstance(serves["circuit"], str)
            and isinstance(serves["other_end"], list)
            and serves["other_end"]
            and all(isinstance(item, str) for item in serves["other_end"])
        ):
            return ("circuit", serves["circuit"], frozenset(serves["other_end"]))
        for kind in ("carrier", "consumes"):
            if keys == {kind}:
                if not Carriers.is_carrier(serves[kind]):
                    raise TestPartnerRegistryError(
                        f"{origin}: the test partner '{name}' names the carrier {serves[kind]!r}, which is none of "
                        f"{', '.join(Carriers.names())}."
                    )
                return (kind, serves[kind])
        if keys == {"fact"} and isinstance(serves["fact"], str):
            return ("fact", serves["fact"])
        for kind in ("observed_by", "controls"):
            if keys == {kind} and isinstance(serves[kind], str):
                return (kind, serves[kind])
        raise TestPartnerRegistryError(
            f"{origin}: the test partner '{name}' serves {dict(serves)!r}; write one of {{partner: <class>}}, "
            "{circuit: <circuit>, other_end: [<class>, ...]}, {carrier: <carrier>}, {consumes: <carrier>}, "
            "{fact: <fact>}, {observed_by: <class>} or {controls: <class>}."
        )

    #: How a message names what a served key stands for, by its kind (the circuit's is built apart).
    KEY_TEXTS: ClassVar[Mapping[str, str]] = {
        "partner": "the partner class {0}",
        "carrier": "the provider of {0}",
        "consumes": "a consumer of {0}",
        "fact": "the provider of the fact {0}",
        "observed_by": "a component an observer of the class {0} observes",
        "controls": "the controller of a {0}'s controllable output",
    }

    @classmethod
    def describe_key(cls, key: ServedKey) -> str:
        """A served key as a message names it."""
        if key[0] == "circuit":
            return f"the other end of the circuit {key[1]} for an end of {', '.join(sorted(key[2]))}"
        return cls.KEY_TEXTS[key[0]].format(key[1])

    def _check_reads(self, partner: TestPartner) -> None:
        """Reads the partner with the partners it requires as a site, so a malformed entry fails here."""
        names = self.closure([partner.name])
        document: Dict[str, Any] = {
            "schema_version": 4,
            "name": "test_partner_check",
            "components": {
                name: dict(self.partners[name].component) for name in names if self.partners[name].component
            },
        }
        imports = {
            name: dict(self.partners[name].import_entry or {}) for name in names if self.partners[name].import_entry
        }
        if imports:
            document["imports"] = imports
        text = yaml.safe_dump(document, sort_keys=False)
        try:
            EnergySystemReader.build(RawDocument.parse_text(text, partner.origin), partner.origin)
        except EnergySystemError as error:
            raise TestPartnerRegistryError(f"{partner.origin}: the site entry does not read: {error}") from error

    # --------------------------------------------------------------------------------------- lookup

    def _find(self, key: ServedKey, port: str, assembly: str) -> TestPartner:
        """The partner registered for a key, or the refusal naming what is missing."""
        partner = self._served.get(key)
        if partner is None:
            raise TestPartnerMissingError(
                f"the port '{port}' of '{assembly}' needs a test partner for {self.describe_key(key)}, and no "
                f"registry serves it (searched: {', '.join(self.files) or f'no {self.FILENAME} on the library path'})."
                f" Register one in the {self.FILENAME} of the library directory."
            )
        return partner

    def for_partner(self, classes: Sequence[str], port: str, assembly: str) -> TestPartner:
        """The partner of a need: the first of its partner classes that is registered.

        Raises:
            TestPartnerMissingError: When none of the classes is registered; names every class.
        """
        for class_name in classes:
            partner = self._served.get(("partner", class_name))
            if partner is not None:
                return partner
        raise TestPartnerMissingError(
            f"the port '{port}' of '{assembly}' needs a test partner of the class "
            f"{' or '.join(classes) or '(none declared)'}, and no registry serves it (searched: "
            f"{', '.join(self.files) or f'no {self.FILENAME} on the library path'}). Register the class in the "
            f"{self.FILENAME} of the library directory with a minimal preset."
        )

    def for_circuit(self, circuit: str, end_classes: Sequence[str], port: str, assembly: str) -> TestPartner:
        """The other end of a circuit, for an end of members of these classes."""
        return self._find(("circuit", circuit, frozenset(end_classes)), port, assembly)

    def for_carrier(self, carrier: str, port: str, assembly: str) -> TestPartner:
        """The provider of a carrier need."""
        return self._find(("carrier", carrier), port, assembly)

    def for_consumer(self, carrier: str, port: str, assembly: str) -> TestPartner:
        """A consumer for a carrier the assembly provides."""
        return self._find(("consumes", carrier), port, assembly)

    def for_fact(self, fact: str, port: str, assembly: str) -> TestPartner:
        """The provider of a sizing fact."""
        return self._find(("fact", fact), port, assembly)

    def for_observed(self, observer_classes: Sequence[str], port: str, assembly: str) -> TestPartner:
        """Something an observer port's members observe: the partner of the first registered class."""
        for class_name in observer_classes:
            partner = self._served.get(("observed_by", class_name))
            if partner is not None:
                return partner
        return self._find(("observed_by", next(iter(observer_classes), "")), port, assembly)

    def for_controller(self, class_name: str, port: str, assembly: str) -> TestPartner:
        """The controller of a controllable output of a member of that class."""
        return self._find(("controls", class_name), port, assembly)

    def closure(self, names: Sequence[str]) -> List[str]:
        """The partners and every partner they require, transitively, each once.

        A partner follows the partners it requires where that is possible; two partners that read
        each other (a boiler and the cylinder on its circuit) are both included, in the order the
        walk reaches them.
        """
        ordered: List[str] = []
        visiting: List[str] = []

        def visit(name: str) -> None:
            if name in ordered or name in visiting:
                return
            visiting.append(name)
            for required in self.partners[name].requires:
                visit(required)
            visiting.pop()
            ordered.append(name)

        for name in names:
            visit(name)
        return ordered

    def get(self, name: str) -> Optional[TestPartner]:
        """A partner by name."""
        return self.partners.get(name)
