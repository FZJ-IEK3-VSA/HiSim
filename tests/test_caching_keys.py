"""Tests for :mod:`hisim.caching.keys`: the key scheme of spec §3 and the import-closure walk behind it.

A key must change when anything that can change the result changes -- inputs, producer code, an imported
module, a library version -- and must not change for anything else. The closure walk is tested against a
small package written into a temporary directory, so the tests can edit source and add imports freely.
"""

# clean

import dataclasses
import enum
import hashlib
import importlib
import pathlib
import sys
from types import ModuleType
from typing import Any, ClassVar, Dict

import pytest

from hisim.caching import (
    CacheKey,
    CacheKeyError,
    CanonicalJson,
    Fingerprints,
    ImportClosure,
    KeyMaterial,
    ProducerLayering,
    ProducerLayeringError,
)

__authors__ = "Noah Pflugradt"
__copyright__ = "Copyright 2021-2026, FZJ-IEK-3 "
__license__ = "MIT"
__version__ = "1"
__maintainer__ = "Noah Pflugradt"
__email__ = "n.pflugradt@fz-juelich.de"
__status__ = "development"


class Orientation(enum.Enum):
    """A stand-in for the enums real DTOs carry."""

    SOUTH = "south"
    EAST = "east"


@dataclasses.dataclass(frozen=True)
class Location:
    """A nested DTO, to prove recursion follows the same rules."""

    latitude: float
    longitude: float


@dataclasses.dataclass(frozen=True)
class PvInputs:
    """A DTO of the shape spec §3.1 describes: primitives, an enum, a nested DTO, a key, and a payload."""

    peak_power_in_watt: float
    orientation: Orientation
    location: Location
    weather_artifact_key: str
    weather_frame: Any = dataclasses.field(default=None, metadata=KeyMaterial.PAYLOAD)


class SyntheticPackage:
    """Writes a small package named ``acme`` into a directory and imports modules from it.

    Using a synthetic package keeps the tests independent of what HiSim itself imports. :meth:`load`
    drops any cached ``acme`` modules first, so a test can edit a file and re-import it.
    """

    ROOT: ClassVar[str] = "acme"

    def __init__(self, directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Creates the empty package on the path.

        Args:
            directory: the directory to write into.
            monkeypatch: used to put the directory on ``sys.path`` for the test's lifetime.
        """
        self.directory = directory
        (directory / self.ROOT).mkdir()
        (directory / self.ROOT / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.syspath_prepend(str(directory))
        self._written: Dict[str, str] = {}

    def write(self, module: str, source: str) -> None:
        """Writes or overwrites one module of the package.

        Args:
            module: the dotted name below the root, e.g. ``"producer"`` or ``"sub.helper"``.
            source: the module's source.
        """
        parts = module.split(".")
        folder = self.directory / self.ROOT
        for part in parts[:-1]:
            folder = folder / part
            folder.mkdir(exist_ok=True)
            init = folder / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
        (folder / f"{parts[-1]}.py").write_text(source, encoding="utf-8")
        self._written[module] = source

    def load(self, module: str) -> ModuleType:
        """Imports a module of the package freshly.

        Args:
            module: the dotted name below the root.

        Returns:
            ModuleType: the imported module.
        """
        qualified = f"{self.ROOT}.{module}"
        for name in list(sys.modules):
            if name == self.ROOT or name.startswith(self.ROOT + "."):
                del sys.modules[name]
        importlib.invalidate_caches()
        return importlib.import_module(qualified)

    def closure(self, module: str) -> ImportClosure:
        """Imports a module and computes its closure within the package.

        Args:
            module: the dotted name below the root.

        Returns:
            ImportClosure: the closure.
        """
        return ImportClosure.of(self.load(module), root_package=self.ROOT)


def inputs(**overrides: Any) -> PvInputs:
    """A baseline DTO, with any field replaced.

    Args:
        **overrides: fields to change from the baseline.

    Returns:
        PvInputs: the DTO.
    """
    baseline: Dict[str, Any] = {
        "peak_power_in_watt": 5000.0,
        "orientation": Orientation.SOUTH,
        "location": Location(latitude=50.77, longitude=6.08),
        "weather_artifact_key": "weather/abc123",
        "weather_frame": None,
    }
    baseline.update(overrides)
    return PvInputs(**baseline)


@pytest.mark.base
def test_canonical_json_is_sorted_renders_enums_by_value_and_recurses() -> None:
    """The three rules of spec §3.1, in one string.

    Catches: field order leaking into the key, an enum rendered by name, or a nested DTO rendered by
    ``repr``.
    """
    rendered = CanonicalJson.dumps(inputs())

    assert rendered == (
        '{"location":{"latitude":50.77,"longitude":6.08},"orientation":"south",'
        '"peak_power_in_watt":5000.0,"weather_artifact_key":"weather/abc123"}'
    )


@pytest.mark.base
def test_a_payload_field_never_reaches_the_key() -> None:
    """Two DTOs that differ only in a payload field have the same key.

    A payload field is identified by the key field beside it (for example ``weather_artifact_key``), so
    hashing the payload too would be redundant, and for a DataFrame impossible.
    """
    with_frame = inputs(weather_frame={"huge": list(range(1000))})
    without_frame = inputs(weather_frame=None)

    assert CanonicalJson.dumps(with_frame) == CanonicalJson.dumps(without_frame)


@pytest.mark.base
def test_every_key_field_moves_the_key() -> None:
    """L1 of the cache-testing spec, for the DTO scheme: perturb one input, the key changes.

    Catches: a key that silently drops a field, which is spec ``roadmap/pylpg_flakiness.md`` F7 in
    its new clothes.
    """
    baseline = CanonicalJson.dumps(inputs())

    assert CanonicalJson.dumps(inputs(peak_power_in_watt=5001.0)) != baseline
    assert CanonicalJson.dumps(inputs(orientation=Orientation.EAST)) != baseline
    assert CanonicalJson.dumps(inputs(location=Location(latitude=50.78, longitude=6.08))) != baseline
    assert CanonicalJson.dumps(inputs(weather_artifact_key="weather/def456")) != baseline


@pytest.mark.base
def test_a_value_with_no_canonical_form_is_refused_by_field_name() -> None:
    """A value the scheme cannot render raises an error naming the field, instead of being stringified.

    Stringifying would let two different inputs share a key whenever their ``str`` is the same.
    """

    @dataclasses.dataclass(frozen=True)
    class Bad:
        """A DTO holding something with no canonical form."""

        handle: object

    with pytest.raises(CacheKeyError, match="handle"):
        CanonicalJson.dumps(Bad(handle=object()))


@pytest.mark.base
def test_a_mapping_with_a_non_string_key_is_refused() -> None:
    """A mapping key that is not a string raises, naming the field, instead of being coerced.

    Coercion would let ``{1: ...}`` and ``{"1": ...}`` render identically -- two different inputs,
    one cache key, which is the exact defect the canonical form exists to prevent.
    """

    @dataclasses.dataclass(frozen=True)
    class WithMapping:
        """A DTO holding a mapping with an integer key."""

        table: Dict[Any, str]

    with pytest.raises(CacheKeyError, match="table"):
        CanonicalJson.dumps(WithMapping(table={1: "x"}))


@pytest.mark.base
def test_a_non_finite_float_is_refused() -> None:
    """A NaN or infinity raises, naming the field, instead of rendering as a non-JSON token.

    Every NaN would render as the same ``NaN``, so two different inputs would share a key -- and the
    ``.meta`` file would not even be parseable JSON.
    """

    @dataclasses.dataclass(frozen=True)
    class WithFloat:
        """A DTO holding one float."""

        value: float

    with pytest.raises(CacheKeyError, match="value"):
        CanonicalJson.dumps(WithFloat(value=float("nan")))
    with pytest.raises(CacheKeyError, match="value"):
        CanonicalJson.dumps(WithFloat(value=float("inf")))


@pytest.mark.base
def test_a_non_dataclass_is_not_a_dto() -> None:
    """Spec §3.1: exactly one frozen dataclass. A dict is refused so the rule stays visible.

    Catches: producers drifting to loose dicts, which have no declared field set to check.
    """
    with pytest.raises(CacheKeyError, match="dataclass"):
        CanonicalJson.dumps({"peak_power_in_watt": 5000.0})


@pytest.mark.base
def test_the_closure_follows_package_imports_and_records_third_parties(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk finds every package module the producer reaches and records third-party imports by name.

    Covers the four spellings: ``import a.b``, ``from a import b`` (module), ``from a.b import name``
    (attribute) and a relative import. Standard-library imports are neither followed nor recorded.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("sub.helper", "import math\nVALUE = 1\n")
    package.write("sub.__init__", "")
    package.write("tables", "import numpy\nTABLE = [1, 2]\n")
    package.write("shapes", "SHAPE = 'flat'\n")
    package.write(
        "producer",
        "import json\n"
        "import acme.tables\n"
        "from acme.sub import helper\n"
        "from acme.shapes import SHAPE\n"
        "from . import shapes as again\n"
        "import pandas as pd\n",
    )

    closure = package.closure("producer")

    assert closure.package_modules == ("acme.producer", "acme.shapes", "acme.sub.helper", "acme.tables")
    assert closure.third_party_top_levels == ("numpy", "pandas")
    assert not closure.dynamic_import_sites


@pytest.mark.base
def test_a_relative_import_in_the_root_init_is_followed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``from . import helper`` in the root package's ``__init__`` resolves to a package module.

    A package's ``__init__`` resolves relative imports against the package itself, not against a
    parent. Getting this wrong files ``helper`` as a third-party name, its source is never
    fingerprinted, and edits to it stop invalidating cache entries.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("helper", "import numpy\nVALUE = 1\n")
    (tmp_path / SyntheticPackage.ROOT / "__init__.py").write_text("from . import helper\n", encoding="utf-8")
    package.write("producer", "import acme\n")

    closure = package.closure("producer")

    assert "acme.helper" in closure.package_modules
    assert "helper" not in closure.third_party_top_levels
    assert closure.third_party_top_levels == ("numpy",)


@pytest.mark.base
def test_the_code_fingerprint_moves_with_a_dependency_and_not_with_a_stranger(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing an imported module changes the fingerprint; editing an unrelated module does not.

    This is the reason the closure is fingerprinted instead of the commit: a KPI edit in a component
    invalidates nothing, a change to physics the producer imports invalidates exactly its entries.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("physics", "COEFFICIENT = 0.6\n")
    package.write("plumbing", "KPI_NAME = 'a'\n")
    package.write("producer", "from acme import physics\n")
    before = Fingerprints.code(package.closure("producer"))

    package.write("plumbing", "KPI_NAME = 'b'\n")
    after_stranger = Fingerprints.code(package.closure("producer"))
    package.write("physics", "COEFFICIENT = 0.7\n")
    after_dependency = Fingerprints.code(package.closure("producer"))

    assert after_stranger == before, "an unrelated module's edit reached the fingerprint"
    assert after_dependency != before, "a dependency's edit did not reach the fingerprint"


@pytest.mark.base
def test_the_third_party_fingerprint_pins_versions_and_the_interpreter(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each third-party import contributes ``name==version``; the interpreter contributes its major.minor.

    Catches: a fingerprint that names packages without versions, so a library upgrade that changes
    results leaves every entry looking current.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("producer", "import numpy\nimport json\n")

    fingerprint = Fingerprints.third_party(package.closure("producer"))

    pins = fingerprint.split(";")
    assert f"python=={sys.version_info.major}.{sys.version_info.minor}" in pins
    numpy_pins = [pin for pin in pins if pin.startswith("numpy==")]
    assert len(numpy_pins) == 1 and numpy_pins[0] != "numpy==unknown"
    assert not any(pin.startswith("json==") for pin in pins), "the standard library must not be pinned by package"


@pytest.mark.base
def test_an_unresolvable_third_party_version_refuses_the_key(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dependency whose version cannot be resolved raises instead of pinning ``==unknown``.

    An ``unknown`` pin would let two environments running different code for that package share
    cache entries -- the failure the fingerprint exists to prevent.
    """
    del tmp_path, monkeypatch
    closure = ImportClosure(
        root_package="acme",
        entry_module="acme.producer",
        package_modules=("acme.producer",),
        module_files={},
        third_party_top_levels=("package_that_is_not_installed_anywhere",),
        dynamic_import_sites=(),
    )

    with pytest.raises(CacheKeyError, match="package_that_is_not_installed_anywhere"):
        Fingerprints.third_party(closure)


@pytest.mark.base
def test_component_machinery_in_the_closure_is_a_layering_violation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closure containing ``hisim.component`` is reported as a violation naming that module.

    The check runs on the whole closure, so an import two hops away is caught as well.
    """
    del tmp_path, monkeypatch
    closure = ImportClosure(
        root_package="hisim",
        entry_module="hisim.components.pv_calculation",
        package_modules=("hisim.component", "hisim.components.pv_calculation"),
        module_files={},
        third_party_top_levels=(),
        dynamic_import_sites=(),
    )

    violations = ProducerLayering.violations(closure)

    assert len(violations) == 1
    assert "hisim.component" in violations[0]
    with pytest.raises(ProducerLayeringError, match="hisim.component"):
        ProducerLayering.check(closure)
    # The error blames the producer the walk started from, not whichever closure module sorts first.
    with pytest.raises(ProducerLayeringError, match="The producer hisim.components.pv_calculation"):
        ProducerLayering.check(closure)


@pytest.mark.base
def test_a_dynamic_import_is_a_layering_violation_with_a_line_number(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``importlib.import_module`` and ``__import__`` are found and reported with their location.

    Catches: a dependency the static walk cannot see, which is a stale entry it cannot prevent.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("producer", "import importlib\n\ndef load(name):\n    return importlib.import_module(name)\n")

    closure = package.closure("producer")

    assert closure.dynamic_import_sites == ("acme.producer:4",)
    assert any("acme.producer:4" in violation for violation in ProducerLayering.violations(closure))


@pytest.mark.base
def test_an_import_module_method_on_another_object_is_not_a_dynamic_import(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``importlib.import_module`` and the bare names count; an unrelated object's method does not.

    Catches: the detector rejecting an innocent producer because some object happens to expose a
    method called ``import_module``.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write(
        "producer",
        "class Loader:\n"
        "    def import_module(self, name):\n"
        "        return name\n"
        "\n"
        "RESULT = Loader().import_module('json')\n",
    )

    closure = package.closure("producer")

    assert not closure.dynamic_import_sites


@pytest.mark.base
def test_a_cache_key_is_the_four_parts_and_its_digest_follows_them(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §3 spelled out: material is ``kind:code:third_party:dto`` and the digest is its sha256.

    Catches: the material being assembled in another order or with another separator than the
    metadata files record.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("producer", "import numpy\n")
    closure = package.closure("producer")

    key = CacheKey(
        artifact_kind="pv_series",
        code_fingerprint=Fingerprints.code(closure),
        third_party_fingerprint=Fingerprints.third_party(closure),
        dto_json=CanonicalJson.dumps(inputs()),
    )

    parts = key.material.split(CacheKey.SEPARATOR, 3)
    assert parts[0] == "pv_series"
    assert parts[1] == key.code_fingerprint
    assert parts[2] == key.third_party_fingerprint
    assert parts[3] == key.dto_json
    assert key.digest == hashlib.sha256(key.material.encode("utf-8")).hexdigest(), (
        "the digest must be the sha256 of the material -- the same rule CacheEntryMetadata.hash_of applies"
    )


@pytest.mark.base
@pytest.mark.parametrize("kind", ["", "pv series", "pv/series", "-pv", "pv.series"])
def test_an_artifact_kind_that_cannot_be_a_path_segment_is_refused(kind: str) -> None:
    """The kind is a filename prefix and a URL segment, so it is validated as one.

    Catches: a kind with a slash or a space producing a path that means something else.
    """
    with pytest.raises(CacheKeyError, match="Artifact kind"):
        CacheKey(artifact_kind=kind, code_fingerprint="c", third_party_fingerprint="t", dto_json="{}")


@pytest.mark.base
def test_for_producer_runs_the_layering_check(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Building a key for a non-compliant producer fails at the layering check, before any hashing.

    Catches: a key being issued for a producer whose fingerprint cannot be trusted.
    """
    package = SyntheticPackage(tmp_path, monkeypatch)
    package.write("producer", "import importlib\nMODULE = importlib.import_module('json')\n")
    module = package.load("producer")

    with pytest.raises(ProducerLayeringError, match="dynamically"):
        CacheKey.for_producer("pv_series", module, inputs())
