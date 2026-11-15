#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DeepSeek prompt assistant for GIMP AI Plugin.

DeepSeek exposes an OpenAI-compatible chat API (text and vision) but no image
generation endpoint. This module therefore uses DeepSeek to rewrite the user's
prompt into a better instruction for the image provider (OpenAI GPT-Image or
Amazon Bedrock), which still does the actual image work.

Pure standard library and no GIMP imports, so it can be tested offline:

    python3 deepseek_client.py
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 60

# Models offered in Settings, as (model id, label)
DEEPSEEK_MODELS = [
    ("deepseek-flash", "DeepSeek Flash (fast, recommended)"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro (slower, more detailed)"),
]

_CHAT_PATH = "/chat/completions"

_SYSTEM_BASE = (
    "You are an expert prompt engineer for AI image models. Rewrite the "
    "user's instruction into one vivid, self-contained prompt that an image "
    "model can follow. Keep every concrete detail the user gave - subject, "
    "colors, style, composition, text - and add only details that are "
    "consistent with it. Never replace the subject with a different one. "
    "Reply with the rewritten prompt only: no preamble, no quotes, no bullet "
    "points and no explanation. Write it in the same language as the user's "
    "prompt."
)

_SYSTEM_OPERATIONS = {
    "generate": (
        " The prompt is for text-to-image generation. Describe the subject, "
        "composition, lighting, colors and style. Mention camera or lens "
        "details only when they help."
    ),
    "inpaint": (
        " The prompt is for inpainting a selected region. Describe exactly "
        "what should appear inside that region and how it should blend with "
        "the surrounding image. Do not ask to change anything outside the "
        "region and do not mention masks or selections."
    ),
    "composite": (
        " The prompt is for blending several images or layers into one "
        "coherent picture. Describe the final scene and how the inputs should "
        "combine, with consistent lighting and perspective."
    ),
    "erase": (
        " The prompt is for removing an object from a photo. Describe "
        "reconstructing only the background that would be visible behind it, "
        "continuing the surrounding textures and lighting without adding any "
        "new object."
    ),
    "upscale": (
        " The prompt is for generative upscaling. Describe the fine detail, "
        "texture and sharpness to add while preserving the original content "
        "and identity."
    ),
}


class DeepSeekError(Exception):
    """Raised when a DeepSeek request cannot be built or understood."""


def resolve_settings(config=None):
    """
    DeepSeek connection settings from config plus environment fallbacks.

    Args:
        config: the plugin config dict (the "deepseek" section is read)

    Returns:
        dict with api_key ("" when missing), base_url, model and timeout
    """
    section = (config or {}).get("deepseek") or {}
    if not isinstance(section, dict):
        section = {}

    api_key = (section.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or "")
    api_key = str(api_key).strip()

    base_url = str(section.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
    model = str(section.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    try:
        timeout = int(section.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(5, min(300, timeout))

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout": timeout,
    }


def credentials_ready(config=None):
    """True when a DeepSeek API key is available in config or the environment"""
    return bool(resolve_settings(config)["api_key"])


def build_messages(prompt, operation="generate"):
    """
    Chat messages that ask DeepSeek to rewrite an image prompt.

    Args:
        prompt: the user's original prompt
        operation: 'generate', 'inpaint', 'composite', 'erase' or 'upscale'

    Returns:
        list of message dicts for the chat completions API
    """
    system = _SYSTEM_BASE + _SYSTEM_OPERATIONS.get(
        operation, _SYSTEM_OPERATIONS["generate"]
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Operation: {operation}\n"
                "Rewrite the prompt between the markers.\n"
                "<<<\n"
                f"{prompt.strip()}\n"
                ">>>"
            ),
        },
    ]


def build_chat_request(config=None, prompt="", operation="generate"):
    """
    Build the HTTP request for one prompt rewrite.

    Args:
        config: plugin config dict
        prompt: original prompt
        operation: image operation the prompt is for

    Returns:
        (url, headers, body_bytes) ready for urllib

    Raises:
        DeepSeekError: when the key or prompt is missing
    """
    settings = resolve_settings(config)
    if not settings["api_key"]:
        raise DeepSeekError(
            "No DeepSeek API key configured. Add one in Settings or set "
            "DEEPSEEK_API_KEY."
        )
    if not prompt or not prompt.strip():
        raise DeepSeekError("Cannot refine an empty prompt")

    body = {
        "model": settings["model"],
        "messages": build_messages(prompt, operation),
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": False,
    }
    url = settings["base_url"] + _CHAT_PATH
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings['api_key']}",
    }
    return url, headers, json.dumps(body).encode("utf-8")


def _error_message(payload, fallback):
    """Pull the human-readable message out of a DeepSeek error payload"""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
        if payload.get("message"):
            return str(payload["message"])
    return fallback


def parse_chat_response(payload):
    """
    Extract the rewritten prompt from a chat completions response.

    Raises:
        DeepSeekError: when the response carries an error or no text
    """
    if not isinstance(payload, dict):
        raise DeepSeekError("DeepSeek returned an unexpected response")

    if payload.get("error"):
        raise DeepSeekError(_error_message(payload, "DeepSeek returned an error"))

    choices = payload.get("choices") or []
    if not choices:
        raise DeepSeekError("DeepSeek returned no choices")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "output_text")
        )
    text = (content or "").strip()
    if not text:
        raise DeepSeekError("DeepSeek returned an empty prompt")
    return text


def _read_http_error(error):
    """Best-effort body text from an HTTPError"""
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        return str(error)
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body[:300]
    return _error_message(payload, body[:300])


def enhance_prompt(config=None, prompt="", operation="generate", opener=None):
    """
    Ask DeepSeek to rewrite an image prompt.

    Args:
        config: plugin config dict
        prompt: original prompt
        operation: 'generate', 'inpaint', 'composite', 'erase' or 'upscale'
        opener: optional callable matching the plugin's _make_url_request
                (request, timeout=...), used for the plugin's SSL fallback

    Returns:
        (success, text): the rewritten prompt on success, or an error message
    """
    settings = resolve_settings(config)
    try:
        url, headers, body = build_chat_request(config, prompt, operation)
    except DeepSeekError as e:
        return False, str(e)

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")

    def default_opener(req, timeout=settings["timeout"]):
        return urllib.request.urlopen(req, timeout=timeout)

    open_request = opener or default_opener

    try:
        with open_request(request, timeout=settings["timeout"]) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return True, parse_chat_response(payload)
    except urllib.error.HTTPError as e:
        detail = _read_http_error(e)
        print(f"DEBUG: DeepSeek HTTP error: {e.code} - {detail}")
        return False, f"DeepSeek API error {e.code}: {detail}"
    except urllib.error.URLError as e:
        print(f"DEBUG: DeepSeek connection failed: {e}")
        return False, f"Could not reach DeepSeek: {e.reason}"
    except DeepSeekError as e:
        return False, str(e)
    except Exception as e:
        print(f"DEBUG: DeepSeek request failed: {e}")
        return False, f"DeepSeek request failed: {e}"


def _run_self_check():
    """Offline checks for request building, parsing and error handling"""
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print(f"  + {label}")
        else:
            print(f"  x {label} {detail}")
            failures.append(label)

    print("Testing DeepSeek client...")

    config = {"deepseek": {"api_key": "test-key", "model": "deepseek-v4-pro"}}
    settings = resolve_settings(config)
    check("config is read", settings["model"] == "deepseek-v4-pro")
    check("default base URL used", settings["base_url"] == DEFAULT_BASE_URL)

    url, headers, body = build_chat_request(config, "a red fox", "inpaint")
    payload = json.loads(body.decode("utf-8"))
    check("chat endpoint", url.endswith("/chat/completions"), url)
    check("bearer token sent", headers["Authorization"] == "Bearer test-key")
    check("model sent", payload["model"] == "deepseek-v4-pro")
    check("both messages sent", len(payload["messages"]) == 2)
    check("inpaint instruction included", "inpainting" in payload["messages"][0]["content"])
    check("prompt included", "a red fox" in payload["messages"][1]["content"])

    reply = {
        "choices": [{"message": {"content": "  a red fox in fresh snow  "}}]
    }
    check("response parsed", parse_chat_response(reply) == "a red fox in fresh snow")

    try:
        parse_chat_response({"error": {"message": "bad key"}})
        check("error payload rejected", False)
    except DeepSeekError as e:
        check("error payload rejected", "bad key" in str(e))

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(reply).encode("utf-8")

    seen = {}

    def fake_opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    ok, text = enhance_prompt(config, "a red fox", "generate", opener=fake_opener)
    check("enhance round trip", ok and text == "a red fox in fresh snow", text)
    check("timeout passed to opener", seen["timeout"] == DEFAULT_TIMEOUT)

    ok, message = enhance_prompt({}, "a red fox", "generate", opener=fake_opener)
    check("missing key rejected", not ok and "API key" in message, message)

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return False
    print("\nAll checks passed")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if _run_self_check() else 1)
