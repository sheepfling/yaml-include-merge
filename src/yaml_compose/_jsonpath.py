"""Bounded JSON snapshots and subprocess execution for JSONPath support."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import as_file, files
from io import BytesIO
from tempfile import TemporaryFile
from typing import cast


@dataclass(frozen=True, slots=True)
class JsonPathLimits:
    """Positive, finite bounds for each JSONPath selection.

    Input nodes count values, including the root and each expanded alias,
    but not object member names. Root depth is zero. Byte limits count ASCII
    JSON serialization, including escaping and punctuation; the query limit
    counts UTF-8 bytes. Output bytes include the enclosing result array.

    IncludeLimits bounds source reads and include traversal. These separate
    limits bound the parsed query input and generated matches: a small source
    can expand aliases or produce many repeated matches. They do not replace
    the source limits.

    The timeout is a subprocess execution deadline, including imports and
    compilation. Input validation runs before that deadline. These bounds
    are resource controls, not a hard memory guarantee or an OS sandbox.
    """

    max_query_bytes: int = 16_384
    max_input_bytes: int = 8_388_608
    max_input_nodes: int = 100_000
    max_input_depth: int = 128
    max_matches: int = 10_000
    max_output_bytes: int = 8_388_608
    timeout_seconds: float = 2.0

    @classmethod
    def generous(cls) -> JsonPathLimits:
        """Return finite enlarged query budgets for trusted, larger inputs.

        Allow 64 KiB queries, 64 MiB input/output, one million input nodes,
        depth 256, 100000 matches, and a ten-second worker deadline.
        This changes neither defaults nor the absence of a memory sandbox.
        """
        return cls(
            max_query_bytes=64 * 1024,
            max_input_bytes=64 * 1024 * 1024,
            max_input_nodes=1_000_000,
            max_input_depth=256,
            max_matches=100_000,
            max_output_bytes=64 * 1024 * 1024,
            timeout_seconds=10.0,
        )

    def __post_init__(self) -> None:
        """Reject disabled, non-finite, boolean, and incorrectly typed limits."""
        for name in (
            "max_query_bytes",
            "max_input_bytes",
            "max_input_nodes",
            "max_input_depth",
            "max_matches",
            "max_output_bytes",
        ):
            value: object = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"JSONPath {name} must be a positive integer")
        timeout = self.timeout_seconds
        if type(timeout) not in (int, float):
            raise ValueError("JSONPath timeout_seconds must be a positive finite number")
        try:
            representable_timeout = float(timeout)
        except OverflowError as exc:
            raise ValueError("JSONPath timeout_seconds must be a positive finite number") from exc
        if not 0 < representable_timeout < math.inf:
            raise ValueError("JSONPath timeout_seconds must be a positive finite number")


DEFAULT_JSONPATH_LIMITS = JsonPathLimits()


@dataclass(slots=True)
class _Frame:
    """Track one container without copying its children onto a work stack."""

    children: Iterator[tuple[str | None, object]]
    identity: int
    depth: int
    closing: bytes
    first: bool = True


def _append(output: BytesIO, data: bytes, maximum: int) -> None:
    """Append only data that fits the serialized input budget."""
    if output.tell() + len(data) > maximum:
        raise ValueError(f"JSONPath maximum input bytes ({maximum}) exceeded")
    output.write(data)


def _scalar(value: object, maximum: int) -> bytes:
    """Encode an already type-checked scalar without coercing Python objects."""
    if type(value) is str:
        string = value
        if len(string) > maximum:
            raise ValueError(f"JSONPath maximum input bytes ({maximum}) exceeded")
        try:
            string.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("JSONPath input strings must contain valid Unicode scalar values") from exc
    if type(value) is int and value.bit_length() > maximum * 4:
        raise ValueError(f"JSONPath maximum input bytes ({maximum}) exceeded")
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    except (ValueError, OverflowError) as exc:
        raise ValueError("JSONPath input contains a non-finite or unrepresentable JSON number") from exc


def _snapshot(value: object, limits: JsonPathLimits) -> bytes:
    """Validate and serialize a JSON tree iteratively, expanding bounded aliases."""
    output = BytesIO()
    stack: list[_Frame] = []
    active: set[int] = set()
    nodes = 0
    depth = 0
    current = value
    while True:
        nodes += 1
        if nodes > limits.max_input_nodes:
            raise ValueError(f"JSONPath maximum input nodes ({limits.max_input_nodes}) exceeded")
        if depth > limits.max_input_depth:
            raise ValueError(f"JSONPath maximum input depth ({limits.max_input_depth}) exceeded")
        kind = type(current)
        if kind in (dict, list):
            identity = id(current)
            if identity in active:
                raise ValueError("JSONPath input must not contain cyclic aliases")
            active.add(identity)
            if kind is dict:
                mapping = cast(dict[object, object], current)
                children = cast(Iterator[tuple[str | None, object]], iter(mapping.items()))
                opening, closing = b"{", b"}"
            else:
                sequence = cast(list[object], current)
                children = ((None, item) for item in sequence)
                opening, closing = b"[", b"]"
            stack.append(_Frame(children, identity, depth, closing))
            _append(output, opening, limits.max_input_bytes)
        elif current is None or kind in (str, bool, int, float):
            _append(output, _scalar(current, limits.max_input_bytes), limits.max_input_bytes)
        else:
            raise ValueError(f"JSONPath input must use JSON types; found {kind.__name__}")

        while stack:
            frame = stack[-1]
            try:
                key, current = next(frame.children)
            except StopIteration:
                stack.pop()
                active.remove(frame.identity)
                _append(output, frame.closing, limits.max_input_bytes)
                continue
            if not frame.first:
                _append(output, b",", limits.max_input_bytes)
            frame.first = False
            if frame.closing == b"}":
                if type(key) is not str:
                    raise ValueError("JSONPath input object member names must be strings")
                _append(output, _scalar(key, limits.max_input_bytes), limits.max_input_bytes)
                _append(output, b":", limits.max_input_bytes)
            depth = frame.depth + 1
            break
        else:
            return output.getvalue()


def _reject_constant(value: str) -> object:
    """Reject non-JSON constants accepted by Python's permissive decoder."""
    raise ValueError(f"non-JSON number {value}")


def select_jsonpath(value: object, query: str, limits: JsonPathLimits) -> list[object]:
    """Return an RFC 9535 nodelist as an independent JSON snapshot.

    Duplicate matches and their order are preserved. Only exact builtin JSON
    types are accepted; tuples, YAML sets, timestamps, non-string object keys,
    non-finite numbers, custom subclasses, and cyclic aliases are rejected.
    The required ``python-jsonpath[strict]`` engine executes in an isolated
    subprocess. Missing dependencies, invalid queries, and exceeded limits
    raise ``ValueError``. No application-supplied query is evaluated as Python.
    """
    if type(limits) is not JsonPathLimits:
        raise TypeError("jsonpath_limits must be a JsonPathLimits instance")
    if type(query) is not str or not query:
        raise ValueError("JSONPath query must be a non-empty string")
    if len(query) > limits.max_query_bytes:
        raise ValueError(f"JSONPath maximum query bytes ({limits.max_query_bytes}) exceeded")
    try:
        query_bytes = query.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("JSONPath query must be valid UTF-8") from exc
    if len(query_bytes) > limits.max_query_bytes:
        raise ValueError(f"JSONPath maximum query bytes ({limits.max_query_bytes}) exceeded")
    document = _snapshot(value, limits)
    header = json.dumps(
        {"query": query, "max_matches": limits.max_matches, "max_output_bytes": limits.max_output_bytes},
        ensure_ascii=True,
    ).encode("ascii")
    worker = files("yaml_compose").joinpath("_jsonpath_worker.py")
    # Temporary files keep subprocess.run from accumulating unbounded pipes in
    # parent memory. The worker also checks output size while serializing it.
    with as_file(worker) as path, TemporaryFile() as source, TemporaryFile() as output, TemporaryFile() as errors:
        source.write(header + b"\n")
        source.write(document)
        source.seek(0)
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(path)],
                stdin=source,
                stdout=output,
                stderr=errors,
                timeout=limits.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run kills and reaps the worker before raising.
            raise ValueError(f"JSONPath execution timeout ({limits.timeout_seconds} seconds) exceeded") from exc
        except OverflowError as exc:
            raise ValueError("JSONPath execution timeout cannot be represented by this runtime") from exc
        except OSError as exc:
            raise ValueError("JSONPath worker could not be started") from exc
        if completed.returncode != 0:
            errors.seek(0)
            detail = errors.read(2048).decode("utf-8", errors="replace").strip()
            raise ValueError(f"JSONPath worker failed: {detail or f'exit status {completed.returncode}'}")
        output.seek(0)
        encoded = output.read(limits.max_output_bytes + 1)
    if len(encoded) > limits.max_output_bytes:
        raise ValueError(f"JSONPath maximum output bytes ({limits.max_output_bytes}) exceeded")
    try:
        result: object = json.loads(encoded, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError("JSONPath worker returned invalid JSON") from exc
    if not isinstance(result, list):
        raise ValueError("JSONPath worker returned an invalid result list")
    matches = cast(list[object], result)
    if len(matches) > limits.max_matches:
        raise ValueError(f"JSONPath maximum matches ({limits.max_matches}) exceeded")
    return matches
