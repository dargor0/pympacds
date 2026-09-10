"""Sphinx configuration for pympacds documentation."""

import os
import sys

# Allow autodoc to import the pympacds package from src/.
sys.path.insert(0, os.path.abspath("../src"))

project = "pympacds"
copyright = "2026, Oscar Diaz"
author = "Oscar Diaz <odiaz@ieee.org>"

extensions = [
    "myst_parser",  # Markdown source support
    "sphinx.ext.autodoc",  # API docs from docstrings
    "sphinx.ext.napoleon",  # Google-style / NumPy-style docstrings
    "sphinx.ext.viewcode",  # links to source code
]

myst_enable_extensions = [
    "colon_fence",
]

# Render type hints in the description rather than the signature.
autodoc_typehints = "description"
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}

napoleon_google_docstring = True
napoleon_numpy_docstring = False

html_theme = "furo"
html_title = "pympacds"
html_theme_options = {}

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
