#!/usr/bin/env python3
"""
Bedrock inpainting test that runs inside GIMP against a real photo.

    INPAINT_TEST=1 INPAINT_TEST_PHOTO=/path/to.jpg \
    INPAINT_TEST_SELECTION=x1,y1,x2,y2 \
    gimp -i --batch-interpreter=python-fu-eval \
         -b "import runpy; runpy.run_path('$PWD/tests/test_inpaint_in_gimp.py')"

Skipped unless INPAINT_TEST is set: it makes three live Bedrock calls.

Checks the two behaviours that distinguish the Bedrock path: several seeded
variations as separate layers, and the whole context window pasted back with no
selection-shaped layer mask (which is what used to leave a rectangular seam).
"""
import importlib.util
import os
import sys
import time

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gegl, Gimp, Gio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "inpaint_test_result.txt")

lines = []
failures = []


def check(label, condition, detail=""):
    lines.append(f"{'PASS' if condition else 'FAIL'}: {label} {detail}".rstrip())
    if not condition:
        failures.append(label)


try:
    photo = os.environ.get("INPAINT_TEST_PHOTO")
    sel_env = os.environ.get("INPAINT_TEST_SELECTION")
    if not os.environ.get("INPAINT_TEST") or not photo or not sel_env \
            or not os.path.exists(photo):
        lines.append("SKIPPED: set INPAINT_TEST=1, INPAINT_TEST_PHOTO, INPAINT_TEST_SELECTION")
    else:
        sel = [int(v) for v in sel_env.split(",")]
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

        image = Gimp.file_load(run_mode=Gimp.RunMode.NONINTERACTIVE,
                               file=Gio.File.new_for_path(photo))
        image.select_rectangle(Gimp.ChannelOps.REPLACE, sel[0], sel[1],
                               sel[2] - sel[0], sel[3] - sel[1])

        context_info = plugin._calculate_native_context_extraction(image)
        check("bedrock inpaint uses a native-resolution window",
              abs(context_info["padding_info"]["scale_factor"] - 1.0) < 0.005,
              f"scale {context_info['padding_info']['scale_factor']}")
        ok, message, image_data = plugin._extract_context_region(image, context_info)
        check("context extraction succeeds", ok, message)

        mask = plugin._convert_mask_to_bedrock(
            plugin._create_context_mask(image, context_info, context_info["target_size"]))

        started = time.time()
        api_ok, api_message, results = plugin._run_bedrock_variations(
            3,
            lambda seed: plugin._call_bedrock_edit(
                image_data, mask, "empty theatre seats", None, seed),
            None,
            name="test inpaint",
        )
        elapsed = time.time() - started
        check("three variations requested concurrently", api_ok, api_message)
        if api_ok:
            check("all three variations returned", len(results) == 3, f"{len(results)}")
            check("concurrent, not serial", elapsed < 60, f"{elapsed:.1f}s for 3 calls")

            placed, msg = plugin._place_variation_layers(
                image, results, context_info, "Inpaint")
            check("three layers placed", placed == 3, f"{placed}: {msg}")

            layers = image.get_layers()[:placed]
            names = [layer.get_name() for layer in layers]
            check("named in order",
                  names == ["AI Inpaint 1 of 3", "AI Inpaint 2 of 3", "AI Inpaint 3 of 3"],
                  f"{names}")
            check("only the first is visible",
                  [layer.get_visible() for layer in layers] == [True, False, False])

            # The seam fix: no selection-shaped layer mask on any variation
            check("no selection mask applied (whole window pasted)",
                  all(layer.get_mask() is None for layer in layers))

            # And the variations must genuinely differ
            sigs = []
            for layer in layers:
                data = layer.get_buffer().get(
                    Gegl.Rectangle.new(sel[0], sel[1], sel[2] - sel[0], sel[3] - sel[1]),
                    1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE)
                sigs.append(sum(data[::97]))
            check("variations differ from each other", len(set(sigs)) == 3, f"{sigs}")
            lines.append(f"  3 variations in {elapsed:.1f}s")

            # Export each variation so the seam can be judged by eye
            for index, layer in enumerate(layers, start=1):
                dup = image.duplicate()
                for candidate in dup.get_layers():
                    if candidate.get_name().startswith("AI Inpaint"):
                        candidate.set_visible(
                            candidate.get_name() == layer.get_name())
                dup.flatten()
                path = os.path.join(HERE, f"inpaint_variation_{index}.png")
                proc = Gimp.get_pdb().lookup_procedure("file-png-export")
                cfg = proc.create_config()
                cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
                cfg.set_property("image", dup)
                cfg.set_property("file", Gio.File.new_for_path(path))
                cfg.set_property("options", None)
                proc.run(cfg)
                dup.delete()
            lines.append("  exported inpaint_variation_1..3.png")
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
