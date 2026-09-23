import asyncio
import base64

import httpx
import pytest

from stickman.cf.client import CloudflareClient
from stickman.cf.errors import CFError, ErrorCategory

PNG = b"\x89PNG\r\n\x1a\nfake-image-bytes"
KLEIN_4B = "@cf/black-forest-labs/flux-2-klein-4b"


def make_client(handler, plan="paid"):
    return CloudflareClient(
        "acc123", "tok-secret", plan=plan, timeout_s=5, transport=httpx.MockTransport(handler)
    )


def generate(handler, **overrides):
    kwargs = dict(prompt="a stickman", width=1920, height=1080, seed=42)
    kwargs.update(overrides)

    async def go():
        async with make_client(handler) as client:
            return await client.generate_image(KLEIN_4B, **kwargs)

    return asyncio.run(go())


def chat(handler, **overrides):
    async def go():
        async with make_client(handler) as client:
            return await client.chat("@cf/openai/gpt-oss-120b", [{"role": "user", "content": "hi"}], **overrides)

    return asyncio.run(go())


def test_generate_image_sends_multipart_and_decodes_base64():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"success": True, "result": {"image": base64.b64encode(PNG).decode()}})

    result = generate(handler, input_images=[b"ref-zero"])
    assert result.image_bytes == PNG
    assert seen["url"] == f"https://api.cloudflare.com/client/v4/accounts/acc123/ai/run/{KLEIN_4B}"
    assert seen["auth"] == "Bearer tok-secret"
    assert seen["ctype"].startswith("multipart/form-data")
    for field in (b'name="prompt"', b'name="width"', b'name="height"', b'name="seed"'):
        assert field in seen["body"]
    assert b'name="input_image_0"; filename="input_image_0.png"' in seen["body"]
    assert b'name="steps"' not in seen["body"]


def test_prompt_only_request_is_still_multipart():
    seen = {}

    def handler(request):
        seen["ctype"] = request.headers["content-type"]
        request.read()
        return httpx.Response(200, json={"result": {"image": base64.b64encode(PNG).decode()}})

    generate(handler)
    assert seen["ctype"].startswith("multipart/form-data")


def test_steps_are_sent_when_given():
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        return httpx.Response(200, json={"result": {"image": base64.b64encode(PNG).decode()}})

    generate(handler, steps=25)
    assert b'name="steps"' in seen["body"]


def test_binary_image_response_is_accepted():
    result = generate(lambda request: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    assert result.image_bytes == PNG


def test_response_without_image_is_bad_request():
    with pytest.raises(CFError) as info:
        generate(lambda request: httpx.Response(200, json={"success": True, "result": {}}))
    assert info.value.category is ErrorCategory.BAD_REQUEST


def test_more_than_four_reference_images_is_rejected():
    with pytest.raises(ValueError, match="at most 4"):
        generate(lambda request: httpx.Response(200), input_images=[b"x"] * 5)


def test_chat_parses_openai_shape_and_sends_response_format():
    seen = {}

    def handler(request):
        seen["json"] = request.read()
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    fmt = {"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}}
    result = chat(handler, response_format=fmt)
    assert result.text == '{"ok": true}'
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert b'"response_format"' in seen["json"]
    assert b'"model":"@cf/openai/gpt-oss-120b"' in seen["json"].replace(b" ", b"")


def test_chat_accepts_result_envelope():
    body = {"result": {"choices": [{"message": {"content": "OK"}}]}, "success": True}
    result = chat(lambda request: httpx.Response(200, json=body))
    assert result.text == "OK"
    assert result.input_tokens is None


def test_chat_endpoint_url():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    chat(handler)
    assert seen["url"] == "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1/chat/completions"


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (401, "bad token", ErrorCategory.AUTH),
        (429, "slow down", ErrorCategory.RATE_LIMITED),
        (500, "oops", ErrorCategory.TRANSIENT),
        (400, "bad width", ErrorCategory.BAD_REQUEST),
    ],
)
def test_http_errors_are_classified(status, body, expected):
    with pytest.raises(CFError) as info:
        generate(lambda request: httpx.Response(status, text=body))
    assert info.value.category is expected
    assert info.value.status == status


def test_retry_after_header_is_parsed():
    with pytest.raises(CFError) as info:
        generate(lambda request: httpx.Response(429, text="slow", headers={"retry-after": "7"}))
    assert info.value.retry_after == 7.0


def test_timeout_is_transient_and_possibly_billed():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(CFError) as info:
        generate(handler)
    assert info.value.category is ErrorCategory.TRANSIENT
    assert info.value.possibly_billed is True


def test_network_error_is_transient_not_billed():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(CFError) as info:
        generate(handler)
    assert info.value.category is ErrorCategory.TRANSIENT
    assert info.value.possibly_billed is False


def test_chat_non_json_body_is_bad_request():
    with pytest.raises(CFError) as info:
        chat(lambda request: httpx.Response(200, text="not json"))
    assert info.value.category is ErrorCategory.BAD_REQUEST


def test_chat_usage_not_a_dict_yields_none_tokens():
    body = {"choices": [{"message": {"content": "OK"}}], "usage": "lots"}
    result = chat(lambda request: httpx.Response(200, json=body))
    assert (result.input_tokens, result.output_tokens) == (None, None)


def test_decoding_error_is_transient():
    def handler(request):
        raise httpx.DecodingError("bad", request=request)

    with pytest.raises(CFError) as info:
        generate(handler)
    assert info.value.category is ErrorCategory.TRANSIENT
    assert info.value.possibly_billed is False


def test_connect_timeout_is_transient_not_billed():
    def handler(request):
        raise httpx.ConnectTimeout("no conn", request=request)

    with pytest.raises(CFError) as info:
        generate(handler)
    assert info.value.category is ErrorCategory.TRANSIENT
    assert info.value.possibly_billed is False
