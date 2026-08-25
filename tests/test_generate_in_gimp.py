#!/usr/bin/env python3
"""
Bedrock text-to-image test that runs inside GIMP.

    GENERATE_TEST=1 gimp -i --batch-interpreter=python-fu-eval \
         -b "import runpy; runpy.run_path('$PWD/tests/test_generate_in_gimp.py')"

Skipped unless GENERATE_TEST is set, since it makes a live Bedrock call.
Writes tests/generate_test_result.txt and tests/generate_test_output.png.
"""
import importlib.util
import os
import sys

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "generate_test_result.txt")

lines = []
failures = []


def check(label, condition, detail=""):
    lines.append(f"{'PASS' if condition else 'FAIL'}: {label} {detail}".rstrip())
    if not condition:
        failures.append(label)


try:
    if not os.environ.get("GENERATE_TEST"):
        lines.append("SKIPPED: set GENERATE_TEST=1 (makes a live Bedrock call)")
    else:
        sys.path.insert(0, REPO)
        spec = importlib.util.spec_from_file_location(
            "plugin_under_test", os.path.join(REPO, "gimp-ai-plugin.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        methods = {k: v for k, v in vars(mod.GimpAIPlugin).items() if callable(v)}
        Stub = type("Stub", (object,), methods)
        plugin = Stub.__new__(Stub)
        plugin.config = {"debug_mode": False, "api_provider": "bedrock", "bedrock": {}}
        plugin._cancel_requested = False

        import bedrock_client

        # Generation lives in its own region and must not be given an
        # inference-profile prefix, which Bedrock rejects outright.
        creds = bedrock_client.generation_credentials({})
        lines.append(f"model {creds['model_id']} in {creds['region']}")
        check("generation model id is unprefixed",
              not creds["model_id"].startswith(("us.", "eu.", "apac.")),
              creds["model_id"])

        # A landscape canvas should map to a landscape aspect ratio
        width, height = 1600, 900
        ratio = bedrock_client.aspect_ratio_for(width, height)
        check("canvas maps to the nearest accepted aspect ratio", ratio == "16:9", ratio)

        ok, message, data = plugin._call_bedrock_generation(
            "a red vintage bicycle leaning against a stone wall", width, height)
        check("bedrock generation succeeds", ok, message)

        if ok:
            check("returned PNG data", data[:4] == b"\x89PNG", repr(data[:8]))
            path = os.path.join(HERE, "generate_test_output.png")
            with open(path, "wb") as f:
                f.write(data)

            loaded = Gimp.file_load(run_mode=Gimp.RunMode.NONINTERACTIVE,
                                    file=Gio.File.new_for_path(path))
            gw, gh = loaded.get_width(), loaded.get_height()
            lines.append(f"generated {gw}x{gh}")
            check("generated image is landscape like the canvas", gw > gh, f"{gw}x{gh}")

            # And it must actually land in the image as a layer
            canvas = Gimp.Image.new(width, height, Gimp.ImageBaseType.RGB)
            base = Gimp.Layer.new(canvas, "bg", width, height,
                                  Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
            canvas.insert_layer(base, None, 0)
            added = plugin._add_layer_from_data(canvas, data)
            check("generated image is added as a layer", added,
                  f"{len(canvas.get_layers())} layers")
            canvas.delete()
            loaded.delete()
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
