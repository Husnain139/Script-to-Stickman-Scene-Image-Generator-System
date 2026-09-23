"""M0 live checks: answers spec §15 against the real Cloudflare API (about $1 in total).

Run one step at a time from the workspace root:
    uv run python scripts/m0_probe.py ids
    uv run python scripts/m0_probe.py llm
    uv run python scripts/m0_probe.py image
    uv run python scripts/m0_probe.py refs
    uv run python scripts/m0_probe.py vision [--model ID]
    uv run python scripts/m0_probe.py errors
    uv run python scripts/m0_probe.py style
    uv run python scripts/m0_probe.py anchor-leak
Raw responses -> tests/fixtures/cf/<step>_<name>.json (long strings truncated).
Images -> m0_out/<step>/<name>.png
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from ruamel.yaml import YAML

from stickman.settings import load_config

ROOT = Path.cwd()
FIXTURES = ROOT / "tests" / "fixtures" / "cf"
OUT = ROOT / "m0_out"
BASE = "https://api.cloudflare.com/client/v4"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_9B = "@cf/black-forest-labs/flux-2-klein-9b"
DEV = "@cf/black-forest-labs/flux-2-dev"
MESSAGES = [
    {"role": "system", "content": "Return only JSON."},
    {"role": "user", "content": "Give one animal and its number of legs."},
]
SCHEMA = {
    "name": "probe",
    "schema": {
        "type": "object",
        "properties": {"animal": {"type": "string"}, "legs": {"type": "integer"}},
        "required": ["animal", "legs"],
    },
}
STYLE_SCENES = {
    "fire_night": (
        "A group of three cavemen stickmen in simple fur loincloths sit around a campfire at night, "
        "telling stories. Flame lines around the fire and short lines radiating outward. A small "
        "crescent moon and a few stars; the background stays white. Wide shot."
    ),
    "clock_3am": (
        "The main character, a stickman with a large round head and exactly three short hair strokes "
        "curling to the right, lies awake in bed under a simple blanket, eyes wide open, staring at a "
        "round wall clock that has two hands and no numerals. A small crescent moon in the window. "
        "Medium shot."
    ),
    "old_firmware": (
        "Visual metaphor: a stickman whose chest is an old boxy computer screen showing a loading bar, "
        "standing in a modern room under a switched-on ceiling lamp. Surprise lines above his head. "
        "Medium shot."
    ),
}
SINGLE_FIGURE_SCENES = [
    "one stickman jogging",
    "one stickman reading a book with blank pages",
    "one stickman sleeping under a blanket",
    "one stickman pointing at the sky",
    "one stickman sitting on a rock, thinking",
]


def truncate(value):
    if isinstance(value, dict):
        return {key: truncate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [truncate(item) for item in value]
    if isinstance(value, str) and len(value) > 200:
        return value[:60] + f"...<{len(value)} chars>"
    return value


def record(step: str, name: str, response: httpx.Response, elapsed: float) -> None:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        body = {"binary_image_bytes": len(response.content)}
    else:
        try:
            body = truncate(response.json())
        except ValueError:
            body = response.text[:2000]
    headers = {k: v for k, v in response.headers.items() if k.lower() != "set-cookie"}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{step}_{name}.json").write_text(
        json.dumps(
            {"status": response.status_code, "elapsed_s": round(elapsed, 2), "headers": headers, "body": body},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[{step}/{name}] HTTP {response.status_code} in {elapsed:.1f}s  {content_type}")
    if response.status_code >= 400:
        print("    ", response.text[:300])


def extract_image(response: httpx.Response) -> bytes | None:
    if response.status_code >= 400:
        return None
    if response.headers.get("content-type", "").startswith("image/"):
        return response.content
    data = response.json()
    result = data.get("result") if isinstance(data, dict) else None
    encoded = (result.get("image") if isinstance(result, dict) else None) or data.get("image")
    return base64.b64decode(encoded) if encoded else None


def test_png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.3, height * 0.1, width * 0.7, height * 0.5), outline="black", width=4)
    draw.line((width * 0.5, height * 0.5, width * 0.5, height * 0.9), fill="black", width=4)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def fit(png: bytes, max_side: int) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class Probe:
    def __init__(self) -> None:
        cfg = load_config(ROOT)
        self.settings = cfg.settings
        self.account = cfg.secrets.cf_account_id
        token = cfg.secrets.cf_api_token.get_secret_value()
        self.http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=300)
        style = YAML(typ="safe").load((ROOT / "config" / "style.yaml").read_text(encoding="utf-8"))
        self.style_text = style["style_text"]
        self.strict = style["strict_clause"]

    async def call(self, step, name, method, path, **kwargs) -> httpx.Response:
        started = time.perf_counter()
        response = await self.http.request(method, f"{BASE}/accounts/{self.account}{path}", **kwargs)
        record(step, name, response, time.perf_counter() - started)
        return response

    async def image(self, step, name, model, prompt, width, height, *, seed=1, refs=(), extra=None):
        fields = [
            ("prompt", (None, prompt)),
            ("width", (None, str(width))),
            ("height", (None, str(height))),
            ("seed", (None, str(seed))),
        ]
        for key, value in (extra or {}).items():
            fields.append((key, (None, str(value))))
        for index, ref in enumerate(refs):
            fields.append((f"input_image_{index}", (f"input_image_{index}.png", ref, "image/png")))
        response = await self.call(step, name, "POST", f"/ai/run/{model}", files=fields)
        data = extract_image(response)
        if data:
            image = Image.open(io.BytesIO(data))
            print(f"     returned {image.format} {image.size[0]}x{image.size[1]}")
            path = OUT / step / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "PNG")
        return data

    def scene_prompt(self, scene: str) -> str:
        return f"{self.style_text}\n\nScene: {scene}\n\n{self.strict}"


async def step_ids(p: Probe) -> None:
    for term in ("gpt-oss", "qwen", "llama-3.3", "flux-2"):
        response = await p.call("ids", term.replace(".", "_"), "GET", "/ai/models/search", params={"search": term})
        if response.status_code == 200:
            for model in response.json().get("result", []):
                print("    ", model.get("name"), "|", (model.get("task") or {}).get("name"))


async def step_llm(p: Probe) -> None:
    for label, model in (("planner", p.settings.llm.planner_model), ("fallback", p.settings.llm.fallback_model)):
        chat = {"model": model, "messages": MESSAGES, "max_tokens": 300}
        await p.call("llm", f"{label}_chat", "POST", "/ai/v1/chat/completions", json=chat)
        with_schema = {**chat, "response_format": {"type": "json_schema", "json_schema": SCHEMA}}
        await p.call("llm", f"{label}_chat_schema", "POST", "/ai/v1/chat/completions", json=with_schema)
        await p.call("llm", f"{label}_native", "POST", f"/ai/run/{model}", json={"messages": MESSAGES, "max_tokens": 300})


async def step_image(p: Probe) -> None:
    prompt = p.scene_prompt("one stickman waving hello.")
    await p.image("image", "klein4b_1920x1080", KLEIN_4B, prompt, 1920, 1080)
    await p.image("image", "klein4b_1080x1920", KLEIN_4B, prompt, 1080, 1920)
    await p.image("image", "klein4b_1920x1088", KLEIN_4B, prompt, 1920, 1088)
    await p.image("image", "klein4b_with_steps", KLEIN_4B, prompt, 1024, 768, extra={"steps": 4})
    await p.image("image", "klein9b_1920x1080", KLEIN_9B, prompt, 1920, 1080)
    await p.image("image", "dev_1920x1080", DEV, prompt, 1920, 1080, extra={"steps": 25})
    await p.image("image", "klein4b_same_seed_repeat", KLEIN_4B, prompt, 1920, 1080)
    first, again = OUT / "image" / "klein4b_1920x1080.png", OUT / "image" / "klein4b_same_seed_repeat.png"
    if first.exists() and again.exists():
        same = Image.open(first).tobytes() == Image.open(again).tobytes()
        print(f"     same seed reproduces identical pixels: {same}")


async def step_refs(p: Probe) -> None:
    prompt = p.scene_prompt("the character from image 0, waving.")
    await p.image("refs", "one_ref_512", KLEIN_4B, prompt, 1024, 768, refs=[test_png(512, 512)])
    await p.image("refs", "one_ref_513", KLEIN_4B, prompt, 1024, 768, refs=[test_png(513, 513)])
    await p.image("refs", "four_refs_512", KLEIN_4B, prompt, 1024, 768, refs=[test_png(512, 512)] * 4)
    await p.image("refs", "five_refs_512", KLEIN_4B, prompt, 1024, 768, refs=[test_png(512, 512)] * 5)


async def step_vision(p: Probe, model: str) -> None:
    first = OUT / "image" / "klein4b_1920x1080.png"
    second = OUT / "refs" / "one_ref_512.png"
    if not first.exists():
        sys.exit("Run the image step first (it creates m0_out/image/klein4b_1920x1080.png).")
    second = second if second.exists() else first

    def part(path: Path) -> dict:
        encoded = base64.b64encode(fit(path.read_bytes(), 768)).decode()
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}

    question = {
        "type": "text",
        "text": 'How many stick figures are in each image, and is there any text? Answer as JSON {"counts": [], "has_text": []}.',
    }
    for name, content in (("one_image", [question, part(first)]), ("two_images", [question, part(first), part(second)])):
        body = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 300}
        await p.call("vision", name, "POST", "/ai/v1/chat/completions", json=body)


async def step_errors(p: Probe) -> None:
    async with httpx.AsyncClient(headers={"Authorization": "Bearer not-a-real-token"}, timeout=60) as bad:
        started = time.perf_counter()
        response = await bad.post(
            f"{BASE}/accounts/{p.account}/ai/v1/chat/completions",
            json={"model": p.settings.llm.fallback_model, "messages": MESSAGES, "max_tokens": 5},
        )
        record("errors", "bad_token", response, time.perf_counter() - started)
    await p.image("errors", "width_too_big", KLEIN_4B, "a stickman", 5000, 1080)
    await p.image("errors", "empty_prompt", KLEIN_4B, "", 1024, 768)
    await p.call("errors", "unknown_model", "POST", "/ai/run/@cf/black-forest-labs/does-not-exist", json={"prompt": "x"})
    print("429, daily-limit and refusal bodies can't be triggered safely; record them when they first occur (spec §15 #7).")


async def step_style(p: Probe) -> None:
    for label, model, extra in (("klein4b", KLEIN_4B, None), ("klein9b", KLEIN_9B, None), ("dev", DEV, {"steps": 25})):
        for scene, text in STYLE_SCENES.items():
            await p.image("style", f"{label}_{scene}", model, p.scene_prompt(text), 1920, 1080, extra=extra)
    print(f"Open {OUT / 'style'} and judge style, consistency and text-free output by eye.")


async def step_anchor_leak(p: Probe) -> None:
    anchor_prompt = p.scene_prompt(
        "two stickmen talking; the taller one gestures with an open palm, the shorter one listens. "
        "A light hatched ground shadow."
    )
    anchor = await p.image("anchor_leak", "anchor_two_figures", DEV, anchor_prompt, 1024, 768, extra={"steps": 25})
    if not anchor:
        sys.exit("Anchor generation failed; see the recorded response.")
    ref = fit(anchor, p.settings.image.ref_max_side)
    for index, scene in enumerate(SINGLE_FIGURE_SCENES, 1):
        prompt = (
            f"{p.style_text}\n\nScene: {scene}. Exactly one stick figure in the image.\n\n"
            "Reference images: image 0 shows the drawing style only — match its line weight and look, "
            f"not its content, and do not copy its figures.\n\n{p.strict}"
        )
        await p.image("anchor_leak", f"single_{index}", KLEIN_9B, prompt, 1920, 1080, seed=100 + index, refs=[ref])
    print("Count the figures in m0_out/anchor_leak/single_*.png. More than one figure anywhere = the anchor leaks.")


STEPS = {
    "ids": step_ids,
    "llm": step_llm,
    "image": step_image,
    "refs": step_refs,
    "errors": step_errors,
    "style": step_style,
    "anchor-leak": step_anchor_leak,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=[*STEPS, "vision"])
    parser.add_argument("--model", help="vision model ID for the vision step (default: llm.vision_model)")
    args = parser.parse_args()

    async def run() -> None:
        probe = Probe()
        try:
            if args.step == "vision":
                await step_vision(probe, args.model or probe.settings.llm.vision_model)
            else:
                await STEPS[args.step](probe)
        finally:
            await probe.http.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
