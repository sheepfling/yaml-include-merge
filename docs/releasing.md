# Releasing yaml-compose

The first planned version is **0.1.0**. Preparing a release does not publish it.
The package version comes from Git tags through `setuptools-scm`; a clean checkout
tagged `v0.1.0` builds version `0.1.0`. Do not replace this with a second version constant.

## Before the first release

1. Review and squash-merge the release preparation PR.
2. Complete the planned history cleanup on `main` before making the repository public.
   Current files and package metadata credit `sheepfling`; old commits, author fields,
   PR diffs, and cached views may still disclose earlier personal attribution. A squash
   merge alone does not remove existing history. Audit the remaining refs after rewriting.
3. Verify that the PyPI account can publish the project name `yaml-compose`. An absent
   project page does not guarantee that the name is available.
4. Configure a [PyPI pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   for a new project, or add a publisher to the existing project, using these values:

   | Setting           | Value                |
   | ----------------- | -------------------- |
   | PyPI project      | `yaml-compose`       |
   | GitHub owner      | `sheepfling`         |
   | GitHub repository | `yaml-include-merge` |
   | Workflow file     | `release.yml`        |
   | Environment       | `pypi`               |

5. Configure the repository's `pypi` environment and any desired release reviewers.
   The repository name is independent of the package name; update the publisher and
   project URLs if the repository is renamed.

Use the maintainer handle in new commits, copyright notices, and package metadata.
The distribution credits `sheepfling` without a personal email address.

## Verify the release candidate

From a clean checkout, install the development extras on Python 3.11 or newer and run:

```shell
python -m pip install -e ".[dev]"
python -m scripts.ci
```

Confirm the full hosted CI matrix is green for the commit being released: Python
3.10–3.15 on Linux and Windows, plus the repository quality job. Review the
[changelog](../CHANGELOG.md), remove the release's `unreleased` label, and commit that
change before creating the version tag.

## Publish v0.1.0

Create the `v0.1.0` tag on the reviewed, clean commit on `main`, then publish a GitHub
release for that tag with the changelog notes. Publishing that GitHub release starts
`.github/workflows/release.yml`. Creating a draft release or pushing a branch does not
upload a package to PyPI.

The release workflow runs the complete CI matrix again, builds the wheel and source
archive, checks MIT metadata and license contents, verifies the `py.typed` marker and
tag/version agreement, runs strict Twine validation, and smoke-tests the installed wheel
with Python's isolated mode. It uploads those exact files
as a workflow artifact, then publishes them from the separate `pypi` environment job.
Only that job requests the identity token required for
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
No long-lived PyPI token is needed. GitHub releases marked as prereleases are skipped.

For a manual inspection of artifacts built from the tag:

```shell
python -m build --outdir dist/0.1.0
python -m scripts.check_artifacts --dist-dir dist/0.1.0 --release-tag v0.1.0
python -m twine check --strict dist/0.1.0/*
```

Use a fresh output directory so old distributions cannot enter an upload. Do not override
`SETUPTOOLS_SCM_PRETEND_VERSION` when building release artifacts; that would bypass
deriving the version from the tag. If a release job fails, inspect its logs before retrying;
PyPI does not allow replacing an uploaded file with a different file of the same name.
