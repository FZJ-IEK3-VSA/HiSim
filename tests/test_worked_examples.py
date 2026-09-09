"""Collector for the worked-example library (cost-spec-v2 §3.5).

Every `tests/worked_examples/<group>/<name>.yaml` is generated from the workbook next to it by
`tools/convert_worked_examples.py`. This module reads the YAML only — never the xlsx (§3.8) —
maps the example's group to the engine entry point it exercises, runs it with the declared
inputs and compares every expected value within its declared tolerance.

The Excel formulas in the workbooks are an independent second implementation: if these tests
pass, two different toolchains agree on the same arithmetic.

Group to entry point:

====================  =======================================================================
group                 entry point
====================  =======================================================================
`financing`           :func:`hisim.economics.financing.loan_flows`
`discounting`         :class:`hisim.economics.parameters.EconomicParameters` + `CashFlowTimeline`
`tariffs`             :func:`hisim.economics.tariffs.apply_tariff`
`subsidies`           :func:`hisim.economics.subsidies.solve_cumulation` on in-memory catalogs
`modernization_levy`  :meth:`hisim.economics.actors.DE2024Ruleset.compute_modernization_levy`,
                      on a retrofit package under the German landlord/tenant split
`end_to_end`          :meth:`hisim.economics.evaluator.EconomicEvaluator.evaluate`, for one device
====================  =======================================================================

The map is one to one: a group is selected by its directory name alone, never by which inputs an
example happens to declare.

The runners aggregate engine outputs (sums, ratios, discounting) but never re-implement
pricing logic: whenever a worked example asserts a capped basis, a scaled rate or a band
price, the number comes out of the engine, so a bug there cannot cancel itself out here.

**Error class.** A failure names a *formula*, and — because the examples are grouped by
calculator and assert intermediates as well as finals (§3.2) — usually the step inside it: the
failure message prints the label, the Excel value, the engine value and the derivation the
workbook recorded. What a failure here can never be is a *price* problem: the end-to-end runner
prices against a synthetic empty country (`XX`, D17) with zero energy prices and overrides every
device figure, so no shipped price can reach an expected value. The synthetic database does carry
two carbon figures (an emission factor and a CO2-price exposure share) plus one CO2 price
trajectory, because those are priced off the database and not off a workbook's tariff contract;
they come from `SyntheticCarbonData`, are declared as inputs by the one example that switches
carbon pricing on, and reach no other example, whose `co2_price_scenario` stays `"none"`. The one
deliberate exception is
the modernization-levy example, which must run under German tenancy law and therefore reads the
statutory percentages of `allocation_DE_2024.json` — it declares them as workbook inputs so that
a change to that file fails the example instead of re-baselining it. Nor can a failure be an extraction
problem — nothing here runs a simulation. Three failure modes of the *library* itself are checked
separately: `test_library_covers_every_group` guards against a group quietly emptying out, and
`test_every_workbook_has_a_generated_yaml` / `test_every_fixture_has_its_workbook` guard the two
directions of the xlsx-to-yaml pairing — a workbook whose YAML was never generated, and a YAML
whose workbook was deleted, which would keep passing against the engine while having lost the
independent Excel ground truth that is the library's whole point. The third direction, a YAML
drifting from a workbook that still exists, is CI's converter re-run (§3.6).

**Uncertainty policy.** Every worked example uses degenerate bands (§3.2, D16); slot mechanics
are verified by the property tests instead. That is why every runner below reads `.best_estimate` and
builds inputs with `UncertainValue.exact` — not a simplification, a stated scope boundary.

**What this module imports from the converter, and why that direction.** The §3.8 fingerprint
protocol and the workbook-discovery predicate used to exist twice — once in the tool, once here,
each carrying a "must stay in sync" comment. They now have one owner each in
`tools/worked_examples/`, imported below. The dependency runs test → tool only: the tool must not
import `hisim` (it runs in a bare CI job that installs only openpyxl and PyYAML), while a test may
import anything, so this is the direction that removes the duplication instead of breaking the
tool's isolation.
"""

# clean

import json
import os
import warnings
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest
import yaml

from hisim.economics.database import CostDatabase

from tests.worked_example_runners import (
    EXAMPLE_YEAR,
    SYNTHETIC_COUNTRY,
    SyntheticCarbonData,
    _discounting_values,
    _end_to_end_values,
    _financing_values,
    _modernization_levy_values,
    _subsidy_values,
    _tariff_values,
)
from tools.worked_examples.attestation import (
    EnforcementMode,
    FingerprintFormat,
    content_for_fingerprint,
    fingerprint_of,
    read_enforcement_mode,
)
from tools.worked_examples.cli import find_workbooks

pytestmark = [pytest.mark.base, pytest.mark.worked_examples]

#: Root of the library; one sub-directory per calculator group (§3.1).
WORKED_EXAMPLES_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worked_examples")


class FixtureKeys:
    """The generated-YAML keys this collector relies on, and the file that is not an example.

    Class-scoped because the two values describe one thing — what `load_examples` accepts as a
    fixture. A YAML missing any required key cannot be parametrized (the test id is built from
    `group` and `name`) or run (the runner needs `inputs` and `expected`), so it is reported as a
    broken fixture rather than allowed to fail with a `KeyError` during collection.
    """

    REQUIRED = ("name", "group", "inputs", "expected")
    #: The §3.8 policy switch lives in the same tree but is not an example.
    POLICY_FILE_NAME = FingerprintFormat.ENFORCEMENT_FILE_NAME


class UnreviewedWorkedExampleWarning(UserWarning):
    """A worked example carries no valid human review attestation (§3.8).

    Its own warning class so a CI job or a reviewer can filter for exactly this signal, and so
    that switching `enforcement.yaml` from `warn` to `error` later changes only the severity, not
    the meaning. It covers both "never attested" and "attested, but the content changed since" —
    the message distinguishes them, since a stale attestation names the reviewer whose sign-off
    no longer applies.
    """


# --------------------------------------------------------------------------- loading


def load_examples() -> Tuple[List[Dict[str, Any]], List[str]]:
    """Reads every generated YAML fixture, sorted by group and name; unreadable ones separately.

    Collection happens at import time so pytest can parametrize one test per example and report
    them individually — adding a workbook plus its YAML adds a named test case with no code
    change. The raw file text is kept alongside the parsed data because the §3.8 attestation is a
    hash over the file *before* its `review:` block, which the parsed mapping cannot reconstruct.
    `enforcement.yaml` is skipped: it is the policy switch, not an example. The sort makes test
    ids and their order deterministic across filesystems.

    A file that does not read, does not parse, or lacks a key this collector needs is returned in
    the second list instead of raising. Raising here aborted collection of the **whole** `-m base`
    suite, so one malformed fixture failed hundreds of unrelated tests with a traceback that named
    an import rather than a file; `test_every_fixture_is_usable` turns it into one failure naming
    the file.

    Returns:
        `(examples, problems)` — the usable fixtures, and one message per unusable one.
    """
    examples = []
    problems = []
    for directory, _, file_names in os.walk(WORKED_EXAMPLES_ROOT):
        for file_name in sorted(file_names):
            if not file_name.endswith(".yaml") or file_name == FixtureKeys.POLICY_FILE_NAME:
                continue
            path = os.path.join(directory, file_name)
            try:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
                example = yaml.safe_load(text)
                if not isinstance(example, dict):
                    raise ValueError(f"parses as {type(example).__name__}, not a mapping")
                missing = [key for key in FixtureKeys.REQUIRED if key not in example]
                if missing:
                    raise ValueError(f"missing key(s) {missing}")
            except (OSError, ValueError, yaml.YAMLError) as error:
                problems.append(f"{os.path.relpath(path, WORKED_EXAMPLES_ROOT)}: {error}")
                continue
            example["path"] = path
            example["raw_text"] = text
            examples.append(example)
    return sorted(examples, key=lambda item: (item["group"], item["name"])), problems


EXAMPLES, UNUSABLE_FIXTURES = load_examples()
EXAMPLE_IDS = [f"{example['group']}/{example['name']}" for example in EXAMPLES]

#: The §3.8 warn/error switch, read and validated once. It is a per-run constant, so re-opening and
#: re-parsing the file inside every parametrized case (25 times) bought nothing; and the read is
#: where the value is checked against the closed set {warn, error}, so an unrecognized word fails
#: loudly here instead of silently leaving the review gate disabled.
ENFORCEMENT_MODE = read_enforcement_mode(WORKED_EXAMPLES_ROOT)


def check_attestation(example: Dict[str, Any], mode: EnforcementMode) -> None:
    """Validates one example's §3.8 attestation from the YAML alone, at the given severity.

    The fingerprint is recomputed from the file text with the converter's own two functions, so the
    hash that is written and the hash that is checked cannot drift apart. An attestation counts only
    while it names the current content: a `review:` block whose fingerprint no longer matches means
    someone reviewed an earlier revision, which is reported as *stale* rather than accepted.

    `mode` is a parameter rather than a module lookup so both branches are reachable in a test:
    under the committed `warn` the failing branch is dead in CI, and an untested `pytest.fail` path
    is exactly how an enforcement switch turns out not to work on the day it is flipped.

    Args:
        example: One loaded fixture, carrying `raw_text` and optionally a parsed `review` block.
        mode: `ERROR` fails the test, `WARN` emits `UnreviewedWorkedExampleWarning`.

    Raises:
        pytest.fail.Exception: If the attestation is missing or stale and `mode` is `ERROR`.
    """
    fingerprint = fingerprint_of(content_for_fingerprint(example["raw_text"]))
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
    if mode is EnforcementMode.ERROR:
        pytest.fail(message)
    warnings.warn(UnreviewedWorkedExampleWarning(message))


# --------------------------------------------------------------------------- group runners


@pytest.fixture(name="synthetic_database", scope="module")
def fixture_synthetic_database(tmp_path_factory) -> CostDatabase:
    """A cost database with nothing in it but one electricity price entry and one CO2 trajectory.

    The end-to-end examples override every device figure and bring their own tariff contract, so
    the database must not contribute any *price* of its own. It also carries no escalation
    defaults file, which keeps the parameter fallback chain (§3.2) at the explicit rates the
    example declares.

    Two carbon figures are the exception, and they have to be: the CO2 price component of a bill
    is priced off the price entry's emission factor and `co2_price_exposure` share times the
    country's trajectory (`calculators/energy.py`), none of which a workbook's tariff contract can
    carry. They come from `SyntheticCarbonData`, are copied into the `co2_price_from_trajectory`
    workbook as declared inputs, and reach no other example: every other end-to-end example leaves
    `co2_price_scenario` at `"none"`, so `get_co2_price_path` returns None and no ENERGY_CO2_PRICE
    entry is emitted regardless of the emission factor.
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
                "emission_factor_in_kg_per_kwh": SyntheticCarbonData.EMISSION_FACTOR_IN_KG_PER_KWH,
                "co2_price_exposure": SyntheticCarbonData.CO2_PRICE_EXPOSURE,
                "tax_and_levy_share": 0.0,
                "quantity_unit": "kWh",
                "source_ids": ["src_worked_example"],
                "notes": "Zero prices: every worked example brings its own tariff contract.",
            }
        ]
    }
    co2_paths = {
        "countries": {
            SYNTHETIC_COUNTRY: {
                SyntheticCarbonData.PRICE_PATH_NAME: {
                    "points": SyntheticCarbonData.PRICE_PATH_POINTS,
                    "source_ids": ["src_worked_example"],
                }
            }
        }
    }
    (directory / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
    (directory / f"energy_prices_{SYNTHETIC_COUNTRY}.json").write_text(json.dumps(prices), encoding="utf-8")
    (directory / "co2_price_paths.json").write_text(json.dumps(co2_paths), encoding="utf-8")
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
        "modernization_levy": lambda inputs: _modernization_levy_values(inputs, synthetic_database),
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
    """Validates the §3.8 attestation of one example at the committed enforcement severity."""
    check_attestation(example, ENFORCEMENT_MODE)


class TestAttestationEnforcement:
    """Both branches of the §3.8 gate, on synthesized fixtures rather than on the committed ones.

    The committed switch is `warn` and every committed example is unreviewed, so on the real
    library the failing branch is unreachable and the passing branch has no fixture that reaches it
    — the gate is inert in CI, which is what the review flagged. These cases exercise it directly:
    a fixture whose `review.fingerprint` equals its own content fingerprint has to pass even under
    `error`, and a missing or stale attestation has to fail under `error` while only warning under
    `warn`.

    The matching case doubles as the coupling test between the tool and this module: the fingerprint
    is produced by the converter's `fingerprint_of` over the converter's `content_for_fingerprint`,
    so if the two ever stopped agreeing on which bytes are hashed, this test would go red rather
    than every attestation silently reading as stale.
    """

    @staticmethod
    def _content() -> str:
        """The semantic part of a fixture: everything the fingerprint covers."""
        return 'name: "synthetic_example"\ngroup: "financing"\nexpected: {}\n'

    @classmethod
    def _fixture(cls, attested_fingerprint: Optional[str]) -> Dict[str, Any]:
        """One loaded-fixture mapping, with a review block only if a fingerprint is given.

        Built by hand rather than by copying a committed file because no committed example carries
        an attestation at all, which is precisely the state under test.
        """
        content = cls._content()
        if attested_fingerprint is None:
            return {"name": "synthetic_example", "raw_text": content}
        review = {"reviewed_by": "A. Reviewer", "review_date": "2026-01-01", "fingerprint": attested_fingerprint}
        text = (
            content
            + FingerprintFormat.BLOCK_KEY
            + f'  reviewed_by: "{review["reviewed_by"]}"\n'
            + f'  review_date: "{review["review_date"]}"\n'
            + f'  fingerprint: "{attested_fingerprint}"\n'
        )
        return {"name": "synthetic_example", "raw_text": text, "review": review}

    def test_a_matching_attestation_passes_under_error_enforcement(self):
        """The branch no committed fixture reaches: a review that still names the current content."""
        fixture = self._fixture(fingerprint_of(self._content()))
        check_attestation(fixture, EnforcementMode.ERROR)  # must not raise

    def test_the_review_block_is_outside_its_own_hash(self):
        """The attestation must not change the fingerprint it contains, or nothing could ever match."""
        fixture = self._fixture(fingerprint_of(self._content()))
        assert content_for_fingerprint(fixture["raw_text"]) == self._content()

    def test_a_missing_attestation_fails_under_error_enforcement(self):
        """The `pytest.fail` branch, unreachable in CI while the committed switch stays `warn`."""
        with pytest.raises(pytest.fail.Exception, match="no review attestation"):
            check_attestation(self._fixture(None), EnforcementMode.ERROR)

    def test_a_stale_attestation_fails_under_error_enforcement(self):
        """A review of an earlier revision must not count; the message names the reviewer."""
        with pytest.raises(pytest.fail.Exception, match="is stale"):
            check_attestation(self._fixture("AAAA-BBBB-CCCC"), EnforcementMode.ERROR)

    @pytest.mark.parametrize("attested", [None, "AAAA-BBBB-CCCC"])
    def test_the_same_states_only_warn_under_warn_enforcement(self, attested):
        """Flipping the switch changes the severity and nothing else about what counts as reviewed."""
        with pytest.warns(UnreviewedWorkedExampleWarning):
            check_attestation(self._fixture(attested), EnforcementMode.WARN)


def test_every_fixture_is_usable() -> None:
    """Every `.yaml` under the library parses into a mapping with the keys this collector needs.

    A fixture that does not is reported here, by name, instead of aborting collection of the whole
    `-m base` suite from `load_examples()` at import time.
    """
    assert not UNUSABLE_FIXTURES, "unusable worked-example fixtures:\n  " + "\n  ".join(UNUSABLE_FIXTURES)


def test_library_covers_every_group() -> None:
    """The library holds at least 20 examples and no group is empty (§3)."""
    assert len(EXAMPLES) >= 20, f"only {len(EXAMPLES)} worked examples found; the library needs at least 20."
    groups = {example["group"] for example in EXAMPLES}
    expected_groups = {"financing", "discounting", "tariffs", "subsidies", "modernization_levy", "end_to_end"}
    assert expected_groups <= groups, f"missing worked-example groups: {sorted(expected_groups - groups)}"
    for group in expected_groups:
        assert any(example["group"] == group for example in EXAMPLES), f"group {group!r} is empty."


def test_every_workbook_has_a_generated_yaml() -> None:
    """Every committed workbook has its generated fixture next to it (§3.6).

    Discovery is the converter's `find_workbooks`, not a second walk written here: the predicate
    ("keep `.xlsx`, skip `_…` and `~$…`") had two implementations that had to agree, and the one
    in the tool is the one that decides what actually gets converted.
    """
    for workbook in find_workbooks(WORKED_EXAMPLES_ROOT):
        yaml_path = os.path.splitext(workbook)[0] + ".yaml"
        assert os.path.isfile(yaml_path), (
            f"{workbook} has no generated YAML; run python tools/convert_worked_examples.py"
        )


def test_every_fixture_has_its_workbook() -> None:
    """Every fixture still has the workbook it was generated from — the other direction of §3.6.

    Without this, deleting an `.xlsx` and keeping its YAML leaves an example that runs and passes
    against the engine while having silently lost the independent Excel derivation that is the only
    reason to trust its numbers. The converter cannot catch it: it iterates over workbooks, so an
    orphaned fixture is invisible to `--check`.
    """
    for example in EXAMPLES:
        workbook = os.path.splitext(example["path"])[0] + ".xlsx"
        assert os.path.isfile(workbook), (
            f"{example['path']} has no workbook next to it; a fixture without its xlsx has no "
            "independent derivation behind its expected values (§3.6)."
        )
