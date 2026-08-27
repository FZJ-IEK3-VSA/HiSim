"""Collector for the worked-example library (cost-spec-v2 §3.5).

Every `tests/worked_examples/<group>/<name>.yaml` is generated from the workbook next to it by
`tools/convert_worked_examples.py`. This module reads the YAML only — never the xlsx (§3.8) —
maps the example's group to the engine entry point it exercises, runs it with the declared
inputs and compares every expected value within its declared tolerance.

The Excel formulas in the workbooks are an independent second implementation: if these tests
pass, two different toolchains agree on the same arithmetic.

Group to entry point:

===============  ==========================================================================
group            entry point
===============  ==========================================================================
`financing`      :func:`hisim.economics.financing.loan_flows`
`discounting`    :class:`hisim.economics.parameters.EconomicParameters` + `CashFlowTimeline`
`tariffs`        :func:`hisim.economics.tariffs.apply_tariff`
`subsidies`      :func:`hisim.economics.subsidies.solve_cumulation` on in-memory catalogs
`end_to_end`     :meth:`hisim.economics.evaluator.EconomicEvaluator.evaluate` (a package under
                 the landlord/tenant split when the example declares a heating investment)
===============  ==========================================================================

The runners aggregate engine outputs (sums, ratios, discounting) but never re-implement
pricing logic: whenever a worked example asserts a capped basis, a scaled rate or a band
price, the number comes out of the engine, so a bug there cannot cancel itself out here.

**Error class.** A failure names a *formula*, and — because the examples are grouped by
calculator and assert intermediates as well as finals (§3.2) — usually the step inside it: the
failure message prints the label, the Excel value, the engine value and the derivation the
workbook recorded. What a failure here can never be is a *price* problem: the end-to-end runner
prices against a synthetic empty country (`XX`, D17) with zero energy prices and overrides every
device figure, so no shipped price can reach an expected value. The one deliberate exception is
the modernization-levy example, which must run under German tenancy law and therefore reads the
statutory percentages of `allocation_DE_2024.json` — it declares them as workbook inputs so that
a change to that file fails the example instead of re-baselining it. Nor can a failure be an extraction
problem — nothing here runs a simulation. Two failure modes of the *library* itself are checked
separately: `test_library_covers_every_group` guards against a group quietly emptying out, and
`test_every_workbook_has_a_generated_yaml` against an xlsx whose YAML was never generated (the
complementary direction, YAML drifting from its workbook, is CI's converter re-run, §3.6).

**Uncertainty policy.** Every worked example uses degenerate bands (§3.2, D16); slot mechanics
are verified by the property tests instead. That is why every runner below reads `.best_estimate` and
builds inputs with `UncertainValue.exact` — not a simplification, a stated scope boundary.
"""

# clean

import hashlib
import json
import os
import warnings
from typing import Any, Callable, Dict, List, Optional

import pytest
import yaml

from hisim.economics.database import CostDatabase

from tests.worked_example_runners import (
    EXAMPLE_YEAR,
    SYNTHETIC_COUNTRY,
    _discounting_values,
    _end_to_end_values,
    _financing_values,
    _subsidy_values,
    _tariff_values,
)

pytestmark = [pytest.mark.base, pytest.mark.worked_examples]

#: Root of the library; one sub-directory per calculator group (§3.1).
WORKED_EXAMPLES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worked_examples")

#: Country code of the synthetic cost database the end-to-end examples price against. It holds
#: no device entries and no escalation defaults, so no shipped price can leak into an example.

#: The price basis year every example is evaluated at.

#: Everything before this marker is hashed for the content fingerprint (§3.8); it must stay in
#: sync with `tools/convert_worked_examples.py`.
REVIEW_BLOCK_MARKER = "\nreview:\n"


class UnreviewedWorkedExampleWarning(UserWarning):
    """A worked example carries no valid human review attestation (§3.8).

    Its own warning class so a CI job or a reviewer can filter for exactly this signal, and so
    that switching `enforcement.yaml` from `warn` to `error` later changes only the severity, not
    the meaning. It covers both "never attested" and "attested, but the content changed since" —
    the message distinguishes them, since a stale attestation names the reviewer whose sign-off
    no longer applies.
    """


# --------------------------------------------------------------------------- loading


def load_examples() -> List[Dict[str, Any]]:
    """Reads every generated YAML fixture, sorted by group and name.

    Collection happens at import time so pytest can parametrize one test per example and report
    them individually — adding a workbook plus its YAML adds a named test case with no code
    change. The raw file text is kept alongside the parsed data because the §3.8 attestation is a
    hash over the file *before* its `review:` block, which the parsed mapping cannot reconstruct.
    `enforcement.yaml` is skipped: it is the policy switch, not an example. The sort makes test
    ids and their order deterministic across filesystems.
    """
    examples = []
    for directory, _, file_names in os.walk(WORKED_EXAMPLES_ROOT):
        for file_name in sorted(file_names):
            if not file_name.endswith(".yaml") or file_name == "enforcement.yaml":
                continue
            path = os.path.join(directory, file_name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            example = yaml.safe_load(text)
            example["path"] = path
            example["raw_text"] = text
            examples.append(example)
    return sorted(examples, key=lambda item: (item["group"], item["name"]))


def enforcement_mode() -> str:
    """Reads the warn/error switch of `tests/worked_examples/enforcement.yaml` (§3.8).

    Whether a missing review attestation fails the suite or only warns is a project decision, not
    a code decision, so it lives in a data file one line long. D9 keeps it at `warn` indefinitely:
    attestations so far are the owner's own, and failing CI on them would enforce a formality
    rather than the external domain-expert review the mechanism is meant to invite.
    """
    path = os.path.join(WORKED_EXAMPLES_ROOT, "enforcement.yaml")
    with open(path, encoding="utf-8") as handle:
        return str(yaml.safe_load(handle).get("enforcement", "warn"))


EXAMPLES = load_examples()
EXAMPLE_IDS = [f"{example['group']}/{example['name']}" for example in EXAMPLES]


# --------------------------------------------------------------------------- group runners


@pytest.fixture(name="synthetic_database", scope="module")
def fixture_synthetic_database(tmp_path_factory) -> CostDatabase:
    """A cost database with nothing in it but one electricity price entry.

    The end-to-end examples override every device figure and bring their own tariff contract, so
    the database must not contribute any number of its own. It also carries no escalation
    defaults file, which keeps the parameter fallback chain (§3.2) at the explicit rates the
    example declares.
    """
    directory = tmp_path_factory.mktemp("worked_example_cost_database")
    sources = {
        "sources": [
            {
                "id": "src_worked_example",
                "citation": "Synthetic worked-example data (cost-spec-v2 §3)",
                "publication_year": 2024,
                "retrieved": "2026-08-12",
                "kind": "EXPERT_ESTIMATE",
                "notes": "Round numbers invented for the worked-example library; never a research input.",
            }
        ]
    }
    prices = {
        "entries": [
            {
                "carrier": "ELECTRICITY",
                "year": EXAMPLE_YEAR,
                "working_price_in_euro_per_kwh": 0.0,
                "standing_charge_in_euro_per_year": 0.0,
                "emission_factor_in_kg_per_kwh": 0.0,
                "co2_price_exposure": 0.0,
                "tax_and_levy_share": 0.0,
                "quantity_unit": "kWh",
                "source_ids": ["src_worked_example"],
                "notes": "Zero prices: every worked example brings its own tariff contract.",
            }
        ]
    }
    (directory / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    (directory / f"energy_prices_{SYNTHETIC_COUNTRY}.json").write_text(json.dumps(prices), encoding="utf-8")
    return CostDatabase(str(directory))


# --------------------------------------------------------------------------- the tests


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_IDS)
def test_worked_example(example: Dict[str, Any], synthetic_database: CostDatabase) -> None:
    """Runs one worked example and compares every expected value within its tolerance."""
    runners: Dict[str, Callable[[Dict[str, Any]], Dict[str, float]]] = {
        "financing": _financing_values,
        "discounting": _discounting_values,
        "tariffs": _tariff_values,
        "subsidies": _subsidy_values,
        "end_to_end": lambda inputs: _end_to_end_values(inputs, synthetic_database),
    }
    group = example["group"]
    assert group in runners, f"no entry point registered for worked-example group {group!r}"
    computed = runners[group](example["inputs"])
    for label, expectation in example["expected"].items():
        assert label in computed, (
            f"{example['name']}: the {group} entry point produces no value called {label!r}; "
            "either the workbook label or the collector mapping is wrong."
        )
        difference = abs(computed[label] - float(expectation["value"]))
        assert difference <= float(expectation["abs_tol"]), (
            f"{example['name']}.{label}: Excel says {expectation['value']}, the engine says "
            f"{computed[label]!r} (difference {difference!r} > tolerance {expectation['abs_tol']}). "
            f"Derivation: {expectation.get('derivation') or expectation.get('note')}"
        )


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_IDS)
def test_worked_example_review_attestation(example: Dict[str, Any]) -> None:
    """Validates the §3.8 attestation from the YAML alone; warns while enforcement is `warn`."""
    text = example["raw_text"]
    index = text.find(REVIEW_BLOCK_MARKER)
    content = text if index == -1 else text[: index + 1]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12].upper()
    fingerprint = f"{digest[0:4]}-{digest[4:8]}-{digest[8:12]}"
    review: Optional[Dict[str, Any]] = example.get("review")
    if review is not None and review.get("fingerprint") == fingerprint:
        return
    if review is None:
        message = f"{example['name']}: no review attestation — content fingerprint {fingerprint} (§3.8)."
    else:
        message = (
            f"{example['name']}: review by {review.get('reviewed_by')!r} is stale — content "
            f"fingerprint {fingerprint} (§3.8)."
        )
    if enforcement_mode() == "error":
        pytest.fail(message)
    warnings.warn(UnreviewedWorkedExampleWarning(message))


def test_library_covers_every_group() -> None:
    """The library holds at least 20 examples and no group is empty (§3)."""
    assert len(EXAMPLES) >= 20, f"only {len(EXAMPLES)} worked examples found; the library needs at least 20."
    groups = {example["group"] for example in EXAMPLES}
    expected_groups = {"financing", "discounting", "tariffs", "subsidies", "end_to_end"}
    assert expected_groups <= groups, f"missing worked-example groups: {sorted(expected_groups - groups)}"
    for group in expected_groups:
        assert any(example["group"] == group for example in EXAMPLES), f"group {group!r} is empty."


def test_every_workbook_has_a_generated_yaml() -> None:
    """Every committed workbook has its generated fixture next to it (§3.6)."""
    for directory, _, file_names in os.walk(WORKED_EXAMPLES_ROOT):
        for file_name in file_names:
            if not file_name.endswith(".xlsx") or file_name.startswith(("_", "~$")):
                continue
            yaml_path = os.path.join(directory, os.path.splitext(file_name)[0] + ".yaml")
            assert os.path.isfile(yaml_path), (
                f"{os.path.join(directory, file_name)} has no generated YAML; "
                "run python tools/convert_worked_examples.py"
            )
