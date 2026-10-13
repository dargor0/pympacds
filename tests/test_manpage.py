"""Tests for the generated pympacds-admin(1) man page.

The man page is not committed: it is generated from ``doc/cli.md`` with Sphinx
(the same source that feeds the HTML docs). These tests build it into a temp
directory and validate the roff with groff/mandoc; they skip when Sphinx or the
roff tooling is unavailable.
"""

import os
import shutil
import subprocess
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DOC = os.path.join(_ROOT, "doc")
_CLI_MD = os.path.join(_DOC, "cli.md")

_GROFF = shutil.which("groff")
_MANDOC = shutil.which("mandoc")

requires_groff = pytest.mark.skipif(_GROFF is None, reason="groff not installed")
requires_mandoc = pytest.mark.skipif(_MANDOC is None, reason="mandoc not installed")


def _project_version():
    pyproject = os.path.join(_ROOT, "pyproject.toml")
    with open(pyproject) as f:
        for line in f:
            if line.startswith("version = "):
                return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.fixture(scope="session")
def man_page(tmp_path_factory):
    """Build doc/cli.md into a man page with Sphinx; return its path."""
    pytest.importorskip("sphinx")
    pytest.importorskip("myst_parser")

    out = tmp_path_factory.mktemp("man")
    env = {**os.environ, "PYMPACDS_MAN_BUILD": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "sphinx", "-E", "-b", "man", _DOC, str(out)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"sphinx man build failed:\n{result.stdout}\n{result.stderr}")

    path = out / "pympacds-admin.1"
    if not path.exists():
        produced = sorted(p.name for p in out.iterdir())
        pytest.fail(f"man page not produced (got: {produced})")
    return path


def test_cli_source_exists():
    assert os.path.exists(_CLI_MD), "doc/cli.md (man page source) is missing"


def test_manpage_has_required_sections(man_page):
    text = man_page.read_text()
    assert ".TH " in text, "missing .TH title"
    for macro in (".SH NAME", ".SH SYNOPSIS", ".SH COMMANDS"):
        assert macro in text, f"missing {macro}"
    assert ".SH EXAMPLES" in text
    assert ".SH FILES" in text
    assert "pympacds-admin" in text
    assert "program" in text  # the custom-program use case


def test_manpage_version_matches_project(man_page):
    version = _project_version()
    if version is None:
        pytest.skip("no version in pyproject.toml")
    title = next((ln for ln in man_page.read_text().splitlines() if ln.startswith(".TH ")), "")
    assert title, "no .TH line found"
    assert version in title, f"man page .TH does not mention {version}: {title!r}"


@requires_groff
def test_manpage_renders_content_with_groff(man_page):
    result = subprocess.run(
        [_GROFF, "-man", "-Tascii", str(man_page)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for token in ("pympacds-admin", "SYNOPSIS", "COMMANDS", "FILES", "EXAMPLES", "program"):
        assert token in out, f"missing {token!r} in rendered man page"


@requires_groff
def test_manpage_groff_has_no_errors(man_page):
    result = subprocess.run(
        [_GROFF, "-man", "-Tascii", "-z", "-ww", str(man_page)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "error" not in result.stderr.lower(), result.stderr


@requires_mandoc
def test_manpage_mandoc_lint_has_no_errors(man_page):
    result = subprocess.run(
        [_MANDOC, "-Tlint", str(man_page)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert "ERROR" not in output, output
