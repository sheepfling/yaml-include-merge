"""Compare local and shared sources without changing merge or spread semantics."""

from pathlib import Path

from yaml_compose import IncludeSearch, YamlConfigLoader


def load_example(include_search: IncludeSearch = "local-first") -> object:
    """Load nested includes using a selected search policy and one pinned file."""
    examples = Path(__file__).parent / "search"
    loader = YamlConfigLoader(search_paths=[examples / "shared"], include_search=include_search)
    return loader.load_file(examples / "app" / "main.yaml")


if __name__ == "__main__":
    print("local-first:", load_example())
    print("search-first:", load_example("search-first"))
