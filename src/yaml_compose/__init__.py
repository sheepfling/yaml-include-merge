"""Safe YAML loading with file includes and mapping overlays."""

from ._search import IncludeSearch
from ._unset import UNSET, UnsetType
from .limits import (
    DEFAULT_INCLUDE_LIMITS,
    IncludeLimits,
    get_default_include_limits,
    set_default_include_limits,
)
from .loader import (
    IncludeLoader,
    YamlConfigLoader,
    YamlIncludeError,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)
from .resolver import (
    FileRoot,
    FileSource,
    FileSystemIncludeResolver,
    IncludeRequest,
    IncludeResolver,
    IncludeRoot,
    IncludeSource,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    ResolvedInclude,
)
from .tags import (
    COMPOSE_LOADER_TAG,
    DEEP_MERGE_KEY,
    DELETE_TAG,
    INCLUDE_TAG,
    LOADER_DIRECTIVE,
    MERGE_KEY,
    MERGE_TAG,
    REPLACE_TAG,
    SPREAD_TAG,
)

__all__ = (
    "COMPOSE_LOADER_TAG",
    "DEEP_MERGE_KEY",
    "DEFAULT_INCLUDE_LIMITS",
    "DELETE_TAG",
    "INCLUDE_TAG",
    "LOADER_DIRECTIVE",
    "MERGE_KEY",
    "MERGE_TAG",
    "REPLACE_TAG",
    "SPREAD_TAG",
    "UNSET",
    "FileRoot",
    "FileSource",
    "FileSystemIncludeResolver",
    "IncludeLimits",
    "IncludeLoader",
    "IncludeRequest",
    "IncludeResolver",
    "IncludeRoot",
    "IncludeSearch",
    "IncludeSource",
    "MemoryIncludeResolver",
    "MemoryRoot",
    "MemorySource",
    "ResolvedInclude",
    "UnsetType",
    "YamlConfigLoader",
    "YamlIncludeError",
    "get_default_include_limits",
    "load_yaml_compose",
    "load_yaml_dispatch",
    "load_yaml_file",
    "set_default_include_limits",
)
