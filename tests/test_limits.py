from __future__ import annotations

from dataclasses import FrozenInstanceError
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
import yaml

from yaml_compose import (
    DEFAULT_INCLUDE_LIMITS,
    UNSET,
    IncludeLimits,
    MemoryIncludeResolver,
    MemoryRoot,
    MemorySource,
    UnsetType,
    YamlConfigLoader,
    load_yaml_compose,
    load_yaml_dispatch,
    load_yaml_file,
)


@pytest.fixture
def included_config(test_path: Path) -> Path:
    (test_path / "base.yaml").write_text("value: 1\n", encoding="utf-8")
    config = test_path / "main.yaml"
    config.write_text("%LOADER YamlCompose\n---\n<<<: !include ./base.yaml\n", encoding="utf-8")
    return config


def load_profile(
    entry: str,
    config: Path,
    profile: IncludeLimits,
    files: int | UnsetType | None = UNSET,
) -> object:
    if entry == "file":
        return load_yaml_file(config, include_limits=profile, max_include_files=files)
    text = config.read_text(encoding="utf-8")
    if entry == "dispatch":
        return load_yaml_dispatch(
            StringIO(text),
            source_path=config,
            include_limits=profile,
            max_include_files=files,
        )
    if entry == "compose":
        return load_yaml_compose(
            StringIO(text),
            source_path=config,
            include_limits=profile,
            max_include_files=files,
        )
    loader = YamlConfigLoader(include_limits=profile, max_include_files=files)
    if entry == "facade-file":
        return loader.load_file(config)
    if entry == "facade-stream":
        return loader.load_stream(StringIO(text), source_path=config)
    return loader.load_include(StringIO(text), source_path=config)


def test_default_profile_is_bounded_inspectable_and_immutable() -> None:
    assert (
        IncludeLimits(
            max_include_depth=32,
            max_include_files=1024,
            max_file_bytes=8_388_608,
            max_total_include_bytes=67_108_864,
        )
        == DEFAULT_INCLUDE_LIMITS
    )
    assert YamlConfigLoader().include_limits is DEFAULT_INCLUDE_LIMITS
    with pytest.raises(FrozenInstanceError):
        DEFAULT_INCLUDE_LIMITS.__setattr__("max_include_files", 1)


@pytest.mark.parametrize(
    "entry", ["file", "dispatch", "compose", "facade-file", "facade-stream", "facade-include"]
)
def test_profiles_apply_to_every_entry_point_and_individual_overrides_win(
    included_config: Path,
    entry: str,
) -> None:
    profile = IncludeLimits(max_include_files=0)
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        load_profile(entry, included_config, profile)
    assert load_profile(entry, included_config, profile, files=1) == {"value": 1}
    assert load_profile(entry, included_config, profile, files=None) == {"value": 1}
    assert profile.max_include_files == 0


def test_loader_effective_profile_tracks_individual_overrides(included_config: Path) -> None:
    profile = IncludeLimits(max_include_files=0)
    loader = YamlConfigLoader(include_limits=profile, max_include_files=1)
    assert loader.include_limits.max_include_files == 1
    loader.max_include_files = 0
    assert loader.include_limits.max_include_files == 0
    with pytest.raises(yaml.YAMLError, match="maximum include count"):
        loader.load_file(included_config)
    loader.max_include_files = 1
    assert loader.load_file(included_config) == {"value": 1}
    assert profile.max_include_files == 0


@pytest.mark.parametrize("invalid", [None, object(), {}])
def test_invalid_profile_types_are_rejected(included_config: Path, invalid: object) -> None:
    profile = cast(IncludeLimits, invalid)
    with pytest.raises(TypeError, match="IncludeLimits instance"):
        YamlConfigLoader(include_limits=profile)
    with pytest.raises(TypeError, match="IncludeLimits instance"):
        load_yaml_file(included_config, include_limits=profile)


def test_zero_profiles_remain_valid_and_enforced(included_config: Path) -> None:
    profile = IncludeLimits(max_include_depth=0)
    assert profile.max_include_depth == 0
    with pytest.raises(yaml.YAMLError, match=r"maximum include depth \(0\)"):
        load_yaml_file(included_config, include_limits=profile)


def test_shared_profile_has_no_per_load_counter_state() -> None:
    root = MemoryRoot("shared-limit-test")
    profile = IncludeLimits(max_include_files=1)
    resolver = MemoryIncludeResolver(root, {"base.yaml": "value: 1\n"})
    source = MemorySource(root, PurePosixPath("main.yaml"))
    for _ in range(2):
        loader = YamlConfigLoader(
            include_resolver=resolver, allowed_roots=[root], include_limits=profile
        )
        for _ in range(2):
            assert loader.load_include(StringIO("!include ./base.yaml\n"), source=source) == {
                "value": 1
            }
        assert loader.include_limits is profile
    assert profile.max_include_files == 1


def test_default_file_budget_accepts_large_fanout_and_still_enforces_its_bound() -> None:
    root = MemoryRoot("default-fanout")
    loader = YamlConfigLoader(
        include_resolver=MemoryIncludeResolver(root, {"shared.yaml": "value: 1\n"}),
        allowed_roots=[root],
    )
    source = MemorySource(root, PurePosixPath("main.yaml"))
    document = "items:\n" + "  - !include ./shared.yaml\n" * 1024
    assert loader.load_include(StringIO(document), source=source) == {
        "items": [{"value": 1}] * 1024
    }
    with pytest.raises(yaml.YAMLError, match=r"maximum include count \(1024\)"):
        loader.load_include(StringIO(document + "  - !include ./shared.yaml\n"), source=source)


def test_default_depth_accepts_32_levels_and_rejects_the_next() -> None:
    root = MemoryRoot("default-depth")
    source = MemorySource(root, PurePosixPath("main.yaml"))

    def load(depth: int) -> object:
        files = {
            f"level-{i}.yaml": f"child: !include ./level-{i + 1}.yaml\n" for i in range(depth - 1)
        }
        files[f"level-{depth - 1}.yaml"] = "value: done\n"
        return YamlConfigLoader(
            include_resolver=MemoryIncludeResolver(root, files), allowed_roots=[root]
        ).load_include(StringIO("child: !include ./level-0.yaml\n"), source=source)

    value = load(32)
    for _ in range(32):
        assert isinstance(value, dict)
        value = cast(dict[str, object], value)["child"]
    assert value == {"value": "done"}
    with pytest.raises(yaml.YAMLError, match=r"maximum include depth \(32\)"):
        load(33)


def test_default_byte_budget_accepts_a_multi_megabyte_include() -> None:
    root = MemoryRoot("default-bytes")
    payload = "x" * (2 * 1024 * 1024)
    loader = YamlConfigLoader(
        include_resolver=MemoryIncludeResolver(root, {"large.yaml": f"payload: {payload}\n"}),
        allowed_roots=[root],
    )
    assert loader.load_include(
        StringIO("!include ./large.yaml\n"), source=MemorySource(root, PurePosixPath("main.yaml"))
    ) == {"payload": payload}
