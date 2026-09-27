"""The RenoVisor builds every test module reads, made once per test session.

A capability document runs the whole probe set through ``validate`` + ``apply`` + ``translate``,
about a hundred seconds in CI, and the translation map is rendered from one. The tests that read
them share :func:`fixture_capability_document` and :func:`fixture_translation_map_page`; the
tests that claim two builds are identical compare the shared build with the one deliberately
independent second build, :func:`fixture_independent_capability_document`, which is made only
when such a test runs. A test that needs a build under a patch makes its own.
"""

import pytest

from hisim.renovisor.capabilities import CapabilityDocument
from hisim.renovisor.map import TranslationMap


@pytest.fixture(scope="session", name="capability_document")
def fixture_capability_document() -> CapabilityDocument:
    """Build the capability document once; every test in the session reads the same probe run."""
    return CapabilityDocument.build()


@pytest.fixture(scope="session", name="independent_capability_document")
def fixture_independent_capability_document() -> CapabilityDocument:
    """A second build of the same state, for the determinism tests and for nothing else."""
    return CapabilityDocument.build()


@pytest.fixture(scope="session", name="translation_map_page")
def fixture_translation_map_page(capability_document: CapabilityDocument) -> str:
    """Render the translation map once, from the shared capability document."""
    return TranslationMap.render(document=capability_document)
