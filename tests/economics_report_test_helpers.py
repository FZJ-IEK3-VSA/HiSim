"""Parsers the renderer test modules of the visualization set share.

`tests/test_economics_sections_a.py`, `..._b.py` and `..._c.py` are one suite in three files —
the first half of the chart set, the second, and the chapters they are told in — and all of them
assert on the *emitted* file rather than on the renderer's own variables, because that is where
the defects they pin were visible. They therefore need the same parsers, and each had a
byte-identical copy of them: a rectangle reader, a section splitter and the two patterns that say
how a section opens. Two copies of a parser is how one of them ends up tolerating a rendering the
other rejects, which in a set of files that exist to police a rendering is the whole failure.

Not a `conftest.py` fixture set: these are pure functions of a string with no test state in them,
and the repository's own convention for that is a plain module beside the tests that import it
(`tests/functions_for_testing.py`, `tests/building_golden_support.py`).
"""

# clean

import re
from typing import List, Optional, Tuple


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


#: How a section opens: its heading (with the chapter tag), then the four authored parts — what
#: it shows, what it adds, the terms it uses and how it is calculated. `scaffold._explanation_html`
#: emits exactly this, and every section of the report either matches it or matches the
#: cross-reference below.
_SECTION_HEADING = (
    r"<section id=\"[^\"]+\"><h3>[^<]*(?:<span class='chapter-tag'>[^<]*</span>)?</h3>"
)
FOUR_PART_OPENING = re.compile(
    _SECTION_HEADING
    + r"<p class='sub'>.+?</p>"
    r"<p class='sub'>.+?</p>"
    r"<details><summary>Terms used here</summary><dl><dt>.+?</dl></details>"
    r"<details><summary>How this is calculated</summary><p class='sub'>.+?</details>",
    flags=re.S,
)

#: The single paragraph a section renders instead when its name was already explained in an
#: earlier chapter; the capture group is the anchor it points at.
CROSS_REFERENCE_OPENING = re.compile(
    _SECTION_HEADING
    + r"<p class='sub'>The same chart, read the same way: see the explanation under "
    r"<a href=\"#([^\"]+)\">[^<]+</a>\.</p>",
    flags=re.S,
)


def opens_with_four_parts(html: str) -> bool:
    """Whether one rendered section opens with the four authored parts.

    Args:
        html: One `<section>` element, as `rendered_sections` returns it.

    Returns:
        True when the heading is followed by the two paragraphs and the two disclosures.
    """
    return FOUR_PART_OPENING.match(html) is not None


def back_link_target(html: str) -> Optional[str]:
    """The anchor a repeated section points its reader at, or None if it is not a repeat.

    A section name appearing in a second chapter renders one cross-reference paragraph instead of
    the four parts, and the anchor in it is the whole content of the claim: it has to name a
    section that is really in the document and really is a different one.

    Args:
        html: One `<section>` element, as `rendered_sections` returns it.

    Returns:
        The anchor without its `#`, or None when this section carries its own explanation.
    """
    match = CROSS_REFERENCE_OPENING.match(html)
    return match.group(1) if match is not None else None
