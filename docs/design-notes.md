# Composition semantics

`yaml-compose` adds file includes, recursive mapping overlays, and sequence insertion
while keeping YAML's data model and PyYAML's existing merge precedence. These notes
explain how those choices fit together; the [README](../README.md) and
[worked examples](examples.md) show the complete syntax.

## Start with YAML's rules

The [YAML data model](https://yaml.org/spec/1.2.2/#chapter-3-processes-and-models)
provides the constraints we build on:

- A mapping associates unique keys with values. Its declaration order does not define
  override priority. Duplicate explicit keys are rejected.
- A sequence is ordered. Inserting items therefore has a meaningful position.
- An anchor names a node; an alias refers to an earlier anchored node in the same
  document. Neither imports another file.
- Tags let applications assign meaning to nodes. YAML does not define file inclusion,
  recursive overlays, deletion, or spreading.
- `null` is a value. Assigning it keeps the key, so removing a key needs a separate operation.

Parsing and composition are separate concerns. PyYAML still reads scalars and constructs
Python values. Its default scalar rules remain in effect, including boolean values such
as `yes` and `on`; this package does not switch the parser to the YAML 1.2 core schema.

## Preserve the merge-key precedence

PyYAML supports the YAML 1.1-era [merge-key working draft](https://yaml.org/type/merge.html).
The key `<<` inserts mapping entries that the receiving mapping does not already define.
When its source is a sequence of mappings, earlier sources win over later sources.
Local explicit keys win over every source, wherever `<<` appears in the mapping.

This is compatibility behavior: [YAML 1.2 removed the special merge key](https://yaml.org/spec/1.2.2/ext/changes/).
We retain PyYAML's shallow `<<` behavior and give `<<<` exactly the same precedence,
with recursive combination of mapping values as the extension.

```yaml
%LOADER YamlCompose
---
defaults: &defaults {service: {host: localhost, port: 8080}}
specific: &specific {service: {port: 9000}}

shallow:
  <<: [*specific, *defaults]

deep:
  <<<: [*specific, *defaults]
```

`shallow.service` is `{port: 9000}`: the first source supplies that whole value.
`deep.service` is `{host: localhost, port: 9000}`: the nested mappings combine,
with the first source still winning the conflicting `port`.
An explicit local `service: {port: 9443}` would win in either case; only `<<<`
would retain the inherited `host`.

A merge source sequence is a priority list. Moving keys around in the receiving mapping
never creates a sequence of patches. To layer configurations, let an environment file
inherit its defaults and put that environment's overrides in its local mapping.

## Add the missing composition operations

| Need                            | Syntax                        | Result                                                 |
| ------------------------------- | ----------------------------- | ------------------------------------------------------ |
| Reuse a value from another file | `!include path.yaml`          | Load that file's completed value at this node.         |
| Combine nested mappings         | `<<<: [*specific, *defaults]` | Apply merge-key priority recursively.                  |
| Insert sequence items           | `- !spread [*items]`          | Insert one source sequence at this position.           |
| Remove an inherited key         | `key: !delete`                | Omit that key from the resulting mapping.              |
| Replace an inherited mapping    | `key: !replace {port: 9443}`  | Use this whole mapping instead of merging its entries. |

Both merge keys accept anchors and included mappings. A local sequence, scalar, or `null`
replaces an inherited value; lists are not concatenated implicitly. Mapping values
combine recursively only under `<<<`, and `!replace` provides an explicit escape from
that recursion. `!delete` takes no value, and deleting an absent key is harmless.

A spread inserts one level of sequence items in order, preserving duplicates and nested
lists. For example, `!spread [!include steps.yaml]` uses a file instead of an anchor.
YAML does not allow a tag on an alias node itself, so the one-item wrapper supplies a
node to tag and one source to unpack. The same wrapper lets `!replace` take an anchored
or included mapping: `!replace [*replacement]` or `!replace [!include replacement.yaml]`.

Delete and replace instructions apply to the mapping overlay in which they are written.
Each file finishes its own composition before another file receives its result. The
result carries ordinary values, with no deferred instructions for a later merge.
The [operator examples](../examples/README.md) cover those boundaries and invalid forms.

## Keep lookup separate from value precedence

Include lookup decides which source supplies a value. Mapping precedence decides how
values combine after loading; changing one does not change the other.

Nested relative references follow the file containing the reference. Bare names can
prefer the local directory or configured search paths; explicit `./` and `../` paths
stay relative to their containing file. There is no implicit working-directory fallback.
Allowed roots, cycle checks, and configurable resource budgets bound those reads.
The same composition rules apply with an application-supplied resolver.

## Return ordinary data

Composition is enabled by the package header `%LOADER YamlCompose` or by explicitly
selecting the composition API. Ordinary YAML keeps the ordinary loader path.
`SafeLoader` is the default; applications can select another trusted PyYAML loader.
The result is a Python value, without comments, source formatting, or per-key provenance.

`!include` dispatches registered document adapters from the resolved source's suffix.
Built-ins cover JSON-family formats, TOML, INI, and dotenv; unknown suffixes keep the
existing YAML parsing behavior. CSV uses the explicit `!include-table` tag so its row-list
or header-record shape is visible. TOML date/time values remain Python `date`, `time`, or
`datetime` objects. `!include-text` returns strict UTF-8 text without parsing, while
`!include-raw` returns exact source bytes. All include forms share the resolver, allowed roots,
cycle detection, stream ownership, and resource budgets.
