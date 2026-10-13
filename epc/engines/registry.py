"""The engines by country, and the loud refusal of a country without one.

The country is an explicit argument (D16) and selects the engine here, before any file is opened: a
country without an engine is refused naming the implemented ones (``E-01``, R3). Adding a country
registers one engine class under its code and touches no other engine, the shell, the dictionaries
or the reports (R10). Austria is registered (the pilot); tests register their own dummies.
"""

from __future__ import annotations

from typing import Dict, Type

from epc.engines.at.method import AustrianEngine
from epc.engines.base import EpcEngine
from epc.model.errors import EpcError, EpcErrorKind

#: Engine class by ISO 3166-1 alpha-2 code.
ENGINES_BY_COUNTRY: Dict[str, Type[EpcEngine]] = {AustrianEngine.country: AustrianEngine}


def register(engine: Type[EpcEngine]) -> None:
    """Registers an engine class under its country.

    Args:
        engine: The engine class.

    Raises:
        ValueError: When the country already has an engine.
    """
    if engine.country in ENGINES_BY_COUNTRY:
        raise ValueError(
            f"Country '{engine.country}' already has the engine {ENGINES_BY_COUNTRY[engine.country].__name__}."
        )
    ENGINES_BY_COUNTRY[engine.country] = engine


def for_country(code: str) -> EpcEngine:
    """The engine of a country, ready to load its data.

    Args:
        code: ISO 3166-1 alpha-2 code as given, not normalised.

    Returns:
        A new instance of the country's engine.

    Raises:
        EpcError: ``E-01`` when no engine is implemented for the country.
    """
    engine = ENGINES_BY_COUNTRY.get(code)
    if engine is None:
        raise EpcError(EpcErrorKind.NO_ENGINE, cc=code, codes=", ".join(sorted(ENGINES_BY_COUNTRY)) or "none")
    return engine()
