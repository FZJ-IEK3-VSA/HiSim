"""The structured addresses of expanded components, as a realized record's metadata states them.

The expansion of imports (``assemblies_spec.md`` §2.3) names every member of every import by the
serialization of its structured address, ``pv-east-PVSystem``, and keeps the address itself beside
the file (:attr:`~hisim.energy_system.model.EnergySystemFile.addresses`). A realized record is
written from the expanded file and must reproduce the run without any assembly, so its metadata
carries the addresses too, under ``imports.addresses``; this module is the one place that writes
and reads that table, so that the record's writer and the reader re-running it agree on its shape.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Mapping, Tuple

from hisim.config import AddressStep, ComponentID
from hisim.energy_system.errors import EnergySystemErrorId, EnergySystemFormatError


class AddressTable:
    """Converts between a file's address table and the plain data of a record's metadata."""

    #: The metadata key of the import record.
    IMPORTS_KEY: ClassVar[str] = "imports"

    #: The key of the address table inside the import record.
    ADDRESSES_KEY: ClassVar[str] = "addresses"

    #: The keys :meth:`address_document` writes for one entry; ``path`` and ``member`` always.
    ENTRY_KEYS: ClassVar[Tuple[str, ...]] = ("path", "member", "assembly", "display_name")

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
        if identity.display_name is not None:
            document["display_name"] = identity.display_name
        return document

    @classmethod
    def to_document(cls, addresses: Mapping[str, ComponentID]) -> Dict[str, Any]:
        """The whole table, by expanded name, in the file's order."""
        return {name: cls.address_document(identity) for name, identity in addresses.items()}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any]) -> Dict[str, ComponentID]:
        """Reads the table a record's metadata carries; empty when it carries no import record.

        Args:
            metadata: The ``metadata`` block of a document.

        Returns:
            Expanded name to structured address.

        Raises:
            EnergySystemFormatError: ``EF-07`` when the import record is not a mapping, carries no
                address table, or an entry is not exactly what :meth:`address_document` writes —
                a path of steps with identifier keys, a member and an optional assembly — or its
                address does not serialize to the name it is listed under: the table is generated,
                so any of these means the record was edited by hand.
        """
        record = metadata.get(cls.IMPORTS_KEY)
        if record is None:
            return {}
        location = f"metadata.{cls.IMPORTS_KEY}.{cls.ADDRESSES_KEY}"
        table = record.get(cls.ADDRESSES_KEY) if isinstance(record, Mapping) else None
        if not isinstance(table, Mapping):
            raise cls._malformed(location, "the record carries an import record without its address table.")
        return {str(name): cls._entry(str(name), entry, f"{location}.{name}") for name, entry in table.items()}

    @classmethod
    def _entry(cls, name: str, entry: Any, location: str) -> ComponentID:
        """Reads one entry of the table, refusing anything :meth:`address_document` does not write."""
        if not isinstance(entry, Mapping) or not {"path", "member"} <= set(entry) <= set(cls.ENTRY_KEYS):
            raise cls._malformed(
                location, f"an address is a mapping of {', '.join(cls.ENTRY_KEYS)}; found {entry!r}."
            )
        path, member = entry["path"], entry["member"]
        assembly, display_name = entry.get("assembly"), entry.get("display_name")
        if (
            not isinstance(path, list)
            or not isinstance(member, str)
            or not all(isinstance(value, (str, type(None))) for value in (assembly, display_name))
        ):
            raise cls._malformed(
                location,
                "an address has a list 'path', a string 'member' and string 'assembly' and 'display_name'; "
                f"found {entry!r}.",
            )
        steps: List[AddressStep] = []
        for index, step in enumerate(path):
            if not isinstance(step, Mapping) or not {"import"} <= set(step) <= {"import", "instance"}:
                raise cls._malformed(
                    f"{location}.path[{index}]", f"a step is a mapping of import and instance; found {step!r}."
                )
            try:
                steps.append(AddressStep(import_key=step["import"], instance=step.get("instance")))
            except (TypeError, ValueError) as error:
                raise cls._malformed(
                    f"{location}.path[{index}]", f"the step {step!r} is no address step: {error}"
                ) from error
        try:
            identity = ComponentID(name=member, path=tuple(steps), assembly=assembly, display_name=display_name)
        except (TypeError, ValueError) as error:
            raise cls._malformed(location, f"the member '{member}' is no component name: {error}") from error
        if identity.address != name:
            raise cls._malformed(
                location, f"'{name}' is listed with an address that serializes to '{identity.address}'."
            )
        return identity

    @classmethod
    def _malformed(cls, location: str, problem: str) -> EnergySystemFormatError:
        """The refusal of a hand-edited address table (``EF-07``)."""
        return EnergySystemFormatError(
            EnergySystemErrorId.MALFORMED_BLOCK,
            location,
            problem,
            remedy=(
                "The table is generated and must not be edited; re-run the authored file that imports the "
                "assemblies."
            ),
        )
