"""Loading the case files of a suite and resolving their inheritance (FR1).

A directory holds one YAML file per case. A file either describes a case completely or names a
parent in ``derives_from`` and restates only what differs: mappings are merged key by key,
recursively; a list, a scalar or ``null`` replaces the parent's value whole. The resolved content
is what :class:`hisim.verification.schema.Case` is built from and what the suite writes beside its
results, so every number of a run can be read in one place.
"""

import copy
from pathlib import Path
from typing import Any, Dict, Mapping

import yaml

from hisim.verification.schema import Case, CaseFileError

#: The key a derived case names its parent under.
PARENT_KEY = "derives_from"


def read_case_files(directory: Path) -> Dict[str, Dict[str, Any]]:
    """Read every ``*.yaml`` of a directory as the raw mapping of one case, keyed by case id.

    Args:
        directory: the case directory.

    Returns:
        Dict[str, Dict[str, Any]]: case id -> the file's content.

    Raises:
        CaseFileError: for a file without a ``case`` key, or two files naming the same case.
    """
    raw: Dict[str, Dict[str, Any]] = {}
    for path in sorted(directory.glob("*.yaml")):
        with open(path, encoding="utf-8") as case_file:
            content = yaml.safe_load(case_file)
        if not isinstance(content, dict) or "case" not in content:
            raise CaseFileError(f"{path.name}: not a case file (a mapping with a 'case' key)")
        case_id = str(content["case"])
        if case_id in raw:
            raise CaseFileError(f"{path.name}: case {case_id} is already defined by another file")
        raw[case_id] = content
    return raw


def merge(parent: Mapping[str, Any], child: Mapping[str, Any]) -> Dict[str, Any]:
    """Lay a child's mapping over its parent's: mappings merge recursively, everything else replaces.

    Args:
        parent: the resolved parent.
        child: what the child restates.

    Returns:
        Dict[str, Any]: the merged copy; neither argument is changed.
    """
    result: Dict[str, Any] = copy.deepcopy(dict(parent))
    for key, value in child.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve(raw: Mapping[str, Mapping[str, Any]], case_id: str, chain: tuple = ()) -> Dict[str, Any]:
    """Resolve one case's inheritance into a complete mapping.

    Args:
        raw: every case's file content, keyed by id.
        case_id: the case to resolve.
        chain: the ids already on the path, for the cycle check.

    Returns:
        Dict[str, Any]: the complete mapping, without the ``derives_from`` key.

    Raises:
        CaseFileError: for an unknown case or parent, or a cycle.
    """
    if case_id in chain:
        raise CaseFileError(f"case {case_id}: the inheritance is a cycle: {' -> '.join(chain + (case_id,))}")
    if case_id not in raw:
        raise CaseFileError(f"case {case_id}: no such case file")
    own = dict(raw[case_id])
    parent_id = own.pop(PARENT_KEY, None)
    if parent_id is None:
        return copy.deepcopy(own)
    parent = resolve(raw, str(parent_id), chain + (case_id,))
    return merge(parent, own)


def load_cases(directory: Path) -> Dict[str, Case]:
    """Load and resolve every case of a directory.

    Args:
        directory: the case directory.

    Returns:
        Dict[str, Case]: case id -> the resolved case, in file order.
    """
    raw = read_case_files(directory)
    return {case_id: Case.from_mapping(resolve(raw, case_id)) for case_id in raw}


def resolved_mapping(directory: Path, case_id: str) -> Dict[str, Any]:
    """The complete mapping of one case, for writing beside the results."""
    return resolve(read_case_files(directory), case_id)
