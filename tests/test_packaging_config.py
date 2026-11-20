"""The sdist and wheel take only the files they name.

Hatch reads ``include`` entries as gitignore-style patterns: an entry without
a leading ``/`` also matches the same name in any subdirectory (for example a
local virtualenv's ``LICENSE`` files), so every entry is anchored at the root.
"""

from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _config():
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_sdist_includes_only_anchored_top_level_entries():
    include = _config()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert sorted(include) == sorted(
        ["/src/lexapi", "/tests", "/README.md", "/CHANGELOG.md", "/LICENSE", "/pyproject.toml"]
    )


def test_license_files_name_only_the_root_licence():
    assert _config()["project"]["license-files"] == ["LICENSE"]


def test_wheel_packages_only_the_library():
    assert _config()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/lexapi"]
