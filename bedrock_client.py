#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Amazon Bedrock request signing and credential resolution.

Pure standard library - no boto3. GIMP ships its own Python interpreter and
installing packages into it is painful, so InvokeModel requests are signed
here with SigV4 using hmac/hashlib.

Two auth modes are supported:
  - Bedrock API key (bearer token), the simplest option
  - Standard AWS credentials (access key / secret / optional session token)

This module has no GIMP dependencies and is unit testable.
"""

import datetime
import hashlib
import hmac
import json
import os
import urllib.parse

DEFAULT_MODEL_ID = "stability.stable-image-inpaint-v1:0"

# Purpose-built object removal: fills the masked region from surrounding
# context and takes no prompt. Prompted inpainting invents content instead of
# removing it, so this is the right model for erasing photobombers.
DEFAULT_ERASE_MODEL_ID = "stability.stable-image-erase-object-v1:0"

# Stability image models are only invocable through a cross-region inference
# profile, whose id is the model id with a geography prefix.
_GEO_PREFIXES = {"us": "us", "eu": "eu", "ap": "apac", "ca": "us", "sa": "us"}
DEFAULT_REGION = "us-east-1"

_ALGORITHM = "AWS4-HMAC-SHA256"
_HOST_PREFIX = "bedrock-runtime"
# bedrock-runtime requests are signed against the "bedrock" service name.
_SERVICE = "bedrock"


class BedrockError(Exception):
    """Raised when credentials or configuration are unusable."""


def _read_aws_ini(path):
    """Parse an AWS ini file into {section: {key: value}}. Returns {} if absent."""
    import configparser

    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except Exception as e:
        print(f"DEBUG: Could not parse {path}: {e}")
        return {}
    return {section: dict(parser[section]) for section in parser.sections()}


def _profile_section(profile):
    """Credentials files use the bare profile name, config files prefix 'profile '."""
    creds = _read_aws_ini("~/.aws/credentials").get(profile, {})
    config = _read_aws_ini("~/.aws/config")
    conf = config.get(profile if profile == "default" else f"profile {profile}", {})
    merged = dict(conf)
    merged.update(creds)
    return merged


def resolve_credentials(bedrock_config=None):
    """
    Resolve Bedrock credentials and region.

    Precedence: plugin config -> environment -> ~/.aws profile.

    Args:
        bedrock_config: The "bedrock" dict from the plugin config (may be None).

    Returns:
        dict with keys: region, model_id, and either api_key (bearer) or
        access_key/secret_key/session_token.

    Raises:
        BedrockError: if no usable credentials were found.
    """
    cfg = bedrock_config or {}
    profile = cfg.get("profile") or os.environ.get("AWS_PROFILE") or "default"
    ini = _profile_section(profile)

    region = (
        cfg.get("region")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or ini.get("region")
        or DEFAULT_REGION
    )
    resolved = {
        "region": region,
        "model_id": inference_profile_id(cfg.get("model_id") or DEFAULT_MODEL_ID, region),
    }

    if cfg.get("api_key"):
        resolved["api_key"] = cfg["api_key"]
        resolved["source"] = "plugin config (Bedrock API key)"
        return resolved
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        resolved["api_key"] = os.environ["AWS_BEARER_TOKEN_BEDROCK"]
        resolved["source"] = "AWS_BEARER_TOKEN_BEDROCK"
        return resolved

    if cfg.get("access_key_id"):
        access_key, source = cfg["access_key_id"], "plugin config"
    elif os.environ.get("AWS_ACCESS_KEY_ID"):
        access_key, source = os.environ["AWS_ACCESS_KEY_ID"], "environment"
    else:
        access_key, source = ini.get("aws_access_key_id"), f"~/.aws profile '{profile}'"

    secret_key = (
        cfg.get("secret_access_key")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
        or ini.get("aws_secret_access_key")
    )
    if not access_key or not secret_key:
        raise BedrockError(
            "No AWS credentials found. Set a Bedrock API key in plugin settings, "
            "or configure AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or run 'aws configure'."
        )

    resolved["access_key"] = access_key
    resolved["secret_key"] = secret_key
    resolved["source"] = source
    resolved["session_token"] = (
        cfg.get("session_token")
        or os.environ.get("AWS_SESSION_TOKEN")
        or ini.get("aws_session_token")
    )
    return resolved


def inference_profile_id(model_id, region):
    """Add the region's geography prefix to a bare model id, if it lacks one."""
    if not model_id.startswith("stability."):
        return model_id  # already prefixed, an ARN, or another provider
    return f"{_GEO_PREFIXES.get(region.split('-')[0], 'us')}.{model_id}"


def erase_credentials(bedrock_config=None):
    """Same credentials, pointed at the object-erase model."""
    cfg = dict(bedrock_config or {})
    cfg["model_id"] = cfg.get("erase_model_id") or DEFAULT_ERASE_MODEL_ID
    return resolve_credentials(cfg)


def _sign(key, message):
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key, date_stamp, region, service):
    key = _sign(f"AWS4{secret_key}".encode("utf-8"), date_stamp)
    key = _sign(key, region)
    key = _sign(key, service)
    return _sign(key, "aws4_request")


def build_invoke_request(creds, body_bytes, now=None):
    """
    Build the URL and headers for a Bedrock InvokeModel POST.

    Args:
        creds: dict from resolve_credentials()
        body_bytes: the JSON request body, already encoded
        now: datetime override for tests (defaults to UTC now)

    Returns:
        (url, headers) ready to hand to urllib.request.Request
    """
    region = creds["region"]
    host = f"{_HOST_PREFIX}.{region}.amazonaws.com"
    # Model ids contain a colon (e.g. ...-v1:0) which must be percent-encoded in
    # the path. SigV4 requires path segments to be encoded twice for every
    # service except S3, so the canonical path carries %253A where the wire
    # path carries %3A.
    encoded_model = urllib.parse.quote(creds["model_id"], safe="")
    path = f"/model/{encoded_model}/invoke"
    canonical_path = f"/model/{urllib.parse.quote(encoded_model, safe='')}/invoke"
    url = f"https://{host}{path}"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Host": host,
    }

    if creds.get("api_key"):
        headers["Authorization"] = f"Bearer {creds['api_key']}"
        return url, headers

    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    headers["X-Amz-Date"] = amz_date
    if creds.get("session_token"):
        headers["X-Amz-Security-Token"] = creds["session_token"]

    payload_hash = hashlib.sha256(body_bytes).hexdigest()

    signed_names = sorted(h.lower() for h in headers if h.lower() != "authorization")
    lower = {h.lower(): v for h, v in headers.items()}
    canonical_headers = "".join(f"{n}:{lower[n].strip()}\n" for n in signed_names)
    signed_headers = ";".join(signed_names)

    canonical_request = "\n".join(
        ["POST", canonical_path, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(creds["secret_key"], date_stamp, region, _SERVICE),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers["Authorization"] = (
        f"{_ALGORITHM} Credential={creds['access_key']}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return url, headers


def build_inpaint_body(image_b64, mask_b64, prompt, negative_prompt="", seed=0, grow_mask=5):
    """
    Build the Stability inpainting request body.

    White mask pixels are inpainted, black pixels are preserved.
    """
    body = {
        "image": image_b64,
        "mask": mask_b64,
        "prompt": prompt,
        "output_format": "png",
        "seed": int(seed),
        "grow_mask": int(grow_mask),
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    return json.dumps(body).encode("utf-8")


def build_erase_body(image_b64, mask_b64, seed=0, grow_mask=5):
    """
    Build the Stability object-erase request body.

    No prompt: the model fills the masked region from the surrounding image.
    White mask pixels are erased, black pixels are preserved. grow_mask expands
    and softens the mask edge (0-20); measured on a real photo, the default of 5
    beat 20, which grew far enough to swallow neighbouring subjects and made the
    model reconstruct them.
    """
    return json.dumps({
        "image": image_b64,
        "mask": mask_b64,
        "output_format": "png",
        "seed": int(seed),
        "grow_mask": max(0, min(20, int(grow_mask))),
    }).encode("utf-8")


def demo():
    """Self-check: signature matches the AWS SigV4 worked example structure."""
    creds = {
        "region": "us-east-1",
        "model_id": "stability.stable-image-inpaint-v1:0",
        "access_key": "AKIDEXAMPLE",
        "secret_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
    }
    fixed = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    url, headers = build_invoke_request(creds, b'{"prompt":"x"}', now=fixed)

    assert url == (
        "https://bedrock-runtime.us-east-1.amazonaws.com"
        "/model/stability.stable-image-inpaint-v1%3A0/invoke"
    ), url
    assert headers["X-Amz-Date"] == "20240101T000000Z"
    auth = headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20240101/us-east-1/bedrock/aws4_request"), auth
    assert "SignedHeaders=accept;content-type;host;x-amz-date," in auth, auth
    # Signing is deterministic for a fixed clock, so pin the value.
    assert auth.endswith(
        "Signature=886a507a9193175fb1904f1256aef8916ed2c0d4699ceba023b58e682949a196"
    ), auth

    # Session token must be signed when present.
    creds_with_token = dict(creds, session_token="TOKEN")
    _, headers2 = build_invoke_request(creds_with_token, b"{}", now=fixed)
    assert "x-amz-security-token" in headers2["Authorization"], headers2["Authorization"]
    assert headers2["Authorization"] != auth

    # Every successful resolution reports where the credentials came from.
    import os as _os
    saved = {k: _os.environ.pop(k, None) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}
    try:
        r = resolve_credentials(
            {"access_key_id": "A", "secret_access_key": "B", "region": "us-east-1"}
        )
        assert r["source"] == "plugin config", r["source"]
        assert r["model_id"] == "us." + DEFAULT_MODEL_ID, r["model_id"]
        r = resolve_credentials({"api_key": "k"})
        assert r["source"] == "plugin config (Bedrock API key)", r["source"]
    finally:
        for k, v in saved.items():
            if v is not None:
                _os.environ[k] = v

    # Bearer mode skips signing entirely.
    _, headers3 = build_invoke_request(
        {"region": "us-east-1", "model_id": "m", "api_key": "abc"}, b"{}"
    )
    assert headers3["Authorization"] == "Bearer abc"
    assert "X-Amz-Date" not in headers3

    assert inference_profile_id("stability.stable-image-inpaint-v1:0", "us-east-1") == (
        "us.stability.stable-image-inpaint-v1:0"
    )
    assert inference_profile_id("stability.stable-image-inpaint-v1:0", "eu-west-1") == (
        "eu.stability.stable-image-inpaint-v1:0"
    )
    assert inference_profile_id("stability.x", "ap-northeast-1") == "apac.stability.x"
    assert inference_profile_id("us.stability.x", "us-east-1") == "us.stability.x"

    erase = json.loads(build_erase_body("IMG", "MASK", grow_mask=99))
    assert "prompt" not in erase, erase
    assert erase["grow_mask"] == 20, erase["grow_mask"]
    assert json.loads(build_erase_body("I", "M", grow_mask=-5))["grow_mask"] == 0

    saved_env = {k: os.environ.pop(k, None) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}
    try:
        creds = erase_credentials({"access_key_id": "A", "secret_access_key": "B",
                                   "region": "us-east-1"})
        assert creds["model_id"] == "us." + DEFAULT_ERASE_MODEL_ID, creds["model_id"]
    finally:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v

    body = json.loads(build_inpaint_body("IMG", "MASK", "a cat", negative_prompt="dogs"))
    assert body["image"] == "IMG" and body["mask"] == "MASK"
    assert body["negative_prompt"] == "dogs" and body["output_format"] == "png"

    print("bedrock_client self-check passed")


if __name__ == "__main__":
    demo()
