"""Symbolic names for yaml-compose YAML tags and header values.

Use these constants when generating YAML documents programmatically so the
application uses the same selectors as the loader.
"""

LOADER_DIRECTIVE = "LOADER"
"""YAML header directive name used for loader selection."""

COMPOSE_LOADER_TAG = "YamlCompose"
"""Value of ``%LOADER`` that opts a document into yaml-compose behavior."""

INCLUDE_TAG = "!include"
"""Tag that loads a structured YAML or registered-format document."""

INCLUDE_MANY_TAG = "!include-many"
"""Tag that collects one result per explicit source or filesystem glob match."""

INCLUDE_ALL_TAG = "!include-all"
"""Tag that loads a YAML stream as a list of all its documents."""

INCLUDE_TABLE_TAG = "!include-table"
"""Tag that loads a CSV source as ordered rows of strings."""

INCLUDE_RAW_TAG = "!include-raw"
"""Tag that includes the exact bounded bytes from a source."""

INCLUDE_TEXT_TAG = "!include-text"
"""Tag that includes a UTF-8 file as an unparsed string."""

JSONPATH_TAG = "!jsonpath"
"""Tag that selects a list of JSONPath matches from one structured value."""

DELETE_TAG = "!delete"
"""Tag that removes an inherited mapping key."""

REPLACE_TAG = "!replace"
"""Tag that replaces a mapping value during a deep merge."""

SPREAD_TAG = "!spread"
"""Tag that inserts a single sequence source at its position in a list."""

MERGE_KEY = "<<"
"""Mapping key for a shallow merge from an anchor or included file."""

DEEP_MERGE_KEY = "<<<"
"""Mapping key for a recursive merge from an anchor or included file."""

MERGE_TAG = "tag:yaml.org,2002:merge"
"""Canonical YAML tag assigned to the ``<<`` merge key."""

__all__ = (
    "COMPOSE_LOADER_TAG",
    "DEEP_MERGE_KEY",
    "DELETE_TAG",
    "INCLUDE_ALL_TAG",
    "INCLUDE_MANY_TAG",
    "INCLUDE_RAW_TAG",
    "INCLUDE_TABLE_TAG",
    "INCLUDE_TAG",
    "INCLUDE_TEXT_TAG",
    "JSONPATH_TAG",
    "LOADER_DIRECTIVE",
    "MERGE_KEY",
    "MERGE_TAG",
    "REPLACE_TAG",
    "SPREAD_TAG",
)
