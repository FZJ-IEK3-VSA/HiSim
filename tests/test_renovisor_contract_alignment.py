"""Checks a *candidate* contract revision against what this HiSim actually implements.

Every other RenoVisor test reads the vendored copy under ``hisim/renovisor/contract/``, which is
the contract this HiSim speaks today. This one reads a different copy: a checkout of the contract
repository on a branch that has not been merged, so that the branch can be judged before anybody
depends on it. It is the acceptance test of the contract pull request of step 7 — "does the
proposed ``openapi.yaml`` say what HiSim does?" — and it answers three questions that the drift
check of ``tests/test_renovisor_contract.py`` cannot, because that one only asserts that the
vendored copy still matches its pin.

Point it at a checkout and run it::

    RENOVISOR_CONTRACT_CHECKOUT=~/renovisor-api-contract pytest tests/test_renovisor_contract_alignment.py

Without the variable every test here is skipped, so the suite stays green on a machine that has no
checkout — which is every CI machine until the branch merges. After the merge the vendored copy
becomes the branch's content and the same assertions run against it through
``test_renovisor_contract.py``'s refresh path.

The three questions:

* **Do the enums agree?** Each schema named in :class:`EnumAlignment` carries the members of one
  HiSim enum, member for member and in order. Decision C3 made the contract's enum lists generated
  artefacts of ``hisim/renovisor/vocabulary.py`` and ``hisim/renovisor/reasons.py``; a list that
  has drifted is a value one side can send and the other cannot read.
* **Is the pending-path list empty against it?**
  :class:`hisim.renovisor.inventory.PendingContractPaths` names every inventory path the
  translation layer writes that the vendored contract does not declare. The contract PR exists to
  empty it, so against the branch every one of those paths must be a declared property of
  ``HomeInventoryInput`` — and a path that is *still* missing is the one thing that would make the
  branch unmergeable from HiSim's side.
* **Are the catalogue ids the ones HiSim derives?** ``measures.yaml`` gains an explicit ``id`` per
  measure, and :class:`hisim.renovisor.catalogue.IdDerivation` is what produced them. An ``id``
  that differs from the derivation is not wrong in itself — an explicit id is allowed to win, and
  that is how a re-worded measure keeps its id — but in *this* revision it would mean somebody
  typed one by hand, so the test pins the generated state.
"""

import os
from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple, Type

import pytest
import yaml
from enum import Enum

from hisim.renovisor.catalogue import IdDerivation
from hisim.renovisor.inventory import PendingContractPaths
from hisim.renovisor.occupancy import EmploymentStatus, ResidentType
from hisim.renovisor.reasons import ReasonCode
from hisim.renovisor.vocabulary import (
    DhwSupply,
    FloorConstruction,
    HeatDistribution,
    HeatGenerator,
    Provenance,
    RetrofitStatus,
    TabulaBuildingType,
    VentilationType,
    WallConstruction,
)

pytestmark = pytest.mark.base


class ContractCheckout:
    """The contract checkout under test, located from the environment.

    The variable holds a directory — the root of a clone of the contract repository, on whatever
    branch is being judged — and not a file, because two files are read out of it and a single
    variable pointing at a directory cannot name one of them by accident.

    Example::

        RENOVISOR_CONTRACT_CHECKOUT=~/renovisor-api-contract pytest -k alignment
    """

    #: The environment variable naming the checkout. Absent means "skip every test in this file".
    VARIABLE: ClassVar[str] = "RENOVISOR_CONTRACT_CHECKOUT"

    #: The two files read out of the checkout.
    OPENAPI_FILENAME: ClassVar[str] = "openapi.yaml"
    MEASURES_FILENAME: ClassVar[str] = "measures.yaml"

    #: Where the schemas sit inside the OpenAPI document.
    COMPONENTS_KEY: ClassVar[str] = "components"
    SCHEMAS_KEY: ClassVar[str] = "schemas"

    #: The inventory schema whose declared paths the pending list is checked against.
    INVENTORY_SCHEMA: ClassVar[str] = "HomeInventoryInput"

    @classmethod
    def directory(cls) -> Optional[Path]:
        """Return the checkout directory, or ``None`` when the variable is unset or empty.

        Returns:
            The expanded path. Its existence is not checked here; :meth:`require` does that, so
            that a typo in the variable is a failure and not a silent skip.
        """
        raw = os.environ.get(cls.VARIABLE, "").strip()
        return Path(raw).expanduser() if raw else None

    @classmethod
    def require(cls) -> Path:
        """Return the checkout directory, skipping the test when there is none.

        Returns:
            The directory.

        Raises:
            pytest.skip.Exception: When the variable is unset, which is the normal case on a
                machine with no checkout.
            AssertionError: When the variable is set to something that is not a directory holding
                the two contract files — a typo must not look like "nothing to check".
        """
        directory = cls.directory()
        if directory is None:
            pytest.skip(f"set {cls.VARIABLE} to a contract checkout to run the alignment checks")
        assert directory.is_dir(), f"{cls.VARIABLE}={directory} is not a directory"
        for filename in (cls.OPENAPI_FILENAME, cls.MEASURES_FILENAME):
            assert (directory / filename).is_file(), f"{directory / filename} is missing"
        return directory

    @classmethod
    def _load(cls, filename: str) -> Dict[str, Any]:
        """Parse one file of the checkout with ``yaml.safe_load``."""
        with (cls.require() / filename).open(encoding="utf-8") as handle:
            return dict(yaml.safe_load(handle))

    @classmethod
    def openapi(cls) -> Dict[str, Any]:
        """Return the checkout's parsed ``openapi.yaml``."""
        return cls._load(cls.OPENAPI_FILENAME)

    @classmethod
    def measures(cls) -> Dict[str, Any]:
        """Return the checkout's parsed ``measures.yaml``."""
        return cls._load(cls.MEASURES_FILENAME)

    @classmethod
    def schema(cls, name: str) -> Mapping[str, Any]:
        """Return one schema of the checkout's document by name.

        Args:
            name: The schema name under ``components.schemas``.

        Returns:
            The schema object.

        Raises:
            AssertionError: When the document has no schema of that name, which for a generated
                enum means the contract dropped a vocabulary HiSim still writes.
        """
        schemas = cls.openapi()[cls.COMPONENTS_KEY][cls.SCHEMAS_KEY]
        assert name in schemas, f"the contract has no schema '{name}'"
        schema: Mapping[str, Any] = schemas[name]
        return schema

    @classmethod
    def declares(cls, path: str) -> bool:
        """Return whether ``HomeInventoryInput`` declares one dotted inventory path.

        The walk is the same one :meth:`hisim.renovisor.inventory.InventorySchema.declares`
        performs against the vendored copy: every segment must be a declared property of the
        object the previous segment reached. It is repeated here rather than imported because
        that one is bound to the vendored document by construction.

        Args:
            path: A dotted path such as ``building_config.general.roof_form``.

        Returns:
            ``True`` when every segment is declared.
        """
        node: Any = cls.schema(cls.INVENTORY_SCHEMA)
        for segment in path.split("."):
            properties = node.get("properties") if isinstance(node, Mapping) else None
            if not isinstance(properties, Mapping) or segment not in properties:
                return False
            node = properties[segment]
        return True


class EnumAlignment:
    """Which contract schema carries which HiSim enum.

    One table, so that "the contract generates its enum lists from HiSim" is a claim with a test
    behind it rather than a sentence in a document (decision C3, requirement A19). A vocabulary
    HiSim owns but the contract does not carry is deliberately absent from the table:
    ``SolarThermalSupplies`` and ``TemperatureControl`` live in ``measures.yaml`` as option values
    rather than as inventory fields, and ``ThermalElement`` is internal to the measure layer. The
    two occupancy vocabularies are here for the same reason as the rest: the contract stated them
    in prose until this revision, and :mod:`hisim.renovisor.occupancy` is where their members are
    spelled.
    """

    #: Contract schema name -> the HiSim enum whose members it must carry, in order.
    BY_SCHEMA: ClassVar[Dict[str, Type[Enum]]] = {
        "HeatGenerator": HeatGenerator,
        "HeatDistribution": HeatDistribution,
        "DhwSupply": DhwSupply,
        "VentilationType": VentilationType,
        "RetrofitStatus": RetrofitStatus,
        "TabulaBuildingType": TabulaBuildingType,
        "FloorConstruction": FloorConstruction,
        "WallConstruction": WallConstruction,
        "ResidentType": ResidentType,
        "EmploymentStatus": EmploymentStatus,
        "Provenance": Provenance,
        "ReasonCode": ReasonCode,
    }

    @classmethod
    def members(cls, enum: Type[Enum]) -> Tuple[str, ...]:
        """Return an enum's member values in declaration order."""
        return tuple(str(member.value) for member in enum)


@pytest.mark.parametrize("schema_name", sorted(EnumAlignment.BY_SCHEMA))
def test_contract_enum_equals_hisim_vocabulary(schema_name: str) -> None:
    """Each generated enum of the contract carries exactly HiSim's members, in HiSim's order."""
    schema = ContractCheckout.schema(schema_name)
    expected = EnumAlignment.members(EnumAlignment.BY_SCHEMA[schema_name])
    actual = tuple(str(value) for value in schema.get("enum", ()))
    assert actual == expected, (
        f"{schema_name} in the contract is {actual}, HiSim's is {expected}; the contract's enum "
        "lists are generated from HiSim (C3, A19)"
    )


def test_every_pending_contract_path_is_now_declared() -> None:
    """Every path on the pending list is a declared property of the branch's inventory schema.

    This is what "the contract PR empties the list" means, measured rather than asserted: the
    paths stay in :class:`PendingContractPaths` until the branch merges and the vendored copy is
    refreshed, and until then this test is the only thing that can tell whether the branch
    actually closed them.
    """
    missing = tuple(path for path in PendingContractPaths.paths() if not ContractCheckout.declares(path))
    assert missing == (), (
        "the contract does not declare these inventory paths that the translation layer writes: "
        + ", ".join(f"{path} (added by {PendingContractPaths.BY_PATH[path]})" for path in missing)
    )


def test_measure_id_enum_matches_the_catalogue() -> None:
    """``MeasureId`` lists exactly the ids of ``measures.yaml``, in catalogue order."""
    expected = tuple(str(entry["id"]) for entry in ContractCheckout.measures()["measures"])
    actual = tuple(str(value) for value in ContractCheckout.schema("MeasureId").get("enum", ()))
    assert actual == expected


def test_every_catalogue_id_is_the_derived_one() -> None:
    """Every measure and option id of the branch is what :class:`IdDerivation` produces.

    An explicit ``id`` is allowed to win over the derivation — that is how a measure survives being
    re-worded — but in the revision that *introduces* the ids the two must agree, because a
    difference there is a hand-typed id rather than a deliberate divergence.
    """
    wrong = []
    for measure in ContractCheckout.measures()["measures"]:
        display_name = str(measure["display_name"])
        derived = IdDerivation.upper_id(display_name)
        if str(measure.get("id")) != derived:
            wrong.append(f"measure '{display_name}': id={measure.get('id')!r}, derived={derived!r}")
        options = measure.get("options")
        assert options is not None, f"measure '{display_name}' still carries a null options list (N3)"
        for option in options:
            option_name = str(option["name"])
            option_derived = IdDerivation.lower_id(option_name)
            if str(option.get("id")) != option_derived:
                wrong.append(
                    f"option '{display_name}.{option_name}': id={option.get('id')!r}, "
                    f"derived={option_derived!r}"
                )
    assert wrong == [], "; ".join(wrong)
