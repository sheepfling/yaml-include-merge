# Security boundary tests intentionally exercise private transport helpers.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import date
from io import BytesIO, TextIOWrapper
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from yaml_compose import _jsonpath, _jsonpath_worker
from yaml_compose._jsonpath import DEFAULT_JSONPATH_LIMITS, JsonPathLimits, select_jsonpath


@pytest.mark.parametrize(
    "field",
    ["max_query_bytes", "max_input_bytes", "max_input_nodes", "max_input_depth", "max_matches", "max_output_bytes"],
)
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.5, "3", float("inf"), float("nan")])
def test_integer_limits_require_positive_exact_integers(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        JsonPathLimits(**cast(dict[str, int], {field: value}))


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "3", 10**1000, float("inf"), float("-inf"), float("nan")])
def test_timeout_requires_positive_finite_number(value: object) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        JsonPathLimits(timeout_seconds=cast(float, value))


def test_defaults_are_immutable_and_explicitly_bounded() -> None:
    assert JsonPathLimits() == DEFAULT_JSONPATH_LIMITS
    assert JsonPathLimits(timeout_seconds=1).timeout_seconds == 1
    assert replace(DEFAULT_JSONPATH_LIMITS, max_matches=2).max_matches == 2
    with pytest.raises(FrozenInstanceError):
        DEFAULT_JSONPATH_LIMITS.max_matches = 2  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("value", [None, {}, object()])
def test_profile_type_is_checked(value: object) -> None:
    with pytest.raises(TypeError, match="JsonPathLimits instance"):
        select_jsonpath({}, "$", cast(JsonPathLimits, value))


@pytest.mark.parametrize("query", [None, True, 1, "", "\ud800"])
def test_invalid_query_values_are_rejected_before_launch(query: object) -> None:
    with pytest.raises(ValueError, match="JSONPath query"):
        select_jsonpath({}, cast(str, query), DEFAULT_JSONPATH_LIMITS)


@pytest.mark.parametrize("query", ["$['abcdefgh']", "$['éé']"])
def test_query_limit_counts_utf8_bytes(query: str) -> None:
    with pytest.raises(ValueError, match="maximum query bytes"):
        select_jsonpath({}, query, JsonPathLimits(max_query_bytes=8))


class _StringSubclass(str):
    pass


class _ListSubclass(list[object]):
    pass


@pytest.mark.parametrize(
    "value",
    [
        (),
        {1, 2},
        b"value",
        date(2026, 1, 1),
        object(),
        _StringSubclass("value"),
        _ListSubclass(),
        {1: "value"},
        {None: "value"},
        {_StringSubclass("value"): 1},
        float("inf"),
        float("nan"),
        "\ud800",
    ],
)
def test_non_json_types_are_rejected_before_launch(value: object) -> None:
    with pytest.raises(ValueError, match="JSONPath input"):
        select_jsonpath(value, "$", DEFAULT_JSONPATH_LIMITS)


def test_snapshot_preserves_json_scalars_and_escapes() -> None:
    value = {"é": [None, True, False, 17, -4, 1.5, "null", '\n\\"']}
    encoded = _jsonpath._snapshot(value, DEFAULT_JSONPATH_LIMITS)
    assert encoded == json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def test_repeated_aliases_are_expanded_and_counted() -> None:
    shared: list[object] = [1]
    value = [shared, shared]
    assert _jsonpath._snapshot(value, JsonPathLimits(max_input_nodes=5)) == b"[[1],[1]]"
    with pytest.raises(ValueError, match="maximum input nodes"):
        _jsonpath._snapshot(value, JsonPathLimits(max_input_nodes=4))


def test_cycles_are_rejected_but_shared_empty_containers_are_allowed() -> None:
    value: list[object] = []
    assert _jsonpath._snapshot([value, value], DEFAULT_JSONPATH_LIMITS) == b"[[],[]]"
    value.append(value)
    with pytest.raises(ValueError, match="cyclic aliases"):
        select_jsonpath(value, "$", DEFAULT_JSONPATH_LIMITS)
    mapping: dict[str, object] = {}
    mapping["self"] = mapping
    with pytest.raises(ValueError, match="cyclic aliases"):
        select_jsonpath(mapping, "$", DEFAULT_JSONPATH_LIMITS)


def test_input_depth_boundary_and_iterative_validation() -> None:
    assert _jsonpath._snapshot([[0]], JsonPathLimits(max_input_depth=2)) == b"[[0]]"
    with pytest.raises(ValueError, match="maximum input depth"):
        _jsonpath._snapshot([[0]], JsonPathLimits(max_input_depth=1))
    nested: object = 0
    for _ in range(1100):
        nested = [nested]
    assert len(_jsonpath._snapshot(nested, JsonPathLimits(max_input_depth=1100))) == 2201


@pytest.mark.parametrize("value", ["abcdef", "é", {"abcdefgh": 0}, 10**30, [0, 0, 0]])
def test_serialized_input_bytes_are_bounded(value: object) -> None:
    with pytest.raises(ValueError, match="maximum input bytes"):
        _jsonpath._snapshot(value, JsonPathLimits(max_input_bytes=4))


def test_input_byte_boundary_is_inclusive() -> None:
    assert _jsonpath._snapshot([1], JsonPathLimits(max_input_bytes=3)) == b"[1]"
    with pytest.raises(ValueError, match="maximum input bytes"):
        _jsonpath._snapshot([1], JsonPathLimits(max_input_bytes=2))


def _use_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> None:
    (tmp_path / "_jsonpath_worker.py").write_text(script, encoding="utf-8")

    def resources(_package: str) -> Path:
        return tmp_path

    monkeypatch.setattr(_jsonpath, "files", resources)


def test_subprocess_is_isolated_and_ignores_pythonpath(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "yaml_compose_test_injected.py").write_text("raise AssertionError('must not import')\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    _use_script(
        monkeypatch,
        tmp_path,
        "import importlib.util, json, sys\n"
        "json.dump([sys.flags.isolated, importlib.util.find_spec('yaml_compose_test_injected') is None], sys.stdout)\n",
    )
    assert select_jsonpath(None, "$", DEFAULT_JSONPATH_LIMITS) == [1, True]


def test_timeout_kills_and_reaps_real_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    marker = tmp_path / "pid"
    _use_script(
        monkeypatch,
        tmp_path,
        f"import os, pathlib, time\npathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n",
    )
    with pytest.raises(ValueError, match="execution timeout"):
        select_jsonpath(None, "$", JsonPathLimits(timeout_seconds=0.5))
    if sys.platform != "win32":
        pid = int(marker.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


def test_worker_start_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("launch failed")

    monkeypatch.setattr(_jsonpath.subprocess, "run", fail)
    with pytest.raises(ValueError, match="could not be started"):
        select_jsonpath(None, "$", DEFAULT_JSONPATH_LIMITS)


def test_runtime_timeout_conversion_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OverflowError("timeout exceeds the operating system wait range")

    monkeypatch.setattr(_jsonpath.subprocess, "run", fail)
    with pytest.raises(ValueError, match="timeout cannot be represented by this runtime"):
        select_jsonpath(None, "$", JsonPathLimits(timeout_seconds=1e100))


@pytest.mark.parametrize(
    ("script", "limits", "message"),
    [
        ("print('[0,0]')", JsonPathLimits(max_matches=1), "maximum matches"),
        ("print('[12345]')", JsonPathLimits(max_output_bytes=4), "maximum output bytes"),
        ("print('oops')", DEFAULT_JSONPATH_LIMITS, "invalid JSON"),
        ("print('[NaN]')", DEFAULT_JSONPATH_LIMITS, "invalid JSON"),
        ("print('{}')", DEFAULT_JSONPATH_LIMITS, "invalid result list"),
        ("raise SystemExit(7)", DEFAULT_JSONPATH_LIMITS, "exit status 7"),
        ("import sys; sys.stderr.write('failure'); sys.exit(1)", DEFAULT_JSONPATH_LIMITS, "failure"),
    ],
)
def test_parent_enforces_protocol_and_result_bounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
    limits: JsonPathLimits,
    message: str,
) -> None:
    _use_script(monkeypatch, tmp_path, script)
    with pytest.raises(ValueError, match=message):
        select_jsonpath(None, "$", limits)


def test_parent_protocol_carries_json_text_without_scalar_coercion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use_script(
        monkeypatch,
        tmp_path,
        "import json, sys\n"
        "header = json.loads(sys.stdin.buffer.readline())\n"
        "document = sys.stdin.buffer.read().decode('ascii')\n"
        "json.dump([header, document], sys.stdout)\n",
    )
    assert select_jsonpath("null", "$", DEFAULT_JSONPATH_LIMITS) == [
        {"query": "$", "max_matches": 10_000, "max_output_bytes": 8_388_608},
        '"null"',
    ]


@dataclass
class _FakeMatch:
    obj: object


def _fake_engine(monkeypatch: pytest.MonkeyPatch, results: list[object], seen: list[object]) -> None:
    class Compiled:
        def finditer(self, data: str) -> Iterator[_FakeMatch]:
            seen.append(data)
            return (_FakeMatch(value) for value in results)

    class Environment:
        def __init__(self, *, strict: bool) -> None:
            seen.append(strict)

        def compile(self, query: str) -> Compiled:
            seen.append(query)
            return Compiled()

    def import_module(name: str) -> ModuleType:
        seen.append(name)
        module = ModuleType(name)
        module.JSONPathEnvironment = Environment  # type: ignore[attr-defined]
        return module

    monkeypatch.setattr(_jsonpath_worker.importlib, "import_module", import_module)


def test_worker_calls_strict_engine_and_preserves_duplicate_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    _fake_engine(monkeypatch, ["null", "null", None], seen)
    assert _jsonpath_worker._evaluate("$", '"null"', 3, 20) == b'["null","null",null]'
    assert seen == ["jsonpath", "regex", "iregexp_check", True, "$", '"null"']


@pytest.mark.parametrize(
    ("results", "matches", "output", "message"),
    [([0, 0], 1, 20, "maximum matches"), (["abcdef"], 2, 5, "maximum output bytes"), ([], 2, 1, "output bytes")],
)
def test_worker_limits_before_retaining_complete_output(
    monkeypatch: pytest.MonkeyPatch, results: list[object], matches: int, output: int, message: str
) -> None:
    _fake_engine(monkeypatch, results, [])
    with pytest.raises(ValueError, match=message):
        _jsonpath_worker._evaluate("$", "null", matches, output)


@pytest.mark.parametrize("missing", ["jsonpath", "regex", "iregexp_check"])
def test_incomplete_installation_fails_without_regex_fallback(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    def import_module(name: str) -> ModuleType:
        if name == missing:
            raise ModuleNotFoundError(name)
        return ModuleType(name)

    monkeypatch.setattr(_jsonpath_worker.importlib, "import_module", import_module)
    with pytest.raises(ModuleNotFoundError, match=missing):
        _jsonpath_worker._evaluate("$", "null", 1, 10)


@pytest.mark.parametrize("invalid_header", [False, True])
def test_worker_main_protocol_handles_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, invalid_header: bool
) -> None:
    _fake_engine(monkeypatch, [None], [])
    request = b'{"query":"$","max_matches":2,"max_output_bytes":8}\nnull'
    if invalid_header:
        request = b"not JSON\nnull"
    source, output, errors = BytesIO(request), BytesIO(), BytesIO()
    monkeypatch.setattr(_jsonpath_worker.sys, "stdin", TextIOWrapper(source))
    monkeypatch.setattr(_jsonpath_worker.sys, "stdout", TextIOWrapper(output))
    monkeypatch.setattr(_jsonpath_worker.sys, "stderr", TextIOWrapper(errors))
    status = _jsonpath_worker._main()
    if invalid_header:
        assert status == 1
        assert b"JSONDecodeError" in errors.getvalue()
        assert output.getvalue() == b""
    else:
        assert status == 0
        assert errors.getvalue() == b""
        assert output.getvalue() == b"[null]"


def _require_engine() -> None:
    importlib.import_module("jsonpath")
    importlib.import_module("regex")
    importlib.import_module("iregexp_check")


@pytest.mark.parametrize(
    ("value", "query", "expected"),
    [
        ({"a": [1, 2, 3]}, "$.a[*]", [1, 2, 3]),
        ({"a": 1}, "$['a','a']", [1, 1]),
        ({"a": 1}, "$.missing", []),
        ([1, 2, 3], "$[-1]", [3]),
        ([1, 2, 3], "$[::-1]", [3, 2, 1]),
        ([{"a": 1}, {"nested": {"a": 2}}], "$..a", [1, 2]),
        ([{"n": 1}, {"n": 3}], "$[?@.n >= 2]", [{"n": 3}]),
        ([{"n": True}, {"n": 1}], "$[?@.n == true]", [{"n": True}]),
        ([{"a": [1]}, {"a": [1, 2]}], "$[?length(@.a) == 2]", [{"a": [1, 2]}]),
        ([{"a": [1]}, {"a": [1, 2]}], "$[?count(@.a[*]) == 1]", [{"a": [1]}]),
        ([{"a": [1]}, {"a": [1, 2]}], "$[?value(@.a[*]) == 1]", [{"a": [1]}]),
        (["abc", "zabc"], '$[?match(@, "a.*")]', ["abc"]),
        (["abc", "zabc", "xyz"], '$[?search(@, "ab")]', ["abc", "zabc"]),
        ({"é": {"a b": 2}}, "$['é']['a b']", [2]),
        (None, "$", [None]),
        (True, "$", [True]),
        (17, "$", [17]),
        ("null", "$", ["null"]),
        ('{"a":1}', "$", ['{"a":1}']),
    ],
)
def test_real_rfc9535_engine(value: object, query: str, expected: list[object]) -> None:
    _require_engine()
    assert select_jsonpath(value, query, DEFAULT_JSONPATH_LIMITS) == expected


@pytest.mark.parametrize("query", ["a", "$.a + 1", "$[?@.a =~ /x/]", "$[?unknown(@)]", "$["])
def test_real_engine_rejects_invalid_or_nonstandard_queries(query: str) -> None:
    _require_engine()
    with pytest.raises(ValueError, match="JSONPath worker failed"):
        select_jsonpath({"a": 1}, query, DEFAULT_JSONPATH_LIMITS)


def test_real_engine_returns_independent_snapshot() -> None:
    _require_engine()
    original: dict[str, object] = {"a": [1]}
    result = select_jsonpath(original, "$", DEFAULT_JSONPATH_LIMITS)
    assert result == [original]
    assert result[0] is not original
    original["a"] = [2]
    assert result == [{"a": [1]}]


def test_real_engine_applies_worker_result_limits() -> None:
    _require_engine()
    with pytest.raises(ValueError, match="maximum matches"):
        select_jsonpath([1, 2], "$[*]", JsonPathLimits(max_matches=1))
    with pytest.raises(ValueError, match="maximum output bytes"):
        select_jsonpath("abcdef", "$", JsonPathLimits(max_output_bytes=5))
