import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def test_pillow_is_a_runtime_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(dep.lower().startswith("pillow") for dep in project["dependencies"])


def test_numpy_is_a_runtime_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(dep.lower().startswith("numpy") for dep in project["dependencies"])


@pytest.mark.parametrize("name", ["fastapi", "uvicorn", "watchfiles"])
def test_the_review_server_dependencies_are_runtime_dependencies(name):
    import tomllib
    from pathlib import Path

    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(dep.split(">")[0].split("=")[0].strip() == name for dep in data["project"]["dependencies"])
