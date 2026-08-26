"""Building every configuration of an energy-system file and resolving its sizing.

This is the stage between checking a file and constructing components: it turns each entry
into the configuration object its component will be handed, and then hands the whole set to
the sizing kernel so that every field left open by a law comes back as a number. Doing it in
that order — configurations complete and sized before the first component exists — is what
lets a file be rejected for a sizing contradiction without anything having been built, and it
is what lets a run write down what it would have built even when it is not going to run.

Three things happen per entry, in a fixed order. The configuration's *origin* is realized:
the named preset is called, or the named constructor is called with the entry's arguments, or
the entry's own complete block is deserialized. The *overrides* are applied on top, each value
decoded into the type its field holds, with the bare word ``AUTO`` re-opening a field that the
preset had pinned. Then the *paths* are expanded, turning the portable ``${inputs}/…``
spelling of the file into locations on this machine.

What follows is one call into the sizing kernel for the whole system. The file's
``sizing_sources`` blocks are handed over as the kernel's per-consumer mapping — a plain
translation, since the format was designed to carry exactly what the kernel asks for — and the
kernel's eight failure modes come back wrapped with the entry and the file location that
caused them, its own message kept verbatim because it already names the candidates and prints
the block an author can paste. A provider nobody read is not a failure but a warning: it is
legal, occasionally intended, and always worth saying out loud.
"""

# clean

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, ClassVar, List, Mapping, Optional, Tuple

from hisim.config.presets import ConfigBuilder
from hisim.config.report import ResolutionReport
from hisim.energy_system.bindings import ClassBinding, ClassBindings
from hisim.energy_system.classes import validate_classes
from hisim.energy_system.codec import ConfigValueCodec
from hisim.energy_system.errors import EnergySystemBindingError, EnergySystemErrorId
from hisim.energy_system.model import EnergySystemFile
from hisim.energy_system.path_resolver import PathFieldCodec, PathResolver
from hisim.energy_system.sizing_bridge import (
    resolve_sizing,
    sizing_sources_bridge,
    unconsumed_warnings,
)
from hisim.energy_system.validation import StructuralValidator


@dataclass(frozen=True)
class ConfiguredSystem:
    """Every configuration of one energy-system file, resolved and ready to be built from.

    The result of the configuring stage: the configurations in file order, each one complete
    — no field left ``AUTO`` — together with the sizing report that says where every number
    came from and the warnings that a run should print but not stop for.

    Keeping the bindings alongside means the constructing stage never re-imports a class, and
    keeping the report means the record writer can state, per sized field, which component
    provided the fact it was computed from.
    """

    configs: Tuple[Tuple[str, Any], ...]
    report: ResolutionReport
    warnings: Tuple[str, ...]
    bindings: ClassBindings

    def config_of(self, name: str) -> Any:
        """Returns the configuration of one component.

        Args:
            name: The component's name, which is its key in the file.

        Returns:
            The resolved configuration object.

        Raises:
            KeyError: If the system holds no component of that name.
        """
        for component_name, config in self.configs:
            if component_name == name:
                return config
        raise KeyError(name)


class EntryConfigurator:
    """Turns one component entry into the configuration object its component takes.

    Built per entry from its class binding, so the configuration class, its description and
    the builder the entry named are already resolved. The work is the three-step sequence the
    format prescribes — realize the origin, apply the overrides, expand the paths — and the
    steps are separate methods because each has its own failure mode and its own message.

    Nothing here decides whether the entry is *allowed* to say what it says; the class-bound
    validator has answered that. What is left is doing it, and reporting the two things that
    can still go wrong: a builder that raises, and a value that does not fit its field.
    """

    #: Nouns that make a configuration field path-valued, matching the rule the structural
    #: validator uses to find the paths it forbids to be absolute. A field is a path when its
    #: name is one of these or ends in one after an underscore.
    PATH_KEY_NOUNS: ClassVar[Tuple[str, ...]] = StructuralValidator.PATH_KEY_NOUNS

    def __init__(self, binding: ClassBinding, resolver: PathResolver) -> None:
        """Prepares the configurator for one entry.

        Args:
            binding: The entry resolved against its component and configuration classes.
            resolver: The registry that expands the file's ``${var}`` path references.
        """
        self.binding = binding
        self.resolver = resolver
        self.codec = ConfigValueCodec(binding.config_class)

    def build(self) -> Any:
        """Builds the entry's configuration object, overrides and paths applied.

        Returns:
            The configuration, with every field the file set written and every field a law
            owns still open for the sizing stage.

        Raises:
            EnergySystemBindingError: ``EF-1A`` for a value that does not fit its field, or a
                builder that refused the arguments the entry passed.
        """
        config = self._realize_origin()
        config = self._apply_overrides(config)
        return self._expand_paths(config)

    def _realize_origin(self) -> Any:
        """Calls the preset or the constructor, or deserializes the entry's own block.

        The instance name handed to a builder is the entry's key, which is the component's
        whole identity in this format; a complete ``config`` block gets the same identity
        injected, since the block itself is identity-free by rule.

        Returns:
            The configuration before any override is applied.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when the builder or the deserialization
                raises, with the underlying message kept.
        """
        entry = self.binding.entry
        if self.binding.preset is not None:
            return self._call_builder(self.binding.preset, {})
        if self.binding.constructor is not None and entry.constructor is not None:
            return self._call_builder(self.binding.constructor, dict(entry.constructor.arguments))
        payload = dict(entry.config)
        payload["component_id"] = {"name": entry.name}
        try:
            return getattr(self.binding.config_class, "from_dict")(payload)
        except Exception as error:  # pylint: disable=broad-except
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNDECODABLE_VALUE,
                f"components.{entry.name}.config",
                f"the config block of '{entry.name}' could not be read by "
                f"{self.binding.config_class.__name__}: {error}.",
            ) from error

    def _call_builder(self, builder: ConfigBuilder, arguments: Mapping[str, Any]) -> Any:
        """Calls one preset or named constructor with the entry's key as the instance name.

        Args:
            builder: The declared builder the entry selected.
            arguments: The arguments the entry passes; empty for a preset.

        Returns:
            The configuration the builder produced.

        Raises:
            EnergySystemBindingError: ``EF-1A`` when the builder raises, naming the builder
                and keeping its own message.
        """
        entry = self.binding.entry
        try:
            return builder.build(entry.name, **arguments)
        except Exception as error:  # pylint: disable=broad-except
            raise EnergySystemBindingError(
                EnergySystemErrorId.UNDECODABLE_VALUE,
                f"components.{entry.name}.{builder.kind.value}",
                f"the {builder.kind.explain()} '{builder.name}' of '{entry.name}' failed: "
                f"{error}.",
            ) from error

    def _apply_overrides(self, config: Any) -> Any:
        """Writes the entry's sparse ``config`` block onto the configuration it built.

        The overrides are applied as one replacement rather than one assignment per field, so
        that a configuration class validating itself on construction sees the final values and
        not an intermediate state. A preset's provenance stamp is carried across, because the
        replacement produces a fresh instance and losing the stamp would make a record forget
        which preset an entry came from.

        Args:
            config: The configuration as the origin produced it.

        Returns:
            The configuration with the overrides applied, or the same object when the entry
            overrides nothing.

        Raises:
            EnergySystemBindingError: ``EF-1A`` for a value that does not fit its field.
        """
        entry = self.binding.entry
        if not entry.config:
            return config
        decoded = {
            key: self.codec.decode(key, value, f"components.{entry.name}.config.{key}", entry.name)
            for key, value in entry.config.items()
        }
        replaced = dataclasses.replace(config, **decoded)
        provenance = getattr(config, ConfigBuilder.PROVENANCE_ATTRIBUTE, None)
        if provenance is not None:
            setattr(replaced, ConfigBuilder.PROVENANCE_ATTRIBUTE, provenance)
        return replaced

    def _expand_paths(self, config: Any) -> Any:
        """Turns the portable ``${var}`` spelling of the path fields into local paths.

        Args:
            config: The configuration with its overrides applied.

        Returns:
            The same configuration, its path fields expanded.

        Raises:
            EnergySystemFormatError: ``EF-04`` when a field references a variable the
                resolver does not know.
        """
        path_fields = [
            field.name
            for field in dataclasses.fields(self.binding.config_class)
            if self._is_path_field(field.name)
        ]
        if not path_fields:
            return config
        return PathFieldCodec.resolve(config, path_fields, self.resolver)

    @classmethod
    def _is_path_field(cls, field_name: str) -> bool:
        """Whether a field name marks its value as a filesystem location.

        Args:
            field_name: The configuration field's name.

        Returns:
            ``True`` when the name is one of the path nouns or ends in one.
        """
        return any(
            field_name == noun or field_name.endswith(f"_{noun}") for noun in cls.PATH_KEY_NOUNS
        )


def configure_energy_system(
    model: EnergySystemFile,
    *,
    bindings: Optional[ClassBindings] = None,
    path_resolver: Optional[PathResolver] = None,
) -> ConfiguredSystem:
    """Builds and sizes every configuration of an energy-system file.

    The fifth and sixth stages of the lifecycle in one call, because they are one unit from
    outside: the configurations are only meaningful once they are sized, and the sizing can
    only run once they all exist. On return every configuration is complete, the report says
    where each computed number came from, and the warnings list the providers nobody read.

    The sizing kernel is called with no seed context at all. Every fact this system uses comes
    from a component of this system, which is the property that makes a file self-contained:
    there is no second place — a Python setup, a command line — where a number could enter and
    quietly override what the file says.

    Args:
        model: The energy system to configure, after group expansion and validation.
        bindings: The class bindings, when a caller has already produced them; they are
            computed here otherwise.
        path_resolver: The registry expanding ``${var}`` path references; the default registry
            for this machine when omitted.

    Returns:
        The configured system: configurations in file order, the sizing report and the
        warnings.

    Raises:
        EnergySystemBindingError: For an entry whose configuration cannot be built.
        EnergySystemSizingError: ``EF-4A`` … ``EF-4H`` for the kernel's eight failure modes,
            each keeping the kernel's own message and naming the file location that caused it.
    """
    resolved_bindings = bindings if bindings is not None else validate_classes(model)
    resolver = path_resolver if path_resolver is not None else PathResolver.default()
    names: List[str] = []
    configs: List[Any] = []
    for binding in resolved_bindings:
        names.append(binding.name)
        configs.append(EntryConfigurator(binding, resolver).build())
    sized = resolve_sizing(configs, sizing_sources_bridge(model), names)
    return ConfiguredSystem(
        configs=tuple(zip(names, sized[0])),
        report=sized[1],
        warnings=unconsumed_warnings(sized[1]),
        bindings=resolved_bindings,
    )
