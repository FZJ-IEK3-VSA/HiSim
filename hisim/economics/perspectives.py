"""Cost perspectives: named configurations of five orthogonal dimensions (cost_spec.md §4)."""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from hisim.economics.financing import FinancingPlan
from hisim.economics.timeline import Actor


class InstallationContext(str, enum.Enum):
    """Which investments the perspective charges (§4.1, §4.2)."""

    GREENFIELD = "GREENFIELD"
    BROWNFIELD = "BROWNFIELD"
    STATUS_QUO = "STATUS_QUO"
    OPERATING_ONLY = "OPERATING_ONLY"


class SubsidyModeKind(str, enum.Enum):
    """Kinds of subsidy filtering (§5.5)."""

    NONE = "NONE"
    FULL = "FULL"
    ONLY = "ONLY"
    EXCLUDE = "EXCLUDE"


@dataclass(frozen=True)
class SubsidyMode:
    """NONE | FULL | ONLY(scheme_ids) | EXCLUDE(scheme_ids)."""

    kind: SubsidyModeKind = SubsidyModeKind.FULL
    scheme_ids: Tuple[str, ...] = ()

    @classmethod
    def none(cls) -> "SubsidyMode":
        """No subsidies."""
        return cls(SubsidyModeKind.NONE)

    @classmethod
    def full(cls) -> "SubsidyMode":
        """All eligible subsidies."""
        return cls(SubsidyModeKind.FULL)

    @classmethod
    def only(cls, scheme_ids: Tuple[str, ...]) -> "SubsidyMode":
        """Only the named schemes."""
        return cls(SubsidyModeKind.ONLY, scheme_ids)

    @classmethod
    def exclude(cls, scheme_ids: Tuple[str, ...]) -> "SubsidyMode":
        """All eligible schemes except the named ones."""
        return cls(SubsidyModeKind.EXCLUDE, scheme_ids)

    def admits(self, scheme_id: str) -> bool:
        """Whether a scheme may contribute under this mode."""
        if self.kind == SubsidyModeKind.NONE:
            return False
        if self.kind == SubsidyModeKind.ONLY:
            return scheme_id in self.scheme_ids
        if self.kind == SubsidyModeKind.EXCLUDE:
            return scheme_id not in self.scheme_ids
        return True


class Accounting(str, enum.Enum):
    """Financial vs macroeconomic accounting (EU 244/2012, §4.5)."""

    FINANCIAL = "FINANCIAL"
    MACROECONOMIC = "MACROECONOMIC"


class ActorScope(str, enum.Enum):
    """Whose cash flows the perspective reports (§6)."""

    SYSTEM = "SYSTEM"
    OWNER_OCCUPIER = "OWNER_OCCUPIER"
    LANDLORD = "LANDLORD"
    TENANT = "TENANT"

    def to_actor(self) -> Actor:
        """Maps to the timeline payer enum."""
        return {
            ActorScope.SYSTEM: Actor.SYSTEM,
            ActorScope.OWNER_OCCUPIER: Actor.OWNER_OCCUPIER,
            ActorScope.LANDLORD: Actor.LANDLORD,
            ActorScope.TENANT: Actor.TENANT,
        }[self]


@dataclass
class Perspective:
    """A named configuration of five orthogonal dimensions (§4)."""

    id: str
    installation_context: InstallationContext
    actor_scope: ActorScope = ActorScope.SYSTEM
    subsidy_mode: SubsidyMode = field(default_factory=SubsidyMode.full)
    financing: Optional[FinancingPlan] = None  # None = cash purchase
    accounting: Accounting = Accounting.FINANCIAL

    @classmethod
    def from_json(cls, raw: dict) -> "Perspective":
        """Parses one entry of perspectives_default.json (or a request block)."""
        subsidy_raw = raw.get("subsidies", "FULL")
        if isinstance(subsidy_raw, dict):
            subsidy_mode = SubsidyMode(
                SubsidyModeKind(subsidy_raw["kind"]), tuple(subsidy_raw.get("scheme_ids", []))
            )
        else:
            subsidy_mode = SubsidyMode(SubsidyModeKind(subsidy_raw))
        financing = None
        if raw.get("financing") not in (None, "cash", "-"):
            financing_raw = raw["financing"]
            financing = FinancingPlan(**financing_raw) if isinstance(financing_raw, dict) else FinancingPlan()
        return cls(
            id=raw["id"],
            installation_context=InstallationContext(raw["context"]),
            actor_scope=ActorScope(raw.get("actor", "SYSTEM")),
            subsidy_mode=subsidy_mode,
            financing=financing,
            accounting=Accounting(raw.get("accounting", "FINANCIAL")),
        )


#: Default location of the shipped default perspective bundle (§7.1).
DEFAULT_BUNDLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cost_database", "perspectives_default.json"
)


def load_default_bundle(path: Optional[str] = None) -> List[Perspective]:
    """Loads the standard perspective bundle (§7.1)."""
    with open(path or DEFAULT_BUNDLE_PATH, encoding="utf-8") as file:
        raw = json.load(file)
    return [Perspective.from_json(item) for item in raw["perspectives"]]


def select_applicable(perspectives: List[Perspective], has_register: bool) -> List[Perspective]:
    """Greenfield rows are skipped when a register exists and vice versa (§7.1)."""
    selected = []
    for perspective in perspectives:
        needs_register = perspective.installation_context in (
            InstallationContext.BROWNFIELD,
            InstallationContext.STATUS_QUO,
        )
        if needs_register and not has_register:
            continue
        if perspective.installation_context == InstallationContext.GREENFIELD and has_register:
            continue
        selected.append(perspective)
    return selected
