# yaml-include-merge

A small PyYAML adapter for ordinary safe YAML loading, bounded file includes,
and deterministic configuration overlays. It reuses PyYAML's SafeLoader; it
does not implement a YAML parser.

## Use it in a normal Python configuration workflow

Install the package in the project's environment, then pass a configuration
path through the dispatcher:

    python -m pip install .

    from pathlib import Path

    from yaml_include_merge import load_yaml_file

    config = load_yaml_file(Path("config/production.yaml"))

Documents without the package header are loaded with a duplicate-key-checking
subclass of PyYAML's SafeLoader. Python-specific object tags are not enabled.
The existing APIs remain available when a caller already owns a stream:

    from yaml_include_merge import load_yaml_dispatch, load_yaml_include

    config = load_yaml_dispatch(stream)  # ordinary safe YAML or header dispatch
    config = load_yaml_include(stream, search_paths=[Path("config")])  # explicit

An in-memory stream that uses !include must supply search_paths or
allowed_roots; the current working directory is never an implicit include
root.

## Include and overlay syntax

    %LOADER LoadInclude
    ---
    <<: !include "base.yaml"
    service:
      port: 8081
      token: !delete

%LOADER LoadInclude is this package's opt-in header. The adapter removes it
before passing the remaining document to PyYAML. !include reads a YAML file
relative to the including file first, then checks the configured search paths.
Nested include files use the same adapter.

Each mapping may contain one <<: !include "file.yaml" overlay. The included
mapping is the base; keys in the current document take precedence:

- Mapping values merge recursively.
- Sequences replace the earlier sequence as a whole.
- null is an explicit value and replaces an earlier value.
- All non-mapping overlay values replace the earlier value; this also defines
  the behavior when types differ.
- !delete removes a key from the included mapping. It is only valid inside an
  overlay.
- Duplicate explicit keys in a document are errors.

Here <<: !include is a package-level configuration operation. YAML anchors and
aliases refer to nodes within a YAML document; PyYAML's legacy <<: *anchor
behavior remains part of its ordinary safe-loader path. Neither mechanism loads
a file. Git conflict merging is a separate operation on text and repository
history.

## Filesystem and error behavior

Includes must be relative paths. By default, the allowed root is the directory
containing the top-level file plus any configured search paths. The resolved
target must stay within an allowed root, including after symlinks are resolved.
The loader also limits include depth, number of included files, size per file,
and total included bytes. It never fetches network URLs.

PyYAML syntax and constructor errors retain source line and column marks.
Include errors point to the !include site and include the failed path or policy.
The returned values are ordinary Python structures and do not retain per-key
origin metadata. The standard PyYAML object model does not preserve comments or
formatting; use a round-trip YAML library such as ruamel.yaml when those are
required.

## Run the example and tests

With the declared PyYAML dependency installed in the active environment:

    python examples/load_config.py
    python -m unittest discover -s tests -v

## Attribution

The supplied starter identifies Patrick Berry as the author of the original
loader component. No license file or license grant was present in the supplied
archive, so this repository does not add license terms.
