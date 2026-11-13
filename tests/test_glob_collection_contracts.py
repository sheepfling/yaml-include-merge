"""Glob helpers accept the weakest safe read-only collection contracts."""
# Direct unit coverage of the private boundary discussed in the PR review.
# pyright: reportPrivateUsage=false

from pathlib import Path

import pytest

from yaml_compose._glob import _confined, _matches_at


def test_confined_accepts_a_one_pass_root_iterable(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    roots = (root for root in [tmp_path])
    assert _confined(child, roots) == child.resolve()


def test_matches_accepts_lists_and_reuses_roots_for_all_candidates(tmp_path: Path) -> None:
    for name in ("b.yaml", "a.yaml", "other.txt"):
        (tmp_path / name).write_text("value: 1", encoding="utf-8")
    steps: list[None] = []
    result = _matches_at(tmp_path, ["*.yaml"], [tmp_path], lambda: steps.append(None), None)
    assert result == [tmp_path / "a.yaml", tmp_path / "b.yaml"]
    result.append(tmp_path / "consumer-added.yaml")
    assert len(result) == 3
    assert len(steps) > 1


def test_iterable_contract_does_not_relax_confinement(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    with pytest.raises(ValueError, match="outside the allowed roots"):
        _confined(tmp_path, iter([root]))
