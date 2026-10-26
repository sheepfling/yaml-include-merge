"""Keep caller-selected PyYAML loaders and duplicate-key policy together."""

from __future__ import annotations

from collections.abc import Hashable
from typing import Protocol, TypeVar, cast

from yaml import FullLoader, Loader, MappingNode, Node, SafeLoader, ScalarNode, UnsafeLoader
from yaml.constructor import ConstructorError

from .tags import (
    DELETE_TAG,
    INCLUDE_TAG,
    MERGE_KEY,
    MERGE_TAG,
    REPLACE_TAG,
    SPREAD_TAG,
)


class _ObjectConstructor(Protocol):
    def construct_object(self, node: Node, deep: bool = False) -> object: ...


class _LoaderRegistries(Protocol):
    yaml_constructors: dict[Hashable, object]
    yaml_multi_constructors: dict[Hashable, object]
    yaml_implicit_resolvers: dict[Hashable, object]
    yaml_path_resolvers: dict[Hashable, object]


class UniqueKeySafeLoader(SafeLoader):
    """Reject duplicate explicit keys while retaining SafeLoader constructors."""

    @staticmethod
    def _is_merge_key(node: Node) -> bool:
        return isinstance(node, ScalarNode) and node.tag == MERGE_TAG and node.value == MERGE_KEY

    # opaque-boundary: PyYAML mappings can contain arbitrary hashable keys and values.
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, object]:
        """Construct a mapping after checking its explicit keys."""
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            if self._is_merge_key(key_node):
                continue
            construct = cast(_ObjectConstructor, self).construct_object
            key = construct(key_node, False)
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found unhashable key {key!r}",
                    key_node.start_mark,
                ) from exc
            if key in seen:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def validate_yaml_loader(yaml_loader: object) -> None:
    """Require a supported PyYAML loader class or one of its subclasses."""
    supported_loaders = (SafeLoader, FullLoader, Loader, UnsafeLoader)
    if not isinstance(yaml_loader, type) or not issubclass(yaml_loader, supported_loaders):
        raise TypeError(
            "yaml_loader must be a class derived from a supported PyYAML loader "
            "(yaml.SafeLoader, yaml.FullLoader, yaml.Loader, or yaml.UnsafeLoader)"
        )


def ordinary_loader(yaml_loader: type[object]) -> type[object]:
    """Layer duplicate-key checking over a caller's PyYAML loader class."""
    validate_yaml_loader(yaml_loader)
    if yaml_loader is SafeLoader:
        return UniqueKeySafeLoader
    loader_class = cast(_LoaderRegistries, cast(object, yaml_loader))
    return cast(
        type[object],
        type(
            "UniqueKeyConfiguredLoader",
            (UniqueKeySafeLoader, yaml_loader),
            {
                "yaml_constructors": dict(loader_class.yaml_constructors),
                "yaml_multi_constructors": dict(loader_class.yaml_multi_constructors),
                "yaml_implicit_resolvers": dict(loader_class.yaml_implicit_resolvers),
                "yaml_path_resolvers": dict(loader_class.yaml_path_resolvers),
            },
        ),
    )


_TLoader = TypeVar("_TLoader", bound=SafeLoader)


def configured_include_loader(yaml_loader: type[object], adapter: type[_TLoader]) -> type[_TLoader]:
    """Give the include adapter a caller's constructors without changing global loaders."""
    validate_yaml_loader(yaml_loader)
    if yaml_loader is SafeLoader:
        return adapter
    loader_class = cast(_LoaderRegistries, cast(object, yaml_loader))
    constructors = dict(loader_class.yaml_constructors)
    for tag in (INCLUDE_TAG, DELETE_TAG, REPLACE_TAG, SPREAD_TAG):
        constructors[tag] = adapter.yaml_constructors[tag]
    return cast(
        type[_TLoader],
        type(
            "ConfiguredIncludeLoader",
            (adapter, yaml_loader),
            {
                "yaml_constructors": constructors,
                "yaml_multi_constructors": dict(loader_class.yaml_multi_constructors),
                "yaml_implicit_resolvers": dict(loader_class.yaml_implicit_resolvers),
                "yaml_path_resolvers": dict(loader_class.yaml_path_resolvers),
            },
        ),
    )
