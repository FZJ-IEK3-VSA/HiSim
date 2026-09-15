"""The vendored copy of the RenoVisor API contract that this HiSim speaks.

The contract lives in the separate repository ``climatemedia/renovisor-api-contract`` and is
co-owned by the RenoVisor frontend and backend teams. HiSim needs three of its files at runtime
and in tests: ``openapi.yaml`` (the home inventory and package schemas an incoming request is
validated against), ``measures.yaml`` (the catalogue of renovation measures the registry is
checked against) and ``materials.yaml`` (the insulation-material database the materials import
reads). This package holds a copy of each, together with ``PINNED.yaml``, which records the
contract commit every copy was taken from and the content hash it had at that moment.

Why a copy and not a dependency: the decision of 2026-09-15 (``roadmap/renovisor/challenges.md``,
Q28) was "vendored copy for now"; where the master version of the contract lives is still to be
discussed in the project, and the installable-package option is on the table. Until then the copy
is made safe by :mod:`hisim.renovisor.contract.refresh`, which is the only sanctioned way to change
these files, and by ``tests/test_renovisor_contract.py``, which fails when a copy no longer matches
the hash ``PINNED.yaml`` records — so a hand edit of the vendored copy, or a refresh that forgot to
update the pin, is a failing build rather than silent drift.

Reading the copies::

    from hisim.renovisor.contract import ContractFiles
    schema = ContractFiles.openapi()      # parsed openapi.yaml
    catalogue = ContractFiles.measures()  # parsed measures.yaml
    pin = ContractFiles.pinned()          # parsed PINNED.yaml

Refreshing them from a local checkout of the contract repository::

    python -m hisim.renovisor.contract.refresh ~/renovisor-api-contract
"""

from pathlib import Path
from typing import Any, ClassVar, Dict

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
    PINNED_FILENAME: ClassVar[str] = "PINNED.yaml"

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
        return cls._load(cls.OPENAPI_FILENAME)

    @classmethod
    def measures(cls) -> Dict[str, Any]:
        """Return the parsed ``measures.yaml``; its ``measures`` key holds the catalogue list."""
        return cls._load(cls.MEASURES_FILENAME)

    @classmethod
    def materials(cls) -> Dict[str, Any]:
        """Return the parsed ``materials.yaml``; its ``materials`` key holds the material list."""
        return cls._load(cls.MATERIALS_FILENAME)

    @classmethod
    def pinned(cls) -> Dict[str, Any]:
        """Return the parsed ``PINNED.yaml``: repository, and per file the source ref, commit and hash."""
        return cls._load(cls.PINNED_FILENAME)
