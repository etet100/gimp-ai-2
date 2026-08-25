#!/usr/bin/env python3
"""
Generative Upscale test that runs inside GIMP against a real photo.

    UPSCALE_TEST=1 UPSCALE_TEST_PHOTO=/path/to.jpg \
    gimp -i --batch-interpreter=python-fu-eval \
         -b "import runpy; runpy.run_path('$PWD/tests/test_upscale_in_gimp.py')"

Skipped unless UPSCALE_TEST is set: it makes live Bedrock calls, and the
creative variant takes about a minute. Set UPSCALE_TEST_VARIANTS to a comma
separated subset (default "fast").

The check that matters most is that GIMP can load what Bedrock returns: the two
large variants must ask for WebP, because a 4K PNG exceeds Bedrock's 16MB
response cap.
"""
import importlib.util
import os
import sys
import time

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "upscale_test_result.txt")

lines = []
failures = []


def check(label, condition, detail=""):
    lines.append(f"{'PASS' if condition else 'FAIL'}: {label} {detail}".rstrip())
    if not condition:
        failures.append(label)


try:
    photo = os.environ.get("UPSCALE_TEST_PHOTO")

    # Dialog construction check, no API calls. This is the bug class that
    # already shipped once: helpers returning tuples unpacked as single widgets
    # are invisible until the dialog is actually built.
    sys.path.insert(0, REPO)
    _spec = importlib.util.spec_from_file_location(
        "plugin_for_dialog", os.path.join(REPO, "gimp-ai-plugin.py"))
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _methods = {k: v for k, v in vars(_mod.GimpAIPlugin).items() if callable(v)}
    _Stub = type("Stub", (object,), _methods)
    _plugin = _Stub.__new__(_Stub)
    _plugin.config = {"api_provider": "bedrock", "bedrock": {}, "prompt_history": []}
    _plugin._cancel_requested = False

    from gi.repository import GLib, Gtk

    def _dismiss():
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, Gtk.Dialog) and window.get_visible():
                window.response(Gtk.ResponseType.CANCEL)
        return False

    GLib.timeout_add(1200, _dismiss)
    try:
        _plugin._show_upscale_dialog()
        check("upscale dialog builds and closes cleanly", True)
    except Exception as exc:
        check("upscale dialog builds and closes cleanly", False, repr(exc))

    if not os.environ.get("UPSCALE_TEST") or not photo or not os.path.exists(photo):
        lines.append("SKIPPED live calls: set UPSCALE_TEST=1 and UPSCALE_TEST_PHOTO")
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

        image = Gimp.file_load(run_mode=Gimp.RunMode.NONINTERACTIVE,
                               file=Gio.File.new_for_path(photo))
        layer = image.get_layers()[0]
        lw, lh = layer.get_width(), layer.get_height()
        lines.append(f"source layer {lw}x{lh}")

        variants = os.environ.get("UPSCALE_TEST_VARIANTS", "fast").split(",")
        for variant in [v.strip() for v in variants if v.strip()]:
            spec_v = bedrock_client.UPSCALE_VARIANTS[variant]

            data_b64, original, sent = plugin._export_layer_for_upscale(layer, variant)
            check(f"[{variant}] layer exported", bool(data_b64))
            check(f"[{variant}] input respects the size limit",
                  (spec_v["max_side"] is None or max(sent) <= spec_v["max_side"])
                  and sent[0] * sent[1] <= spec_v["max_pixels"],
                  f"sending {sent[0]}x{sent[1]}")

            started = time.time()
            ok, message, data = plugin._call_bedrock_upscale(variant, data_b64, "a theatre interior")
            elapsed = time.time() - started
            check(f"[{variant}] upscale call succeeds", ok, message)
            if not ok:
                continue

            check(f"[{variant}] response is under the 16MB cap",
                  len(data) < 16 * 1024 * 1024, f"{len(data)/1e6:.1f}MB")

            # The real risk: GIMP must be able to load the returned format
            suffix = "." + spec_v["output_format"]
            new_layer = plugin._add_layer_from_bytes(
                image, data, suffix, f"AI Upscale ({variant})",
                offsets=layer.get_offsets()[1:])
            check(f"[{variant}] GIMP loads the {spec_v['output_format']} result",
                  new_layer is not None)
            if new_layer:
                nw, nh = new_layer.get_width(), new_layer.get_height()
                check(f"[{variant}] result is larger than the source",
                      nw > sent[0] and nh > sent[1], f"{sent[0]}x{sent[1]} -> {nw}x{nh}")
                check(f"[{variant}] aspect ratio preserved",
                      abs((nw / nh) - (sent[0] / sent[1])) < 0.02,
                      f"{nw/nh:.3f} vs {sent[0]/sent[1]:.3f}")
                lines.append(
                    f"  {variant}: {sent[0]}x{sent[1]} -> {nw}x{nh} "
                    f"({nw/sent[0]:.1f}x) in {elapsed:.1f}s, {len(data)/1e6:.1f}MB")
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
