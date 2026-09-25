import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_pillow_is_a_runtime_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(dep.lower().startswith("pillow") for dep in project["dependencies"])
