"""Sphinx configuration for pympacds documentation."""

import glob
import os
import sys
from email.utils import parsedate_to_datetime

# Allow autodoc to import the pympacds package from src/.
sys.path.insert(0, os.path.abspath("../src"))

project = "pympacds"
copyright = "2026, Oscar Diaz"
author = "Oscar Diaz <odiaz@ieee.org>"


def _project_version() -> str:
    """Read the version from pyproject.toml (best effort)."""
    pyproject = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pyproject.toml"))
    try:
        with open(pyproject, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version = "):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


version = _project_version()
release = version


def _release_date() -> str:
    """Release date (YYYY-MM-DD) from the top debian/changelog entry.

    Used as the man page ``.TH`` date so it reflects the version bump (the
    manual's revision date) rather than the build date, keeping builds
    reproducible.
    """
    changelog = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "debian", "changelog")
    )
    try:
        with open(changelog, encoding="utf-8") as f:
            for line in f:
                if line.startswith(" -- "):
                    stamp = line.strip().rsplit("  ", 1)[-1]
                    return parsedate_to_datetime(stamp).strftime("%Y-%m-%d")
    except (OSError, ValueError):
        pass
    return ""


today = _release_date()

extensions = [
    "myst_parser",  # Markdown source support
    "sphinx.ext.autodoc",  # API docs from docstrings
    "sphinx.ext.napoleon",  # Google-style / NumPy-style docstrings
    "sphinx.ext.viewcode",  # links to source code
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
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

# Man page: generated from doc/cli.md by debian/rules (PYMPACDS_MAN_BUILD=1).
man_pages = [
    (
        "cli",
        "pympacds-admin",
        "administration tool for pympacds services",
        ["Oscar Diaz"],
        1,
    )
]
if os.environ.get("PYMPACDS_MAN_BUILD"):
    # Build *only* cli.md. The man builder reads every reachable document, and
    # api.md's autodoc imports pympacds.contracts (which needs a D-Bus library),
    # so make cli.md the root and exclude every other document: the build is then
    # dependency-light and free of "not in toctree" warnings.
    _docdir = os.path.dirname(os.path.abspath(__file__))
    _others = [
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(os.path.join(_docdir, "*.md"))
        if os.path.basename(p) != "cli.md"
    ]
    root_doc = "cli"
    exclude_patterns = [*exclude_patterns, *_others]
