#!/usr/bin/env python3
"""Offline tests for the custom OpenAI-compatible provider."""

import base64
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import custom_provider  # noqa: E402


class FakeResponse:
    def __init__(self, payload, raw=False):
        self.payload = payload
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        if self.raw:
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def http_error(code, body=b"backend error", url="http://localhost/test"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


def config(**overrides):
    custom = {
        "base_url": "http://192.168.1.50:8080/",
        "api_key": "",
        "timeout": 600,
        "extra_parameters": "",
    }
    custom.update(overrides)
    return {"api_provider": "custom", "custom": custom}


class TestSettingsAndCapabilities(unittest.TestCase):
    def test_base_url_and_blank_key(self):
        settings = custom_provider.resolve_settings(config())
        self.assertEqual(settings["base_url"], "http://192.168.1.50:8080")
        self.assertEqual(settings["api_key"], "")
        self.assertEqual(settings["timeout"], 600)

    def test_base_url_validation(self):
        invalid = (
            "ftp://example.test",
            "example.test:8080",
            "http://example.test/../admin",
            "http://example.test/%2e%2e/admin",
            "http://example.test/path?x=1",
            "http://example.test\\admin",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(custom_provider.CustomProviderError):
                custom_provider.normalize_base_url(value)

    def test_reverse_proxy_path_is_preserved(self):
        self.assertEqual(
            custom_provider.normalize_base_url("https://example.test/sd///"),
            "https://example.test/sd",
        )

    def test_timeout_range(self):
        self.assertEqual(custom_provider.validate_timeout(None), 600)
        self.assertEqual(custom_provider.validate_timeout("3600"), 3600)
        for value in (29, 3601, "abc", "600.5", True):
            with self.subTest(value=value), self.assertRaises(custom_provider.CustomProviderError):
                custom_provider.validate_timeout(value)

    def test_custom_capability_defaults_and_overrides(self):
        capabilities = custom_provider.provider_capabilities(config(), "custom")
        self.assertEqual(
            capabilities,
            {"t2i": True, "inpaint": True, "erase": True, "upscale": False, "composite": False},
        )
        overridden = config(capabilities={"upscale": True, "composite": True, "t2i": False})
        capabilities = custom_provider.provider_capabilities(overridden, "custom")
        self.assertTrue(capabilities["upscale"])
        self.assertTrue(capabilities["composite"])
        self.assertFalse(capabilities["t2i"])

    def test_existing_provider_defaults_are_unchanged(self):
        self.assertTrue(custom_provider.provider_capabilities({}, "openai")["composite"])
        self.assertFalse(custom_provider.provider_capabilities({}, "openai")["upscale"])
        self.assertTrue(custom_provider.provider_capabilities({}, "bedrock")["upscale"])


class TestGenerationRequest(unittest.TestCase):
    def test_url_body_and_blank_authorization(self):
        url, headers, body = custom_provider.build_generation_request(
            config(extra_parameters='{"steps": 12, "cfg_scale": 6.5, "sampling_method": "euler"}'),
            "a fox",
            "512x512",
        )
        self.assertEqual(url, "http://192.168.1.50:8080/v1/images/generations")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(
            json.loads(body),
            {
                "prompt": "a fox",
                "n": 1,
                "size": "512x512",
                "steps": 12,
                "cfg_scale": 6.5,
                "sampling_method": "euler",
            },
        )
        for name in ("model", "quality", "moderation", "background"):
            self.assertNotIn(name, json.loads(body))

    def test_nonblank_key_adds_bearer_header(self):
        _, headers, _ = custom_provider.build_generation_request(
            config(api_key="local-secret"), "a fox"
        )
        self.assertEqual(headers["Authorization"], "Bearer local-secret")

    def test_extra_json_must_be_an_object(self):
        for value in ("not json", "[1, 2]", "null", '{"quality": "high"}'):
            with self.subTest(value=value), self.assertRaisesRegex(
                custom_provider.CustomProviderError, "Extra parameters"
            ):
                custom_provider.build_generation_request(
                    config(extra_parameters=value), "a fox"
                )

    def test_core_fields_cannot_be_replaced_by_extras(self):
        _, _, body = custom_provider.build_generation_request(
            config(extra_parameters='{"prompt": "other", "n": 4}'), "a fox"
        )
        payload = json.loads(body)
        self.assertEqual(payload["prompt"], "a fox")
        self.assertEqual(payload["n"], 1)

    def test_b64_response(self):
        opener = FakeOpener([FakeResponse({"data": [{"b64_json": "UE5H"}]})])
        data = custom_provider.generate_image(config(), "a fox", opener=opener)
        self.assertEqual(data, b"PNG")
        self.assertEqual(opener.calls[0][1], 600)

    def test_url_response(self):
        opener = FakeOpener([
            FakeResponse({"data": [{"url": "http://images.test/result.png"}]}),
            FakeResponse(b"image bytes", raw=True),
        ])
        data = custom_provider.generate_image(config(), "a fox", opener=opener)
        self.assertEqual(data, b"image bytes")
        self.assertEqual(opener.calls[1][0].full_url, "http://images.test/result.png")
        self.assertNotIn("Authorization", opener.calls[1][0].headers)

    def test_partial_image_entries_are_retained(self):
        parsed = custom_provider.parse_image_response({
            "data": [{"revised_prompt": "none"}, {"b64_json": "QQ=="}, {"url": "http://x"}]
        })
        self.assertEqual(len(parsed["data"]), 2)

    def test_plain_text_error_body_is_preserved(self):
        error = http_error(500, "out of memory: model load failed")
        self.assertIn(
            "out of memory: model load failed",
            custom_provider.format_http_error(error),
        )
        error.close()


class TestEditRequestAndDegradation(unittest.TestCase):
    def test_multipart_body_and_parameters(self):
        image = base64.b64encode(b"IMAGE").decode("ascii")
        url, headers, body = custom_provider.build_edit_request(
            config(extra_parameters='{"steps": 20, "cfg_scale": 7}'),
            "replace with flowers",
            "512x512",
            image,
            b"MASK",
            boundary="test-boundary",
        )
        self.assertEqual(url, "http://192.168.1.50:8080/v1/images/edits")
        self.assertNotIn("Authorization", headers)
        text = body.decode("utf-8")
        for expected in (
            'name="prompt"\r\n\r\nreplace with flowers',
            'name="steps"\r\n\r\n20',
            'name="cfg_scale"\r\n\r\n7',
            'name="image"; filename="image_0.png"',
            'name="mask"; filename="mask.png"',
        ):
            self.assertIn(expected, text)
        for forbidden in ('name="quality"', 'name="moderation"', 'name="background"', 'name="model"'):
            self.assertNotIn(forbidden, text)

    def test_multiple_images_with_mask_use_image_array_fields(self):
        _, _, body = custom_provider.build_edit_request(
            config(), "blend", "512x512", [b"ONE", b"TWO"], b"MASK",
            boundary="test-boundary",
        )
        text = body.decode("utf-8")
        self.assertEqual(text.count('name="image[]"'), 2)
        self.assertNotIn('name="image";', text)
        self.assertIn('name="mask"; filename="mask.png"', text)

    def test_404_disables_inpaint_and_erase(self):
        for code in (404, 501):
            with self.subTest(code=code):
                cfg = config()
                opener = FakeOpener([http_error(code, "not implemented")])
                with self.assertRaisesRegex(
                    custom_provider.CustomProviderError, "use native API build"
                ):
                    custom_provider.edit_image(
                        cfg, "erase", "512x512", b"IMAGE", b"MASK", opener=opener
                    )
                capabilities = custom_provider.provider_capabilities(cfg, "custom")
                self.assertFalse(capabilities["inpaint"])
                self.assertFalse(capabilities["erase"])
                self.assertTrue(capabilities["t2i"])


class TestPromptEnhancement(unittest.TestCase):
    def enhancement_config(self):
        cfg = config()
        cfg["prompt_enhancement"] = {
            "enabled": True,
            "endpoint": "http://192.168.1.50:8081/v1/chat/completions/",
            "model": "local-model",
            "api_key": "",
            "timeout": 120,
        }
        return cfg

    def test_success_replaces_prompt(self):
        opener = FakeOpener([
            FakeResponse({"choices": [{"message": {"content": "detailed scene"}}]})
        ])
        prompt, enhanced, _ = custom_provider.enhance_or_original(
            self.enhancement_config(), "terse instruction", opener=opener
        )
        self.assertTrue(enhanced)
        self.assertEqual(prompt, "detailed scene")
        request = opener.calls[0][0]
        self.assertEqual(request.full_url, "http://192.168.1.50:8081/v1/chat/completions")
        self.assertNotIn("Authorization", request.headers)
        body = json.loads(request.data)
        self.assertEqual(body["messages"][0]["content"], custom_provider.PROMPT_SYSTEM)
        self.assertEqual(body["messages"][1]["content"], "terse instruction")
        self.assertEqual(opener.calls[0][1], 120)

    def test_failure_falls_back_to_original(self):
        opener = FakeOpener([urllib.error.URLError("offline")])
        prompt, enhanced, message = custom_provider.enhance_or_original(
            self.enhancement_config(), "original instruction", opener=opener
        )
        self.assertFalse(enhanced)
        self.assertEqual(prompt, "original instruction")
        self.assertIn("offline", message)

    def test_disabled_by_default(self):
        cfg = config()
        self.assertFalse(custom_provider.resolve_enhancement_settings(cfg)["enabled"])
        opener = FakeOpener([])
        prompt, enhanced, _ = custom_provider.enhance_or_original(cfg, "original", opener)
        self.assertEqual(prompt, "original")
        self.assertFalse(enhanced)
        self.assertEqual(opener.calls, [])


class TestConnectionProbe(unittest.TestCase):
    def test_fast_probe_and_endpoint_reporting(self):
        opener = FakeOpener([
            FakeResponse({"data": [{"b64_json": "UE5H"}]}),
            FakeResponse(b"", raw=True),
        ])
        clock = mock.Mock(side_effect=[10.0, 10.125])
        result = custom_provider.test_connection(config(), opener=opener, clock=clock)
        self.assertTrue(result["success"])
        self.assertTrue(result["generations"])
        self.assertTrue(result["edits"])
        self.assertEqual(result["latency_ms"], 125)
        generation_body = json.loads(opener.calls[0][0].data)
        self.assertEqual(generation_body["size"], "64x64")
        self.assertEqual(generation_body["steps"], 1)
        self.assertEqual(opener.calls[1][0].get_method(), "OPTIONS")
        self.assertIn("edits: available", result["message"])

    def test_fallback_size_and_missing_edits(self):
        opener = FakeOpener([
            http_error(400, "small size unsupported"),
            FakeResponse({"data": [{"b64_json": "UE5H"}]}),
            http_error(404, "not found"),
        ])
        result = custom_provider.test_connection(config(), opener=opener, clock=mock.Mock(side_effect=[1, 2]))
        self.assertTrue(result["success"])
        self.assertFalse(result["edits"])
        self.assertEqual(json.loads(opener.calls[1][0].data)["size"], "256x256")
        self.assertNotIn("steps", json.loads(opener.calls[1][0].data))
        self.assertIn("256x256 fallback", result["message"])

    def test_backend_error_is_reported(self):
        opener = FakeOpener([http_error(500, "model load failed")])
        result = custom_provider.test_connection(config(), opener=opener)
        self.assertFalse(result["success"])
        self.assertIn("model load failed", result["message"])


if __name__ == "__main__":
    unittest.main()
