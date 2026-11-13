"""A normal package install supplies the strict JSONPath engine."""

from collections.abc import Callable
from pathlib import Path
from typing import cast

from packaging.requirements import Requirement

from yaml_compose._compat import toml_parser_module


def test_jsonpath_is_a_required_strict_dependency_not_an_extra() -> None:
    # Read the source contract rather than a potentially stale editable egg-info.
    loads = cast(Callable[[str], dict[str, object]], toml_parser_module().loads)
    data = loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    project = cast(dict[str, object], data["project"])
    requirements = [Requirement(value) for value in cast(list[str], project["dependencies"])]
    engines = [item for item in requirements if item.name == "python-jsonpath"]
    assert len(engines) == 1
    engine = engines[0]
    assert engine.marker is None
    assert engine.extras == {"strict"}
    assert "2.2.2" in engine.specifier
    assert "3.0" not in engine.specifier
    extras = cast(dict[str, object], project["optional-dependencies"])
    assert "jsonpath" not in extras
