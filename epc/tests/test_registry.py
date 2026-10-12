"""T-7: an engine registers under its country, computes and reports through the shell alone (R3, R10, I-7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Type

import pytest

from epc import batch
from epc.engines import registry
from epc.engines.base import EpcEngine
from epc.model.errors import EpcError
from epc.model.result import COMMON_CORE
from epc.tests import support

pytestmark = pytest.mark.base


@pytest.fixture(name="registered")
def fixture_registered(request: pytest.FixtureRequest) -> Iterator[Type[EpcEngine]]:
    """Registers the dummy engine class given as parameter for one test, and removes it afterwards."""
    engine: Type[EpcEngine] = request.param
    registry.register(engine)
    yield engine
    registry.ENGINES_BY_COUNTRY.pop(engine.country)


def test_a_country_without_engine_is_refused() -> None:
    """E-01 names the country and the implemented ones."""
    with pytest.raises(EpcError) as refusal:
        registry.for_country("DE")
    assert refusal.value.error_id == "E-01"
    assert "'DE'" in refusal.value.message
    implemented = ", ".join(sorted(registry.ENGINES_BY_COUNTRY)) or "none"
    assert f"(implemented: {implemented})" in refusal.value.message


@pytest.mark.parametrize("registered", [support.DummyEngine], indirect=True)
def test_a_registered_engine_computes_and_reports(registered: Type[EpcEngine], tmp_path: Path) -> None:
    """The dummy engine runs through the shell and its result carries the core and its assumption."""
    assert isinstance(registry.for_country(registered.country), registered)
    with pytest.raises(ValueError, match="already has"):
        registry.register(support.DummyEngine)
    written = batch.run(support.FIXTURES / support.CONFIGURED, registered.country, tmp_path)
    assert written == [tmp_path / support.CONFIGURED / "building.epc.json"]
    document = json.loads(written[0].read_text(encoding="utf-8"))
    assert set(COMMON_CORE) <= set(document["indicators"])
    assert document["run"]["country"] == registered.country
    assert document["run"]["edition"] == registered.edition
    assert document["assumptions"] == [{"key": "dummy_default", "value": "a placeholder", "source": "no norm"}]


@pytest.mark.parametrize("registered", [support.CorelessEngine], indirect=True)
def test_a_result_without_the_core_is_refused(registered: Type[EpcEngine], tmp_path: Path) -> None:
    """An engine returning no common core is refused with E-17, and nothing is written."""
    with pytest.raises(EpcError) as refusal:
        batch.run(support.FIXTURES / support.CONFIGURED, registered.country, tmp_path / "out")
    assert refusal.value.error_id == "E-17"
    assert not (tmp_path / "out").exists()
