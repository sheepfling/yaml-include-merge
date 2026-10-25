"""Safe YAML loading with file includes and mapping overlays."""

from .loader import (
    IncludeLoader,
    YamlConfigLoader,
    YamlIncludeError,
    load_yaml_dispatch,
    load_yaml_file,
    load_yaml_include,
)
from .limits import DEFAULT_INCLUDE_LIMITS, IncludeLimits
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

__all__ = (
    "IncludeLoader",
    "IncludeLimits",
    "DEFAULT_INCLUDE_LIMITS",
    "YamlConfigLoader",
    "YamlIncludeError",
    "load_yaml_dispatch",
    "load_yaml_file",
    "load_yaml_include",
    "FileRoot",
    "FileSource",
    "FileSystemIncludeResolver",
    "IncludeRequest",
    "IncludeResolver",
    "IncludeRoot",
    "IncludeSource",
    "MemoryIncludeResolver",
    "MemoryRoot",
    "MemorySource",
    "ResolvedInclude",
)
