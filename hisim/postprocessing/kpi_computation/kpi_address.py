"""Stable KPI addresses and a finder over a tag-sorted KPI collection (``roadmap/kpi_address_spec.md``).

A KPI is addressed by its building, its tag, its name and, for a component KPI, its structured
source (:class:`~hisim.postprocessing.kpi_computation.kpi_structure.KpiSource`). The collection
key -- ``"<name> (<source.name>)"`` for a component KPI, the bare name for a derived one -- is a
function of that address alone; :attr:`KpiAddress.key` is the one place it is built, and nothing
ever splits it. :class:`KpiFinder` enumerates and resolves entries by their fields, in process
(``KpiPreparation.finder``) and on a loaded ``all_kpis.json`` alike, which is why this module
imports nothing but :mod:`kpi_structure`.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from hisim.postprocessing.kpi_computation.kpi_structure import KpiSource, KpiTagEnumClass

#: The file ``WRITE_KPIS_TO_JSON`` writes the tag-sorted KPI collection to, in a result directory;
#: the one spelling every writer and reader of ``all_kpis.json`` uses.
ALL_KPIS_FILE_NAME: str = "all_kpis.json"


@dataclass(frozen=True)
class KpiAddress:
    """The address of one KPI entry: a function of the KPI itself, never of its neighbours.

    Attributes:
        building: The building object (or district) the entry belongs to.
        tag: The KPI tag as written in the JSON (the ``KpiTagEnumClass`` value).
        name: The entry's own name, never qualified.
        source: The component the KPI is reported for; ``None`` for a derived KPI.
    """

    building: str
    tag: str
    name: str
    source: Optional[KpiSource] = None

    @staticmethod
    def key_for(name: str, source: Optional[KpiSource]) -> str:
        """The collection key of an entry: ``"<name> (<source.name>)"``, or the bare name without source.

        Args:
            name: The entry's own name.
            source: Its source, or ``None`` for a derived KPI.

        Returns:
            The key the collection, ``all_kpis.json`` and the goldens address the entry by.
        """
        if source is None:
            return name
        return f"{name} ({source.name})"

    @property
    def key(self) -> str:
        """The collection key of this entry (:meth:`key_for`)."""
        return self.key_for(self.name, self.source)

    @property
    def dotted(self) -> str:
        """``"<building>.<tag>.<key>"``, the golden references' flat form."""
        return f"{self.building}.{self.tag}.{self.key}"


class KpiFinder:
    """Enumerates and resolves the entries of a tag-sorted KPI collection by their fields.

    The collection is nested ``building -> tag -> key -> entry dict``: ``all_kpis.json`` as
    loaded, or ``KpiGenerator.kpi_collection_dict_sorted`` in process. Every filter is optional
    and exact; ``source`` matches ``source.name``, and ``tag`` takes a ``KpiTagEnumClass`` member
    or its string value. The finder reads each entry's source from the
    entry (:meth:`KpiSource.from_entry_dict`), never from its key, and checks on construction that
    every key is the one its entry's address produces, so a collection whose keys and entries
    disagree is refused rather than half-read. A JSON written before the source existed is read
    too: an entry without ``source`` takes its source name from ``nameOfSourceComponent``, and its
    key may be the bare name it had while it was the only one of that name in its building; its
    address is then the one of today's scheme.

    Args:
        sorted_collection: ``building -> tag -> key -> entry``.

    Raises:
        ValueError: If the collection or a level of it is not a mapping, an entry has no ``name``,
            no ``value`` or no ``tag``, or a key differs from the key its entry's address produces.
    """

    def __init__(self, sorted_collection: Mapping[str, Mapping[Any, Mapping[str, Mapping[str, Any]]]]) -> None:
        """Index every entry of the collection by its address."""
        if not isinstance(sorted_collection, Mapping):
            raise ValueError(
                f"all_kpis.json holds a JSON {self._json_type(sorted_collection)}, not a KPI collection "
                "(an object of building -> tag -> key -> entry)."
            )
        self._entries: List[Tuple[KpiAddress, Mapping[str, Any]]] = []
        for building, tags in sorted_collection.items():
            if not isinstance(tags, Mapping):
                raise ValueError(f"KPI collection: building '{building}' does not hold a mapping of tags.")
            for tag, entries in tags.items():
                tag_name = self._tag_name(tag)
                if not isinstance(entries, Mapping):
                    raise ValueError(f"KPI collection: {building}.{tag_name} does not hold a mapping of entries.")
                for key, entry in entries.items():
                    self._entries.append((self._address_of(building, tag_name, key, entry), entry))

    @staticmethod
    def _json_type(document: Any) -> str:
        """The JSON name of a parsed document's type, for the message refusing it."""
        if document is None:
            return "null"
        if isinstance(document, bool):
            return "boolean"
        if isinstance(document, (int, float)):
            return "number"
        if isinstance(document, str):
            return "string"
        if isinstance(document, (list, tuple)):
            return "list"
        return type(document).__name__

    @staticmethod
    def _tag_name(tag: Any) -> str:
        """A tag as the JSON writes it: a ``KpiTagEnumClass`` member's value, or the string itself.

        The one normalisation for the tags the index stores and the tag a caller filters by, so
        ``tag=KpiTagEnumClass.BATTERY`` and ``tag="Battery"`` select the same entries.
        """
        return str(getattr(tag, "value", tag))

    @staticmethod
    def _address_of(building: str, tag: str, key: str, entry: Any) -> KpiAddress:
        """The address of one entry, refusing an untagged entry and one whose key its address does not produce."""
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
            raise ValueError(f"KPI collection: {building}.{tag}.{key} is not a KPI entry with a name.")
        if "value" not in entry:
            raise ValueError(
                f"KPI collection: {building}.{tag}.{key} has no 'value' and so is not a KPI entry; "
                'an entry whose value is not known carries "value": null.'
            )
        source = KpiSource.from_entry_dict(entry)
        if entry.get("tag") is None:
            reported_for = f"the component '{source.name}'" if source is not None else "no component (a derived KPI)"
            raise ValueError(
                f"KPI collection: the KPI entry '{entry['name']}' under '{building}.{tag}.{key}', reported for "
                f"{reported_for}, carries no tag. Every KPI entry is filed under a KpiTagEnumClass tag; an "
                "untagged one would be filed under the tag 'None'."
            )
        address = KpiAddress(building=str(building), tag=tag, name=entry["name"], source=source)
        # An entry written before the source existed was keyed by its bare name while it was the
        # only one of that name in its building; its address is the one of today's scheme.
        written_before_source = "source" not in entry and key == address.name
        if address.key != key and not written_before_source:
            raise ValueError(
                f"KPI collection: the entry under '{building}.{tag}.{key}' addresses itself as "
                f"'{address.dotted}'. A key is the entry's name, qualified with its source's name "
                "for a component KPI; a collection whose keys disagree with their entries cannot be "
                "read by address."
            )
        return address

    def _matching(
        self,
        building: Optional[str] = None,
        tag: Optional[Union[str, KpiTagEnumClass]] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        import_key: Optional[str] = None,
        instance: Optional[str] = None,
        member: Optional[str] = None,
        assembly: Optional[str] = None,
        derived: bool = False,
    ) -> Iterator[Tuple[KpiAddress, Mapping[str, Any]]]:
        """Yield every entry whose address matches all the given filters; ``derived`` keeps the entries without source.

        Raises:
            ValueError: For ``derived`` together with a source filter, which no entry can match.
        """
        source_filters: Dict[str, Optional[str]] = {
            "name": source,
            "import_key": import_key,
            "instance": instance,
            "member": member,
            "assembly": assembly,
        }
        wanted_source_fields = {field: value for field, value in source_filters.items() if value is not None}
        if derived and wanted_source_fields:
            raise ValueError(
                f"A derived KPI has no source, so it matches no source filter ({', '.join(wanted_source_fields)})."
            )
        wanted_tag = None if tag is None else self._tag_name(tag)
        for address, entry in self._entries:
            if building is not None and address.building != building:
                continue
            if wanted_tag is not None and address.tag != wanted_tag:
                continue
            if name is not None and address.name != name:
                continue
            if derived and address.source is not None:
                continue
            if wanted_source_fields:
                if address.source is None:
                    continue
                if any(getattr(address.source, field) != value for field, value in wanted_source_fields.items()):
                    continue
            yield address, entry

    def addresses(
        self,
        *,
        building: Optional[str] = None,
        tag: Optional[Union[str, KpiTagEnumClass]] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        import_key: Optional[str] = None,
        instance: Optional[str] = None,
        member: Optional[str] = None,
        assembly: Optional[str] = None,
    ) -> List[KpiAddress]:
        """Every address matching the filters, in collection order; no filter lists the whole collection."""
        return [
            address
            for address, _ in self._matching(building, tag, name, source, import_key, instance, member, assembly)
        ]

    def entries(
        self,
        *,
        building: Optional[str] = None,
        tag: Optional[Union[str, KpiTagEnumClass]] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        import_key: Optional[str] = None,
        instance: Optional[str] = None,
        member: Optional[str] = None,
        assembly: Optional[str] = None,
        derived: bool = False,
    ) -> List[Tuple[KpiAddress, Mapping[str, Any]]]:
        """Every ``(address, entry)`` matching the filters, in collection order; ``derived``: those without source."""
        return list(self._matching(building, tag, name, source, import_key, instance, member, assembly, derived))

    def one(
        self,
        *,
        building: Optional[str] = None,
        tag: Optional[Union[str, KpiTagEnumClass]] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        import_key: Optional[str] = None,
        instance: Optional[str] = None,
        member: Optional[str] = None,
        assembly: Optional[str] = None,
        derived: bool = False,
    ) -> Tuple[KpiAddress, Mapping[str, Any]]:
        """The one entry matching the filters; ``derived`` keeps the entries without source.

        Raises:
            ValueError: If none or several match, naming the filters and every candidate, so a
                lookup that meant "the car's distance" in a two-car setup fails by name.
        """
        found = list(self._matching(building, tag, name, source, import_key, instance, member, assembly, derived))
        if len(found) == 1:
            return found[0]
        filters = {
            "building": building,
            "tag": None if tag is None else self._tag_name(tag),
            "name": name,
            "source": source,
            "import_key": import_key,
            "instance": instance,
            "member": member,
            "assembly": assembly,
            "derived": derived or None,
        }
        described = ", ".join(f"{field}={value!r}" for field, value in filters.items() if value is not None)
        if not found:
            raise ValueError(f"No KPI matches {described or 'no filter'}.")
        candidates = "\n".join(f"  {address.dotted}" for address, _ in found)
        raise ValueError(f"{len(found)} KPIs match {described or 'no filter'}; expected one:\n{candidates}")

    def value(
        self,
        *,
        building: Optional[str] = None,
        tag: Optional[Union[str, KpiTagEnumClass]] = None,
        name: Optional[str] = None,
        source: Optional[str] = None,
        import_key: Optional[str] = None,
        instance: Optional[str] = None,
        member: Optional[str] = None,
        assembly: Optional[str] = None,
        derived: bool = False,
    ) -> Any:
        """The ``value`` of :meth:`one` (a float for numeric KPIs, a string or ``None`` otherwise)."""
        _, entry = self.one(
            building=building,
            tag=tag,
            name=name,
            source=source,
            import_key=import_key,
            instance=instance,
            member=member,
            assembly=assembly,
            derived=derived,
        )
        return entry["value"]
