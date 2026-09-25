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


class VisualRules(_ConfigFile):
    rules: list[str]


def _load(model: type[C], workspace: Path, name: str) -> C:
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
    return _load(StyleConfig, workspace, "style.yaml")


def load_mascot(workspace: Path) -> MascotConfig:
    return _load(MascotConfig, workspace, "mascot.yaml")


def load_visual_rules(workspace: Path) -> VisualRules:
    return _load(VisualRules, workspace, "visual_rules.yaml")
