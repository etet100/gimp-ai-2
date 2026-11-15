#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Custom OpenAI-compatible image and prompt providers, using stdlib only."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_TIMEOUT = 600
DEFAULT_ENHANCE_TIMEOUT = 120
MIN_TIMEOUT = 30
MAX_TIMEOUT = 3600
EDITS_UNAVAILABLE_MESSAGE = (
    "Custom backend has no /v1/images/edits — use native API build"
)
PROMPT_SYSTEM = (
    "You expand terse image-edit instructions into one precise, detailed scene "
    "description. Output only the final description."
)
FORBIDDEN_EXTRA_PARAMETERS = {"quality", "moderation", "background"}
CAPABILITY_NAMES = ("t2i", "inpaint", "erase", "upscale", "composite")
DEFAULT_CAPABILITIES = {
    "openai": {
        "t2i": True,
        "inpaint": True,
        "erase": True,
        "upscale": False,
        "composite": True,
    },
    "bedrock": {
        "t2i": True,
        "inpaint": True,
        "erase": True,
        "upscale": True,
        "composite": True,
    },
    "deepseek": {
        "t2i": True,
        "inpaint": True,
        "erase": True,
        "upscale": False,
        "composite": True,
    },
    "custom": {
        "t2i": True,
        "inpaint": True,
        "erase": True,
        "upscale": False,
        "composite": False,
    },
}


class CustomProviderError(Exception):
    """Raised when custom provider configuration or responses are invalid."""


def normalize_base_url(value):
    """Validate and normalize an HTTP(S) base URL without a trailing slash."""
    value = str(value or "").strip()
    if not value:
        raise CustomProviderError("Custom provider base URL is required")
    if "\\" in value:
        raise CustomProviderError("Base URL must not contain backslashes")
    try:
        parts = urllib.parse.urlsplit(value)
        _ = parts.port
    except ValueError as e:
        raise CustomProviderError(f"Invalid base URL: {e}") from e
    if parts.scheme.lower() not in ("http", "https"):
        raise CustomProviderError("Base URL must use http or https")
    if not parts.hostname:
        raise CustomProviderError("Base URL must include a host")
    if parts.username or parts.password:
        raise CustomProviderError("Base URL must not contain credentials")
    if parts.query or parts.fragment:
        raise CustomProviderError("Base URL must not contain a query or fragment")
    path = parts.path
    decoded = path
    for _ in range(3):
        if any(segment in (".", "..") for segment in decoded.split("/")):
            raise CustomProviderError("Base URL must not contain path traversal")
        decoded = urllib.parse.unquote(decoded)
    path = path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, path, "", "")
    )


def validate_timeout(value, default=DEFAULT_TIMEOUT):
    """Return an integer timeout in the supported 30-3600 second range."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise CustomProviderError("Timeout must be a whole number from 30 to 3600 seconds")
    try:
        timeout = int(value)
    except (TypeError, ValueError) as e:
        raise CustomProviderError(
            "Timeout must be a whole number from 30 to 3600 seconds"
        ) from e
    if str(value).strip() != str(timeout) and not isinstance(value, int):
        raise CustomProviderError("Timeout must be a whole number from 30 to 3600 seconds")
    if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
        raise CustomProviderError("Timeout must be from 30 to 3600 seconds")
    return timeout


def resolve_settings(config=None):
    """Read and validate the custom provider section."""
    section = (config or {}).get("custom") or {}
    if not isinstance(section, dict):
        section = {}
    raw_base_url = str(section.get("base_url") or "").strip()
    return {
        "base_url": normalize_base_url(raw_base_url) if raw_base_url else "",
        "api_key": str(section.get("api_key") or "").strip(),
        "timeout": validate_timeout(section.get("timeout"), DEFAULT_TIMEOUT),
        "extra_parameters": section.get("extra_parameters", ""),
    }


def resolve_enhancement_settings(config=None):
    """Read and validate the optional local prompt enhancement section."""
    section = (config or {}).get("prompt_enhancement") or {}
    if not isinstance(section, dict):
        section = {}
    endpoint = str(section.get("endpoint") or "").strip()
    return {
        "enabled": bool(section.get("enabled", False)),
        "endpoint": normalize_base_url(endpoint) if endpoint else "",
        "model": str(section.get("model") or "").strip(),
        "api_key": str(section.get("api_key") or "").strip(),
        "timeout": validate_timeout(
            section.get("timeout"), DEFAULT_ENHANCE_TIMEOUT
        ),
    }


def provider_capabilities(config=None, provider="custom"):
    """Merge one provider's defaults with optional config.json overrides."""
    defaults = DEFAULT_CAPABILITIES.get(provider, DEFAULT_CAPABILITIES["openai"])
    result = dict(defaults)
    section = (config or {}).get(provider) or {}
    if isinstance(section, dict):
        overrides = section.get("capabilities") or {}
        if isinstance(overrides, dict):
            for name in CAPABILITY_NAMES:
                if name in overrides:
                    result[name] = bool(overrides[name])
    return result


def disable_edits(config):
    """Disable custom inpainting and erase after an endpoint 404/501."""
    custom = config.setdefault("custom", {})
    capabilities = custom.setdefault("capabilities", {})
    capabilities["inpaint"] = False
    capabilities["erase"] = False
    return provider_capabilities(config, "custom")


def parse_extra_parameters(value):
    """Parse the Extra parameters JSON object and reject OpenAI-only fields."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        parameters = dict(value)
    else:
        try:
            parameters = json.loads(value)
        except (json.JSONDecodeError, TypeError) as e:
            detail = e.msg if isinstance(e, json.JSONDecodeError) else str(e)
            raise CustomProviderError(
                f"Extra parameters must be valid JSON: {detail}"
            ) from e
    if not isinstance(parameters, dict):
        raise CustomProviderError("Extra parameters must be a JSON object")
    forbidden = FORBIDDEN_EXTRA_PARAMETERS.intersection(parameters)
    if forbidden:
        raise CustomProviderError(
            "Extra parameters must not include OpenAI-only fields: "
            + ", ".join(sorted(forbidden))
        )
    return parameters


def _headers(api_key, content_type="application/json"):
    headers = {"Content-Type": content_type, "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_generation_request(config, prompt, size="512x512"):
    """Build a minimal text-to-image request for /v1/images/generations."""
    settings = resolve_settings(config)
    if not settings["base_url"]:
        raise CustomProviderError("Custom provider base URL is required")
    if not prompt or not str(prompt).strip():
        raise CustomProviderError("Cannot generate an image with an empty prompt")
    body = parse_extra_parameters(settings["extra_parameters"])
    body.update({"prompt": str(prompt), "n": 1, "size": size or "512x512"})
    url = settings["base_url"] + "/v1/images/generations"
    headers = _headers(settings["api_key"])
    return url, headers, json.dumps(body).encode("utf-8")


def _multipart_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    if value is None:
        return "null"
    return str(value)


def _append_text_part(parts, boundary, name, value):
    parts.extend([
        f"--{boundary}\r\n".encode("ascii"),
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
        _multipart_value(value).encode("utf-8"),
        b"\r\n",
    ])


def _append_file_part(parts, boundary, name, filename, data):
    parts.extend([
        f"--{boundary}\r\n".encode("ascii"),
        (
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'
        ).encode("utf-8"),
        b"Content-Type: image/png\r\n\r\n",
        data,
        b"\r\n",
    ])


def build_edit_request(config, prompt, size, image_data, mask_data=None, boundary=None):
    """Build a multipart image edit; repeated image[] supports experiments."""
    settings = resolve_settings(config)
    if not settings["base_url"]:
        raise CustomProviderError("Custom provider base URL is required")
    if not prompt or not str(prompt).strip():
        raise CustomProviderError("Cannot edit an image with an empty prompt")
    if not image_data:
        raise CustomProviderError("No image data provided")

    fields = parse_extra_parameters(settings["extra_parameters"])
    fields.update({"prompt": str(prompt), "n": 1, "size": size or "512x512"})
    images = image_data if isinstance(image_data, list) else [image_data]
    if len(images) > 16:
        raise CustomProviderError("Maximum 16 images supported")

    boundary = boundary or uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        _append_text_part(parts, boundary, name, value)
    for index, image in enumerate(images):
        image_bytes = base64.b64decode(image) if isinstance(image, str) else image
        if not isinstance(image_bytes, bytes):
            raise CustomProviderError("Image data must be PNG bytes or base64")
        name = "image[]" if len(images) > 1 else "image"
        _append_file_part(parts, boundary, name, f"image_{index}.png", image_bytes)
    if mask_data is not None:
        if not isinstance(mask_data, bytes):
            raise CustomProviderError("Mask data must be PNG bytes")
        _append_file_part(parts, boundary, "mask", "mask.png", mask_data)
    parts.append(f"--{boundary}--\r\n".encode("ascii"))

    url = settings["base_url"] + "/v1/images/edits"
    headers = _headers(
        settings["api_key"], f"multipart/form-data; boundary={boundary}"
    )
    headers["User-Agent"] = "GIMP-AI-Plugin/1.0"
    return url, headers, b"".join(parts)


def parse_image_response(payload):
    """Validate and retain all returned b64_json or URL image entries."""
    if not isinstance(payload, dict):
        raise CustomProviderError("Custom backend returned an unexpected response")
    if payload.get("error"):
        error = payload["error"]
        if isinstance(error, dict):
            error = error.get("message") or str(error)
        raise CustomProviderError(str(error))
    data = payload.get("data")
    if not isinstance(data, list):
        raise CustomProviderError("Custom backend returned no data array")
    images = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("b64_json") or item.get("url"):
            images.append(item)
    if not images:
        raise CustomProviderError("Custom backend returned no image data")
    return {"data": images}


def _open_request(opener, request, timeout):
    if opener is None:
        return urllib.request.urlopen(request, timeout=timeout)
    return opener(request, timeout=timeout)


def _read_json_response(response):
    try:
        return json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CustomProviderError(f"Custom backend returned invalid JSON: {e}") from e


def _image_bytes(result, timeout, opener=None):
    if result.get("b64_json"):
        try:
            return base64.b64decode(result["b64_json"], validate=True)
        except (ValueError, TypeError) as e:
            raise CustomProviderError(f"Custom backend returned invalid base64: {e}") from e
    request = urllib.request.Request(result["url"])
    with _open_request(opener, request, timeout) as response:
        return response.read()


def generate_image(config, prompt, size="512x512", opener=None):
    """Generate and return image bytes from either b64_json or a URL."""
    settings = resolve_settings(config)
    url, headers, body = build_generation_request(config, prompt, size)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with _open_request(opener, request, settings["timeout"]) as response:
        payload = _read_json_response(response)
    result = parse_image_response(payload)["data"][0]
    return _image_bytes(result, settings["timeout"], opener)


def edit_image(config, prompt, size, image_data, mask_data=None, opener=None):
    """Edit an image and return the normalized b64_json/URL response."""
    settings = resolve_settings(config)
    url, headers, body = build_edit_request(config, prompt, size, image_data, mask_data)
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _open_request(opener, request, settings["timeout"]) as response:
            payload = _read_json_response(response)
        return parse_image_response(payload)
    except urllib.error.HTTPError as e:
        if e.code in (404, 501):
            disable_edits(config)
            raise CustomProviderError(EDITS_UNAVAILABLE_MESSAGE) from e
        raise


def response_image_bytes(config, response, opener=None):
    """Decode the first image in a normalized response, downloading a URL if needed."""
    settings = resolve_settings(config)
    result = parse_image_response(response)["data"][0]
    return _image_bytes(result, settings["timeout"], opener)


def enhance_or_original(config, prompt, opener=None):
    """Return (prompt, enhanced, message), retaining the original on failure."""
    success, text = enhance_prompt(config, prompt, opener)
    if success:
        return text, True, "Prompt enhanced"
    return prompt, False, text


def build_chat_request(config, prompt):
    """Build the optional OpenAI-compatible prompt enhancement request."""
    settings = resolve_enhancement_settings(config)
    if not settings["endpoint"]:
        raise CustomProviderError("Prompt enhancement endpoint is required")
    if not settings["model"]:
        raise CustomProviderError("Prompt enhancement model is required")
    if not prompt or not str(prompt).strip():
        raise CustomProviderError("Cannot enhance an empty prompt")
    body = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": str(prompt)},
        ],
        "stream": False,
    }
    headers = _headers(settings["api_key"])
    return (
        settings["endpoint"],
        headers,
        json.dumps(body).encode("utf-8"),
    )


def parse_chat_response(payload):
    """Extract the enhanced prompt from a chat completions response."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise CustomProviderError("Prompt enhancer returned no description") from e
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "output_text")
        )
    text = str(content or "").strip()
    if not text:
        raise CustomProviderError("Prompt enhancer returned an empty description")
    return text


def read_http_error(error):
    """Return a plain-text or OpenAI-style JSON backend error body."""
    try:
        body = error.read().decode("utf-8", "replace").strip()
    except Exception:
        return str(error)
    if not body:
        return str(error)
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body
    if isinstance(payload, dict):
        detail = payload.get("error") or payload.get("message") or payload.get("detail")
        if isinstance(detail, dict):
            detail = detail.get("message") or str(detail)
        if detail:
            return str(detail)
    return body


def format_http_error(error):
    """Format an HTTP error without hiding the backend's response body."""
    return f"Custom backend HTTP {error.code}: {read_http_error(error)[:1000]}"


def enhance_prompt(config, prompt, opener=None):
    """Return (success, enhanced prompt or error), never raising for failures."""
    settings = resolve_enhancement_settings(config)
    if not settings["enabled"]:
        return False, "Prompt enhancement is disabled"
    try:
        url, headers, body = build_chat_request(config, prompt)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with _open_request(opener, request, settings["timeout"]) as response:
            payload = _read_json_response(response)
        return True, parse_chat_response(payload)
    except urllib.error.HTTPError as e:
        return False, format_http_error(e)
    except Exception as e:
        return False, str(e)


def _generation_probe(config, size, extra_parameters, opener, timeout):
    probe_config = dict(config)
    probe_config["custom"] = dict((config or {}).get("custom") or {})
    probe_config["custom"]["extra_parameters"] = extra_parameters
    url, headers, body = build_generation_request(
        probe_config, "connection test", size
    )
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with _open_request(opener, request, timeout) as response:
        payload = _read_json_response(response)
    parse_image_response(payload)


def _probe_edits(config, opener, timeout):
    settings = resolve_settings(config)
    request = urllib.request.Request(
        settings["base_url"] + "/v1/images/edits",
        headers=_headers(settings["api_key"]),
        method="OPTIONS",
    )
    try:
        with _open_request(opener, request, min(timeout, 30)) as response:
            response.read()
        return True
    except urllib.error.HTTPError as e:
        read_http_error(e)
        return False if e.code in (404, 501) else True
    except Exception:
        return None


def test_connection(config, opener=None, clock=time.monotonic):
    """Probe generation and edits endpoints and return a UI-ready result."""
    started = clock()
    result = {
        "success": False,
        "generations": False,
        "edits": None,
        "latency_ms": 0,
        "message": "",
    }
    settings = None
    try:
        settings = resolve_settings(config)
        if not settings["base_url"]:
            raise CustomProviderError("Custom provider base URL is required")
        try:
            _generation_probe(
                config, "64x64", {"steps": 1}, opener, settings["timeout"]
            )
            result["generations"] = True
            result["success"] = True
            generation_note = "64x64, 1 step"
        except urllib.error.HTTPError as e:
            if e.code not in (400, 422):
                raise
            read_http_error(e)
            _generation_probe(config, "256x256", {}, opener, settings["timeout"])
            result["generations"] = True
            result["success"] = True
            generation_note = "256x256 fallback"
        result["edits"] = _probe_edits(config, opener, settings["timeout"])
        edits_text = {True: "available", False: "not found", None: "unknown"}[
            result["edits"]
        ]
        result["message"] = (
            f"Generations: available ({generation_note}); edits: {edits_text}"
        )
    except urllib.error.HTTPError as e:
        result["message"] = format_http_error(e)
    except Exception as e:
        result["message"] = str(e)
    finally:
        if settings and settings["base_url"] and result["edits"] is None:
            result["edits"] = _probe_edits(config, opener, settings["timeout"])
        if not result["success"] and settings:
            edits_text = {True: "available", False: "not found", None: "unknown"}[
                result["edits"]
            ]
            result["message"] += f"; edits: {edits_text}"
        result["latency_ms"] = max(0, round((clock() - started) * 1000))
    result["message"] += f"; latency: {result['latency_ms']} ms"
    return result
