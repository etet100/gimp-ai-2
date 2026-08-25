#!/usr/bin/env python3
"""
Generated-layer fitting test. Runs inside GIMP, makes no API calls.

    gimp -i --batch-interpreter=python-fu-eval \
         -b "import runpy; runpy.run_path('$PWD/tests/test_fit_in_gimp.py')"

Checks that a generated layer is scaled to *cover* the canvas and centred: the
tighter axis matches exactly, the other overhangs, and nothing is left gapped.
Writes tests/fit_test_result.txt.
"""
import importlib.util
import io
import os
import struct
import sys
import zlib

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "fit_test_result.txt")

lines = []
failures = []


def check(label, condition, detail=""):
    lines.append(f"{'PASS' if condition else 'FAIL'}: {label} {detail}".rstrip())
    if not condition:
        failures.append(label)


def make_png(width, height):
    """Minimal solid-colour RGB PNG, so the test needs no image library."""
    raw = b"".join(
        b"\x00" + bytes([80, 140, 200] * width) for _ in range(height)
    )

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


try:
    sys.path.insert(0, REPO)
    spec = importlib.util.spec_from_file_location(
        "plugin_under_test", os.path.join(REPO, "gimp-ai-plugin.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    methods = {k: v for k, v in vars(mod.GimpAIPlugin).items() if callable(v)}
    plugin = type("Stub", (object,), methods).__new__(type("Stub", (object,), methods))
    plugin.config = {"debug_mode": False}
    plugin._cancel_requested = False

    # (canvas, generated) pairs covering enlarge, reduce, and exact match
    cases = [
        ((1600, 900), (1344, 768), "landscape needing enlargement"),
        ((2048, 1536), (1216, 832), "4:3 canvas from a 3:2 generation"),
        ((800, 600), (1344, 768), "canvas smaller than the generation"),
        ((1024, 1024), (1024, 1024), "already an exact match"),
        ((900, 1600), (768, 1344), "portrait"),
    ]

    for (cw, ch), (gw, gh), label in cases:
        image = Gimp.Image.new(cw, ch, Gimp.ImageBaseType.RGB)
        base = Gimp.Layer.new(image, "bg", cw, ch, Gimp.ImageType.RGBA_IMAGE,
                              100.0, Gimp.LayerMode.NORMAL)
        image.insert_layer(base, None, 0)

        added = plugin._add_layer_from_data(image, make_png(gw, gh))
        layer = image.get_layers()[0]
        lw, lh = layer.get_width(), layer.get_height()
        ox, oy = layer.get_offsets()[1:]

        check(f"[{label}] layer added", added)
        check(f"[{label}] covers the canvas",
              lw >= cw and lh >= ch, f"layer {lw}x{lh} vs canvas {cw}x{ch}")
        check(f"[{label}] one axis matches exactly",
              abs(lw - cw) <= 1 or abs(lh - ch) <= 1, f"layer {lw}x{lh}")
        check(f"[{label}] aspect ratio preserved",
              abs((lw / lh) - (gw / gh)) < 0.01, f"{lw/lh:.3f} vs {gw/gh:.3f}")
        check(f"[{label}] centred, no gap",
              ox <= 0 and oy <= 0 and ox + lw >= cw and oy + lh >= ch,
              f"offsets ({ox},{oy}) size {lw}x{lh}")
        lines.append(f"  {label}: {gw}x{gh} -> {lw}x{lh} at ({ox},{oy})")
        image.delete()
except Exception:
    import traceback
    lines.append("ERROR")
    lines.append(traceback.format_exc())
    failures.append("exception")

lines.append("ALL PASSED" if not failures else f"FAILURES: {failures}")
with open(OUT, "w") as handle:
    handle.write("\n".join(lines) + "\n")
print("\n".join(lines))

Gimp.quit()
