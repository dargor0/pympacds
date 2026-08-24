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

    def validate(
        self, cp: configparser.ConfigParser, strict: bool = False
    ) -> list[str]:
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
            if section in ("DEFAULT", "dbus"):
                continue
            if not cp.has_section(section):
                continue
            required = spec.get("required", [])
            keys = spec.get("keys", {})
            for rk in required:
                if rk not in cp[section]:
                    errors.append(f"{section}.{rk}: required key is missing")
            for kname, kspec in keys.items():
                if kname not in cp[section]:
                    if "default" in kspec:
                        cp[section][kname] = str(kspec["default"])
                    continue
                val = cp[section][kname]
                ktype = kspec.get("type", "str")
                if ktype == "int":
                    try:
                        ival = int(val)
                        if "min" in kspec and ival < kspec["min"]:
                            errors.append(
                                f"{section}.{kname}: {ival} < min {kspec['min']}"
                            )
                        if "max" in kspec and ival > kspec["max"]:
                            errors.append(
                                f"{section}.{kname}: {ival} > max {kspec['max']}"
                            )
                    except ValueError:
                        errors.append(
                            f"{section}.{kname}: expected int, got '{val}'"
                        )
                elif ktype == "float":
                    try:
                        fval = float(val)
                        if "min" in kspec and fval < kspec["min"]:
                            errors.append(
                                f"{section}.{kname}: {fval} < min {kspec['min']}"
                            )
                        if "max" in kspec and fval > kspec["max"]:
                            errors.append(
                                f"{section}.{kname}: {fval} > max {kspec['max']}"
                            )
                    except ValueError:
                        errors.append(
                            f"{section}.{kname}: expected float, got '{val}'"
                        )
                elif ktype == "bool":
                    try:
                        cp.getboolean(section, kname)
                    except ValueError:
                        errors.append(
                            f"{section}.{kname}: expected bool, got '{val}'"
                        )
                elif ktype == "str" and "pattern" in kspec:
                    import re

                    if not re.match(kspec["pattern"], val):
                        errors.append(
                            f"{section}.{kname}: '{val}' does not match "
                            f"pattern '{kspec['pattern']}'"
                        )

        return errors
