"""ConfigManager — centralized configuration management."""

import configparser
import json
import logging


class ConfigManager:
    """Provides methods for reading, writing, and validating configuration.

    Args:
        logger: Logger instance.
        schema_file: Optional path to a JSON schema file.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        schema_file: str | None = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.schema_file = schema_file

    def read_ini(self, path: str) -> configparser.ConfigParser:
        """Read an INI configuration file and return a ConfigParser."""
        cp = configparser.ConfigParser()
        cp.read(path)
        return cp

    def write_ini(self, cp: configparser.ConfigParser, path: str) -> None:
        """Write a ConfigParser to an INI file."""
        with open(path, "w") as f:
            cp.write(f)

    def validate(self, cp: configparser.ConfigParser, strict: bool = False) -> list[str]:
        """Validate a ConfigParser against the schema file.

        Args:
            cp: The configuration to validate.
            strict: If True, unknown sections/keys raise errors.

        Returns:
            A list of error strings (empty = valid).
        """
        errors: list[str] = []
        if not self.schema_file:
            return errors
        try:
            with open(self.schema_file) as f:
                schema = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Cannot load schema '{self.schema_file}': {exc}")
            return errors

        for section, spec in schema.items():
            self._validate_section(section, spec, cp, errors)
        return errors

    def _validate_section(self, section: str, spec: dict, cp, errors: list[str]) -> None:
        if section in ("DEFAULT", "dbus"):
            return
        if not cp.has_section(section):
            return
        self._check_required(section, spec, cp, errors)
        for kname, kspec in spec.get("keys", {}).items():
            if kname not in cp[section]:
                if "default" in kspec:
                    cp[section][kname] = str(kspec["default"])
                continue
            self._check_key(section, kname, cp[section][kname], kspec, cp, errors)

    def _check_required(self, section: str, spec: dict, cp, errors: list[str]) -> None:
        for rk in spec.get("required", []):
            if rk not in cp[section]:
                errors.append(f"{section}.{rk}: required key is missing")

    def _check_key(self, section, kname, val, kspec, cp, errors: list[str]) -> None:
        ktype = kspec.get("type", "str")
        if ktype == "int":
            self._check_int(section, kname, val, kspec, errors)
        elif ktype == "float":
            self._check_float(section, kname, val, kspec, errors)
        elif ktype == "bool":
            self._check_bool(section, kname, val, cp, errors)
        elif ktype == "str" and "pattern" in kspec:
            self._check_pattern(section, kname, val, kspec, errors)

    def _check_int(self, section, kname, val, kspec, errors: list[str]) -> None:
        try:
            ival = int(val)
        except ValueError:
            errors.append(f"{section}.{kname}: expected int, got '{val}'")
            return
        if "min" in kspec and ival < kspec["min"]:
            errors.append(f"{section}.{kname}: {ival} < min {kspec['min']}")
        if "max" in kspec and ival > kspec["max"]:
            errors.append(f"{section}.{kname}: {ival} > max {kspec['max']}")

    def _check_float(self, section, kname, val, kspec, errors: list[str]) -> None:
        try:
            fval = float(val)
        except ValueError:
            errors.append(f"{section}.{kname}: expected float, got '{val}'")
            return
        if "min" in kspec and fval < kspec["min"]:
            errors.append(f"{section}.{kname}: {fval} < min {kspec['min']}")
        if "max" in kspec and fval > kspec["max"]:
            errors.append(f"{section}.{kname}: {fval} > max {kspec['max']}")

    def _check_bool(self, section, kname, val, cp, errors: list[str]) -> None:
        try:
            cp.getboolean(section, kname)
        except ValueError:
            errors.append(f"{section}.{kname}: expected bool, got '{val}'")

    def _check_pattern(self, section, kname, val, kspec, errors: list[str]) -> None:
        import re

        if not re.match(kspec["pattern"], val):
            errors.append(
                f"{section}.{kname}: '{val}' does not match "
                f"pattern '{kspec['pattern']}'"
            )
