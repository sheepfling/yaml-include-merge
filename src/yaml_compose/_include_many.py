"""Collect explicit source results without implicit flattening or resolver fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from yaml import Node

from ._glob import expand_include_glob
from ._include_specs import IncludeSpec, parse_many_include_spec
from .resolver import FileSource, FileSystemIncludeResolver, IncludeRequest, ResolvedInclude, validate_include_name
from .tags import INCLUDE_TAG

if TYPE_CHECKING:
    from .loader import IncludeLoader


def construct_include_many(loader: IncludeLoader, node: Node) -> list[object]:
    """Return one completed selected value per source in deterministic order."""
    # Imported at construction time to keep the loader/constructor dependency acyclic.
    from .loader import _attach_include_route, _include_error  # pyright: ignore[reportPrivateUsage]

    frame = loader._route_frame(node, tag=INCLUDE_TAG)  # pyright: ignore[reportPrivateUsage]
    route = (*loader.include_route, frame)
    try:
        spec = parse_many_include_spec(node)
        # Validate all names and resolver capabilities before any source is opened.
        for source in spec.sources:
            frame = loader._route_frame(source.node, tag=INCLUDE_TAG)  # pyright: ignore[reportPrivateUsage]
            route = (*loader.include_route, frame)
            validate_include_name(source.path, allow_absolute=loader.allow_absolute_includes)
            if source.glob and type(loader.include_resolver) is not FileSystemIncludeResolver:
                raise ValueError("include globs require the standard filesystem resolver; no fallback is performed")

        def step() -> None:
            loader.budget.glob_steps += 1
            maximum = loader.budget.max_glob_steps
            if maximum is not None and loader.budget.glob_steps > maximum:
                raise ValueError(f"maximum glob traversal steps ({maximum}) exceeded")

        results: list[object] = []
        for source in spec.sources:
            frame = loader._route_frame(source.node, tag=INCLUDE_TAG)  # pyright: ignore[reportPrivateUsage]
            route = (*loader.include_route, frame)
            resolved_sources: tuple[ResolvedInclude | None, ...]
            if source.glob:
                remaining = (
                    None
                    if loader.budget.max_include_files is None
                    else max(0, loader.budget.max_include_files - loader.budget.include_files)
                )
                matches = expand_include_glob(
                    IncludeRequest(
                        name=source.path,
                        including=loader.source,
                        search_paths=loader.search_paths,
                        allowed_roots=loader.allowed_roots,
                        include_search=loader.include_search,
                    ),
                    step=step,
                    max_matches=remaining,
                )
                files: list[ResolvedInclude] = []
                for path in matches:
                    canonical = path.resolve(strict=True)
                    files.append(ResolvedInclude(FileSource(canonical), lambda path=canonical: path.open("rb")))
                resolved_sources = tuple(files)
            else:
                resolved_sources = (None,)
            for resolved in resolved_sources:
                value = loader._include(  # pyright: ignore[reportPrivateUsage]
                    source.node,
                    tag_override=INCLUDE_TAG,
                    provided_spec=IncludeSpec(
                        source.path,
                        json_pointer=spec.json_pointer,
                        json_path=spec.json_path,
                        all_documents=spec.all_documents,
                    ),
                    resolved_override=resolved,
                )
                results.append(value)
        frame = loader._route_frame(node, tag=INCLUDE_TAG)  # pyright: ignore[reportPrivateUsage]
        loader._include_routes_by_node[node] = (  # pyright: ignore[reportPrivateUsage]
            *loader.include_route,
            frame._replace(requested_path="<multiple sources>"),
        )
        return results
    except yaml.YAMLError:
        # Each individual include already has its exact source route.
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        try:
            _include_error(node, str(exc), exc, tag=INCLUDE_TAG)
        except yaml.YAMLError as error:
            _attach_include_route(error, route)
            raise
