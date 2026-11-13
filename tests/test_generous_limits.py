"""Convenience profiles enlarge finite budgets without changing defaults."""

from dataclasses import asdict
from io import StringIO

from yaml_compose import (
    DEFAULT_INCLUDE_LIMITS,
    DEFAULT_JSONPATH_LIMITS,
    IncludeLimits,
    JsonPathLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    YamlConfigLoader,
    get_default_include_limits,
)


def test_generous_include_profile_is_explicit_finite_and_independent() -> None:
    original = asdict(get_default_include_limits())
    baseline = asdict(DEFAULT_INCLUDE_LIMITS)
    first = IncludeLimits.generous()
    second = IncludeLimits.generous()
    assert asdict(first) == {
        "max_include_depth": 64,
        "max_include_files": 8192,
        "max_file_bytes": 67_108_864,
        "max_total_include_bytes": 536_870_912,
        "max_glob_steps": 1_000_000,
    }
    assert first == second and first is not second
    first.max_include_files = 1
    assert second.max_include_files == 8192
    assert asdict(get_default_include_limits()) == original
    assert asdict(DEFAULT_INCLUDE_LIMITS) == baseline
    assert asdict(IncludeLimits()) == baseline


def test_generous_query_profile_is_explicit_finite_and_independent() -> None:
    baseline = asdict(DEFAULT_JSONPATH_LIMITS)
    first = JsonPathLimits.generous()
    second = JsonPathLimits.generous()
    assert asdict(first) == {
        "max_query_bytes": 65_536,
        "max_input_bytes": 67_108_864,
        "max_input_nodes": 1_000_000,
        "max_input_depth": 256,
        "max_matches": 100_000,
        "max_output_bytes": 67_108_864,
        "timeout_seconds": 10.0,
    }
    assert first == second and first is not second
    assert asdict(DEFAULT_JSONPATH_LIMITS) == baseline
    assert asdict(JsonPathLimits()) == baseline


def test_generous_profiles_work_through_loader_and_nested_selection() -> None:
    root = MemoryRoot("generous")
    loader = YamlConfigLoader(
        include_limits=IncludeLimits.generous(),
        jsonpath_limits=JsonPathLimits.generous(),
        allowed_roots=[root],
        include_resolver=MemoryIncludeResolver(
            root,
            {
                "outer.yaml": "!include {path: inner.yaml, json_path: '$.items[*]'}",
                "inner.yaml": "items: [1, 2, 3]",
            },
        ),
    )
    assert loader.load_include(StringIO("!include outer.yaml")) == [1, 2, 3]
