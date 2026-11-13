"""Keep the runnable selection showcase and its documented result synchronized."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from examples.include_selection import load_example

EXPECTED: dict[str, object] = {
    "pointer": "api",
    "names": ["api", "worker"],
    "no_matches": [],
    "settings": [{"name": "base"}, {"name": "local"}, {"name": "east"}, {"name": "west"}],
    "one_document": [{"settings": {"name": "base"}}],
    "second_document": "finished",
    "recent_documents": [{"event": "finished"}],
    "whole_stream": [[{"event": "started"}, {"event": "finished"}]],
    "per_file_streams": [[{"event": "finished"}], [{"event": "finished"}]],
    "text_from_second": "second\nthird\n",
    "text_through_second": "first\nsecond\n",
    "raw_after_two": b"CDEF",
    "raw_first_two": b"AB",
    "table": [["name", "count"], ["Ada", "02"]],
}


def test_selection_example_exact_output() -> None:
    assert load_example() == EXPECTED


def test_selection_runner_works_outside_checkout(repo_root: Path, tmp_path: Path) -> None:
    environment = dict(os.environ, PYTHONPATH=str(repo_root / "src"))
    result = subprocess.run(
        [sys.executable, str(repo_root / "examples" / "include_selection.py")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert ast.literal_eval(result.stdout) == EXPECTED


def test_selection_documented_output_matches(repo_root: Path) -> None:
    guide = (repo_root / "docs" / "examples.md").read_text(encoding="utf-8")
    section = guide.split("## Select values with object options\n", 1)[1].split("\n## ", 1)[0]
    output = section.split("```python\n", 1)[1].split("\n```", 1)[0]
    assert ast.literal_eval(output) == EXPECTED
