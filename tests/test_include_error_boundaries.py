"""Shared include errors retain identity and actionable budget diagnostics."""

from __future__ import annotations

import pickle
from io import StringIO

import pytest
import yaml

from yaml_compose import (
    IncludeRouteFrame,
    JsonPathLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    _include_errors,
    get_include_route,
    loader,
)


def test_public_route_identity_and_pickle_address_survive_internal_extraction() -> None:
    assert IncludeRouteFrame is loader.IncludeRouteFrame is _include_errors.IncludeRouteFrame
    assert get_include_route is loader.get_include_route is _include_errors.get_include_route
    frame = IncludeRouteFrame("source", 1, 2, "!include", "data.yaml", None)
    assert frame.__class__.__module__ == "yaml_compose.loader"
    assert pickle.loads(pickle.dumps(frame)) == frame


@pytest.mark.parametrize(
    ("message", "hint"),
    [
        ("JSONPath maximum input bytes (1) exceeded", True),
        ("JSONPath execution timeout (2.0 seconds) exceeded", True),
        ("JSONPath worker failed: ValueError: maximum matches (1) exceeded", True),
        ("JSONPath worker failed: JSONPathSyntaxError: invalid query '$.maximum'", False),
        ("JSONPath execution timeout cannot be represented by this runtime", False),
        ("JSONPath input object member names must be strings", False),
    ],
)
def test_budget_hint_is_actionable_without_mislabeling_invalid_queries(
    monkeypatch: pytest.MonkeyPatch, message: str, hint: bool
) -> None:
    cause = ValueError(message)

    def fail(value: object, query: str, limits: JsonPathLimits) -> list[object]:
        raise cause

    monkeypatch.setattr(loader, "select_jsonpath", fail)
    root = MemoryRoot("budget-hints")
    with pytest.raises(yaml.YAMLError) as caught:
        loader.load_yaml_compose(
            StringIO("!include {path: data.yaml, json_path: '$'}"),
            allowed_roots=[root],
            include_resolver=MemoryIncludeResolver(root, {"data.yaml": "{}"}),
        )
    assert ("jsonpath_limits=JsonPathLimits.generous()" in str(caught.value)) is hint
    assert message in str(caught.value)
    assert caught.value.__cause__ is cause
    assert get_include_route(caught.value)[-1].requested_path == "data.yaml"
