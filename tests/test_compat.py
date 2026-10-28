"""Python-version compatibility helpers."""

from __future__ import annotations

from types import ModuleType

import pytest

import yaml_compose._compat as compat


@pytest.mark.parametrize(
    ("standard_library_missing", "expected_module", "expected_calls"),
    [
        (False, "tomllib", ["tomllib"]),
        (True, "tomli", ["tomllib", "tomli"]),
    ],
)
def test_toml_parser_uses_stdlib_then_python_310_backport(
    monkeypatch: pytest.MonkeyPatch,
    standard_library_missing: bool,
    expected_module: str,
    expected_calls: list[str],
) -> None:
    modules = {name: ModuleType(name) for name in ("tomllib", "tomli")}
    calls: list[str] = []

    def import_module(name: str) -> ModuleType:
        calls.append(name)
        if name == "tomllib" and standard_library_missing:
            raise ModuleNotFoundError(name=name)
        return modules[name]

    monkeypatch.setattr(compat.importlib, "import_module", import_module)

    assert compat.toml_parser_module() is modules[expected_module]
    assert calls == expected_calls
