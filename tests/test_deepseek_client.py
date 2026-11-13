#!/usr/bin/env python3
"""Offline tests for deepseek_client.py (no network, no DeepSeek key needed)."""

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepseek_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DeepSeekError,
    build_chat_request,
    build_messages,
    credentials_ready,
    enhance_prompt,
    parse_chat_response,
    resolve_settings,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.payload)


class TestSettings(unittest.TestCase):
    def test_defaults(self):
        settings = resolve_settings({"deepseek": {"api_key": "k"}})
        self.assertEqual(settings["model"], DEFAULT_MODEL)
        self.assertEqual(settings["base_url"], DEFAULT_BASE_URL)
        self.assertEqual(settings["timeout"], 60)

    def test_environment_key_fallback(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key"}):
            self.assertTrue(credentials_ready({}))
            self.assertEqual(resolve_settings({})["api_key"], "env-key")

    def test_config_key_wins(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key"}):
            settings = resolve_settings({"deepseek": {"api_key": "config-key"}})
            self.assertEqual(settings["api_key"], "config-key")

    def test_base_url_is_normalized(self):
        settings = resolve_settings(
            {"deepseek": {"api_key": "k", "base_url": "https://example.test/v1/"}}
        )
        self.assertEqual(settings["base_url"], "https://example.test/v1")

    def test_bad_timeout_uses_default(self):
        settings = resolve_settings(
            {"deepseek": {"api_key": "k", "timeout": "not-a-number"}}
        )
        self.assertEqual(settings["timeout"], 60)


class TestRequest(unittest.TestCase):
    def setUp(self):
        self.config = {"deepseek": {"api_key": "test-key", "model": "deepseek-v4-pro"}}

    def test_endpoint_and_auth(self):
        url, headers, body = build_chat_request(self.config, "a red fox", "generate")
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body)["model"], "deepseek-v4-pro")

    def test_messages_carry_prompt_and_operation(self):
        _, _, body = build_chat_request(self.config, "a red fox", "inpaint")
        payload = json.loads(body)
        self.assertEqual(len(payload["messages"]), 2)
        self.assertIn("inpainting", payload["messages"][0]["content"])
        self.assertIn("a red fox", payload["messages"][1]["content"])
        self.assertFalse(payload["stream"])

    def test_every_operation_has_guidance(self):
        expected = {
            "generate": "text-to-image",
            "inpaint": "inpainting",
            "composite": "blending",
            "erase": "removing an object",
            "upscale": "upscaling",
        }
        for operation, keyword in expected.items():
            system = build_messages("prompt", operation)[0]["content"]
            self.assertIn(keyword, system)

    def test_missing_key_is_rejected(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(DeepSeekError):
                build_chat_request({}, "a red fox", "generate")

    def test_empty_prompt_is_rejected(self):
        with self.assertRaises(DeepSeekError):
            build_chat_request(self.config, "   ", "generate")


class TestResponse(unittest.TestCase):
    def test_content_is_trimmed(self):
        payload = {"choices": [{"message": {"content": "  refined prompt  "}}]}
        self.assertEqual(parse_chat_response(payload), "refined prompt")

    def test_list_content_is_joined(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "one "},
                            {"type": "text", "text": "two"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(parse_chat_response(payload), "one two")

    def test_error_payload_is_reported(self):
        with self.assertRaises(DeepSeekError) as raised:
            parse_chat_response({"error": {"message": "invalid api key"}})
        self.assertIn("invalid api key", str(raised.exception))

    def test_missing_choices_is_rejected(self):
        with self.assertRaises(DeepSeekError):
            parse_chat_response({})

    def test_empty_content_is_rejected(self):
        with self.assertRaises(DeepSeekError):
            parse_chat_response({"choices": [{"message": {"content": "  "}}]})


class TestEnhancePrompt(unittest.TestCase):
    def setUp(self):
        self.config = {"deepseek": {"api_key": "test-key"}}
        self.reply = {
            "choices": [{"message": {"content": "a red fox in deep snow"}}]
        }

    def test_success_round_trip(self):
        opener = FakeOpener(self.reply)
        ok, text = enhance_prompt(
            self.config, "a red fox", "generate", opener=opener
        )
        self.assertTrue(ok)
        self.assertEqual(text, "a red fox in deep snow")
        self.assertEqual(opener.request.full_url, DEFAULT_BASE_URL + "/chat/completions")
        self.assertEqual(opener.timeout, 60)

    def test_missing_key_returns_message(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            ok, message = enhance_prompt({}, "a red fox", "generate")
        self.assertFalse(ok)
        self.assertIn("API key", message)

    def test_http_error_body_is_included(self):
        body = io.BytesIO(b'{"error": {"message": "invalid api key"}}')
        error = urllib.error.HTTPError(
            DEFAULT_BASE_URL, 401, "Unauthorized", {}, body
        )

        def failing_opener(_request, timeout=None):
            raise error

        ok, message = enhance_prompt(
            self.config, "a red fox", "generate", opener=failing_opener
        )
        self.assertFalse(ok)
        self.assertIn("401", message)
        self.assertIn("invalid api key", message)

    def test_connection_error_is_reported(self):
        def failing_opener(_request, timeout=None):
            raise urllib.error.URLError("no route to host")

        ok, message = enhance_prompt(
            self.config, "a red fox", "generate", opener=failing_opener
        )
        self.assertFalse(ok)
        self.assertIn("Could not reach DeepSeek", message)


if __name__ == "__main__":
    unittest.main()
