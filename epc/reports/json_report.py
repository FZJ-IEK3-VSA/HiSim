"""One result dictionary written as ``<run path>/<building>.epc.json``.

The file is :meth:`~epc.model.result.EpcResult.to_dict` as it is, the JSON of requirements Mockup A
(R4.2, R7.1). Numbers are written at full precision; a value that is not a finite number is refused,
because JSON has no spelling for it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

#: Suffix of a result file, after the building's name.
SUFFIX = ".epc.json"


def write(document: Mapping[str, Any], path: Path) -> Path:
    """Writes one result dictionary, creating the folders above it.

    Args:
        document: The result as :meth:`~epc.model.result.EpcResult.to_dict` returns it.
        path: The file to write.

    Returns:
        The path written.

    Raises:
        ValueError: When the document holds a number that is not finite.
    """
    text = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return path
