"""The vendored copies of the RenoVisor contract that this HiSim speaks.

The contract lives in two places. ``measures.yaml`` (the catalogue of renovation measures),
``materials.yaml`` (the insulation-material database) and ``openapi.yaml`` (the superseded v0.3
draft) come from the separate repository ``climatemedia/renovisor-api-contract``, which is
co-owned by the RenoVisor frontend and backend teams. ``calculation-request.schema.json`` (the
request the translator validates against), ``calculation-request.mockup-1.yaml`` (the worked
example every probe set anchors on) and ``measure-capabilities.openapi.yaml`` (the shape of the
capability document the translator generates) come from the frontend side's proposal directory
``~/contract-proposals`` and are vendored as local files until they move into the contract
repository. This package holds a copy of each, together with ``PINNED.yaml``, which records where
every copy came from and the content hash it had at that moment.

One file here is not a copy at all. ``measure-capabilities.results-extension.yaml`` is HiSim's
own proposal back to the frontend team -- the shape of the capability document's ``results``
section, which says what ``result.json`` will carry -- and is therefore listed in
:attr:`ContractFiles.HISIM_AUTHORED` and carries no pin: a pin records which revision of somebody
else's file a copy came from, and there is no such revision.

Why copies and not a dependency: the decision of 2026-09-15 (``roadmap/renovisor/challenges.md``,
Q28) was "vendored copy for now"; where the master version of the contract lives is still to be
discussed in the project, and the installable-package option is on the table. Until then the
copies are made safe by :mod:`hisim.renovisor.contract.refresh`, which is the only sanctioned way
to change these files, and by ``tests/renovisor/test_contract.py``, which fails when a copy no
longer matches the hash ``PINNED.yaml`` records -- so a hand edit of a vendored copy, or a refresh
that forgot to update the pin, is a failing build rather than silent drift.

``openapi.yaml`` is pinned with ``authoritative: false``: it is the v0.3 draft written before the
energy-system redesign and is superseded by ``calculation-request.schema.json``. It stays vendored
only so that the revision the branch once aligned against remains a committed fact.

Reading the copies::

    from hisim.renovisor.contract import ContractFiles
    catalogue = ContractFiles.measures()          # parsed measures.yaml
    schema = ContractFiles.request_schema()       # parsed calculation-request.schema.json
    mockup = ContractFiles.request_mockup()       # parsed calculation-request.mockup-1.yaml
    shape = ContractFiles.capabilities_schema()   # parsed measure-capabilities.openapi.yaml
    results = ContractFiles.results_extension_schema()  # HiSim's own results-section proposal
    pin = ContractFiles.pinned()                  # parsed PINNED.yaml

Refreshing them from a local checkout and the proposal directory::

    python -m hisim.renovisor.contract.refresh ~/renovisor-api-contract --proposals ~/contract-proposals
"""

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple, cast

import yaml


class ContractFiles:
    """Locates and parses the vendored contract files.

    Every accessor reads the file beside this module and returns the parsed YAML as plain
    dictionaries and lists, exactly as ``yaml.safe_load`` produces them. Nothing is cached,
    because the files are small and a cache would hide a refresh that happened during a long
    test session.
    """

    #: The directory holding the vendored copies: the directory of this module.
    DIRECTORY: ClassVar[Path] = Path(__file__).resolve().parent

    #: File names of the three vendored contract files and the pin record.
    OPENAPI_FILENAME: ClassVar[str] = "openapi.yaml"
    MEASURES_FILENAME: ClassVar[str] = "measures.yaml"
    MATERIALS_FILENAME: ClassVar[str] = "materials.yaml"
    REQUEST_SCHEMA_FILENAME: ClassVar[str] = "calculation-request.schema.json"
    REQUEST_MOCKUP_FILENAME: ClassVar[str] = "calculation-request.mockup-1.yaml"
    CAPABILITIES_SCHEMA_FILENAME: ClassVar[str] = "measure-capabilities.openapi.yaml"
    PINNED_FILENAME: ClassVar[str] = "PINNED.yaml"

    #: The one schema in this directory HiSim wrote itself rather than copied: the proposal for
    #: the capability document's ``results`` section. It is not pinned, because a pin records
    #: which revision of somebody else's file a copy came from and there is no such revision.
    RESULTS_EXTENSION_FILENAME: ClassVar[str] = "measure-capabilities.results-extension.yaml"

    #: Every file here that is HiSim's own and therefore carries no pin.
    HISIM_AUTHORED: ClassVar[Tuple[str, ...]] = (RESULTS_EXTENSION_FILENAME,)

    @classmethod
    def path(cls, filename: str) -> Path:
        """Return the absolute path of one vendored file by its file name.

        Args:
            filename: One of the ``*_FILENAME`` class attributes.

        Returns:
            The path inside the vendored contract directory. The file is not checked for
            existence here; the parsing accessors raise ``FileNotFoundError`` when it is missing.
        """
        return cls.DIRECTORY / filename

    @classmethod
    def _load(cls, filename: str) -> Any:
        """Parse one vendored YAML file with ``yaml.safe_load``."""
        with cls.path(filename).open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)

    @classmethod
    def openapi(cls) -> Dict[str, Any]:
        """Return the parsed ``openapi.yaml`` (the OpenAPI 3.1 document as a dictionary)."""
        return cast(Dict[str, Any], cls._load(cls.OPENAPI_FILENAME))

    @classmethod
    def measures(cls) -> Dict[str, Any]:
        """Return the parsed ``measures.yaml``; its ``measures`` key holds the catalogue list."""
        return cast(Dict[str, Any], cls._load(cls.MEASURES_FILENAME))

    @classmethod
    def materials(cls) -> Dict[str, Any]:
        """Return the parsed ``materials.yaml``; its ``materials`` key holds the material list."""
        return cast(Dict[str, Any], cls._load(cls.MATERIALS_FILENAME))

    @classmethod
    def pinned(cls) -> Dict[str, Any]:
        """Return the parsed ``PINNED.yaml``: repository, and per file the source ref, commit and hash."""
        return cast(Dict[str, Any], cls._load(cls.PINNED_FILENAME))

    @classmethod
    def request_schema(cls) -> Dict[str, Any]:
        """Return the parsed ``calculation-request.schema.json``, the JSON Schema of the request.

        The translator validates every incoming request against this document itself rather than
        against a hand-written mirror of it, so the schema is the single source of truth for what
        a well-formed request is (step 8 section 3).

        Returns:
            The JSON Schema 2020-12 document as nested dictionaries and lists.
        """
        with cls.path(cls.REQUEST_SCHEMA_FILENAME).open(encoding="utf-8") as handle:
            return cast(Dict[str, Any], json.load(handle))

    @classmethod
    def request_mockup(cls) -> Dict[str, Any]:
        """Return the parsed ``calculation-request.mockup-1.yaml``: one worked calculation request.

        It is a 1975 Irish detached house with a five-measure package. Every probe of the
        capability document is a one-change patch on it, and the end-to-end test runs it, so the
        one example that the frontend side and this translator both point at is the same bytes.

        Returns:
            The request as nested dictionaries and lists.
        """
        return cast(Dict[str, Any], cls._load(cls.REQUEST_MOCKUP_FILENAME))

    @classmethod
    def capabilities_schema(cls) -> Dict[str, Any]:
        """Return the parsed ``measure-capabilities.openapi.yaml``: the capability document's shape.

        Its ``components.schemas.ImplementedMeasures`` is the schema the document written by
        ``python -m hisim.renovisor capabilities`` is validated against.

        Returns:
            The OpenAPI 3.1 document as nested dictionaries and lists.
        """
        return cast(Dict[str, Any], cls._load(cls.CAPABILITIES_SCHEMA_FILENAME))

    @classmethod
    def results_extension_schema(cls) -> Dict[str, Any]:
        """Return the parsed ``measure-capabilities.results-extension.yaml``.

        HiSim's own proposal, not a vendored copy: the shape of the capability document's
        ``results`` section, which says what ``result.json`` will carry. Its
        ``components.schemas.ResultFields`` is what
        :meth:`hisim.renovisor.capabilities.CapabilityDocument.validate` checks that section
        against, in addition to the vendored ``ImplementedMeasures`` the whole document is
        checked against.

        Returns:
            The OpenAPI 3.1 schema fragment as nested dictionaries and lists.
        """
        return cast(Dict[str, Any], cls._load(cls.RESULTS_EXTENSION_FILENAME))
