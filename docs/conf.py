"""Configuration file for the Sphinx documentation builder."""
# pylint: skip-file

# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use pathlib to make it absolute, like shown here.
#
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# -- Project information -----------------------------------------------------

project: str = 'House Infrastructure Simulator'
copyright: str = '2020-2022, Forschungszentrum Jülich, IEK-3'
author: str = 'Vitor Hugo Bellotto Zago, Noah Pflugradt'

# The full version, including alpha/beta/rc tags
release: str = '0.1'


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions: list[str] = [
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx.ext.graphviz',
    'sphinx.ext.inheritance_diagram',
    'sphinxcontrib.mermaid',
]
numfig: bool = True

# Render "Attributes:" sections as :ivar: fields instead of .. attribute::
# directives, which would duplicate the attribute docs autodoc generates.
napoleon_use_ivar: bool = True

# Generate one page per module from the autosummary directives in
# components.rst and postprocessing.rst (written to docs/_autosummary).
autosummary_generate: bool = True

# wetterdienst is commented out in requirements.txt until
# weather_data_import.py is migrated to its new API, so mock it here to keep
# hisim.components.weather_data_import importable for autodoc.
autodoc_mock_imports: list[str] = ['wetterdienst']


def _drop_third_party_docstrings(app, what, name, obj, options, lines):
    """Drop docstrings inherited from dataclasses_json.

    Its ``to_dict``/``from_dict`` docstrings contain reST that docutils cannot
    parse, which floods the build with warnings for every config dataclass.
    """
    module = getattr(obj, "__module__", "") or ""
    if module.startswith("dataclasses_json"):
        lines.clear()


def setup(app):
    """Register Sphinx event handlers for this documentation build.

    Hooks ``_drop_third_party_docstrings`` into the
    ``autodoc-process-docstring`` event so docstrings inherited from
    dataclasses_json are stripped before rendering.
    """
    app.connect("autodoc-process-docstring", _drop_third_party_docstrings)


# Add any paths that contain templates here, relative to this directory.
templates_path: list[str] = ['_templates']

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns: list[str] = ['_build', 'Thumbs.db', '.DS_Store']


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
# html_theme = 'alabaster'
html_theme: str = 'sphinx_rtd_theme'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path: list[str] = []
