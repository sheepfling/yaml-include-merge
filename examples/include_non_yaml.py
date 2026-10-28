"""Load INI, dotenv, and text sources through YAML's include layer."""

from __future__ import annotations

from pathlib import Path

from yaml_compose import FormatRegistry, IniAdapter, load_yaml_file

_EXAMPLES = Path(__file__).parent


def load_example() -> object:
    """Return the documented default INI, dotenv, and text example result."""
    return load_yaml_file(_EXAMPLES / "include_non_yaml.yaml")


def load_conf_alias_example() -> object:
    """Load an ambiguous .conf source only after an explicit registry opt-in."""
    formats = FormatRegistry()
    formats.register(IniAdapter(), replace=True, suffixes=(".ini", ".conf", ".cfg"))
    return load_yaml_file(_EXAMPLES / "include_conf.yaml", format_registry=formats)


if __name__ == "__main__":
    print(load_example())
    print(load_conf_alias_example())
