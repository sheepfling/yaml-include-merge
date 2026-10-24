# yaml-include-merge

A small PyYAML adapter for ordinary safe YAML loading, bounded file includes,
and deterministic configuration overlays. It reuses PyYAML's SafeLoader; it
does not implement a YAML parser.

## Quick start

Install the package in the project's environment, then pass an ordinary YAML
file to `load_yaml_file`:

    python -m pip install .

    from pathlib import Path

    from yaml_include_merge import load_yaml_file

    config = load_yaml_file(Path("config.yaml"))

The same call also dispatches files that opt into includes with
`%LOADER LoadInclude`. See the [worked examples](docs/examples.md) for the
base, intermediate layer, production overlay, resolved value, and in-memory
resolver.

Documents without the package header are loaded with a duplicate-key-checking
subclass of PyYAML's SafeLoader. Python-specific object tags are not enabled.
The existing APIs remain available when a caller already owns a stream:

    from yaml_include_merge import load_yaml_dispatch, load_yaml_include

    config = load_yaml_dispatch(stream)  # ordinary safe YAML or header dispatch
    config = load_yaml_include(stream, search_paths=[Path("config")])  # explicit

For an application that loads several files with the same policy, configure
once and reuse the facade. Each call gets a fresh recursion stack and resource
budget:

    from yaml_include_merge import YamlConfigLoader

    loader = YamlConfigLoader(search_paths=[Path("config/shared")])
    development = loader.load_file("config/development.yaml")
    production = loader.load_file("config/production.yaml")

A stream using filesystem includes must have a `source_path` or explicit
`search_paths`/`allowed_roots`; the current working directory is never an
implicit root. A virtual source instead supplies a typed `MemorySource` and
`MemoryRoot` to a `MemoryIncludeResolver`.

## Supplying an include resolver

The default resolver reads local files and keeps the root and symlink checks.
To replace its lookup policy, pass an object implementing the small
`IncludeResolver.resolve(IncludeRequest) -> ResolvedInclude` protocol:

    loader = YamlConfigLoader(
        include_resolver=my_local_resolver,
        allowed_roots=[Path("config")],
    )
    config = loader.load_file("config/production.yaml")

`ResolvedInclude` carries a stable source identity and a deferred binary-stream
opener. The loader checks the identity against the configured roots before
opening it, reads only within the byte budgets, and closes the stream on success
and errors. It still parses with SafeLoader and applies the same overlay rules.
The resolver is caller-supplied trusted code: it must honor the request roots
and must not access the network. The built-in `MemoryIncludeResolver` is a
small local fixture/data-map implementation; see
`examples/in_memory_config.py` for a runnable example.

`yaml.safe_load()` remains untouched and is still suitable for ordinary YAML
that does not use this package's include feature. The package's own ordinary
dispatch uses a SafeLoader subclass that additionally rejects duplicate keys.

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

The built-in include limits are package-selected starting bounds, not values
defined by the YAML specification or derived from measured application
workloads. The defaults are 16 nested include levels, 64 included files, 1 MiB
per included file, and 8 MiB total included content. They bound include data;
the caller controls the top-level input stream. Tune them for the size and
shape of the application's configuration using the `max_*` arguments on the
loader facade or loading functions.

## Run the example and tests

With the declared PyYAML dependency installed in the active environment:

    python examples/load_config.py
    python examples/in_memory_config.py
    python -m unittest discover -s tests -v

## License status

The supplied archive contained no license file or license grant. This
repository does not add license terms.
