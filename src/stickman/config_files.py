"""style.yaml, mascot.yaml and visual_rules.yaml (spec §7.1–7.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from stickman.settings import ConfigError, default_config_text, read_config_text

C = TypeVar("C", bound=BaseModel)


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1


class StyleConfig(_ConfigFile):
    style_version: int = Field(ge=1)
    style_text: str = Field(min_length=1)
    strict_clause: str = Field(min_length=1)
    negative_prompt: str = ""


class MascotConfig(_ConfigFile):
    id: Literal["mascot"] = "mascot"
    name: str = Field(min_length=1)
    figures: int = Field(1, ge=1)
    identity: str = Field(min_length=1)
    default_outfit: str = ""
    sheet: str
    ref: str
    seed: int | None = None
    model: str | None = None
    style_version: int = Field(ge=1)

    @property
    def description(self) -> str:
        """What the image prompt says about the mascot (spec §7.4)."""
        if not self.default_outfit:
            return self.identity
        return (
            f"{self.identity}, usually wearing {self.default_outfit} "
            "(these may be hidden or left out when the scene calls for it)"
        )


# Text-free wording for props that tend to come out with text (spec §7.5 `text`). A text retry adds
# it to the matching prop. Older visual_rules.yaml files without `text_free` get this table.
TEXT_FREE: dict[str, str] = {
    "clock": "a round face with two hands and no numerals",
    "watch": "a round face with two hands and no numerals",
    "calendar": "a grid of empty squares",
    "book": "blank pages or a few wavy lines",
    "notebook": "blank pages or a few wavy lines",
    "paper": "blank, or a few wavy lines",
    "newspaper": "columns of wavy lines, no letters",
    "letter": "a blank folded sheet",
    "sign": "a blank board with simple shapes, no writing",
    "poster": "simple shapes only, no writing",
    "map": "simple shapes, no names",
    "screen": "blank, or simple shapes",
    "computer": "a blank screen, or simple shapes",
    "laptop": "a blank screen, or simple shapes",
    "phone": "a blank screen",
    "chart": "bars or an arrow line without labels",
    "graph": "bars or an arrow line without labels",
    "money": "coins or bills without numbers or symbols",
    "coin": "plain, without numbers or symbols",
}


class VisualRules(_ConfigFile):
    rules: list[str]
    text_free: dict[str, str] = Field(default_factory=lambda: dict(TEXT_FREE))


def load_config_file(model: type[C], workspace: Path, name: str) -> C:
    """config/<name> validated as `model`; the packaged default when the file is missing."""
    path = workspace / "config" / name
    text = read_config_text(path) if path.exists() else default_config_text(name)
    try:
        data = YAML(typ="safe").load(text) or {}
    except YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"{path}: {details}") from exc


def load_style(workspace: Path) -> StyleConfig:
    return load_config_file(StyleConfig, workspace, "style.yaml")


def load_mascot(workspace: Path) -> MascotConfig:
    return load_config_file(MascotConfig, workspace, "mascot.yaml")


def load_visual_rules(workspace: Path) -> VisualRules:
    return load_config_file(VisualRules, workspace, "visual_rules.yaml")
