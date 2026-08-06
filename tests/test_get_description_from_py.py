"""Unit tests for :func:`hisim.hisim_convert_to_json.get_description_from_py`.

``get_description_from_py`` reads only the first line of a Python setup file and
strips a leading triple-double-quote or triple-single-quote delimiter. It is the
only self-contained,
deterministic function in ``hisim_convert_to_json`` (the others require a full
simulator), so these are pure-function tests using pytest's ``tmp_path`` fixture:
no mocking, no simulator, no network.

The function uses ``str.replace(quote_type, '')`` (no ``count`` argument), which
removes *every* occurrence of the delimiter on the first line, not only the
leading one. The expected values below reflect that verified behaviour rather
than a one-off leading-only strip.
"""

# clean

from pathlib import Path

import pytest

from hisim.hisim_convert_to_json import get_description_from_py


def _write_first_line(tmp_path: Path, first_line: str) -> Path:
    """Write ``first_line`` as the sole content of ``setup.py`` and return it.

    A trailing newline is appended only when ``first_line`` does not already end
    with one, mirroring a normal source file; ``readline`` returns the line up to
    and including the newline and ``.strip()`` removes it, so the newline never
    affects the assertion.
    """
    setup_py = tmp_path / "setup.py"
    content = first_line if first_line.endswith("\n") else first_line + "\n"
    setup_py.write_text(content, encoding="utf-8")
    return setup_py


@pytest.mark.base
def test_double_quoted_one_line_docstring(tmp_path: Path) -> None:
    """A one-line triple-double-quoted docstring keeps its inner text.

    Both the leading and trailing triple-double-quotes are removed because
    ``replace`` substitutes every occurrence, so the returned description has no
    surrounding quotes.
    """
    setup_py = _write_first_line(tmp_path, '"""A household setup."""')
    assert get_description_from_py(setup_py) == "A household setup."


@pytest.mark.base
def test_single_quoted_one_line_docstring(tmp_path: Path) -> None:
    """A one-line ``'''...'''`` docstring is stripped of both triple-single-quotes."""
    setup_py = _write_first_line(tmp_path, "'''Single-quoted docstring.'''")
    assert get_description_from_py(setup_py) == "Single-quoted docstring."


@pytest.mark.base
def test_double_quoted_with_trailing_comment(tmp_path: Path) -> None:
    """A leading triple-double-quote followed by a closing one and a comment.

    Every triple-double-quote on the line is removed and the remainder is
    stripped, so the closing delimiter and the trailing comment survive as plain
    text.
    """
    setup_py = _write_first_line(
        tmp_path, '"""Description with trailing content""" # comment'
    )
    assert (
        get_description_from_py(setup_py)
        == "Description with trailing content # comment"
    )


@pytest.mark.base
def test_line_without_quote_marker_is_unchanged(tmp_path: Path) -> None:
    """A first line with no quote marker is returned stripped but otherwise unchanged."""
    setup_py = _write_first_line(tmp_path, "# just a comment")
    assert get_description_from_py(setup_py) == "# just a comment"


@pytest.mark.base
def test_lone_opening_triple_quotes(tmp_path: Path) -> None:
    """A first line that is only the opening triple-double-quote yields an empty string.

    Removing every triple-double-quote from a line that contains only one
    produces an empty string.
    """
    setup_py = _write_first_line(tmp_path, '"""')
    assert get_description_from_py(setup_py) == ""


@pytest.mark.base
def test_empty_file(tmp_path: Path) -> None:
    """A zero-byte file: ``readline`` returns ``''`` and ``.strip()`` keeps it empty."""
    setup_py = tmp_path / "setup.py"
    setup_py.write_text("", encoding="utf-8")
    assert get_description_from_py(setup_py) == ""


@pytest.mark.base
def test_whitespace_only_first_line(tmp_path: Path) -> None:
    """A first line containing only whitespace is stripped to an empty string."""
    setup_py = _write_first_line(tmp_path, "   \n")
    assert get_description_from_py(setup_py) == ""
