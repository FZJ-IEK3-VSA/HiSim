"""Named default presets of a config class: the ``Catalog`` helper and its provenance.

A component configuration usually has several defensible defaults — a condensing gas
boiler, an oil boiler, a pellet boiler, all on one ``GenericBoilerConfig`` — which HiSim
used to express as a hundred-odd ``get_*default*`` factory methods with sixty-odd naming
spellings. The preset design replaces them with a
single ``presets`` :class:`Catalog` per class: a small mapping from a preset name to a
zero-argument builder, so a preset reference autocompletes, jumps to its definition, and
can be enumerated by tests, the JSON executor and a future GUI palette without a regex.

The module is a leaf of the ``hisim.config`` package: it imports nothing at all from
HiSim, which is why :mod:`hisim.config.sizing` can depend on it (it carries the
provenance stamp onto resolved copies) rather than the other way round.
"""

# clean

from __future__ import annotations

from typing import Any, Callable, ClassVar, Dict, Iterator, Optional, Tuple


class Catalog:
    """Attribute-style access to the named default presets of one config class.

    ``SomeConfig.presets.oil`` builds and returns a **fresh instance** on every access
    (setups mutate configs freely, so sharing instances would be a footgun), lazily (no
    I/O at import time). The first entry is the canonical default, reachable as
    ``presets.canonical``; iteration yields ``(name, builder)`` pairs for the contract
    test, the executor and the GUI palette. Preset names are wire format — scenario
    files reference them by name, so a rename is a breaking change; choose them with
    the care of API names.

    Every instance a Catalog access builds is stamped with its **preset provenance** —
    the preset's attribute name, under :attr:`PROVENANCE_ATTRIBUTE` — using the same
    non-field-attribute technique as ``sizing_record``: serialization, dataclass equality
    and ``dataclasses.replace`` all ignore it, ``resolve``/``resolve_all`` carry it onto
    the resolved copy, and a manually constructed config simply has none. The template
    creator reads it to emit ``"preset": "<name>"`` entries (v2 spec decision 21).
    """

    #: Name of the non-field attribute stamped onto every instance a Catalog access
    #: builds, carrying the preset's attribute name (e.g. ``"oil"``). Read through
    #: :func:`preset_provenance` rather than a raw ``getattr``.
    PROVENANCE_ATTRIBUTE: ClassVar[str] = "preset_provenance"

    def __init__(self, **builders: Callable[[], Any]) -> None:
        """Registers the named builders, first one becoming the canonical default.

        Args:
            **builders: Zero-argument callables producing a fresh config each.

        Raises:
            ValueError: If no builder is given, or a name would shadow a Catalog member.
        """
        if not builders:
            raise ValueError("a Catalog needs at least one preset")
        reserved = {"canonical", "names"}
        clashes = reserved.intersection(builders)
        if clashes:
            raise ValueError(f"preset names {sorted(clashes)} shadow Catalog members")
        self._builders: Dict[str, Callable[[], Any]] = dict(builders)
        self._canonical_name = next(iter(builders))

    def _build(self, name: str) -> Any:
        """Builds one fresh preset instance and stamps its preset provenance onto it.

        The provenance is a plain attribute rather than a dataclass field (the
        ``sizing_record`` technique), so ``to_dict`` never emits it, dataclass equality
        never compares it, and a config built any other way simply does not carry it —
        provenance means "this instance came from this Catalog".
        """
        instance = self._builders[name]()
        setattr(instance, self.PROVENANCE_ATTRIBUTE, name)
        return instance

    def __getattr__(self, name: str) -> Any:
        """Builds and returns a fresh, provenance-stamped instance of the named preset."""
        if name not in self._builders:
            raise AttributeError(
                f"no preset named '{name}'; available: {', '.join(self._builders)}"
            )
        return self._build(name)

    @property
    def canonical(self) -> Any:
        """Fresh instance of the canonical preset, which is the first-declared one."""
        return self._build(self._canonical_name)

    def names(self) -> Tuple[str, ...]:
        """Names of all presets, canonical first, in declaration order."""
        return tuple(self._builders)

    def __iter__(self) -> Iterator[Tuple[str, Callable[[], Any]]]:
        """Iterates ``(name, builder)`` pairs in declaration order."""
        return iter(self._builders.items())


def preset_provenance(config: Any) -> Optional[str]:
    """Returns the preset name a config instance was built from, or ``None``.

    The name is the :class:`Catalog` attribute name stamped onto the instance at access
    time and carried through ``resolve_config`` — the exact string a v2 scenario entry
    spells as its ``"preset"`` value (decision 21). Manually constructed configs and
    configs deserialized from files carry no provenance and return ``None``, which is
    what tells the template creator to fall back to a full config dump.
    """
    provenance = getattr(config, Catalog.PROVENANCE_ATTRIBUTE, None)
    return provenance if isinstance(provenance, str) else None
