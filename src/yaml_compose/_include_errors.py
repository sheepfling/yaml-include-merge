"""Shared marked include errors and route provenance, independent of loaders."""

from __future__ import annotations

from typing import NamedTuple, NoReturn, cast

import yaml
from yaml import Node
from yaml.constructor import ConstructorError
from yaml.error import MarkedYAMLError

from .tags import INCLUDE_TAG


class IncludeRouteFrame(NamedTuple):
    """One YAML include site on the route to a failed included source.

    Lines and columns are one-based. ``resolved_source`` is ``None`` when
    resolution failed before a canonical source identity was available.
    """

    including_source: str
    line: int
    column: int
    tag: str
    requested_path: str | None
    resolved_source: str | None


# Preserve the public import/pickle address despite the internal extraction.
IncludeRouteFrame.__module__ = "yaml_compose.loader"


def render_include_route(route: tuple[IncludeRouteFrame, ...]) -> str:
    """Render a route once, from the root document toward the failure."""
    lines = ["Include route (outermost first):"]
    for frame in route:
        requested = repr(frame.requested_path) if frame.requested_path is not None else "<invalid path>"
        destination = f" -> {frame.resolved_source}" if frame.resolved_source is not None else ""
        lines.append(f"  {frame.including_source}:{frame.line}:{frame.column} {frame.tag} {requested}{destination}")
    return "\n".join(lines)


def is_route_prefix(
    prefix: tuple[IncludeRouteFrame, ...],
    route: tuple[IncludeRouteFrame, ...],
) -> bool:
    return len(prefix) <= len(route) and route[: len(prefix)] == prefix


def get_include_route(error: BaseException) -> tuple[IncludeRouteFrame, ...]:
    """Return the typed route attached to an include-related YAML error.

    Non-include errors and errors raised by custom YAML exception subclasses
    that cannot store attributes return an empty tuple; their rendered message
    still includes the route when available.
    """
    route_value: object = getattr(error, "include_route", ())
    route_candidates = cast(tuple[object, ...], route_value) if isinstance(route_value, tuple) else ()
    if all(isinstance(frame, IncludeRouteFrame) for frame in route_candidates):
        return cast(tuple[IncludeRouteFrame, ...], route_candidates)
    return ()


def attach_include_route(
    error: yaml.YAMLError,
    route: tuple[IncludeRouteFrame, ...],
) -> None:
    """Attach structured route data without replacing the original YAML error."""
    if not route:
        return

    previous = get_include_route(error)
    if previous:
        if is_route_prefix(previous, route):
            selected = route
        elif is_route_prefix(route, previous):
            selected = previous
        elif len(previous) == len(route) and all(
            (old.including_source, old.line, old.column, old.tag, old.requested_path)
            == (new.including_source, new.line, new.column, new.tag, new.requested_path)
            for old, new in zip(previous, route, strict=True)
        ):
            selected = tuple(
                old if old.resolved_source is not None else new for old, new in zip(previous, route, strict=True)
            )
        else:
            selected = route
    else:
        selected = route

    if selected == previous:
        return

    error_state_value: object = getattr(error, "__dict__", None)
    error_state = cast(dict[str, object], error_state_value) if isinstance(error_state_value, dict) else None
    base_message: object = error_state.get("_yaml_compose_base_message") if error_state is not None else None
    if not isinstance(base_message, str):
        if isinstance(error, MarkedYAMLError):
            base_message = error.problem or error.context or str(error)
        else:
            base_message = str(error)
        if error_state is not None:
            error_state["_yaml_compose_base_message"] = base_message
    if error_state is None and "Include route (outermost first):" in base_message:
        return

    rendered = f"{base_message}\n{render_include_route(selected)}"
    if isinstance(error, MarkedYAMLError):
        # Keep the original PyYAML class and marks so existing exception
        # handlers and source-location inspection continue to work.
        error.problem = rendered
    else:
        error.args = (rendered,)
    if error_state is not None:
        error_state["include_route"] = selected


def include_error(
    node: Node,
    message: str,
    cause: Exception | None = None,
    *,
    tag: str = INCLUDE_TAG,
) -> NoReturn:
    """Raise a marked constructor error, retaining its original cause."""
    error = ConstructorError(
        f"while resolving {tag}",
        None,
        message,
        node.start_mark,
    )
    if cause is not None:
        raise error from cause
    raise error
