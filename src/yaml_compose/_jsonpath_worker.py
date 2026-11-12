"""Private standalone worker, launched by absolute filename in isolated Python.

Keep this module independent of yaml_compose imports: the parent locates its
packaged resource, while ``python -I`` deliberately ignores the working
directory and PYTHONPATH. The optional engine must be installed in the active
interpreter's environment.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from io import BytesIO
from typing import Protocol, cast


class _Match(Protocol):
    """The documented match value exposed by python-jsonpath."""

    obj: object


class _CompiledQuery(Protocol):
    """The documented compiled query interface used by the worker."""

    def finditer(self, data: str) -> Iterator[_Match]:
        """Evaluate against a serialized JSON document."""
        ...


class _Environment(Protocol):
    """The strict engine's compilation interface."""

    def compile(self, query: str) -> _CompiledQuery:
        """Parse and type-check an RFC 9535 query."""
        ...


class _EnvironmentFactory(Protocol):
    """The engine constructor's documented keyword argument."""

    def __call__(self, *, strict: bool) -> _Environment:
        """Create an environment with RFC conformance enabled."""
        ...


def _evaluate(query: str, document: str, max_matches: int, max_output_bytes: int) -> bytes:
    """Evaluate a query and serialize its complete, size-limited nodelist."""
    try:
        engine = importlib.import_module("jsonpath")
        # The strict extra supplies RFC 9485 regex validation and matching.
        # Do not silently fall back to Python re's different syntax.
        importlib.import_module("regex")
        importlib.import_module("iregexp_check")
    except ImportError as exc:
        raise ValueError("JSONPath requires the optional dependency; install 'yaml-compose[jsonpath]'") from exc
    factory = cast(_EnvironmentFactory, engine.JSONPathEnvironment)
    compiled = factory(strict=True).compile(query)
    output = BytesIO()
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def append(chunk: bytes) -> None:
        """Enforce the budget before retaining another output chunk."""
        if output.tell() + len(chunk) > max_output_bytes:
            raise ValueError(f"maximum output bytes ({max_output_bytes}) exceeded")
        output.write(chunk)

    append(b"[")
    # Pass JSON text, not a decoded scalar string: the engine interprets str
    # input as JSON data and would otherwise decode scalar values a second time.
    for index, match in enumerate(compiled.finditer(document)):
        if index >= max_matches:
            raise ValueError(f"maximum matches ({max_matches}) exceeded")
        if index:
            append(b",")
        for chunk in encoder.iterencode(match.obj):
            append(chunk.encode("ascii"))
    append(b"]")
    return output.getvalue()


def _main() -> int:
    """Read the parent's private JSON protocol and write one result or error."""
    try:
        header = cast(dict[str, object], json.loads(sys.stdin.buffer.readline()))
        result = _evaluate(
            cast(str, header["query"]),
            sys.stdin.buffer.read().decode("ascii"),
            cast(int, header["max_matches"]),
            cast(int, header["max_output_bytes"]),
        )
        sys.stdout.buffer.write(result)
    except Exception as exc:  # The subprocess boundary must convert backend failures.
        message = f"{type(exc).__name__}: {str(exc)[:512]}\n"
        sys.stderr.buffer.write(message.encode("utf-8", errors="replace"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
