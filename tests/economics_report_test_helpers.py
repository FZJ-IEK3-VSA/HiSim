"""Parsers the two renderer test modules of the visualization set share.

`tests/test_economics_sections_a.py` and `tests/test_economics_sections_b.py` are two halves of
one suite — the first half of the chart set and the second — and both assert on the *emitted*
file rather than on the renderer's own variables, because that is where the defects they pin were
visible. Both therefore need the same two parsers, and both had a byte-identical copy of them: a
rectangle reader and a section splitter. Two copies of a parser is how one of them ends up
tolerating a rendering the other rejects, which in a pair of files that exist to police a
rendering is the whole failure.

Not a `conftest.py` fixture set: these are pure functions of a string with no test state in them,
and the repository's own convention for that is a plain module beside the tests that import it
(`tests/functions_for_testing.py`, `tests/building_golden_support.py`).
"""

# clean

import re
from typing import List, Tuple


def rects(svg: str) -> List[Tuple[float, float, float, float]]:
    """Every rectangle of an inline-SVG chart as `(x, y, width, height)`.

    The marks of a bar chart, the tiles of a treemap and the nodes of a Sankey are all `<rect>`,
    which is why one parser serves all three. Rectangles that carry a class before their
    coordinates — the Sankey's net-position stubs — are deliberately not matched: they are chrome
    closing a node's deficient face, not a mark, and the one test that wants them has its own
    reader.

    Args:
        svg: The rendered `<svg>` element, or a document containing one.

    Returns:
        One tuple per rectangle, in emission order.
    """
    return [
        (float(x), float(y), float(w), float(h))
        for x, y, w, h in re.findall(
            r'<rect x="(-?[\d.]+)" y="(-?[\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg
        )
    ]


def rendered_sections(text: str) -> List[Tuple[str, str]]:
    """Every anchored section of a rendered report as `(anchor, html)`, in page order.

    The anchor is the chapter-prefixed id a contents link resolves against, so a test that looks
    a section up by it is asserting on the same identity a reader navigates by.

    Args:
        text: The rendered document.

    Returns:
        One `(anchor, html)` pair per `<section id="...">`, in the order they appear.
    """
    return [
        (match.group(1), match.group(0))
        for match in re.finditer(r"<section id=\"([^\"]+)\">.*?</section>", text, flags=re.S)
    ]
