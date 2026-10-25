# Releasing

Use the same provider-neutral release pattern as
[mdrepo](https://github.com/sheepfling/mdrepo/blob/main/scripts/release.py): derive the
version from Git tags, build the distributions, validate them with Twine, then upload.

## Validate the candidate

Update the [changelog](../CHANGELOG.md), install the development dependencies, and run
all checks before tagging the reviewed commit:

```shell
python -m pip install -e ".[dev]"
python -m scripts.ci
```

Confirm the supported-Python CI matrix is green. Create a `vX.Y.Z` tag on the release
commit; `setuptools-scm` supplies the package version from that tag.

## Build the tagged version

Check out the tag and reinstall the project so its installed metadata reflects that
checkout. Set `RELEASE_TAG` to the tag being released, then run:

```shell
python -m pip install -e ".[dev]"
python -m scripts.release build
```

For PowerShell, set the variable with `$env:RELEASE_TAG = "vX.Y.Z"`; for a POSIX shell,
use `export RELEASE_TAG=vX.Y.Z`.

The helper checks that the tag matches the installed version, replaces prior wheel and
source-archive files in `dist/`, builds a fresh pair, and runs strict Twine validation.
It passes that resolved version to the build backend, as mdrepo does. Use `--output`
to select a different distribution directory.

## Publish

Upload the validated files with the usual packaging tools:

```shell
python -m twine upload dist/*
```

The GitHub release workflow calls the same helper after CI, smoke-tests the installed
wheel, and publishes the built files using the `pypi` environment. Configure its
[PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
for `release.yml` before publishing a GitHub release. A
[pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
can be used for a new PyPI project. The helper also works outside GitHub Actions.
