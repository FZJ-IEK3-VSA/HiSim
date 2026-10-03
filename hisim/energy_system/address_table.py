"""The structured addresses of expanded components, as a realized record's metadata states them.

The expansion of imports (``assemblies_spec.md`` §2.3) names every member of every import by the
serialization of its structured address, ``pv-east-PVSystem``, and keeps the address itself beside
the file (:attr:`~hisim.energy_system.model.EnergySystemFile.addresses`). A realized record is
written from the expanded file and must reproduce the run without any assembly, so its metadata
carries the addresses too, under ``imports.addresses``; this module is the one place that writes
and reads that table, so that the record's writer and the reader re-running it agree on its shape.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Mapping

from hisim.config import AddressStep, ComponentID


class AddressTable:
    """Converts between a file's address table and the plain data of a record's metadata."""

    #: The metadata key of the import record.
    IMPORTS_KEY: ClassVar[str] = "imports"

    #: The key of the address table inside the import record.
    ADDRESSES_KEY: ClassVar[str] = "addresses"

    @classmethod
    def step_document(cls, step: AddressStep) -> Dict[str, Any]:
        """One address step as plain data: ``{import: pv, instance: east}``."""
        document: Dict[str, Any] = {"import": step.import_key}
        if step.instance is not None:
            document["instance"] = step.instance
        return document

    @classmethod
    def address_document(cls, identity: ComponentID) -> Dict[str, Any]:
        """One member's address as plain data."""
        document: Dict[str, Any] = {
            "path": [cls.step_document(step) for step in identity.path],
            "member": identity.name,
        }
        if identity.assembly is not None:
            document["assembly"] = identity.assembly
        return document

    @classmethod
    def to_document(cls, addresses: Mapping[str, ComponentID]) -> Dict[str, Any]:
        """The whole table, by expanded name, in the file's order."""
        return {name: cls.address_document(identity) for name, identity in addresses.items()}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> Dict[str, ComponentID]:
        """Reads the table a record's metadata carries; empty when it carries none.

        Args:
            metadata: The ``metadata`` block of a document.

        Returns:
            Expanded name to structured address.

        Raises:
            ValueError: If an entry's address does not serialize to the name it is listed under:
                the table is generated, so a disagreement means the record was edited by hand.
        """
        record = metadata.get(cls.IMPORTS_KEY)
        if not isinstance(record, Mapping):
            return {}
        table = record.get(cls.ADDRESSES_KEY)
        if not isinstance(table, Mapping):
            return {}
        addresses: Dict[str, ComponentID] = {}
        for name, entry in table.items():
            steps: List[AddressStep] = [
                AddressStep(import_key=str(step["import"]), instance=step.get("instance"))
                for step in entry.get("path", [])
            ]
            identity = ComponentID(name=str(entry["member"]), path=tuple(steps), assembly=entry.get("assembly"))
            if identity.address != name:
                raise ValueError(
                    f"The record's address table lists '{name}' with an address that serializes to "
                    f"'{identity.address}'; the table is generated and must not be edited."
                )
            addresses[str(name)] = identity
        return addresses
