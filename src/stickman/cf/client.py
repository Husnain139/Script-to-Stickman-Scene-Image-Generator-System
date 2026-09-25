"""The only module that talks to Cloudflare Workers AI (spec §9)."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from stickman.cf.errors import CFError, ErrorCategory, classify

DEFAULT_BASE_URL = "https://api.cloudflare.com/client/v4"
MAX_REFERENCE_IMAGES = 4


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    raw: dict[str, Any]
    neurons: float | None = None


@dataclass(frozen=True)
class ImageResult:
    image_bytes: bytes


class CloudflareClient:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        *,
        plan: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._account_url = f"{base_url}/accounts/{account_id}"
        self._plan = plan
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def __aenter__(self) -> CloudflareClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format
        response = await self._post(f"{self._account_url}/ai/v1/chat/completions", json=body)
        try:
            data = response.json()
        except ValueError as exc:
            raise CFError(
                ErrorCategory.BAD_REQUEST, f"chat response is not JSON: {response.text[:300]}"
            ) from exc
        if isinstance(data, dict) and "choices" not in data and isinstance(data.get("result"), dict):
            data = data["result"]
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise CFError(
                ErrorCategory.BAD_REQUEST, f"unexpected chat response shape: {str(data)[:300]}"
            ) from exc
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        return LLMResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw=data,
            neurons=_neurons(response, usage),
        )

    async def generate_image(
        self,
        model: str,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        steps: int | None = None,
        guidance: float | None = None,
        input_images: Sequence[bytes] = (),
    ) -> ImageResult:
        if len(input_images) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images are allowed")
        # (None, value) tuples make httpx send plain multipart fields; FLUX.2 requires
        # multipart even when there are no reference images.
        fields: list[tuple[str, tuple[str | None, Any] | tuple[str, bytes, str]]] = [
            ("prompt", (None, prompt)),
            ("width", (None, str(width))),
            ("height", (None, str(height))),
            ("seed", (None, str(seed))),
        ]
        if steps is not None:
            fields.append(("steps", (None, str(steps))))
        if guidance is not None:
            fields.append(("guidance", (None, str(guidance))))
        for index, image in enumerate(input_images):
            name = f"input_image_{index}"
            fields.append((name, (f"{name}.png", image, "image/png")))
        response = await self._post(f"{self._account_url}/ai/run/{model}", files=fields)
        return ImageResult(image_bytes=_extract_image(response))

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.post(url, **kwargs)
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise CFError(
                ErrorCategory.TRANSIENT, f"timeout: {exc!r}", possibly_billed=False
            ) from exc
        except httpx.TimeoutException as exc:
            raise CFError(ErrorCategory.TRANSIENT, f"timeout: {exc!r}", possibly_billed=True) from exc
        except httpx.RequestError as exc:
            raise CFError(ErrorCategory.TRANSIENT, f"network error: {exc!r}") from exc
        if response.status_code >= 400:
            raise CFError(
                classify(response.status_code, response.text, plan=self._plan),
                response.text[:500],
                status=response.status_code,
                retry_after=_retry_after(response),
            )
        return response


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _neurons(response: httpx.Response, usage: dict[str, Any]) -> float | None:
    """Actual cost of the call: the cf-ai-neurons header, else usage.neurons (docs/m0-findings.md)."""
    for value in (response.headers.get("cf-ai-neurons"), usage.get("neurons")):
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_image(response: httpx.Response) -> bytes:
    if response.headers.get("content-type", "").startswith("image/"):
        return response.content
    try:
        data = response.json()
    except ValueError as exc:
        raise CFError(ErrorCategory.BAD_REQUEST, "image response is neither an image nor JSON") from exc
    result = data.get("result") if isinstance(data, dict) else None
    encoded = (result.get("image") if isinstance(result, dict) else None) or (
        data.get("image") if isinstance(data, dict) else None
    )
    if not encoded:
        raise CFError(ErrorCategory.BAD_REQUEST, f"no image in response: {str(data)[:300]}")
    try:
        return base64.b64decode(encoded)
    except (binascii.Error, ValueError) as exc:
        raise CFError(ErrorCategory.BAD_REQUEST, "image field is not valid base64") from exc
