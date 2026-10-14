# Contributing to pympacds

Thanks for your interest! Contributions of all kinds are welcome — bug reports, docs, tests, services, and code.

## Development setup

Requires Python ≥ 3.10 and (for the integration tests) a `dbus-daemon` on `PATH`.

```bash
git clone https://github.com/dargor0/pympacds
cd pympacds
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,dbus-fast,dbus-next]"
```

## Running the checks

Every change must pass the quality gate — formatting, linting, type check, tests (with coverage), and cyclomatic complexity:

```bash
ruff format --check .
ruff check .
mypy src/pympacds
pytest
lizard src -C 10
```

For a service, run the same commands from `services/<name>/`, using `mypy src/pympacds_<name>`.

## Conventions

- **Style**: `ruff` (format + lint) and `mypy` must pass.
- **Complexity**: keep functions at `lizard -C 10` or below — refactor rather than suppress.
- **Tests**: add tests for new behaviour; keep coverage at or above the existing level. Prefer black-box D-Bus tests for service behaviour.
- **Commits**: small, focused, imperative-mood messages.
- **Services**: live under `services/<name>/` with their own `pyproject.toml`, `src/`, `tests/`, `config/` and `debian/`.

## Reporting issues

When opening an issue, please include:

- the version (`pympacds-admin --version`);
- **how you installed pympacds** — pip (`pip install pympacds`) or the Debian/Ubuntu package from the PPA (`apt install python3-pympacds`);
- your platform (distro / Python version / D-Bus backend) and D-Bus configuration (bus type and `bus_prefix`);
- what you expected and what happened, with the relevant logs.

See the issue templates for the details.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
