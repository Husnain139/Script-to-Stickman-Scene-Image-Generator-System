"""Image files: format sniffing, PNG files with their version record inside, and names (spec §3, §9.2)."""

from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, PngImagePlugin

HISTORY_DIR = "images/_history"
META_KEY = "stickman"  # the PNG text chunk that holds the version record (spec §5.2 [M3])
_HISTORY_NAME = re.compile(r"^(?P<unit>\d{3}[ab]?)_v(?P<v>[1-9]\d*)\.png$")


class ImageDecodeError(ValueError):
    """The API's bytes aren't a JPEG, PNG or WebP image that can be read."""


def sniff(data: bytes) -> str | None:
    """The image format, from its magic bytes (spec §9.2)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def decode_image(data: bytes) -> Image.Image:
    kind = sniff(data)
    if kind is None:
        raise ImageDecodeError(f"not a JPEG, PNG or WebP image (it starts with {data[:12]!r})")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (OSError, SyntaxError, ValueError) as exc:
        raise ImageDecodeError(f"can't read the {kind} image: {exc}") from exc
    return image


def encode_png(image: Image.Image, metadata: dict[str, Any]) -> bytes:
    """PNG bytes with `metadata` as JSON in a compressed text chunk, so an image saved just before a
    run was killed can be added back to state.json (spec §5.2 [M3])."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    info = PngImagePlugin.PngInfo()
    info.add_text(META_KEY, json.dumps(metadata, sort_keys=True), zip=True)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


def read_metadata(path: Path) -> dict[str, Any] | None:
    """The metadata encode_png put in the file, or None when it has none or can't be read."""
    try:
        with Image.open(path) as image:
            text = getattr(image, "text", {}).get(META_KEY)
    except (OSError, SyntaxError, ValueError):
        return None
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def image_stem(unit_id: str, start: float) -> str:
    """`<unit>_<MM-SS.s>`: the start rounded half up to 0.1 s, like 006a_00-21.0 (spec §3)."""
    tenths = math.floor(start * 10 + 0.5)
    minutes, rest = divmod(tenths, 600)
    return f"{unit_id}_{minutes:02d}-{rest // 10:02d}.{rest % 10}"


def history_name(unit_id: str, version: int) -> str:
    return f"{unit_id}_v{version}.png"


def history_files(project_dir: Path) -> dict[str, dict[int, Path]]:
    """The images/_history/<unit>_v<N>.png files, by unit and version number."""
    folder = project_dir / HISTORY_DIR
    found: dict[str, dict[int, Path]] = {}
    if folder.is_dir():
        for path in folder.iterdir():
            match = _HISTORY_NAME.match(path.name)
            if match:
                found.setdefault(match["unit"], {})[int(match["v"])] = path
    return found
