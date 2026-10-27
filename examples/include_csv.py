"""Show the explicit row-list and header-record CSV loading modes."""

from pathlib import Path

from yaml_compose import CsvAdapter, FormatRegistry, YamlConfigLoader


def load_example() -> dict[str, object]:
    """Load one CSV source with both supported result shapes."""
    root = Path(__file__).with_name("include_csv")
    config = root / "main.yaml"

    rows = YamlConfigLoader().load_file(config)
    formats = FormatRegistry()
    formats.register(CsvAdapter(mode="dicts"), replace=True)
    records = YamlConfigLoader(format_registry=formats).load_file(config)
    return {"rows": rows, "records": records}
