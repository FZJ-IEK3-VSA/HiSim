"""The §3.8 review layer: the content fingerprint, the three review states and the warn/error switch.

Owns the fingerprint *protocol* — which bytes of a generated fixture are hashed, how the digest is
shortened, and how it is grouped so a human can retype it — and owns it for the whole repository.
`tests/test_worked_examples.py` imports `content_for_fingerprint` and `fingerprint_of` from here
rather than re-deriving them from the marker string, so the code that writes an attestation and the
code that validates one cannot drift apart. That import runs test → tool only: the tests may import
`hisim`, this package must not (see the package docstring), and the dependency in this direction is
what removes the duplication instead of creating a cycle.

Separate from `emitter.py` because the two run in opposite directions. The emitter turns an
`Example` into text; everything here reads text back — the fingerprint is computed over the
emitter's own output, and the enforcement switch comes from a data file no workbook contributes to.
"""

from __future__ import annotations

import enum
import hashlib
import os
import re
from typing import Tuple

import yaml

from tools.worked_examples.model import Example, ValidationError


class FingerprintFormat:
    """Where the review block starts and what the attestation token looks like (§3.8).

    Every value here is part of a wire format shared by three parties — this package, the generated
    YAML files, and the collector in `tests/test_worked_examples.py` — so they are declared once,
    on one class, and derived from each other where they overlap: `BLOCK_MARKER` is `BLOCK_KEY`
    with the preceding newline, which is the only reason the emitter and the fingerprint split can
    be proven to agree rather than assumed to.
    """

    #: The line that opens the review block. `emit_yaml` writes exactly this.
    BLOCK_KEY = "review:\n"
    #: Everything before this marker is hashed for the content fingerprint; the block itself is not.
    BLOCK_MARKER = "\n" + BLOCK_KEY
    #: Hex characters of the SHA-256 digest that survive into the token.
    DIGEST_CHARACTERS = 12
    #: A fingerprint as it is typed into the workbook and printed by this tool.
    PATTERN = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}$")
    #: The policy file, next to the group directories rather than inside one.
    ENFORCEMENT_FILE_NAME = "enforcement.yaml"
    #: Its single key.
    ENFORCEMENT_KEY = "enforcement"


class ReviewState(enum.Enum):
    """Whether an example's attestation is valid, outdated, or absent (§3.8).

    An enum rather than three bare strings because the three states drive different reporting and
    the set is closed: a typo in a string comparison would silently classify a stale attestation as
    reviewed, which is the one mistake this layer exists to prevent. `STALE` is deliberately
    distinct from `UNREVIEWED` — someone *did* review an earlier revision, and that is a different
    signal to a reviewer than an example nobody has ever checked.
    """

    OK = "ok"
    STALE = "stale"
    UNREVIEWED = "unreviewed"


class EnforcementMode(enum.Enum):
    """Whether a missing or stale attestation warns or fails the suite (§3.8).

    Read from `enforcement.yaml`, which makes the severity a project decision rather than a code
    decision. The set is closed and validated on read: before that, the value was an unchecked free
    string, so `"ERROR"`, `"eror"` or any future third mode compared unequal to `"error"` and
    silently left the gate *disabled* — a policy file that fails open is worse than no policy file.
    """

    WARN = "warn"
    ERROR = "error"

    @classmethod
    def parse(cls, text: str, source: str) -> "EnforcementMode":
        """Resolves the switch's spelling to a mode, refusing anything outside the closed set.

        Args:
            text: The raw value read from the policy file.
            source: Path of that file, so the error names the file the reader has to edit.

        Returns:
            The matching mode.

        Raises:
            ValidationError: If the value is not exactly one of the accepted spellings.
        """
        for mode in cls:
            if text == mode.value:
                return mode
        accepted = ", ".join(repr(mode.value) for mode in cls)
        raise ValidationError(
            f"{source}: enforcement mode {text!r} is not one of {accepted}. An unrecognized value "
            "used to leave the review gate disabled instead of reporting the typo (§3.8)."
        )


def content_for_fingerprint(yaml_text: str) -> str:
    """The YAML minus its `review:` block — the content the fingerprint addresses (§3.8).

    Excluding the attestation from what it attests to is what breaks the self-reference: the block
    the reviewer's signature lands in would otherwise change the hash it contains. Everything
    semantic (inputs, derivations, expected values, tolerances) lies before the marker and is
    therefore inside the hash, so any edit to the example invalidates the attestation automatically.

    This function and `fingerprint_of` are the whole protocol, and they have exactly one home:
    `tests/test_worked_examples.py` validates attestations from the YAML alone (it never opens a
    workbook) by calling these two, instead of repeating the marker split a second time.
    """
    index = yaml_text.find(FingerprintFormat.BLOCK_MARKER)
    if index == -1:
        return yaml_text
    return yaml_text[: index + 1]


def fingerprint_of(content: str) -> str:
    """Shortened SHA-256 of the fingerprinted content, displayed as XXXX-XXXX-XXXX (§3.8).

    The attestation token: the converter prints it, the reviewer types it into the workbook's
    `reviewed_fingerprint` row after re-deriving the example by hand, and from then on it is
    reproduced by anyone regenerating the YAML. Truncated to 12 hex characters and grouped in
    threes purely for human transcription — this is a change detector, not a security boundary.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[: FingerprintFormat.DIGEST_CHARACTERS].upper()
    return f"{digest[0:4]}-{digest[4:8]}-{digest[8:12]}"


def review_status(example: Example, yaml_text: str) -> Tuple[ReviewState, str]:
    """Classifies one converted example's attestation and formats the line the CLI prints.

    The three states are distinguished on purpose: `STALE` means someone reviewed an earlier
    revision and the content has changed since. In both non-OK cases the message ends in the
    current fingerprint, which is exactly the string the reviewer types back into the workbook.

    Returns:
        `(state, message)` — the state and a one-line, human-addressed summary.
    """
    fingerprint = fingerprint_of(content_for_fingerprint(yaml_text))
    name = example.metadata["name"]
    stored = example.metadata["reviewed_fingerprint"]
    if stored == fingerprint:
        return (
            ReviewState.OK,
            f"{name}: reviewed by {example.metadata['reviewed_by']} on {example.metadata['review_date']}",
        )
    if stored:
        return (
            ReviewState.STALE,
            f"{name}: UNREVIEWED — stale review by {example.metadata['reviewed_by']} "
            f"(attested {stored}) — content fingerprint {fingerprint}",
        )
    return ReviewState.UNREVIEWED, f"{name}: UNREVIEWED — content fingerprint {fingerprint}"


def read_enforcement_mode(root: str) -> EnforcementMode:
    """Reads and validates the warn/error switch of `<root>/enforcement.yaml` (§3.8).

    The key must be present and spelled exactly: a missing or misspelled key used to fall back to
    `warn`, which meant a project that had turned enforcement *on* could be silently returned to
    the permissive state by one typo. Failing loudly instead is the point of this function.

    Args:
        root: The worked-example root directory holding the policy file.

    Returns:
        The declared mode.

    Raises:
        ValidationError: If the file does not parse into a mapping, does not declare the key, or
            declares a value outside {`warn`, `error`}.
    """
    path = os.path.join(root, FingerprintFormat.ENFORCEMENT_FILE_NAME)
    with open(path, encoding="utf-8") as handle:
        parsed = yaml.safe_load(handle)
    if not isinstance(parsed, dict) or FingerprintFormat.ENFORCEMENT_KEY not in parsed:
        raise ValidationError(
            f"{path}: no {FingerprintFormat.ENFORCEMENT_KEY!r} key; the review gate has no declared "
            "severity (§3.8)."
        )
    return EnforcementMode.parse(str(parsed[FingerprintFormat.ENFORCEMENT_KEY]), path)
